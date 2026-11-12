"""Wake Word Recorder: record wake word takes through the household's own voice satellites.

Recordings made by the microphones that will later listen for the word are the best training
data. A session switches a satellite (e.g. Home Assistant Voice PE) to the "Wake word recording"
pipeline; the satellite says how to say each take, listens without a wake word, and the takes are
stored in the household folder of the wakeword toolkit. A background session records the sounds of
the home through the same microphones (for training and for measuring false wakes). Every wake of a
satellite goes to a log file, which shows how often each word wakes each room on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from . import api, wake_log
from .background import BackgroundSession
from .const import CONF_HOUSEHOLD, DEFAULT_BACKGROUND_MINUTES, DOMAIN, SIGNAL_UPDATE, STATIC_URL
from .household import HOUSEHOLD_FILE, Household, HouseholdError, load
from .listen_signal import SIGNAL_BEEP
from .pipeline import Restorer, async_ensure_pipeline, pipeline_select
from .session import Session

_LOGGER = logging.getLogger(__name__)

# household.yaml is edited by hand or by a toolkit on a computer - noticed within this time
WATCH_INTERVAL = timedelta(seconds=10)
PLATFORMS = [Platform.BUTTON, Platform.CONVERSATION, Platform.SELECT, Platform.SENSOR, Platform.STT, Platform.TTS]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class RuntimeData:
    household: Household
    restorer: Restorer
    session: Session | BackgroundSession | None = None
    restoring: asyncio.Task | None = None  # puts back the pipeline of the last ended session
    # choices of the select entities (used by the start button and as service defaults)
    speaker: str | None = None
    word: str | None = None
    satellite: str | None = None
    background_satellite: str | None = None  # for background recording (falls back to satellite)
    last: dict = field(default_factory=dict)  # summary of the last finished session
    mtime: float | None = None  # of household.yaml when it was read
    ended_at: float = 0.0  # monotonic time the last session ended (late turns stay silent)
    signal: str = SIGNAL_BEEP  # "speak now" sound after an instruction (select "Signal")


type RecorderConfigEntry = ConfigEntry[RuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    _register_services(hass)
    api.async_register(hass)  # the training panel fetches the recordings without a file share
    await hass.http.async_register_static_paths(
        [StaticPathConfig(f"{STATIC_URL}/silence.wav", str(Path(__file__).parent / "silence.wav"), True)]
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RecorderConfigEntry) -> bool:
    try:
        household = await hass.async_add_executor_job(load, Path(entry.data[CONF_HOUSEHOLD]))
    except HouseholdError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = RuntimeData(household, Restorer(hass))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _started(_hass: HomeAssistant) -> None:
        # a session cut off by a restart left the satellite on the recording pipeline
        await entry.runtime_data.restorer.async_restore()
        try:
            await async_ensure_pipeline(hass)
        except HomeAssistantError as err:
            _LOGGER.error("%s", err)

    entry.async_on_unload(async_at_started(hass, _started))
    entry.async_on_unload(wake_log.async_install(hass))

    async def _watch(_now) -> None:
        mtime = await hass.async_add_executor_job(_mtime, entry)
        if mtime != entry.runtime_data.mtime:
            try:
                await async_reload_household(hass, entry)
            except HomeAssistantError as err:
                _LOGGER.warning("household.yaml changed but could not be read: %s", err)
                entry.runtime_data.mtime = mtime  # do not warn every interval

    entry.runtime_data.mtime = await hass.async_add_executor_job(_mtime, entry)
    entry.async_on_unload(async_track_time_interval(hass, _watch, WATCH_INTERVAL))
    return True


def _mtime(entry: ConfigEntry) -> float | None:
    try:
        return (Path(entry.data[CONF_HOUSEHOLD]) / HOUSEHOLD_FILE).stat().st_mtime
    except OSError:
        return None


async def async_unload_entry(hass: HomeAssistant, entry: RecorderConfigEntry) -> bool:
    if entry.runtime_data.session is not None:
        await entry.runtime_data.session.async_stop(announce=False)
    if entry.runtime_data.restoring is not None:
        await entry.runtime_data.restoring
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Wake Word Recorder",
        manufacturer="home-assistant-wakeword",
        entry_type=DeviceEntryType.SERVICE,
    )


@callback
def notify(hass: HomeAssistant) -> None:
    async_dispatcher_send(hass, SIGNAL_UPDATE)


def get_entry(hass: HomeAssistant) -> RecorderConfigEntry:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("Wake Word Recorder is not set up")
    return entries[0]


async def async_reload_household(hass: HomeAssistant, entry: RecorderConfigEntry) -> Household:
    """household.yaml may change while HA runs (new person, new word)."""
    runtime = entry.runtime_data
    mtime = await hass.async_add_executor_job(_mtime, entry)
    try:
        household = await hass.async_add_executor_job(load, Path(entry.data[CONF_HOUSEHOLD]))
    except HouseholdError as err:
        raise HomeAssistantError(str(err)) from err
    runtime.household, runtime.mtime = household, mtime
    if runtime.speaker not in household.speakers:
        runtime.speaker = None
    # the chosen word must be one of the person's words; default to the first
    words = [w.id for w in household.words_for(runtime.speaker)] if runtime.speaker else list(household.words)
    if runtime.word not in words:
        runtime.word = words[0] if words else None
    notify(hass)
    return household


async def _async_run(
    hass: HomeAssistant,
    entry: RecorderConfigEntry,
    satellite: str,
    factory: Callable[[Callable, Callable], Session | BackgroundSession],
    summary: Callable[[Session | BackgroundSession], dict],
) -> Session | BackgroundSession:
    """Switch the satellite to the recording pipeline and start a session (one at a time)."""
    runtime = entry.runtime_data
    if runtime.session is not None:
        await runtime.session.async_stop(announce=False)
    if runtime.restoring is not None:
        # the old session's satellite gets its pipeline back before the next switch, so a
        # restore never runs late over a new session (also on the same satellite)
        await runtime.restoring
    await async_ensure_pipeline(hass)
    select = pipeline_select(hass, satellite)
    await runtime.restorer.async_switch(select)

    @callback
    def ended(session: Session | BackgroundSession) -> None:
        if runtime.session is session:
            runtime.session = None
        runtime.ended_at = hass.loop.time()
        if session.info is not None:
            runtime.last = summary(session)
        runtime.restoring = hass.async_create_task(runtime.restorer.async_restore(select))
        notify(hass)

    session = factory(ended, lambda: notify(hass))
    runtime.session = session
    try:
        await session.async_start()
    except Exception:
        runtime.session = None
        await runtime.restorer.async_restore(select)
        raise
    return session


async def async_start_session(
    hass: HomeAssistant,
    entry: RecorderConfigEntry,
    satellite: str | None = None,
    speaker: str | None = None,
    word: str | None = None,
    count: int | None = None,
) -> Session:
    runtime = entry.runtime_data
    satellite = satellite or runtime.satellite
    speaker = speaker or runtime.speaker
    word = word or runtime.word
    if not satellite or not speaker or not word:
        raise HomeAssistantError("Choose the satellite, the person and the word first")
    household = await async_reload_household(hass, entry)
    if speaker not in household.speakers:
        raise HomeAssistantError(f"Unknown person '{speaker}' in household.yaml")
    if word not in household.words:
        raise HomeAssistantError(f"Unknown word '{word}' in household.yaml")
    if speaker not in household.words[word].speakers:
        raise HomeAssistantError(f"'{speaker}' does not use the word '{word}' (household.yaml)")
    session = await _async_run(
        hass,
        entry,
        satellite,
        lambda ended, changed: Session(hass, household, satellite, speaker, word, count, ended, changed),
        lambda s: {"speaker": s.speaker, "word": s.word, "done": s.info.done, "target": s.info.target},
    )
    assert isinstance(session, Session)
    return session


async def async_start_background(
    hass: HomeAssistant,
    entry: RecorderConfigEntry,
    satellite: str | None = None,
    minutes: int = DEFAULT_BACKGROUND_MINUTES,
) -> BackgroundSession:
    runtime = entry.runtime_data
    satellite = satellite or runtime.background_satellite or runtime.satellite
    if not satellite:
        raise HomeAssistantError("Choose the satellite first")
    household = await async_reload_household(hass, entry)
    session = await _async_run(
        hass,
        entry,
        satellite,
        lambda ended, changed: BackgroundSession(hass, household, satellite, minutes, ended, changed),
        lambda s: {"background": s.info.file, "minutes": round(s.info.seconds / 60, 1)},
    )
    assert isinstance(session, BackgroundSession)
    return session


def _register_services(hass: HomeAssistant) -> None:
    async def start(call: ServiceCall) -> ServiceResponse:
        session = await async_start_session(
            hass,
            get_entry(hass),
            call.data.get("satellite"),
            call.data.get("speaker"),
            call.data.get("word"),
            call.data.get("count"),
        )
        info = session.info
        return {"done": info.done, "target": info.target} if info else {}

    async def stop(call: ServiceCall) -> ServiceResponse:
        runtime = get_entry(hass).runtime_data
        if runtime.session is not None:
            await runtime.session.async_stop()
        return dict(runtime.last)

    async def reload(call: ServiceCall) -> ServiceResponse:
        household = await async_reload_household(hass, get_entry(hass))
        return {"speakers": list(household.speakers), "words": list(household.words)}

    hass.services.async_register(
        DOMAIN,
        "start_session",
        start,
        schema=vol.Schema(
            {
                vol.Optional("satellite"): cv.entity_domain("assist_satellite"),
                vol.Optional("speaker"): cv.string,
                vol.Optional("word"): cv.string,
                vol.Optional("count"): vol.All(int, vol.Range(1, 100)),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def background(call: ServiceCall) -> ServiceResponse:
        session = await async_start_background(hass, get_entry(hass), call.data.get("satellite"), call.data["minutes"])
        return {"file": session.info.file} if session.info else {}

    hass.services.async_register(
        DOMAIN,
        "start_background",
        background,
        schema=vol.Schema(
            {
                vol.Optional("satellite"): cv.entity_domain("assist_satellite"),
                vol.Optional("minutes", default=DEFAULT_BACKGROUND_MINUTES): vol.All(int, vol.Range(1, 240)),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "stop_session", stop, schema=vol.Schema({}), supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, "reload", reload, schema=vol.Schema({}), supports_response=SupportsResponse.OPTIONAL
    )
