# Changelog

## 0.2.1

- A satellite with two wake words (Voice PE) records again: it has a pipeline per wake word and the
  recorder's conversation can run on either, so a session switches them all to "Wake word
  recording" and gives each its own back. Before, only the first was switched; the takes went
  through the second wake word's ordinary assistant (long processing, nothing saved).

## 0.2.0

- One **Satellite** select for both recording takes and listening to the home (they never run at
  once); the second "Background satellite" select is removed.
- Only satellites that are up are listed. Starting on one that is unavailable says so instead of
  waiting in silence (a session started on an unavailable satellite did nothing at all).
- The status shows `listening` while the home is being recorded, `recording` while takes are.

## 0.1.1

- Its own repository (it was part of a wake word training toolkit).
- A new `household.yaml` shows how to add a word by hand; the setup texts no longer point to the
  toolkit.

## 0.1.0

First version.

- Guided recording sessions through voice satellites: the satellite switches to its own "Wake word
  recording" pipeline, says each take, listens without a wake word, checks it and asks again; its
  pipeline is restored afterwards, also after a restart.
- Takes as 16 kHz mono WAV in `data/real/<word>/<person>/`, with `recordings.csv`; a status sensor.
- Instructions through its own text-to-speech entity with a "speak now" signal, and the end of the
  instruction cut from the start of a take (a Voice PE streams its microphone before its speaker
  has finished).
- Household background recordings through a satellite.
- The wake log: every wake of every satellite in `/config/assist/wake_word_log.csv`.
- Translations: English, Polish.
