"""The "speak now" signal appended to the last instruction before the satellite listens.

`beep` is a short rising two-tone made here; `chime` is Home Assistant's own announcement sound
(assist_satellite/preannounce.mp3), decoded with ffmpeg. Both are added to the end of the spoken
WAV, so the signal plays right before the microphone opens - the satellite firmware has no sound
for a conversation that continues without a wake word. A silent tail follows, because the
microphone stream starts before the speaker has finished playing.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
from pathlib import Path
import struct
import wave

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

SIGNAL_BEEP = "beep"
SIGNAL_CHIME = "chime"
SIGNAL_OFF = "off"
SIGNALS = (SIGNAL_BEEP, SIGNAL_CHIME, SIGNAL_OFF)

GAP_SECONDS = 0.25  # silence between the speech and the signal
# silence after it: the microphone opens ~0.4 s before the speaker has finished playing, so the
# end of the prompt must be silent or it is recorded at the start of the take
TAIL_SECONDS = 0.6
# (frequency Hz, seconds) - silence for frequency 0
BEEP_TONES = ((660.0, 0.09), (0.0, 0.03), (990.0, 0.14))
BEEP_LEVEL = 0.35

_chimes: dict[tuple[int, int], bytes] = {}


def _pcm(samples: list[float], channels: int, width: int) -> bytes:
    """Float samples (-1..1) as little-endian PCM of the given sample width, one copy per channel."""
    if width == 1:  # 8-bit WAV is unsigned
        values = [max(0, min(255, int(128 + s * 127))) for s in samples for _ in range(channels)]
        return bytes(values)
    peak = (1 << (8 * width - 1)) - 1
    ints = [max(-peak - 1, min(peak, int(s * peak))) for s in samples for _ in range(channels)]
    if width == 2:
        return struct.pack(f"<{len(ints)}h", *ints)
    if width == 4:
        return struct.pack(f"<{len(ints)}i", *ints)
    return b"".join(v.to_bytes(width, "little", signed=True) for v in ints)


def silence(rate: int, channels: int, width: int, seconds: float) -> bytes:
    return _pcm([0.0] * int(rate * seconds), channels, width)


def beep(rate: int, channels: int, width: int) -> bytes:
    """Two short tones with soft edges (no clicks)."""
    samples: list[float] = []
    for freq, seconds in BEEP_TONES:
        n = int(rate * seconds)
        for i in range(n):
            if freq == 0.0:
                samples.append(0.0)
                continue
            envelope = math.sin(math.pi * i / n) ** 0.5
            samples.append(BEEP_LEVEL * envelope * math.sin(2 * math.pi * freq * i / rate))
    return _pcm(samples, channels, width)


def preannounce_path() -> Path:
    from homeassistant.components import assist_satellite  # noqa: PLC0415
    from homeassistant.components.assist_satellite.const import PREANNOUNCE_FILENAME  # noqa: PLC0415

    return Path(assist_satellite.__file__).parent / PREANNOUNCE_FILENAME


async def async_chime(hass: HomeAssistant, rate: int, channels: int) -> bytes | None:
    """HA's announcement sound as 16-bit PCM (cached), None when ffmpeg cannot decode it."""
    key = (rate, channels)
    if key not in _chimes:
        from homeassistant.components.ffmpeg import get_ffmpeg_manager  # noqa: PLC0415

        try:
            process = await asyncio.create_subprocess_exec(
                get_ffmpeg_manager(hass).binary,
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(preannounce_path()),
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ar",
                str(rate),
                "-ac",
                str(channels),
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(process.communicate(), timeout=10)
        except (OSError, TimeoutError) as error:
            _LOGGER.warning("Could not decode the announcement sound: %s", error)
            return None
        if process.returncode != 0 or not out:
            _LOGGER.warning("Could not decode the announcement sound: %s", err.decode(errors="replace").strip())
            return None
        _chimes[key] = out
    return _chimes[key]


def read_wav(data: bytes) -> tuple[int, int, int, bytes]:
    """(rate, channels, sample width, PCM) of a PCM WAV.

    Also reads WAVs streamed by ffmpeg, whose RIFF and data sizes are placeholders.
    """
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        chunk, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        body = pos + 8
        if chunk == b"fmt ":
            audio_format, channels, rate = struct.unpack("<HHI", data[body : body + 8])
            bits = struct.unpack("<H", data[body + 14 : body + 16])[0]
            if audio_format not in (1, 0xFFFE):
                raise ValueError("not a PCM WAV file")
            fmt = (rate, channels, bits // 8)
        elif chunk == b"data":
            if fmt is None:
                raise ValueError("WAV data before its format")
            pcm = data[body : body + size] if size <= len(data) - body else data[body:]
            frame = fmt[1] * fmt[2]
            return (*fmt, pcm[: len(pcm) - len(pcm) % frame])
        pos = body + size + (size % 2)
    raise ValueError("WAV file without data")


def write_wav(rate: int, channels: int, width: int, pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return out.getvalue()


async def async_append_signal(hass: HomeAssistant, data: bytes, kind: str) -> bytes:
    """The WAV `data` followed by a pause, the signal (none for `off`) and a silent tail."""
    rate, channels, width, pcm = read_wav(data)
    sound = b""
    if kind != SIGNAL_OFF:
        sound = await async_chime(hass, rate, channels) if kind == SIGNAL_CHIME and width == 2 else None
        if sound is None:
            sound = beep(rate, channels, width)
        sound = silence(rate, channels, width, GAP_SECONDS) + sound
    return write_wav(rate, channels, width, pcm + sound + silence(rate, channels, width, TAIL_SECONDS))
