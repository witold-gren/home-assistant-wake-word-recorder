"""Speech to text of the recording pipeline: stores the audio as a take instead of transcribing."""

from __future__ import annotations

from collections.abc import AsyncIterable

from homeassistant.components import stt
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RecorderConfigEntry, device_info
from .const import ENTITY_IDS, TRANSCRIPT
from .session import MAX_AUDIO_BYTES

# the pipeline is created for the HA language - accept the common ones
LANGUAGES = ["cs", "da", "de", "en", "es", "fi", "fr", "it", "nb", "nl", "pl", "pt", "ru", "sk", "sv", "uk"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RecorderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([RecorderStt(entry)])


class RecorderStt(stt.SpeechToTextEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "stt"

    def __init__(self, entry: RecorderConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-stt"
        self.entity_id = ENTITY_IDS["stt"]
        self._attr_device_info = device_info(entry)

    @property
    def supported_languages(self) -> list[str]:
        language = self.hass.config.language if self.hass else None
        return sorted({*LANGUAGES, *([language] if language else [])})

    @property
    def supported_formats(self) -> list[stt.AudioFormats]:
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        return [stt.AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[stt.AudioChannels]:
        return [stt.AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(
        self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> stt.SpeechResult:
        chunks: list[bytes] = []
        size = 0
        async for chunk in stream:  # always read to the end, the pipeline waits for it
            if size < MAX_AUDIO_BYTES:
                chunks.append(chunk)
                size += len(chunk)
        session = self._entry.runtime_data.session
        if session is not None:
            await session.async_add_audio(b"".join(chunks))
        # never empty: an empty transcript ends the pipeline with an error (red ring)
        return stt.SpeechResult(TRANSCRIPT, stt.SpeechResultState.SUCCESS)
