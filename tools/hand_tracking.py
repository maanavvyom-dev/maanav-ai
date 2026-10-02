"""Optional, camera-local hand gestures for the Maanav galaxy controls."""
import os
import threading
import time
import statistics
from collections import deque
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HandFrame:
    x: float
    y: float
    pinch_distance: float
    open_palm: bool
    peace: bool
    tracked: bool = True


class HandTracker:
    CAMERA_READ_FAILURE_LIMIT = 20
    CAMERA_INDEX_LIMIT = 4
    PINCH_THRESHOLD = 0.48
    HAND_CONNECTIONS = (
        (0, 1), (1, 2), (2, 3), (3, 4),
        (0, 5), (5, 6), (6, 7), (7, 8),
        (5, 9), (9, 10), (10, 11), (11, 12),
        (9, 13), (13, 14), (14, 15), (15, 16),
        (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
    )

    def __init__(self, on_frame, on_error, camera_index=0, on_preview=None):
        self.on_frame = on_frame
        self.on_error = on_error
        self.camera_index = camera_index
        self.on_preview = on_preview
        self._stop = threading.Event()
        self._thread = None

    @staticmethod
    def dependencies_available():
        try:
            os.environ.setdefault(
                "MPLCONFIGDIR",
                str(Path(__file__).resolve().parents[1] / ".cache" / "matplotlib"),
            )
            import cv2  # noqa: F401
            import mediapipe  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def model_path():
        return Path(__file__).resolve().parents[1] / "models" / "hand_landmarker.task"

    def start(self):
        if not self.dependencies_available():
            raise RuntimeError("Hand controls need the optional MediaPipe package.")
        if not self.model_path().is_file():
            raise RuntimeError(
                "The local hand-tracking model is missing. Download hand_landmarker.task "
                "from Google by running: .venv\\Scripts\\python.exe "
                "tools\\download_hand_model.py"
            )
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="MaanavHandCamera", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    @staticmethod
    def frame_from_landmarks(points):
        """Convert normalized MediaPipe landmarks to stable UI gesture values."""
        index_up = points[8].y < points[6].y
        middle_up = points[12].y < points[10].y
        ring_up = points[16].y < points[14].y
        pinky_up = points[20].y < points[18].y
        open_palm = index_up and middle_up and ring_up and pinky_up
        peace = index_up and middle_up and not ring_up and not pinky_up
        dx, dy = points[4].x - points[8].x, points[4].y - points[8].y
        palm_dx, palm_dy = points[0].x - points[9].x, points[0].y - points[9].y
        palm_size = max((palm_dx * palm_dx + palm_dy * palm_dy) ** 0.5, 0.04)
        return HandFrame(
            x=float(points[8].x), y=float(points[8].y),
            # Scale the pinch by palm size so zoom responds similarly when a
            # hand moves closer to or farther from the camera.
            pinch_distance=(dx * dx + dy * dy) ** 0.5 / palm_size,
            open_palm=open_palm, peace=peace, tracked=True,
        )

    @staticmethod
    def draw_landmarks(frame, points, cv2):
        """Draw a bright, visible 21-point skeleton over a mirrored camera frame."""
        height, width = frame.shape[:2]
        if points:
            locations = [
                (max(0, min(width - 1, round(point.x * width))),
                 max(0, min(height - 1, round(point.y * height))))
                for point in points
            ]
            for start, end in HandTracker.HAND_CONNECTIONS:
                cv2.line(frame, locations[start], locations[end], (70, 220, 255), 2, cv2.LINE_AA)
            for index, location in enumerate(locations):
                radius = 7 if index in (4, 8, 12, 16, 20) else 4
                cv2.circle(frame, location, radius + 2, (20, 24, 28), -1, cv2.LINE_AA)
                cv2.circle(frame, location, radius, (85, 255, 120), -1, cv2.LINE_AA)
            cv2.putText(
                frame, "HAND TRACKED  ·  21 POINTS", (15, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.62, (110, 255, 145), 2, cv2.LINE_AA,
            )
        else:
            cv2.putText(
                frame, "SHOW ONE HAND TO THE CAMERA", (15, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.62, (220, 220, 240), 2, cv2.LINE_AA,
            )
        return frame

    @staticmethod
    def _open_camera(cv2, first_index=0):
        """Try available Windows camera backends and nearby device indices."""
        backends = []
        for name, attribute in (("DirectShow", "CAP_DSHOW"),
                                ("Media Foundation", "CAP_MSMF"),
                                ("automatic", "CAP_ANY")):
            backend = getattr(cv2, attribute, None)
            if backend is not None and backend not in [item[1] for item in backends]:
                backends.append((name, backend))

        start = max(0, int(first_index))
        indices = list(range(start, min(start + HandTracker.CAMERA_INDEX_LIMIT, 10)))
        if start:
            indices.extend(index for index in range(min(start, 3)) if index not in indices)
        attempts = []
        for index in indices:
            for name, backend in backends:
                capture = None
                try:
                    capture = cv2.VideoCapture(index, backend)
                    if capture is not None and capture.isOpened():
                        return capture, index, name
                except Exception as error:
                    attempts.append(f"{name} camera {index}: {error}")
                if capture is not None:
                    try:
                        capture.release()
                    except Exception:
                        pass
        hint = (" Ensure Windows Settings > Privacy & security > Camera allows desktop apps, "
                "then close other camera apps and try again.")
        detail = (" Driver errors: " + "; ".join(attempts[:3])) if attempts else ""
        raise RuntimeError(
            "No camera could be opened (checked device indices "
            f"{', '.join(map(str, indices)) or 'none'} with DirectShow, Media Foundation, "
            "and the automatic OpenCV driver)." + hint + detail
        )

    @staticmethod
    def _smooth_frame(history, frame):
        """Dampen cursor jitter and require repeated evidence for gestures."""
        history.append(frame)
        samples = tuple(history)
        return HandFrame(
            x=sum(sample.x for sample in samples) / len(samples),
            y=sum(sample.y for sample in samples) / len(samples),
            pinch_distance=statistics.median(sample.pinch_distance for sample in samples),
            open_palm=sum(sample.open_palm for sample in samples) >= 2,
            peace=sum(sample.peace for sample in samples) >= 2,
            tracked=all(sample.tracked for sample in samples),
        )

    def _run(self):
        capture = None
        try:
            os.environ.setdefault(
                "MPLCONFIGDIR",
                str(Path(__file__).resolve().parents[1] / ".cache" / "matplotlib"),
            )
            import cv2
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision

            capture, camera_index, backend_name = self._open_camera(cv2, self.camera_index)
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            capture.set(cv2.CAP_PROP_FPS, 15)
            if not capture.isOpened():
                raise RuntimeError("The selected camera closed before hand tracking started. Check camera privacy settings and close other camera apps.")
            print(f"[HAND TRACKING] Camera {camera_index} opened with {backend_name}.")

            options = vision.HandLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(self.model_path())),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=0.6,
                min_tracking_confidence=0.5,
            )
            with vision.HandLandmarker.create_from_options(options) as hands:
                failed_reads = 0
                smoothed_frames = deque(maxlen=3)
                while not self._stop.is_set():
                    ok, frame = capture.read()
                    if not ok:
                        failed_reads += 1
                        if failed_reads >= self.CAMERA_READ_FAILURE_LIMIT:
                            raise RuntimeError(
                                "The camera stopped providing frames. Check camera privacy "
                                "settings and close other camera apps, then try again."
                            )
                        self._stop.wait(0.1)
                        continue
                    failed_reads = 0
                    # Mirror the preview coordinate system so hand movement
                    # matches the direction the user sees on screen.
                    frame = cv2.flip(frame, 1)
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                    timestamp_ms = int(time.monotonic() * 1000)
                    results = hands.detect_for_video(image, timestamp_ms)
                    if results.hand_landmarks:
                        landmarks = results.hand_landmarks[0]
                        detected = self.frame_from_landmarks(landmarks)
                        self.on_frame(self._smooth_frame(smoothed_frames, detected))
                    else:
                        landmarks = None
                        # Reset one-shot gestures and pinch baselines when a hand
                        # leaves frame so the next gesture can trigger normally.
                        smoothed_frames.clear()
                        self.on_frame(HandFrame(0.5, 0.5, 1.0, False, False, tracked=False))
                    if self.on_preview is not None:
                        try:
                            self.on_preview(frame, landmarks)
                        except Exception as preview_error:
                            # A preview renderer must never stop Maanav's gesture tracker.
                            print("[HAND TRACKING] Preview callback failed:", preview_error)
                    self._stop.wait(0.04)
        except Exception as error:
            if not self._stop.is_set():
                try:
                    self.on_error(str(error))
                except Exception as callback_error:
                    print("Hand-tracking error callback failed:", callback_error)
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception as error:
                    print("Hand camera cleanup failed:", error)


def model_self_test():
    """Run one synthetic-frame inference without opening a camera."""
    if not HandTracker.dependencies_available():
        print("Hand model check unavailable: install requirements-vision.txt first.")
        return False
    model = HandTracker.model_path()
    if not model.is_file():
        print("Hand model check unavailable: run tools\\download_hand_model.py first.")
        return False
    try:
        import mediapipe as mp
        import numpy as np
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        options = vision.HandLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.IMAGE,
            num_hands=1,
        )
        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=np.zeros((480, 640, 3), dtype=np.uint8),
        )
        with vision.HandLandmarker.create_from_options(options) as detector:
            result = detector.detect(image)
        detections = len(result.hand_landmarks)
        if detections:
            print(f"Hand model check failed: blank image produced {detections} detections.")
            return False
        print("PASS: bundled hand model loaded and inferred on a blank frame; no camera was opened.")
        return True
    except Exception as error:
        print("Hand model check failed:", error)
        return False


def camera_self_test():
    """Check real camera access with the same backend fallbacks as the app."""
    if not HandTracker.dependencies_available():
        print("Camera check unavailable: install requirements-vision.txt first.")
        return False
    capture = None
    try:
        import cv2

        capture, camera_index, backend_name = HandTracker._open_camera(cv2)
        ok, frame = capture.read()
        if not ok or frame is None or getattr(frame, "size", 0) == 0:
            print(f"Camera {camera_index} opened with {backend_name}, but returned no video frame.")
            return False
        print(
            f"PASS: camera {camera_index} opened with {backend_name}; "
            f"received a {frame.shape[1]}x{frame.shape[0]} frame."
        )
        return True
    except Exception as error:
        print("Camera check failed:", error)
        return False
    finally:
        if capture is not None:
            capture.release()


if __name__ == "__main__":
    import sys

    if "--camera-check" in sys.argv:
        raise SystemExit(0 if camera_self_test() else 1)
    if "--self-test" not in sys.argv:
        print("Use --self-test to check the local model or --camera-check to probe a camera.")
        raise SystemExit(2)
    raise SystemExit(0 if model_self_test() else 1)
