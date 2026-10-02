import unittest
from types import SimpleNamespace
from unittest.mock import patch

import gemini_ai


class GeminiProviderTests(unittest.TestCase):
    def test_temporary_503_retries_once_then_returns_answer(self):
        events = []
        close_calls = []

        class Chat:
            def send_message(self, _contents):
                events.append("send")
                if events.count("send") == 1:
                    raise RuntimeError("503 UNAVAILABLE")
                return SimpleNamespace(text="Gemini recovered")

        client = SimpleNamespace(
            chats=SimpleNamespace(create=lambda **kwargs: (events.append(kwargs["model"]) or Chat())),
            close=lambda: close_calls.append(True),
        )
        client_options = []
        with patch.dict(gemini_ai.os.environ, {"GEMINI_API_KEY": "test-key"}), \
             patch.object(gemini_ai.genai, "Client", side_effect=lambda **kwargs: (client_options.append(kwargs) or client)), \
             patch.object(gemini_ai.time, "sleep") as sleep:
            self.assertEqual(gemini_ai.ask_gemini("Hello"), "Gemini recovered")
        self.assertEqual(events, [gemini_ai.MODEL, "send", gemini_ai.MODEL, "send"])
        self.assertEqual(client_options[0]["http_options"].timeout, 30000)
        self.assertEqual(close_calls, [True])
        sleep.assert_called_once_with(0.8)

    def test_gemini_timeout_can_be_overridden(self):
        close_calls = []
        client = SimpleNamespace(
            chats=SimpleNamespace(
                create=lambda **_kwargs: SimpleNamespace(send_message=lambda _contents: SimpleNamespace(text="OK"))
            ),
            close=lambda: close_calls.append(True),
        )
        client_options = []
        with patch.dict(gemini_ai.os.environ, {"GEMINI_API_KEY": "test-key", "MAANAV_GEMINI_TIMEOUT": "60"}), \
             patch.object(gemini_ai.genai, "Client", side_effect=lambda **kwargs: (client_options.append(kwargs) or client)):
            self.assertEqual(gemini_ai.ask_gemini("Hello"), "OK")
        self.assertEqual(client_options[0]["http_options"].timeout, 60000)
        self.assertEqual(close_calls, [True])

    def test_permanent_error_returns_none_without_retry(self):
        events = []

        class Chat:
            def send_message(self, _contents):
                events.append("send")
                raise RuntimeError("invalid model configuration")

        close_calls = []
        client = SimpleNamespace(
            chats=SimpleNamespace(create=lambda **_kwargs: Chat()),
            close=lambda: close_calls.append(True),
        )
        with patch.dict(gemini_ai.os.environ, {"GEMINI_API_KEY": "test-key"}), \
             patch.object(gemini_ai.genai, "Client", return_value=client):
            self.assertIsNone(gemini_ai.ask_gemini("Hello"))
        self.assertEqual(events, ["send"])
        self.assertEqual(close_calls, [True])


if __name__ == "__main__":
    unittest.main()
