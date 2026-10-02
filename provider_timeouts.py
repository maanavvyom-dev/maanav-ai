"""Small bounded timeout settings for cloud AI provider requests."""

import math
import os


def provider_timeout_seconds(environment_name, default, *, minimum=5.0, maximum=300.0):
    """Read a timeout from the environment, falling back safely on bad values."""
    try:
        timeout = float(os.getenv(environment_name, default))
    except (TypeError, ValueError):
        timeout = float(default)
    if not math.isfinite(timeout):
        timeout = float(default)
    return min(float(maximum), max(float(minimum), timeout))
