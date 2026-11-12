"""The log of wakes: which word woke which satellite, and whether anyone spoke."""

from __future__ import annotations

import csv
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType, async_get_pipeline
from homeassistant.components.assist_pipeline.error import DuplicateWakeUpDetectedError
from homeassistant.components.assist_satellite import entity as satellite_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wake_word_recorder import wake_log

STT_END = PipelineEvent(PipelineEventType.STT_END, {"stt_output": {"text": "Włącz światło."}})
INTENT_DONE = PipelineEvent(
    PipelineEventType.INTENT_END,
    {"intent_output": {"response": {"response_type": "action_done", "data": {"targets": []}}}},
)
INTENT_NO_MATCH = PipelineEvent(
    PipelineEventType.INTENT_END,
    {"intent_output": {"response": {"response_type": "error", "data": {"code": "no_intent_match"}}}},
)
NO_TEXT = PipelineEvent(PipelineEventType.ERROR, {"code": "stt-no-text-recognized", "message": "No text"})


@pytest.fixture(autouse=True)
async def assist_pipeline(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "assist_pipeline", {})


def run_emitting(*events: PipelineEvent) -> AsyncMock:
    async def run(hass, *, event_callback, **kwargs):
        for event in events:
            event_callback(event)

    return AsyncMock(side_effect=run)


async def wake(hass: HomeAssistant, pipeline: AsyncMock, path: Path, **kwargs) -> list[dict]:
    """One pipeline run the way a satellite starts it; the rows of the log afterwards."""
    with patch.object(satellite_entity, "async_pipeline_from_audio_stream", pipeline):
        uninstall = wake_log.async_install(hass, path)
        seen = []
        try:
            await satellite_entity.async_pipeline_from_audio_stream(hass, event_callback=seen.append, **kwargs)
        finally:
            uninstall()
            await hass.async_block_till_done(wait_background_tasks=True)
    return read(path)


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file, delimiter=";"))


async def test_a_command_is_logged_with_its_word_room_and_text(hass: HomeAssistant, tmp_path: Path) -> None:
    config = MockConfigEntry(domain="esphome")
    config.add_to_hass(hass)
    area = ar.async_get(hass).async_create("Salon")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config.entry_id, identifiers={("esphome", "salon")}, name="Salon Voice"
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)

    rows = await wake(
        hass,
        run_emitting(STT_END, INTENT_DONE),
        tmp_path / "log.csv",
        wake_word_phrase="Hey Jarvis",
        device_id=device.id,
    )

    assert len(rows) == 1
    row = rows[0]
    assert (row["area"], row["device"], row["wake_word"]) == ("Salon", "Salon Voice", "Hey Jarvis")
    assert (row["outcome"], row["text"], row["response"]) == ("command", "Włącz światło.", "action_done")
    assert row["pipeline"] == async_get_pipeline(hass).name
    assert float(row["seconds"]) >= 0


async def test_a_wake_where_nobody_spoke_is_no_speech(hass: HomeAssistant, tmp_path: Path) -> None:
    rows = await wake(hass, run_emitting(NO_TEXT), tmp_path / "log.csv", wake_word_phrase="Okay Nabu")
    assert (rows[0]["wake_word"], rows[0]["outcome"], rows[0]["text"]) == ("Okay Nabu", "no_speech", "")


async def test_noise_heard_as_words_keeps_the_agents_verdict(hass: HomeAssistant, tmp_path: Path) -> None:
    rows = await wake(hass, run_emitting(STT_END, INTENT_NO_MATCH), tmp_path / "log.csv", wake_word_phrase="Okay Nabu")
    assert (rows[0]["outcome"], rows[0]["response"]) == ("command", "no_intent_match")


async def test_a_word_another_satellite_took_is_a_duplicate(hass: HomeAssistant, tmp_path: Path) -> None:
    event = PipelineEvent(PipelineEventType.ERROR, {"code": wake_log.ERROR_DUPLICATE, "message": "Duplicate"})
    rows = await wake(hass, run_emitting(event), tmp_path / "log.csv", wake_word_phrase="Okay Nabu")
    assert rows[0]["outcome"] == "duplicate"


async def test_a_raised_pipeline_error_is_logged_and_passed_on(hass: HomeAssistant, tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    with pytest.raises(DuplicateWakeUpDetectedError):
        await wake(
            hass, AsyncMock(side_effect=DuplicateWakeUpDetectedError("Okay Nabu")), path, wake_word_phrase="Okay Nabu"
        )
    assert read(path)[0]["outcome"] == "duplicate"


async def test_a_run_stopped_before_speech_to_text_is_cancelled(hass: HomeAssistant, tmp_path: Path) -> None:
    rows = await wake(hass, run_emitting(), tmp_path / "log.csv", wake_word_phrase="Okay Nabu")
    assert rows[0]["outcome"] == "cancelled"


async def test_a_run_without_a_wake_word_is_not_a_wake(hass: HomeAssistant, tmp_path: Path) -> None:
    pipeline = run_emitting(STT_END)
    assert await wake(hass, pipeline, tmp_path / "log.csv", wake_word_phrase=None) == []
    pipeline.assert_awaited_once()


async def test_the_satellite_still_gets_every_event(hass: HomeAssistant, tmp_path: Path) -> None:
    seen: list[PipelineEvent] = []
    with patch.object(satellite_entity, "async_pipeline_from_audio_stream", run_emitting(STT_END, NO_TEXT)):
        uninstall = wake_log.async_install(hass, tmp_path / "log.csv")
        await satellite_entity.async_pipeline_from_audio_stream(
            hass, event_callback=seen.append, wake_word_phrase="Okay Nabu"
        )
        uninstall()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert seen == [STT_END, NO_TEXT]


async def test_rows_are_added_under_one_header_excel_can_read(hass: HomeAssistant, tmp_path: Path) -> None:
    path = tmp_path / "assist" / "log.csv"
    await wake(hass, run_emitting(NO_TEXT), path, wake_word_phrase="Okay Nabu")
    rows = await wake(hass, run_emitting(STT_END), path, wake_word_phrase="Hey Jarvis")
    assert [row["wake_word"] for row in rows] == ["Okay Nabu", "Hey Jarvis"]
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbftimestamp;area;")
    assert raw.count(b"\xef\xbb\xbf") == 1


async def test_a_log_that_cannot_be_written_does_not_stop_the_satellite(hass: HomeAssistant, tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a folder")
    pipeline = run_emitting(STT_END)
    await wake(hass, pipeline, blocked / "log.csv", wake_word_phrase="Okay Nabu")
    pipeline.assert_awaited_once()


async def test_the_wrapped_function_takes_what_the_satellite_sends() -> None:
    """HA keeps the phrase as a keyword of this function: the log depends on it."""
    parameters = inspect.signature(satellite_entity.async_pipeline_from_audio_stream).parameters
    assert {"wake_word_phrase", "event_callback", "device_id", "pipeline_id"} <= set(parameters)


async def test_the_log_follows_the_config_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    original = satellite_entity.async_pipeline_from_audio_stream
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert satellite_entity.async_pipeline_from_audio_stream is not original

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert satellite_entity.async_pipeline_from_audio_stream is original
