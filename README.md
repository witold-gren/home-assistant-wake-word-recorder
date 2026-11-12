# Wake Word Recorder for Home Assistant

[Polska wersja](README.pl.md)

Record the takes for your own wake word **through the voice satellites of your house**, with the very
microphones that will later listen for it. The device guides each person: *"Take 4 of 18. Step back
to the other side of the room. Say: Jarvis."*, listens without a wake word, checks the take and asks
again when it is too quiet, clipped or empty.

A model trained on such takes (e.g. with [microWakeWord](https://github.com/kahrendt/microWakeWord))
wakes reliably for your family, children's voices and accents included, where a stock word often
does not.

- **Guided sessions** on any Home Assistant voice satellite (Home Assistant Voice PE, ESPHome
  satellites): a person, a word, a satellite, **Start recording**.
- **Takes checked at once** (silence, too quiet, clipped, too short or long, drowned in noise); a good
  one is saved as a 16 kHz mono WAV, a bad one is asked again.
- **Varied delivery** the way training needs it: near, far, quiet, fast, from the side of the room…
- **Household background**: the satellite silently records 30 minutes of ordinary life (TV, dinner)
  for negatives and for measuring false wakes.
- **Wake log**: every wake of every satellite goes to a CSV file, so you can see how often each word
  wakes each room, and when it wakes for nothing.
- **Your pipelines stay as they are**: the satellite is switched to a recording pipeline for the
  session and switched back afterwards, also after a restart of Home Assistant.
- Everything stays at home: the takes are files in your config folder.

## Requirements

- Home Assistant 2026.2 or newer.
- A voice satellite (`assist_satellite`) with a pipeline select, e.g. Home Assistant Voice PE.
- A text-to-speech engine for the spoken instructions (the one of your preferred assistant is used).

## Installation

### HACS

1. HACS → three dots → **Custom repositories** → add this repository as an **Integration**.
2. Install **Wake Word Recorder** and restart Home Assistant.

### Manual

Copy `custom_components/wake_word_recorder` to `config/custom_components/wake_word_recorder` and
restart Home Assistant.

## Setup

**Settings → Devices & services → Add integration → Wake Word Recorder**, then give the household
folder, e.g. `/config/wakeword`. When it has no `household.yaml` yet, the integration creates one with
the people of Home Assistant (person entities): choose who will record and mark the children (they
need more takes). Then add your words to the file.

### household.yaml

```yaml
ui_language: en            # language of the spoken instructions (en, pl)

speakers:                  # everyone who will say a wake word
  - id: dad
    group: adult           # adult | child
  - id: kid
    group: child

words:
  jarvis:                  # id: the folder name of its takes
    wake_word: Jarvis      # what the satellite tells people to say
    speakers: all          # all, or a list of speaker ids
  robo:
    wake_word: Robo
    speakers: [kid]

targets:
  adult: 12                # takes per adult per word
  child: 18                # children's voices vary more: more takes
  held_out: 3              # takes per person kept aside for testing
```

Changes to the file are noticed within 10 seconds (or at once with the action
`wake_word_recorder.reload`).

## Recording

On the **Wake Word Recorder** device choose the **Person**, the **Wake word** and the **Satellite**,
then press **Start recording** (or call `wake_word_recorder.start_session`).

- The satellite switches to the **"Wake word recording"** pipeline (created by the integration, with
  the voice of your preferred assistant) and back to its own pipeline afterwards.
- It says the next take and listens **without a wake word**. After the instruction a short signal
  says "speak now" (the **Speak now signal** select: beep, announcement chime or none).
- A good take goes to `data/real/<word>/<person>/<person>_<NNN>_<variant>.wav`; a bad one is asked
  again ("Too quiet. Once more…").
- The session ends at the person's target (or after `count` takes), after 3 silent turns, after 6
  rejected takes in a row, or with **Stop recording**.
- `data/real/recordings.csv` notes which device recorded each take.
- `sensor.wake_word_recorder_status` shows the progress: person, word, done/target, last file, problems.

### Household background

Choose the **Background satellite** and press **Listen 30 min** (or call
`wake_word_recorder.start_background` with `minutes`). The device silently records to
`data/background/<satellite>_<date>.wav`, exactly what its microphones hear. Nobody should say the wake
word meanwhile.

### Wake log

Every wake of a satellite is written to `/config/assist/wake_word_log.csv` (semicolons, opens in a
spreadsheet), with no settings: `timestamp`, `area`, `device`, `wake_word`, `pipeline`, `outcome`,
`text`, `response`, `seconds`.

| `outcome` | Meaning |
|---|---|
| `no_speech` | The device woke and nobody said anything: the surest sign of a false wake. |
| `command` | Speech to text returned text; check `response` (`no_intent_match` with odd text is usually noise). |
| `duplicate` | Another device took the same word within 2 s: usually someone really said it and two rooms heard it. |
| `cancelled`, `error <code>` | The run was cancelled or failed. |

## Actions

| Action | What it does |
|---|---|
| `wake_word_recorder.start_session` | `satellite`, `speaker`, `word`, optional `count`: a guided session. |
| `wake_word_recorder.start_background` | `satellite`, `minutes` (default 30): background recording. |
| `wake_word_recorder.stop_session` | Ends the running session; the satellite gets its pipeline back. |
| `wake_word_recorder.reload` | Reads `household.yaml` again. |

## FAQ

**The start of a take has a beep or the end of the instruction in it.** A Voice PE streams its
microphone about 0.4 s before its speaker has finished. The integration speaks the instructions
through its own text-to-speech entity (**Instructions voice**), which adds the signal and a short
silence, and cuts sound that starts in the first 60 ms of a take. Do not change the text-to-speech of
the "Wake word recording" pipeline.

**"Too long" or "Too much noise" for every take.** The room is too loud for the word to stand out (TV,
music, a fan). Record in a quieter moment; record the noise separately with **Listen 30 min**.

**The satellite stayed on "Wake word recording".** The integration restores the previous pipeline
after each session, also after a restart. If a satellite was switched by hand meanwhile, choose its
pipeline again in the satellite's settings.

**"No household.yaml".** The folder given at setup has no `household.yaml`: add the integration again
and let it create one, or create it by hand as above.

## Development

`make help` lists the steps (`make setup`, `make test`, `make lint`).

## License

MIT
