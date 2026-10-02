import threading
import tkinter as tk
from tkinter import scrolledtext, filedialog, messagebox, simpledialog, colorchooser
import pyttsx3
import speech_recognition as sr
import ollama
import pyaudiowpatch
import sys
import os
from importlib.util import find_spec
from gemini_ai import ask_gemini
import json
from pathlib import Path
import re
import time
import math
import random
import uuid
import tempfile
from datetime import datetime
import memory
import conversation_history
from router import classify
from conversation_history import (
    load_history, add_turn, clear_history, list_sessions, new_session,
    switch_session, get_active_session_id, rename_session, delete_session,
)
from openai_ai import ask_openai
from app_launcher import launch_app as universal_launch_app
from routines import load_routines, save_routines, match_routine, execute_routine
from voice_output import TTSController
from voice_selection import choose_voice
from expressive_tts import GeminiExpressiveTTS
from offline_tts import OfflinePiperTTS
from galaxy_view import generate_spiral_stars
from tools.hand_tracking import HandTracker
from confirmations import PendingConfirmation

pending_system_action = PendingConfirmation(timeout_seconds=30)
from tools.apps import open_website
from tools.system import (
    get_battery,
    mute,
    unmute,
    volume_up,
    volume_down,
    lock_pc,
    shutdown_pc,
    restart_pc,
    cancel_shutdown,
)
from tools.files import open_folder, search_files, search_file_contents, open_file, create_file
from tools.network import set_wifi_enabled
from ai_routing import route_message
from ollama_models import choose_installed_model, bounded_timeout_seconds
from microphone_status import MicrophoneStatus
from voice_input import recognize_audio
from personality import build_gemini_prompt, build_local_messages
from computer_use import run_computer_task

# Windows may start Python with a legacy console code page. Keep emoji and other
# Unicode replies from crashing diagnostics or voice-loop logging when printed.
for _console_stream in (sys.stdout, sys.stderr):
    if hasattr(_console_stream, "reconfigure"):
        try:
            _console_stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

# =========================================================
# PYAUDIOWPATCH COMPATIBILITY
# =========================================================

sys.modules["pyaudio"] = pyaudiowpatch

# Start only one Maanav process and one wake-word microphone owner.
_instance_mutex = None
if __name__ == "__main__" and not any(flag in sys.argv for flag in (
    "--self-test", "--voice-check", "--voice-demo", "--offline-voice-demo", "--offline-sample-test",
    "--offline-ai-test", "--online-ai-test", "--ui-smoke-test", "--memory-flow-test",
    "--wake-sample-test"
)):
    from single_instance import acquire as acquire_single_instance, focus_existing_window
    if not acquire_single_instance():
        focused = focus_existing_window()
        if focused:
            print("Maanav AI is already running; brought the existing window forward.")
        else:
            print("Maanav AI is already running, but its window could not be found.")
        raise SystemExit(0)

if "--ui-smoke-test" in sys.argv:
    # Keep a layout-only test isolated from the user's saved chats.
    import tempfile
    _ui_test_storage = tempfile.TemporaryDirectory(prefix="maanav-ui-test-")
    conversation_history.STORE_FILE = Path(_ui_test_storage.name) / "chats.json"
    conversation_history.HISTORY_FILE = Path(_ui_test_storage.name) / "legacy.json"
    conversation_history._temporary_sessions = {}
    conversation_history._active_session_override = None


# =========================================================
# SETTINGS
# =========================================================

MODEL = "qwen3.5:9b"
OLLAMA_TIMEOUT_SECONDS = bounded_timeout_seconds(os.getenv("MAANAV_OLLAMA_TIMEOUT", "180"))
OLLAMA_CLIENT = ollama.Client(timeout=OLLAMA_TIMEOUT_SECONDS)
MEMORY_FILE = Path(__file__).with_name("memory.json")
SETTINGS_FILE = Path(__file__).with_name("settings.json")


def read_settings():
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return settings if isinstance(settings, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_setting(key, value):
    return save_settings({key: value})


def save_settings(updates):
    settings = read_settings()
    settings.update(updates)
    try:
        temporary = SETTINGS_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        temporary.replace(SETTINGS_FILE)
        return True
    except OSError as error:
        print("Settings save error:", error)
        return False


_configured_file_roots = read_settings().get("file_search_roots", [])
FILE_SEARCH_ROOTS = [path for path in _configured_file_roots
                     if isinstance(path, str) and path.strip()] if isinstance(_configured_file_roots, list) else []


_saved_settings = read_settings()
EXPRESSIVE_VOICE_ENABLED = bool(_saved_settings.get("expressive_voice", False))
MAANAV_RESPONSE_STYLE = _saved_settings.get("response_style", "natural")
if MAANAV_RESPONSE_STYLE not in {"natural", "concise", "detailed", "creative"}:
    MAANAV_RESPONSE_STYLE = "natural"
SPEAK_RESPONSES_ENABLED = bool(_saved_settings.get("speak_responses", True))
NESUKO_WANDER_ENABLED = bool(_saved_settings.get("nesuko_random_movement", True))
NESUKO_SOUNDS_ENABLED = bool(_saved_settings.get("nesuko_sound_effects", False))
expressive_tts = GeminiExpressiveTTS()
offline_tts = OfflinePiperTTS()


def physical_input_devices():
    audio = None
    try:
        audio = pyaudiowpatch.PyAudio()
        devices = []
        seen = set()
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            name = info.get("name", f"Microphone {index}")
            lowered = name.lower()
            if info.get("maxInputChannels", 0) <= 0 or info.get("isLoopbackDevice", False):
                continue
            if any(term in lowered for term in ("stereo mix", "loopback", "sound mapper", "primary sound")):
                continue
            # PortAudio can list the same physical mic through several host APIs.
            key = "microphone array" if "microphone array" in lowered else lowered
            if key in seen:
                continue
            seen.add(key)
            devices.append((index, name))
        return devices
    except Exception as error:
        print("Microphone discovery error:", error)
        return []
    finally:
        if audio is not None:
            audio.terminate()


def startup_microphone():
    devices = physical_input_devices()
    try:
        settings = read_settings()
        saved_name = settings.get("microphone_name")
        for index, name in devices:
            if saved_name and name == saved_name:
                return index, name
        saved_index = settings.get("microphone_device")
        # An index is only stable when there was no saved device name to validate.
        if not saved_name:
            for index, name in devices:
                if index == saved_index:
                    return index, name
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass

    # Prefer the actual built-in array, never Windows Stereo Mix or a loopback.
    built_in = next((item for item in devices if "microphone array" in item[1].lower()), None)
    if built_in:
        return built_in
    if devices:
        return devices[0]
    return 1, "Default microphone"


MIC_DEVICE, MIC_DEVICE_NAME = startup_microphone()
microphone_status = MicrophoneStatus()

WAKE_WORD = "wake up"
TTS_VOICE_NAME = "Hazel"

voice_enabled = True
wake_word_enabled = bool(read_settings().get("wake_listening", True))
voice_call_active = False
_voice_enabled_before_call = None

voice_loop_running = True
voice_activity = "idle"

_microphone_lock = threading.Lock()
_voice_loop_guard = threading.Lock()
_wispr_dictation_active = threading.Event()
_voice_input_paused = threading.Event()
_manual_voice_trigger = threading.Event()
_chat_request_lock = threading.Lock()
_assistant_request_lock = threading.Lock()
_active_chat_cancel = None
OFFLINE_VOICE_ONLY = "--offline-voice" in sys.argv or os.getenv("MAANAV_OFFLINE_VOICE", "").lower() in {"1", "true", "yes"}
OFFLINE_AI_ONLY = any(flag in sys.argv for flag in ("--offline", "--offline-ai-test")) or os.getenv(
    "MAANAV_OFFLINE_AI", ""
).lower() in {"1", "true", "yes"}

# Keep one recognizer so its energy threshold can adapt across wake-word listens.
voice_recognizer = sr.Recognizer()
voice_recognizer.dynamic_energy_threshold = True
mic_calibrated = False
_whisper_model = None
_whisper_model_checked = False
_whisper_lock = threading.Lock()


# =========================================================
# MEMORY
# =========================================================

def load_memory():
    return memory.get_all_memory()


def save_memory(key, value):
    try:
        return memory.remember(key, value)
    except Exception as error:
        print("Memory save error:", error)
        return False


# =========================================================
# TEXT TO SPEECH
# =========================================================

def choose_tts_voice(voices, text=""):
    """Prefer a matching language voice, then the configured warm Windows voice."""
    configured_voice = read_settings().get("tts_voice_name", TTS_VOICE_NAME)
    return choose_voice(voices, text, (configured_voice, "aria", "jenny", "hazel"))


def set_voice_activity(activity):
    global voice_activity
    voice_activity = activity
    companion = globals().get("desktop_companion")
    if companion is not None:
        companion.activity_changed(activity)


def speak_preferred_voice(text, is_cancelled=lambda: False):
    expressive_active = (
        EXPRESSIVE_VOICE_ENABLED and not OFFLINE_VOICE_ONLY and not OFFLINE_AI_ONLY
    )
    if expressive_active:
        try:
            if expressive_tts.speak(text, is_cancelled=is_cancelled):
                return True
        except Exception as error:
            print("Expressive cloud speech failed; trying local voice:", error)
        if is_cancelled():
            return False
    if offline_tts.available() and offline_tts.supports_text(text):
        try:
            if offline_tts.speak(text, is_cancelled=is_cancelled):
                return True
        except Exception as error:
            print("Local Piper speech failed; trying Windows voice:", error)
    return False


def stop_preferred_voice():
    expressive_tts.stop()
    offline_tts.stop()


tts_controller = TTSController(
    engine_factory=pyttsx3.init,
    voice_chooser=choose_tts_voice,
    is_enabled=lambda: voice_enabled and SPEAK_RESPONSES_ENABLED,
    get_activity=lambda: voice_activity,
    set_activity=set_voice_activity,
    on_error=lambda _error: set_voice_status(
        "SPEECH OUTPUT UNAVAILABLE · CHECK WINDOWS VOICE ACCESS"
    ),
    preferred_speaker=speak_preferred_voice,
    is_preferred_enabled=lambda: (
        (EXPRESSIVE_VOICE_ENABLED and not OFFLINE_VOICE_ONLY and not OFFLINE_AI_ONLY)
        or offline_tts.available()
    ),
    preferred_stopper=stop_preferred_voice,
)


def speak(text, cancel_event=None):
    return tts_controller.speak(text, cancel_event=cancel_event)


def stop_speaking():
    global _active_chat_cancel
    with _chat_request_lock:
        if _active_chat_cancel is not None:
            _active_chat_cancel.set()
    tts_controller.stop()
    print("[CHAT/TTS] Stop requested.")



# =========================================================
# MICROPHONE
# =========================================================

def wake_word_detected(text):
    """Match wake-word transcripts despite punctuation or small STT spacing errors."""
    if not text:
        return False
    normalized = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # Trigger only when the wake phrase leads the transcript; discussing the
    # words later in a sentence should not wake the assistant accidentally.
    return normalized in {"wake up", "wakeup"} or normalized.startswith((
        "wake up ", "wakeup ", "hey wake up", "hey wakeup", "hey maanav wake up",
    ))


def set_voice_status(text):
    if "voice_state" in globals():
        try:
            root.after(0, lambda: voice_state.config(text=text))
        except Exception:
            pass
    if "galaxy_status" in globals():
        try:
            root.after(0, lambda: galaxy_status.config(text=text))
        except Exception:
            pass


def local_whisper_model_path():
    """Return only an already-downloaded Faster Whisper model; never fetch at startup."""
    configured = os.getenv("MAANAV_WHISPER_MODEL")
    candidates = [Path(configured)] if configured else []
    candidates.extend([
        Path(__file__).with_name("models") / "whisper-tiny-en",
        Path(__file__).with_name("models") / "whisper-small",
        Path(__file__).with_name("models") / "whisper-base",
        Path(__file__).with_name("models") / "whisper-tiny",
    ])
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "model.bin").is_file():
            return candidate
    return None


def recognize_offline(audio):
    """Transcribe with an optional local Faster Whisper model, if installed."""
    global _whisper_model, _whisper_model_checked
    if not _whisper_model_checked:
        with _whisper_lock:
            if not _whisper_model_checked:
                model_path = local_whisper_model_path()
                if model_path is not None:
                    try:
                        from faster_whisper import WhisperModel
                        _whisper_model = WhisperModel(str(model_path), device="cpu", compute_type="int8")
                        print(f"Offline speech model ready: {model_path}")
                    except Exception as error:
                        print("Offline speech model unavailable:", error)
                _whisper_model_checked = True
    if _whisper_model is None:
        return None
    import numpy as np
    pcm_samples = np.frombuffer(
        audio.get_raw_data(convert_rate=16000, convert_width=2), dtype=np.int16
    )
    samples = pcm_samples.astype(np.float32) / 32768.0
    segments, _ = _whisper_model.transcribe(samples, beam_size=1, vad_filter=True)
    return " ".join(segment.text.strip() for segment in segments).strip() or None


def _recognize_audio(audio, *, force_local=False):
    return recognize_audio(
        audio,
        offline_transcriber=recognize_offline,
        online_transcriber=voice_recognizer.recognize_google,
        offline_only=OFFLINE_VOICE_ONLY or force_local,
        local_model_ready=lambda: _whisper_model is not None,
        set_status=set_voice_status,
        request_error=sr.RequestError,
        unknown_value_error=sr.UnknownValueError,
    )


def _listen_from_microphone(timeout=5, phrase_time_limit=5, status_text="MICROPHONE READY · LISTENING",
                            force_local_recognition=False):
    global mic_calibrated, MIC_DEVICE, MIC_DEVICE_NAME

    source = None
    mic_open_failed = False
    try:
        source = sr.Microphone(device_index=MIC_DEVICE)
        source.__enter__()

        # SpeechRecognition can suppress PortAudio's device-open exception;
        # its normal context cleanup then raises a confusing None.close error.
        if source.stream is None:
            if source.audio is not None:
                source.audio.terminate()
                source.audio = None
            mic_open_failed = True
            raise RuntimeError(
                f"Could not open microphone {MIC_DEVICE}. Check Windows microphone "
                "privacy settings, that the device is connected, and that another "
                "app is not holding it exclusively."
            )

        print(f"Using microphone: {MIC_DEVICE}")
        microphone_status.ready()
        set_voice_status(status_text)

        # Calibrate only on the first successful open. Recalibrating before every
        # listen adds delay and can miss the beginning of a spoken wake word.
        if not mic_calibrated:
            voice_recognizer.adjust_for_ambient_noise(source, duration=0.5)
            mic_calibrated = True
            print(f"Microphone calibrated (threshold {voice_recognizer.energy_threshold:.0f}).")

        set_voice_activity("listening")
        audio = voice_recognizer.listen(
            source,
            timeout=timeout,
            phrase_time_limit=phrase_time_limit
        )

        print("Recognizing...")
        text = _recognize_audio(audio, force_local=force_local_recognition)
        if text:
            print("You said:", text)
        return text

    except sr.WaitTimeoutError:
        return None

    except sr.UnknownValueError:
        print("Could not understand audio.")
        return None

    except sr.RequestError as error:
        print("Speech recognition internet error:", error)
        return None

    except Exception as error:
        print("Microphone error:", error)
        microphone_status.unavailable(open_failed=mic_open_failed)
        set_voice_status(microphone_status.listening_label)
        if mic_open_failed and MIC_DEVICE is not None:
            print("Retrying with the Windows default input device.")
            MIC_DEVICE = None
            MIC_DEVICE_NAME = "Windows default microphone"
        # Allow ambient calibration to retry after the device is reconnected.
        mic_calibrated = False
        return None

    finally:
        if source is not None:
            if source.stream is not None:
                source.__exit__(None, None, None)
            elif source.audio is not None:
                source.audio.terminate()
                source.audio = None
        if voice_activity == "listening":
            set_voice_activity("idle")


def listen_from_microphone(timeout=5, phrase_time_limit=5, status_text="MICROPHONE READY · LISTENING",
                           force_local_recognition=False):
    """Enforce one active PortAudio microphone stream throughout the app."""
    if not _microphone_lock.acquire(blocking=False):
        return None
    try:
        return _listen_from_microphone(
            timeout=timeout,
            phrase_time_limit=phrase_time_limit,
            status_text=status_text,
            force_local_recognition=force_local_recognition,
        )
    finally:
        _microphone_lock.release()


# =========================================================
# AI
# =========================================================

