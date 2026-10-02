import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HISTORY_FILE = ROOT / "chat_history.json"  # Kept for one-time migration from the original app.
STORE_FILE = ROOT / "chats.json"
MAX_STORED_MESSAGES = 80
_lock = threading.RLock()
_temporary_sessions = {}
_active_session_override = None


def _backup_corrupt_store():
    """Preserve unreadable chat data before recovering through the legacy store."""
    if not STORE_FILE.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = STORE_FILE.with_name(f"{STORE_FILE.name}.corrupt-{stamp}.bak")
    try:
        shutil.copy2(STORE_FILE, backup)
    except OSError as error:
        # Do not allow a later save to silently replace the only copy.
        raise RuntimeError(
            f"The conversation archive is unreadable and its backup failed: {error}"
        ) from error
    print(f"[CHAT HISTORY] Unreadable archive preserved at {backup}")
    return backup


def _clean_messages(messages):
    if not isinstance(messages, list):
        return []
    return [
        {"role": item["role"], "content": item["content"]}
        for item in messages
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
    ][-MAX_STORED_MESSAGES:]


def _legacy_messages():
    try:
        return _clean_messages(json.loads(HISTORY_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def _title_from(messages):
    first_user = next((m["content"].strip() for m in messages if m["role"] == "user"), "")
    if not first_user:
        return "New chat"
    first_line = first_user.splitlines()[0]
    return first_line[:40].rstrip() + ("…" if len(first_line) > 40 else "")


def _read_store():
    global _active_session_override
    try:
        data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
        sessions = data.get("sessions", []) if isinstance(data, dict) else []
        sessions = [
            {
                "id": str(item["id"]),
                "title": str(item.get("title") or "New chat"),
                "messages": _clean_messages(item.get("messages", [])),
                "updated": str(item.get("updated") or ""),
                "created": str(item.get("created") or item.get("updated") or ""),
                "temporary": bool(item.get("temporary", False)),
            }
            for item in sessions
            if isinstance(item, dict) and item.get("id") and not item.get("temporary", False)
        ]
        sessions.extend(_temporary_sessions.values())
        if sessions:
            active_id = _active_session_override or str(data.get("active_id", sessions[0]["id"]))
            if not any(item["id"] == active_id for item in sessions):
                active_id = sessions[0]["id"]
            _active_session_override = active_id
            return {"active_id": active_id, "sessions": sessions}
        _backup_corrupt_store()
    except OSError:
        pass
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        _backup_corrupt_store()

    # Import the previous single transcript without deleting or rewriting it.
    legacy = _legacy_messages()
    session_id = "legacy"
    legacy_store = {
        "active_id": session_id,
        "sessions": [{
            "id": session_id,
            "title": _title_from(legacy) if legacy else "New chat",
            "messages": legacy,
            "updated": datetime.now(timezone.utc).isoformat(),
            "created": datetime.now(timezone.utc).isoformat(),
            "temporary": False,
        }],
    }
    legacy_store["sessions"].extend(_temporary_sessions.values())
    if _active_session_override and any(item["id"] == _active_session_override for item in legacy_store["sessions"]):
        legacy_store["active_id"] = _active_session_override
    return legacy_store


def _write_store(data):
    global _active_session_override
    _temporary_sessions.clear()
    _temporary_sessions.update({item["id"]: item for item in data["sessions"] if item.get("temporary")})
    _active_session_override = data["active_id"]
    persistent = [item for item in data["sessions"] if not item.get("temporary")]
    persistent_ids = {item["id"] for item in persistent}
    active_id = data["active_id"] if data["active_id"] in persistent_ids else (
        persistent[-1]["id"] if persistent else ""
    )
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STORE_FILE.with_suffix(STORE_FILE.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps({"active_id": active_id, "sessions": persistent},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, STORE_FILE)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def get_active_session_id():
    with _lock:
        return _read_store()["active_id"]


def list_sessions(limit=20):
    with _lock:
        data = _read_store()
        sessions = sorted(data["sessions"], key=lambda item: item["updated"], reverse=True)
        return [
            {"id": item["id"], "title": item["title"], "created": item["created"],
             "updated": item["updated"], "temporary": item["temporary"],
             "active": item["id"] == data["active_id"]}
            for item in sessions[:limit]
        ]


def new_session(temporary=False):
    with _lock:
        data = _read_store()
        session_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        data["sessions"].append({
            "id": session_id,
            "title": "New chat",
            "messages": [],
            "updated": now,
            "created": now,
            "temporary": bool(temporary),
        })
        data["active_id"] = session_id
        _write_store(data)
        return session_id


def switch_session(session_id):
    with _lock:
        data = _read_store()
        if not any(item["id"] == session_id for item in data["sessions"]):
            return False
        data["active_id"] = session_id
        _write_store(data)
        return True


def load_history(limit=12, session_id=None):
    with _lock:
        data = _read_store()
        session_id = session_id or data["active_id"]
        active = next((item for item in data["sessions"] if item["id"] == session_id), None)
        if active is None:
            active = next(item for item in data["sessions"] if item["id"] == data["active_id"])
        messages = active["messages"]
        return messages[-limit:] if limit else messages[:]


def add_turn(user_text, assistant_text, session_id=None):
    with _lock:
        data = _read_store()
        session_id = session_id or data["active_id"]
        active = next((item for item in data["sessions"] if item["id"] == session_id), None)
        if active is None:
            return False
        active["messages"].extend([
            {"role": "user", "content": str(user_text)},
            {"role": "assistant", "content": str(assistant_text)},
        ])
        active["messages"] = active["messages"][-MAX_STORED_MESSAGES:]
        if active["title"] == "New chat":
            active["title"] = _title_from(active["messages"])
        active["updated"] = datetime.now(timezone.utc).isoformat()
        _write_store(data)
        return True


def rename_session(session_id, title):
    title = str(title).strip()
    if not title:
        return False
    with _lock:
        data = _read_store()
        session = next((item for item in data["sessions"] if item["id"] == session_id), None)
        if session is None:
            return False
        session["title"] = title[:80]
        session["updated"] = datetime.now(timezone.utc).isoformat()
        _write_store(data)
        return True


def delete_session(session_id):
    with _lock:
        data = _read_store()
        remaining = [item for item in data["sessions"] if item["id"] != session_id]
        if len(remaining) == len(data["sessions"]):
            return False
        if not remaining:
            now = datetime.now(timezone.utc).isoformat()
            remaining = [{"id": uuid.uuid4().hex[:12], "title": "New chat", "messages": [],
                          "updated": now, "created": now, "temporary": False}]
        if data["active_id"] == session_id:
            data["active_id"] = remaining[-1]["id"]
        data["sessions"] = remaining
        _write_store(data)
        return True


def clear_history():
    with _lock:
        data = _read_store()
        active = next(item for item in data["sessions"] if item["id"] == data["active_id"])
        active["messages"] = []
        active["title"] = "New chat"
        active["updated"] = datetime.now(timezone.utc).isoformat()
        _write_store(data)
