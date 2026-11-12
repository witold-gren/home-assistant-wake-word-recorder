"""Base of the entities that follow the recorder's state."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import RecorderConfigEntry, device_info
from .const import ENTITY_IDS, SIGNAL_UPDATE


class RecorderEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: RecorderConfigEntry, key: str) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-{key}"
        self._attr_translation_key = key
        self.entity_id = ENTITY_IDS[key]
        self._attr_device_info = device_info(entry)

    @property
    def runtime(self):
        return self._entry.runtime_data

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._updated))

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()