def ask_ai(message, image_path=None, session_id=None):
    message_lower = str(message or "").lower().strip()
    theme_match = re.search(
        r"(?:(?:change|set|switch)(?: the)? (?:app )?(?:theme|background)(?: to)?|make (?:the )?background) (gold|yellow|black|blue|purple|violet|teal|green|rose|pink|default)",
        message_lower,
    )
    if theme_match:
        chosen = "gold" if theme_match.group(1) in {"gold", "yellow", "black", "default"} else theme_match.group(1)
        if "root" in globals() and root.winfo_exists():
            root.after(0, lambda: apply_theme_preset(chosen, persist=True))
        return f"I changed Maanav's theme to {chosen}."

    style_match = re.fullmatch(
        r"(?:set|change) (?:(?:your|maanav(?: vyom)?(?:'s)?) )?(?:(?:reply|response) )?(?:style|tone) to (natural|concise|detailed|creative)",
        message_lower,
    )
    if style_match:
        global MAANAV_RESPONSE_STYLE
        MAANAV_RESPONSE_STYLE = style_match.group(1)
        if not save_setting("response_style", MAANAV_RESPONSE_STYLE):
            return "I couldn't save the reply-style setting. Check the project folder permissions."
        return f"Reply style set to {MAANAV_RESPONSE_STYLE}."

    speech_match = re.fullmatch(
        r"(?:turn|switch) (?:spoken )?(?:answers|replies|responses|speech) (on|off)|(?:enable|disable) spoken (?:answers|replies|responses)",
        message_lower,
    )
    if speech_match:
        global SPEAK_RESPONSES_ENABLED
        enabled = (speech_match.group(1) == "on") if speech_match.group(1) else message_lower.startswith("enable ")
        SPEAK_RESPONSES_ENABLED = enabled
        save_setting("speak_responses", enabled)
        return "I will speak my replies." if enabled else "Spoken replies are off. I will keep replying in text."

    listening_match = re.fullmatch(
        r"(?:turn|switch) wake[- ]?word listening (on|off)|(?:enable|disable) wake[- ]?word listening",
        message_lower,
    )
    if listening_match:
        global wake_word_enabled
        listening = (listening_match.group(1) == "on") if listening_match.group(1) else message_lower.startswith("enable ")
        save_setting("wake_listening", listening)
        wake_word_enabled = listening
        return "Wake-word listening is on." if listening else "Wake-word listening is off. Use the direct keyboard or mouse shortcut to speak to me."

    if re.fullmatch(r"(?:open|show) (?:maanav )?(?:settings|personalization)", message_lower):
        if "root" in globals() and root.winfo_exists():
            root.after(0, open_personalization_dialog)
            return "Opening Maanav's settings."
        return "Open the Settings button in Maanav to change appearance and voice options."

    content_query = re.search(
        r"(?:search|find) (?:inside |within )?(?:my )?(?:files|file contents|file content|documents)(?: for| containing| with)? (.+)",
        message_lower,
    )
    if content_query:
        success, results = search_file_contents(
            content_query.group(1).strip(), extra_roots=FILE_SEARCH_ROOTS,
        )
        if not success:
            return results
        return "I found these matches in your files:\n" + "\n".join(
            f"• {Path(path).name}, line {line}: {snippet} ({path})"
            for path, line, snippet in results
        )

    routine = match_routine(message)
    if routine:
        print(f"[ROUTINE] {routine['name']} triggered by: {message}")
        _ok, result = execute_routine(routine)
        return result

    # Maanav AI intent router
    message_lower = message.lower().strip()
    intent = classify(message)
    print(f"[ROUTER] {message} -> {intent}")

    if intent == "time":
        now = datetime.now().astimezone()
        if any(word in message_lower for word in ("date", "day", "today")):
            date_text = now.strftime("%A, %B %d").replace(", 0", ", ")
            return "It's " + date_text + "."
        return "It's " + now.strftime("%I:%M %p").lstrip("0") + "."

    folder_like_command = re.fullmatch(
        r"(?:open|launch|start|run)\s+(?:the\s+)?(?:downloads?|documents?|desktop|pictures?|music|videos?)(?:\s+folder)?",
        message_lower,
    )
    if intent == "app" and not folder_like_command:
        app_name = message

        for prefix in ["open ", "launch ", "start ", "run "]:
            if app_name.lower().startswith(prefix):
                app_name = app_name[len(prefix):].strip()
                break

        success, result = universal_launch_app(app_name)
        if success:
            return result
        print(f"[APP LAUNCH] {result}")
        return result

    message_lower = message.lower().strip()

    # =====================================================
    # FILE CONTROL
    # =====================================================

    # Open common Windows folders
    folder_commands = {
        "open desktop": "desktop",
        "open the desktop": "desktop",

        "open downloads": "downloads",
        "open download": "downloads",
        "open the downloads": "downloads",
        "open the download": "downloads",
        "open downloads folder": "downloads",
        "open the downloads folder": "downloads",

        "open documents": "documents",
        "open document": "documents",
        "open documents folder": "documents",
        "open the documents": "documents",
        "open the documents folder": "documents",

        "open pictures": "pictures",
        "open picture": "pictures",
        "open the pictures": "pictures",
        "open pictures folder": "pictures",
        "open the pictures folder": "pictures",

        "open music": "music",
        "open the music": "music",

        "open videos": "videos",
        "open video": "videos",
        "open the videos": "videos",
        "open videos folder": "videos",
        "open the videos folder": "videos",
    }

    if message_lower in folder_commands:
        success, result = open_folder(folder_commands[message_lower])
        return result

    # Search for a file
    if message_lower.startswith("find "):
        query = message_lower[5:].strip()

        success, results = search_files(query, extra_roots=FILE_SEARCH_ROOTS)

        if not success:
            return results

        if len(results) == 1:
            return f"I found it: {results[0]}"

        response = f"I found {len(results)} files:\n"

        for path in results[:10]:
            response += f"\n• {path}"

        if len(results) > 10:
            response += f"\n\nAnd {len(results) - 10} more."

        return response

    # Open a file by name
    if message_lower.startswith("open file "):
        filename = message_lower[10:].strip()

        success, results = search_files(filename, max_results=5, extra_roots=FILE_SEARCH_ROOTS)

        if not success:
            return results

        if len(results) == 1:
            success, result = open_file(results[0])
            return result

        response = f"I found {len(results)} matching files:\n"

        for path in results:
            response += f"\n• {path}"

        response += "\n\nPlease give me the exact file name."

        return response

    create_match = re.fullmatch(
        r"create (?:a )?(?:new )?(?:text )?file(?: named)? (.+?)(?: (?:in|inside) (downloads?|documents?|desktop|pictures?|music|videos?))?",
        message.strip(), re.IGNORECASE,
    )
    if create_match:
        filename = create_match.group(1).strip().strip('"')
        folder_name = (create_match.group(2) or "documents").rstrip("s")
        if folder_name == "download":
            folder_name = "downloads"
        elif folder_name == "document":
            folder_name = "documents"
        elif folder_name == "picture":
            folder_name = "pictures"
        elif folder_name == "video":
            folder_name = "videos"
        return create_file(filename, folder_name)[1]

    # =====================================================
    # SYSTEM ACTION CONFIRMATION
    # =====================================================

    confirmation_status, confirmed_action = pending_system_action.resolve(
        message_lower, session_id=session_id
    )
    if confirmation_status == "confirmed":
        if confirmed_action == "shutdown":
            return shutdown_pc()[1]
        if confirmed_action == "restart":
            return restart_pc()[1]
        if confirmed_action == "wifi_off":
            return set_wifi_enabled(False)[1]
    if confirmation_status == "cancelled":
        return "Okay, cancelled."
    if confirmation_status == "expired":
        return "That confirmation expired. Please repeat the request if you still want me to do it."
    if confirmation_status == "wrong_session":
        return "That confirmation belongs to a different conversation, so I cancelled it. Repeat the request here if needed."
    if confirmation_status == "unclear":
        return "I cancelled the action because I didn't get a clear confirmation."

    if message_lower in ["cancel shutdown", "cancel restart", "abort shutdown", "abort restart"]:
        success, result = cancel_shutdown()
        return result

    if message_lower in {"turn wifi off", "turn wi-fi off", "disable wifi", "disable wi-fi", "turn off wifi", "turn off wi-fi"}:
        pending_system_action.request("wifi_off", session_id=session_id)
        return "Turning Wi-Fi off disconnects internet. Should I continue?"
    if message_lower in {"turn wifi on", "turn wi-fi on", "enable wifi", "enable wi-fi", "turn on wifi", "turn on wi-fi"}:
        return set_wifi_enabled(True)[1]

    # =====================================================
    # SHUTDOWN / RESTART REQUEST
    # =====================================================

    if message_lower in [
        "shutdown",
        "shut down",
        "turn off the laptop",
        "turn off laptop",
        "power off"
    ]:

        pending_system_action.request("shutdown", session_id=session_id)
        return "Are you sure you want me to shut down the laptop?"

    if message_lower in [
        "restart",
        "restart the laptop",
        "reboot",
        "reboot the laptop"
    ]:

        pending_system_action.request("restart", session_id=session_id)
        return "Are you sure you want me to restart the laptop?"

    # =====================================================
    # SYSTEM CONTROL
    # =====================================================

    if message_lower in ["lock laptop", "lock my laptop", "lock computer", "lock my pc"]:
        success, result = lock_pc()
        return result

    if message_lower in [
        "volume up",
        "increase volume",
        "turn up volume",
        "increase the volume",
    ]:
        success, result = volume_up()
        return result

    if message_lower in [
        "volume down",
        "decrease volume",
        "turn down volume",
        "decrease the volume",
    ]:
        success, result = volume_down()
        return result

    if message_lower in [
        "mute",
        "mute laptop",
        "mute the laptop",
        "mute my laptop",
    ]:
        success, result = mute()
        return result

    if message_lower in [
        "unmute",
        "unmute laptop",
        "unmute the laptop",
        "unmute my laptop",
    ]:
        success, result = unmute()
        return result

    if message_lower in [
        "battery",
        "battery status",
        "check battery",
        "what is my battery",
        "what's my battery",
    ]:
        success, result = get_battery()
        return result
 
    # =====================================================
    # WEBSITE CONTROL
    # =====================================================

    website_commands = {
        "youtube": "youtube",
        "open youtube": "youtube",
        "google": "google",
        "open google": "google",
        "gmail": "gmail",
        "open gmail": "gmail",
    }

    if message_lower in website_commands:
        website = website_commands[message_lower]

        success, result = open_website(website)

        return result

    # =====================================================
    # REMEMBER SOMETHING
    # =====================================================

    explicit_memory_text = re.sub(r"^remember(?: that)?\s+", "", message, flags=re.IGNORECASE)
    explicit_fact = memory.classify_memory_candidate(explicit_memory_text)
    if explicit_fact:
        key, value = explicit_fact
        if save_memory(key, value):
            return f"Got it. I'll remember {key}: {value}."
        return "I couldn't save that memory. Check that the project folder is writable."

    remember_match = re.match(
        r"remember(?: that)? (.+?) (?:is|are|=) (.+)",
        message,
        re.IGNORECASE
    )

    if remember_match:

        key = remember_match.group(1).strip()
        value = remember_match.group(2).strip()

        if save_memory(key, value):
            return f"Got it! I'll remember that {key} is {value}."
        return "I couldn't save that memory. Check that the project folder is writable."

    if message_lower.startswith("remember "):
        note = re.sub(r"^remember(?: that)?\s*", "", message, flags=re.IGNORECASE).strip()
        note = re.sub(r"^this\s*[:,\-]\s*", "", note, flags=re.IGNORECASE).strip()
        if not note or note.lower() in {"this", "that"}:
            return "What would you like me to remember?"
        note_key = memory.remember_note(note)
        if note_key:
            return f"Got it. I'll remember that note: {note}. You can say 'forget {note_key}' to remove it."
        return "I couldn't save that memory. Check that the project folder is writable."

    # =====================================================
    # FORGET SOMETHING
    # =====================================================

    if message_lower in {"forget everything", "forget everything you remember", "clear all memory"}:
        try:
            memory.forget_everything()
            return "Okay, I've cleared everything I had saved in permanent memory."
        except OSError as error:
            return f"I couldn't clear memory: {error}"

    forget_match = re.match(
        r"forget(?: that)? (.+)",
        message,
        re.IGNORECASE
    )

    if forget_match:

        key = forget_match.group(1).strip().lower()
        key = re.sub(r"^my ", "", key)
        key = "favorite " + key[len("favorite "):] if key.startswith("favorite ") else key
        if key in {"name", "my name"}:
            key = "name"

        memory_data = load_memory()
        matching_key = next((saved for saved in memory_data if saved.lower() == key), None)
        if matching_key and memory.forget(matching_key):
            return f"Okay, I've forgotten {matching_key}."

        return f"I don't have anything stored for {key}."

    # =====================================================
    # SHOW MEMORY
    # =====================================================

    if (
        "what do you remember" in message_lower
        or "what do you know about me" in message_lower
        or "show my memory" in message_lower
    ):

        memory_data = load_memory()

        if not memory_data:
            return "I don't have anything saved in my memory yet."

        memory_lines = []

        for key, value in memory_data.items():
            memory_lines.append(f"{key}: {value}")

        return "Here's what I remember about you:\n" + "\n".join(memory_lines)

    # Save only well-defined, durable facts. Other conversation stays temporary.
    candidate = memory.classify_memory_candidate(message)
    if candidate:
        save_memory(*candidate)

    # =====================================================
    # LOAD MEMORY FOR AI
    # =====================================================

    memory_data = load_memory()

    memory_text = ""

    if memory_data:

        memory_text = (
            "\n\nUSER MEMORY:\n"
            + json.dumps(
                memory_data,
                indent=2,
                ensure_ascii=False
            )
        )

    # =====================================================
    # ASK GEMINI
    # =====================================================

    history = load_history(limit=12, session_id=session_id)
    prompt = build_gemini_prompt(
        message,
        memory_text=memory_text,
        history=history,
        image_attached=bool(image_path),
        response_style=MAANAV_RESPONSE_STYLE,
    )

    def ask_local_ai(user_message, *, history, memory_text):
        print("Cloud AI unavailable. Switching to Ollama...")
        local_model = MODEL
        installed = []
        try:
            installed = [item.model for item in OLLAMA_CLIENT.list().models]
            local_model = choose_installed_model(MODEL, installed)
            if local_model != MODEL:
                print(f"Configured model {MODEL!r} is not installed; using {local_model!r}.")
        except Exception as error:
            print("Could not list local models; trying the configured model:", error)
        messages = build_local_messages(
            user_message,
            memory_text=memory_text,
            history=history,
            response_style=MAANAV_RESPONSE_STYLE,
        )
        options = {
            # Keep memory use comfortable on the 4 GB GPU / 16 GB RAM system.
            "num_ctx": 8192,
            "num_predict": 512,
            "temperature": 0.55,
            "repeat_penalty": 1.12,
            "repeat_last_n": 128,
        }
        try:
            response = OLLAMA_CLIENT.chat(model=local_model, messages=messages, options=options, think=False)
        except Exception as error:
            fallback = choose_installed_model("qwen3:4b-instruct", installed)
            if local_model == MODEL and fallback != MODEL and fallback in installed:
                print(f"{MODEL} failed to load; retrying offline with {fallback}: {error}")
                options["num_ctx"] = 4096
                response = OLLAMA_CLIENT.chat(model=fallback, messages=messages, options=options, think=False)
            else:
                raise
        return response["message"]["content"]

    if OFFLINE_AI_ONLY:
        print("Offline AI mode enabled. Skipping OpenAI and Gemini.")
    return route_message(
        message,
        image_path=image_path,
        history=history,
        memory_text=memory_text,
        offline=OFFLINE_AI_ONLY,
        openai_provider=ask_openai,
        gemini_provider=lambda _user_message, image_path=None: ask_gemini(
            prompt, image_path=image_path
        ),
        local_provider=ask_local_ai,
        local_model=MODEL,
    )


def run_assistant_request(message, image_path=None, session_id=None):
    """Serialize chat, voice, and mini-pet requests through the same Maanav brain."""
    with _assistant_request_lock:
        return ask_ai(message, image_path=image_path, session_id=session_id)

# =========================================================
# GUI ANSWER
# =========================================================

def show_answer(answer, speak_answer=True, cancel_event=None):

    chat.config(state="normal")

    pending = chat.search("Thinking...", "1.0", tk.END)
    if pending:
        chat.delete(pending, f"{pending}+11c")

    chat.insert(tk.END, "MAANAV  ", "assistant_label")
    chat.insert(tk.END, answer + "\n\n", "assistant_message")

    chat.config(state="disabled")
    chat.see(tk.END)

    # Speak without blocking the GUI thread 
    if speak_answer:
        threading.Thread(
            target=_speak_chat_answer,
            args=(answer, cancel_event),
            daemon=True
        ).start()
    entry.config(state="normal")
    send_button.config(state="normal")
    entry.focus()


def _speak_chat_answer(answer, cancel_event=None):
    """Keep Stop Speaking connected until queued TTS has actually finished."""
    global _active_chat_cancel
    try:
        speak(answer, cancel_event=cancel_event)
    finally:
        if _finish_chat_request(cancel_event):
            set_wispr_dictation_active(False)


def show_error(error):

    chat.config(state="normal")

    pending = chat.search("Thinking...", "1.0", tk.END)
    if pending:
        chat.delete(pending, f"{pending}+11c")

    chat.insert(
        tk.END,
        "Maanav AI: Something went wrong.\n\n"
        + str(error)
        + "\n\n"
    )

    chat.config(state="disabled")
    chat.see(tk.END)

    entry.config(state="normal")
    send_button.config(state="normal")
    entry.focus() 	


# =========================================================
# PROCESS MESSAGE
# =========================================================

