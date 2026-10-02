import subprocess
import os
import shutil
import webbrowser


# =========================================================
# WEBSITES
# =========================================================

WEBSITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
}


def open_website(website):
    website = website.lower().strip()

    if website not in WEBSITES:
        return False, f"I don't know that website yet."

    try:
        webbrowser.open(WEBSITES[website])
        return True, f"Opening {website}."

    except Exception as error:
        return False, f"I couldn't open {website}: {error}"


# =========================================================
# APPLICATIONS
# =========================================================

APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",

    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",

    "spotify": "spotify.exe",

    "apple music": "AppleMusic.exe",

    "valorant": "valorant.exe",

    "smart connect": "SmartConnect.exe",
}


# =========================================================
# OPEN APPLICATION
# =========================================================

def open_app(app_name):

    app_name = app_name.lower().strip()

    if app_name not in APPS:
        return False, f"I don't know how to open {app_name} yet."

    program = APPS[app_name]

    try:

        if shutil.which(program):
            subprocess.Popen(program)
            return True, f"Opening {app_name}."

        subprocess.Popen(
            ["cmd", "/c", "start", "", program],
            shell=False
        )

        return True, f"Opening {app_name}."

    except Exception as error:

        return False, f"I couldn't open {app_name}: {error}"


# =========================================================
# AVAILABLE APPLICATIONS
# =========================================================

def available_apps():
    return list(APPS.keys())
