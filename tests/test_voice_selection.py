import unittest
from types import SimpleNamespace

from voice_selection import choose_voice, language_preferences


class VoiceSelectionTests(unittest.TestCase):
    def test_non_latin_reply_selects_matching_installed_language(self):
        english = SimpleNamespace(name="Microsoft Hazel", languages=["en-GB"])
        tamil = SimpleNamespace(name="Microsoft Valluvar", languages=["ta-IN"])
        self.assertIs(choose_voice([english, tamil], "நான் உதவுகிறேன்", ["hazel"]), tamil)

    def test_english_keeps_configured_voice_preference(self):
        hazel = SimpleNamespace(name="Microsoft Hazel", languages=["en-GB"])
        zira = SimpleNamespace(name="Microsoft Zira", languages=["en-US"])
        self.assertIs(choose_voice([zira, hazel], "Hello there", ["hazel"]), hazel)

    def test_mixed_text_prioritizes_the_dominant_non_latin_script(self):
        self.assertEqual(language_preferences("Hello नमस्ते नमस्ते"), ["hi", "mr", "ne", "sa"])

    def test_missing_language_voice_uses_normal_default_fallback(self):
        first = SimpleNamespace(name="Voice One", languages=["en-US"])
        second = SimpleNamespace(name="Voice Two", languages=["en-GB"])
        self.assertIs(choose_voice([first, second], "こんにちは", []), second)


if __name__ == "__main__":
    unittest.main()
