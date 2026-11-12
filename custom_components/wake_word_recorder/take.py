"""One recorded take: quality check, trimming and saving, in pure Python (no numpy).

Files follow the toolkit's naming, ``data/real/<word>/<speaker>/<speaker>_<NNN>_<variant>.wav``
(16 kHz mono 16-bit), so ``wakeword check`` and training read them as they are. Limits are the
toolkit's (wakeword/dataset.py).
"""

from __future__ import annotations

from array import array
import csv
from dataclasses import dataclass, field
from datetime import datetime
import math
from pathlib import Path
import re
import sys
import wave

from .household import Household
from .messages import take_variant

SAMPLE_RATE = 16000
FRAME = SAMPLE_RATE * 20 // 1000  # 20 ms
FULL_SCALE = 32768.0
CLIP_LEVEL = 32767 * 0.999
MIN_SPEECH = 0.25
MAX_SPEECH = 3.0
MIN_RMS_DB = -42.0
MAX_CLIPPED = 0.001
PAD_MS = 250
LOG_FILE = "recordings.csv"  # in data/real: which device recorded which take

TAKE_PATTERN = re.compile(r"^(?P<speaker>[a-z0-9_]+?)_(?P<index>\d{3})_(?P<variant>[a-z_]+)\.wav$")


def to_samples(pcm: bytes) -> array:
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def dbfs(value: float) -> float:
    return 20 * math.log10(value) if value > 0 else float("-inf")


def _rms(samples, start: int = 0, end: int | None = None) -> float:
    end = len(samples) if end is None else end
    if end <= start:
        return 0.0
    total = 0
    for i in range(start, end):
        total += samples[i] * samples[i]
    return math.sqrt(total / (end - start)) / FULL_SCALE


def frame_levels(samples) -> list[float]:
    return [dbfs(_rms(samples, i, i + FRAME)) for i in range(0, len(samples) - FRAME + 1, FRAME)]


# --- where the word is: kept identical in wakeword/audio.py and the integration's take.py ---------
NOISE_PERCENTILE = 0.1  # the quietest 10 % of frames tell the background level
WORD_MARGIN_DB = 10.0  # the word stands out at least this much from the background
WORD_RANGE_DB = 30.0  # and is never more than this below its loudest frame
WORD_GAP_FRAMES = 25  # 0.5 s - a pause inside the word ("hej … dżarwis") keeps it one piece
WORD_EDGE_DB = 4.0  # the word's quiet start and end: still this much above the background
MIN_SNR_DB = 8.0  # loudest frame above the background - less and the word is lost in noise
SILENCE_DB = -120.0


def noise_floor(levels) -> float:
    """Background level: a low percentile of the frame levels (dBFS)."""
    ordered = sorted(max(level, SILENCE_DB) for level in levels)  # digital silence counts as very quiet
    return ordered[int(NOISE_PERCENTILE * (len(ordered) - 1))] if ordered else SILENCE_DB


def word_frames(levels) -> tuple[int, int] | None:
    """First and last frame of the word, or None for silence.

    Frames louder than the background (by WORD_MARGIN_DB) form segments; pauses up to
    WORD_GAP_FRAMES stay inside one segment. The segment with the most energy above the background
    is the word - other sounds before or after it (a door, a cough, the TV) are left out, while
    the noise under the word stays part of it. Its edges then follow the sound down to
    WORD_EDGE_DB above the background.
    """
    levels = list(levels)
    if not levels or max(levels) < -70:
        return None
    peak, floor = max(levels), noise_floor(levels)
    cut = max(peak - WORD_RANGE_DB, min(floor + WORD_MARGIN_DB, peak))
    best, best_score = None, -1.0
    start = last = None
    score = 0.0
    for i, level in enumerate(levels + [float("-inf")] * (WORD_GAP_FRAMES + 1)):
        if level >= cut:
            if start is None:
                start, score = i, 0.0
            last = i
            score += 10 ** ((level - floor) / 10)
        elif start is not None and i - last > WORD_GAP_FRAMES:
            if score > best_score:
                best, best_score = (start, last), score
            start = None
    # widen to the quiet start and end of the word (soft consonants, fade-out)
    first, end = best
    edge = max(floor + WORD_EDGE_DB, peak - WORD_RANGE_DB)
    while first > 0 and levels[first - 1] >= edge:
        first -= 1
    while end < len(levels) - 1 and levels[end + 1] >= edge:
        end += 1
    return first, end


def snr_db(levels) -> float:
    """How far the loudest frame stands out from the background (dB)."""
    levels = list(levels)
    return max(levels) - noise_floor(levels) if levels else 0.0


PLAYBACK_FRAMES = 3  # sound within the first 60 ms of a turn started before the microphone
PLAYBACK_GAP_FRAMES = 4  # 80 ms of quiet ends it
MAX_PLAYBACK_FRAMES = 50  # 1 s - a longer sound at the start is the speaker, not the device


