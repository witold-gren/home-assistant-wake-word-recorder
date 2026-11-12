# Wake Word Recorder dla Home Assistant

[English version](README.md)

Nagrania do własnego słowa wybudzającego **przez satelity głosowe w domu**, tymi samymi mikrofonami,
które później będą go nasłuchiwać. Urządzenie prowadzi każdą osobę: *„Nagranie 4 z 18. Odejdź na
drugi koniec pokoju. Powiedz: Dżarwis.”*, słucha bez słowa wybudzającego, sprawdza nagranie i prosi
o powtórkę, gdy jest za ciche, przesterowane albo puste.

Model wytrenowany na takich nagraniach (np. w [microWakeWord](https://github.com/kahrendt/microWakeWord))
budzi się niezawodnie dla Twojej rodziny, także na głosy dzieci i akcenty, na które gotowe słowo
często nie reaguje.

- **Prowadzone sesje** na dowolnym satelicie głosowym Home Assistant (Home Assistant Voice PE,
  satelity ESPHome): osoba, słowo, satelita, **Rozpocznij nagrywanie**.
- **Nagranie sprawdzane od razu** (cisza, za cicho, przesterowane, za krótkie albo za długie,
  zagłuszone hałasem); dobre trafia do pliku WAV 16 kHz mono, złe jest powtarzane.
- **Różne sposoby wypowiedzi**, jakich potrzebuje trening: blisko, daleko, cicho, szybko, z boku pokoju…
- **Tło domu**: satelita po cichu nagrywa 30 minut zwykłego życia (telewizor, obiad) jako przykłady
  negatywne i do mierzenia fałszywych wybudzeń.
- **Dziennik wybudzeń**: każde wybudzenie każdej satelity trafia do pliku CSV, więc widać, jak często
  dane słowo budzi dany pokój i kiedy budzi się bez powodu.
- **Twoje potoki zostają bez zmian**: na czas sesji satelita przechodzi na potok nagrywania, a potem
  wraca do swojego, także po restarcie Home Assistant.
- Wszystko zostaje w domu: nagrania to pliki w folderze konfiguracji.

## Wymagania

- Home Assistant 2026.2 lub nowszy.
- Satelita głosowy (`assist_satellite`) z wyborem potoku, np. Home Assistant Voice PE.
- Silnik zamiany tekstu na mowę dla instrukcji (używany jest ten z Twojego preferowanego asystenta).

## Instalacja

### HACS

1. HACS → trzy kropki → **Własne repozytoria** → dodaj to repozytorium jako **Integracja**.
2. Zainstaluj **Wake Word Recorder** i uruchom ponownie Home Assistant.

### Ręcznie

Skopiuj `custom_components/wake_word_recorder` do `config/custom_components/wake_word_recorder` i
uruchom ponownie Home Assistant.

## Konfiguracja

**Ustawienia → Urządzenia i usługi → Dodaj integrację → Wake Word Recorder**, potem podaj folder
domu, np. `/config/wakeword`. Gdy nie ma w nim jeszcze `household.yaml`, integracja tworzy go z osób
Home Assistant (encje person): wybierz, kto będzie nagrywał, i zaznacz dzieci (potrzebują więcej
nagrań). Potem dopisz do pliku swoje słowa.

### household.yaml

```yaml
ui_language: pl            # język instrukcji mówionych (pl, en)

speakers:                  # wszyscy, którzy będą mówić słowo wybudzające
  - id: tata
    group: adult           # adult | child
  - id: ola
    group: child

words:
  dzarwis:                 # id: nazwa folderu z nagraniami
    wake_word: Dżarwis     # co satelita każe powiedzieć
    speakers: all          # all albo lista id osób
  robo:
    wake_word: Robo
    speakers: [ola]

targets:
  adult: 12                # nagrań na dorosłego na słowo
  child: 18                # głosy dzieci są bardziej zmienne: więcej nagrań
  held_out: 3              # nagrań na osobę odłożonych do testów
```

Zmiany w pliku są wykrywane w ciągu 10 sekund (albo od razu akcją `wake_word_recorder.reload`).

## Nagrywanie

Na urządzeniu **Wake Word Recorder** wybierz **Osobę**, **Słowo wybudzające** i **Satelitę** (lista
pokazuje tylko działające satelity), potem
naciśnij **Rozpocznij nagrywanie** (albo wywołaj `wake_word_recorder.start_session`).

- Satelita przechodzi na potok **„Wake word recording”** (tworzy go integracja, z głosem Twojego
  preferowanego asystenta), a potem wraca do swojego potoku.
- Mówi, jakie jest następne nagranie, i słucha **bez słowa wybudzającego**. Po instrukcji krótki
  sygnał mówi „teraz” (wybór **Sygnał „mów teraz”**: bip, dźwięk zapowiedzi albo brak).
- Dobre nagranie trafia do `data/real/<słowo>/<osoba>/<osoba>_<NNN>_<wariant>.wav`, złe jest
  powtarzane („Za cicho. Jeszcze raz…”).
- Sesja kończy się po osiągnięciu celu osoby (albo po `count` nagraniach), po 3 turach ciszy, po 6
  odrzuconych nagraniach z rzędu albo przyciskiem **Zakończ nagrywanie**.
- `data/real/recordings.csv` zapisuje, które urządzenie nagrało każde nagranie.
- `sensor.wake_word_recorder_status` pokazuje `recording` i postęp: osoba, słowo, zrobione/cel, ostatni
  plik, problemy.

### Tło domu

Przy tej samej wybranej **Satelicie** naciśnij **Nasłuchuj 30 min** (albo wywołaj
`wake_word_recorder.start_background` z `minutes`). Urządzenie po cichu nagrywa do
`data/background/<satelita>_<data>.wav` dokładnie to, co słyszą jego mikrofony; status pokazuje
`listening` (Nasłuchiwanie otoczenia). W tym czasie nikt nie
powinien mówić słowa wybudzającego.

### Dziennik wybudzeń

Każde wybudzenie satelity trafia do `/config/assist/wake_word_log.csv` (średniki, otwiera się w
arkuszu), bez żadnych ustawień: `timestamp`, `area`, `device`, `wake_word`, `pipeline`, `outcome`,
`text`, `response`, `seconds`.

| `outcome` | Znaczenie |
|---|---|
| `no_speech` | Urządzenie się obudziło i nikt nic nie powiedział: najpewniejszy znak fałszywego wybudzenia. |
| `command` | Zamiana mowy na tekst zwróciła tekst; sprawdź `response` (`no_intent_match` z dziwnym tekstem to zwykle hałas). |
| `duplicate` | Inne urządzenie przyjęło to samo słowo w ciągu 2 s: zwykle ktoś naprawdę je powiedział i usłyszały je dwa pokoje. |
| `cancelled`, `error <kod>` | Przebieg został anulowany albo się nie udał. |

## Akcje

| Akcja | Co robi |
|---|---|
| `wake_word_recorder.start_session` | `satellite`, `speaker`, `word`, opcjonalnie `count`: prowadzona sesja. |
| `wake_word_recorder.start_background` | `satellite`, `minutes` (domyślnie 30): nagrywanie tła. |
| `wake_word_recorder.stop_session` | Kończy trwającą sesję; satelita wraca do swojego potoku. |
| `wake_word_recorder.reload` | Wczytuje `household.yaml` ponownie. |

## FAQ

**Na początku nagrania słychać piknięcie albo koniec instrukcji.** Voice PE zaczyna przesyłać mikrofon
ok. 0,4 s, zanim jego głośnik skończy. Integracja mówi instrukcje własną encją zamiany tekstu na mowę
(**Głos poleceń**), która dokłada sygnał i chwilę ciszy, i wycina dźwięk zaczynający się w
pierwszych 60 ms nagrania. Nie zmieniaj zamiany tekstu na mowę w potoku „Wake word recording”.

**„Za długo” albo „Za dużo szumu” przy każdym nagraniu.** W pokoju jest za głośno, żeby słowo się
wyróżniało (telewizor, muzyka, wentylator). Nagrywaj w cichszej chwili; hałas nagraj osobno przyciskiem
**Nasłuchuj 30 min**.

**Satelita została na „Wake word recording”.** Integracja przywraca poprzedni potok po każdej sesji,
także po restarcie. Jeśli w międzyczasie satelita została przełączona ręcznie, wybierz jej potok
ponownie w ustawieniach satelity.

**Rozpocznij nagrywanie albo Nasłuchuj 30 min nic nie robi.** Do wersji 0.1.1 lista satelit pokazywała
też niedostępne, a sesja na takiej czekała w ciszy. Od 0.2.0 lista ma tylko działające satelity, a start
na takiej, która przestała działać, pokazuje błąd.

**„No household.yaml”.** W folderze podanym przy konfiguracji nie ma `household.yaml`: dodaj
integrację ponownie i pozwól jej go utworzyć albo utwórz go ręcznie jak wyżej.

## Rozwój

`make help` pokazuje kroki (`make setup`, `make test`, `make lint`).

## Licencja

MIT
