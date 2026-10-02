import ctypes
import json
import os
import re
import shutil
import subprocess
import uuid
import webbrowser
from pathlib import Path

ROUTINE_FILE = Path(__file__).resolve().with_name("routines.json")
DEFAULT_ROUTINE = {
    "name": "Valorant Mode",
    "phrase": "open valo",
    "app": "valorant",
    "close_browsers": True,
    "close_apps": [],
    "power_mode": "performance",
}
DEFAULT_CODING_ROUTINE = {
    "id": "coding-mode",
    "name": "Coding Mode",
    "phrase": "start coding",
    "apps": ["Maanav Project", "ChatGPT"],
    "close_browsers": False,
    "close_apps": [],
    "power_mode": "current",
}
BROWSER_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe",
}
PROTECTED_PROCESSES = {
    "explorer.exe", "winlogon.exe", "services.exe", "dwm.exe", "taskmgr.exe",
    "csrss.exe", "lsass.exe", "svchost.exe", "system", "system idle process",
}
BALANCED_SCHEME = "381b4222-f694-41f0-9685-ff5bb260df2e"
POWER_SAVER_SCHEME = "a1841308-3541-4fab-bc81-f71556f20b4a"
ULTIMATE_SCHEME = "e9a42b02-d5df-448d-aa00-03f14749eb61"
HIGH_SCHEME = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
SCHEME_RE = re.compile(r"([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})")


def _normalize(text):
    return re.sub(r"[^a-z0-9 ]+", " ", str(text).lower()).split()


def _clean_close_apps(value):
    values = value if isinstance(value, (list, tuple, set)) else str(value or "").split(",")
    cleaned = []
    for item in values:
        name = str(item).strip().lower()
        if not name:
            continue
        if not name.endswith(".exe"):
            name += ".exe"
        if name not in PROTECTED_PROCESSES and re.fullmatch(r"[a-z0-9_. -]+\.exe", name):
            cleaned.append(name)
    return list(dict.fromkeys(cleaned))


def _clean_routine(item):
    phrase = str(item.get("phrase", "")).strip()
    raw_apps = item.get("apps")
    if isinstance(raw_apps, str):
        raw_apps = raw_apps.split(",")
    if not isinstance(raw_apps, (list, tuple)):
        raw_apps = [item.get("app", "")]
    apps = list(dict.fromkeys(str(app).strip() for app in raw_apps if str(app).strip()))
    if not phrase or not apps:
        return None
    mode = str(item.get("power_mode", "")).strip().lower()
    if not mode:
        mode = "performance" if item.get("performance_mode") else "current"
    if mode not in {"current", "performance", "balanced", "efficiency"}:
        mode = "current"
    return {
        "id": str(item.get("id") or uuid.uuid4().hex[:12]),
        "name": str(item.get("name") or phrase).strip(),
        "phrase": phrase,
        "phrases": list(dict.fromkeys(
            [phrase] + [str(value).strip() for value in item.get("phrases", []) if str(value).strip()]
        )) if isinstance(item.get("phrases", []), (list, tuple)) else [phrase],
        "apps": apps,
        # Keep the original field for older callers and saved routines.
        "app": apps[0],
        "close_browsers": bool(item.get("close_browsers", False)),
        "close_apps": _clean_close_apps(item.get("close_apps", [])),
        "power_mode": mode,
        # Backward compatibility for routines created by the first editor.
        "performance_mode": mode == "performance",
    }


def load_routines():
    try:
        data = json.loads(ROUTINE_FILE.read_text(encoding="utf-8"))
        items = data.get("routines", []) if isinstance(data, dict) else []
        return [clean for item in items if isinstance(item, dict) and (clean := _clean_routine(item))]
    except (OSError, ValueError, TypeError):
        return [
            {"id": "valorant-mode", **_clean_routine(DEFAULT_ROUTINE)},
            _clean_routine(DEFAULT_CODING_ROUTINE),
        ]