def playback_tail(samples) -> int:
    """Samples at the start that are the satellite's own speaker (end of the instruction or signal).

    The microphone stream starts while the last ~0.4 s of the instruction is still playing; that
    sound runs from the very first frame, before the person could react. Returns 0 when the turn
    starts quietly or the sound runs on (a person who spoke over the instruction).
    """
    levels = frame_levels(samples)
    if len(levels) <= PLAYBACK_FRAMES:
        return 0
    floor = noise_floor(levels)
    loud = [level >= floor + WORD_MARGIN_DB for level in levels]
    if not any(loud[:PLAYBACK_FRAMES]):
        return 0
    quiet = 0
    for i, is_loud in enumerate(loud):
        quiet = 0 if is_loud else quiet + 1
        if quiet >= PLAYBACK_GAP_FRAMES:
            end = i + 1
            return end * FRAME if end <= MAX_PLAYBACK_FRAMES and any(loud[end:]) else 0
    return 0


def speech_bounds(samples, pad_ms: int = 150) -> tuple[int, int]:
    """Sample range of the word widened by `pad_ms`; (0, 0) for silence."""
    frames = word_frames(frame_levels(samples))
    if frames is None:
        return 0, 0
    pad = SAMPLE_RATE * pad_ms // 1000
    return max(0, frames[0] * FRAME - pad), min(len(samples), (frames[1] + 1) * FRAME + pad)


@dataclass(frozen=True)
class Stats:
    duration: float
    speech: float
    rms_db: float
    clipped: float
    snr_db: float = 99.0


def analyze(samples) -> Stats:
    if not len(samples):
        return Stats(0.0, 0.0, float("-inf"), 0.0)
    start, end = speech_bounds(samples, pad_ms=0)
    clipped = sum(1 for s in samples if abs(s) >= CLIP_LEVEL) / len(samples)
    return Stats(
        len(samples) / SAMPLE_RATE,
        (end - start) / SAMPLE_RATE,
        dbfs(_rms(samples, start, end)),
        clipped,
        snr_db(frame_levels(samples)),
    )


def quality_issues(stats: Stats) -> list[str]:
    if stats.speech <= 0:
        return ["no_speech"]
    issues = []
    if stats.speech < MIN_SPEECH:
        issues.append("too_short")
    if stats.speech > MAX_SPEECH:
        issues.append("too_long")
    if stats.rms_db < MIN_RMS_DB:
        issues.append("too_quiet")
    if stats.clipped > MAX_CLIPPED:
        issues.append("clipped")
    if stats.snr_db < MIN_SNR_DB:
        # the word's length and loudness mean nothing when it cannot be told from the noise
        issues = [i for i in issues if i not in ("too_short", "too_long", "too_quiet")] + ["too_noisy"]
    return issues


def prepare(samples) -> array:
    """Silence trimmed (with padding) and the peak at -1 dBFS - what the toolkit stores."""
    start, end = speech_bounds(samples, pad_ms=PAD_MS)
    part = samples[start:end] if end > start else samples
    peak = max((abs(s) for s in part), default=0)
    if not peak:
        return array("h", part)
    gain = (FULL_SCALE * 10 ** (-1 / 20)) / peak
    return array("h", (max(-32768, min(32767, round(s * gain))) for s in part))


def write_wav(path: Path, samples: array) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = array("h", samples)
    if sys.byteorder == "big":
        data.byteswap()
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(data.tobytes())


def list_takes(folder: Path, speaker: str) -> list[tuple[int, Path]]:
    if not folder.is_dir():
        return []
    takes = []
    for path in folder.glob("*.wav"):
        match = TAKE_PATTERN.match(path.name)
        if match and match["speaker"] == speaker:
            takes.append((int(match["index"]), path))
    return sorted(takes)


@dataclass
class Progress:
    done: int
    target: int
    variant: str  # delivery variant of the next take


def progress(household: Household, word: str, speaker: str) -> Progress:
    """Blocking - run in the executor."""
    done = len(list_takes(household.real_dir(word, speaker), speaker))
    return Progress(done, household.target(speaker), take_variant(done))


@dataclass
class TakeResult:
    file: Path | None  # None when the take was not good enough and was not stored
    issues: list[str] = field(default_factory=list)
    variant: str = ""


def save_take(household: Household, word: str, speaker: str, pcm: bytes, satellite: str) -> TakeResult:
    """Check a recording and store it when it is good. Blocking - run in the executor."""
    samples = to_samples(pcm)
    samples = samples[playback_tail(samples) :]
    issues = quality_issues(analyze(samples))
    folder = household.real_dir(word, speaker)
    takes = list_takes(folder, speaker)
    variant = take_variant(len(takes))
    if issues:
        return TakeResult(None, issues, variant)
    index = takes[-1][0] + 1 if takes else 0
    path = folder / f"{speaker}_{index:03d}_{variant}.wav"
    write_wav(path, prepare(samples))
    _log(household, path, word, speaker, satellite)
    return TakeResult(path, [], variant)


def _log(household: Household, path: Path, word: str, speaker: str, satellite: str) -> None:
    log = household.root / "data" / "real" / LOG_FILE
    new = not log.exists()
    with log.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["time", "file", "word", "speaker", "satellite"])
        writer.writerow(
            [
                datetime.now().isoformat(timespec="seconds"),
                path.relative_to(household.root).as_posix(),
                word,
                speaker,
                satellite,
            ]
        )
