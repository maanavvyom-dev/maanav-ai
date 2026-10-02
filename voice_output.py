"""Serialized, cancellable offline text-to-speech controller."""
import re
import threading
import unicodedata
from voice_expression import PYTTSX3_RATES, delivery_for


class TTSController:
    def __init__(self, engine_factory, voice_chooser, is_enabled, get_activity, set_activity,
                 on_error=None, fallback_speaker=None, preferred_speaker=None,
                 is_preferred_enabled=lambda: False, preferred_stopper=None):
        self._engine_factory = engine_factory
        self._voice_chooser = voice_chooser
        self._is_enabled = is_enabled
        self._get_activity = get_activity
        self._set_activity = set_activity
        self._on_error = on_error
        self._fallback_speaker = fallback_speaker
        self._preferred_speaker = preferred_speaker
        self._is_preferred_enabled = is_preferred_enabled
        self._preferred_stopper = preferred_stopper
        self._utterance_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._generation = 0
        self._engine = None

    @staticmethod
    def _clean_text(text):
        # Keep accented and non-Latin letters so Windows voices can speak the
        # same words shown in chat. Remove control/format characters and emoji
        # that local speech engines commonly pronounce as noise.
        cleaned = []
        for character in str(text):
            category = unicodedata.category(character)
            if category == "Cc":
                cleaned.append(" ")
            elif category not in {"Cs", "Co", "Cn", "So", "Sk"}:
                cleaned.append(character)
        value = "".join(cleaned)
        value = re.sub(r"\*\*(.*?)\*\*", r"\1", value)
        value = re.sub(r"\*(.*?)\*", r"\1", value)
        value = re.sub(r"`(.*?)`", r"\1", value)
        value = re.sub(r"#+\s*", "", value)
        return re.sub(r"\s+", " ", value).strip()

    def speak(self, text, cancel_event=None):
        speech_text = self._clean_text(text)
        if not speech_text or not self._is_enabled() or (cancel_event and cancel_event.is_set()):
            return False
        previous_activity = self._get_activity()
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            active = self._engine
        if active is not None:
            try:
                active.stop()
            except Exception:
                pass
        self._stop_preferred()

        with self._utterance_lock:
            with self._state_lock:
                if (generation != self._generation or not self._is_enabled()
                        or (cancel_event and cancel_event.is_set())):
                    return False
            engine = None
            try:
                if self._preferred_speaker is not None and self._is_preferred_enabled():
                    self._set_activity("speaking")
                    try:
                        preferred_worked = self._preferred_speaker(
                            speech_text,
                            is_cancelled=lambda: (
                                generation != self._generation
                                or not self._is_enabled()
                                or (cancel_event and cancel_event.is_set())
                            ),
                        )
                    except Exception as preferred_error:
                        print("Preferred voice failed; trying local speech:", preferred_error)
                        preferred_worked = False
                    if preferred_worked:
                        return True
                    with self._state_lock:
                        if (generation != self._generation or not self._is_enabled()
                                or (cancel_event and cancel_event.is_set())):
                            return False
                engine = self._engine_factory()
                with self._state_lock:
                    if generation != self._generation:
                        engine.stop()
                        return False
                    self._engine = engine
                voices = engine.getProperty("voices")
                voice = self._voice_chooser(voices, speech_text)
                if voice is not None:
                    engine.setProperty("voice", voice.id)
                engine.setProperty("rate", PYTTSX3_RATES[delivery_for(speech_text)])
                engine.setProperty("volume", 0.9)
                if cancel_event and cancel_event.is_set():
                    return False
                engine.say(speech_text)
                with self._state_lock:
                    should_run = (
                        generation == self._generation
                        and self._is_enabled()
                        and not (cancel_event and cancel_event.is_set())
                    )
                if should_run:
                    self._set_activity("speaking")
                    engine.runAndWait()
                    with self._state_lock:
                        return generation == self._generation
                return False
            except Exception as error:
                print("Voice error:", error)
                fallback_worked = False
                if (self._fallback_speaker is not None and self._is_enabled()
                        and not (cancel_event and cancel_event.is_set())):
                    try:
                        with self._state_lock:
                            still_current = generation == self._generation
                        if still_current:
                            self._set_activity("speaking")
                            fallback_worked = bool(self._fallback_speaker(
                                speech_text,
                                is_cancelled=lambda: (
                                    generation != self._generation
                                    or not self._is_enabled()
                                    or (cancel_event and cancel_event.is_set())
                                ),
                            ))
                    except Exception as fallback_error:
                        print("Expressive voice fallback error:", fallback_error)
                if not fallback_worked and self._on_error is not None:
                    try:
                        self._on_error(error)
                    except Exception as callback_error:
                        print("Voice error callback failed:", callback_error)
                return False
            finally:
                if engine is not None:
                    try:
                        engine.stop()
                    except Exception:
                        pass
                    with self._state_lock:
                        if self._engine is engine:
                            self._engine = None
                with self._state_lock:
                    current = generation == self._generation
                if current and self._get_activity() == "speaking":
                    self._set_activity("listening" if previous_activity == "listening" else "idle")

    def stop(self):
        with self._state_lock:
            self._generation += 1
            engine = self._engine
        if engine is not None:
            try:
                engine.stop()
            except Exception as error:
                print("[TTS STOP ERROR]", error)
        self._stop_preferred()
        if self._get_activity() == "speaking":
            self._set_activity("idle")

    def _stop_preferred(self):
        if self._preferred_stopper is not None:
            try:
                self._preferred_stopper()
            except Exception as error:
                print("Preferred voice stop error:", error)
