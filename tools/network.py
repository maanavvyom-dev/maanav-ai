"""Small, explicit Windows Wi-Fi controls. Adapter changes may require admin rights."""
import os
import subprocess


def wifi_adapters():
    if os.name != "nt":
        return []
    command = (
        "Get-NetAdapter -Physical | Where-Object { "
        "$_.NdisPhysicalMedium -eq 9 -or $_.Name -match 'Wi-?Fi|WLAN|Wireless' "
        "} | Select-Object -ExpandProperty Name"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def set_wifi_enabled(enabled):
    if os.name != "nt":
        return False, "Wi-Fi adapter control is available only on Windows."
    adapters = wifi_adapters()
    if not adapters:
        return False, "I couldn't find a physical Wi-Fi adapter."
    action = "enabled" if enabled else "disabled"
    for name in adapters:
        try:
            result = subprocess.run(
                ["netsh", "interface", "set", "interface", f"name={name}", f"admin={action}"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "Windows denied the adapter change.").strip()
                return False, f"Couldn't turn Wi-Fi {'on' if enabled else 'off'}: {detail}"
        except (OSError, subprocess.SubprocessError) as error:
            return False, f"Couldn't turn Wi-Fi {'on' if enabled else 'off'}: {error}"
    return True, f"Turned Wi-Fi { 'on' if enabled else 'off' }."
