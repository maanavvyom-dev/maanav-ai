"""Opt-in expressive Gemini speech with cancellable local audio playback."""
import argparse
import base64
import io
import json
import os
import threading
import urllib.request
import wave
from voice_expression import delivery_for


MODEL = "gemini-3.8-flash-tts"
API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DELIVERY_STYLES = {
    # Google recommends short style prompts for turn-level adjustments.
    "warm": "Warm, lively, present, and sincerely conversational, with gentle emphasis.",
    "reassuring": "Gentle, caring, unhurried, and emotionally present.",
    "upbeat": "Joyful and bright, with buoyant rhythm and genuine delight.",
    "curious": "Warmly curious and animated, with a light, inviting rise.",
    "playful": "Lightly playful, charming, and a little mischievous; never childish.",
    "serious": "Serious, composed, and gently concerned, with measured, clear emphasis.",
    "confident": "Assured and encouraging, with grounded energy and decisive clarity.",
}
DEFAULT_STYLE = DELIVERY_STYLES["warm"]


def build_payload(text, voice="Kore", style=None):
    style = style or DELIVERY_STYLES[delivery_for(text)]
    return {
        "model": MODEL,
        "input": [{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": str(text),
                "annotations": [{"type": "speech_metadata", "style": style}],
            }],
        }],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }


def extract_wav(response_json):
    """Extract the WAV data from the completed Interactions audio response."""
    for step in reversed(response_json.get("steps", [])):
        for item in step.get("content", []):
            encoded = item.get("data")
            if item.get("type") == "audio" and encoded:
                raw = base64.b64decode(encoded, validate=True)
                with wave.open(io.BytesIO(raw), "rb") as audio:
                    if audio.getcomptype() != "NONE" or audio.getsampwidth() not in (1, 2, 3, 4):
                        raise ValueError("Gemini returned an unsupported audio encoding")
                    if audio.getnchannels() not in (1, 2) or audio.getframerate() <= 0:
                        raise ValueError("Gemini returned invalid audio format details")
                return raw
    raise ValueError("Gemini response did not contain audio")


class GeminiExpressiveTTS:
    def __init__(self, api_key_getter=lambda: os.getenv("GEMINI_API_KEY"),
                 audio_module=None, opener=urllib.request.urlopen, timeout=45):
        self._api_key_getter = api_key_getter
        self._audio_module = audio_module
        self._opener = opener
        self._timeout = timeout
        self._active_lock = threading.Lock()
        self._active_stream = None

    def _generate(self, text):
        key = self._api_key_getter()
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        request = urllib.request.Request(
            API_URL,
            data=json.dumps(build_payload(text), ensure_ascii=False).encode("utf-8"),
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            method="POST",
        )
        with self._opener(request, timeout=self._timeout) as response:
            return extract_wav(json.loads(response.read().decode("utf-8")))

    def speak(self, text, is_cancelled=lambda: False):
        """Generate and play expressive speech. Returns false if canceled/failed."""
        if is_cancelled():
            return False
        raw = self._generate(text)
        if is_cancelled():
            return False
        audio_module = self._audio_module
        if audio_module is None:
            import pyaudiowpatch as audio_module
        with wave.open(io.BytesIO(raw), "rb") as audio_file:
            audio = audio_module.PyAudio()
            stream = None
            try:
                stream = audio.open(
                    format=audio.get_format_from_width(audio_file.getsampwidth()),
                    channels=audio_file.getnchannels(),
                    rate=audio_file.getframerate(),
                    output=True,
                    frames_per_buffer=2048,
                )
                with self._active_lock:
                    self._active_stream = stream
                while True:
                    if is_cancelled():
                        return False
                    chunk = audio_file.readframes(2048)
                    if not chunk:
                        return True
                    stream.write(chunk)
            finally:
                try:
                    if stream is not None:
                        with self._active_lock:
                            if self._active_stream is stream:
                                self._active_stream = None
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
        """Interrupt active audio playback; an in-flight HTTP request is discarded after it returns."""
        with self._active_lock:
            stream = self._active_stream
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="Play a short expressive Maanav voice sample.")
    parser.add_argument("--demo", action="store_true", help="Speak a short warm voice demonstration")
    args = parser.parse_args()
    if not args.demo:
        parser.print_help()
        return 2
    speaker = GeminiExpressiveTTS()
    try:
        worked = True
        for line in (
            "Hey Maanav, I am glad you are here.",
            "I'm here with you. Take your time; we can work through it together.",
            "That's fantastic! You did it, and you should feel proud of that.",
            "Nice one. I had a feeling you'd pull that off!",
            "Would you like to try one more idea? I'm curious what you'll make next.",
        ):
            if not speaker.speak(line):
                worked = False
                break
    except Exception as error:
        print("Expressive voice demo failed:", error)
        return 1
    if not worked:
        print("Expressive voice demo was canceled or could not play.")
        return 1
    print("Expressive Maanav voice demo completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
