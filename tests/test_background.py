"""Background recording through a satellite."""

from __future__ import annotations

from datetime import timedelta
import wave

from homeassistant.core import HomeAssistant
import homeassistant.util.dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.wake_word_recorder.background import MAX_SILENT_RESTARTS, RESUME_SECONDS
from custom_components.wake_word_recorder.const import DOMAIN, PIPELINE_NAME, SILENCE_URL

from .conftest import PIPELINE_SELECT, SATELLITE, Satellite, pcm_silence, pcm_word
from .test_integration import STATUS, _setup, _speech, _turn


async def test_background_session(hass: HomeAssistant, entry, satellite: Satellite, household_dir) -> None:
    await _setup(hass, entry)
    response = await hass.services.async_call(
        DOMAIN, "start_background", {"satellite": SATELLITE, "minutes": 1}, blocking=True, return_response=True
    )
    assert response["file"].startswith("data/background/kid_room_") and response["file"].endswith(".wav")
    assert hass.states.get(PIPELINE_SELECT).state == PIPELINE_NAME
    assert satellite.conversations[-1]["start_message"].startswith("Nagrywam dźwięki domu przez 1 minut.")
    status = hass.states.get(STATUS)
    assert status.state == "listening" and status.attributes["mode"] == "background"

    for audio in (pcm_word(seconds=2), pcm_silence(3)):
        answer = await _turn(hass, audio)
        assert _speech(answer) == "" and answer.continue_conversation  # silent, keeps listening
    assert hass.states.get(STATUS).attributes["recorded_minutes"] == round((3.0 + 3) / 60, 1)

    # the satellite stopped listening: it is restarted with a silence, not with speech
    hass.states.async_set(SATELLITE, "idle")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RESUME_SECONDS + 0.5))
    await hass.async_block_till_done()
    assert satellite.conversations[-1] == {"entity_id": SATELLITE, "start_media_id": SILENCE_URL, "preannounce": False}

    # time is up
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1, seconds=5))
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "idle"
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"
    assert satellite.announcements[-1]["message"] == "Skończyłem nagrywać dźwięki domu. Nagrano 0 minut."
    file = household_dir / response["file"]
    with wave.open(str(file)) as wav:
        assert (wav.getframerate(), wav.getnchannels()) == (16000, 1)
        assert wav.getnframes() == 16000 * (3 + 3)  # word with padding (3 s) + silence (3 s)
    assert hass.states.get(STATUS).attributes["last_session"]["background"] == response["file"]

    # a turn still running when the session ended gets no spoken answer
    answer = await _turn(hass, pcm_silence(1))
    assert _speech(answer) == "" and not answer.continue_conversation


async def test_background_with_button_and_stop(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.wake_word_recorder_satellite", "option": "Kid room"},
        blocking=True,
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.wake_word_recorder_background"}, blocking=True
    )
    assert "30 minut" in satellite.conversations[-1]["start_message"]
    await _turn(hass, pcm_silence(2))
    response = await hass.services.async_call(DOMAIN, "stop_session", {}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    assert response["minutes"] == 0.0
    assert satellite.announcements[-1]["message"].startswith("Kończę nagrywanie dźwięków domu.")
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"


async def test_background_ends_when_satellite_is_gone(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(DOMAIN, "start_background", {"satellite": SATELLITE}, blocking=True)
    now = dt_util.utcnow()
    for _ in range(MAX_SILENT_RESTARTS):
        hass.states.async_set(SATELLITE, "listening")
        hass.states.async_set(SATELLITE, "idle")
        await hass.async_block_till_done()
        now += timedelta(seconds=RESUME_SECONDS + 0.5)
        async_fire_time_changed(hass, now)
        await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "idle"
    assert hass.states.get(PIPELINE_SELECT).state == "Home Assistant"


async def test_background_needs_a_satellite(hass: HomeAssistant, entry) -> None:
    from homeassistant.exceptions import HomeAssistantError
    import pytest

    await _setup(hass, entry)
    with pytest.raises(HomeAssistantError, match="satellite"):
        await hass.services.async_call(DOMAIN, "start_background", {}, blocking=True)


async def test_silence_is_served(hass: HomeAssistant, entry, hass_client) -> None:
    await _setup(hass, entry)
    client = await hass_client()
    response = await client.get(SILENCE_URL)
    assert response.status == 200
    assert (await response.read())[:4] == b"RIFF"


async def test_one_satellite_for_takes_and_the_home(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    """Takes and listening never run at once: the one satellite select serves both."""
    await _setup(hass, entry)
    assert hass.states.get("select.wake_word_recorder_background_satellite") is None
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen"})
    await hass.async_block_till_done()
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.wake_word_recorder_satellite", "option": "Kid room"},
        blocking=True,
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.wake_word_recorder_background"}, blocking=True
    )
    assert satellite.conversations[-1]["entity_id"] == SATELLITE
    status = hass.states.get(STATUS)
    assert status.state == "listening"
    assert status.attributes["satellite"] == SATELLITE


async def test_the_old_background_select_is_removed(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    from homeassistant.helpers import entity_registry as er

    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "select",
        DOMAIN,
        f"{entry.entry_id}-background_satellite",
        config_entry=entry,
        suggested_object_id="wake_word_recorder_background_satellite",
    )
    await _setup(hass, entry)
    assert registry.async_get_entity_id("select", DOMAIN, f"{entry.entry_id}-background_satellite") is None


async def test_only_satellites_that_are_up_are_offered(hass: HomeAssistant, entry, satellite: Satellite) -> None:
    from homeassistant.exceptions import HomeAssistantError
    import pytest

    await _setup(hass, entry)
    select = "select.wake_word_recorder_satellite"
    hass.states.async_set("assist_satellite.kitchen", "unavailable", {"friendly_name": "Kitchen"})
    await hass.async_block_till_done()
    assert hass.states.get(select).attributes["options"] == ["Kid room"]
    # It comes back: offered again
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen"})
    await hass.async_block_till_done()
    assert hass.states.get(select).attributes["options"] == ["Kid room", "Kitchen"]
    # The chosen one goes away: still shown, but nothing starts on it, and it says why
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "Kitchen"}, blocking=True)
    hass.states.async_set("assist_satellite.kitchen", "unavailable", {"friendly_name": "Kitchen"})
    await hass.async_block_till_done()
    assert hass.states.get(select).state == "Kitchen"
    with pytest.raises(HomeAssistantError, match="Kitchen is unavailable"):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.wake_word_recorder_background"}, blocking=True
        )
    with pytest.raises(HomeAssistantError, match="Kitchen is unavailable"):
        await hass.services.async_call(
            DOMAIN,
            "start_session",
            {"satellite": "assist_satellite.kitchen", "speaker": "kid", "word": "robo"},
            blocking=True,
            return_response=True,
        )
    assert hass.states.get(STATUS).state == "idle"
