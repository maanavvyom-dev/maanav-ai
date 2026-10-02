import unittest

from voice_input import recognize_audio


class RequestFailure(Exception):
    pass


class UnintelligibleSpeech(Exception):
    pass


class VoiceInputTests(unittest.TestCase):
    def route(self, local, online, *, offline=False, force_local=False, model_ready=True):
        statuses = []
        result = recognize_audio(
            b"audio",
            offline_transcriber=local,
            online_transcriber=online,
            offline_only=offline or force_local,
            local_model_ready=lambda: model_ready,
            set_status=statuses.append,
            request_error=RequestFailure,
            unknown_value_error=UnintelligibleSpeech,
        )
        return result, statuses

    def test_local_speech_is_preferred_even_when_online_is_available(self):
        online_calls = []
        result, statuses = self.route(
            lambda _audio: "  Hello Maanav! ",
            lambda _audio: online_calls.append(True),
        )
        self.assertEqual(result, "hello maanav!")
        self.assertEqual(online_calls, [])
        self.assertEqual(statuses, [])

    def test_offline_missing_model_is_distinguished_from_silence(self):
        online_calls = []
        result, statuses = self.route(
            lambda _audio: None, lambda _audio: online_calls.append(True),
            offline=True, model_ready=False,
        )
        self.assertIsNone(result)
        self.assertEqual(statuses, ["OFFLINE SPEECH MODEL NOT READY"])
        self.assertEqual(online_calls, [])

    def test_wake_listening_forced_local_never_uses_online_recognition(self):
        online_calls = []
        result, statuses = self.route(
            lambda _audio: None, lambda _audio: online_calls.append(True),
            force_local=True,
        )
        self.assertIsNone(result)
        self.assertEqual(online_calls, [])
        self.assertEqual(statuses, ["NO SPEECH DETECTED · TRY AGAIN"])

    def test_offline_silence_reports_no_speech_not_missing_model(self):
        result, statuses = self.route(lambda _audio: None, lambda _audio: "unexpected", offline=True)
        self.assertIsNone(result)
        self.assertEqual(statuses, ["NO SPEECH DETECTED · TRY AGAIN"])

    def test_offline_model_error_is_visible_without_online_fallback(self):
        def broken(_audio):
            raise RuntimeError("model failed")

        online_calls = []
        result, statuses = self.route(
            broken, lambda _audio: online_calls.append(True), offline=True,
        )
        self.assertIsNone(result)
        self.assertEqual(statuses, ["OFFLINE RECOGNITION ERROR · RETRYING"])
        self.assertEqual(online_calls, [])

    def test_online_fallback_runs_only_after_empty_local_result(self):
        online_calls = []
        result, statuses = self.route(
            lambda _audio: None,
            lambda _audio: online_calls.append(True) or "  Open Downloads  ",
        )
        self.assertEqual(result, "open downloads")
        self.assertEqual(online_calls, [True])
        self.assertEqual(statuses, [])

    def test_online_connection_failure_is_reported_clearly(self):
        def broken(_audio):
            raise RequestFailure("offline")

        result, statuses = self.route(lambda _audio: None, broken)
        self.assertIsNone(result)
        self.assertEqual(statuses, ["SPEECH · INTERNET OR LOCAL MODEL NEEDED"])

    def test_online_unintelligible_speech_remains_a_no_speech_result(self):
        def unintelligible(_audio):
            raise UnintelligibleSpeech("not speech")

        with self.assertRaises(UnintelligibleSpeech):
            self.route(lambda _audio: None, unintelligible)


if __name__ == "__main__":
    unittest.main()
