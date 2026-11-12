"""Choices for the start button: the person, their word and the satellite.

Options show names (person id, wake word, satellite name); the choice is remembered across
restarts.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_added_domain, async_track_state_removed_domain
from homeassistant.helpers.restore_state import RestoreEntity

from . import RecorderConfigEntry, async_reload_household, notify
from .entity import RecorderEntity
from .listen_signal import SIGNALS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RecorderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        [
            SpeakerSelect(entry),
            WordSelect(entry),
            SatelliteSelect(entry),
            SignalSelect(entry),
        ]
    )


class ChoiceSelect(RecorderEntity, SelectEntity, RestoreEntity):
    """Option label <-> value (id) mapping, the value kept in the runtime data."""

    key: str

    def __init__(self, entry: RecorderConfigEntry) -> None:
        super().__init__(entry, self.key)

    def choices(self) -> dict[str, str]:
        """label -> value"""
        raise NotImplementedError

    @property
    def options(self) -> list[str]:
        return list(self.choices())

    @property
    def current_option(self) -> str | None:
        value = getattr(self.runtime, self.key)
        return next((label for label, v in self.choices().items() if v == value), None)

    async def async_select_option(self, option: str) -> None:
        setattr(self.runtime, self.key, self.choices()[option])
        await self._chosen()
        notify(self.hass)

    async def _chosen(self) -> None:
        """Hook after a choice."""

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        # the id, so the choice survives a restart even before the options exist (satellites)
        return {"value": getattr(self.runtime, self.key)}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if getattr(self.runtime, self.key) is None and (last := await self.async_get_last_state()):
            value = last.attributes.get("value") or self.choices().get(last.state)
            if value is not None:
                setattr(self.runtime, self.key, value)


class SpeakerSelect(ChoiceSelect):
    key = "speaker"

    def choices(self) -> dict[str, str]:
        return {sid: sid for sid in self.runtime.household.speakers}

    async def _chosen(self) -> None:
        # also picks the person's first word when the chosen one is not theirs
        await async_reload_household(self.hass, self._entry)


class WordSelect(ChoiceSelect):
    key = "word"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.runtime.word not in self.choices().values():
            self.runtime.word = next(iter(self.choices().values()), None)

    def choices(self) -> dict[str, str]:
        household = self.runtime.household
        words = household.words_for(self.runtime.speaker) if self.runtime.speaker else household.words.values()
        return {w.wake_word: w.id for w in words}


class SatelliteSelect(ChoiceSelect):
    key = "satellite"

    """The satellite for both recording takes and listening to the home: only one can run at a time."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # satellites (ESPHome …) load after this integration and come and go: refresh the options
        for track in (async_track_state_added_domain, async_track_state_removed_domain):
            self.async_on_remove(track(self.hass, "assist_satellite", self._satellites_changed))
        self.async_on_remove(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._availability_changed))

    @callback
    def _satellites_changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    @callback
    def _availability_changed(self, event: Event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if not event.data["entity_id"].startswith("assist_satellite.") or old is None or new is None:
            return
        if (old.state == STATE_UNAVAILABLE) != (new.state == STATE_UNAVAILABLE):
            self.async_write_ha_state()

    @callback
    def choices(self) -> dict[str, str]:
        """The satellites that are up, and the chosen one even when it is not."""
        if self.hass is None:
            return {}
        return {
            state.name: state.entity_id
            for state in self.hass.states.async_all("assist_satellite")
            if state.state != STATE_UNAVAILABLE or state.entity_id == self.runtime.satellite
        }


class SignalSelect(RecorderEntity, SelectEntity, RestoreEntity):
    """The "speak now" sound after each instruction: a beep, HA's announcement chime or none."""

    _attr_options = list(SIGNALS)

    def __init__(self, entry: RecorderConfigEntry) -> None:
        super().__init__(entry, "signal")

    @property
    def current_option(self) -> str:
        return self.runtime.signal

    async def async_select_option(self, option: str) -> None:
        self.runtime.signal = option
        notify(self.hass)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state in SIGNALS:
            self.runtime.signal = last.state
