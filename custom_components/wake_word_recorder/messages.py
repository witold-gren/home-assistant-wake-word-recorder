"""What the satellite says while recording, per language (no HA imports).

Delivery variants are the toolkit's (wakeword/languages): ids and order must stay the same,
they end up in file names. The texts are short, because the device speaks them before every take.
"""

from __future__ import annotations

TAKE_ORDER = (
    "near",
    "far",
    "quiet",
    "loud",
    "fast",
    "slow",
    "question",
    "tired",
    "cheerful",
    "background",
    "other_side",
    "normal",
)

TAKES = {
    "pl": {
        "near": "Stań blisko urządzenia.",
        "far": "Odejdź na drugi koniec pokoju.",
        "quiet": "Cicho.",
        "loud": "Głośno, jak z innego pokoju.",
        "fast": "Szybko.",
        "slow": "Powoli i wyraźnie.",
        "question": "Pytająco.",
        "tired": "Zmęczonym głosem.",
        "cheerful": "Wesoło.",
        "background": "Przy włączonym telewizorze albo muzyce.",
        "other_side": "Bokiem do urządzenia.",
        "normal": "Normalnie.",
    },
    "en": {
        "near": "Stand close to the device.",
        "far": "Step back to the other side of the room.",
        "quiet": "Quietly.",
        "loud": "Loudly, as if from another room.",
        "fast": "Quickly.",
        "slow": "Slowly and clearly.",
        "question": "Like a question.",
        "tired": "In a tired voice.",
        "cheerful": "Cheerfully.",
        "background": "With the TV or music on.",
        "other_side": "Facing away from the device.",
        "normal": "Normally.",
    },
}

MESSAGES = {
    "pl": {
        "instruction": "Nagranie {number} z {target}. {take} Powiedz: {wake_word}.",
        "start": "Nagrywam słowo {wake_word}, osoba: {speaker}.",
        "saved": "Zapisano.",
        "retry": "Jeszcze raz.",
        "no_speech": "Nic nie usłyszałem.",
        "too_short": "Za krótko.",
        "too_long": "Za długo, powiedz samo słowo.",
        "too_quiet": "Za cicho.",
        "clipped": "Za głośno.",
        "too_noisy": "Za dużo szumu, mów głośniej albo bliżej.",
        "overlap": "Mówiłem jeszcze w trakcie nagrania.",
        "done": "Gotowe. Nagrań słowa {wake_word}: {done}. Dziękuję!",
        "stopped": "Kończę nagrywanie. Zapisane: {done} z {target}.",
        "inactive": "Nagrywanie słów nie jest teraz włączone.",
        "background_start": "Nagrywam dźwięki domu przez {minutes} minut. W tym czasie nie mówcie słowa wybudzającego.",
        "background_done": "Skończyłem nagrywać dźwięki domu. Nagrano {minutes} minut.",
        "background_stopped": "Kończę nagrywanie dźwięków domu. Nagrano {minutes} minut.",
    },
    "en": {
        "instruction": "Take {number} of {target}. {take} Say: {wake_word}.",
        "start": "Recording the word {wake_word} for {speaker}.",
        "saved": "Saved.",
        "retry": "Once more.",
        "no_speech": "I did not hear anything.",
        "too_short": "Too short.",
        "too_long": "Too long, say just the word.",
        "too_quiet": "Too quiet.",
        "clipped": "Too loud.",
        "too_noisy": "Too much noise, speak louder or closer.",
        "overlap": "I was still speaking during the take.",
        "done": "Done. Takes of {wake_word}: {done}. Thank you!",
        "stopped": "Stopping the recording. Saved {done} of {target}.",
        "inactive": "Wake word recording is not active right now.",
        "background_start": "Recording the sounds of the home for {minutes} minutes. Please do not say the wake word meanwhile.",
        "background_done": "Finished recording the sounds of the home: {minutes} minutes.",
        "background_stopped": "Stopping the recording of the sounds of the home: {minutes} minutes recorded.",
    },
}


def take_variant(index: int) -> str:
    return TAKE_ORDER[index % len(TAKE_ORDER)]


class Texts:
    """Messages in one language (English when the language has none)."""

    def __init__(self, language: str | None) -> None:
        code = (language or "en").replace("_", "-").split("-")[0].lower()
        if code not in MESSAGES:
            code = "en"
        self.language = code
        self._messages = MESSAGES[code]
        self._takes = TAKES[code]

    def get(self, key: str, **values) -> str:
        return self._messages[key].format(**values)

    def instruction(self, number: int, target: int, variant: str, wake_word: str) -> str:
        return self.get(
            "instruction", number=number, target=max(target, number), take=self._takes[variant], wake_word=wake_word
        )

    def issues(self, issues: list[str]) -> str:
        return " ".join(self._messages[i] for i in issues if i in self._messages)
