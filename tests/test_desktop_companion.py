import threading
import unittest
import io
import queue
import wave
from types import SimpleNamespace
from unittest.mock import patch

import desktop_companion as companion_module
from desktop_companion import DesktopCompanion


class FakeRoot:
    def __init__(self):
        self.callbacks = []

    def after(self, _delay, callback, *args):
        self.callbacks.append((callback, args))


class CompanionFlowTests(unittest.TestCase):
    def test_camera_hand_can_pet_nesuko_once_per_touch(self):
        chirps = []
        companion = DesktopCompanion.__new__(DesktopCompanion)
        companion.root = SimpleNamespace(
            winfo_screenwidth=lambda: 1000, winfo_screenheight=lambda: 800,
        )
        companion.pet = SimpleNamespace(
            winfo_rootx=lambda: 400, winfo_rooty=lambda: 300,
            winfo_width=lambda: 100, winfo_height=lambda: 150,
        )
        companion._pet_mood = "idle"
        companion._hand_pet_active = False
        companion._play_chirp = chirps.append

        companion.hand_track_pet(0.45, 0.45)
        companion.hand_track_pet(0.46, 0.46)
        self.assertEqual(companion._pet_mood, "petting")
        self.assertEqual(chirps, ["pet"])

        companion.hand_track_pet(0.9, 0.9)
        self.assertEqual(companion._pet_mood, "idle")
        companion.hand_track_pet(0.5, 0.5, tracked=False)
        self.assertFalse(companion._hand_pet_active)

    def test_windows_desktop_shell_is_not_mistaken_for_fullscreen_game(self):
        class FakeUser32:
            def __init__(self, class_name):
                self.class_name = class_name

            def GetClassNameW(self, _hwnd, buffer, _size):
                buffer.value = self.class_name
                return len(self.class_name)

            @staticmethod
            def GetWindowThreadProcessId(_hwnd, process_id):
                process_id._obj.value = 999
                return 1

            @staticmethod
            def GetWindowRect(_hwnd, rect):
                rect._obj.left, rect._obj.top = 0, 0
                rect._obj.right, rect._obj.bottom = 1920, 1080
                return True

            @staticmethod
            def GetWindowLongW(_hwnd, _index):
                return 0  # Borderless fullscreen.

        self.assertFalse(DesktopCompanion._is_fullscreen_external_window(
            FakeUser32("Progman"), 1, 1920, 1080
        ))
        self.assertFalse(DesktopCompanion._is_fullscreen_external_window(
            FakeUser32("WorkerW"), 1, 1920, 1080
        ))
        self.assertTrue(DesktopCompanion._is_fullscreen_external_window(
            FakeUser32("GameWindow"), 1, 1920, 1080
        ))

    def test_screen_capture_falls_back_to_primary_monitor(self):
        image = object()
        with patch(
            "PIL.ImageGrab.grab",
            side_effect=[OSError("all-monitor mode unavailable"), image],
        ) as grab:
            self.assertIs(DesktopCompanion.grab_screen_image(), image)
        self.assertEqual(grab.call_args_list[0].kwargs, {"all_screens": True})
        self.assertEqual(grab.call_args_list[1].kwargs, {})

    def test_screen_capture_reports_both_capture_failures(self):
        with patch(
            "PIL.ImageGrab.grab",
            side_effect=[OSError("all-monitor failed"), OSError("primary failed")],
        ):
            with self.assertRaisesRegex(RuntimeError, "primary-screen capture failed"):
                DesktopCompanion.grab_screen_image()

    def make_companion(self, **overrides):
        companion = DesktopCompanion.__new__(DesktopCompanion)
        companion.root = FakeRoot()
        companion.status = SimpleNamespace(configure=lambda **_kwargs: None)
        companion.ready_status = "TEXT PET · WISPR READY"
        companion.call_active = False
        companion.session_id = lambda: "session-1"
        companion.ask_ai = lambda message, **_kwargs: "answer: " + message
        companion.add_turn = lambda *_args, **_kwargs: None
        companion.on_turn = lambda *_args: None
        companion.speak = lambda *_args: True
        companion.dictation_active = False
        companion.dictation_mode = None
        companion._dictation_reply_pending = False
        companion._show_notification = lambda *_args: None
        companion._play_chirp = lambda *_args: None
        companion.request_finish = lambda _event: None
        for key, value in overrides.items():
            setattr(companion, key, value)
        return companion

    def test_wispr_handoff_is_idempotent_and_releases(self):
        states = []
        companion = self.make_companion(dictation_mode=states.append)
        companion._set_dictation_active(True)
        companion._set_dictation_active(True)
        companion._set_dictation_active(False)
        self.assertEqual(states, [True, False])

    def test_global_shortcut_dispatches_voice_activation_callback(self):
        activations = []
        companion = self.make_companion(on_hotkey=lambda: activations.append("voice"))
        events = queue.SimpleQueue()
        events.put(("hotkey", None))
        events.put(("mouse5", None))
        companion._shortcut = SimpleNamespace(events=events)

        companion._dispatch_shortcut_events()

        self.assertEqual(activations, ["voice", "voice"])
        self.assertEqual(len(companion.root.callbacks), 1)

    def test_companion_reply_uses_shared_session_and_restores_dictation(self):
        turns, refreshed, finished = [], [], []
        states = []
        companion = self.make_companion(
            add_turn=lambda *args, **kwargs: turns.append((args, kwargs)),
            on_turn=refreshed.append,
            dictation_mode=states.append,
            request_finish=lambda event: finished.append(event) or True,
        )
        cancel_event = threading.Event()
        companion._set_dictation_active(True)
        companion._answer("hello", None, cancel_event)
        self.assertEqual(turns[0][0], ("hello", "answer: hello"))
        self.assertEqual(turns[0][1], {"session_id": "session-1"})
        self.assertEqual(refreshed, ["session-1"])
        self.assertEqual(companion.root.callbacks[0][0], companion._answer_ready)
        self.assertTrue(companion.root.callbacks[0][1][2])
        companion._answer_ready("answer: hello", cancel_event, True)
        self.assertEqual(states, [True, False])
        self.assertEqual(finished, [cancel_event])
        self.assertFalse(companion.dictation_active)

    def test_pet_replies_with_text_and_chirp_without_tts(self):
        rendered, sounds, speech, finished = [], [], [], []
        companion = self.make_companion(
            _show_notification=rendered.append,
            _play_chirp=sounds.append,
            speak=lambda *_args: speech.append(True),
            request_finish=lambda event: finished.append(event) or True,
        )
        event = threading.Event()
        companion._answer_ready("A text reply", event, True)
        self.assertEqual(rendered, ["A text reply"])
        self.assertEqual(sounds, ["reply"])
        self.assertEqual(speech, [])
        self.assertEqual(finished, [event])

    def test_pet_talk_button_uses_the_shared_hotkey_input_flow(self):
        calls = []
        companion = self.make_companion(on_hotkey=lambda: calls.append("activate input"))
        companion.call_voice()
        self.assertEqual(calls, ["activate input"])
        self.assertFalse(companion.call_active)

    def test_pet_chirp_uses_short_note_bent_nonverbal_audio(self):
        sounds = []

        class ImmediateExecutor:
            def submit(self, callback):
                callback()

        companion = self.make_companion(_sound_executor=ImmediateExecutor())
        del companion._play_chirp
        fake_winsound = SimpleNamespace(
            SND_MEMORY=1, SND_SYNC=2,
            PlaySound=lambda audio, flags: sounds.append((audio, flags)),
        )
        with patch("desktop_companion.os.name", "nt"), patch.object(companion_module, "winsound", fake_winsound):
            companion._play_chirp("reply")
        self.assertEqual(len(sounds), 1)
        self.assertEqual(sounds[0][1], 3)
        with wave.open(io.BytesIO(sounds[0][0]), "rb") as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 22050))
            self.assertGreater(audio.getnframes() / audio.getframerate(), 0.2)
            self.assertLess(audio.getnframes() / audio.getframerate(), 0.5)

    def test_voice_activity_is_queued_for_pet_animation_without_tk_calls(self):
        companion = self.make_companion()
        companion._activity_events = queue.SimpleQueue()
        companion._last_queued_activity = None
        companion.activity_changed("speaking")
        companion.activity_changed("speaking")
        self.assertEqual(companion._activity_events.get_nowait(), "speaking")
        with self.assertRaises(queue.Empty):
            companion._activity_events.get_nowait()

    def test_stopped_companion_reply_is_saved_without_speaking(self):
        turns, speech = [], []
        event = threading.Event()
        event.set()
        finished = []
        companion = self.make_companion(
            add_turn=lambda *args, **kwargs: turns.append((args, kwargs)),
            speak=lambda *_args: speech.append(True),
            request_finish=lambda finished_event: finished.append(finished_event) or True,
        )
        companion._answer("hello", None, event)
        callback, args = companion.root.callbacks[0]
        self.assertEqual(callback, companion._answer_ready)
        self.assertEqual(args[0], "I stopped that reply.")
        self.assertFalse(args[2])
        callback(*args)
        self.assertEqual(turns[0][0], ("hello", "[Response generation stopped by the user.]"))
        self.assertEqual(speech, [])
        self.assertEqual(finished, [event])


if __name__ == "__main__":
    unittest.main()
