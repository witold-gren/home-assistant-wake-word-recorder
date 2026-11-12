"""A recording session: the satellite asks for each take itself, no wake word needed.

The satellite is switched to the "Wake word recording" pipeline and starts a conversation with
the first instruction. Each answer goes through that pipeline: our speech-to-text stores the take,
our agent says how it went and the next instruction and keeps the conversation open, so the
satellite listens again right away. When the satellite stops listening without an answer
(silence, a broken-off conversation), the session asks again; after a few misses it ends.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .household import Household
from .messages import TAKE_ORDER, Texts, take_variant
from .take import TakeResult, progress, save_take

_LOGGER = logging.getLogger(__name__)

MAX_MISSES = 3  # silent turns in a row -> stop
MAX_FAILS = 6  # rejected takes in a row (too quiet, clipped …) -> stop
# after the agent's reply the satellite may pass through idle before listening again
RESUME_SECONDS = 4.0
MAX_AUDIO_BYTES = 20 * 16000 * 2  # 20 s; longer is not a wake word


def satellite_player(hass: HomeAssistant, satellite: str) -> str | None:
    """The media player on the satellite's device: the speaker it talks through."""
    registry = er.async_get(hass)
    entry = registry.async_get(satellite)
    if entry is None or entry.device_id is None:
        return None
    return next(
        (e.entity_id for e in er.async_entries_for_device(registry, entry.device_id) if e.domain == "media_player"),
        None,
    )


@dataclass
class SessionInfo:
    satellite: str
    speaker: str
    word: str
    wake_word: str
    done: int
    target: int  # stop when done reaches this
    variant: str
    last_file: str | None = None
    last_issues: tuple[str, ...] = ()