def process_message(message, *, pet_reply=False):

    if not message:
        return
    session_id = get_active_session_id()

    print("Processing:", message)
    set_voice_activity("thinking")

    try:

        # Show user message
        root.after(
            0,
            add_user_message,
            message
        )

        # Get AI answer
        answer = run_assistant_request(message, session_id=session_id)
        add_turn(message, answer, session_id=session_id)
        root.after(0, refresh_sidebar_history)

        print("ANSWER RECEIVED:", answer)

        if pet_reply and "desktop_companion" in globals():
            root.after(0, desktop_companion.show_text_turn, message, answer)

        # Show answer in GUI WITHOUT speaking
        root.after(
            0,
            show_answer,
            answer,
            False
        )

        if not pet_reply:
            # Ordinary voice commands still use Maanav's speech output.
            speak(answer)
            set_voice_status("READY · SAY WAKE UP")
        else:
            set_voice_activity("idle")

    except Exception as error:

        print("AI error:", error)

        root.after(
            0,
            show_error,
            str(error)
        )
        if pet_reply and "desktop_companion" in globals():
            root.after(0, desktop_companion.show_voice_error, "I couldn't complete that. " + str(error))
        set_voice_activity("idle")


def add_user_message(message):

    chat.config(state="normal")

    chat.insert(tk.END, "YOU  ", "user_label")
    chat.insert(tk.END, message + "\n\n", "user_message")
    chat.insert(tk.END, "MAANAV  ", "assistant_label")
    chat.insert(tk.END, "Thinking...\n\n", "thinking")

    chat.config(state="disabled")
    chat.see(tk.END)


# =========================================================
# CONTINUOUS VOICE ASSISTANT
# =========================================================

def _voice_assistant_loop_impl():

    global voice_loop_running

    print()
    print("===================================")
    print("       MAANAV AI VOICE SYSTEM")
    print("===================================")
    print()
    print("Wake word:", WAKE_WORD)
    print("Microphone:", MIC_DEVICE)
    print()

    while voice_loop_running:
        if not voice_enabled:
            time.sleep(0.25)
            continue

        # Wispr Flow owns the microphone while the user dictates into Maanav's
        # focused chat field. Keep Maanav's wake listener off that same device.
        if _wispr_dictation_active.is_set() or _voice_input_paused.is_set():
            time.sleep(0.15)
            continue

        if voice_call_active:
            try:
                command = listen_from_microphone(
                    timeout=5, phrase_time_limit=10,
                    status_text="CALL MODE · LISTENING",
                )
            except Exception as error:
                print("Call-mode listener recovered from error:", error)
                time.sleep(1)
                continue
            if command:
                process_message(command)
                # process_message speaks the answer before the next mic capture.
                time.sleep(0.25)
            continue

        # Ctrl+Alt+Space starts one command turn without requiring the spoken wake
        # phrase. Online dictation is handled by Wispr; offline stays local.
        if _manual_voice_trigger.is_set():
            _manual_voice_trigger.clear()
            try:
                command = listen_from_microphone(
                    timeout=10, phrase_time_limit=12,
                    status_text="SHORTCUT · LISTENING TO MAANAV VYOM",
                    force_local_recognition=OFFLINE_VOICE_ONLY,
                )
            except Exception as error:
                print("Shortcut command listener recovered from error:", error)
                set_voice_status("MIC · CHECK DEVICE / PERMISSIONS")
                time.sleep(1)
                continue
            if command:
                process_message(command)
            else:
                set_voice_status("NO SPEECH HEARD · PRESS SHORTCUT TO RETRY")
            continue

        if not wake_word_enabled:
            time.sleep(0.25)
            continue

        # -------------------------------------------------
        # WAIT FOR WAKE WORD
        # -------------------------------------------------

        print("Waiting for wake word:", WAKE_WORD)
        set_voice_status(microphone_status.listening_label)

        try:
            wake_text = listen_from_microphone(
                timeout=2, phrase_time_limit=4,
                status_text="LISTENING · SAY WAKE UP",
                force_local_recognition=True,
            )
        except Exception as error:
            print("Wake listener recovered from error:", error)
            set_voice_status("MIC · CHECK DEVICE / PERMISSIONS")
            time.sleep(2)
            continue

        if _manual_voice_trigger.is_set():
            # If Ctrl+Alt+Space was pressed during the short wake listen, reuse
            # that captured audio as the command instead of discarding it.
            _manual_voice_trigger.clear()
            command = wake_text
            if not command:
                command = listen_from_microphone(
                    timeout=10, phrase_time_limit=12,
                    status_text="HOTKEY · LISTENING FOR COMMAND",
                    force_local_recognition=OFFLINE_VOICE_ONLY,
                )
            if command:
                process_message(command)
            else:
                set_voice_status("NO SPEECH HEARD · PRESS SHORTCUT TO RETRY")
            continue

        if not wake_text:
            # Avoid spinning at full CPU if the microphone is unavailable.
            time.sleep(1)
            continue

        print("Heard:", wake_text)
        set_voice_status("HEARD · " + wake_text[:28].upper())

        if wake_word_detected(wake_text):

            print("Wake word detected!")
            set_voice_status("WAKE WORD · LISTENING FOR COMMAND")

            # -------------------------------------------------
            # ACKNOWLEDGE WAKE WORD
            # -------------------------------------------------

            speak("Yes, I'm listening.")

            # Keep acoustic echo from the acknowledgement out of command capture.
            time.sleep(0.25)

            # Online dictation is handled by Wispr Flow. Its Windows app inserts
            # the completed transcript into the focused composer, so Maanav must
            # release its microphone and wait for the user to submit that text.
            # Wake-word detection remains local, and offline mode keeps the
            # direct local Whisper command path below.
            if not OFFLINE_VOICE_ONLY:
                set_wispr_dictation_active(True)
                root.after(0, entry.focus_set)
                print("Online command input handed to Wispr Flow; send the dictated text to continue.")
                continue

            # -------------------------------------------------
            # LISTEN FOR COMMAND
            # -------------------------------------------------

            print("Listening for your command...")

            command = listen_from_microphone(
                timeout=10, phrase_time_limit=10,
                status_text="LISTENING FOR COMMAND",
            )

            if not command:
                print("No command heard.")
                continue

            # -------------------------------------------------
            # PROCESS COMMAND
            # -------------------------------------------------

            print("Command:", command)

            # Do AI work in this same voice thread.
            # After it finishes, the loop automatically
            # returns to "Waiting for wake word".
            process_message(command)

            # Wait until AI response is generated before
            # opening microphone again.
            time.sleep(1)

            print()
            print("Returning to wake-word mode...")
            print()


def voice_assistant_loop():
    """Only one long-running wake-word loop may own the mic at a time."""
    if not _voice_loop_guard.acquire(blocking=False):
        print("Voice listener already running; duplicate listener skipped.")
        return
    try:
        _voice_assistant_loop_impl()
    finally:
        _voice_loop_guard.release()


# =========================================================
# IMAGE ATTACHMENTS AND MEMORY
# =========================================================

selected_image = None


def update_attachment_bar():
    for widget in attachment_bar.winfo_children():
        widget.destroy()
    if selected_image:
        tk.Label(
            attachment_bar,
            text="IMAGE  " + Path(selected_image).name,
            bg=CARD, fg=CYAN, font=("Segoe UI", 9), padx=12, pady=5
        ).pack(side=tk.LEFT)
        tk.Button(
            attachment_bar, text="Remove", command=remove_image,
            bg=CARD, fg=MUTED, activebackground=CARD, activeforeground=TEXT,
            relief=tk.FLAT, borderwidth=0, cursor="hand2"
        ).pack(side=tk.LEFT)


def list_input_devices():
    return physical_input_devices()


def short_microphone_label(name):
    lowered = name.lower()
    if "microphone array" in lowered:
        return "BUILT-IN"
    if "audiocular" in lowered:
        return "AUDIOCULAR"
    return name[:14].upper()


def choose_microphone():
    global MIC_DEVICE, MIC_DEVICE_NAME, mic_calibrated
    devices = list_input_devices()
    if not devices:
        messagebox.showerror("Microphone", "No input microphones were found.")
        return

    dialog = tk.Toplevel(root)
    dialog.title("Choose microphone")
    dialog.configure(bg=PANEL)
    dialog.transient(root)
    dialog.grab_set()
    tk.Label(
        dialog, text="Choose the microphone you speak into", bg=PANEL,
        fg=TEXT, font=("Segoe UI", 11, "bold"), padx=18, pady=14
    ).pack(anchor="w")
    choices = tk.Listbox(
        dialog, bg=CARD, fg=TEXT, selectbackground=ACCENT_DARK,
        selectforeground="white", relief=tk.FLAT, borderwidth=0,
        height=min(8, len(devices)), width=52, exportselection=False,
        font=("Segoe UI", 10)
    )
    for index, name in devices:
        choices.insert(tk.END, f"{index}  ·  {name}")
        if index == MIC_DEVICE:
            choices.selection_set(tk.END)
            choices.see(tk.END)
    choices.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 12))

    def apply_microphone():
        global MIC_DEVICE, MIC_DEVICE_NAME, mic_calibrated
        selected = choices.curselection()
        if not selected:
            return
        MIC_DEVICE, MIC_DEVICE_NAME = devices[selected[0]]
        mic_calibrated = False
        save_setting("microphone_device", MIC_DEVICE)
        save_setting("microphone_name", MIC_DEVICE_NAME)
        mic_button.config(text=f"MIC · {short_microphone_label(MIC_DEVICE_NAME)}")
        set_voice_status("MIC CHANGED · LISTENING")
        dialog.destroy()

    tk.Button(
        dialog, text="USE THIS MICROPHONE", command=apply_microphone,
        bg=ACCENT_DARK, fg="white", activebackground=ACCENT,
        activeforeground="white", relief=tk.FLAT, borderwidth=0,
        cursor="hand2", padx=14, pady=9
    ).pack(anchor="e", padx=18, pady=(0, 16))


def remove_image():
    global selected_image
    selected_image = None
    update_attachment_bar()


def choose_image():
    global selected_image
    path = filedialog.askopenfilename(
        title="Choose an image",
        filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"), ("All files", "*.*")],
    )
    if not path:
        return
    if Path(path).stat().st_size > 12 * 1024 * 1024:
        messagebox.showerror("Image too large", "Choose an image smaller than 12 MB.")
        return
    selected_image = path
    update_attachment_bar()


def show_saved_memory():
    facts = memory.get_all_memory()
    history = load_history(limit=10)
    fact_text = "\n".join(f"- {key}: {value}" for key, value in facts.items()) or "No saved facts yet."
    history_text = f"\n\nRecent conversation turns available to Maanav: {len(history) // 2}"
    if messagebox.askyesno(
        "Maanav memory",
        fact_text + history_text + "\n\nClear saved chat history?"
    ):
        clear_history()
        refresh_sidebar_history()
        open_chat_session(get_active_session_id())
        messagebox.showinfo("Maanav memory", "This chat was cleared. Your saved facts remain.")


def open_routines_dialog():
    dialog = tk.Toplevel(root)
    dialog.title("Maanav routines")
    dialog.geometry("820x680")
    dialog.minsize(760, 620)
    dialog.configure(bg=PANEL)
    dialog.transient(root)

    tk.Label(
        dialog, text="VOICE ROUTINES", bg=PANEL, fg=ACCENT,
        font=("Segoe UI", 16, "bold")
    ).pack(anchor="w", padx=22, pady=(18, 2))
    tk.Label(
        dialog, text="Assign a phrase to actions Maanav should run together.",
        bg=PANEL, fg=MUTED, font=("Segoe UI", 10)
    ).pack(anchor="w", padx=22, pady=(0, 14))

    body = tk.Frame(dialog, bg=PANEL)
    body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 16))
    list_panel = tk.Frame(body, bg=SIDEBAR, width=240)
    list_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 16))
    list_panel.pack_propagate(False)
    tk.Label(list_panel, text="SAVED ROUTINES", bg=SIDEBAR, fg=MUTED,
             font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=13, pady=(12, 8))
    routine_list = tk.Listbox(
        list_panel, bg=SIDEBAR, fg=TEXT, selectbackground=ACCENT_DARK,
        selectforeground="white", relief=tk.FLAT, borderwidth=0,
        activestyle="none", font=("Segoe UI", 10),
        highlightthickness=0, exportselection=False
    )
    routine_list.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 8))

    editor = tk.Frame(body, bg=PANEL)
    editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    name_var = tk.StringVar()
    phrase_var = tk.StringVar()
    phrases_var = tk.StringVar()
    apps_var = tk.StringVar()
    close_var = tk.BooleanVar(value=False)
    close_apps_var = tk.StringVar()
    power_mode_labels = {
        "current": "Keep current",
        "performance": "Best performance",
        "balanced": "Balanced",
        "efficiency": "Power saver",
    }
    power_mode_codes = {label: code for code, label in power_mode_labels.items()}
    power_mode_var = tk.StringVar(value=power_mode_labels["current"])
    form_routines = load_routines()
    editing_id = {"value": None}

    def field(label, variable, hint=None):
        tk.Label(editor, text=label, bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(5, 4))
        box = tk.Entry(
            editor, textvariable=variable, bg=CARD, fg=TEXT,
            insertbackground=TEXT, relief=tk.FLAT, borderwidth=0,
            font=("Segoe UI", 10)
        )
        box.pack(fill=tk.X, ipady=8)
        if hint:
            tk.Label(editor, text=hint, bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))
        return box

    field("Routine name", name_var)
    field("Spoken phrase", phrase_var, 'Example: "open valo"')
    field("Also say (optional)", phrases_var, 'Comma-separated. Example: "lets code, I wanna code"')
    field("Apps to open", apps_var, "Comma-separated. Example: VS Code, ChatGPT, Windows Terminal")

    tk.Label(editor, text="ACTIONS", bg=PANEL, fg=ACCENT,
             font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(18, 7))
    tk.Checkbutton(
        editor, text="Close browser windows / tabs (may prompt about unsaved pages)", variable=close_var,
        bg=PANEL, fg=TEXT_DIM, activebackground=PANEL, activeforeground=TEXT,
        selectcolor=CARD, font=("Segoe UI", 9), anchor="w"
    ).pack(fill=tk.X, pady=3)
    tk.Label(editor, text="WINDOWS POWER MODE", bg=PANEL, fg=TEXT_DIM,
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(10, 4))
    power_menu = tk.OptionMenu(editor, power_mode_var, *power_mode_labels.values())
    power_menu.config(
        bg=CARD, fg=TEXT, activebackground=SIDEBAR_HOVER, activeforeground=ACCENT,
        relief=tk.FLAT, borderwidth=0, highlightthickness=0, anchor="w",
        font=("Segoe UI", 9), padx=10, pady=6
    )
    power_menu["menu"].config(bg=CARD, fg=TEXT, activebackground=ACCENT_DARK,
                              activeforeground="white", relief=tk.FLAT)
    power_menu.pack(fill=tk.X)
    tk.Label(
        editor,
        text="Best performance uses a high-performance plan; Power saver limits CPU boost and maximum speed.",
        bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=500, justify="left"
    ).pack(anchor="w", pady=(4, 1))
    field("Other apps to close (optional)", close_apps_var,
          "Comma-separated process names, e.g. Discord, Spotify. Windows may ask to save work.")

    footer = tk.Frame(editor, bg=PANEL)
    footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(16, 0))
    message_label = tk.Label(editor, text="", bg=PANEL, fg=MUTED,
                             font=("Segoe UI", 8), wraplength=500, justify="left")
    message_label.pack(side=tk.BOTTOM, anchor="w", pady=(7, 0))

    def refresh_list(select_id=None):
        routine_list.delete(0, tk.END)
        selected_index = None
        for index, item in enumerate(form_routines):
            routine_list.insert(tk.END, item["name"])
            if item.get("id") == select_id:
                selected_index = index
        if selected_index is not None:
            routine_list.selection_set(selected_index)
            routine_list.activate(selected_index)

    def load_selected(_event=None):
        selected = routine_list.curselection()
        if not selected:
            return
        item = form_routines[selected[0]]
        editing_id["value"] = item["id"]
        name_var.set(item["name"])
        phrase_var.set(item["phrase"])
        phrases_var.set(", ".join(item.get("phrases", [item["phrase"]])[1:]))
        apps_var.set(", ".join(item.get("apps") or [item.get("app", "")]))
        close_var.set(item["close_browsers"])
        close_apps_var.set(", ".join(item.get("close_apps", [])))
        power_mode_var.set(power_mode_labels.get(item.get("power_mode", "current"), power_mode_labels["current"]))
        message_label.config(text="")

    def clear_form():
        routine_list.selection_clear(0, tk.END)
        editing_id["value"] = None
        name_var.set("")
        phrase_var.set("")
        phrases_var.set("")
        apps_var.set("")
        close_var.set(False)
        close_apps_var.set("")
        power_mode_var.set(power_mode_labels["current"])
        message_label.config(text="New routine · list apps separated by commas, then choose optional actions.")

    def save_form():
        name, phrase = name_var.get().strip(), phrase_var.get().strip()
        apps = [part.strip() for part in apps_var.get().split(",") if part.strip()]
        if not name or not phrase or not apps:
            messagebox.showerror("Routine details", "Enter a name, spoken phrase, and at least one app.", parent=dialog)
            return
        normalized_phrase = " ".join(phrase.lower().split())
        duplicate = next((
            old for old in form_routines
            if old.get("id") != editing_id["value"]
            and " ".join(old.get("phrase", "").lower().split()) == normalized_phrase
        ), None)
        if duplicate:
            messagebox.showerror("Routine phrase", "Another routine already uses that phrase.", parent=dialog)
            return
        item = {
            "id": editing_id["value"] or uuid.uuid4().hex[:12],
            "name": name, "phrase": phrase,
            "phrases": [phrase] + [part.strip() for part in phrases_var.get().split(",") if part.strip()],
            "apps": apps,
            "close_browsers": close_var.get(),
            "close_apps": close_apps_var.get(),
            "power_mode": power_mode_codes.get(power_mode_var.get(), "current"),
        }
        index = next((i for i, old in enumerate(form_routines) if old.get("id") == item["id"]), None)
        if index is None:
            form_routines.append(item)
        else:
            form_routines[index] = item
        try:
            form_routines[:] = save_routines(form_routines)
        except OSError as error:
            messagebox.showerror("Save failed", str(error), parent=dialog)
            return
        editing_id["value"] = item["id"]
        refresh_list(item["id"])
        message_label.config(text="Routine saved. Say: " + phrase)

    def delete_selected():
        selected = routine_list.curselection()
        if not selected:
            return
        del form_routines[selected[0]]
        try:
            form_routines[:] = save_routines(form_routines)
        except OSError as error:
            messagebox.showerror("Delete failed", str(error), parent=dialog)
            return
        clear_form()
        refresh_list()

    def run_current():
        selected = routine_list.curselection()
        item = form_routines[selected[0]] if selected else {
            "name": name_var.get().strip() or phrase_var.get().strip(),
            "phrase": phrase_var.get().strip(), "apps": [part.strip() for part in apps_var.get().split(",") if part.strip()],
            "close_browsers": close_var.get(),
            "close_apps": close_apps_var.get(),
            "power_mode": power_mode_codes.get(power_mode_var.get(), "current"),
        }
        if not (item.get("apps") or item.get("app")):
            messagebox.showerror("Routine details", "Choose or save a routine first.", parent=dialog)
            return
        message_label.config(text="Running routine…")
        def worker():
            ok, result = execute_routine(item)
            def show_result():
                try:
                    message_label.config(
                        text=("Done · " if ok else "Finished with issues · ") + result.replace("\n", "  ·  ")
                    )
                except tk.TclError:
                    pass
            root.after(0, show_result)
        threading.Thread(target=worker, daemon=True).start()

    routine_list.bind("<<ListboxSelect>>", load_selected)
    tk.Button(footer, text="New", command=clear_form, bg=SIDEBAR_HOVER, fg=TEXT,
              activebackground="#2b2617", activeforeground=ACCENT, relief=tk.FLAT,
              borderwidth=0, cursor="hand2", padx=12, pady=8).pack(side=tk.LEFT)
    tk.Button(footer, text="Delete", command=delete_selected, bg=SIDEBAR, fg=MUTED,
              activebackground=SIDEBAR_HOVER, activeforeground=TEXT, relief=tk.FLAT,
              borderwidth=0, cursor="hand2", padx=12, pady=8).pack(side=tk.LEFT, padx=6)
    tk.Button(footer, text="Run now", command=run_current, bg=SIDEBAR_HOVER, fg=TEXT,
              activebackground="#2b2617", activeforeground=ACCENT, relief=tk.FLAT,
              borderwidth=0, cursor="hand2", padx=12, pady=8).pack(side=tk.RIGHT)
    tk.Button(footer, text="Save routine", command=save_form, bg=ACCENT_DARK, fg="white",
              activebackground=ACCENT, activeforeground="black", relief=tk.FLAT,
              borderwidth=0, cursor="hand2", padx=14, pady=8).pack(side=tk.RIGHT, padx=8)

    refresh_list()
    if form_routines:
        routine_list.selection_set(0)
        load_selected()


