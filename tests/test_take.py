"""Audio handling and the household file, without Home Assistant."""

import pytest

from custom_components.wake_word_recorder import household as hh
from custom_components.wake_word_recorder import messages, take

from .conftest import HOUSEHOLD, pcm_silence, pcm_word


def test_every_language_has_every_variant_and_message():
    for language in messages.TAKES:
        assert set(messages.TAKES[language]) == set(messages.TAKE_ORDER)
        assert set(messages.MESSAGES[language]) == set(messages.MESSAGES["en"])


def test_household_errors(tmp_path):
    with pytest.raises(hh.HouseholdError, match="not found"):
        hh.load(tmp_path)
    (tmp_path / "household.yaml").write_text("speakers: [")
    with pytest.raises(hh.HouseholdError):
        hh.load(tmp_path)
    for data, message in [
        ([], "mapping"),
        ({"speakers": []}, "no speakers"),
        ({"speakers": [{"group": "adult"}]}, "id"),
        ({"speakers": [{"id": "a"}], "words": {"w": "x"}}, "settings"),
        ({"speakers": [{"id": "a"}], "words": {"w": {"speakers": ["b"]}}}, "unknown"),
    ]:
        with pytest.raises(hh.HouseholdError, match=message):
            hh.parse(data, tmp_path)


def test_household_defaults(tmp_path):
    household = hh.parse({"speakers": [{"id": "a"}], "words": {"w": {"speakers": "a"}}}, tmp_path)
    assert household.words["w"].wake_word == "w" and household.words["w"].speakers == ("a",)
    assert household.target("a") == 12 and household.name == tmp_path.name


@pytest.mark.parametrize(
    "pcm, issues",
    [
        (pcm_word(), []),
        (pcm_silence(), ["no_speech"]),
        (pcm_word(seconds=0.1), ["too_short"]),
        (pcm_word(seconds=3.6), ["too_long"]),
        (pcm_word(level=0.003), ["too_quiet"]),
        (pcm_word(level=3.0), ["clipped"]),
    ],
)
def test_quality(pcm, issues):
    samples = take.to_samples(pcm)
    assert take.quality_issues(take.analyze(samples)) == issues


def test_clipped_detection():
    loud = take.array("h", [32767, -32768] * 4000 + [0] * 8000)
    assert "clipped" in take.quality_issues(take.analyze(loud))


def test_prepare_trims_and_normalises():
    samples = take.to_samples(pcm_silence(2) + pcm_word(level=0.1) + pcm_silence(2))
    out = take.prepare(samples)
    assert len(out) / 16000 < 1.5
    assert max(abs(s) for s in out) == pytest.approx(32768 * 10 ** (-1 / 20), rel=0.01)
    assert take.prepare(take.array("h", [0] * 100)) == take.array("h", [0] * 100)


def test_save_take(tmp_path):
    household = hh.parse(HOUSEHOLD, tmp_path)
    first = take.save_take(household, "robo", "kid", pcm_word(), "assist_satellite.x")
    assert first.file.name == "kid_000_near.wav" and first.issues == []
    second = take.save_take(household, "robo", "kid", pcm_word(), "assist_satellite.x")
    assert second.file.name == "kid_001_far.wav"
    bad = take.save_take(household, "robo", "kid", pcm_silence(), "assist_satellite.x")
    assert bad.file is None and bad.issues == ["no_speech"] and bad.variant == "quiet"
    progress = take.progress(household, "robo", "kid")
    assert (progress.done, progress.target, progress.variant) == (2, 6, "quiet")
    log = (tmp_path / "data" / "real" / "recordings.csv").read_text().splitlines()
    assert log[0] == "time,file,word,speaker,satellite" and len(log) == 3
    assert "data/real/robo/kid/kid_001_far.wav,robo,kid,assist_satellite.x" in log[2]


def test_saved_file_is_16_khz_mono(tmp_path):
    import wave

    household = hh.parse(HOUSEHOLD, tmp_path)
    result = take.save_take(household, "robo", "kid", pcm_word(), "s")
    with wave.open(str(result.file)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)


