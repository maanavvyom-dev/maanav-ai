import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import computer_use


class ComputerUseTests(unittest.TestCase):
    def test_action_summary_shows_clicks_keys_and_typed_content(self):
        summary = computer_use.summarize_actions([
            {"type": "click", "x": 25, "y": 40, "button": "left"},
            {"type": "keypress", "keys": ["CTRL", "A"]},
            {"type": "type", "text": "x" * 300},
            {"type": "drag", "path": [[1, 2], {"x": 3, "y": 4}]},
            {"type": "scroll", "x": 10, "y": 20, "scroll_y": 4},
        ])
        self.assertIn("Click at (25, 40)", summary)
        self.assertIn("CTRL+A", summary)
        self.assertIn("x" * 300, summary)
        self.assertIn("(1, 2) → (3, 4)", summary)
        self.assertIn("vertical 4", summary)

    def test_drag_path_moves_between_all_points_and_releases_button(self):
        import ctypes

        events = []
        user32 = SimpleNamespace(
            GetSystemMetrics=lambda axis: 1000 if axis == 0 else 800,
            SetCursorPos=lambda x, y: events.append(("move", x, y)),
            mouse_event=lambda *args: events.append(("mouse", *args)),
            keybd_event=lambda *_args: None,
        )
        with patch("computer_use.os.name", "nt"), \
                patch.object(ctypes, "windll", SimpleNamespace(user32=user32), create=True), \
                patch("computer_use.time.sleep"):
            result = computer_use.execute_action({
                "type": "drag", "path": [[10, 20], [30, 40], [50, 60]],
            })
        self.assertEqual(result, "dragged")
        self.assertEqual([event for event in events if event[0] == "move"], [
            ("move", 10, 20), ("move", 30, 40), ("move", 50, 60),
        ])
        self.assertEqual(events[-1][1], 0x0004)

    def test_scroll_uses_horizontal_and_vertical_deltas(self):
        import ctypes

        events = []
        user32 = SimpleNamespace(
            GetSystemMetrics=lambda axis: 1000 if axis == 0 else 800,
            SetCursorPos=lambda *_args: None,
            mouse_event=lambda *args: events.append(args),
            keybd_event=lambda *_args: None,
        )
        with patch("computer_use.os.name", "nt"), \
                patch.object(ctypes, "windll", SimpleNamespace(user32=user32), create=True):
            result = computer_use.execute_action({
                "type": "scroll", "x": 25, "y": 30, "scroll_x": 2, "scroll_y": -3,
            })
        self.assertEqual(result, "scrolled")
        self.assertEqual(events, [(0x0800, 0, 0, 360, 0), (0x1000, 0, 0, -240, 0)])

    def test_user_denial_stops_before_action_execution(self):
        fake_client = SimpleNamespace(close=lambda: None)
        call = SimpleNamespace(
            type="computer_call", call_id="call-1",
            actions=[{"type": "click", "x": 10, "y": 20, "button": "left"}],
        )
        fake_client.responses = SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(output=[call]))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
                patch("openai.OpenAI", return_value=fake_client), \
                patch.object(computer_use, "_screenshot_data_url", return_value="data:image/png;base64,AA=="), \
                patch.object(computer_use, "execute_action") as execute:
            result = computer_use.run_computer_task(
                "click a button", confirm_sharing=lambda _task: True,
                approve_actions=lambda _task, _actions: False,
            )
        self.assertIn("stopped", result)
        execute.assert_not_called()

    def test_approved_action_runs_then_returns_final_answer(self):
        call = SimpleNamespace(
            type="computer_call", call_id="call-1",
            actions=[{"type": "click", "x": 10, "y": 20, "button": "left"}],
        )
        responses = [
            SimpleNamespace(output=[call], id="response-1"),
            SimpleNamespace(output=[], output_text="Finished."),
        ]
        fake_client = SimpleNamespace(
            responses=SimpleNamespace(create=lambda **_kwargs: responses.pop(0)),
            close=lambda: None,
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
                patch("openai.OpenAI", return_value=fake_client), \
                patch.object(computer_use, "_screenshot_data_url", return_value="data:image/png;base64,AA=="), \
                patch.object(computer_use, "execute_action", return_value="clicked") as execute:
            result = computer_use.run_computer_task(
                "click a button", confirm_sharing=lambda _task: True,
                approve_actions=lambda _task, _actions: True,
            )
        self.assertEqual(result, "Finished.")
        execute.assert_called_once_with(call.actions[0])

    def test_screen_sharing_denial_never_creates_api_client(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
                patch("openai.OpenAI") as client_factory:
            result = computer_use.run_computer_task(
                "look at the screen", confirm_sharing=lambda _task: False,
                approve_actions=lambda *_args: True,
            )
        self.assertIn("cancelled", result)
        client_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