def save_routines(routines):
    clean = [item for raw in routines if isinstance(raw, dict) and (item := _clean_routine(raw))]
    ROUTINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = ROUTINE_FILE.with_suffix(ROUTINE_FILE.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps({"routines": clean}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, ROUTINE_FILE)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return clean


def match_routine(message):
    command = " ".join(_normalize(message))
    if not command:
        return None
    for routine in load_routines():
        phrases = routine.get("phrases") or [routine["phrase"]]
        for raw_phrase in phrases:
            phrase = " ".join(_normalize(raw_phrase))
            if phrase and (command == phrase or command.startswith(phrase + " ")):
                return routine
    return None


def close_process_windows(process_names, label="apps"):
    """Send WM_CLOSE to visible windows belonging to selected processes."""
    if os.name != "nt":
        return False, "Closing application windows is supported on Windows only."
    import psutil
    from ctypes import wintypes

    names = _clean_close_apps(process_names)
    if not names:
        return True, "No additional apps selected to close."
    pid_names = {}
    for process in psutil.process_iter(["pid", "name"]):
        try:
            name = (process.info.get("name") or "").lower()
            if name in names:
                pid_names[process.info["pid"]] = name.removesuffix(".exe")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not pid_names:
        return True, "None of the selected apps are open."

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    enum_windows = user32.EnumWindows
    enum_windows.argtypes = [callback_type, wintypes.LPARAM]
    enum_windows.restype = wintypes.BOOL
    get_pid = user32.GetWindowThreadProcessId
    get_pid.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    post_message = user32.PostMessageW
    post_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    is_visible = user32.IsWindowVisible
    is_visible.argtypes = [wintypes.HWND]

    asked_to_close = set()
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        get_pid(hwnd, ctypes.byref(pid))
        if pid.value in pid_names and is_visible(hwnd):
            if post_message(hwnd, 0x0010, 0, 0):  # WM_CLOSE
                asked_to_close.add(pid_names[pid.value])
        return True

    enum_windows(callback_type(visit), 0)
    if asked_to_close:
        return True, f"Asked {label} to close: " + ", ".join(sorted(asked_to_close)) + "."
    return True, f"No visible {label} windows needed closing."


def close_browser_windows():
    """Ask each open browser window to close cleanly so tabs close normally."""
    return close_process_windows(BROWSER_PROCESSES, "browser windows")


def _run_powercfg(*args):
    result = subprocess.run(
        ["powercfg", *args], capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "powercfg failed").strip())
    return result.stdout


