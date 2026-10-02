import base64
import io
import json
import unittest
import wave

from expressive_tts import GeminiExpressiveTTS, build_payload, extract_wav
from voice_output import TTSController
from voice_expression import delivery_for


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\x00\x00" * 480)
    return buffer.getvalue()


class ExpressiveVoiceTests(unittest.TestCase):
    def test_delivery_style_tracks_emotion_without_changing_words(self):
        self.assertEqual(delivery_for("I'm here for you. Take your time."), "reassuring")
        self.assertEqual(delivery_for("Congratulations, you did it!"), "upbeat")
        self.assertEqual(delivery_for("Would you like to continue?"), "curious")
        self.assertEqual(delivery_for("Here is the summary."), "warm")
        self.assertEqual(delivery_for("Nice one, that was a fun little adventure."), "playful")
        self.assertEqual(delivery_for("Please stop. This is urgent."), "serious")
        self.assertEqual(delivery_for("I can do that. Here's the plan."), "confident")

    def test_payload_marks_speech_style_without_modifying_transcript(self):
        payload = build_payload("Hello, Maanav.")
        text = payload["input"][0]["content"][0]
        self.assertEqual(text["text"], "Hello, Maanav.")
        self.assertIn("warm", text["annotations"][0]["style"].lower())
        self.assertEqual(payload["response_format"], {"type": "audio"})

    def test_payload_adds_contextual_emotion_direction(self):
        style = build_payload("Congratulations, you did it!")["input"][0]["content"][0]["annotations"][0]["style"]
        self.assertIn("joyful", style.lower())
        playful = build_payload("Nice one, that was a fun little adventure!")
        self.assertIn("playful", playful["input"][0]["content"][0]["annotations"][0]["style"].lower())
        serious = build_payload("Please stop. This is urgent.")
        self.assertIn("concerned", serious["input"][0]["content"][0]["annotations"][0]["style"].lower())

    def test_extracts_valid_interactions_wav(self):
        raw = wav_bytes()
        response = {"steps": [{"content": [{
            "type": "audio", "data": base64.b64encode(raw).decode("ascii"),
        }]}]}
        self.assertEqual(extract_wav(response), raw)

    def test_rejects_missing_or_invalid_audio(self):
        with self.assertRaises(ValueError):
            extract_wav({"steps": []})
        with self.assertRaises(Exception):
            extract_wav({"steps": [{"content": [{"type": "audio", "data": "not-base64"}]}]})

    def test_generate_posts_api_key_and_friendly_style(self):
        raw = wav_bytes()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"steps": [{"content": [{
                    "type": "audio", "data": base64.b64encode(raw).decode("ascii"),
                }]}]}).encode()

        def opener(request, timeout):
            self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
            self.assertEqual(timeout, 45)
            self.assertEqual(json.loads(request.data)["model"], "gemini-3.8-flash-tts")
            return Response()

        tts = GeminiExpressiveTTS(api_key_getter=lambda: "test-key", opener=opener)
        self.assertEqual(tts._generate("Hello"), raw)

    def test_cancelled_before_generation_does_not_send_request(self):
        calls = []
        tts = GeminiExpressiveTTS(api_key_getter=lambda: "key", opener=lambda *_a, **_k: calls.append(1))
        self.assertFalse(tts.speak("Hello", is_cancelled=lambda: True))
        self.assertEqual(calls, [])

    def test_preferred_emotional_voice_runs_before_local_sapi(self):
        local_calls = []
        emotional_calls = []
        controller = TTSController(
            engine_factory=lambda: local_calls.append("local"),
            voice_chooser=lambda _voices, _text: None,
            is_enabled=lambda: True,
            get_activity=lambda: "idle",
            set_activity=lambda _activity: None,
            preferred_speaker=lambda text, is_cancelled: emotional_calls.append(text) or True,
            is_preferred_enabled=lambda: True,
        )
        self.assertTrue(controller.speak("You have got this."))
        self.assertEqual(emotional_calls, ["You have got this."])
        self.assertEqual(local_calls, [])

    def test_preferred_voice_failure_falls_back_to_local_speech(self):
        calls = []

        class LocalEngine:
            def getProperty(self, name):
                return [] if name == "voices" else None

            def setProperty(self, *_args):
                pass

            def say(self, text):
                calls.append(("say", text))

            def runAndWait(self):
                calls.append(("played", True))

            def stop(self):
                pass

        controller = TTSController(
            engine_factory=LocalEngine,
            voice_chooser=lambda _voices, _text: None,
            is_enabled=lambda: True,
            get_activity=lambda: "idle",
            set_activity=lambda _activity: None,
            preferred_speaker=lambda *_args, **_kwargs: False,
            is_preferred_enabled=lambda: True,
        )
        self.assertTrue(controller.speak("A reassuring reply."))
        self.assertEqual(calls, [("say", "A reassuring reply."), ("played", True)])

    def test_cancellation_during_preferred_voice_never_starts_local_speech(self):
        local_calls = []

        def cancel_cloud(is_cancelled):
            self.assertFalse(is_cancelled())
            cancellation.set()
            self.assertTrue(is_cancelled())
            return False

        import threading
        cancellation = threading.Event()
        controller = TTSController(
            engine_factory=lambda: local_calls.append("local"),
            voice_chooser=lambda _voices, _text: None,
            is_enabled=lambda: True,
            get_activity=lambda: "idle",
            set_activity=lambda _activity: None,
            preferred_speaker=lambda _text, is_cancelled: cancel_cloud(is_cancelled),
            is_preferred_enabled=lambda: True,
        )
        self.assertFalse(controller.speak("Stop this reply.", cancellation))
        self.assertEqual(local_calls, [])

    def test_stop_calls_active_preferred_speaker_stopper(self):
        activity = ["speaking"]
        stopped = []
        controller = TTSController(
            engine_factory=lambda: None,
            voice_chooser=lambda _voices, _text: None,
            is_enabled=lambda: True,
            get_activity=lambda: activity[0],
            set_activity=lambda value: activity.__setitem__(0, value),
            preferred_stopper=lambda: stopped.append(True),
        )
        controller.stop()
        self.assertEqual(stopped, [True])
        self.assertEqual(activity[0], "idle")


if __name__ == "__main__":
    unittest.main()
