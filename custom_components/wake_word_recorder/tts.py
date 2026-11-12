"""Text-to-speech of the recording pipeline: the household's usual voice plus the "speak now" signal.

The speech itself comes from the text-to-speech of the preferred Assist pipeline (same voice as
every day). Instructions after which the satellite listens for a take end with the signal chosen
in the "Signal" select; everything else (end of the session, errors) is spoken as it is.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import tts
from homeassistant.components.assist_pipeline import async_get_pipeline, async_get_pipelines
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RecorderConfigEntry, device_info
from .const import ENTITY_IDS
from .listen_signal import async_append_signal

OPTION_SIGNAL = "signal"
OPTION_VOICE = "voice"
# 16-bit WAV from the real voice, so the signal can be appended
WAV_OPTIONS = {tts.ATTR_PREFERRED_FORMAT: "wav", tts.ATTR_PREFERRED_SAMPLE_BYTES: 2}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RecorderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([RecorderTts(entry)])


@callback
def voice_settings(hass: HomeAssistant) -> tuple[str, str | None, str | None]:
    """(engine, language, voice) of the household's usual text-to-speech.

    The preferred pipeline, or the first one with another text-to-speech when the preferred one
    is the recording pipeline itself.
    """
    ours = ENTITY_IDS["tts"]
    candidates = [async_get_pipeline(hass), *async_get_pipelines(hass)]
    for pipeline in candidates:
        if pipeline.tts_engine and pipeline.tts_engine != ours:
            return pipeline.tts_engine, pipeline.tts_language, pipeline.tts_voice
    raise HomeAssistantError("No Assist pipeline with a text-to-speech to speak the instructions")


class RecorderTts(tts.TextToSpeechEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "tts"

    def __init__(self, entry: RecorderConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-tts"
        self.entity_id = ENTITY_IDS["tts"]
        self._attr_device_info = device_info(entry)

    @property
    def default_language(self) -> str:
        return self.hass.config.language

    @property
    def supported_languages(self) -> list[str]:
        # whatever the real voice speaks (the pipeline copies its language)
        try:
            engine, language, _ = voice_settings(self.hass)
        except HomeAssistantError:
            return [self.hass.config.language]
        instance = tts.get_engine_instance(self.hass, engine)
        languages = list(getattr(instance, "supported_languages", None) or [])
        return sorted({*languages, self.hass.config.language, *([language] if language else [])})

    @property
    def supported_options(self) -> list[str]:
        return [OPTION_VOICE, OPTION_SIGNAL]

    @property
    def default_options(self) -> dict[str, Any]:
        # part of the cache key: a cached instruction changes with the chosen signal
        return {OPTION_SIGNAL: self._entry.runtime_data.signal}

    async def async_get_tts_audio(self, message: str, language: str, options: dict[str, Any]) -> tts.TtsAudioType:
        engine, voice_language, voice = voice_settings(self.hass)
        inner = dict(WAV_OPTIONS)
        if voice:
            inner[OPTION_VOICE] = voice
        media_id = tts.generate_media_source_id(self.hass, message, engine, voice_language or language, inner)
        _extension, data = await tts.async_get_media_source_audio(self.hass, media_id)
        runtime = self._entry.runtime_data
        # instructions of a recording session after which the satellite listens for a take
        if message in getattr(runtime.session, "listen_prompts", ()):
            data = await async_append_signal(self.hass, data, options.get(OPTION_SIGNAL, runtime.signal))
        return "wav", data