# =========================================================
# TEXT CHAT
# =========================================================

def _begin_chat_request():
    """Give the newest UI request ownership of Stop Speaking/cancellation."""
    global _active_chat_cancel
    cancel_event = threading.Event()
    _voice_input_paused.set()
    with _chat_request_lock:
        previous = _active_chat_cancel
        _active_chat_cancel = cancel_event
    if previous is not None:
        previous.set()
        tts_controller.stop()
    return cancel_event


def _finish_chat_request(cancel_event):
    """Resume wake listening only when the current request has really finished."""
    global _active_chat_cancel
    if cancel_event is None:
        return False
    with _chat_request_lock:
        if _active_chat_cancel is not cancel_event:
            return False
        _active_chat_cancel = None
        _voice_input_paused.clear()
        return True


def set_wispr_dictation_active(active):
    """Yield the microphone to Wispr Flow while its text is being dictated."""
    if active:
        # Do not let Maanav's current TTS leak into Wispr's live transcription.
        stop_speaking()
        _wispr_dictation_active.set()
        _set_dictation_status("RELEASING MIC FOR WISPR…")

        def wait_for_microphone_release():
            try:
                # A direct-call capture may run longer than the short wake listen.
                released = _microphone_lock.acquire(timeout=15)
                if released:
                    _microphone_lock.release()
                    if _wispr_dictation_active.is_set():
                        root.after(0, _set_dictation_status, "WISPR FLOW · READY TO DICTATE")
                elif _wispr_dictation_active.is_set():
                    root.after(0, _set_dictation_status, "MIC BUSY · WAIT OR END CALL")
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=wait_for_microphone_release, daemon=True).start()
    else:
        _wispr_dictation_active.clear()
        if voice_call_active:
            _set_dictation_status("CALL ACTIVE · SPEAK NATURALLY")
        elif voice_enabled:
            _set_dictation_status("READY · SAY WAKE UP")
        else:
            _set_dictation_status("MIC PAUSED")
    button = globals().get("wispr_input_button")
    if button is not None:
        try:
            button.config(text="CANCEL WISPR" if active else "WISPR INPUT")
        except tk.TclError:
            pass


def _set_dictation_status(text):
    set_voice_status(text)
    companion = globals().get("desktop_companion")
    if companion is not None:
        try:
            companion.status.configure(text=text)
        except tk.TclError:
            pass


def focus_wispr_input():
    """Focus the chat composer or cancel the active external dictation handoff."""
    if _wispr_dictation_active.is_set():
        set_wispr_dictation_active(False)
    else:
        set_wispr_dictation_active(True)
        entry.focus_set()


def _refresh_active_chat_display(session_id):
    """Keep the main chat transcript in sync when a turn came from the pet."""
    if session_id != get_active_session_id():
        return
    chat.config(state="normal")
    chat.delete("1.0", tk.END)
    messages = load_history(limit=80, session_id=session_id)
    if not messages:
        chat.insert(tk.END, "MAANAV  /  NEW CHAT\n", "assistant_title")
        chat.insert(tk.END, "What would you like to explore?\n\n", "assistant_text")
    for item in messages:
        if item["role"] == "user":
            chat.insert(tk.END, "YOU  ", "user_label")
            chat.insert(tk.END, item["content"] + "\n\n", "user_message")
        else:
            chat.insert(tk.END, "MAANAV  ", "assistant_label")
            chat.insert(tk.END, item["content"] + "\n\n", "assistant_message")
    chat.config(state="disabled")
    chat.see(tk.END)


def send_message(event=None):

    global selected_image, _active_chat_cancel
    message = entry.get("1.0", tk.END).strip()
    image_path = selected_image

    if not message and not image_path:
        return
    if image_path and not message:
        message = "Please describe this image."

    entry.delete("1.0", tk.END)
    selected_image = None
    update_attachment_bar()

    chat.config(state="normal")
    chat.insert(tk.END, "YOU  ", "user_label")
    display_message = message + (f"\n[Image: {Path(image_path).name}]" if image_path else "")
    chat.insert(tk.END, display_message + "\n\n", "user_message")
    chat.insert(tk.END, "MAANAV  ", "assistant_label")
    chat.insert(tk.END, "Thinking...\n\n", "thinking")

    chat.config(state="disabled")
    chat.see(tk.END)

    entry.config(state="disabled")
    send_button.config(state="disabled")

    session_id = get_active_session_id()
    cancel_event = _begin_chat_request()
    if _wispr_dictation_active.is_set():
        set_voice_status("WISPR FLOW · WAITING FOR MAANAV")
    threading.Thread(
        target=get_answer,
        args=(message, image_path, session_id, cancel_event),
        daemon=True
    ).start()


def get_answer(message, image_path=None, session_id=None, cancel_event=None):

    global _active_chat_cancel

    set_voice_activity("thinking")

    speech_pending = False
    try:

        answer = run_assistant_request(message, image_path=image_path, session_id=session_id)
        if cancel_event is not None and cancel_event.is_set():
            add_turn(message, "[Response generation stopped by the user.]", session_id=session_id)
            if session_id == get_active_session_id():
                root.after(0, show_generation_stopped)
            else:
                root.after(0, finish_chat_request)
            return
        add_turn(message, answer, session_id=session_id)
        root.after(0, refresh_sidebar_history)
        if session_id == get_active_session_id():
            root.after(0, show_answer, answer, True, cancel_event)
            speech_pending = True
        else:
            root.after(0, finish_chat_request)

    except Exception as error:

        root.after(
            0,
            show_error,
            str(error)
        )
    finally:
        request_finished = False
        with _chat_request_lock:
            if _active_chat_cancel is cancel_event and not speech_pending:
                _active_chat_cancel = None
                _voice_input_paused.clear()
                request_finished = True
        if request_finished:
            set_wispr_dictation_active(False)
        if voice_activity == "thinking":
            set_voice_activity("idle")


def finish_chat_request():
    entry.config(state="normal")
    send_button.config(state="normal")
    entry.focus_set()


def show_generation_stopped():
    chat.config(state="normal")
    pending = chat.search("Thinking...", "1.0", tk.END)
    if pending:
        chat.delete(pending, f"{pending}+11c")
    chat.insert(tk.END, "MAANAV  ", "assistant_label")
    chat.insert(tk.END, "Response generation stopped.\n\n", "assistant_text")
    chat.config(state="disabled")
    chat.see(tk.END)
    finish_chat_request()


# =========================================================
# VOICE TOGGLE
# =========================================================

def _set_voice_enabled(enabled):
    global voice_enabled, voice_call_active, _voice_enabled_before_call
    voice_enabled = bool(enabled)
    if not voice_enabled:
        voice_call_active = False
        _voice_enabled_before_call = None
        stop_speaking()
        set_voice_activity("idle")
    if "voice_button" in globals():
        voice_button.config(
            text="VOICE ON · HAZEL" if voice_enabled else "VOICE OFF",
            fg=GREEN if voice_enabled else MUTED,
        )
    if "voice_state" in globals():
        voice_state.config(text="MIC · LISTENING" if voice_enabled else "MIC · PAUSED")
    if "desktop_companion" in globals() and not voice_call_active:
        desktop_companion.set_call_active(False)


def toggle_voice():
    _set_voice_enabled(not voice_enabled)


def set_voice_call_mode(active):
    """Let the pet Call button speak directly without the wake phrase."""
    global voice_call_active, _voice_enabled_before_call
    active = bool(active)
    if active and not voice_call_active:
        _voice_enabled_before_call = voice_enabled
        voice_call_active = True
        _set_voice_enabled(True)
        set_voice_status("CALL ACTIVE · SPEAK NATURALLY")
    elif not active and voice_call_active:
        resume_voice = bool(_voice_enabled_before_call)
        voice_call_active = False
        _voice_enabled_before_call = None
        if not resume_voice:
            _set_voice_enabled(False)
        else:
            if "voice_state" in globals():
                voice_state.config(text="MIC · LISTENING")
            set_voice_status("READY · CTRL+ALT+SPACE OR MOUSE 5")
    if "desktop_companion" in globals():
        desktop_companion.set_call_active(voice_call_active)


# =========================================================
# EXPRESSIVE VOICE
# =========================================================

expressive_voice_buttons = []


def refresh_expressive_voice_buttons():
    expressive_active = (
        EXPRESSIVE_VOICE_ENABLED and not OFFLINE_VOICE_ONLY and not OFFLINE_AI_ONLY
    )
    for button in expressive_voice_buttons:
        try:
            button.config(
                text="WARM VOICE: ON" if expressive_active else "WARM VOICE: OFF",
                fg=ACCENT if expressive_active else MUTED,
            )
        except tk.TclError:
            pass


def toggle_expressive_voice():
    global EXPRESSIVE_VOICE_ENABLED
    enabling = not EXPRESSIVE_VOICE_ENABLED
    if enabling and (OFFLINE_VOICE_ONLY or OFFLINE_AI_ONLY):
        messagebox.showinfo(
            "Offline mode",
            "Expressive Gemini speech is unavailable while offline mode is active. Local voice remains enabled.",
        )
        return
    if enabling and not os.getenv("GEMINI_API_KEY"):
        messagebox.showwarning(
            "Expressive voice unavailable",
            "Add GEMINI_API_KEY to enable Gemini expressive speech. Local voice remains available.",
        )
        return
    if enabling and not messagebox.askyesno(
        "Enable expressive voice?",
        "Expressive speech sends Maanav's reply text to Google Gemini to create warmer, more emotional audio. "
        "Local speech remains available when this is off. Enable it?",
    ):
        return
    EXPRESSIVE_VOICE_ENABLED = enabling
    save_setting("expressive_voice", EXPRESSIVE_VOICE_ENABLED)
    refresh_expressive_voice_buttons()


