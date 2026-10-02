"""Short-lived, conversation-bound confirmations for risky local actions."""
import threading
import time


class PendingConfirmation:
    YES = {"yes", "yeah", "yep", "confirm", "do it", "okay", "ok"}
    NO = {"no", "nope", "cancel", "don't", "dont"}

    def __init__(self, timeout_seconds=30, clock=time.monotonic):
        self.timeout_seconds = timeout_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._pending = None

    def request(self, action, session_id=None):
        with self._lock:
            self._pending = {
                "action": action,
                "session_id": str(session_id or ""),
                "expires_at": self._clock() + self.timeout_seconds,
            }

    def resolve(self, response, session_id=None):
        """Return (status, action); consuming any response clears the request."""
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            return None, None
        if self._clock() >= pending["expires_at"]:
            return "expired", None
        if pending["session_id"] != str(session_id or ""):
            return "wrong_session", None
        normalized = " ".join(str(response).lower().split())
        if normalized in self.YES:
            return "confirmed", pending["action"]
        if normalized in self.NO:
            return "cancelled", pending["action"]
        return "unclear", pending["action"]
