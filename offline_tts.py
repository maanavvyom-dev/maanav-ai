"""Optional local Piper speech backend; never downloads a voice at startup."""
import importlib.util
import json
import os
import threading
import unicodedata
from pathlib import Path
from voice_expression import PIPER_PROFILES, delivery_for


DEFAULT_MODEL = Path(__file__).resolve().parent / "models" / "piper" / "en_US-lessac-medium.onnx"


class OfflinePiperTTS:
    def __init__(self, model_path=None, audio_module=None, piper_module=None):
        configured = os.getenv("MAANAV_PIPER_MODEL")
        self.model_path = Path(model_path or configured or DEFAULT_MODEL)
        self._audio_module = audio_module
        self._piper_module = piper_module
        self._voice = None
        self._voice_lock = threading.Lock()
        self._stream_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._active_stream = None

    def available(self):
        try:
            package_ready = self._piper_module is not None or importlib.util.find_spec("piper") is not None
        except (ImportError, ValueError):
            package_ready = False
        return package_ready and self.model_path.is_file()

    def _voice_language(self):
        """Read Piper's adjacent model metadata, defaulting to the bundled English voice."""
        try:
            metadata = json.loads(Path(str(self.model_path) + ".json").read_text(encoding="utf-8"))
            voice = str(metadata.get("espeak", {}).get("voice", "en"))
            return voice.lower().replace("_", "-").split("-")[0]
        except (OSError, ValueError, TypeError, AttributeError):
            return "en"

    def supports_text(self, text):
        """Whether this Piper model's language covers the letters in the text."""
        language = self._voice_language()
        script_languages = {
            "ARABIC": {"ar", "fa", "ur"}, "ARMENIAN": {"hy"},
            "BENGALI": {"bn", "as"}, "CYRILLIC": {"ru", "uk", "bg", "sr", "mk", "be", "kk"},
            "DEVANAGARI": {"hi", "mr", "ne", "sa"}, "GEORGIAN": {"ka"},
            "GREEK": {"el"}, "GUJARATI": {"gu"}, "GURMUKHI": {"pa"},
            "HANGUL": {"ko"}, "HEBREW": {"he"}, "HIRAGANA": {"ja"},
            "KATAKANA": {"ja"}, "CJK UNIFIED IDEOGRAPH": {"zh", "ja"},
            "MALAYALAM": {"ml"}, "ORIYA": {"or"}, "SINHALA": {"si"},
            "TAMIL": {"ta"}, "TELUGU": {"te"}, "THAI": {"th"},
        }
        for character in str(text or ""):
            if unicodedata.category(character).startswith("L"):
                name = unicodedata.name(character, "")
                if name.startswith("LATIN "):
                    continue
                supported = any(
                    name.startswith(script) and language in languages
                    for script, languages in script_languages.items()
                )
                if not supported:
                    return False
        return True

    def _load_voice(self):
        if self._voice is not None:
            return self._voice
        with self._voice_lock:
            if self._voice is None:
                module = self._piper_module
                if module is None:
                    from piper import PiperVoice, SynthesisConfig
                else:
                    PiperVoice, SynthesisConfig = module.PiperVoice, module.SynthesisConfig
                self._voice = PiperVoice.load(str(self.model_path))
                self._synthesis_config_factory = SynthesisConfig
        return self._voice

    def speak(self, text, is_cancelled=lambda: False):
        if not self.available() or not str(text).strip() or is_cancelled():
            return False
        self._stop_event.clear()
        voice = self._load_voice()
        if is_cancelled() or self._stop_event.is_set():
            return False
        audio_module = self._audio_module
        if audio_module is None:
            import pyaudiowpatch as audio_module
        audio = audio_module.PyAudio()
        stream = None
        chunk_count = 0
        try:
            profile = PIPER_PROFILES[delivery_for(text)]
            syn_config = self._synthesis_config_factory(**profile)
            for chunk in voice.synthesize(str(text), syn_config=syn_config):
                if is_cancelled() or self._stop_event.is_set():
                    return False
                if stream is None:
                    stream = audio.open(
                        format=audio.get_format_from_width(chunk.sample_width),
                        channels=chunk.sample_channels,
                        rate=chunk.sample_rate,
                        output=True,
                        frames_per_buffer=2048,
                    )
                    with self._stream_lock:
                        self._active_stream = stream
                if is_cancelled() or self._stop_event.is_set():
                    return False
                stream.write(chunk.audio_int16_bytes)
                chunk_count += 1
            return chunk_count > 0
        finally:
            with self._stream_lock:
                if self._active_stream is stream:
                    self._active_stream = None
            try:
                if stream is not None:
                    try:
                        stream.stop_stream()
                    except Exception:
                        pass
                    try:
                        stream.close()
                    except Exception:
                        pass
            finally:
                audio.terminate()

    def stop(self):
        self._stop_event.set()
        with self._stream_lock:
            stream = self._active_stream
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