def open_personalization_dialog():
    dialog = tk.Toplevel(root)
    dialog.title("Maanav Vyom · Settings")
    dialog.geometry("640x700")
    dialog.minsize(580, 520)
    dialog.resizable(True, True)
    dialog.configure(bg=PANEL)
    dialog.transient(root)

    footer = tk.Frame(dialog, bg=PANEL)
    footer.pack(side=tk.BOTTOM, fill=tk.X)
    settings_canvas = tk.Canvas(dialog, bg=PANEL, highlightthickness=0, borderwidth=0)
    settings_scrollbar = tk.Scrollbar(dialog, orient=tk.VERTICAL, command=settings_canvas.yview)
    settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
    settings_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    settings_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    settings_content = tk.Frame(settings_canvas, bg=PANEL)
    settings_window = settings_canvas.create_window((0, 0), window=settings_content, anchor="nw")

    def update_settings_scroll(_event=None):
        settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))

    settings_content.bind("<Configure>", update_settings_scroll)
    settings_canvas.bind("<Configure>", lambda event: settings_canvas.itemconfigure(settings_window, width=event.width))
    settings_canvas.bind("<MouseWheel>", lambda event: settings_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units"))

    tk.Label(
        settings_content, text="MAANAV VYOM SETTINGS", bg=PANEL, fg=ACCENT,
        font=("Segoe UI", 16, "bold"),
    ).pack(anchor="w", padx=24, pady=(22, 4))
    tk.Label(
        settings_content,
        text="Appearance, voice, response style, and the folders Maanav can search.",
        bg=PANEL, fg=MUTED, font=("Segoe UI", 10),
    ).pack(anchor="w", padx=24, pady=(0, 16))

    card = tk.Frame(settings_content, bg=SIDEBAR, padx=16, pady=14)
    card.pack(fill=tk.X, padx=20)
    theme_row = tk.Frame(card, bg=SIDEBAR)
    theme_row.pack(fill=tk.X, pady=(0, 12))
    tk.Label(theme_row, text="GALAXY THEME", bg=SIDEBAR, fg=TEXT,
             font=("Segoe UI", 9, "bold")).pack(anchor="w")
    theme_var = tk.StringVar(value=ACTIVE_THEME.title())
    theme_options = {name.title(): name for name in THEME_PRESETS}
    theme_menu = tk.OptionMenu(theme_row, theme_var, *theme_options)
    theme_menu.configure(bg=CARD, fg=TEXT, activebackground=CARD, activeforeground=ACCENT,
                         relief=tk.FLAT, borderwidth=0, highlightthickness=0, anchor="w")
    theme_menu["menu"].configure(bg=CARD, fg=TEXT, activebackground=ACCENT_DARK)
    theme_menu.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=(5, 0))
    color_buttons = tk.Frame(theme_row, bg=SIDEBAR)
    color_buttons.pack(side=tk.RIGHT, padx=(8, 0), pady=(5, 0))
    custom_colors = {"BG": BG, "ACCENT": ACCENT}
    custom_changed = set()

    def choose_theme_color(role):
        picked = colorchooser.askcolor(color=custom_colors[role], parent=dialog,
                                       title="Choose background color" if role == "BG" else "Choose highlight color")[1]
        if picked:
            custom_colors[role] = picked
            custom_changed.add(role)

    tk.Button(color_buttons, text="Background…", command=lambda: choose_theme_color("BG"),
              bg=CARD, fg=TEXT, activebackground=ACCENT_DARK, relief=tk.FLAT,
              borderwidth=0, padx=8, pady=5).pack(side=tk.LEFT, padx=2)
    tk.Button(color_buttons, text="Accent…", command=lambda: choose_theme_color("ACCENT"),
              bg=CARD, fg=TEXT, activebackground=ACCENT_DARK, relief=tk.FLAT,
              borderwidth=0, padx=8, pady=5).pack(side=tk.LEFT, padx=2)
    style_names = {
        "natural": "Natural and friendly",
        "concise": "Short and direct",
        "detailed": "Detailed and teach me",
        "creative": "Lively and creative",
    }
    style_values = {label: key for key, label in style_names.items()}
    selected_style = tk.StringVar(value=style_names[MAANAV_RESPONSE_STYLE])
    tk.Label(
        card, text="MAANAV VYOM'S REPLY STYLE", bg=SIDEBAR, fg=TEXT,
        font=("Segoe UI", 9, "bold"),
    ).pack(anchor="w")
    style_menu = tk.OptionMenu(card, selected_style, *style_values)
    style_menu.configure(
        bg=CARD, fg=TEXT, activebackground=CARD, activeforeground=ACCENT,
        relief=tk.FLAT, borderwidth=0, highlightthickness=0, anchor="w",
    )
    style_menu["menu"].configure(bg=CARD, fg=TEXT, activebackground=ACCENT_DARK)
    style_menu.pack(fill=tk.X, pady=(6, 12))

    speak_var = tk.BooleanVar(value=SPEAK_RESPONSES_ENABLED)
    listening_var = tk.BooleanVar(value=wake_word_enabled)
    wander_var = tk.BooleanVar(value=NESUKO_WANDER_ENABLED)
    pet_sounds_var = tk.BooleanVar(value=NESUKO_SOUNDS_ENABLED)
    for label, variable in (
        ("Keep wake-word listening active", listening_var),
        ("Speak Maanav's answers aloud", speak_var),
        ("Let Nesuko wander around the desktop", wander_var),
        ("Nesuko sound effects (off by default)", pet_sounds_var),
    ):
        tk.Checkbutton(
            card, text=label, variable=variable, bg=SIDEBAR, fg=TEXT,
            activebackground=SIDEBAR, activeforeground=ACCENT,
            selectcolor=CARD, font=("Segoe UI", 9), anchor="w",
        ).pack(fill=tk.X, pady=4)
    tk.Button(card, text=f"Choose microphone · {short_microphone_label(MIC_DEVICE_NAME)}",
              command=choose_microphone, bg=CARD, fg=TEXT, activebackground=ACCENT_DARK,
              relief=tk.FLAT, borderwidth=0, anchor="w", padx=10, pady=7).pack(fill=tk.X, pady=(6, 0))

    roots_card = tk.Frame(settings_content, bg=SIDEBAR, padx=16, pady=12)
    roots_card.pack(fill=tk.BOTH, expand=True, padx=20, pady=(12, 0))
    tk.Label(roots_card, text="FILE SEARCH LOCATIONS", bg=SIDEBAR, fg=TEXT,
             font=("Segoe UI", 9, "bold")).pack(anchor="w")
    tk.Label(roots_card,
             text="Maanav searches Desktop, Downloads, Documents, Pictures, Music, and Videos by default. Add any other folders here.",
             bg=SIDEBAR, fg=MUTED, font=("Segoe UI", 8), wraplength=560,
             justify=tk.LEFT).pack(anchor="w", pady=(3, 7))
    roots_list = tk.Listbox(roots_card, bg=CARD, fg=TEXT, selectbackground=ACCENT_DARK,
                            relief=tk.FLAT, borderwidth=0, height=4, font=("Segoe UI", 9))
    roots_list.pack(fill=tk.BOTH, expand=True)
    for folder_path in FILE_SEARCH_ROOTS:
        roots_list.insert(tk.END, folder_path)

    def add_search_folder():
        selected = filedialog.askdirectory(parent=dialog, title="Choose a folder Maanav may search")
        if selected and selected not in roots_list.get(0, tk.END):
            roots_list.insert(tk.END, selected)

    def remove_search_folder():
        selection = roots_list.curselection()
        if selection:
            roots_list.delete(selection[0])

    roots_buttons = tk.Frame(roots_card, bg=SIDEBAR)
    roots_buttons.pack(fill=tk.X, pady=(7, 0))
    tk.Button(roots_buttons, text="＋ Add folder", command=add_search_folder, bg=CARD, fg=TEXT,
              activebackground=ACCENT_DARK, relief=tk.FLAT, borderwidth=0, padx=10, pady=5).pack(side=tk.LEFT)
    tk.Button(roots_buttons, text="Remove selected", command=remove_search_folder, bg=CARD, fg=TEXT,
              activebackground=ACCENT_DARK, relief=tk.FLAT, borderwidth=0, padx=10, pady=5).pack(side=tk.LEFT, padx=6)

    tk.Label(
        settings_content,
        text="Voice: press Ctrl+Alt+Space or Mouse Button 5, then speak. Try “change theme to blue” or “search inside my files for …”. Content search checks common text formats and returns short matching lines.",
        bg=PANEL, fg=MUTED, font=("Segoe UI", 9), justify=tk.LEFT,
        wraplength=465,
    ).pack(anchor="w", padx=24, pady=(16, 12))

    def save_customization():
        global MAANAV_RESPONSE_STYLE, SPEAK_RESPONSES_ENABLED
        global NESUKO_WANDER_ENABLED, NESUKO_SOUNDS_ENABLED, FILE_SEARCH_ROOTS, wake_word_enabled
        chosen_style = style_values.get(selected_style.get(), "natural")
        chosen_theme = theme_options.get(theme_var.get(), "gold")
        chosen_roots = list(roots_list.get(0, tk.END))
        chosen_palette = dict(THEME_PRESETS[chosen_theme])
        if chosen_theme == ACTIVE_THEME:
            chosen_palette.update({"BG": BG, "ACCENT": ACCENT})
        for role, color in custom_colors.items():
            if role in custom_changed:
                chosen_palette[role] = color
        updates = {
            "response_style": chosen_style,
            "speak_responses": speak_var.get(),
            "wake_listening": listening_var.get(),
            "nesuko_random_movement": wander_var.get(),
            "nesuko_sound_effects": pet_sounds_var.get(),
            "file_search_roots": chosen_roots,
            "theme": {"preset": chosen_theme, **{key.lower(): val for key, val in chosen_palette.items()}},
        }
        if not save_settings(updates):
            messagebox.showerror(
                "Preferences not saved",
                "Maanav could not save these options. Check that the project folder is writable.",
                parent=dialog,
            )
            return
        MAANAV_RESPONSE_STYLE = chosen_style
        SPEAK_RESPONSES_ENABLED = speak_var.get()
        if not SPEAK_RESPONSES_ENABLED:
            stop_speaking()
        NESUKO_WANDER_ENABLED = wander_var.get()
        NESUKO_SOUNDS_ENABLED = pet_sounds_var.get()
        FILE_SEARCH_ROOTS = chosen_roots
        wake_word_enabled = listening_var.get()
        overrides = {role: color for role, color in chosen_palette.items()
                     if color != THEME_PRESETS[chosen_theme][role]}
        apply_theme_preset(chosen_theme, custom_colors=overrides, persist=False)
        dialog.destroy()

    tk.Button(
        footer, text="SAVE OPTIONS", command=save_customization,
        bg=ACCENT_DARK, fg="white", activebackground=ACCENT,
        activeforeground="white", relief=tk.FLAT, borderwidth=0,
        cursor="hand2", padx=18, pady=9,
    ).pack(anchor="e", padx=24, pady=(2, 18))


# =========================================================
# SUGGESTIONS
# =========================================================

def use_suggestion(text):

    entry.delete(
        "1.0",
        tk.END
    )

    entry.insert(
        "1.0",
        text
    )

    entry.focus()


def suggestion(text):

    button = tk.Button(
        suggestions,
        text=text,
        font=("Segoe UI", 10),
        bg=CARD,
        fg=TEXT,
        activebackground="#211f17",
        activeforeground=TEXT,
        relief=tk.FLAT,
        borderwidth=0,
        cursor="hand2",
        padx=15,
        pady=10,
        command=lambda: use_suggestion(text)
    )

    button.pack(
        side=tk.LEFT,
        padx=(0, 10)
    )


# =========================================================
# GUI
# =========================================================

def voice_diagnostics():
    print("Maanav voice diagnostic")
    print(f"Wake phrase: {WAKE_WORD}")
    print(f"Selected microphone: {MIC_DEVICE} ({MIC_DEVICE_NAME})")
    devices = physical_input_devices()
    print("Available microphones:")
    for index, name in devices:
        print(f"  {index}: {name}")
    if OFFLINE_VOICE_ONLY:
        print("Command speech: local Faster Whisper (offline-only)")
    else:
        print("Wake detection: local Faster Whisper; online command dictation: Wispr Flow")
    print("Offline-only mode:", OFFLINE_VOICE_ONLY)
    model_path = local_whisper_model_path()
    print("Offline recognition:", model_path if model_path else "not configured (optional)")
    print("Offline Piper voice:", offline_tts.model_path if offline_tts.available() else "not configured")
    print("Windows voice fallback: pyttsx3")
    if not devices:
        print("WARNING: no input microphone was detected.")


def offline_sample_test():
    sample = Path(__file__).with_name("maanav_voice.wav")
    if not sample.is_file():
        print("Offline sample test unavailable: maanav_voice.wav was not found.")
        return False
    try:
        with sr.AudioFile(str(sample)) as source:
            audio = voice_recognizer.record(source)
        text = recognize_offline(audio)
        if not text:
            print("Offline sample test failed: no local speech model or no speech detected.")
            return False
        print("Offline transcription:", text)
        return True
    except Exception as error:
        print("Offline sample test failed:", error)
        return False


if __name__ == "__main__" and "--self-test" in sys.argv:
    cases = {
        "wake up": True,
        "Wake, up!": True,
        "hey wakeup maanav": True,
        "make it wake up the computer": False,
        "hello maanav": False,
        "wake me later": False,
    }
    for transcript, expected in cases.items():
        actual = wake_word_detected(transcript)
        assert actual is expected, f"wake matcher: {transcript!r} -> {actual}"
    assert classify("open calculator") == "app"
    assert classify("find my pdf") == "files"
    assert classify("turn up volume") == "system"
    print("PASS: wake phrase normalization (6 cases)")
    print("PASS: app, file, and system intent routing")
    _original_universal_launcher = universal_launch_app
    _original_routine_matcher = match_routine
    try:
        match_routine = lambda _message: None
        _launch_calls = []
        universal_launch_app = lambda app_name: (_launch_calls.append(app_name) or (True, "Opening " + app_name + "."))
        assert ask_ai("open Chrome") == "Opening Chrome."
        assert _launch_calls == ["Chrome"]
        universal_launch_app = lambda _app_name: (False, "I couldn't find that application.")
        assert ask_ai("open an unknown application") == "I couldn't find that application."
        print("PASS: chat and voice app intents use the unified launcher and report failures without AI guessing")
    finally:
        universal_launch_app = _original_universal_launcher
        match_routine = _original_routine_matcher
    voice_diagnostics()
    raise SystemExit(0)


if __name__ == "__main__" and "--voice-check" in sys.argv:
    voice_diagnostics()
    raise SystemExit(0)
if __name__ == "__main__" and "--voice-demo" in sys.argv:
    voice_diagnostics()
    voice_samples = (
        "Hey Maanav, I'm glad you're here.",
        "I'm here with you. Take your time; we can work through it together.",
        "That's fantastic! You did it, and you should feel proud of that.",
        "Nice one. I had a feeling you'd pull that off!",
        "I can do that. Here's the plan, step by step.",
        "What would you like to make next? I'm curious to hear your idea.",
    )
    for sample in voice_samples:
        if not speak(sample):
            print("TTS demo failed: local and configured cloud voice paths were unavailable.")
            raise SystemExit(1)
    print("TTS emotion demo passed: warm, reassuring, celebratory, playful, confident, and curious samples completed.")
    raise SystemExit(0)


if __name__ == "__main__" and "--offline-voice-demo" in sys.argv:
    voice_diagnostics()
    if not offline_tts.available():
        print("Offline TTS demo unavailable: install requirements-voice.txt and the local Piper voice model.")
        raise SystemExit(1)
    try:
        played = offline_tts.speak(
            "Hey Maanav, your local voice is ready. I can still speak when the internet is unavailable."
        )
    except Exception as error:
        print("Offline TTS demo failed:", error)
        raise SystemExit(1)
    if not played:
        print("Offline TTS demo was canceled or could not play.")
        raise SystemExit(1)
    print("PASS: Piper generated and played speech locally without an internet request.")
    raise SystemExit(0)


if __name__ == "__main__" and "--offline-sample-test" in sys.argv:
    raise SystemExit(0 if offline_sample_test() else 1)


if __name__ == "__main__" and "--wake-sample-test" in sys.argv:
    import tempfile
    import wave
    if not offline_tts.available() or not local_whisper_model_path():
        print("Offline wake sample needs both the Piper voice and local Whisper model.")
        raise SystemExit(1)
    try:
        with tempfile.TemporaryDirectory(prefix="maanav-wake-test-") as directory:
            sample_path = Path(directory) / "wake.wav"
            with wave.open(str(sample_path), "wb") as sample:
                offline_tts._load_voice().synthesize_wav(WAKE_WORD, sample)
            with sr.AudioFile(str(sample_path)) as source:
                audio = voice_recognizer.record(source)
            transcript = recognize_offline(audio)
            matched = bool(transcript and wake_word_detected(transcript))
            print("Local wake phrase transcription:", transcript or "no speech recognized")
            print("PASS: local wake phrase was recognized" if matched else "FAIL: local wake phrase was not recognized")
            raise SystemExit(0 if matched else 1)
    except SystemExit:
        raise
    except Exception as error:
        print("Offline wake sample failed:", error)
        raise SystemExit(1)


if __name__ == "__main__" and "--memory-flow-test" in sys.argv:
    import tempfile
    old_memory_path = memory.MEMORY_FILE
    try:
        with tempfile.TemporaryDirectory(prefix="maanav-memory-test-") as test_directory:
            memory.MEMORY_FILE = Path(test_directory) / "memory.json"
            saved_reply = ask_ai("Remember this: I prefer concise coding demos")
            assert memory.recall("note 1") == "I prefer concise coding demos"
            assert "remember" in saved_reply.lower()
            listed_reply = ask_ai("What do you remember about me?")
            assert "note 1: I prefer concise coding demos" in listed_reply
            deleted_reply = ask_ai("Forget note 1")
            assert "forgotten" in deleted_reply.lower()
            assert memory.get_all_memory() == {}
    except Exception as error:
        print("Explicit memory flow failed:", error)
        raise SystemExit(1)
    finally:
        memory.MEMORY_FILE = old_memory_path
    print("PASS: explicit note save, memory review, and forget all work locally")
    raise SystemExit(0)


if __name__ == "__main__" and "--offline-ai-test" in sys.argv:
    result = ask_ai("Explain what a Python variable is in one sentence.")
    print("Offline AI response:", result)
    raise SystemExit(0 if result and not result.startswith("I couldn't reach") else 1)


if __name__ == "__main__" and "--online-ai-test" in sys.argv:
    if OFFLINE_AI_ONLY:
        print("Online AI test cannot run while MAANAV_OFFLINE_AI or --offline is enabled.")
        raise SystemExit(1)
    result = ask_ai("Say hello in one short sentence.")
    print("Online routing response:", result)
    raise SystemExit(0 if result and not result.startswith("I couldn't reach") else 1)


def focus_chat():
    if "galaxy_page" in globals():
        galaxy_page.pack_forget()
    if "chat_page" in globals() and not chat_page.winfo_manager():
        chat_page.pack(fill=tk.BOTH, expand=True)
    if "entry" in globals():
        entry.focus_set()
    if "chat_nav_button" in globals():
        chat_nav_button.config(bg=SIDEBAR_HOVER, fg=ACCENT)
    if "galaxy_nav_button" in globals():
        galaxy_nav_button.config(bg=SIDEBAR, fg=TEXT_DIM)


def activate_voice_shortcut():
    """Listen once and route the spoken command directly to Maanav Vyom."""
    if not voice_enabled:
        set_voice_status("VOICE OFF · TURN ON VOICE FIRST")
        return
    if _wispr_dictation_active.is_set():
        set_wispr_dictation_active(False)
    try:
        root.deiconify()
        root.lift()
    except tk.TclError:
        pass
    focus_chat()
    _manual_voice_trigger.set()
    set_voice_status("SHORTCUT · SPEAK TO MAANAV VYOM")


def focus_galaxy():
    if "chat_page" in globals():
        chat_page.pack_forget()
    if "galaxy_page" in globals() and not galaxy_page.winfo_manager():
        galaxy_page.pack(fill=tk.BOTH, expand=True)
    if "animate_main_galaxy" in globals():
        animate_main_galaxy()
    if "galaxy_nav_button" in globals():
        galaxy_nav_button.config(bg=SIDEBAR_HOVER, fg=ACCENT)
    if "chat_nav_button" in globals():
        chat_nav_button.config(bg=SIDEBAR, fg=TEXT_DIM)


def refresh_sidebar_history():
    if "recent_frame" not in globals():
        return
    for widget in recent_frame.winfo_children():
        widget.destroy()
    sessions = list_sessions(limit=10)
    if not sessions:
        tk.Label(
            recent_frame, text="Start a conversation to see it here",
            bg=SIDEBAR, fg=MUTED, font=("Segoe UI", 9),
            wraplength=190, justify="left", anchor="w"
        ).pack(fill=tk.X, padx=12, pady=6)
        return
    active_session = next((item for item in sessions if item["active"]), None)
    if active_session and "chat_subtitle" in globals():
        chat_subtitle.config(text=active_session["title"])
    for session in sessions:
        title = session["title"]
        if session.get("temporary"):
            title = "◌ " + title
        if len(title) > 25:
            title = title[:24].rstrip() + "…"
        tk.Button(
            recent_frame, text=title, anchor="w",
            command=lambda key=session["id"]: open_chat_session(key),
            bg=SIDEBAR_HOVER if session["active"] else SIDEBAR,
            fg=ACCENT if session["active"] else TEXT_DIM,
            activebackground=SIDEBAR_HOVER, activeforeground=ACCENT,
            relief=tk.FLAT, borderwidth=0, cursor="hand2",
            font=("Segoe UI", 9), padx=12, pady=8
        ).pack(fill=tk.X, padx=5, pady=1)
        recent_frame.winfo_children()[-1].bind(
            "<Button-3>", lambda event, key=session["id"]: show_chat_menu(event, key)
        )


