"""Windows named-mutex guard preventing duplicate assistants/listeners."""
import atexit
import ctypes
import json
import os
import time
from pathlib import Path

_mutex_handle = None
_OWNER_FILE = Path(__file__).with_name(".maanav_instance.json")
_MUTEX_NAME = "Local\\MaanavAI.SingleInstance"
_WINDOW_TITLE = "Maanav AI"


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    return kernel32


def _main_window():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
    user32.FindWindowW.restype = ctypes.c_void_p
    return user32, user32.FindWindowW(None, _WINDOW_TITLE)


def acquire():
    global _mutex_handle
    if os.name != "nt":
        return True
    kernel32 = _kernel32()
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return False
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        if focus_existing_window():
            return False
        # A crashed/hidden old GUI can leave its process and mutex alive with
        # no window to activate. Give a fresh process time to finish creating
        # its window, then recover only the PID that created this app's mutex.
        if not _recover_windowless_instance(kernel32):
            return False
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle or ctypes.get_last_error() == 183:
            if handle:
                kernel32.CloseHandle(handle)
            return False
    _mutex_handle = handle
    try:
        _OWNER_FILE.write_text(
            json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8"
        )
    except OSError:
        pass
    atexit.register(release)
    return True


def focus_existing_window(title="Maanav AI"):
    """Restore the existing GUI when a desktop shortcut starts a duplicate."""
    if os.name != "nt":
        return False
    try:
        user32, window = _main_window()
        user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
        user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
        user32.FlashWindow.argtypes = (ctypes.c_void_p, ctypes.c_bool)
        if title != _WINDOW_TITLE:
            window = user32.FindWindowW(None, title)
        if not window:
            return False
        user32.ShowWindow(window, 9)  # SW_RESTORE also un-minimizes the window.
        if not user32.SetForegroundWindow(window):
            # Windows may reject foreground changes from a background process.
            # Flashing the existing window makes the launcher action visible.
            user32.FlashWindow(window, True)
        return True
    except (AttributeError, OSError, TypeError):
        return False


def _recover_windowless_instance(kernel32):
    try:
        owner = json.loads(_OWNER_FILE.read_text(encoding="utf-8"))
        pid = int(owner["pid"])
        started = float(owner["started"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False
    if pid <= 0 or pid == os.getpid():
        return False

    # Startup is deliberately allowed a generous window to build its Tk UI.
    deadline = time.monotonic() + max(0.0, 25.0 - (time.time() - started))
    while time.monotonic() < deadline:
        if focus_existing_window():
            return False
        time.sleep(0.2)
    if focus_existing_window():
        return False

    try:
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        process = kernel32.OpenProcess(0x0001 | 0x1000, False, pid)
        if not process:
            return False
        try:
            if focus_existing_window():
                return False
            if not kernel32.TerminateProcess(process, 1):
                return False
            kernel32.WaitForSingleObject(process, 5000)
        finally:
            kernel32.CloseHandle(process)
        return True
    except (AttributeError, OSError, TypeError):
        return False
    return False


def _write_owner_pid():
    try:
        _OWNER_FILE.write_text(
            json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8"
        )
    except OSError:
        pass


def release():
    global _mutex_handle
    if _mutex_handle and os.name == "nt":
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(_mutex_handle)
        _mutex_handle = None
    try:
        owner = json.loads(_OWNER_FILE.read_text(encoding="utf-8"))
        if int(owner.get("pid", -1)) == os.getpid():
            _OWNER_FILE.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
