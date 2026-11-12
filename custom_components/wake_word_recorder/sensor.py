"""Status of the recorder: idle or recording, with the session's progress."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RecorderConfigEntry
from .background import BackgroundSession
from .entity import RecorderEntity

STATES = ["idle", "recording"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RecorderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([StatusSensor(entry)])


class StatusSensor(RecorderEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATES

    def __init__(self, entry: RecorderConfigEntry) -> None:
        super().__init__(entry, "status")

    @property
    def native_value(self) -> str:
        session = self.runtime.session
        return "recording" if session is not None and session.active else "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        household = self.runtime.household
        attributes: dict[str, Any] = {"household": str(household.root), "last_session": self.runtime.last}
        session = self.runtime.session
        if isinstance(session, BackgroundSession) and session.info is not None:
            attributes.update(
                mode="background",
                satellite=session.info.satellite,
                minutes=session.info.minutes,
                recorded_minutes=round(session.info.seconds / 60, 1),
                file=session.info.file,
            )
        elif session is not None and session.info is not None:
            attributes["mode"] = "takes"
            info = session.info
            attributes.update(
                satellite=info.satellite,
                speaker=info.speaker,
                word=info.word,
                wake_word=info.wake_word,
                done=info.done,
                target=info.target,
                next_variant=info.variant,
                last_file=info.last_file,
                last_issues=list(info.last_issues),
            )
        return attributes