def open_chat_session(session_id):
    if not switch_session(session_id):
        return
    focus_chat()
    chat.config(state="normal")
    chat.delete("1.0", tk.END)
    messages = load_history(limit=80, session_id=session_id)
    for item in messages:
        if item["role"] == "user":
            chat.insert(tk.END, "YOU  ", "user_label")
            chat.insert(tk.END, item["content"] + "\n\n", "user_message")
        else:
            chat.insert(tk.END, "MAANAV  ", "assistant_label")
            chat.insert(tk.END, item["content"] + "\n\n", "assistant_message")
    if not messages:
        chat.insert(tk.END, "MAANAV  /  NEW CHAT\n", "assistant_title")
        chat.insert(tk.END, "What would you like to explore?\n\n", "assistant_text")
    chat.config(state="disabled")
    chat.see(tk.END)
    active = next((item for item in list_sessions() if item["id"] == session_id), None)
    if active and "chat_subtitle" in globals():
        chat_subtitle.config(text=active["title"])
    refresh_sidebar_history()
    entry.focus_set()


def start_new_chat():
    session_id = new_session()
    chat.config(state="normal")
    chat.delete("1.0", tk.END)
    chat.insert(tk.END, "MAANAV  /  NEW CHAT\n", "assistant_title")
    chat.insert(
        tk.END,
        "A fresh conversation is ready. What would you like to explore?\n\n",
        "assistant_text"
    )
    chat.config(state="disabled")
    if "chat_subtitle" in globals():
        chat_subtitle.config(text="New chat")
    refresh_sidebar_history()
    entry.focus_set()


def start_temporary_chat():
    session_id = new_session(temporary=True)
    chat.config(state="normal")
    chat.delete("1.0", tk.END)
    chat.insert(tk.END, "MAANAV  /  TEMPORARY CHAT\n", "assistant_title")
    chat.insert(tk.END, "This conversation is kept only until Maanav closes.\n\n", "assistant_text")
    chat.config(state="disabled")
    if "chat_subtitle" in globals():
        chat_subtitle.config(text="Temporary chat")
    refresh_sidebar_history()
    entry.focus_set()


def manage_chat_session(action, session_id):
    sessions = list_sessions(limit=1000)
    session = next((item for item in sessions if item["id"] == session_id), None)
    if not session:
        return
    if action == "rename":
        title = simpledialog.askstring("Rename chat", "Conversation name:",
                                       initialvalue=session["title"], parent=root)
        if title and rename_session(session_id, title):
            refresh_sidebar_history()
    elif action == "delete" and messagebox.askyesno(
        "Delete chat", f"Permanently delete ‘{session['title']}’ and its messages?", parent=root
    ):
        delete_session(session_id)
        open_chat_session(get_active_session_id())


def show_chat_menu(event, session_id):
    menu = tk.Menu(root, tearoff=False, bg=CARD, fg=TEXT,
                   activebackground=ACCENT_DARK, activeforeground="white")
    menu.add_command(label="Rename chat…", command=lambda: manage_chat_session("rename", session_id))
    menu.add_command(label="Delete chat…", command=lambda: manage_chat_session("delete", session_id))
    menu.tk_popup(event.x_root, event.y_root)


# ---------- SUBTLE BLACK + GOLD GALAXY PALETTE ----------

THEME_PRESETS = {
    "gold": {"BG": "#070705", "PANEL": "#0e0e0b", "CARD": "#17160f", "SIDEBAR": "#0a0a08", "SIDEBAR_HOVER": "#201d13", "TEXT": "#f0ecdc", "TEXT_DIM": "#c9c1a7", "MUTED": "#9b947b", "ACCENT": "#d3b44c", "ACCENT_DARK": "#806a2d", "CYAN": "#c5a94c", "GREEN": "#b6a34c"},
    "blue": {"BG": "#050914", "PANEL": "#0b1220", "CARD": "#111d30", "SIDEBAR": "#080e19", "SIDEBAR_HOVER": "#172740", "TEXT": "#e4efff", "TEXT_DIM": "#b7c9e6", "MUTED": "#8499b8", "ACCENT": "#70aaff", "ACCENT_DARK": "#315f9b", "CYAN": "#76caff", "GREEN": "#73d6b3"},
    "purple": {"BG": "#0b0710", "PANEL": "#130d1b", "CARD": "#20142a", "SIDEBAR": "#100a17", "SIDEBAR_HOVER": "#2b1939", "TEXT": "#f3eaff", "TEXT_DIM": "#cfbddf", "MUTED": "#a58db5", "ACCENT": "#ce91ff", "ACCENT_DARK": "#784aa0", "CYAN": "#bb9cff", "GREEN": "#a2d8bd"},
    "teal": {"BG": "#04100f", "PANEL": "#091917", "CARD": "#102522", "SIDEBAR": "#071311", "SIDEBAR_HOVER": "#16312d", "TEXT": "#e4faf5", "TEXT_DIM": "#b5d7cf", "MUTED": "#85aaa1", "ACCENT": "#68d5c1", "ACCENT_DARK": "#347a70", "CYAN": "#74dfd0", "GREEN": "#9cdaa1"},
    "rose": {"BG": "#11080c", "PANEL": "#1b0f15", "CARD": "#29151e", "SIDEBAR": "#150b11", "SIDEBAR_HOVER": "#38202c", "TEXT": "#ffedf3", "TEXT_DIM": "#e4c5d1", "MUTED": "#ba94a3", "ACCENT": "#ef91b3", "ACCENT_DARK": "#94516a", "CYAN": "#df9bd8", "GREEN": "#b5d49a"},
}
_saved_theme = read_settings().get("theme", {})
if not isinstance(_saved_theme, dict):
    _saved_theme = {}
_saved_preset = _saved_theme.get("preset", "gold")
if not isinstance(_saved_preset, str) or _saved_preset not in THEME_PRESETS:
    _saved_preset = "gold"
