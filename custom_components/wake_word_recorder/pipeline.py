"""The recording pipeline and switching a satellite to it (and back)."""

from __future__ import annotations

import asyncio
import logging
import re

from homeassistant.components.assist_pipeline import (
    async_create_default_pipeline,
    async_get_pipeline,
    async_get_pipelines,
    async_update_pipeline,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import ENTITY_IDS, PIPELINE_NAME, STORE_KEY, STORE_VERSION

_LOGGER = logging.getLogger(__name__)


async def async_ensure_pipeline(hass: HomeAssistant) -> None:
    """Create "Wake word recording" (our STT, agent and text-to-speech - the preferred pipeline's
    voice with the "speak now" signal) or point an existing one back at our entities."""
    stt_id, agent_id, tts_id = ENTITY_IDS["stt"], ENTITY_IDS["conversation"], ENTITY_IDS["tts"]
    pipeline = next((p for p in async_get_pipelines(hass) if p.name == PIPELINE_NAME), None)
    preferred = async_get_pipeline(hass)
    if pipeline is None:
        if not preferred.tts_engine:
            raise HomeAssistantError(
                "The preferred Assist pipeline has no text-to-speech - the satellite could not say the instructions"
            )
        pipeline = await async_create_default_pipeline(hass, stt_id, preferred.tts_engine, PIPELINE_NAME)
        if pipeline is None:
            raise HomeAssistantError(f"Could not create the pipeline '{PIPELINE_NAME}'")
        await async_update_pipeline(
            hass,
            pipeline,
            conversation_engine=agent_id,
            tts_engine=tts_id,
            tts_language=preferred.tts_language,
            tts_voice=preferred.tts_voice,
        )
        _LOGGER.info("Created the Assist pipeline '%s'", PIPELINE_NAME)
    elif (pipeline.stt_engine, pipeline.conversation_engine, pipeline.tts_engine) != (stt_id, agent_id, tts_id):
        await async_update_pipeline(hass, pipeline, stt_engine=stt_id, conversation_engine=agent_id, tts_engine=tts_id)


def pipeline_selects(hass: HomeAssistant, satellite: str) -> list[str]:
    """The satellite's "Assistant" selects (pipeline choosers) on the same device.

    A satellite with two wake words (Voice PE) has one per wake word, and a conversation the
    recorder starts runs on either of them, so a session switches them all.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(satellite)
    if entry is None or entry.device_id is None:
        raise HomeAssistantError(f"{satellite} is not a voice satellite with a device")
    selects = sorted(
        candidate.entity_id
        for candidate in er.async_entries_for_device(registry, entry.device_id)
        if candidate.domain == "select" and PIPELINE_SELECT.search(candidate.unique_id)
    )
    if not selects:
        raise HomeAssistantError(f"{satellite} has no pipeline select")
    return selects


async def async_select(hass: HomeAssistant, select: str, option: str) -> None:
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": option}, blocking=True)


# The unique id of a satellite's pipeline select: "<mac>-pipeline", "<mac>-pipeline_2" …
PIPELINE_SELECT = re.compile(r"-pipeline(_\d+)?$")


class Restorer:
    """Remembers each switched satellite's pipeline before a session, also across restarts.

    Every switched select is kept separately until it is restored, and switching and restoring
    run one at a time - a restore finishing late never drops the entry of a newer switch.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict] = Store(hass, STORE_VERSION, STORE_KEY)
        self._lock = asyncio.Lock()

    async def _async_pending(self) -> dict[str, str]:
        """select -> option to put back; also reads the single entry of version 0.1.0."""
        data = await self._store.async_load() or {}
        if "select" in data:
            return {data["select"]: data["option"]}
        return dict(data.get("pending", {}))

    async def _async_save(self, pending: dict[str, str]) -> None:
        # an empty dict, not async_remove: a removed store could still load its cached data
        await self._store.async_save({"pending": pending})

    async def async_switch(self, select: str) -> str:
        """Switch to the recording pipeline; returns the previous option."""
        async with self._lock:
            state = self.hass.states.get(select)
            if state is None:
                raise HomeAssistantError(f"{select} is not available")
            previous = state.state
            if previous != PIPELINE_NAME:
                pending = await self._async_pending()
                pending[select] = previous
                await self._async_save(pending)
            await async_select(self.hass, select, PIPELINE_NAME)
            return previous

    async def async_restore(self, select: str | None = None) -> None:
        """Put back the remembered pipeline of one select, or of all (no-op when none pending)."""
        async with self._lock:
            pending = await self._async_pending()
            for name in [select] if select else list(pending):
                if name not in pending:
                    continue
                option = pending[name]
                state = self.hass.states.get(name)
                if state is not None and option in state.attributes.get("options", []):
                    try:
                        await async_select(self.hass, name, option)
                    except HomeAssistantError as err:
                        # stays pending, tried again after the next session or restart
                        _LOGGER.warning("Could not restore %s to '%s': %s", name, option, err)
                        continue
                del pending[name]
            await self._async_save(pending)
