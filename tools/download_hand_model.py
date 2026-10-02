"""Download the official Google MediaPipe Hand Landmarker model bundle."""
import tempfile
import urllib.request
import zipfile
from pathlib import Path


MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)
MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "hand_landmarker.task"
MAX_MODEL_BYTES = 30 * 1024 * 1024


def download_model():
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        MODEL_URL,
        headers={"User-Agent": "MaanavAI/1.0 (optional local hand tracking)"},
    )
    temp_path = None
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_MODEL_BYTES:
                raise ValueError("The model download is larger than the 30 MB safety limit.")
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=MODEL_PATH.parent, suffix=".download", delete=False
            ) as temporary:
                temp_path = Path(temporary.name)
                total = 0
                while chunk := response.read(64 * 1024):
                    total += len(chunk)
                    if total > MAX_MODEL_BYTES:
                        raise ValueError("The model download is larger than the 30 MB safety limit.")
                    temporary.write(chunk)
        if temp_path.stat().st_size < 1_000_000 or temp_path.stat().st_size > MAX_MODEL_BYTES:
            raise ValueError("The downloaded model has an unexpected file size.")
        if not zipfile.is_zipfile(temp_path):
            raise ValueError("The download did not contain a valid MediaPipe task bundle.")
        with zipfile.ZipFile(temp_path) as bundle:
            if not bundle.namelist():
                raise ValueError("The MediaPipe task bundle is empty.")
        temp_path.replace(MODEL_PATH)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return MODEL_PATH


if __name__ == "__main__":
    try:
        print(f"Downloaded official hand-tracking model to: {download_model()}")
    except Exception as error:
        raise SystemExit(f"Could not download the official MediaPipe model: {error}") from error
