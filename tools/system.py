import subprocess
import psutil


# =========================================================
# BATTERY
# =========================================================

def get_battery():

    battery = psutil.sensors_battery()

    if battery is None:
        return False, "I can't detect the battery."

    percent = round(battery.percent)

    if battery.power_plugged:
        status = "charging"
    else:
        status = "on battery"

    return True, f"Battery is at {percent}% and the laptop is {status}."


# =========================================================
# MUTE / UNMUTE
# =========================================================

def mute():

    try:
        from pycaw.pycaw import AudioUtilities

        device = AudioUtilities.GetSpeakers()
        volume = device.EndpointVolume

        volume.SetMute(1, None)

        return True, "Muted the laptop."

    except Exception as error:
        return False, f"I couldn't mute the laptop: {error}"


def unmute():

    try:
        from pycaw.pycaw import AudioUtilities

        device = AudioUtilities.GetSpeakers()
        volume = device.EndpointVolume

        volume.SetMute(0, None)

        return True, "Unmuted the laptop."

    except Exception as error:
        return False, f"I couldn't unmute the laptop: {error}"


# =========================================================
# VOLUME
# =========================================================

def volume_up():

    try:
        for _ in range(5):
            subprocess.run(
                [
                    "powershell",
                    "-Command",
                    "(New-Object -ComObject WScript.Shell).SendKeys([char]175)"
                ],
                creationflags=subprocess.CREATE_NO_WINDOW
            )

        return True, "Volume increased."

    except Exception as error:
        return False, f"I couldn't increase the volume: {error}"


def volume_down():

    try:
        for _ in range(5):
            subprocess.run(
                [
                    "powershell",
                    "-Command",
                    "(New-Object -ComObject WScript.Shell).SendKeys([char]174)"
                ],
                creationflags=subprocess.CREATE_NO_WINDOW
            )

        return True, "Volume decreased."

    except Exception as error:
        return False, f"I couldn't decrease the volume: {error}"


# =========================================================
# LOCK COMPUTER
# =========================================================

def lock_pc():

    try:
        subprocess.Popen(
            ["rundll32.exe", "user32.dll,LockWorkStation"]
        )

        return True, "Locking the laptop."

    except Exception as error:
        return False, f"I couldn't lock the laptop: {error}"


# =========================================================
# SHUTDOWN
# =========================================================

def shutdown_pc():

    try:
        subprocess.Popen(
            ["shutdown", "/s", "/t", "10"]
        )

        return True, "The laptop will shut down in 10 seconds."

    except Exception as error:
        return False, f"I couldn't shut down the laptop: {error}"


# =========================================================
# RESTART
# =========================================================

def restart_pc():

    try:
        subprocess.Popen(
            ["shutdown", "/r", "/t", "10"]
        )

        return True, "The laptop will restart in 10 seconds."

    except Exception as error:
        return False, f"I couldn't restart the laptop: {error}"


# =========================================================
# CANCEL SHUTDOWN / RESTART
# =========================================================

def cancel_shutdown():

    try:
        subprocess.Popen(
            ["shutdown", "/a"]
        )

        return True, "Cancelled the pending shutdown or restart."

    except Exception as error:
        return False, f"I couldn't cancel it: {error}"