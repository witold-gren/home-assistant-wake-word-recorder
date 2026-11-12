"""The API the training panel uses to fetch the recordings and household.yaml without a file share."""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant
import yaml

from custom_components.wake_word_recorder.api import list_files

from .conftest import HOUSEHOLD


def make_recordings(root):
    take = root / "data" / "real" / "dzarwis" / "dad" / "dad_000_near.wav"
    take.parent.mkdir(parents=True)
    take.write_bytes(b"RIFF-take")
    (root / "data" / "real" / "recordings.csv").write_text("path,satellite\n")
    (take.parent / ".dad_001_far.wav").write_bytes(b"half-written")
    (take.parent / "notes.txt").write_text("not a recording")
    background = root / "data" / "background" / "kid_room_20261028_2100.wav"
    background.parent.mkdir(parents=True)
    background.write_bytes(b"RIFF-background")
    (root / "data" / "build").mkdir()
    (root / "data" / "build" / "model.tflite").write_bytes(b"TFL3")
    return take, background


async def test_lists_and_hands_out_recordings(hass: HomeAssistant, entry, household_dir, hass_client) -> None:
    take, _ = make_recordings(household_dir)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client()

    response = await client.get("/api/wake_word_recorder/files")
    assert response.status == 200
    listed = await response.json()
    assert listed["household"] == household_dir.name
    assert sorted(f["path"] for f in listed["files"]) == [
        "data/background/kid_room_20261028_2100.wav",
        "data/real/dzarwis/dad/dad_000_near.wav",
        "data/real/recordings.csv",
    ]
    assert {f["size"] for f in listed["files"] if f["path"].endswith("near.wav")} == {len(b"RIFF-take")}

    response = await client.get(
        "/api/wake_word_recorder/file", params={"path": "data/real/dzarwis/dad/dad_000_near.wav"}
    )
    assert response.status == 200 and await response.read() == b"RIFF-take"
    for outside in (
        "household.yaml",
        "data/build/model.tflite",
        "data/real/../../household.yaml",
        "data/real/dzarwis/dad/notes.txt",
    ):
        response = await client.get("/api/wake_word_recorder/file", params={"path": outside})
        assert response.status == 400, outside
    response = await client.get("/api/wake_word_recorder/file", params={"path": "data/real/dzarwis/dad/gone.wav"})
    assert response.status == 404


async def test_household_read_and_replaced(hass: HomeAssistant, entry, household_dir, hass_client) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client()

    response = await client.get("/api/wake_word_recorder/household")
    assert yaml.safe_load(await response.text()) == HOUSEHOLD

    changed = {**HOUSEHOLD, "speakers": [*HOUSEHOLD["speakers"], {"id": "mum", "group": "adult"}]}
    response = await client.put("/api/wake_word_recorder/household", data=yaml.safe_dump(changed))
    assert response.status == 200
    assert yaml.safe_load((household_dir / "household.yaml").read_text()) == changed
    assert yaml.safe_load((household_dir / "household.yaml.bak").read_text()) == HOUSEHOLD

    response = await client.put("/api/wake_word_recorder/household", data="speakers: [")
    assert response.status == 400
    assert yaml.safe_load((household_dir / "household.yaml").read_text()) == changed  # a broken file is refused


async def test_only_for_administrators(
    hass: HomeAssistant, entry, hass_client_no_auth, hass_read_only_access_token
) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()
    assert (await client.get("/api/wake_word_recorder/files")).status == 401
    # a token of an ordinary user: the family's recordings are not for every account
    headers = {"Authorization": f"Bearer {hass_read_only_access_token}"}
    for url in ("/api/wake_word_recorder/files", "/api/wake_word_recorder/household"):
        assert (await client.get(url, headers=headers)).status == 403, url


def test_the_background_being_recorded_waits(household_dir) -> None:
    _, background = make_recordings(household_dir)
    busy = background.relative_to(household_dir).as_posix()
    assert busy not in [f["path"] for f in list_files(household_dir, busy)]
    assert busy in [f["path"] for f in list_files(household_dir)]


async def test_esphome_devices_and_models_sent_to_ha(hass: HomeAssistant, entry, hass_client, tmp_path) -> None:
    esphome = Path(hass.config.path("esphome"))
    esphome.mkdir(parents=True, exist_ok=True)
    (esphome / "home-assistant-voice-1a2b3c.yaml").write_text(
        'substitutions:\n  name: "sypialnia-ha-voice"\n  friendly_name: Sypialnia Voice\n'
        "packages:\n  voice: github://esphome/home-assistant-voice-pe/home-assistant-voice.yaml\n"
    )
    (esphome / "garage-door.yaml").write_text("esphome:\n  name: garage\n")  # not a voice device
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client()

    response = await client.get("/api/wake_word_recorder/esphome")
    assert (await response.json())["devices"] == [
        {"name": "sypialnia-ha-voice", "friendly_name": "Sypialnia Voice", "file": "home-assistant-voice-1a2b3c.yaml"}
    ]

    response = await client.put("/api/wake_word_recorder/wake_words/hej_jarvis.tflite", data=b"TFL3-1")
    assert response.status == 200 and (await response.json())["saved"] == "esphome/wake_words/hej_jarvis.tflite"
    await client.put("/api/wake_word_recorder/wake_words/hej_jarvis.tflite", data=b"TFL3-2")
    assert (esphome / "wake_words" / "hej_jarvis.tflite").read_bytes() == b"TFL3-2"
    assert (esphome / "wake_words" / "hej_jarvis.tflite.bak").read_bytes() == b"TFL3-1"  # the previous one kept
    for bad in ("../secrets.yaml", "hej_jarvis.yaml", "Hej.json"):
        response = await client.put(f"/api/wake_word_recorder/wake_words/{bad}", data=b"x")
        assert response.status in (400, 404), bad
    assert not (esphome.parent / "secrets.yaml").exists() or (esphome.parent / "secrets.yaml").read_bytes() != b"x"
