import unittest

from personality import build_gemini_prompt, build_local_messages, build_system_prompt


class PersonalityTests(unittest.TestCase):
    def test_shared_personality_is_warm_without_claiming_human_feelings(self):
        prompt = build_system_prompt("\nUSER MEMORY: Python")
        self.assertIn("emotionally perceptive", prompt)
        self.assertIn("do not claim to be conscious", prompt)
        self.assertIn("USER MEMORY: Python", prompt)

    def test_system_prompt_includes_local_clock_and_natural_repetition_guidance(self):
        prompt = build_system_prompt()
        self.assertIn("CURRENT LOCAL DATE AND TIME", prompt)
        self.assertIn("from this computer's clock", prompt)
        self.assertIn("vary the suggestion across turns", prompt)
        self.assertIn("Spider-Man what-if", prompt)
        self.assertIn("Do not bring it up unless asked or useful", prompt)

    def test_local_provider_gets_turns_as_conversation_roles(self):
        history = [
            {"role": "user", "content": "I am learning Python."},
            {"role": "assistant", "content": "That's a great start."},
        ]
        messages = build_local_messages("What should I learn next?", history=history)
        self.assertEqual([item["role"] for item in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[-1]["content"], "What should I learn next?")

    def test_gemini_prompt_has_history_and_image_fallback(self):
        prompt = build_gemini_prompt(
            "", history=[{"role": "user", "content": "Earlier question"}],
            image_attached=True,
        )
        self.assertIn("Earlier question", prompt)
        self.assertIn("Please describe the attached image.", prompt)

    def test_reply_style_customization_reaches_both_ai_routes(self):
        self.assertIn("Keep replies brief and direct", build_system_prompt(response_style="concise"))
        self.assertIn("enough detail to teach the user", build_system_prompt(response_style="detailed"))
        local = build_local_messages("Explain this", response_style="creative")
        remote = build_gemini_prompt("Explain this", response_style="creative")
        self.assertIn("vivid, lively wording", local[0]["content"])
        self.assertIn("vivid, lively wording", remote)
        self.assertIn("You are Maanav Vyom", local[0]["content"])


if __name__ == "__main__":
    unittest.main()
