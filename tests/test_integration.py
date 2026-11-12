"""The integration in Home Assistant: config flow, entities, a whole recording session."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components import conversation, stt
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
import homeassistant.util.dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.wake_word_recorder import pipeline as pipeline_module
from custom_components.wake_word_recorder.const import (
    CONF_HOUSEHOLD,
    DOMAIN,
    PIPELINE_NAME,
    STORE_KEY,
    TRANSCRIPT,
)
from custom_components.wake_word_recorder.pipeline import Restorer
from custom_components.wake_word_recorder.session import RESUME_SECONDS

from .conftest import (
    PIPELINE_SELECT,
    PIPELINE_SELECT_2,
    SATELLITE,
    SATELLITE_2,
    Satellite,
    pcm_silence,
    pcm_word,
)

STT = "stt.wake_word_recorder"
AGENT = "conversation.wake_word_recorder"
TTS = "tts.wake_word_recorder"
STATUS = "sensor.wake_word_recorder_status"
METADATA = stt.SpeechMetadata(
    language="pl",
    format=stt.AudioFormats.WAV,
    codec=stt.AudioCodecs.PCM,
    bit_rate=stt.AudioBitRates.BITRATE_16,
    sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
    channel=stt.AudioChannels.CHANNEL_MONO,
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _stream(audio: bytes) -> AsyncIterable[bytes]:
    for i in range(0, len(audio), 1024):
        yield audio[i : i + 1024]


async def _turn(hass: HomeAssistant, audio: bytes) -> conversation.ConversationResult:
    """One satellite turn through the recording pipeline: listening, STT, agent."""
    hass.states.async_set(SATELLITE, "listening")
    await hass.async_block_till_done()
    engine = stt.async_get_speech_to_text_engine(hass, STT)
    result = await engine.internal_async_process_audio_stream(METADATA, _stream(audio))
    assert result.text == TRANSCRIPT and result.result == stt.SpeechResultState.SUCCESS
    answer = await conversation.async_converse(hass, result.text, None, Context(), language="pl", agent_id=AGENT)
    hass.states.async_set(SATELLITE, "responding")
    await hass.async_block_till_done()
    return answer


def _speech(answer: conversation.ConversationResult) -> str:
    return answer.response.speech["plain"]["speech"]


async def test_config_flow(hass: HomeAssistant, household_dir, tmp_path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "household.yaml").write_text("speakers: [")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOUSEHOLD: str(broken)})
    assert result["errors"] == {"base": "household"} and "household.yaml" in result["description_placeholders"]["error"]
    with patch("custom_components.wake_word_recorder.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOUSEHOLD: f"{household_dir}/"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOUSEHOLD: str(household_dir)}
    assert result["title"] == "Wake Word Recorder (wakeword)"
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT


async def test_missing_household_retries(hass: HomeAssistant, tmp_path) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={CONF_HOUSEHOLD: str(tmp_path)})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state.name == "SETUP_RETRY"


async def test_entities(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    await _setup(hass, entry)
    for entity_id in (
        STT,
        AGENT,
        STATUS,
        "select.wake_word_recorder_speaker",
        "select.wake_word_recorder_word",
        "select.wake_word_recorder_satellite",
        "button.wake_word_recorder_start",
        "button.wake_word_recorder_stop",
    ):
        assert hass.states.get(entity_id) is not None, entity_id
    assert hass.states.get(STATUS).state == "idle"
    satellites = hass.states.get("select.wake_word_recorder_satellite").attributes["options"]
    assert satellites == ["Kid room", "Kitchen"]
    assert hass.states.get("select.wake_word_recorder_speaker").attributes["options"] == ["dad", "kid"]


async def test_agent_without_session(hass: HomeAssistant, entry) -> None:
    await _setup(hass, entry)
    answer = await conversation.async_converse(hass, "x", None, Context(), language="pl", agent_id=AGENT)
    assert _speech(answer) == "Nagrywanie słów nie jest teraz włączone."
    assert not answer.continue_conversation


async def test_session_with_selects_and_buttons(
    hass: HomeAssistant, entry, satellite: Satellite, household_dir
) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.wake_word_recorder_satellite", "option": "Kid room"},
        blocking=True,
    )
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.wake_word_recorder_speaker", "option": "kid"}, blocking=True
    )
    word = hass.states.get("select.wake_word_recorder_word")
    assert word.attributes["options"] == ["Dżarwis", "Robo"] and word.state == "Dżarwis"
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.wake_word_recorder_word", "option": "Robo"}, blocking=True
    )
    await hass.services.async_call("button", "press", {"entity_id": "button.wake_word_recorder_start"}, blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get(PIPELINE_SELECT).state == PIPELINE_NAME
    first = satellite.conversations[-1]
    assert first["entity_id"] == SATELLITE and first["preannounce"] is False
    assert first["start_message"] == (
        "Nagrywam słowo Robo, osoba: kid. Nagranie 1 z 6. Stań blisko urządzenia. Powiedz: Robo."
    )
    status = hass.states.get(STATUS)
    assert status.state == "recording" and status.attributes["target"] == 6

    session = entry.runtime_data.session
    assert first["start_message"] in session.listen_prompts
    answer = await _turn(hass, pcm_word())
    assert _speech(answer) == "Zapisano. Nagranie 2 z 6. Odejdź na drugi koniec pokoju. Powiedz: Robo."
    assert answer.continue_conversation and _speech(answer) in session.listen_prompts
    assert (household_dir / "data/real/robo/kid/kid_000_near.wav").exists()
    assert hass.states.get(STATUS).attributes["last_file"] == "kid_000_near.wav"

    answer = await _turn(hass, pcm_silence())
    assert (
        _speech(answer)
        == "Nic nie usłyszałem. Jeszcze raz. Nagranie 2 z 6. Odejdź na drugi koniec pokoju. Powiedz: Robo."
    )
    assert answer.continue_conversation and _speech(answer) in session.listen_prompts
    assert hass.states.get(STATUS).attributes["last_issues"] == ["no_speech"]

    await hass.services.async_call("button", "press", {"entity_id": "button.wake_word_recorder_stop"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "idle"
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"
    assert satellite.announcements[-1]["message"] == "Kończę nagrywanie. Zapisane: 1 z 6."
    assert hass.states.get(STATUS).attributes["last_session"] == {
        "speaker": "kid",
        "word": "robo",
        "done": 1,
        "target": 6,
    }


async def test_session_ends_at_count(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    await _setup(hass, entry)
    response = await hass.services.async_call(
        DOMAIN,
        "start_session",
        {"satellite": SATELLITE, "speaker": "dad", "word": "dzarwis", "count": 2},
        blocking=True,
        return_response=True,
    )
    assert response == {"done": 0, "target": 2}
    session = entry.runtime_data.session
    await _turn(hass, pcm_word())
    answer = await _turn(hass, pcm_word(freq=250))
    assert _speech(answer) == "Gotowe. Nagrań słowa Dżarwis: 2. Dziękuję!"
    assert not answer.continue_conversation
    assert _speech(answer) not in session.listen_prompts  # the end gets no signal
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "idle"
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"
    # the next session continues the numbering and the variants
    await hass.services.async_call(
        DOMAIN, "start_session", {"satellite": SATELLITE, "speaker": "dad", "word": "dzarwis"}, blocking=True
    )
    assert "Nagranie 3 z 4. Cicho." in satellite.conversations[-1]["start_message"]


async def test_too_many_failed_takes_stop(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN, "start_session", {"satellite": SATELLITE, "speaker": "kid", "word": "robo"}, blocking=True
    )
    for _ in range(5):
        assert (await _turn(hass, pcm_word(level=0.002))).continue_conversation
    answer = await _turn(hass, pcm_word(level=0.002))
    assert not answer.continue_conversation and _speech(answer).startswith("Kończę nagrywanie")
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "idle"


async def test_silence_watchdog(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN, "start_session", {"satellite": SATELLITE, "speaker": "kid", "word": "robo"}, blocking=True
    )
    asked = len(satellite.conversations)
    for miss in range(1, 3):
        hass.states.async_set(SATELLITE, "listening")
        hass.states.async_set(SATELLITE, "idle")
        await hass.async_block_till_done()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RESUME_SECONDS * miss + 1))
        await hass.async_block_till_done()
        assert len(satellite.conversations) == asked + miss
        assert satellite.conversations[-1]["start_message"].startswith("Nic nie usłyszałem.")
    hass.states.async_set(SATELLITE, "listening")
    hass.states.async_set(SATELLITE, "idle")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RESUME_SECONDS * 4))
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "idle"
    assert satellite.announcements[-1]["message"].startswith("Kończę nagrywanie")
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"


async def test_watchdog_resumes_after_reply(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    """The agent answered but the satellite did not listen again - the instruction is repeated."""
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN, "start_session", {"satellite": SATELLITE, "speaker": "kid", "word": "robo"}, blocking=True
    )
    await _turn(hass, pcm_word())
    hass.states.async_set(SATELLITE, "idle")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RESUME_SECONDS + 1))
    await hass.async_block_till_done()
    assert (
        satellite.conversations[-1]["start_message"] == "Nagranie 2 z 6. Odejdź na drugi koniec pokoju. Powiedz: Robo."
    )
    assert hass.states.get(STATUS).state == "recording"


async def test_listening_again_cancels_the_watchdog(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN, "start_session", {"satellite": SATELLITE, "speaker": "kid", "word": "robo"}, blocking=True
    )
    asked = len(satellite.conversations)
    hass.states.async_set(SATELLITE, "listening")
    hass.states.async_set(SATELLITE, "idle")
    hass.states.async_set(SATELLITE, "listening")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RESUME_SECONDS + 1))
    await hass.async_block_till_done()
    assert len(satellite.conversations) == asked


@pytest.mark.parametrize(
    "data, message",
    [
        ({"satellite": SATELLITE, "speaker": "dad", "word": "robo"}, "does not use"),
        ({"satellite": SATELLITE, "speaker": "nobody", "word": "robo"}, "Unknown person"),
        ({"satellite": SATELLITE, "speaker": "kid", "word": "nope"}, "Unknown word"),
        ({"speaker": "kid", "word": "robo"}, "Choose"),
        ({"satellite": "assist_satellite.ghost", "speaker": "kid", "word": "robo"}, "not a voice satellite"),
    ],
)
async def test_start_errors(hass: HomeAssistant, entry, satellite: Satellite, data, message) -> None:
    await _setup(hass, entry)
    with pytest.raises(HomeAssistantError, match=message):
        await hass.services.async_call(DOMAIN, "start_session", data, blocking=True)
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"


async def test_new_session_replaces_the_old(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    await _setup(hass, entry)
    for word in ("robo", "dzarwis"):
        await hass.services.async_call(
            DOMAIN, "start_session", {"satellite": SATELLITE, "speaker": "kid", "word": word}, blocking=True
        )
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).attributes["word"] == "dzarwis"
    assert hass.states.get(PIPELINE_SELECT).state == PIPELINE_NAME
    response = await hass.services.async_call(DOMAIN, "stop_session", {}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    assert response["word"] == "dzarwis"
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"


async def test_reload_service(hass: HomeAssistant, entry, household_dir) -> None:
    await _setup(hass, entry)
    text = (household_dir / "household.yaml").read_text(encoding="utf-8")
    (household_dir / "household.yaml").write_text(
        text.replace("- group: child\n  id: kid", "- group: child\n  id: kid\n- id: mum"), encoding="utf-8"
    )
    response = await hass.services.async_call(DOMAIN, "reload", {}, blocking=True, return_response=True)
    assert "mum" in response["speakers"]


async def test_restore_after_restart(hass: HomeAssistant, satellite: Satellite) -> None:
    restorer = Restorer(hass)
    assert await restorer.async_switch(PIPELINE_SELECT) == "Home Assistant"
    assert hass.states.get(PIPELINE_SELECT).state == PIPELINE_NAME
    await Restorer(hass).async_restore()  # a new instance, as after a restart
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"
    satellite.selects.clear()
    await Restorer(hass).async_restore()  # nothing pending any more
    assert satellite.selects == []


async def test_switching_to_another_satellite_restores_both(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    """7.11.2026: a session on one satellite, 15 s later a session on the kitchen one. The first
    session's restore ran late and dropped the kitchen's entry, so the kitchen stayed on the
    recording pipeline."""
    await _setup(hass, entry)
    satellite.slow.add(PIPELINE_SELECT)  # the first one was an unavailable Onju
    for target in (SATELLITE, SATELLITE_2):
        await hass.services.async_call(
            DOMAIN, "start_session", {"satellite": target, "speaker": "kid", "word": "robo"}, blocking=True
        )
    await hass.async_block_till_done()
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"
    assert hass.states.get(PIPELINE_SELECT_2).state == PIPELINE_NAME
    await hass.services.async_call(DOMAIN, "stop_session", {}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(PIPELINE_SELECT_2).state == "Kitchen"
    satellite.selects.clear()
    await Restorer(hass).async_restore()  # nothing left pending, as after a restart
    assert satellite.selects == []
    assert hass.states.get(PIPELINE_SELECT_2).state == "Kitchen"


async def test_new_session_on_the_same_satellite_keeps_recording(
    hass: HomeAssistant, entry, satellite: Satellite
) -> None:
    await _setup(hass, entry)
    for word in ("robo", "dzarwis"):
        await hass.services.async_call(
            DOMAIN, "start_session", {"satellite": SATELLITE_2, "speaker": "kid", "word": word}, blocking=True
        )
    await hass.async_block_till_done()  # the first session's late restore must not undo the switch
    assert hass.states.get(PIPELINE_SELECT_2).state == PIPELINE_NAME
    await hass.services.async_call(DOMAIN, "stop_session", {}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(PIPELINE_SELECT_2).state == "Kitchen"


async def test_restorer_keeps_every_switched_satellite(hass: HomeAssistant, satellite: Satellite) -> None:
    restorer = Restorer(hass)
    await restorer.async_switch(PIPELINE_SELECT)
    await restorer.async_switch(PIPELINE_SELECT_2)
    await restorer.async_restore(PIPELINE_SELECT)
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"
    assert hass.states.get(PIPELINE_SELECT_2).state == PIPELINE_NAME
    await Restorer(hass).async_restore()  # after a restart: the rest is still remembered
    assert hass.states.get(PIPELINE_SELECT_2).state == "Kitchen"


async def test_restore_and_switch_wait_for_each_other(hass: HomeAssistant, satellite: Satellite) -> None:
    restorer = Restorer(hass)
    await restorer.async_switch(PIPELINE_SELECT)
    await asyncio.gather(restorer.async_restore(PIPELINE_SELECT), restorer.async_switch(PIPELINE_SELECT_2))
    await Restorer(hass).async_restore()
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"
    assert hass.states.get(PIPELINE_SELECT_2).state == "Kitchen"


async def test_restore_reads_the_old_single_entry(hass: HomeAssistant, satellite: Satellite, hass_storage) -> None:
    hass_storage[STORE_KEY] = {
        "version": 1,
        "key": STORE_KEY,
        "data": {"select": PIPELINE_SELECT_2, "option": "Kitchen"},
    }
    hass.states.async_set(PIPELINE_SELECT_2, PIPELINE_NAME, {"options": ["Kitchen", PIPELINE_NAME]})
    await Restorer(hass).async_restore()
    assert hass.states.get(PIPELINE_SELECT_2).state == "Kitchen"
    assert hass_storage[STORE_KEY]["data"] == {"pending": {}}


async def test_unload_stops_the_session(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN, "start_session", {"satellite": SATELLITE, "speaker": "kid", "word": "robo"}, blocking=True
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"


def _pipeline(name, stt_engine=None, conversation_engine=None, tts_engine="tts.voice"):
    return MagicMock(
        name=name,
        stt_engine=stt_engine,
        conversation_engine=conversation_engine,
        tts_engine=tts_engine,
        tts_language="pl",
        tts_voice="gosia",
    )


async def test_ensure_pipeline_creates_it(hass: HomeAssistant) -> None:
    created = _pipeline(PIPELINE_NAME)
    preferred = _pipeline("Home Assistant")
    preferred.name = "Home Assistant"
    with (
        patch.object(pipeline_module, "async_get_pipelines", return_value=[preferred]),
        patch.object(pipeline_module, "async_get_pipeline", return_value=preferred),
        patch.object(pipeline_module, "async_create_default_pipeline", AsyncMock(return_value=created)) as create,
        patch.object(pipeline_module, "async_update_pipeline", AsyncMock()) as update,
    ):
        await pipeline_module.async_ensure_pipeline(hass)
    create.assert_awaited_once_with(hass, STT, "tts.voice", PIPELINE_NAME)
    update.assert_awaited_once_with(
        hass, created, conversation_engine=AGENT, tts_engine=TTS, tts_language="pl", tts_voice="gosia"
    )


async def test_ensure_pipeline_repoints_existing(hass: HomeAssistant) -> None:
    existing = _pipeline(PIPELINE_NAME, stt_engine="stt.other", conversation_engine=AGENT)
    existing.name = PIPELINE_NAME
    with (
        patch.object(pipeline_module, "async_get_pipelines", return_value=[existing]),
        patch.object(pipeline_module, "async_get_pipeline", return_value=existing),
        patch.object(pipeline_module, "async_update_pipeline", AsyncMock()) as update,
    ):
        await pipeline_module.async_ensure_pipeline(hass)
        update.assert_awaited_once_with(hass, existing, stt_engine=STT, conversation_engine=AGENT, tts_engine=TTS)
        existing.stt_engine, existing.tts_engine = STT, TTS
        update.reset_mock()
        await pipeline_module.async_ensure_pipeline(hass)
        update.assert_not_awaited()


@pytest.mark.parametrize(
    "tts, created, message", [(None, None, "no text-to-speech"), ("tts.voice", None, "Could not create")]
)
async def test_ensure_pipeline_errors(hass: HomeAssistant, tts, created, message) -> None:
    preferred = _pipeline("Home Assistant", tts_engine=tts)
    preferred.name = "Home Assistant"
    with (
        patch.object(pipeline_module, "async_get_pipelines", return_value=[preferred]),
        patch.object(pipeline_module, "async_get_pipeline", return_value=preferred),
        patch.object(pipeline_module, "async_create_default_pipeline", AsyncMock(return_value=created)),
        pytest.raises(HomeAssistantError, match=message),
    ):
        await pipeline_module.async_ensure_pipeline(hass)


async def test_config_flow_creates_household(hass: HomeAssistant, tmp_path) -> None:
    from custom_components.wake_word_recorder.household import load

    for person, name in (("person.tomek", "Tomek"), ("person.ola", "Ola"), ("person.ewa_k", "Ewa")):
        hass.states.async_set(person, "home", {"friendly_name": name})
    folder = tmp_path / "wakeword"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOUSEHOLD: str(folder)})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "create"
    assert result["description_placeholders"] == {"path": str(folder)}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"people": [], "children": []})
    assert result["errors"] == {"base": "no_people"}
    with patch("custom_components.wake_word_recorder.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"people": ["person.tomek", "person.ola", "person.ewa_k"], "children": ["person.ola"]},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"] == {CONF_HOUSEHOLD: str(folder)}
    household = load(folder)
    assert {s.id: s.group for s in household.speakers.values()} == {"tomek": "adult", "ola": "child", "ewa_k": "adult"}
    assert household.words == {}
    text = (folder / "household.yaml").read_text()
    assert "ui_language: pl" in text and "wake_word: Jarvis" in text
    assert (folder / ".gitignore").read_text().endswith("data/\n")


async def test_config_flow_duplicate_people(hass: HomeAssistant, tmp_path) -> None:
    hass.states.async_set("person.ola", "home")
    hass.states.async_set("person.o_la", "home")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOUSEHOLD: str(tmp_path / "x")})
    with patch("custom_components.wake_word_recorder.config_flow.speaker_id", return_value="ola"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"people": ["person.ola", "person.o_la"]}
        )
    assert result["errors"] == {"base": "duplicate_people"}


async def test_household_changes_are_noticed(hass: HomeAssistant, entry, household_dir) -> None:
    import os

    import yaml

    from custom_components.wake_word_recorder import WATCH_INTERVAL

    await _setup(hass, entry)
    word = "select.wake_word_recorder_word"
    assert hass.states.get(word).state == "Dżarwis"  # the first word is chosen by default
    file = household_dir / "household.yaml"
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    data["words"]["wajana"] = {"wake_word": "Wajana", "spellings": {"pl": ["wajana"]}}
    file.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    os.utime(file, (1, 2_000_000_000))
    async_fire_time_changed(hass, dt_util.utcnow() + WATCH_INTERVAL + timedelta(seconds=1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "Wajana" in hass.states.get(word).attributes["options"]
    # a broken file is reported once, the old household stays
    file.write_text("speakers: [", encoding="utf-8")
    os.utime(file, (1, 2_000_000_100))
    async_fire_time_changed(hass, dt_util.utcnow() + WATCH_INTERVAL * 2 + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "Wajana" in hass.states.get(word).attributes["options"]


async def test_choosing_a_person_picks_their_word(hass: HomeAssistant, entry) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.wake_word_recorder_word", "option": "Dżarwis"}, blocking=True
    )
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.wake_word_recorder_speaker", "option": "dad"}, blocking=True
    )
    assert hass.states.get("select.wake_word_recorder_word").state == "Dżarwis"
    assert hass.states.get("select.wake_word_recorder_word").attributes["options"] == ["Dżarwis"]


async def test_satellites_loaded_later_appear(hass: HomeAssistant, entry) -> None:
    await _setup(hass, entry)
    select = "select.wake_word_recorder_satellite"
    assert hass.states.get(select).attributes["options"] == []
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen"})
    await hass.async_block_till_done()
    assert hass.states.get(select).attributes["options"] == ["Kitchen"]
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "Kitchen"}, blocking=True)
    assert hass.states.get(select).state == "Kitchen"
    hass.states.async_remove("assist_satellite.kitchen")
    await hass.async_block_till_done()
    assert hass.states.get(select).attributes["options"] == []


async def test_another_round_after_the_target(hass: HomeAssistant, entry, satellite: Satellite, household_dir) -> None:
    from .conftest import pcm_word as word

    await _setup(hass, entry)
    for _ in range(6):  # the kid's target for robo
        from custom_components.wake_word_recorder import household as hh
        from custom_components.wake_word_recorder import take

        take.save_take(hh.load(household_dir), "robo", "kid", word(), SATELLITE)
    response = await hass.services.async_call(
        DOMAIN,
        "start_session",
        {"satellite": SATELLITE, "speaker": "kid", "word": "robo"},
        blocking=True,
        return_response=True,
    )
    assert response == {"done": 6, "target": 18}  # one more round of the 12 delivery variants
    assert "Nagranie 7 z 18. Pytająco." in satellite.conversations[-1]["start_message"]


async def test_a_satellite_with_two_wake_words_records_on_both_pipelines(
    hass: HomeAssistant, entry, satellite: Satellite
) -> None:
    """A Voice PE has a pipeline per wake word, and the recorder's conversation may run on either:
    a session switches both and gives each its own back."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from .conftest import PIPELINE_SELECT_WORD_2

    device = dr.async_get(hass).async_get_device(identifiers={("esphome", "aa:bb")})
    er.async_get(hass).async_get_or_create(
        "select", "esphome", "aa:bb-pipeline_2", device_id=device.id, suggested_object_id="kid_room_assistant_2"
    )
    options = hass.states.get(PIPELINE_SELECT).attributes["options"]
    hass.states.async_set(PIPELINE_SELECT_WORD_2, "Kitchen", {"options": options})
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "start_session",
        {"satellite": SATELLITE, "speaker": "kid", "word": "robo"},
        blocking=True,
        return_response=True,
    )
    assert hass.states.get(PIPELINE_SELECT).state == PIPELINE_NAME
    assert hass.states.get(PIPELINE_SELECT_WORD_2).state == PIPELINE_NAME
    await hass.services.async_call(DOMAIN, "stop_session", {}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"
    assert hass.states.get(PIPELINE_SELECT_WORD_2).state == "Kitchen"


async def test_a_take_the_device_spoke_into_is_not_kept(
    hass: HomeAssistant, entry, satellite: Satellite, household_dir
) -> None:
    """A slow text-to-speech: the satellite listens before its instruction plays, and the
    instruction lands in the take. Such a take is asked again, not stored."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    device = dr.async_get(hass).async_get_device(identifiers={("esphome", "aa:bb")})
    er.async_get(hass).async_get_or_create(
        "media_player", "esphome", "aa:bb-media_player", device_id=device.id, suggested_object_id="kid_room_speaker"
    )
    hass.states.async_set("media_player.kid_room_speaker", "idle")
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "start_session",
        {"satellite": SATELLITE, "speaker": "kid", "word": "robo"},
        blocking=True,
        return_response=True,
    )
    folder = household_dir / "data" / "real" / "robo" / "kid"
    before = len(list(folder.glob("*.wav"))) if folder.exists() else 0

    # The satellite listens, then its late instruction plays into the take
    hass.states.async_set(SATELLITE, "listening")
    await hass.async_block_till_done()
    hass.states.async_set("media_player.kid_room_speaker", "playing")
    await hass.async_block_till_done()
    engine = stt.async_get_speech_to_text_engine(hass, STT)
    await engine.internal_async_process_audio_stream(METADATA, _stream(pcm_word()))
    answer = await conversation.async_converse(hass, TRANSCRIPT, None, Context(), language="pl", agent_id=AGENT)
    assert _speech(answer).startswith("Mówiłem jeszcze w trakcie nagrania. Jeszcze raz.")
    assert hass.states.get(STATUS).attributes["last_issues"] == ["overlap"]
    assert (len(list(folder.glob("*.wav"))) if folder.exists() else 0) == before

    # The next turn, with the speaker quiet, is stored
    hass.states.async_set(SATELLITE, "responding")
    hass.states.async_set("media_player.kid_room_speaker", "idle")
    await _turn(hass, pcm_word())
    assert len(list(folder.glob("*.wav"))) == before + 1
