"""Background recording: the satellite records the sounds of the home for a while.

What the satellite's microphones hear on an ordinary evening (TV, dinner, talking) is the best
background for training and the honest test for false wakes. Voice PE streams audio to Home
Assistant only while it listens, and Home Assistant ends one turn after at most 15 s, so the
recording is a chain of turns: our agent answers with nothing and keeps the conversation open,
the satellite listens again; when it stops anyway, it is restarted by playing a short silence.
All turns are appended to one WAV file in ``data/background/``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import wave

from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import SILENCE_URL
from .household import Household
from .messages import Texts
from .take import SAMPLE_RATE

_LOGGER = logging.getLogger(__name__)

RESUME_SECONDS = 1.5  # idle this long -> listen again
MAX_SILENT_RESTARTS = 8  # restarts in a row without any audio -> the satellite is gone


@dataclass
class BackgroundInfo:
    satellite: str
    minutes: int
    file: str
    seconds: float = 0.0


def background_path(household: Household, satellite: str, now: datetime) -> Path:
    name = satellite.split(".", 1)[-1]
    return household.root / "data" / "background" / f"{name}_{now:%Y%m%d_%H%M}.wav"


class Writer:
    """One WAV file, appended turn by turn (blocking - run in the executor)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._wav = wave.open(str(path), "wb")  # noqa: SIM115 - open for the whole session, closed in close()
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)

    def write(self, pcm: bytes) -> None:
        self._wav.writeframes(pcm[: len(pcm) - len(pcm) % 2])

    def close(self) -> None:
        self._wav.close()


class BackgroundSession:
    kind = "background"

    def __init__(
        self,
        hass: HomeAssistant,
        household: Household,
        satellite: str,
        minutes: int,
        on_end: Callable[[BackgroundSession], None],
        on_change: Callable[[], None],
    ) -> None:
        self.hass = hass
        self.household = household
        self.satellite = satellite
        self.minutes = minutes
        self.texts = Texts(hass.config.language)
        self._on_end = on_end
        self._on_change = on_change
        self.active = False
        self.info: BackgroundInfo | None = None
        self._writer: Writer | None = None
        self._listened = False
        self._heard = False
        self._silent_restarts = 0
        self._unsubs: list[CALLBACK_TYPE] = []
        self._resume: CALLBACK_TYPE | None = None

    async def async_start(self) -> None:
        path = background_path(self.household, self.satellite, datetime.now())
        self._writer = await self.hass.async_add_executor_job(Writer, path)
        self.info = BackgroundInfo(self.satellite, self.minutes, path.relative_to(self.household.root).as_posix())
        self.active = True
        self._unsubs.append(async_track_state_change_event(self.hass, [self.satellite], self._state_changed))
        self._unsubs.append(async_call_later(self.hass, self.minutes * 60, self._time_up))
        await self.hass.services.async_call(
            "assist_satellite",
            "start_conversation",
            {
                "entity_id": self.satellite,
                "start_message": self.texts.get("background_start", minutes=self.minutes),
                "preannounce": False,
            },
            blocking=False,
        )
        self._on_change()

    async def _listen(self) -> None:
        """Listen again without saying anything."""
        self._listened = False
        await self.hass.services.async_call(
            "assist_satellite",
            "start_conversation",
            {"entity_id": self.satellite, "start_media_id": SILENCE_URL, "preannounce": False},
            blocking=False,
        )

    async def async_add_audio(self, pcm: bytes) -> None:
        if not self.active or self._writer is None or self.info is None:
            return
        self._heard = True
        self._silent_restarts = 0
        await self.hass.async_add_executor_job(self._writer.write, pcm)
        self.info.seconds += len(pcm) / (SAMPLE_RATE * 2)
        self._on_change()

    @callback
    def reply(self) -> tuple[str, bool]:
        """Nothing to say; keep listening while recording."""
        return "", self.active

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if not self.active or new is None:
            return
        if new.state == "listening":
            self._cancel_resume()
            self._listened, self._heard = True, False
            return
        if new.state == "idle" and self._listened:
            self._cancel_resume()
            self._resume = async_call_later(self.hass, RESUME_SECONDS, self._check_idle)

    @callback
    def _check_idle(self, _now) -> None:
        self._resume = None
        state = self.hass.states.get(self.satellite)
        if not self.active or state is None or state.state != "idle":
            return
        if not self._heard:
            self._silent_restarts += 1
            if self._silent_restarts >= MAX_SILENT_RESTARTS:
                _LOGGER.warning("%s stopped sending audio - background recording ends", self.satellite)
                self.hass.async_create_task(self.async_stop())
                return
        self.hass.async_create_task(self._listen())

    @callback
    def _time_up(self, _now) -> None:
        self.hass.async_create_task(self.async_stop(finished=True))

    @callback
    def _cancel_resume(self) -> None:
        if self._resume is not None:
            self._resume()
            self._resume = None

    async def async_stop(self, announce: bool = True, finished: bool = False) -> None:
        if not self.active:
            return
        self.active = False
        self._cancel_resume()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._writer is not None:
            await self.hass.async_add_executor_job(self._writer.close)
            self._writer = None
        minutes = round((self.info.seconds if self.info else 0) / 60)
        _LOGGER.info("Background recording on %s ended: %d min", self.satellite, minutes)
        self._on_end(self)
        if announce:
            key = "background_done" if finished else "background_stopped"
            await self.hass.services.async_call(
                "assist_satellite",
                "announce",
                {"entity_id": self.satellite, "message": self.texts.get(key, minutes=minutes), "preannounce": False},
                blocking=False,
            )