def test_texts():
    pl = messages.Texts("pl-PL")
    assert pl.instruction(3, 6, "far", "Dżarwis") == "Nagranie 3 z 6. Odejdź na drugi koniec pokoju. Powiedz: Dżarwis."
    assert pl.instruction(7, 6, "near", "X").startswith("Nagranie 7 z 7.")
    assert pl.issues(["too_quiet", "unknown"]) == "Za cicho."
    assert messages.Texts("de").language == "en" and messages.Texts(None).language == "en"


def test_create_household(tmp_path):
    household = hh.create(tmp_path / "new", [("tomek", "adult"), ("ola", "child")], "pl")
    assert household.target("ola") == 18 and household.words == {}
    with pytest.raises(hh.HouseholdError, match="already exists"):
        hh.create(tmp_path / "new", [("a", "adult")], "pl")
    with pytest.raises(hh.HouseholdError, match="no speakers"):
        hh.create(tmp_path / "other", [], "pl")
    with pytest.raises(hh.HouseholdMissing):
        hh.load(tmp_path / "other")


def _noise(seconds: float, level: float, seed: int = 4) -> bytes:
    import random
    import struct

    rng = random.Random(seed)
    values = [max(-32768, min(32767, int(rng.gauss(0, level) * 32767))) for _ in range(int(seconds * 16000))]
    return struct.pack(f"<{len(values)}h", *values)


def _mix(*parts: tuple[float, bytes], seconds: float) -> bytes:
    """Sum 16-bit parts placed at their start times."""
    total = take.array("h", [0] * int(seconds * 16000))
    acc = [0] * len(total)
    for start, pcm in parts:
        offset = int(start * 16000)
        for i, value in enumerate(take.to_samples(pcm)):
            if offset + i < len(acc):
                acc[offset + i] += value
    return take.array("h", (max(-32768, min(32767, v)) for v in acc)).tobytes()


def test_word_in_background_noise():
    # TV-like noise 30 dB louder than before: the word is still the word, not the whole recording
    pcm = _mix((0.0, _noise(4.0, 0.02)), (1.0, pcm_word(pad=0.0)), seconds=4.0)
    stats = take.analyze(take.to_samples(pcm))
    assert 0.3 < stats.speech < 0.9 and take.quality_issues(stats) == []


def test_word_drowned_in_noise():
    pcm = _mix((0.0, _noise(3.0, 0.3)), (1.0, pcm_word(pad=0.0)), seconds=3.0)
    assert take.quality_issues(take.analyze(take.to_samples(pcm))) == ["too_noisy"]


def test_end_of_the_instruction_is_cut_off():
    # the speaker still plays the last 0.3 s when the microphone opens, then the person speaks
    playback = pcm_word(seconds=0.6, pad=0.0, freq=330.0)[len(pcm_word(seconds=0.6, pad=0.0)) // 2 :]
    pcm = _mix((0.0, _noise(3.0, 0.002)), (0.0, playback), (0.8, pcm_word(pad=0.0)), seconds=3.0)
    samples = take.to_samples(pcm)
    cut = take.playback_tail(samples)
    assert 0.25 * 16000 < cut < 0.6 * 16000
    # a quiet start, or a person talking from the first moment on, is kept
    assert take.playback_tail(take.to_samples(pcm_word())) == 0
    assert take.playback_tail(take.to_samples(pcm_word(seconds=1.5, pad=0.0))) == 0


def test_save_take_skips_the_instruction(tmp_path):
    household = hh.parse(HOUSEHOLD, tmp_path)
    playback = pcm_word(seconds=0.6, pad=0.0, freq=330.0)[len(pcm_word(seconds=0.6, pad=0.0)) // 2 :]
    pcm = _mix((0.0, _noise(3.5, 0.002)), (0.0, playback), (0.6, pcm_word(pad=0.0)), seconds=3.5)
    result = take.save_take(household, "robo", "kid", pcm, "assist_satellite.x")
    assert result.issues == []
    import wave

    with wave.open(str(result.file)) as wav:
        seconds = wav.getnframes() / 16000
    assert seconds < 1.4  # the word with its padding, without the instruction before it
