"""Conversation agent of the recording pipeline: says how the take went and asks for the next."""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RecorderConfigEntry, device_info
from .const import ENTITY_IDS
from .messages import Texts

LATE_TURN_SECONDS = 30.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RecorderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([RecorderAgent(entry)])


class RecorderAgent(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "agent"

    def __init__(self, entry: RecorderConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-conversation"
        self.entity_id = ENTITY_IDS["conversation"]
        self._attr_device_info = device_info(entry)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    async def async_process(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
        runtime = self._entry.runtime_data
        session = runtime.session
        if session is not None:
            text, keep = session.reply()
        elif self.hass.loop.time() - runtime.ended_at < LATE_TURN_SECONDS:
            # a turn that was still running when the session ended - nothing to say
            text, keep = "", False
        else:
            text, keep = Texts(self.hass.config.language).get("inactive"), False
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(text)
        return conversation.ConversationResult(
            response=response,
            conversation_id=user_input.conversation_id,
            continue_conversation=keep,
        )
