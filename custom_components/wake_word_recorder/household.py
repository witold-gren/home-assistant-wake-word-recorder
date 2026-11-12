"""Read household.yaml of the wakeword toolkit (only what recording needs, no HA imports).

The toolkit (``wakeword`` command) owns and validates the file; this reader takes speakers,
words with their speakers and the per-person targets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

HOUSEHOLD_FILE = "household.yaml"
DEFAULT_TARGETS = {"adult": 12, "child": 18, "held_out": 3}


class HouseholdError(ValueError):
    """household.yaml is missing or invalid."""


class HouseholdMissing(HouseholdError):
    """There is no household.yaml yet."""


@dataclass(frozen=True)
class Speaker:
    id: str
    group: str


@dataclass(frozen=True)
class Word:
    id: str
    wake_word: str
    speakers: tuple[str, ...]


@dataclass
class Household:
    root: Path
    speakers: dict[str, Speaker]
    words: dict[str, Word]
    targets: dict[str, int]

    @property
    def name(self) -> str:
        return self.root.name

    def target(self, speaker: str) -> int:
        group = self.speakers[speaker].group
        return int(self.targets["child" if group == "child" else "adult"])

    def words_for(self, speaker: str) -> list[Word]:
        return [w for w in self.words.values() if speaker in w.speakers]

    def real_dir(self, word: str, speaker: str) -> Path:
        return self.root / "data" / "real" / word / speaker


def parse(data: Any, root: Path) -> Household:
    if not isinstance(data, dict):
        raise HouseholdError("household.yaml: expected a mapping")
    speakers: dict[str, Speaker] = {}
    for raw in data.get("speakers") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            raise HouseholdError("speakers: every entry needs an id")
        sid = str(raw["id"])
        speakers[sid] = Speaker(sid, str(raw.get("group", "adult")))
    if not speakers:
        raise HouseholdError("household.yaml: no speakers")
    words: dict[str, Word] = {}
    for wid, raw in (data.get("words") or {}).items():
        if not isinstance(raw, dict):
            raise HouseholdError(f"words.{wid}: expected settings")
        who = raw.get("speakers", "all")
        ids = tuple(speakers) if who == "all" else tuple(str(s) for s in ([who] if isinstance(who, str) else who))
        unknown = [s for s in ids if s not in speakers]
        if unknown:
            raise HouseholdError(f"words.{wid}: unknown speaker(s) {', '.join(unknown)}")
        words[str(wid)] = Word(str(wid), str(raw.get("wake_word") or wid), ids)
    targets = {**DEFAULT_TARGETS, **{k: int(v) for k, v in (data.get("targets") or {}).items()}}
    return Household(root, speakers, words, targets)


def load(root: Path | str) -> Household:
    """Blocking - run in the executor."""
    root = Path(root)
    file = root / HOUSEHOLD_FILE
    if not file.is_file():
        raise HouseholdMissing(f"{file} not found")
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise HouseholdError(f"{file}: {err}") from err
    return parse(data, root)


TEMPLATE = """\
# Wake word household: who records which words (see the Wake Word Recorder README)
ui_language: {language}

speakers:                # everyone who will say a wake word; group: adult | child
{speakers}

words: {{}}               # e.g. jarvis: {{wake_word: Jarvis, speakers: all}}

targets:
  adult: 12              # takes per adult per word
  child: 18              # children's voices vary more - more takes
  held_out: 3            # takes per person kept aside for testing

devices: {{}}             # ESPHome device name: [up to 2 words]
"""


def create(root: Path | str, speakers: list[tuple[str, str]], language: str) -> Household:
    """Write a new household.yaml with the given (id, group) speakers. Blocking."""
    root = Path(root)
    file = root / HOUSEHOLD_FILE
    if file.exists():
        raise HouseholdError(f"{file} already exists")
    if not speakers:
        raise HouseholdError("no speakers")
    lines = "\n".join(f"  - id: {sid}\n    group: {group}" for sid, group in speakers)
    root.mkdir(parents=True, exist_ok=True)
    file.write_text(TEMPLATE.format(language=language, speakers=lines), encoding="utf-8")
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# Recordings stay at home\ndata/\n", encoding="utf-8")
    return load(root)
