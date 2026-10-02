"""Small, durable local memory store with legacy JSON compatibility."""
import json
import os
import threading
from pathlib import Path

MEMORY_FILE = Path(__file__).resolve().with_name("memory.json")
_lock = threading.RLock()


def load_memory():
    with _lock:
        try:
            data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Legacy key/value JSON remains the canonical on-disk format.
                return {str(key): value for key, value in data.items() if isinstance(value, (str, int, float, bool))}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        return {}


def save_memory(memory):
    if not isinstance(memory, dict):
        raise TypeError("Memory must be a dictionary of facts.")
    with _lock:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = MEMORY_FILE.with_suffix(MEMORY_FILE.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, MEMORY_FILE)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def remember(key, value):
    key, value = str(key).strip(), str(value).strip()
    if not key or not value:
        return False
    with _lock:
        data = load_memory()
        data[key] = value
        save_memory(data)
    return True


def remember_note(value):
    """Persist an explicitly requested free-form note without saving normal chat."""
    note = str(value).strip()
    if not note:
        return None
    with _lock:
        data = load_memory()
        for key, saved_value in data.items():
            if key.lower().startswith("note ") and str(saved_value).casefold() == note.casefold():
                return key
        existing_keys = {str(key).casefold() for key in data}
        index = 1
        while f"note {index}" in existing_keys:
            index += 1
        key = f"note {index}"
        data[key] = note
        try:
            save_memory(data)
        except OSError:
            return None
        return key


def recall(key):
    return load_memory().get(str(key).strip())


def forget(key):
    with _lock:
        data = load_memory()
        if key not in data:
            return False
        del data[key]
        save_memory(data)
        return True


def forget_everything():
    save_memory({})


def get_all_memory():
    return load_memory()


def classify_memory_candidate(message):
    """Extract only a few clearly durable, user-owned preference facts."""
    import re
    text = str(message).strip()
    patterns = (
        (r"my favorite ([a-z][a-z0-9 _-]{0,35}) is (.+)", "favorite {0}"),
        (r"my favourite ([a-z][a-z0-9 _-]{0,35}) is (.+)", "favorite {0}"),
        (r"my name is (.+)", "name"),
    )
    for pattern, key_template in patterns:
        match = re.fullmatch(pattern, text, re.IGNORECASE)
        if match:
            if key_template == "name":
                return "name", match.group(1).strip()
            return key_template.format(match.group(1).strip().lower()), match.group(2).strip().rstrip(".!?")
    return None
