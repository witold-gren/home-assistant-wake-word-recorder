"""HTTP API for the training panel: the recordings made here and household.yaml, without a file share.

The panel (on a Mac or on the GPU machine) keeps its own household folder and asks Home Assistant
for what only Home Assistant has: the takes recorded through the satellites (``data/real``), the
background recordings (``data/background``) and household.yaml, which it also sends back after an
edit. Every view needs a Home Assistant administrator (a long-lived token in the panel).

    GET /api/wake_word_recorder/files             {"household", "files": [{"path", "size", "mtime"}]}
    GET /api/wake_word_recorder/file?path=<path>  the file
    GET /api/wake_word_recorder/household         household.yaml
    PUT /api/wake_word_recorder/household         a new household.yaml (validated, the old one kept as .bak)
    GET /api/wake_word_recorder/esphome           {"devices": [{"name", "friendly_name", "file"}]} - voice devices
    PUT /api/wake_word_recorder/wake_words/<file> an exported model (<word>.tflite / <word>.json) into
                                                  /config/esphome/wake_words/, the previous one kept as .bak
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
import re
import shutil

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import yaml

from .background import BackgroundSession
from .household import HOUSEHOLD_FILE, HouseholdError, parse
from .take import LOG_FILE

AREAS = ("real", "background")  # under data/: what the satellites recorded
ESPHOME_DIR = "esphome"  # in the Home Assistant config folder
WAKE_WORDS_DIR = "wake_words"  # next to the device YAMLs: `model: wake_words/<word>.json`
WAKE_WORD_FILE = re.compile(r"^[a-z0-9_]+\.(tflite|json)$")
MAX_WAKE_WORD_BYTES = 5_000_000  # a model is ~60 kB
# a device YAML of a voice device: the Voice PE package, a voice assistant or wake word detection
VOICE_MARKERS = ("home-assistant-voice", "voice_assistant:", "micro_wake_word:")


def esphome_devices(folder: Path) -> list[dict[str, str]]:
    """Voice devices configured in the ESPHome folder (the toolkit's wakeword.scaffold does the same). Blocking.

    The device name comes from the YAML (`substitutions: name:` or `esphome: name:`), which can differ
    from the file name. Read as text, so `!secret` tags and packages need no resolving.
    """
    devices = []
    for path in sorted(Path(folder).glob("*.yaml")) if Path(folder).is_dir() else []:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(marker in text for marker in VOICE_MARKERS):
            continue
        found = {}
        for key in ("name", "friendly_name"):
            for match in re.finditer(rf"^\s+{key}:\s*[\"']?([^\"'\n#]+?)[\"']?\s*(?:#.*)?$", text, re.MULTILINE):
                if "${" not in match.group(1):
                    found[key] = match.group(1).strip()
                    break
        name = found.get("name", path.stem)
        devices.append({"name": name, "friendly_name": found.get("friendly_name", name), "file": path.name})
    return devices


def write_wake_word(folder: Path, name: str, data: bytes) -> Path:
    """An exported model file into <esphome>/wake_words/ (the previous one kept as .bak). Blocking."""
    if not WAKE_WORD_FILE.match(name):
        raise ValueError(f"not a wake word model file: {name}")
    if len(data) > MAX_WAKE_WORD_BYTES:
        raise ValueError("file too large for a wake word model")
    target = Path(folder) / WAKE_WORDS_DIR / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        shutil.copyfile(target, target.with_name(target.name + ".bak"))
    target.write_bytes(data)
    return target


def list_files(root: Path, busy: str | None = None) -> list[dict]:
    """The recordings and the takes log, paths relative to the household folder. Blocking.

    ``busy``: the background recording being written now - left out until it is finished.
    """
    files = []
    for area in AREAS:
        base = root / "data" / area
        for path in sorted(base.rglob("*")) if base.is_dir() else []:
            relative = path.relative_to(root).as_posix()
            if not path.is_file() or path.name.startswith(".") or relative == busy:
                continue
            if path.suffix != ".wav" and path.name != LOG_FILE:
                continue
            stat = path.stat()
            files.append({"path": relative, "size": stat.st_size, "mtime": stat.st_mtime})
    return files


def checked_path(root: Path, relative: str) -> Path:
    """A file the API may hand out: a recording or the takes log inside data/real or data/background."""
    path = (root / relative).resolve()
    allowed = [(root / "data" / area).resolve() for area in AREAS]
    if not any(area in path.parents for area in allowed):
        raise ValueError(f"not a recording: {relative}")
    if path.suffix != ".wav" and path.name != LOG_FILE:
        raise ValueError(f"not a recording: {relative}")
    if not path.is_file():
        raise FileNotFoundError(relative)
    return path


def write_household(root: Path, text: str) -> None:
    """Replace household.yaml after checking it reads; the previous one stays as household.yaml.bak. Blocking."""
    try:
        parse(yaml.safe_load(text), root)
    except (yaml.YAMLError, HouseholdError) as err:
        raise ValueError(f"household.yaml: {err}") from err
    target = root / HOUSEHOLD_FILE
    if target.is_file():
        shutil.copyfile(target, root / f"{HOUSEHOLD_FILE}.bak")
    target.write_text(text, encoding="utf-8")


def _entry(hass: HomeAssistant):
    from . import get_entry  # noqa: PLC0415 - the package imports this module

    return get_entry(hass)


class _AdminView(HomeAssistantView):
    requires_auth = True

    def _root(self, request: web.Request) -> Path:
        if not request["hass_user"].is_admin:
            raise web.HTTPForbidden(text="a Home Assistant administrator is needed")
        try:
            entry = _entry(request.app["hass"])
        except HomeAssistantError as err:
            raise web.HTTPServiceUnavailable(text=str(err)) from err
        return entry.runtime_data.household.root


class FilesView(_AdminView):
    url = "/api/wake_word_recorder/files"
    name = "api:wake_word_recorder:files"

    async def get(self, request: web.Request) -> web.Response:
        root = self._root(request)
        hass = request.app["hass"]
        session = _entry(hass).runtime_data.session
        busy = session.info.file if isinstance(session, BackgroundSession) and session.info else None
        files = await hass.async_add_executor_job(list_files, root, busy)
        return self.json({"household": root.name, "files": files})


class FileView(_AdminView):
    url = "/api/wake_word_recorder/file"
    name = "api:wake_word_recorder:file"

    async def get(self, request: web.Request) -> web.StreamResponse:
        root = self._root(request)
        try:
            path = await request.app["hass"].async_add_executor_job(checked_path, root, request.query.get("path", ""))
        except FileNotFoundError:
            return self.json_message("no such recording", HTTPStatus.NOT_FOUND)
        except ValueError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        return web.FileResponse(path)


class HouseholdView(_AdminView):
    url = "/api/wake_word_recorder/household"
    name = "api:wake_word_recorder:household"

    async def get(self, request: web.Request) -> web.Response:
        path = self._root(request) / HOUSEHOLD_FILE
        text = await request.app["hass"].async_add_executor_job(path.read_text, "utf-8")
        return web.Response(text=text, content_type="application/yaml")

    async def put(self, request: web.Request) -> web.Response:
        root = self._root(request)
        text = await request.text()
        try:
            await request.app["hass"].async_add_executor_job(write_household, root, text)
        except ValueError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        return self.json({"saved": True})  # the household is read again within WATCH_INTERVAL


class EsphomeView(_AdminView):
    url = "/api/wake_word_recorder/esphome"
    name = "api:wake_word_recorder:esphome"

    async def get(self, request: web.Request) -> web.Response:
        self._root(request)
        hass = request.app["hass"]
        devices = await hass.async_add_executor_job(esphome_devices, Path(hass.config.path(ESPHOME_DIR)))
        return self.json({"devices": devices})


class WakeWordFileView(_AdminView):
    url = "/api/wake_word_recorder/wake_words/{name}"
    name = "api:wake_word_recorder:wake_words"

    async def put(self, request: web.Request, name: str) -> web.Response:
        self._root(request)
        hass = request.app["hass"]
        data = await request.read()
        try:
            target = await hass.async_add_executor_job(write_wake_word, Path(hass.config.path(ESPHOME_DIR)), name, data)
        except ValueError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        return self.json({"saved": f"{ESPHOME_DIR}/{WAKE_WORDS_DIR}/{target.name}"})


def async_register(hass: HomeAssistant) -> None:
    for view in (FilesView(), FileView(), HouseholdView(), EsphomeView(), WakeWordFileView()):
        hass.http.register_view(view)
