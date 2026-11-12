"""Start and stop a recording session with the choices of the select entities."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RecorderConfigEntry, async_start_background, async_start_session
from .entity import RecorderEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RecorderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([StartButton(entry), StopButton(entry), BackgroundButton(entry)])


class StartButton(RecorderEntity, ButtonEntity):
    def __init__(self, entry: RecorderConfigEntry) -> None:
        super().__init__(entry, "start")

    async def async_press(self) -> None:
        await async_start_session(self.hass, self._entry)


class StopButton(RecorderEntity, ButtonEntity):
    def __init__(self, entry: RecorderConfigEntry) -> None:
        super().__init__(entry, "stop")

    async def async_press(self) -> None:
        if self.runtime.session is not None:
            await self.runtime.session.async_stop()


class BackgroundButton(RecorderEntity, ButtonEntity):
    """Record the sounds of the home through the chosen satellite (30 minutes)."""

    def __init__(self, entry: RecorderConfigEntry) -> None:
        super().__init__(entry, "background")

    async def async_press(self) -> None:
        await async_start_background(self.hass, self._entry)
