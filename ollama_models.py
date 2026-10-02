"""Select an installed local Ollama model without downloading anything."""

import math


PREFERRED_LOCAL_MODELS = (
    "qwen3.5:9b",
    "qwen3:4b-instruct",
    "qwen3:4b",
    "llama3.2:3b",
)


def bounded_timeout_seconds(value, default=180.0, minimum=15.0, maximum=900.0):
    """Parse a local model request timeout and keep it within sane bounds."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = float(default)
    if not math.isfinite(seconds):
        seconds = float(default)
    return min(float(maximum), max(float(minimum), seconds))


def choose_installed_model(requested, available):
    """Use the requested model, then known smaller local options, then any installed model."""
    names = [str(name).strip() for name in (available or ()) if str(name).strip()]
    if requested in names:
        return requested
    for candidate in PREFERRED_LOCAL_MODELS:
        if candidate in names:
            return candidate
    return names[0] if names else requested
