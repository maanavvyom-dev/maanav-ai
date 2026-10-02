import os
import threading
import time
import winreg
from pathlib import Path


_START_MENU_CACHE_SECONDS = 60
_START_MENU_CACHE_LOCK = threading.Lock()
_START_MENU_CACHE = None
_START_MENU_CACHE_TIME = 0.0


# =========================================================
# FIND INSTALLED APPS
# =========================================================

def find_installed_apps():
    apps = {}

    registry_locations = [
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
        ),
        (
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
        ),
    ]

    for root, path in registry_locations:

        try:
            key = winreg.OpenKey(root, path)

            count = winreg.QueryInfoKey(key)[0]

            for i in range(count):

                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)

                    try:
                        name = winreg.QueryValueEx(
                            subkey,
                            "DisplayName"
                        )[0]
                    except FileNotFoundError:
                        continue

                    try:
                        location = winreg.QueryValueEx(
                            subkey,
                            "InstallLocation"
                        )[0]
                    except FileNotFoundError:
                        location = ""

                    apps[name] = location

                    subkey.Close()

                except OSError:
                    continue

            key.Close()

        except OSError:
            continue

    return apps


# =========================================================
# FIND START MENU SHORTCUTS
# =========================================================

def _start_menu_paths():
    """Return user and shared Start Menu roots."""
    return [
        Path(os.environ.get("PROGRAMDATA", "")) /
        "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("APPDATA", "")) /
        "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]


def find_start_menu_apps(refresh=False):
    """Find shortcuts once per minute instead of walking both trees per launch."""
    global _START_MENU_CACHE, _START_MENU_CACHE_TIME
    with _START_MENU_CACHE_LOCK:
        now = time.monotonic()
        if (_START_MENU_CACHE is not None and not refresh
                and now - _START_MENU_CACHE_TIME < _START_MENU_CACHE_SECONDS):
            return dict(_START_MENU_CACHE)
        shortcuts = {}
        for start_menu in _start_menu_paths():
            if not start_menu.exists():
                continue
            try:
                for shortcut in start_menu.rglob("*.lnk"):
                    name = shortcut.stem.strip()
                    if name:
                        shortcuts[name] = str(shortcut)
            except OSError as error:
                print(f"[APP DISCOVERY WARNING] Couldn't scan {start_menu}: {error}")

        _START_MENU_CACHE = dict(shortcuts)
        _START_MENU_CACHE_TIME = time.monotonic()
        return dict(shortcuts)


# =========================================================
# FIND ALL APPS
# =========================================================

def find_all_apps():

    apps = find_installed_apps()
    shortcuts = find_start_menu_apps()

    return {
        "installed": apps,
        "shortcuts": shortcuts,
    }


# =========================================================
# TEST
# =========================================================

if __name__ == "__main__":

    data = find_all_apps()

    installed = data["installed"]
    shortcuts = data["shortcuts"]

    print("=" * 60)
    print("MAANAV AI - WINDOWS APP DISCOVERY")
    print("=" * 60)

    print()
    print(f"Registry applications: {len(installed)}")
    print(f"Start Menu shortcuts: {len(shortcuts)}")

    print()
    print("START MENU APPS")
    print("-" * 60)

    for name in sorted(shortcuts):
        print(name)

# =========================================================
# LAUNCH APPLICATION
# =========================================================

def launch_app(app_name):

    app_name = app_name.lower().strip()

    shortcuts = find_start_menu_apps()

    # Exact match
    for name, shortcut in shortcuts.items():

        if name.lower() == app_name:

            try:
                os.startfile(shortcut)
                return True, f"Opening {name}."

            except Exception as error:
                return False, f"I couldn't open {name}: {error}"

    # Partial match
    for name, shortcut in shortcuts.items():

        if app_name in name.lower():

            try:
                os.startfile(shortcut)
                return True, f"Opening {name}."

            except Exception as error:
                return False, f"I couldn't open {name}: {error}"

    return False, f"I couldn't find an application called {app_name}."