def _launch_routine_app(app_name):
    """Launch an installed app, with reliable aliases for coding tools."""
    name = app_name.strip()
    normalized = re.sub(r"[^a-z0-9]+", "", name.lower())

    if normalized in {"maanavproject", "maanavaiproject", "maanavworkspace"}:
        try:
            project_path = str(ROUTINE_FILE.parent)
            if os.name == "nt":
                # Opening the workspace makes its scoped .vscode/mcp.json
                # available to compatible VS Code MCP clients.
                subprocess.Popen(
                    ["cmd", "/c", "start", "", "code", project_path],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                executable = shutil.which("code")
                if not executable:
                    return False, "Visual Studio Code is not installed."
                subprocess.Popen([executable, project_path])
            return True, "Opening the Maanav AI project in Visual Studio Code."
        except OSError as error:
            return False, f"Couldn't open the Maanav AI project: {error}"

    try:
        from app_launcher import launch_app as discover_app
        from tools.apps import open_app
        ok, message = discover_app(name)
        if not ok:
            ok, message = open_app(name)
        if ok:
            return True, message
    except Exception:
        pass

    if normalized in {"chatgpt", "openai chatgpt"}:
        try:
            webbrowser.open("https://chatgpt.com")
            return True, "Opened ChatGPT in your browser."
        except Exception as error:
            return False, f"Couldn't open ChatGPT: {error}"

    if normalized in {"vscode", "visualstudiocode", "code"}:
        executable = shutil.which("code") or shutil.which("code.cmd")
        try:
            if executable:
                subprocess.Popen([executable], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return True, "Opening Visual Studio Code."
            if os.name == "nt":
                subprocess.Popen(["cmd", "/c", "start", "", "code"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return True, "Opening Visual Studio Code."
        except OSError as error:
            return False, f"Couldn't open Visual Studio Code: {error}"
    return False, f"I couldn't find an application called {name}."


def _schemes():
    output = _run_powercfg("/list")
    return [(match.group(1).lower(), line.lower()) for line in output.splitlines() if (match := SCHEME_RE.search(line))]


def _existing_custom_plan(schemes, name):
    for guid, line in schemes:
        if name.lower() in line:
            return guid
    return None


def _create_custom_plan(mode, base_scheme):
    name = "Maanav Game Performance" if mode == "performance" else "Maanav Power Saver"
    duplicate_output = _run_powercfg("/duplicatescheme", base_scheme)
    match = SCHEME_RE.search(duplicate_output)
    if not match:
        raise RuntimeError("Windows did not return an ID for the new power plan.")
    plan_id = match.group(1).lower()
    _run_powercfg("/changename", plan_id, name, f"Maanav {mode} profile")
    if mode == "performance":
        _run_powercfg("/setacvalueindex", plan_id, "SUB_PROCESSOR", "PROCTHROTTLEMAX", "100")
        _run_powercfg("/setacvalueindex", plan_id, "SUB_PROCESSOR", "PERFBOOSTMODE", "2")
        _run_powercfg("/setdcvalueindex", plan_id, "SUB_PROCESSOR", "PROCTHROTTLEMAX", "100")
        _run_powercfg("/setdcvalueindex", plan_id, "SUB_PROCESSOR", "PERFBOOSTMODE", "2")
    else:
        for power_source in ("ac", "dc"):
            _run_powercfg(f"/set{power_source}valueindex", plan_id, "SUB_PROCESSOR", "PROCTHROTTLEMAX", "65")
            _run_powercfg(f"/set{power_source}valueindex", plan_id, "SUB_PROCESSOR", "PERFBOOSTMODE", "0")
    return plan_id


def set_power_mode(mode):
    mode = str(mode or "current").lower().strip()
    if mode == "current":
        return True, "Kept the current Windows power mode."
    try:
        schemes = _schemes()
        available = {guid for guid, _ in schemes}
        if mode == "balanced":
            target = BALANCED_SCHEME if BALANCED_SCHEME in available else None
            message = "Switched to Windows Balanced mode."
        elif mode == "efficiency":
            target = POWER_SAVER_SCHEME if POWER_SAVER_SCHEME in available else _existing_custom_plan(schemes, "maanav power saver")
            if target is None:
                if BALANCED_SCHEME not in available:
                    raise RuntimeError("Windows Balanced plan is unavailable as a base for Power saver.")
                target = _create_custom_plan("efficiency", BALANCED_SCHEME)
            message = "Switched to Windows Power saver mode."
        elif mode == "performance":
            target = next((guid for guid in (ULTIMATE_SCHEME, HIGH_SCHEME) if guid in available), None)
            if target is None:
                target = _existing_custom_plan(schemes, "maanav game performance")
            if target is None:
                if BALANCED_SCHEME not in available:
                    raise RuntimeError("Windows Balanced plan is unavailable as a base for Performance mode.")
                target = _create_custom_plan("performance", BALANCED_SCHEME)
            message = "Switched to Windows Best performance mode."
        else:
            return False, f"Unknown Windows power mode: {mode}."
        if not target:
            return False, f"The Windows {mode} power plan is not available."
        _run_powercfg("/setactive", target)
        return True, message
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        return False, f"Couldn't switch Windows power mode: {error}"


def enable_performance_mode():
    return set_power_mode("performance")


def execute_routine(routine):
    results = []
    ok_all = True
    extra_apps = _clean_close_apps(routine.get("close_apps", []))
    if extra_apps:
        ok, message = close_process_windows(extra_apps, "selected apps")
        ok_all = ok_all and ok
        results.append(message)
    if routine.get("close_browsers"):
        ok, message = close_browser_windows()
        ok_all = ok_all and ok
        results.append(message)

    mode = routine.get("power_mode")
    if not mode and routine.get("performance_mode"):
        mode = "performance"
    if mode and mode != "current":
        ok, message = set_power_mode(mode)
        ok_all = ok_all and ok
        results.append(message)

    apps = routine.get("apps")
    if isinstance(apps, str):
        apps = apps.split(",")
    if not apps:
        apps = [routine.get("app", "")]
    for app_name in apps:
        app_name = str(app_name).strip()
        if not app_name:
            continue
        ok, message = _launch_routine_app(app_name)
        ok_all = ok_all and ok
        results.append(message)
    return ok_all, "\n".join(results) if results else "This routine has no actions assigned."
