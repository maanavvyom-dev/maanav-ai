"""Persistent microphone health for the wake-word status display."""


class MicrophoneStatus:
    def __init__(self):
        self.problem = None

    def unavailable(self, open_failed=False):
        if open_failed:
            self.problem = "MICROPHONE UNAVAILABLE · RETRYING DEFAULT INPUT"
        else:
            self.problem = "MICROPHONE ERROR · RETRYING"

    def ready(self):
        self.problem = None

    @property
    def listening_label(self):
        return self.problem or "LISTENING · SAY WAKE UP"
