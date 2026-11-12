"""Log every wake of a voice satellite to a CSV file.

A wake word is detected on the satellite, and no entity or event of Home Assistant shows which one it
was: the phrase only travels with the call that starts the pipeline. That call is wrapped here, which
also gives the events of the run, so a wake where nobody said anything can be told from a real command.
One row per wake, written when the run ends.
"""

from __future__ import annotations

from collections.abc import Callable
import csv
from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time
from typing import Any

from homeassistant.components.assist_pipeline import (
    PipelineEvent,
    PipelineEventType,
    PipelineNotFound,
    async_get_pipeline,
)
from homeassistant.components.assist_satellite import entity as satellite_entity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

LOG_FILE = ("assist", "wake_word_log.csv")
HEADER = ("timestamp", "area", "device", "wake_word", "pipeline", "outcome", "text", "response", "seconds")

OUTCOME_COMMAND = "command"  # speech-to-text heard something
OUTCOME_NO_SPEECH = "no_speech"  # the satellite woke and nobody said anything
OUTCOME_DUPLICATE = "duplicate"  # another satellite took the same word first
OUTCOME_CANCELLED = "cancelled"  # the run ended before speech-to-text did

ERROR_NO_TEXT = "stt-no-text-recognized"
ERROR_DUPLICATE = "duplicate_wake_up_detected"

_write_lock = threading.Lock()


@dataclass
class WakeRun:
    """What one pipeline run told about a wake."""

    text: str = ""
    response: str = ""
    error: str = ""

    @callback
    def on_event(self, event: PipelineEvent) -> None:
        data = event.data or {}
        if event.type == PipelineEventType.STT_END:
            self.text = data.get("stt_output", {}).get("text", "")
        elif event.type == PipelineEventType.INTENT_END:
            response = data.get("intent_output", {}).get("response", {})
            self.response = response.get("data", {}).get("code") or response.get("response_type", "")
        elif event.type == PipelineEventType.ERROR:
            self.error = data.get("code", "")

    @property
    def outcome(self) -> str:
        if self.error == ERROR_NO_TEXT:
            return OUTCOME_NO_SPEECH
        if self.error == ERROR_DUPLICATE:
            return OUTCOME_DUPLICATE
        if self.error:
            return self.error
        return OUTCOME_COMMAND if self.text else OUTCOME_CANCELLED


def append_row(path: Path, row: tuple) -> None:
    """Add a row; semicolons and a BOM, like the Assist Router log next to it, so Excel opens it."""
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8-sig" if new else "utf-8", newline="") as file:
            writer = csv.writer(file, delimiter=";")
            if new:
                writer.writerow(HEADER)
            writer.writerow(row)


def _write(path: Path, row: tuple) -> None:
    try:
        append_row(path, row)
    except OSError as err:
        _LOGGER.warning("Could not write the wake word log %s: %s", path, err)


def _device_names(hass: HomeAssistant, device_id: str | None) -> tuple[str, str]:
    device = dr.async_get(hass).async_get(device_id) if device_id else None
    if device is None:
        return "", ""
    area = ar.async_get(hass).async_get_area(device.area_id) if device.area_id else None
    return (area.name if area else "", device.name_by_user or device.name or "")


def _pipeline_name(hass: HomeAssistant, pipeline_id: str | None) -> str:
    try:
        return async_get_pipeline(hass, pipeline_id).name
    except PipelineNotFound:
        return ""


@callback
def async_install(hass: HomeAssistant, path: Path | None = None) -> Callable[[], None]:
    """Start logging wakes; the returned function stops it."""
    log_path = path or Path(hass.config.path(*LOG_FILE))
    original = satellite_entity.async_pipeline_from_audio_stream

    async def logged(*args: Any, **kwargs: Any) -> None:
        phrase = kwargs.get("wake_word_phrase")
        if phrase is None:  # a button, or the next turn of a conversation: nobody said a wake word
            return await original(*args, **kwargs)

        run = WakeRun()
        forward = kwargs["event_callback"]

        @callback
        def on_event(event: PipelineEvent) -> None:
            run.on_event(event)
            forward(event)

        kwargs["event_callback"] = on_event
        now = dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
        area, device = _device_names(hass, kwargs.get("device_id"))
        pipeline = _pipeline_name(hass, kwargs.get("pipeline_id"))
        started = time.monotonic()
        try:
            return await original(*args, **kwargs)
        except Exception as err:
            run.error = run.error or getattr(err, "code", type(err).__name__)
            raise
        finally:
            seconds = round(time.monotonic() - started, 1)
            row = (now, area, device, phrase, pipeline, run.outcome, run.text, run.response, seconds)
            hass.async_add_executor_job(_write, log_path, row)

    satellite_entity.async_pipeline_from_audio_stream = logged

    @callback
    def uninstall() -> None:
        if satellite_entity.async_pipeline_from_audio_stream is logged:
            satellite_entity.async_pipeline_from_audio_stream = original

    return uninstall
