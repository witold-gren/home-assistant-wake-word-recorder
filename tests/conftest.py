"""Fixtures: a household folder, a fake voice satellite with its pipeline select."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import math
from pathlib import Path
import struct
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import yaml

from custom_components.wake_word_recorder.const import CONF_HOUSEHOLD, DOMAIN

HOUSEHOLD = {
    "ui_language": "pl",
    "speakers": [{"id": "dad", "group": "adult"}, {"id": "kid", "group": "child"}],
    "words": {
        "dzarwis": {"wake_word": "Dżarwis", "spellings": {"pl": ["dżarwis"]}},
        "robo": {"wake_word": "Robo", "speakers": ["kid"], "spellings": {"pl": ["robo"]}},
    },
    "targets": {"adult": 4, "child": 6, "held_out": 1},
}
SATELLITE = "assist_satellite.kid_room"
PIPELINE_SELECT = "select.kid_room_assistant"
# a second satellite (the kitchen), to switch between satellites
SATELLITE_2 = "assist_satellite.kitchen"
PIPELINE_SELECT_2 = "select.kitchen_assistant"


def pcm_word(seconds: float = 0.6, pad: float = 0.5, level: float = 0.3, freq: float = 300.0) -> bytes:
    """Silence, a harmonic 'word' with a smooth envelope, silence - 16 kHz 16-bit mono."""
    rate = 16000
    n = int(seconds * rate)
    samples = [0] * int(pad * rate)
    for i in range(n):
        t = i / rate
        env = 0.5 - 0.5 * math.cos(2 * math.pi * i / n)
        value = sum(math.sin(2 * math.pi * freq * k * t) / k for k in (1, 2, 3)) / 1.83
        samples.append(max(-32768, min(32767, int(level * env * value * 32767))))
    samples += [0] * int(pad * rate)
    return struct.pack(f"<{len(samples)}h", *samples)


def pcm_silence(seconds: float = 1.0) -> bytes:
    return b"\x00\x00" * int(seconds * 16000)


@pytest.fixture(autouse=True)
async def auto_enable_custom_integrations(hass: HomeAssistant, enable_custom_integrations):
    assert await async_setup_component(hass, "homeassistant", {})
    hass.config.language = "pl"
    yield


@pytest.fixture
def household_dir(tmp_path: Path) -> Path:
    root = tmp_path / "wakeword"
    root.mkdir()
    (root / "household.yaml").write_text(yaml.safe_dump(HOUSEHOLD, allow_unicode=True), encoding="utf-8")
    return root


@pytest.fixture
def entry(household_dir: Path) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, title="Wake Word Recorder", unique_id=DOMAIN, data={CONF_HOUSEHOLD: str(household_dir)}
    )


@dataclass
class Satellite:
    """Records the service calls the recorder makes."""

    conversations: list[dict] = field(default_factory=list)
    announcements: list[dict] = field(default_factory=list)
    selects: list[str] = field(default_factory=list)
    slow: set[str] = field(default_factory=set)  # selects that answer late (an unavailable device)


@pytest.fixture
def satellite(hass: HomeAssistant) -> Satellite:
    fake = Satellite()
    owner = MockConfigEntry(domain="esphome")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("esphome", "aa:bb")}, name="Kid room"
    )
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "assist_satellite", "esphome", "aa:bb-assist_satellite", device_id=device.id, suggested_object_id="kid_room"
    )
    registry.async_get_or_create(
        "select", "esphome", "aa:bb-pipeline", device_id=device.id, suggested_object_id="kid_room_assistant"
    )
    kitchen = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("esphome", "cc:dd")}, name="Kitchen"
    )
    registry.async_get_or_create(
        "assist_satellite", "esphome", "cc:dd-assist_satellite", device_id=kitchen.id, suggested_object_id="kitchen"
    )
    registry.async_get_or_create(
        "select", "esphome", "cc:dd-pipeline", device_id=kitchen.id, suggested_object_id="kitchen_assistant"
    )
    hass.states.async_set(SATELLITE, "idle", {"friendly_name": "Kid room"})
    hass.states.async_set(SATELLITE_2, "idle", {"friendly_name": "Kitchen"})
    options = ["preferred", "Home Assistant", "Kitchen", "Wake word recording"]
    hass.states.async_set(PIPELINE_SELECT, "Home Assistant", {"options": options})
    hass.states.async_set(PIPELINE_SELECT_2, "Kitchen", {"options": options})

    async def select_option(_hass: HomeAssistant, select: str, option: str) -> None:
        assert select in (PIPELINE_SELECT, PIPELINE_SELECT_2)
        if select in fake.slow:
            await asyncio.sleep(0.05)
        if select == PIPELINE_SELECT:
            fake.selects.append(option)
        hass.states.async_set(select, option, {"options": options})

    async def start_conversation(call: ServiceCall) -> None:
        fake.conversations.append(dict(call.data))

    async def announce(call: ServiceCall) -> None:
        fake.announcements.append(dict(call.data))

    hass.services.async_register("assist_satellite", "start_conversation", start_conversation)
    hass.services.async_register("assist_satellite", "announce", announce)
    # the real select service only reaches real entities; the satellite's select is a fake state
    with patch("custom_components.wake_word_recorder.pipeline.async_select", select_option):
        yield fake


@pytest.fixture(autouse=True)
def no_pipeline_store():
    """The real pipeline store needs STT/TTS providers; its use is tested separately."""
    with patch("custom_components.wake_word_recorder.async_ensure_pipeline", AsyncMock()) as ensure:
        yield ensure
