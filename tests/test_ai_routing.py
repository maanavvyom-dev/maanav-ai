import unittest
import os
from unittest.mock import patch

from ai_routing import route_message
from ollama_models import choose_installed_model, bounded_timeout_seconds
from provider_timeouts import provider_timeout_seconds


class AIRoutingTests(unittest.TestCase):
    def test_ollama_timeout_is_configurable_but_bounded(self):
        self.assertEqual(bounded_timeout_seconds("75"), 75.0)
        self.assertEqual(bounded_timeout_seconds("not-a-number"), 180.0)
        self.assertEqual(bounded_timeout_seconds("0"), 15.0)
        self.assertEqual(bounded_timeout_seconds("999999"), 900.0)
        self.assertEqual(bounded_timeout_seconds("NaN"), 180.0)

    def test_cloud_provider_timeouts_have_safe_defaults_and_bounds(self):
        setting = "MAANAV_TEST_PROVIDER_TIMEOUT"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(setting, None)
            self.assertEqual(provider_timeout_seconds(setting, 45), 45.0)
            os.environ[setting] = "not-a-number"
            self.assertEqual(provider_timeout_seconds(setting, 45), 45.0)
            os.environ[setting] = "NaN"
            self.assertEqual(provider_timeout_seconds(setting, 45), 45.0)
            os.environ[setting] = "1"
            self.assertEqual(provider_timeout_seconds(setting, 45), 5.0)
            os.environ[setting] = "999"
            self.assertEqual(provider_timeout_seconds(setting, 45), 300.0)

    def test_local_model_prefers_configured_then_installed_fallback(self):
        self.assertEqual(choose_installed_model("qwen3.5:9b", ["qwen3.5:9b", "qwen3:4b-instruct"]), "qwen3.5:9b")
        self.assertEqual(choose_installed_model("qwen3.5:9b", ["qwen3:4b-instruct"]), "qwen3:4b-instruct")
        self.assertEqual(choose_installed_model("missing", ["custom:latest", "other:latest"]), "custom:latest")
        self.assertEqual(choose_installed_model("missing", []), "missing")

    def test_cloud_failure_falls_through_openai_gemini_then_local(self):
        calls = []

        def openai(message, **kwargs):
            calls.append(("openai", message, kwargs["history"]))
            return None

        def gemini(message, **_kwargs):
            calls.append(("gemini", message))
            return "  "

        def local(message, **kwargs):
            calls.append(("local", message, kwargs["memory_text"]))
            return "Local answer"

        result = route_message(
            "Explain this", history=[{"role": "user", "content": "Earlier"}],
            memory_text="saved facts", openai_provider=openai,
            gemini_provider=gemini, local_provider=local,
        )
        self.assertEqual(result, "Local answer")
        self.assertEqual([call[0] for call in calls], ["openai", "gemini", "local"])
        self.assertEqual(calls[0][2], [{"role": "user", "content": "Earlier"}])
        self.assertEqual(calls[-1][2], "saved facts")

    def test_openai_success_does_not_call_fallbacks(self):
        calls = []
        result = route_message(
            "Hello", openai_provider=lambda *_args, **_kwargs: "OpenAI answer",
            gemini_provider=lambda *_args, **_kwargs: calls.append("gemini"),
            local_provider=lambda *_args, **_kwargs: calls.append("local"),
        )
        self.assertEqual(result, "OpenAI answer")
        self.assertEqual(calls, [])

    def test_provider_exception_continues_to_next_provider(self):
        calls = []

        def broken_openai(*_args, **_kwargs):
            raise RuntimeError("offline")

        result = route_message(
            "Hello", openai_provider=broken_openai,
            gemini_provider=lambda *_args, **_kwargs: "Gemini answer",
            local_provider=lambda *_args, **_kwargs: calls.append("local"),
        )
        self.assertEqual(result, "Gemini answer")
        self.assertEqual(calls, [])

    def test_offline_mode_never_calls_cloud_providers(self):
        calls = []
        result = route_message(
            "Hello", offline=True,
            openai_provider=lambda *_args, **_kwargs: calls.append("openai"),
            gemini_provider=lambda *_args, **_kwargs: calls.append("gemini"),
            local_provider=lambda *_args, **_kwargs: "Offline answer",
        )
        self.assertEqual(result, "Offline answer")
        self.assertEqual(calls, [])

    def test_image_is_not_sent_to_local_model_in_offline_mode(self):
        calls = []
        result = route_message(
            "Describe it", image_path="photo.png", offline=True,
            local_provider=lambda *_args, **_kwargs: calls.append("local"),
        )
        self.assertIn("did not send it anywhere", result)
        self.assertEqual(calls, [])

    def test_cloud_image_failure_never_sends_image_to_ollama(self):
        calls = []
        result = route_message(
            "Describe it", image_path="photo.png",
            openai_provider=lambda *_args, **_kwargs: None,
            gemini_provider=lambda *_args, **_kwargs: None,
            local_provider=lambda *_args, **_kwargs: calls.append("local"),
        )
        self.assertIn("image-capable AI service", result)
        self.assertEqual(calls, [])

    def test_local_exception_returns_clear_retry_message(self):
        def broken_local(*_args, **_kwargs):
            raise RuntimeError("Ollama is stopped")

        result = route_message("Hello", local_provider=broken_local,
                              local_model="qwen3.5:9b")
        self.assertIn("qwen3.5:9b", result)
        self.assertIn("please retry", result)

    def test_all_providers_missing_returns_actionable_message(self):
        result = route_message("Hello", local_provider=lambda *_args, **_kwargs: None,
                              local_model="qwen3.5:9b")
        self.assertIn("qwen3.5:9b", result)
        self.assertIn("please retry", result)


if __name__ == "__main__":
    unittest.main()