class Session:
    def __init__(
        self,
        hass: HomeAssistant,
        household: Household,
        satellite: str,
        speaker: str,
        word: str,
        count: int | None,
        on_end: Callable[[Session], None],
        on_change: Callable[[], None],
    ) -> None:
        self.hass = hass
        self.household = household
        self.satellite = satellite
        self.speaker = speaker
        self.word = word
        self.wake_word = household.words[word].wake_word
        self.texts = Texts(hass.config.language)
        self._count = count
        self._on_end = on_end
        self._on_change = on_change
        self.active = False
        self.info: SessionInfo | None = None
        self._result: TakeResult | None = None  # take of the current turn, read by the agent
        self._heard = False  # audio arrived in the current listening turn
        self._listened = False  # the satellite listened since the last prompt
        self._misses = 0
        self._fails = 0
        # The satellite's own speaker: a take during which it played holds the device's voice
        self._player = satellite_player(hass, satellite)
        self._overlap = False
        self._unsub: CALLBACK_TYPE | None = None
        self._resume: CALLBACK_TYPE | None = None
        # texts after which the satellite listens for a take (spoken with the "speak now" signal)
        self.listen_prompts: set[str] = set()

    async def async_start(self) -> None:
        state = await self.hass.async_add_executor_job(progress, self.household, self.word, self.speaker)
        if self._count:
            target = state.done + self._count
        elif state.done < state.target:
            target = state.target
        else:
            # the person's target is reached - record one more round of every delivery variant
            target = state.done + len(TAKE_ORDER)
        self.info = SessionInfo(
            self.satellite, self.speaker, self.word, self.wake_word, state.done, target, state.variant
        )
        self.active = True
        watched = [self.satellite, *([self._player] if self._player else [])]
        self._unsub = async_track_state_change_event(self.hass, watched, self._state_changed)
        start = self.texts.get("start", wake_word=self.wake_word, speaker=self.speaker)
        await self._ask(f"{start} {self._instruction()}")
        self._on_change()

    def _instruction(self) -> str:
        assert self.info is not None
        return self.texts.instruction(self.info.done + 1, self.info.target, self.info.variant, self.wake_word)

    async def _ask(self, text: str) -> None:
        self._listened = False
        self.listen_prompts.add(text)
        await self.hass.services.async_call(
            "assist_satellite",
            "start_conversation",
            {"entity_id": self.satellite, "start_message": text, "preannounce": False},
            blocking=False,
        )

    async def _announce(self, text: str) -> None:
        await self.hass.services.async_call(
            "assist_satellite",
            "announce",
            {"entity_id": self.satellite, "message": text, "preannounce": False},
            blocking=False,
        )

    async def async_add_audio(self, pcm: bytes) -> None:
        """Audio of one turn from our speech-to-text."""
        if not self.active:
            return
        self._heard = True
        self._misses = 0
        if self._overlap:
            # The instruction came late (a slow text-to-speech) and played into the take
            self._result = TakeResult(None, ["overlap"])
            return
        self._result = await self.hass.async_add_executor_job(
            save_take, self.household, self.word, self.speaker, pcm[:MAX_AUDIO_BYTES], self.satellite
        )

    @callback
    def reply(self) -> tuple[str, bool]:
        """What our agent says after a turn, and whether the satellite keeps listening."""
        assert self.info is not None
        result, self._result = self._result, None
        if not self.active:
            return self.texts.get("inactive"), False
        if result is None or result.file is None:
            self._fails += 1
            issues = result.issues if result else ["no_speech"]
            if result:
                self.info.last_issues = tuple(issues)
            self._on_change()
            if self._fails >= MAX_FAILS:
                self.hass.async_create_task(self.async_stop(announce=False))
                return self.texts.get("stopped", done=self.info.done, target=self.info.target), False
            return self._prompt(f"{self.texts.issues(issues)} {self.texts.get('retry')} {self._instruction()}")
        self._fails = 0
        self.info.done += 1
        self.info.last_file = result.file.name
        self.info.last_issues = ()
        self.info.variant = take_variant(self.info.done)
        if self.info.done >= self.info.target:
            self.hass.async_create_task(self.async_stop(announce=False))
            return self.texts.get("done", wake_word=self.wake_word, done=self.info.done), False
        self._on_change()
        return self._prompt(f"{self.texts.get('saved')} {self._instruction()}")

    def _prompt(self, text: str) -> tuple[str, bool]:
        """A reply after which the satellite keeps listening."""
        self.listen_prompts.add(text)
        return text, True

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if not self.active or new is None:
            return
        if event.data["entity_id"] == self._player:
            satellite = self.hass.states.get(self.satellite)
            if new.state == "playing" and satellite is not None and satellite.state == "listening":
                self._overlap = True
            return
        if new.state == "listening":
            self._cancel_resume()
            self._listened, self._heard, self._overlap = True, False, False
            return
        if new.state == "idle" and self._listened:
            self._cancel_resume()
            self._resume = async_call_later(self.hass, RESUME_SECONDS, self._check_idle)

    @callback
    def _check_idle(self, _now) -> None:
        """Still idle a while after listening: the conversation ended without us."""
        self._resume = None
        state = self.hass.states.get(self.satellite)
        if not self.active or state is None or state.state != "idle":
            return
        if self._heard:
            # the agent replied but the satellite did not listen again - ask once more
            self.hass.async_create_task(self._ask(self._instruction()))
            return
        self._misses += 1
        if self._misses >= MAX_MISSES:
            self.hass.async_create_task(self.async_stop())
            return
        text = f"{self.texts.get('no_speech')} {self._instruction()}"
        self.hass.async_create_task(self._ask(text))

    @callback
    def _cancel_resume(self) -> None:
        if self._resume is not None:
            self._resume()
            self._resume = None

    async def async_stop(self, announce: bool = True) -> None:
        if not self.active:
            return
        self.active = False
        self._cancel_resume()
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if announce and self.info is not None:
            await self._announce(self.texts.get("stopped", done=self.info.done, target=self.info.target))
        _LOGGER.info("Recording of %s by %s ended", self.word, self.speaker)
        self._on_end(self)
