"""Constants for Wake Word Recorder."""

DOMAIN = "wake_word_recorder"

CONF_HOUSEHOLD = "household_path"
# /config is the share most people already mount on their computer (Samba add-on)
DEFAULT_HOUSEHOLD = "/config/wakeword"

# the Assist pipeline the satellite is switched to while recording
PIPELINE_NAME = "Wake word recording"
# text the speech-to-text returns - an empty transcript would end the pipeline with an error
TRANSCRIPT = "wake word take"

SIGNAL_UPDATE = f"{DOMAIN}_update"
# a short silence played to make the satellite listen again without saying anything
STATIC_URL = f"/api/{DOMAIN}/static"
SILENCE_URL = f"{STATIC_URL}/silence.wav"
DEFAULT_BACKGROUND_MINUTES = 30
STORE_KEY = f"{DOMAIN}.restore"
STORE_VERSION = 1

# entity ids independent of the HA language (key = unique id suffix)
ENTITY_IDS = {
    "stt": "stt.wake_word_recorder",
    "tts": "tts.wake_word_recorder",
    "conversation": "conversation.wake_word_recorder",
    "status": "sensor.wake_word_recorder_status",
    "speaker": "select.wake_word_recorder_speaker",
    "word": "select.wake_word_recorder_word",
    "satellite": "select.wake_word_recorder_satellite",
    "signal": "select.wake_word_recorder_signal",
    "start": "button.wake_word_recorder_start",
    "stop": "button.wake_word_recorder_stop",
    "background": "button.wake_word_recorder_background",
}