_initial_palette = dict(THEME_PRESETS[_saved_preset])
if isinstance(_saved_theme, dict):
    for _role in _initial_palette:
        _value = _saved_theme.get(_role.lower())
        if isinstance(_value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", _value):
            _initial_palette[_role] = _value
BG = _initial_palette["BG"]
PANEL = _initial_palette["PANEL"]
CARD = _initial_palette["CARD"]
SIDEBAR = _initial_palette["SIDEBAR"]
SIDEBAR_HOVER = _initial_palette["SIDEBAR_HOVER"]
TEXT = _initial_palette["TEXT"]
TEXT_DIM = _initial_palette["TEXT_DIM"]
MUTED = _initial_palette["MUTED"]
ACCENT = _initial_palette["ACCENT"]
ACCENT_DARK = _initial_palette["ACCENT_DARK"]
CYAN = _initial_palette["CYAN"]
GREEN = _initial_palette["GREEN"]
ACTIVE_THEME = _saved_theme.get("preset", "gold")
if ACTIVE_THEME not in THEME_PRESETS:
    ACTIVE_THEME = "gold"


def apply_theme_preset(name, persist=False, custom_role=None, custom_color=None, custom_colors=None):
    """Apply a palette to existing Tk widgets and optionally persist it."""
    global BG, PANEL, CARD, SIDEBAR, SIDEBAR_HOVER, TEXT, TEXT_DIM, MUTED
    global ACCENT, ACCENT_DARK, CYAN, GREEN, ACTIVE_THEME
    name = str(name or "gold").lower()
    if name not in THEME_PRESETS:
        name = "gold"
    palette = dict(THEME_PRESETS[name])
    if custom_role in palette and isinstance(custom_color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", custom_color):
        palette[custom_role] = custom_color
    for role, color in (custom_colors or {}).items():
        if role in palette and isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            palette[role] = color
    old_to_role = {
        "#070705": "BG", "#050505": "BG", "#0e0e0b": "PANEL", "#0b0b08": "PANEL",
        "#17160f": "CARD", "#0a0a08": "SIDEBAR", "#201d13": "SIDEBAR_HOVER",
        "#f0ecdc": "TEXT", "#c9c1a7": "TEXT_DIM", "#9b947b": "MUTED",
        "#d3b44c": "ACCENT", "#806a2d": "ACCENT_DARK", "#c5a94c": "CYAN",
        "#b6a34c": "GREEN", "#211f17": "SIDEBAR_HOVER", "#2b2617": "SIDEBAR_HOVER",
        "#242116": "SIDEBAR_HOVER", "#292316": "CARD", "#201d12": "SIDEBAR_HOVER",
    }
    _current_palette = {role: globals()[role] for role in THEME_PRESETS["gold"]}
    for _role, _color in _current_palette.items():
        old_to_role[_color.lower()] = _role
    for _role, _color in _initial_palette.items():
        old_to_role[_color.lower()] = _role
    values = {role: globals()[role] for role in palette}
    values.update(palette)
    for key, value in palette.items():
        globals()[key] = value
    ACTIVE_THEME = name

    def restyle(widget):
        try:
            for option in ("bg", "background", "activebackground", "selectcolor", "highlightbackground", "insertbackground"):
                current = str(widget.cget(option)).lower()
                if current in old_to_role:
                    widget.configure(**{option: palette[old_to_role[current]]})
            for option in ("fg", "foreground", "activeforeground", "selectforeground"):
                current = str(widget.cget(option)).lower()
                if current in old_to_role:
                    widget.configure(**{option: palette[old_to_role[current]]})
        except (tk.TclError, KeyError):
            pass
        for child in widget.winfo_children():
            restyle(child)
    if "root" in globals() and root.winfo_exists():
        root.configure(bg=BG)
        restyle(root)
    if persist:
        save_setting("theme", {"preset": name, **{key.lower(): val for key, val in palette.items()}})


root = tk.Tk()

root.title("Maanav AI")
screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
window_width = max(760, min(1180, screen_width - 40))
window_height = max(500, min(700, screen_height - 90))
root.geometry(f"{window_width}x{window_height}")
root.minsize(min(window_width, max(720, min(980, screen_width - 40))),
             min(window_height, max(480, min(600, screen_height - 90))))
root.configure(bg=BG)

main_shell = tk.Frame(root, bg=BG)
main_shell.pack(fill=tk.BOTH, expand=True)

sidebar = tk.Frame(main_shell, bg=SIDEBAR, width=244)
sidebar.pack(side=tk.LEFT, fill=tk.Y)
sidebar.pack_propagate(False)

workspace = tk.Frame(main_shell, bg=BG)
workspace.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

chat_page = tk.Frame(workspace, bg=BG)
galaxy_page = tk.Frame(workspace, bg=BG)

galaxy_header = tk.Frame(galaxy_page, bg=BG)
galaxy_header.pack(fill=tk.X, padx=26, pady=(20, 8))
tk.Label(galaxy_header, text="MAANAV  /  GALAXY", bg=BG, fg=ACCENT,
         font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
galaxy_status = tk.Label(galaxy_header, text="READY · SAY WAKE UP", bg=BG,
                         fg=MUTED, font=("Segoe UI", 9, "bold"))
galaxy_status.pack(side=tk.RIGHT, padx=(12, 0))
tk.Button(galaxy_header, text="STOP SPEAKING", command=stop_speaking,
          bg=BG, fg=TEXT_DIM, activebackground=BG, activeforeground=ACCENT,
          relief=tk.FLAT, borderwidth=0, cursor="hand2",
          font=("Segoe UI", 9, "bold")).pack(side=tk.RIGHT, padx=10)
tk.Button(galaxy_header, text="VOICE ON/OFF", command=toggle_voice,
          bg=BG, fg=TEXT_DIM, activebackground=BG, activeforeground=ACCENT,
          relief=tk.FLAT, borderwidth=0, cursor="hand2",
          font=("Segoe UI", 9, "bold")).pack(side=tk.RIGHT, padx=8)
expressive_voice_buttons.append(tk.Button(
    galaxy_header, command=toggle_expressive_voice,
    bg=BG, activebackground=BG, activeforeground=ACCENT,
    relief=tk.FLAT, borderwidth=0, cursor="hand2",
    font=("Segoe UI", 8, "bold")
))
expressive_voice_buttons[-1].pack(side=tk.RIGHT, padx=7)
refresh_expressive_voice_buttons()

galaxy_canvas = tk.Canvas(galaxy_page, bg=BG, highlightthickness=0, cursor="crosshair")
galaxy_canvas.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 8))
main_galaxy_stars = generate_spiral_stars()
galaxy_canvas_items = []
galaxy_canvas_colors = []
galaxy_hint_item = galaxy_canvas.create_text(
    16, 0, anchor="sw", text="2,100 STARS  ·  MOVE TO DISTURB  ·  SCROLL TO ZOOM",
    fill=MUTED, font=("Segoe UI", 8, "bold"), tags=("overlay",),
)
galaxy_state_item = galaxy_canvas.create_text(
    0, 0, anchor="se", text="IDLE", fill=MUTED,
    font=("Segoe UI", 8, "bold"), tags=("overlay",),
)
# Apply the thousands of Canvas item updates inside Tcl in two batched calls.
# This keeps the existing per-star rendering and interaction while reducing
# Python/Tk boundary crossings substantially on each animation frame.
galaxy_canvas.tk.eval("""
namespace eval ::maanav {}
proc ::maanav::set_star_coords {canvas packed} {
    foreach {id x1 y1 x2 y2} $packed {
        $canvas coords $id $x1 $y1 $x2 $y2
    }
}
proc ::maanav::set_star_colors {canvas packed} {
    foreach {id color} $packed {
        $canvas itemconfigure $id -fill $color
    }
}
""")
galaxy_zoom = [1.0]
galaxy_mouse = [-1000.0, -1000.0]


def move_main_galaxy(event):
    galaxy_mouse[0], galaxy_mouse[1] = event.x, event.y


def zoom_main_galaxy(event):
    galaxy_zoom[0] = max(0.5, min(2.5, galaxy_zoom[0] * (1.12 if event.delta > 0 else 0.89)))


def reset_main_galaxy_zoom(_event=None):
    galaxy_zoom[0] = 1.0


def animate_main_galaxy():
    if not galaxy_page.winfo_manager():
        return
    phase = time.monotonic()
    state = voice_activity
    active = state != "idle"
    motion = 2.4 if state == "listening" else (4.0 if state == "thinking" else (3.1 if state == "speaking" else 0.22))
    wave_size = 0 if state == "idle" else (7 if state == "listening" else (13 if state == "thinking" else 10))
    width, height = max(galaxy_canvas.winfo_width(), 300), max(galaxy_canvas.winfo_height(), 250)
    center_x, center_y = width / 2, height / 2
    if not galaxy_canvas_items:
        for _star in main_galaxy_stars:
            galaxy_canvas_items.append(
                galaxy_canvas.create_oval(0, 0, 1, 1, fill="#5e512d", outline="", tags=("star",))
            )
            galaxy_canvas_colors.append(None)
    packed_coords = []
    packed_colors = []
    for index, star in enumerate(main_galaxy_stars):
        angle = star["angle"] + phase * star["speed"] * 60 * motion
        radius = star["radius"] * galaxy_zoom[0]
        spiral = math.sin(radius * 0.018) * 18
        x = center_x + math.cos(angle) * (radius + spiral)
        y = center_y + math.sin(angle) * (radius + spiral) * 0.58
        y += math.sin(angle * 2.0 + phase * (9 if state == "thinking" else 4)) * wave_size
        dx, dy = x - galaxy_mouse[0], y - galaxy_mouse[1]
        distance = math.hypot(dx, dy)
        if 1 < distance < 125:
            force = (125 - distance) / 125
            x += dx * force * 0.85 - dy * force * 0.26
            y += dy * force * 0.85 + dx * force * 0.26
        ratio = min(star["radius"] / 650, 1)
        bright = star["brightness"] >= 130
        if state == "speaking":
            color = ACCENT if bright else ACCENT_DARK
        elif active:
            color = ACCENT if bright else ACCENT_DARK
        else:
            color = ACCENT_DARK if bright else (MUTED if ratio < 0.18 else ACCENT_DARK)
        ix, iy = int(x), int(y)
        size = star["size"]
        item = galaxy_canvas_items[index]
        packed_coords.extend((item, ix, iy, ix + size, iy + size))
        if color != galaxy_canvas_colors[index]:
            packed_colors.extend((item, color))
            galaxy_canvas_colors[index] = color
    galaxy_canvas.tk.call("::maanav::set_star_coords", galaxy_canvas._w, tuple(packed_coords))
    if packed_colors:
        galaxy_canvas.tk.call("::maanav::set_star_colors", galaxy_canvas._w, tuple(packed_colors))
    galaxy_canvas.coords(galaxy_hint_item, 16, height - 12)
    galaxy_canvas.coords(galaxy_state_item, width - 16, height - 12)
    galaxy_canvas.itemconfigure(galaxy_state_item, text=state.upper(), fill=ACCENT if active else MUTED)
    # Schedule by frame-start time, not after render completion, so the canvas
    # can reach about 20 fps when active without adding a busy polling loop.
    frame_period = 0.05 if active else 0.18
    elapsed = time.monotonic() - phase
    root.after(max(16, int((frame_period - elapsed) * 1000)), animate_main_galaxy)


galaxy_canvas.bind("<Motion>", move_main_galaxy)
galaxy_canvas.bind("<MouseWheel>", zoom_main_galaxy)
galaxy_canvas.bind("<Double-Button-1>", reset_main_galaxy_zoom)

# Animated spiral galaxy in the sidebar, adapted from ui_galaxy.py.
sidebar_brand = tk.Frame(sidebar, bg=SIDEBAR)
sidebar_brand.pack(fill=tk.X, padx=18, pady=(20, 10))
tk.Label(sidebar_brand, text="✦", bg=SIDEBAR, fg=ACCENT,
         font=("Segoe UI", 20, "bold")).pack(side=tk.LEFT, padx=(0, 8))
tk.Label(sidebar_brand, text="MAANAV", bg=SIDEBAR, fg=TEXT,
         font=("Segoe UI", 15, "bold")).pack(side=tk.LEFT)
tk.Label(sidebar, text="PERSONAL AI COMPANION", bg=SIDEBAR, fg=MUTED,
         font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=22, pady=(0, 10))

side_galaxy = tk.Canvas(sidebar, width=220, height=154, bg=SIDEBAR,
                        highlightthickness=0, cursor="crosshair")
side_galaxy.pack(fill=tk.X, padx=12, pady=(0, 14))
side_star_rng = random.Random(37)
side_stars = [{
    "angle": side_star_rng.uniform(0, math.tau),
    "radius": side_star_rng.uniform(8, 102),
    "speed": side_star_rng.uniform(0.35, 1.0),
    "phase": side_star_rng.uniform(0, math.tau),
    "size": side_star_rng.choice((1, 1, 1, 2)),
} for _ in range(100)]
side_mouse = [110.0, 77.0]
side_zoom = [1.0]
hand_paused = [False]
hand_tracker = None
hand_pinch_distance = [None]
hand_peace_active = [False]
hand_scroll_y = [None]
hand_scroll_time = [0.0]

def move_side_galaxy(event):
    side_mouse[0], side_mouse[1] = event.x, event.y

def zoom_side_galaxy(event):
    side_zoom[0] = max(0.55, min(2.4, side_zoom[0] * (1.12 if event.delta > 0 else 0.89)))

def reset_side_galaxy_zoom(_event=None):
    side_zoom[0] = 1.0

def apply_hand_frame(frame):
    if not frame.tracked:
        hand_paused[0] = False
        hand_pinch_distance[0] = None
        hand_peace_active[0] = False
        hand_scroll_y[0] = None
        if "desktop_companion" in globals():
            desktop_companion.hand_track_pet(0, 0, tracked=False)
        return
    if not hand_paused[0]:
        side_mouse[0] = frame.x * max(side_galaxy.winfo_width(), 220)
        side_mouse[1] = frame.y * max(side_galaxy.winfo_height(), 154)
    hand_paused[0] = frame.open_palm
    if frame.pinch_distance < HandTracker.PINCH_THRESHOLD:
        previous = hand_pinch_distance[0]
        if previous is not None:
            zoom_delta = (frame.pinch_distance - previous) * 0.18
            zoom_factor = max(0.94, min(1.06, 1 + zoom_delta))
            side_zoom[0] = max(0.55, min(2.4, side_zoom[0] * zoom_factor))
            galaxy_zoom[0] = max(0.5, min(2.5, galaxy_zoom[0] * zoom_factor))
        hand_pinch_distance[0] = frame.pinch_distance
    else:
        hand_pinch_distance[0] = None
    now = time.monotonic()
    if frame.open_palm and frame.pinch_distance >= 0.65:
        previous_y = hand_scroll_y[0]
        if (previous_y is not None and abs(frame.y - previous_y) >= 0.085
                and now - hand_scroll_time[0] >= 0.18):
            if "chat" in globals() and chat.winfo_exists():
                chat.yview_scroll(3 if frame.y < previous_y else -3, "units")
            hand_scroll_time[0] = now
        hand_scroll_y[0] = frame.y
    else:
        hand_scroll_y[0] = None
    if frame.peace and not hand_peace_active[0]:
        toggle_voice()
    hand_peace_active[0] = frame.peace
    if "desktop_companion" in globals():
        desktop_companion.hand_track_pet(frame.x, frame.y)

def hand_frame_received(frame):
    try:
        def update_ui():
            if hand_tracker is not None and "sidebar_hand_button" in globals():
                sidebar_hand_button.config(text="◇   Hand controls · LIVE")
            apply_hand_frame(frame)
        root.after(0, update_ui)
    except tk.TclError:
        pass

def hand_tracking_error(message):
    def show_error():
        global hand_tracker
        hand_paused[0] = False
        hand_pinch_distance[0] = None
        hand_peace_active[0] = False
        hand_scroll_y[0] = None
        if "desktop_companion" in globals():
            desktop_companion.hand_track_pet(0, 0, tracked=False)
        hand_tracker = None
        if "sidebar_hand_button" in globals():
            sidebar_hand_button.config(text="◇   Hand controls")
        messagebox.showwarning("Hand controls", message, parent=root)
    try:
        root.after(0, show_error)
    except tk.TclError:
        pass

def toggle_hand_tracking():
    global hand_tracker
    if hand_tracker is not None:
        hand_tracker.stop()
        hand_tracker = None
        hand_paused[0] = False
        hand_pinch_distance[0] = None
        hand_peace_active[0] = False
        hand_scroll_y[0] = None
        if "desktop_companion" in globals():
            desktop_companion.hand_track_pet(0, 0, tracked=False)
        sidebar_hand_button.config(text="◇   Hand controls")
        return
    try:
        hand_tracker = HandTracker(hand_frame_received, hand_tracking_error)
        hand_tracker.start()
        sidebar_hand_button.config(text="◇   Starting camera…")
    except RuntimeError as error:
        hand_tracker = None
        messagebox.showinfo(
            "Optional hand controls",
            str(error) + "\n\nInstall the optional packages listed in requirements-vision.txt, then restart Maanav.",
            parent=root,
        )

def animate_side_galaxy():
    phase = time.monotonic()
    state = voice_activity
    active = state != "idle"
    motion = 3.8 if state == "thinking" else (2.8 if active else 0.22)
    width = max(side_galaxy.winfo_width(), 220)
    height = max(side_galaxy.winfo_height(), 154)
    cx, cy = width / 2, height / 2
    side_galaxy.delete("all")
    for radius in (20, 37, 54, 72, 91):
        radius *= side_zoom[0]
        side_galaxy.create_oval(
            cx-radius, cy-radius*0.47, cx+radius, cy+radius*0.47,
            outline=CARD if active else SIDEBAR_HOVER, width=1
        )
    for star in side_stars:
        angle = star["angle"] + phase * star["speed"] * motion
        radius = star["radius"] * side_zoom[0]
        spiral = math.sin(radius * 0.018) * 6
        x = cx + math.cos(angle) * (radius + spiral)
        wave = math.sin(angle * 2.0 + phase * (8 if state == "thinking" else 4)) * (5 if state != "idle" else 0)
        y = cy + math.sin(angle + phase * 0.45) * (radius + spiral) * 0.48 + wave
        dx, dy = x-side_mouse[0], y-side_mouse[1]
        distance = math.hypot(dx, dy)
        if not hand_paused[0] and 1 < distance < 45:
            force = (45-distance) / 45
            x += dx * force * 0.7 - dy * force * 0.18
            y += dy * force * 0.7 + dx * force * 0.18
        bright = int(phase * (8 if state == "thinking" else 6 if active else 1) + star["phase"]) % 7 == 0
        color = ACCENT if active and bright else (TEXT_DIM if bright else ACCENT_DARK)
        size = star["size"] + (1 if active and bright else 0)
        side_galaxy.create_oval(x, y, x+size, y+size, fill=color, outline="")
    side_galaxy.create_oval(cx-3, cy-2, cx+3, cy+2, fill=ACCENT, outline="")
    side_galaxy.create_text(
        12, height-12, text="GALAXY  ·  LIVE", anchor="sw",
        fill=ACCENT if active else MUTED, font=("Segoe UI", 8, "bold")
    )
    root.after(35 if active else 100, animate_side_galaxy)

side_galaxy.bind("<Motion>", move_side_galaxy)
side_galaxy.bind("<MouseWheel>", zoom_side_galaxy)
side_galaxy.bind("<Double-Button-1>", reset_side_galaxy_zoom)
animate_side_galaxy()

new_chat_button = tk.Button(
    sidebar, text="＋   New chat", command=start_new_chat, anchor="w",
    bg=SIDEBAR_HOVER, fg=TEXT, activebackground="#2b2617",
    activeforeground=ACCENT, relief=tk.FLAT, borderwidth=0,
    cursor="hand2", font=("Segoe UI", 10, "bold"), padx=14, pady=11
)
new_chat_button.pack(fill=tk.X, padx=14, pady=(0, 19))
temporary_chat_button = tk.Button(
    sidebar, text="◌   Temporary chat", command=start_temporary_chat, anchor="w",
    bg=SIDEBAR, fg=TEXT_DIM, activebackground=SIDEBAR_HOVER,
    activeforeground=ACCENT, relief=tk.FLAT, borderwidth=0,
    cursor="hand2", font=("Segoe UI", 9), padx=14, pady=8
)
temporary_chat_button.pack(fill=tk.X, padx=14, pady=(0, 10))

tk.Label(sidebar, text="WORKSPACE", bg=SIDEBAR, fg=MUTED,
         font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=22, pady=(0, 7))
chat_nav_button = tk.Button(
    sidebar, text="◉   Chat", command=focus_chat, anchor="w",
    bg=SIDEBAR_HOVER, fg=ACCENT, activebackground=SIDEBAR_HOVER,
    activeforeground=ACCENT, relief=tk.FLAT, borderwidth=0,
    cursor="hand2", font=("Segoe UI", 10, "bold"), padx=14, pady=10
)
chat_nav_button.pack(fill=tk.X, padx=9, pady=2)
galaxy_nav_button = tk.Button(
    sidebar, text="✦   Galaxy", command=focus_galaxy, anchor="w",
    bg=SIDEBAR, fg=TEXT_DIM, activebackground=SIDEBAR_HOVER,
    activeforeground=ACCENT, relief=tk.FLAT, borderwidth=0,
    cursor="hand2", font=("Segoe UI", 10), padx=14, pady=10
)
galaxy_nav_button.pack(fill=tk.X, padx=9, pady=2, before=chat_nav_button)
sidebar_memory_button = tk.Button(
    sidebar, text="▤   Memory", command=show_saved_memory, anchor="w",
    bg=SIDEBAR, fg=TEXT_DIM, activebackground=SIDEBAR_HOVER,
    activeforeground=ACCENT, relief=tk.FLAT, borderwidth=0,
    cursor="hand2", font=("Segoe UI", 10), padx=14, pady=10
)
sidebar_memory_button.pack(fill=tk.X, padx=9, pady=2)
sidebar_routines_button = tk.Button(
    sidebar, text="◇   Routines", command=open_routines_dialog, anchor="w",
    bg=SIDEBAR, fg=TEXT_DIM, activebackground=SIDEBAR_HOVER,
    activeforeground=ACCENT, relief=tk.FLAT, borderwidth=0,
    cursor="hand2", font=("Segoe UI", 10), padx=14, pady=10
)
sidebar_routines_button.pack(fill=tk.X, padx=9, pady=2)
personalize_button = tk.Button(
    sidebar, text="⚙   Settings", command=open_personalization_dialog, anchor="w",
    bg=SIDEBAR, fg=TEXT_DIM, activebackground=SIDEBAR_HOVER,
    activeforeground=ACCENT, relief=tk.FLAT, borderwidth=0,
    cursor="hand2", font=("Segoe UI", 10), padx=14, pady=10
)
personalize_button.pack(fill=tk.X, padx=9, pady=2)
sidebar_hand_button = tk.Button(
    sidebar, text="◇   Hand controls", command=toggle_hand_tracking, anchor="w",
    bg=SIDEBAR, fg=TEXT_DIM, activebackground=SIDEBAR_HOVER,
    activeforeground=ACCENT, relief=tk.FLAT, borderwidth=0,
    cursor="hand2", font=("Segoe UI", 10), padx=14, pady=10
)
sidebar_hand_button.pack(fill=tk.X, padx=9, pady=2)
tk.Label(
    sidebar, text="Pinch: zoom  ·  Open palm: scroll\nMove your hand over Nesuko to pet",
    bg=SIDEBAR, fg=MUTED, font=("Segoe UI", 8), justify=tk.LEFT, wraplength=195,
).pack(anchor="w", padx=22, pady=(1, 7))

tk.Frame(sidebar, bg="#242116", height=1).pack(fill=tk.X, padx=16, pady=15)
tk.Label(sidebar, text="RECENT CHATS", bg=SIDEBAR, fg=MUTED,
         font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=22, pady=(0, 7))
recent_frame = tk.Frame(sidebar, bg=SIDEBAR)
recent_frame.pack(fill=tk.X, padx=4)
refresh_sidebar_history()


# ---------- HEADER ----------

header = tk.Frame(
    chat_page,
    bg=BG
)

header.pack(
    fill=tk.X,
    padx=25,
    pady=(20, 5)
)


brand = tk.Frame(
    header,
    bg=BG
)

brand.pack(
    side=tk.LEFT
)


logo = tk.Label(
    brand,
    text="✦",
    bg=BG,
    fg=ACCENT,
    font=("Segoe UI", 25, "bold")
)

logo.pack(
    side=tk.LEFT,
    padx=(0, 8)
)


title = tk.Label(
    brand,
        text="Maanav Vyom",
    bg=BG,
    fg=TEXT,
    font=("Segoe UI", 22, "bold")
)

title.pack(
    side=tk.LEFT
)


active_provider = (
    "GPT-6 ASTRA" if os.getenv("OPENAI_API_KEY") and find_spec("openai")
    else "GEMINI" if os.getenv("GEMINI_API_KEY")
    else "LOCAL AI"
)
status = tk.Label(
    header,
    text="● " + active_provider + " READY",
    bg=BG,
    fg=GREEN,
    font=("Segoe UI", 10, "bold")
)

status.pack(
    side=tk.RIGHT,
    pady=8
)

voice_state = tk.Label(
    header,
    text="MIC · READY",
    bg=BG,
    fg=CYAN,
    font=("Segoe UI", 8, "bold")
)
voice_state.pack(side=tk.RIGHT, padx=(0, 18), pady=8)

wispr_input_button = tk.Button(
    header,
    text="WISPR INPUT",
    font=("Segoe UI", 8, "bold"),
    bg=BG,
    fg=TEXT_DIM,
    activebackground=BG,
    activeforeground=ACCENT,
    relief=tk.FLAT,
    borderwidth=0,
    cursor="hand2",
    command=focus_wispr_input,
)
wispr_input_button.pack(side=tk.RIGHT, padx=(0, 12), pady=8)


voice_button = tk.Button(
    header,
    text="VOICE ON",
    font=("Segoe UI", 9, "bold"),
    bg=BG,
    fg=GREEN,
    activebackground=BG,
    activeforeground=TEXT,
    relief=tk.FLAT,
    borderwidth=0,
    cursor="hand2",
    command=toggle_voice
)

voice_button.pack(
    side=tk.RIGHT,
    padx=(0, 20),
    pady=8
)

stop_voice_button = tk.Button(
    header,
    text="STOP SPEAKING",
    font=("Segoe UI", 9, "bold"),
    bg=BG,
    fg=TEXT,
    activebackground=BG,
    activeforeground=TEXT,
    relief=tk.FLAT,
    borderwidth=0,
    cursor="hand2",
    command=stop_speaking
)

stop_voice_button.pack(
    side=tk.RIGHT,
    padx=(0, 15),
    pady=8
)

expressive_voice_buttons.append(tk.Button(
    header, command=toggle_expressive_voice,
    bg=BG, activebackground=BG, activeforeground=ACCENT,
    relief=tk.FLAT, borderwidth=0, cursor="hand2",
    font=("Segoe UI", 8, "bold")
))
expressive_voice_buttons[-1].pack(side=tk.RIGHT, padx=(0, 12), pady=8)
refresh_expressive_voice_buttons()

# ---------- WELCOME ----------

welcome = tk.Frame(
    chat_page,
    bg=BG
)

welcome.pack(
    fill=tk.X,
    padx=45,
    pady=(35, 15)
)


welcome_title = tk.Label(
    welcome,
    text="Good to see you.",
    bg=BG,
    fg=TEXT,
    font=("Segoe UI", 28, "bold")
)

welcome_title.pack(
    anchor="w"
)


welcome_text = tk.Label(
    welcome,
    text="Your personal AI is standing by. Ask, show, or tell me what you need.",
    bg=BG,
    fg=MUTED,
    font=("Segoe UI", 13)
)

welcome_text.pack(
    anchor="w",
    pady=(4, 0)
)

orb = tk.Canvas(welcome, width=210, height=144, bg=BG, highlightthickness=0)
orb.place(relx=1.0, rely=0.5, anchor="e")

# A quiet two-arm star field that accelerates while the mic captures speech
# or Maanav's TTS is playing. The particles remain visible on the black theme.
galaxy_stars = [
    {"angle": index * 2.39996, "radius": 12 + (index % 17) * 4.1,
     "speed": 0.72 + (index % 5) * 0.12, "size": 1 if index % 4 else 2}
    for index in range(34)
]

def animate_orb():
    phase = time.monotonic()
    state = voice_activity
    active = state != "idle"
    speed = 4.5 if state == "thinking" else (2.6 if state == "speaking" else (2.3 if active else 0.12))
    center_x, center_y = 105, 72
    orb.delete("all")

    # Dim orbit lines and a soft central glow establish the galaxy silhouette.
    for radius in (21, 37, 53, 69):
        orb.create_oval(
            center_x - radius, center_y - radius * 0.46,
            center_x + radius, center_y + radius * 0.46,
            outline="#292316" if active else "#1b1913", width=1
        )

    for index, star in enumerate(galaxy_stars):
        angle = star["angle"] + phase * star["speed"] * speed
        radius = star["radius"]
        arm_curve = math.sin(radius * 0.075 + angle * 2.0) * 4
        x = center_x + math.cos(angle) * (radius + arm_curve)
        wave = math.sin(angle * 2.0 + phase * (9 if state == "thinking" else 4)) * (7 if state != "idle" else 0)
        y = center_y + math.sin(angle) * (radius * 0.48 + arm_curve * 0.4) + wave
        bright = (index + int(phase * (7 if active else 1))) % 6 == 0
        color = ACCENT if active and bright else ("#aa9146" if bright else ACCENT_DARK)
        size = star["size"] + (1 if active and bright else 0)
        orb.create_oval(x, y, x + size, y + size, fill=color, outline="")

    pulse = 1 + int((1 + math.sin(phase * (7.0 if state == "speaking" else 4.0 if active else 1.0))) * (3 if active else 1))
    orb.create_oval(
        center_x - 5 - pulse, center_y - 3 - pulse,
        center_x + 5 + pulse, center_y + 3 + pulse,
        fill="#211d12", outline=ACCENT if active else ACCENT_DARK, width=1
    )
    orb.create_oval(center_x - 2, center_y - 2, center_x + 2, center_y + 2,
                    fill=ACCENT if active else "#9a8039", outline="")
    root.after(45 if active else 130, animate_orb)

animate_orb()


# ---------- SUGGESTIONS ----------

suggestions = tk.Frame(
    chat_page,
    bg=BG
)

suggestions.pack(
    fill=tk.X,
    padx=45,
    pady=(5, 15)
)


suggestion("Give me an idea")
suggestion("Help me study")
suggestion("Help me code")


# ---------- CHAT ----------

chat_frame = tk.Frame(
    chat_page,
    bg=PANEL
)

chat_frame.pack_forget()
chat_frame.configure(height=280)
chat_frame.pack_propagate(False)

chat_heading = tk.Frame(chat_frame, bg=PANEL)
chat_heading.pack(fill=tk.X, padx=22, pady=(16, 4))
tk.Label(chat_heading, text="CHAT", bg=PANEL, fg=ACCENT,
         font=("Segoe UI", 9, "bold")).pack(anchor="w")
chat_subtitle = tk.Label(chat_heading, text="Your conversation with Maanav", bg=PANEL, fg=MUTED,
                         font=("Segoe UI", 11))
chat_subtitle.pack(anchor="w", pady=(3, 0))

chat = scrolledtext.ScrolledText(
    chat_frame,
    wrap=tk.WORD,
    font=("Segoe UI", 11),
    bg=PANEL,
    fg=TEXT,
    insertbackground=TEXT,
    selectbackground=ACCENT_DARK,
    relief=tk.FLAT,
    borderwidth=0,
    padx=20,
    pady=20,
    state="disabled"
)

chat.pack(
    fill=tk.BOTH,
    expand=True, padx=8, pady=(5, 8)
)

chat.tag_config("assistant_label", foreground=CYAN, font=("Segoe UI", 9, "bold"))
chat.tag_config("assistant_message", foreground=TEXT, font=("Segoe UI", 11))
chat.tag_config("user_label", foreground=GREEN, font=("Segoe UI", 9, "bold"))
chat.tag_config("user_message", foreground="#d4ceb8", font=("Segoe UI", 11))
chat.tag_config("thinking", foreground=MUTED, font=("Segoe UI", 10, "italic"))


# ---------- INPUT ----------

input_area = tk.Frame(
    chat_page,
    bg=BG
)

input_area.pack(
    side=tk.BOTTOM,
    fill=tk.X,
    padx=30,
    pady=(0, 8)
)

# Pack the composer before the expanding conversation panel. Tk's packer
# allocates space in packing order; keeping the composer first guarantees it
# remains on-screen even when the window is short or the chat grows.
chat_frame.pack(
    side=tk.TOP,
    fill=tk.BOTH,
    expand=False,
    padx=(24, 30),
    pady=(5, 12)
)

attachment_bar = tk.Frame(input_area, bg=BG)
attachment_bar.pack(fill=tk.X)

input_box = tk.Frame(
    input_area,
    bg=CARD
)

input_box.pack(
    fill=tk.X
)


attach_button = tk.Button(
    input_box, text="+ IMAGE", font=("Segoe UI", 9, "bold"),
    bg=CARD, fg=CYAN, activebackground=CARD, activeforeground=TEXT,
    relief=tk.FLAT, borderwidth=0, cursor="hand2", command=choose_image,
    padx=12, pady=12
)
attach_button.pack(side=tk.LEFT, padx=(8, 0))

mic_button = tk.Button(
    header, text=f"MIC · {short_microphone_label(MIC_DEVICE_NAME)}", font=("Segoe UI", 8, "bold"),
    bg=BG, fg=CYAN, activebackground=BG, activeforeground=TEXT,
    relief=tk.FLAT, borderwidth=0, cursor="hand2", command=choose_microphone
)
mic_button.pack(side=tk.RIGHT, padx=(0, 14), pady=8)

memory_button = tk.Button(
    header, text="MEMORY", font=("Segoe UI", 9, "bold"),
    bg=BG, fg=MUTED, activebackground=BG, activeforeground=CYAN,
    relief=tk.FLAT, borderwidth=0, cursor="hand2", command=show_saved_memory
)
memory_button.pack(side=tk.RIGHT, padx=(0, 18), pady=8)

entry = tk.Text(
    input_box,
    font=("Segoe UI", 12),
    bg=CARD,
    fg=TEXT,
    insertbackground=TEXT,
    relief=tk.FLAT,
    borderwidth=0,
    height=2,
    wrap=tk.WORD
)

entry.pack(
    side=tk.LEFT,
    fill=tk.BOTH,
    expand=True,
    padx=(18, 5),
    pady=6
)


input_scroll = tk.Scrollbar(
    input_box,
    command=entry.yview
)

input_scroll.pack(
    side=tk.LEFT,
    fill=tk.Y,
    pady=10
)


entry.config(
    yscrollcommand=input_scroll.set
)


send_button = tk.Button(
    input_box,
    text="➤",
    font=("Segoe UI", 16, "bold"),
    bg=ACCENT,
    fg="white",
    activebackground="#aa8e3e",
    activeforeground="white",
    relief=tk.FLAT,
    borderwidth=0,
    cursor="hand2",
    command=send_message,
    padx=18,
    pady=5
)

send_button.pack(
    side=tk.RIGHT,
    padx=7,
    pady=7
)


# =========================================================
# ENTER KEY
# =========================================================

def enter_pressed(event=None):

    if event.state & 0x0001:

        entry.insert(
            tk.INSERT,
            "\n"
        )

        return "break"

    send_message()

    return "break"


entry.bind(
    "<Return>",
    enter_pressed
)


# =========================================================
# WELCOME MESSAGE
# =========================================================

chat.config(
    state="normal"
)

chat.insert(
    tk.END,
    "MAANAV  /  PERSONAL ASSISTANT\n",
    "assistant_title"
)

chat.insert(
    tk.END,
    "Systems ready. I can answer questions, open apps, find files, manage sound, remember useful facts, and analyse an image you attach.\n\n",
    "assistant_text"
)

chat.tag_config(
    "assistant_title",
    font=("Segoe UI", 14, "bold"),
    foreground=ACCENT
)

chat.tag_config(
    "assistant_text",
    font=("Segoe UI", 11),
    foreground=TEXT
)

chat.config(
    state="disabled"
)

if load_history(limit=80):
    open_chat_session(get_active_session_id())
else:
    refresh_sidebar_history()

entry.focus()
focus_galaxy()


# =========================================================
# START VOICE ASSISTANT
# =========================================================

def run_galaxy_demo(index=0):
    """Cycle real UI activity states so animation is demonstrable without audio hardware."""
    sequence = (
        ("listening", "Listening: say a wake phrase", 1500),
        ("thinking", "Thinking: routing a request", 1500),
        ("speaking", "Speaking: Maanav reply", 2200),
        ("idle", "Demo complete", 1000),
    )
    activity, label, duration = sequence[index]
    set_voice_activity(activity)
    set_voice_status(label.upper())
    if activity == "speaking":
        threading.Thread(
            target=speak,
            args=("This is a visual demo of Maanav's listening, thinking, and speaking states.",),
            daemon=True,
        ).start()
    if index + 1 < len(sequence):
        root.after(duration, run_galaxy_demo, index + 1)
    else:
        root.after(duration, lambda: set_voice_status("READY — SAY WAKE UP"))


if "--ui-smoke-test" in sys.argv:
    # Exercise both page layouts without taking control of the microphone.
    pass
elif "--galaxy-demo" in sys.argv:
    # The demo simulates UI states instead of opening a microphone stream.
        root.after(500, run_galaxy_demo)
else:
        threading.Thread(
        target=voice_assistant_loop,
        daemon=True
    ).start()


# =========================================================
# START GUI
# =========================================================

def close_maanav():
    global voice_loop_running, hand_tracker
    voice_loop_running = False
    stop_speaking()
    if hand_tracker is not None:
        hand_tracker.stop()
        hand_tracker = None
    if "desktop_companion" in globals():
        desktop_companion.close()
    try:
        root.destroy()
    except tk.TclError:
        pass


from desktop_companion import DesktopCompanion

desktop_companion = DesktopCompanion(
    root, ask_ai=run_assistant_request, speak=speak, listen=listen_from_microphone,
    session_id=get_active_session_id, add_turn=add_turn, load_history=load_history,
    memory_action=memory.get_all_memory, offline_voice=OFFLINE_VOICE_ONLY,
    call_mode=set_voice_call_mode, request_begin=_begin_chat_request,
    on_turn=lambda session_id: root.after(0, _refresh_active_chat_display, session_id),
    dictation_mode=set_wispr_dictation_active,
    request_finish=_finish_chat_request,
    on_hotkey=activate_voice_shortcut,
    computer_task=run_computer_task,
    pet_sounds_enabled=lambda: NESUKO_SOUNDS_ENABLED,
    wander_enabled=lambda: NESUKO_WANDER_ENABLED,
)

root.protocol("WM_DELETE_WINDOW", close_maanav)

if "--ui-smoke-test" in sys.argv:
    root.update_idletasks()
    companion_ok = (
        desktop_companion.pet.winfo_exists()
        and desktop_companion.chat_window.winfo_exists()
        and bool(desktop_companion.pet.attributes("-topmost"))
        and desktop_companion.chat_window.state() == "withdrawn"
        and desktop_companion.pet_image is not None
        and hasattr(desktop_companion, "use_pc_button")
        and desktop_companion.use_pc_button.cget("text") == "USE PC"
        and desktop_companion.pet.winfo_width() >= 80
        and desktop_companion.pet.winfo_height() >= 135
        and not hasattr(desktop_companion, "transcript")
    )
    desktop_companion.show_chat(focus=True)
    root.update_idletasks()
    companion_ok = companion_ok and desktop_companion.chat_window.state() != "withdrawn"
    desktop_companion.toggle_pin()
    companion_ok = companion_ok and desktop_companion.pinned
    desktop_companion.toggle_pin()
    desktop_companion.minimize_chat()
    root.update_idletasks()
    companion_ok = companion_ok and desktop_companion.chat_window.state() == "withdrawn"
    prior_voice_enabled = voice_enabled
    desktop_companion.focus_for_wispr()
    wispr_paused = _wispr_dictation_active.is_set() and desktop_companion.dictation_active
    desktop_companion._set_dictation_active(False)
    root.update_idletasks()
    wispr_resumed = (
        not _wispr_dictation_active.is_set()
        and wispr_input_button.cget("text") == "WISPR INPUT"
        and wispr_input_button.winfo_x() + wispr_input_button.winfo_width() <= header.winfo_width()
    )
    earlier_request = _begin_chat_request()
    current_request = _begin_chat_request()
    stale_finish_ignored = not _finish_chat_request(earlier_request)
    still_paused_for_current = _voice_input_paused.is_set()
    current_finish_applied = _finish_chat_request(current_request)
    request_pause_ok = (
        stale_finish_ignored and still_paused_for_current and current_finish_applied
        and not _voice_input_paused.is_set()
    )
    activate_voice_shortcut()
    hotkey_action_ok = (
        _manual_voice_trigger.is_set()
        and not _wispr_dictation_active.is_set()
        and chat_page.winfo_manager() == "pack"
    )
    _manual_voice_trigger.clear()  # UI smoke test deliberately avoids the microphone.
    desktop_companion.set_call_active(True)
    call_started = desktop_companion.call_active and voice_call_active and voice_enabled
    desktop_companion.set_call_active(False)
    call_restored = not desktop_companion.call_active and not voice_call_active and voice_enabled == prior_voice_enabled
    companion_ok = companion_ok and wispr_paused and call_started and call_restored
    print("Floating companion, open/pin/minimize/call restore: ", "PASS" if companion_ok else "FAIL")
    print("Approval-gated computer-use control: ", "PASS" if companion_ok else "FAIL")
    print("Wispr handoff/pause/resume and header fit: ", "PASS" if wispr_paused and wispr_resumed else "FAIL")
    print("Direct Maanav voice shortcut: ", "PASS" if hotkey_action_ok else "FAIL")
    print("Voice pause respects newest chat request: ", "PASS" if request_pause_ok else "FAIL")
    if os.name == "nt" and not desktop_companion._hotkey_registered:
        print("Ctrl+Alt+Space registration: SKIPPED (another app may own the system shortcut)")
    else:
        print("Ctrl+Alt+Space registration:", "PASS" if desktop_companion._hotkey_registered else "SKIPPED")
    print(
        "Mouse Button 5 registration:",
        "PASS" if desktop_companion._mouse_button_5_registered else "SKIPPED",
    )
    focus_chat()
    root.update_idletasks()
    composer_ok = (
        chat_page.winfo_manager() == "pack"
        and input_area.winfo_y() + input_area.winfo_height() <= chat_page.winfo_height()
        and entry.winfo_height() >= 20
    )
    print(
        "Chat section/composer layout:",
        "PASS" if composer_ok else "FAIL",
        f"(composer bottom {input_area.winfo_y() + input_area.winfo_height()} / page height {chat_page.winfo_height()})",
    )
    open_personalization_dialog()
    root.update_idletasks()
    personalization_dialog = next(
        (window for window in root.winfo_children()
         if isinstance(window, tk.Toplevel) and window.title() == "Maanav Vyom · Settings"),
        None,
    )
    personalization_ok = (
        personalization_dialog is not None and personalization_dialog.winfo_exists()
    )
    if personalization_dialog is not None:
        personalization_dialog.destroy()
    print("Maanav Vyom customization panel: ", "PASS" if personalization_ok else "FAIL")
    _smoke_search_root = Path(_ui_test_storage.name) / "search-scope"
    _smoke_search_root.mkdir(exist_ok=True)
    (_smoke_search_root / "voice-search-check.md").write_text(
        "Maanav local content search demonstration phrase.", encoding="utf-8"
    )
    _old_search_roots = FILE_SEARCH_ROOTS
    FILE_SEARCH_ROOTS = [str(_smoke_search_root)]
    _search_demo = ask_ai("search inside my files for local content search demonstration")
    file_search_ok = "voice-search-check.md" in _search_demo and "demonstration phrase" in _search_demo
    FILE_SEARCH_ROOTS = _old_search_roots
    print("Voice command local file-content search:", "PASS" if file_search_ok else "FAIL")
    focus_galaxy()
    root.update_idletasks()
    galaxy_ok = (
        galaxy_page.winfo_manager() == "pack"
        and galaxy_canvas.winfo_width() >= 300
        and galaxy_canvas.winfo_height() >= 250
        and len(main_galaxy_stars) == 2100
    )
    rendered_states = []
    frame_times = []
    for demo_state in ("listening", "thinking", "speaking", "idle"):
        set_voice_activity(demo_state)
        animate_main_galaxy()  # warm the canvas and apply the state color once
        state_count = len(galaxy_canvas_items)
        for _ in range(8):
            frame_start = time.perf_counter()
            animate_main_galaxy()
            frame_times.append((time.perf_counter() - frame_start) * 1000)
        rendered_states.append(state_count)
    galaxy_ok = galaxy_ok and all(count == 2100 for count in rendered_states)
    print(
        "Galaxy section/canvas/star field:",
        "PASS" if galaxy_ok else "FAIL",
        f"({len(main_galaxy_stars)} stars, canvas {galaxy_canvas.winfo_width()}x{galaxy_canvas.winfo_height()}, states {rendered_states})",
    )
    print(
        "Galaxy redraw time:",
        f"{sum(frame_times) / len(frame_times):.1f} ms average, "
        f"{sorted(frame_times)[len(frame_times) // 2]:.1f} ms median, {max(frame_times):.1f} ms max",
    )
    root.destroy()
    raise SystemExit(0 if composer_ok and galaxy_ok and companion_ok and wispr_resumed and request_pause_ok and hotkey_action_ok and personalization_ok and file_search_ok else 1)

root.mainloop()

