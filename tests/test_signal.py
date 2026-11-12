"""The "speak now" signal and the text-to-speech of the recording pipeline."""

from __future__ import annotations

import shutil
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.wake_word_recorder import listen_signal
from custom_components.wake_word_recorder import tts as tts_module
from custom_components.wake_word_recorder.listen_signal import (
    GAP_SECONDS,
    TAIL_SECONDS,
    async_append_signal,
    async_chime,
    beep,
    read_wav,
    write_wav,
)

from .conftest import SATELLITE, Satellite

TTS = "tts.wake_word_recorder"
SIGNAL = "select.wake_word_recorder_signal"


def _speech(rate: int = 22050, channels: int = 1, seconds: float = 0.5) -> bytes:
    return write_wav(rate, channels, 2, b"\x10\x00" * int(rate * seconds) * channels)


def test_read_wav_roundtrip_and_streamed_header() -> None:
    data = _speech(16000, 2, 0.1)
    rate, channels, width, pcm = read_wav(data)
    assert (rate, channels, width, len(pcm)) == (16000, 2, 2, 1600 * 4)
    # ffmpeg writing to a pipe cannot seek back: RIFF and data sizes are placeholders
    streamed = data[:4] + b"\xff\xff\xff\xff" + data[8:40] + b"\xff\xff\xff\xff" + data[44:]
    assert read_wav(streamed)[3] == pcm
    with pytest.raises(ValueError, match="not a WAV"):
        read_wav(b"ID3 mp3 data")


def test_beep_is_short_and_not_clipped() -> None:
    pcm = beep(16000, 1, 2)
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    assert 0.2 < len(samples) / 16000 < 0.4
    assert 8000 < max(samples) < 32767 and samples[0] == 0
    assert len(beep(8000, 2, 1)) == len(beep(8000, 1, 1)) * 2  # 8-bit, stereo


async def test_append_beep_and_off(hass: HomeAssistant) -> None:
    speech = _speech()
    rate, channels, width, pcm = read_wav(speech)
    with_beep = await async_append_signal(hass, speech, "beep")
    tail = int(rate * TAIL_SECONDS) * 2
    expected = len(pcm) + int(rate * GAP_SECONDS) * 2 + len(beep(rate, channels, width)) + tail
    assert read_wav(with_beep)[:3] == (rate, channels, width) and len(read_wav(with_beep)[3]) == expected
    assert read_wav(with_beep)[3].endswith(b"\x00" * tail)  # the microphone opens on silence
    # no signal: still the silent tail, so the end of the instruction is not recorded
    assert read_wav(await async_append_signal(hass, speech, "off"))[3] == pcm + b"\x00" * tail


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_chime_is_home_assistants_announcement_sound(hass: HomeAssistant) -> None:
    listen_signal._chimes.clear()
    with patch(
        "homeassistant.components.ffmpeg.get_ffmpeg_manager",
        return_value=SimpleNamespace(binary=shutil.which("ffmpeg")),
    ):
        pcm = await async_chime(hass, 16000, 1)
        speech = _speech(16000)
        with_chime = await async_append_signal(hass, speech, "chime")
    assert 1.5 < len(pcm) / 2 / 16000 < 3  # preannounce.mp3 is about 2 s
    assert read_wav(with_chime)[3][: -int(16000 * TAIL_SECONDS) * 2].endswith(pcm)


async def test_chime_falls_back_to_beep(hass: HomeAssistant) -> None:
    listen_signal._chimes.clear()
    with patch(
        "homeassistant.components.ffmpeg.get_ffmpeg_manager", return_value=SimpleNamespace(binary="/nonexistent/ffmpeg")
    ):
        assert await async_chime(hass, 16000, 1) is None
        result = await async_append_signal(hass, _speech(16000), "chime")
    assert read_wav(result)[3][: -int(16000 * TAIL_SECONDS) * 2].endswith(beep(16000, 1, 2))


def _pipeline(engine, language="pl", voice="gosia"):
    return SimpleNamespace(tts_engine=engine, tts_language=language, tts_voice=voice)


def test_voice_settings_skip_the_recording_pipeline(hass: HomeAssistant) -> None:
    ours, real = _pipeline(TTS), _pipeline("tts.piper")
    with (
        patch.object(tts_module, "async_get_pipeline", return_value=ours),
        patch.object(tts_module, "async_get_pipelines", return_value=[ours, real]),
    ):
        assert tts_module.voice_settings(hass) == ("tts.piper", "pl", "gosia")
    with (
        patch.object(tts_module, "async_get_pipeline", return_value=ours),
        patch.object(tts_module, "async_get_pipelines", return_value=[ours, _pipeline(None)]),
        pytest.raises(HomeAssistantError, match="No Assist pipeline"),
    ):
        tts_module.voice_settings(hass)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_instructions_end_with_the_signal(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        "wake_word_recorder",
        "start_session",
        {"satellite": SATELLITE, "speaker": "dad", "word": "dzarwis"},
        blocking=True,
    )
    prompt = satellite.conversations[-1]["start_message"]
    entity = tts_module.tts.get_engine_instance(hass, TTS)
    speech = _speech(16000)
    get_audio = AsyncMock(return_value=("wav", speech))
    with (
        patch.object(tts_module, "voice_settings", return_value=("tts.piper", "pl", "gosia")),
        patch.object(tts_module.tts, "generate_media_source_id", return_value="media-source://tts/x") as media,
        patch.object(tts_module.tts, "async_get_media_source_audio", get_audio),
    ):
        extension, with_signal = await entity.async_get_tts_audio(prompt, "pl", {"signal": "beep"})
        _, plain = await entity.async_get_tts_audio("Kończę nagrywanie.", "pl", {"signal": "beep"})
        _, silent = await entity.async_get_tts_audio(prompt, "pl", {"signal": "off"})
    assert extension == "wav"
    assert media.call_args.args[1:] == (
        prompt,
        "tts.piper",
        "pl",
        {"preferred_format": "wav", "preferred_sample_bytes": 2, "voice": "gosia"},
    )
    assert len(read_wav(with_signal)[3]) > len(read_wav(speech)[3]) + 16000 * 2 * GAP_SECONDS
    assert plain == speech  # not followed by listening: as it is
    assert len(read_wav(silent)[3]) == len(read_wav(speech)[3]) + int(16000 * TAIL_SECONDS) * 2
    assert entity.default_options == {"signal": "beep"}
    assert "pl" in entity.supported_languages


async def test_signal_select(hass: HomeAssistant, entry) -> None:
    mock_restore_cache(hass, [State(SIGNAL, "chime")])
    await _setup(hass, entry)
    state = hass.states.get(SIGNAL)
    assert state.state == "chime" and state.attributes["options"] == ["beep", "chime", "off"]
    await hass.services.async_call("select", "select_option", {"entity_id": SIGNAL, "option": "off"}, blocking=True)
    assert entry.runtime_data.signal == "off" and hass.states.get(SIGNAL).state == "off"
