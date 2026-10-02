import ctypes
import os
import uuid
from pathlib import Path
import re


TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx", ".json",
    ".csv", ".tsv", ".html", ".htm", ".css", ".xml", ".yaml", ".yml",
    ".ini", ".cfg", ".log", ".ps1", ".bat", ".cmd", ".sql", ".java",
    ".c", ".h", ".cpp", ".cs", ".go", ".rs", ".toml",
}


def _search_roots(extra_roots=()):
    """Return existing user folders plus explicitly configured search folders."""
    roots = []
    for raw in [*FOLDERS.values(), *(extra_roots or ())]:
        if not raw:
            continue
        try:
            path = Path(raw).expanduser().resolve()
            if path.is_dir() and path not in roots:
                roots.append(path)
        except (OSError, TypeError, ValueError):
            continue
    return roots


# =========================================================
# WINDOWS SPECIAL FOLDERS
# =========================================================

KNOWN_FOLDERS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def get_windows_folder(folder_name):
    """Resolve a Windows known folder without starting PowerShell."""
    guid_text = KNOWN_FOLDERS.get(str(folder_name).lower())
    if not guid_text:
        return None

    if os.name != "nt":
        conventional = {
            "desktop": "Desktop", "downloads": "Downloads", "documents": "Documents",
            "pictures": "Pictures", "music": "Music", "videos": "Videos",
        }
        path = Path.home() / conventional[folder_name.lower()]
        return str(path) if path.is_dir() else None

    try:
        folder_uuid = uuid.UUID(guid_text)
        folder_id = _GUID(
            folder_uuid.fields[0],
            folder_uuid.fields[1],
            folder_uuid.fields[2],
            (ctypes.c_ubyte * 8).from_buffer_copy(folder_uuid.bytes[8:]),
        )
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.SHGetKnownFolderPath.argtypes = (
            ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_wchar_p),
        )
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        allocated_path = ctypes.c_wchar_p()
        result = shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(allocated_path)
        )
        if result != 0 or not allocated_path.value:
            return None
        try:
            return allocated_path.value
        finally:
            co_task_mem_free = ctypes.WinDLL("ole32").CoTaskMemFree
            co_task_mem_free.argtypes = (ctypes.c_void_p,)
            co_task_mem_free.restype = None
            co_task_mem_free(ctypes.cast(allocated_path, ctypes.c_void_p))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


FOLDERS = {
    name: get_windows_folder(name) for name in KNOWN_FOLDERS
}

# =========================================================
# OPEN FOLDER
# =========================================================

def open_folder(folder_name):

    folder_name = str(folder_name or "").lower().strip()

    if folder_name not in FOLDERS:
        return False, f"I don't know the {folder_name} folder."

    path = FOLDERS[folder_name]

    if not path or not os.path.isdir(path):
        return False, f"I couldn't locate your {folder_name} folder. Check that it is available in Windows."

    try:
        os.startfile(path)
        return True, f"Opening {folder_name}."

    except Exception as error:
        return False, f"I couldn't open {folder_name}: {error}"


# =========================================================
# AVAILABLE FOLDERS
# =========================================================

def available_folders():
    return list(FOLDERS.keys())

# =========================================================
# SEARCH FILES
# =========================================================

def search_files(query, max_results=20, extra_roots=()):

    query = query.lower().strip()

    if not query:
        return False, "Tell me what file you want me to find."

    search_locations = _search_roots(extra_roots)

    results = []

    for location in search_locations:

        if not location or not os.path.exists(location):
            continue

        try:
            for root, dirs, files in os.walk(location):

                # Don't search hidden/system-like folders
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".")
                ]

                for filename in files:

                    if query in filename.lower():

                        results.append(
                            os.path.join(root, filename)
                        )

                        if len(results) >= max_results:
                            break

                if len(results) >= max_results:
                    break

        except (PermissionError, OSError):
            continue

        if len(results) >= max_results:
            break

    if not results:
        return False, f"I couldn't find any files matching {query}."

    return True, results


def search_file_contents(query, max_results=10, extra_roots=(), max_file_bytes=2_000_000):
    """Search text snippets in common text formats inside approved user folders.

    Scanning is bounded, skips hidden/system folders and large/binary files, and
    never follows symlinked directories. extra_roots must come from user settings.
    """
    query = str(query or "").strip()
    if len(query) < 2:
        return False, "Give me at least two characters to search for inside your files."
    needle = query.casefold()
    matches = []
    scanned = 0
    scan_limit = 6000
    for location in _search_roots(extra_roots):
        try:
            for root, dirs, files in os.walk(location, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("$")]
                for filename in files:
                    lowered_name = filename.casefold()
                    if (Path(filename).suffix.lower() not in TEXT_EXTENSIONS
                            or any(secret in lowered_name for secret in ("secret", "credential", "private_key", "password"))):
                        continue
                    scanned += 1
                    if scanned > scan_limit:
                        break
                    path = Path(root) / filename
                    try:
                        if path.is_symlink():
                            continue
                        if path.stat().st_size > max_file_bytes:
                            continue
                        with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
                            for line_number, line in enumerate(stream, 1):
                                if needle in line.casefold():
                                    snippet = re.sub(r"\s+", " ", line).strip()[:240]
                                    matches.append((str(path), line_number, snippet))
                                    if len(matches) >= max_results:
                                        break
                    except (OSError, PermissionError):
                        continue
                    if len(matches) >= max_results:
                        break
                if scanned > scan_limit or len(matches) >= max_results:
                    break
        except (OSError, PermissionError):
            continue
        if scanned > scan_limit or len(matches) >= max_results:
            break
    if not matches:
        suffix = " (search limit reached)" if scanned > scan_limit else ""
        return False, f"I couldn't find that text in searchable files{suffix}."
    return True, matches


# =========================================================
# OPEN FILE
# =========================================================

def open_file(path):

    if not isinstance(path, (str, os.PathLike)) or not str(path).strip():
        return False, "Give me the path to a file you want me to open."
    if not os.path.isfile(path):
        return False, "That file doesn't exist."

    try:
        os.startfile(path)
        return True, f"Opening {os.path.basename(path)}."

    except Exception as error:
        return False, f"I couldn't open the file: {error}"


def create_file(filename, folder_name="documents"):
    """Create one empty file in a known user folder without overwriting data."""
    name = str(filename or "").strip()
    folder = str(folder_name or "documents").lower().strip()
    if folder not in FOLDERS or not FOLDERS[folder]:
        return False, f"I couldn't find your {folder} folder."
    if (not name or name in {".", ".."} or Path(name).name != name
            or any(char in name for char in '<>:"/\\|?*') or name.endswith((".", " "))):
        return False, "Use a simple file name without a folder path or reserved characters."
    path = Path(FOLDERS[folder]) / name
    try:
        with path.open("x", encoding="utf-8"):
            pass
        return True, f"Created an empty file: {path}"
    except FileExistsError:
        return False, f"I left the existing file unchanged: {path}"
    except OSError as error:
        return False, f"I couldn't create that file: {error}"
