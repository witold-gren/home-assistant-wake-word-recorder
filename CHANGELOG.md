# Changelog

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
