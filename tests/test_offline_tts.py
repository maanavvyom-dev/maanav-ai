import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from offline_tts import OfflinePiperTTS


class FakeAudio:
    def __init__(self):
        self.written = []
        self.stopped = 0
        self.closed = 0
        self.terminated = 0
        self.on_write = None

    def PyAudio(self):
        return self

    def get_format_from_width(self, width):
        return width

    def open(self, **_kwargs):
        return self

    def write(self, data):
        self.written.append(data)
        if self.on_write:
            self.on_write()

    def stop_stream(self):
        self.stopped += 1

    def close(self):
        self.closed += 1

    def terminate(self):
        self.terminated += 1


class FakeVoice:
    def __init__(self, chunks):
        self.chunks = chunks
        self.configs = []

    def synthesize(self, _text, syn_config=None):
        self.configs.append(syn_config)
        return iter(self.chunks)


class PiperOfflineTests(unittest.TestCase):
    def test_english_piper_backend_declines_non_latin_scripts(self):
        with tempfile.TemporaryDirectory() as root:
            english = self.make_tts(root, FakeAudio(), [])
            self.assertTrue(english.supports_text("Hello, café!"))
            self.assertFalse(english.supports_text("नमस्ते"))
            self.assertFalse(english.supports_text("你好"))

    def test_configured_piper_language_metadata_enables_matching_script(self):
        with tempfile.TemporaryDirectory() as root:
            model = Path(root) / "hindi.onnx"
            model.write_bytes(b"test model placeholder")
            model.with_name(model.name + ".json").write_text(
                '{"espeak": {"voice": "hi"}}', encoding="utf-8"
            )
            tts = OfflinePiperTTS(model_path=model, piper_module=SimpleNamespace())
            self.assertTrue(tts.supports_text("नमस्ते, hello!"))
            self.assertFalse(tts.supports_text("你好"))

    def make_tts(self, root, audio, chunks):
        model_path = Path(root) / "voice.onnx"
        model_path.write_bytes(b"test model placeholder")
        fake_module = SimpleNamespace(
            PiperVoice=SimpleNamespace(load=lambda _path: FakeVoice(chunks)),
            SynthesisConfig=lambda **kwargs: kwargs,
        )
        return OfflinePiperTTS(model_path=model_path, audio_module=audio, piper_module=fake_module)

    @staticmethod
    def chunk(data):
        return SimpleNamespace(
            sample_width=2, sample_channels=1, sample_rate=22050, audio_int16_bytes=data,
        )

    def test_local_voice_streams_audio_and_releases_device(self):
        with tempfile.TemporaryDirectory() as root:
            audio = FakeAudio()
            tts = self.make_tts(root, audio, [self.chunk(b"one"), self.chunk(b"two")])
            self.assertTrue(tts.available())
            self.assertTrue(tts.speak("A warm local reply."))
            self.assertEqual(audio.written, [b"one", b"two"])
            self.assertEqual((audio.stopped, audio.closed, audio.terminated), (1, 1, 1))

    def test_offline_voice_changes_delivery_pacing_for_emotion(self):
        with tempfile.TemporaryDirectory() as root:
            audio = FakeAudio()
            tts = self.make_tts(root, audio, [self.chunk(b"one")])
            voice = tts._load_voice()
            tts.speak("I'm here with you. Take your time.")
            tts.speak("Congratulations, you did it!")
            self.assertGreater(
                voice.configs[0]["length_scale"], voice.configs[1]["length_scale"]
            )

    def test_stop_during_playback_stops_before_the_next_audio_chunk(self):
        with tempfile.TemporaryDirectory() as root:
            audio = FakeAudio()
            tts = self.make_tts(root, audio, [self.chunk(b"first"), self.chunk(b"second")])
            audio.on_write = tts.stop
            self.assertFalse(tts.speak("Stop this local reply."))
            self.assertEqual(audio.written, [b"first"])
            self.assertGreaterEqual(audio.stopped, 1)
            self.assertEqual(audio.terminated, 1)

    def test_cancellation_before_synthesis_does_not_open_audio(self):
        with tempfile.TemporaryDirectory() as root:
            audio = FakeAudio()
            tts = self.make_tts(root, audio, [self.chunk(b"unplayed")])
            self.assertFalse(tts.speak("Canceled.", is_cancelled=lambda: True))
            self.assertEqual(audio.written, [])
            self.assertEqual(audio.terminated, 0)

    def test_missing_model_disables_optional_backend_cleanly(self):
        with tempfile.TemporaryDirectory() as root:
            audio = FakeAudio()
            tts = OfflinePiperTTS(
                model_path=Path(root) / "missing.onnx", audio_module=audio,
                piper_module=SimpleNamespace(),
            )
            self.assertFalse(tts.available())
            self.assertFalse(tts.speak("Use the fallback voice."))
            self.assertEqual(audio.terminated, 0)


if __name__ == "__main__":
    unittest.main()
