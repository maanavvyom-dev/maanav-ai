import os
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import mock_open, patch

import openai_ai


class OpenAIProviderTests(unittest.TestCase):
    def test_gpt6_astra_uses_responses_with_history_memory_and_image(self):
        calls = []
        close_calls = []
        client = SimpleNamespace(responses=SimpleNamespace(
            create=lambda **kwargs: (calls.append(kwargs) or SimpleNamespace(output_text="Astra reply"))
        ), close=lambda: close_calls.append(True))
        client_options = []
        sdk = ModuleType("openai")
        sdk.OpenAI = lambda **kwargs: (client_options.append(kwargs) or client)
        history = [{"role": "user", "content": "Earlier question"}]

        with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-key"}), \
             patch.dict(sys.modules, {"openai": sdk}), \
             patch("builtins.open", mock_open(read_data=b"unit-test image bytes")):
            answer = openai_ai.ask_openai(
                "Describe this", history=history, image_path="sample.png",
                memory_text="USER MEMORY: Python",
            )

        self.assertEqual(answer, "Astra reply")
        self.assertEqual(close_calls, [True])
        self.assertEqual(client_options, [{"timeout": 45.0}])
        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request["model"], "gpt-6-astra")
        self.assertIn("USER MEMORY: Python", request["instructions"])
        self.assertEqual(request["input"][0], history[0])
        self.assertEqual(request["input"][-1]["role"], "user")
        self.assertEqual(request["input"][-1]["content"][0]["text"], "Describe this")
        self.assertTrue(request["input"][-1]["content"][1]["image_url"].startswith("data:image/png;base64,"))

    def test_openai_timeout_can_be_overridden(self):
        client_options = []
        close_calls = []
        sdk = ModuleType("openai")
        sdk.OpenAI = lambda **kwargs: (client_options.append(kwargs) or SimpleNamespace(
            responses=SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(output_text="OK")),
            close=lambda: close_calls.append(True),
        ))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-key", "MAANAV_OPENAI_TIMEOUT": "90"}), \
             patch.dict(sys.modules, {"openai": sdk}):
            self.assertEqual(openai_ai.ask_openai("Hello"), "OK")
        self.assertEqual(client_options, [{"timeout": 90.0}])
        self.assertEqual(close_calls, [True])

    def test_missing_key_skips_sdk_without_network(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(openai_ai.ask_openai("Hello"))


if __name__ == "__main__":
    unittest.main()
