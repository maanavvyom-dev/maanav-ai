"""Standalone live hand tracking preview with visible landmark dots."""
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path

from tools.hand_tracking import HandTracker


class HandTrackingStudio:
    BG = "#0b0b09"
    PANEL = "#17160f"
    GOLD = "#d3b44c"
    TEXT = "#f0ecdc"
    MUTED = "#9b947b"

    def __init__(self, root, *, auto_start=True):
        self.root = root
        self.root.title("Maanav · Hand Tracking Studio")
        self.root.configure(bg=self.BG)
        self.root.geometry("790x650")
        self.root.minsize(640, 510)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.tracker = None
        self._stopping = False
        self.frames = queue.Queue(maxsize=1)
        self.photo = None

        header = tk.Frame(root, bg=self.PANEL)
        header.pack(fill="x")
        tk.Label(
            header, text="HAND TRACKING STUDIO", bg=self.PANEL, fg=self.GOLD,
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left", padx=18, pady=13)
        tk.Label(
            header, text="LOCAL CAMERA · 21 LANDMARK DOTS", bg=self.PANEL,
            fg=self.MUTED, font=("Segoe UI", 9, "bold"),
        ).pack(side="right", padx=18)

        preview_frame = tk.Frame(root, bg="#080807", highlightthickness=1, highlightbackground="#42361c")
        preview_frame.pack(fill="both", expand=True, padx=18, pady=(16, 8))
        self.preview = tk.Label(
            preview_frame, text="Starting camera…", bg="#080807", fg=self.MUTED,
            font=("Segoe UI", 12), compound="center",
        )
        self.preview.pack(fill="both", expand=True)

        footer = tk.Frame(root, bg=self.BG)
        footer.pack(fill="x", padx=18, pady=(3, 16))
        self.status = tk.Label(
            footer, text="CAMERA STARTING", bg=self.BG, fg=self.GOLD,
            font=("Segoe UI", 9, "bold"), anchor="w",
        )
        self.status.pack(side="left", fill="x", expand=True)
        self.retry_button = tk.Button(
            footer, text="RETRY CAMERA", command=self.start,
            bg=self.PANEL, fg=self.TEXT, relief="flat", padx=14, pady=8,
        )
        self.retry_button.pack(side="right", padx=(6, 0))
        self.stop_button = tk.Button(
            footer, text="STOP", command=self.stop,
            bg="#463719", fg=self.TEXT, relief="flat", padx=16, pady=8,
        )
        self.stop_button.pack(side="right")
        tk.Label(
            root,
            text="Your camera frames stay on this computer. Close Maanav’s Hand controls first if another app is using the camera.",
            bg=self.BG, fg=self.MUTED, font=("Segoe UI", 8), anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 12))

        self.root.after(25, self._show_latest_frame)
        if auto_start:
            self.root.after(150, self.start)

    def _queue_frame(self, frame, landmarks):
        import cv2

        marked = HandTracker.draw_landmarks(frame, landmarks, cv2)
        item = (cv2.cvtColor(marked, cv2.COLOR_BGR2RGB), bool(landmarks))
        try:
            self.frames.put_nowait(item)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frames.put_nowait(item)
            except queue.Full:
                pass

    def _show_latest_frame(self):
        try:
            while True:
                frame, tracked = self.frames.get_nowait()
                from PIL import Image, ImageTk

                image = Image.fromarray(frame)
                available_w = max(320, self.preview.winfo_width() - 24)
                available_h = max(240, self.preview.winfo_height() - 24)
                image.thumbnail((available_w, available_h), Image.Resampling.LANCZOS)
                self.photo = ImageTk.PhotoImage(image)
                self.preview.configure(image=self.photo, text="")
                self.status.configure(
                    text="HAND TRACKED · 21 DOTS VISIBLE" if tracked else "CAMERA LIVE · SHOW ONE HAND",
                    fg="#77f58f" if tracked else self.GOLD,
                )
        except queue.Empty:
            pass
        except (tk.TclError, ImportError) as error:
            self.status.configure(text="PREVIEW ERROR · " + str(error)[:90], fg="#ed8d76")
        try:
            self.root.after(25, self._show_latest_frame)
        except tk.TclError:
            pass

    def start(self):
        if self.tracker is not None or self._stopping:
            return
        if not HandTracker.dependencies_available():
            self.status.configure(text="INSTALL OPTIONAL VISION PACKAGES · SEE README", fg="#ed8d76")
            return
        if not HandTracker.model_path().is_file():
            self.status.configure(text="HAND MODEL MISSING · SEE README FOR DOWNLOAD", fg="#ed8d76")
            return
        self.status.configure(text="OPENING CAMERA · CHECK WINDOWS CAMERA PERMISSION", fg=self.GOLD)
        self.tracker = HandTracker(
            on_frame=lambda _frame: None,
            on_error=lambda message: self.root.after(0, self._camera_error, message),
            on_preview=self._queue_frame,
        )
        try:
            self.tracker.start()
        except Exception as error:
            self.tracker = None
            self._camera_error(str(error))

    def _camera_error(self, message):
        self.tracker = None
        self.status.configure(text="CAMERA UNAVAILABLE · " + str(message), fg="#ed8d76")
        self.preview.configure(image="", text="Camera unavailable\n\nAllow desktop apps to use your camera in Windows Privacy settings,\nthen close other apps using it and choose Retry Camera.")
        self.photo = None

    def stop(self):
        tracker, self.tracker = self.tracker, None
        if tracker is None:
            self.status.configure(text="TRACKING STOPPED · CAMERA RELEASED", fg=self.MUTED)
            return
        self._stopping = True
        self.status.configure(text="STOPPING · RELEASING CAMERA", fg=self.MUTED)
        def stop_and_report():
            tracker.stop()
            try:
                self.root.after(0, self._stopped)
            except tk.TclError:
                pass
        threading.Thread(target=stop_and_report, name="HandTrackerStop", daemon=True).start()

    def _stopped(self):
        self._stopping = False
        self.status.configure(text="TRACKING STOPPED · CAMERA RELEASED", fg=self.MUTED)

    def close(self):
        tracker, self.tracker = self.tracker, None
        if tracker is not None:
            tracker.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    smoke_test = "--ui-smoke-test" in sys.argv
    studio = HandTrackingStudio(root, auto_start=not smoke_test)
    if smoke_test:
        root.update_idletasks()
        passed = (
            root.title() == "Maanav · Hand Tracking Studio"
            and studio.status.winfo_exists()
            and studio.retry_button.cget("text") == "RETRY CAMERA"
            and studio.stop_button.cget("text") == "STOP"
        )
        root.destroy()
        print("Standalone hand tracking preview UI:", "PASS" if passed else "FAIL")
        raise SystemExit(0 if passed else 1)
    root.mainloop()


if __name__ == "__main__":
    main()
