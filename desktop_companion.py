"""Floating pixel companion for the existing Maanav desktop app."""
import ctypes
import ctypes.wintypes
import io
import math
import os
import queue
import random
import struct
import tempfile
import threading
import time
import tkinter as tk
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import winsound
except ImportError:
    winsound = None


class _GlobalShortcut:
    """Listen for Ctrl+Alt+Space and Mouse Button 5 on Windows."""
    HOTKEY_ID = 0x4D41
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    WH_MOUSE_LL = 14
    WM_XBUTTONDOWN = 0x020B
    XBUTTON2 = 2

    def __init__(self, callback, on_unavailable):
        self.callback, self.on_unavailable = callback, on_unavailable
        self.events = queue.SimpleQueue()
        self.thread = None
        self.thread_id = None
        self.registered = False
        self.mouse_registered = False
        self._mouse_hook = None
        self._mouse_hook_proc = None
        self.ready = threading.Event()

    def start(self):
        if os.name != "nt":
            return False
        self.thread = threading.Thread(target=self._run, name="MaanavCompanionHotkey", daemon=True)
        self.thread.start()
        self.ready.wait(1.0)
        return self.registered or self.mouse_registered

    def _run(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.thread_id = kernel32.GetCurrentThreadId()
        message = ctypes.wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        self.registered = bool(
            user32.RegisterHotKey(None, self.HOTKEY_ID, 0x0002 | 0x0001 | 0x4000, 0x20)
        )
        if not self.registered:
            self.events.put(("keyboard_unavailable", ctypes.get_last_error()))

        class MouseHookData(ctypes.Structure):
            _fields_ = [
                ("x", ctypes.c_long), ("y", ctypes.c_long),
                ("mouseData", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("extraInfo", ctypes.c_size_t),
            ]

        hook_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t
        )
        user32.CallNextHookEx.argtypes = (
            ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t,
        )
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
        user32.UnhookWindowsHookEx.restype = ctypes.c_int

        def on_mouse(code, message_id, data_ptr):
            if code >= 0 and message_id == self.WM_XBUTTONDOWN:
                data = ctypes.cast(data_ptr, ctypes.POINTER(MouseHookData)).contents
                if data.mouseData >> 16 == self.XBUTTON2:
                    self.events.put(("mouse5", None))
            return user32.CallNextHookEx(None, code, message_id, data_ptr)

        self._mouse_hook_proc = hook_proc_type(on_mouse)
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        self._mouse_hook = user32.SetWindowsHookExW(
            self.WH_MOUSE_LL, self._mouse_hook_proc, None, 0
        )
        self.mouse_registered = bool(self._mouse_hook)
        if not self.mouse_registered:
            self.events.put(("mouse_unavailable", ctypes.get_last_error()))
        if not self.registered and not self.mouse_registered:
            self.ready.set()
            return
        self.ready.set()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0 or message.message == self.WM_QUIT:
                    break
                if message.message == self.WM_HOTKEY and message.wParam == self.HOTKEY_ID:
                    self.events.put(("hotkey", None))
        finally:
            if self.registered:
                user32.UnregisterHotKey(None, self.HOTKEY_ID)
            if self._mouse_hook:
                user32.UnhookWindowsHookEx(self._mouse_hook)
                self._mouse_hook = None
            self.mouse_registered = False
            self.registered = False

    def stop(self):
        if self.thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)
            except Exception:
                pass
        if self.thread and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=0.8)


class DesktopCompanion:
    """Always-on-top pixel pet, transient chat, shortcut, voice, and screen look."""
    KEY = "#ff00ff"

    def __init__(self, root, *, ask_ai, speak, listen, session_id, add_turn, load_history,
                 memory_action=None, offline_voice=False, call_mode=None,
                 request_begin=None, on_turn=None, dictation_mode=None,
                 request_finish=None, on_hotkey=None, computer_task=None,
                 pet_sounds_enabled=None, wander_enabled=None):
        self.root, self.ask_ai, self.speak, self.listen = root, ask_ai, speak, listen
        self.session_id, self.add_turn, self.load_history = session_id, add_turn, load_history
        self.offline_voice = bool(offline_voice)
        self.ready_status = "LOCAL WHISPER · TEXT PET" if self.offline_voice else "TEXT PET · WISPR READY"
        self.call_mode = call_mode
        self.pet_sounds_enabled = pet_sounds_enabled or (lambda: False)
        self.wander_enabled = wander_enabled or (lambda: True)
        self.on_hotkey = on_hotkey or (lambda: self.show_chat(focus=True))
        self.computer_task = computer_task
        self.dictation_mode = dictation_mode
        self.dictation_active = False
        self._dictation_reply_pending = False
        self.request_begin = request_begin or (lambda: None)
        self.request_finish = request_finish or (lambda _cancel_event: True)
        self.on_turn = on_turn or (lambda _session_id: None)
        self.pinned = False
        self.call_active = False
        self.hidden = False
        self.frame = 0
        self._pet_mood = "idle"
        self._hand_pet_active = False
        self._activity_events = queue.SimpleQueue()
        self._last_queued_activity = None
        self._wander_target = None
        self._wander_start = None
        self._wander_started = 0.0
        self._wander_duration = 0.0
        self._wander_next_time = time.monotonic() + random.uniform(2.0, 4.0)
        self.notification_window = None
        self._notification_job = None
        self._computer_task_active = False
        self._computer_task_cancel = None
        self._computer_window_state = None
        self._sound_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="MaanavPetSound")
        self.close_job = None
        self.drag_origin = None
        self._create_pet()
        self._create_chat()
        self._register_hotkey()
        self._animate()
        self._watch_fullscreen()

    def _create_pet(self):
        self.pet = tk.Toplevel(self.root)
        self.pet.overrideredirect(True)
        self.pet.attributes("-topmost", True)
        self.pet.configure(bg=self.KEY)
        try:
            self.pet.wm_attributes("-transparentcolor", self.KEY)
        except tk.TclError:
            self.pet.configure(bg="#17160f")
        self.pet.geometry("86x144+40+180")
        self.canvas = tk.Canvas(self.pet, width=86, height=144, bg=self.KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.pet_image = None
        image_path = Path(__file__).resolve().parent / "assets" / "maanav_pet_transparent.png"
        if image_path.is_file():
            try:
                self.pet_image = tk.PhotoImage(file=str(image_path)).subsample(2, 2)
            except tk.TclError as error:
                print("Pet reference image could not be loaded; using the built-in pixel pet:", error)
        self.canvas.bind("<Enter>", self._hover_open)
        self.canvas.bind("<Leave>", self._schedule_close)
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._pin_click)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.show_chat(focus=True))

    def _create_chat(self):
        self.chat_window = tk.Toplevel(self.root)
        self.chat_window.withdraw()
        self.chat_window.title("Nesuko · Pet Input")
        self.chat_window.configure(bg="#0e0e0b")
        self.chat_window.attributes("-topmost", True)
        self.chat_window.geometry("440x136")
        self.chat_window.minsize(390, 124)
        self.chat_window.protocol("WM_DELETE_WINDOW", self.hide)
        head = tk.Frame(self.chat_window, bg="#17160f")
        head.pack(fill="x")
        tk.Label(head, text="NESUKO  ·  PET", bg="#17160f", fg="#d3b44c", font=("Segoe UI", 9, "bold")).pack(side="left", padx=10, pady=6)
        self.status = tk.Label(head, text=self.ready_status, bg="#17160f", fg="#9b947b", font=("Segoe UI", 8), anchor="w")
        self.status.pack(side="left", padx=4, fill="x", expand=True)
        tk.Button(head, text="−", command=self.minimize_chat, bg="#17160f", fg="#c9c1a7", relief="flat", bd=0).pack(side="right", padx=4)
        tk.Button(head, text="×", command=self.hide, bg="#17160f", fg="#c9c1a7", relief="flat", bd=0).pack(side="right", padx=4)
        composer = tk.Frame(self.chat_window, bg="#17160f")
        composer.pack(fill="both", expand=True, padx=7, pady=(5, 6))
        self.entry = tk.Entry(composer, bg="#17160f", fg="#f0ecdc", insertbackground="#f0ecdc", relief="flat", font=("Segoe UI", 10))
        self.entry.pack(fill="x", padx=7, pady=(2, 5), ipady=4)
        self.entry.bind("<Return>", self.send)
        actions = tk.Frame(composer, bg="#17160f")
        actions.pack(fill="x", padx=2, pady=(0, 1))
        self.pin_button = tk.Button(actions, text="PIN", command=self.toggle_pin, bg="#17160f", fg="#9b947b", relief="flat", bd=0)
        self.pin_button.pack(side="left")
        self.call_button = tk.Button(actions, text="TALK", command=self.call_voice, bg="#17160f", fg="#d3b44c", relief="flat", bd=0)
        self.call_button.pack(side="left", padx=3)
        tk.Button(actions, text="LOOK", command=self.capture_screen, bg="#17160f", fg="#d3b44c", relief="flat", bd=0).pack(side="left", padx=3)
        self.use_pc_button = tk.Button(actions, text="USE PC", command=self.start_computer_task, bg="#17160f", fg="#e7a56f", relief="flat", bd=0)
        self.use_pc_button.pack(side="left", padx=3)
        tk.Button(actions, text="WISPR", command=self.focus_for_wispr, bg="#17160f", fg="#c9c1a7", relief="flat", bd=0).pack(side="left", padx=3)
        tk.Button(actions, text="SEND", command=self.send, bg="#806a2d", fg="white", relief="flat", bd=0, padx=10).pack(side="right", padx=4)
        self.chat_window.bind("<Enter>", self._cancel_close)
        self.chat_window.bind("<Leave>", self._schedule_close)

    def _draw_pet(self):
        c = self.canvas
        c.delete("all")
        if self.pet_image is not None:
            bob_scale = 2 if self._pet_mood == "idle" else 4
            bob = (0, 1, bob_scale, 1, 0, -1, -bob_scale, -1)[(self.frame // 2) % 8]
            c.create_image(43, 72 + bob, image=self.pet_image)
            speed = 0.20 if self._pet_mood != "idle" else 0.10
            sparkle = "#fff2a8" if self.frame % 10 < 5 else "#d3b44c"
            for index in range(4):
                angle = self.frame * speed + index * math.pi / 2
                x = 43 + math.cos(angle) * 38
                y = 72 + bob + math.sin(angle) * 65
                c.create_rectangle(x - 1, y - 3, x + 2, y + 3, fill=sparkle, outline="")
                c.create_rectangle(x - 3, y - 1, x + 4, y + 2, fill=sparkle, outline="")
            if self._pet_mood == "petting":
                for x, y in ((62, 20), (68, 20), (59, 23), (71, 23),
                             (62, 26), (65, 29), (68, 26)):
                    c.create_rectangle(x, y, x + 4, y + 4, fill="#ff9bbc", outline="")
            return
        outline, gold, light, shadow = "#463719", "#c9a63f", "#f6e5a6", "#17140b"
        def p(x, y, w, h, color):
            c.create_rectangle(x, y, x+w, y+h, fill=color, outline=color)
        # A tiny gold star familiar: dark pixel outline, warm eyes, and orbiting sparks.
        for x, y in ((14, 15), (83, 19), (15, 85), (88, 77)):
            p(x, y, 4, 4, light if self.frame % 8 < 4 else gold)
        p(47, 12, 8, 8, gold); p(44, 16, 14, 4, light)
        p(31, 28, 42, 9, outline); p(23, 36, 58, 39, outline)
        p(28, 38, 48, 31, gold); p(24, 47, 8, 19, gold); p(72, 47, 8, 19, gold)
        p(33, 34, 38, 9, light); p(30, 40, 44, 8, gold)
        p(37, 49, 9, 12, shadow); p(58, 49, 9, 12, shadow)
        if self.frame % 24 in (20, 21):
            p(37, 57, 9, 3, outline); p(58, 57, 9, 3, outline)
        else:
            p(39, 50, 4, 6, "#fff7d5"); p(60, 50, 4, 6, "#fff7d5")
            p(42, 55, 3, 4, "#755a24"); p(63, 55, 3, 4, "#755a24")
        p(31, 63, 7, 4, "#8f4e30"); p(70, 63, 7, 4, "#8f4e30")
        p(48, 65, 8, 4, outline)
        p(32, 75, 40, 8, outline); p(26, 82, 52, 13, outline)
        p(34, 76, 36, 8, gold); p(31, 84, 46, 8, light)
        p(37, 91, 11, 6, outline); p(59, 91, 11, 6, outline)
        if self.frame % 8 < 4:
            p(18, 58, 4, 4, light); p(82, 40, 3, 3, gold)
        else:
            p(86, 55, 4, 4, light); p(18, 40, 3, 3, gold)

    def _animate(self):
        activity = None
        try:
            while True:
                activity = self._activity_events.get_nowait()
        except queue.Empty:
            pass
        if activity is not None:
            self._pet_mood = {
                "listening": "listening", "thinking": "thinking",
                "speaking": "happy",
            }.get(activity, "idle")
            if activity == "listening":
                self._play_chirp("hello")
            elif activity == "speaking":
                self._play_chirp("talk")
        if not self.hidden:
            self.frame += 1
            self._draw_pet()
            self._advance_wander()
        try: self.root.after(55, self._animate)
        except tk.TclError: return

    def _advance_wander(self):
        """Gently move the pet to random safe spots when it is not in use."""
        now = time.monotonic()
        if not self.wander_enabled():
            self._wander_target = None
            return
        if (self.hidden or self.drag_origin or self.dictation_active or self._hand_pet_active
                or self._dictation_reply_pending
                or self.chat_window.state() != "withdrawn"
                or self.pet.state() == "withdrawn"):
            self._wander_target = None
            self._wander_next_time = now + 1.5
            return
        if self._wander_target is None:
            if now < self._wander_next_time:
                return
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            pet_w = max(self.pet.winfo_width(), 82)
            pet_h = max(self.pet.winfo_height(), 144)
            current_x, current_y = self.pet.winfo_x(), self.pet.winfo_y()
            self._wander_start = (current_x, current_y)
            self._wander_target = (
                random.randint(12, max(12, screen_w - pet_w - 12)),
                random.randint(34, max(34, screen_h - pet_h - 48)),
            )
            if (abs(self._wander_target[0] - current_x) < 90
                    and abs(self._wander_target[1] - current_y) < 70):
                self._wander_target = (
                    max(12, min(screen_w - pet_w - 12, current_x + random.choice((-1, 1)) * 150)),
                    max(34, min(screen_h - pet_h - 48, current_y + random.choice((-1, 1)) * 110)),
                )
            self._wander_started = now
            self._wander_duration = random.uniform(1.3, 2.4)

        progress = min(1.0, (now - self._wander_started) / self._wander_duration)
        eased = progress * progress * (3 - 2 * progress)
        x0, y0 = self._wander_start
        x1, y1 = self._wander_target
        x = round(x0 + (x1 - x0) * eased)
        y = round(y0 + (y1 - y0) * eased)
        self.pet.geometry(f"+{x}+{y}")
        if progress >= 1.0:
            self._wander_target = None
            self._wander_next_time = now + random.uniform(1.8, 4.2)

    def activity_changed(self, activity):
        """Queue main-assistant voice activity for the Tk animation loop."""
        if activity == self._last_queued_activity:
            return
        self._last_queued_activity = activity
        self._activity_events.put(activity)

    def hand_track_pet(self, x, y, *, tracked=True):
        """Let a camera-tracked index finger pet Nesuko when it reaches her."""
        if not tracked:
            self._hand_pet_active = False
            if self._pet_mood == "petting":
                self._pet_mood = "idle"
            return
        screen_x = float(x) * self.root.winfo_screenwidth()
        screen_y = float(y) * self.root.winfo_screenheight()
        pet_x, pet_y = self.pet.winfo_rootx(), self.pet.winfo_rooty()
        touching = (
            pet_x - 10 <= screen_x <= pet_x + self.pet.winfo_width() + 10
            and pet_y - 10 <= screen_y <= pet_y + self.pet.winfo_height() + 10
        )
        if touching:
            self._pet_mood = "petting"
            if not self._hand_pet_active:
                self._play_chirp("pet")
        elif self._hand_pet_active:
            self._pet_mood = "idle"
        self._hand_pet_active = touching

    def _hover_open(self, _e=None):
        was_hidden = self.chat_window.state() == "withdrawn"
        self._cancel_close(); self.show_chat()
        if was_hidden:
            self._play_chirp("hello")

    def _drag_start(self, e): self.drag_origin = (e.x_root, e.y_root, self.pet.winfo_x(), self.pet.winfo_y())
    def _drag_move(self, e):
        if self.drag_origin:
            sx, sy, ox, oy = self.drag_origin
            self.pet.geometry(f"+{ox+e.x_root-sx}+{oy+e.y_root-sy}")
            if self.chat_window.state() != "withdrawn": self._place_chat()
    def _pin_click(self, e):
        if self.drag_origin and abs(e.x_root-self.drag_origin[0]) < 5 and abs(e.y_root-self.drag_origin[1]) < 5: self.toggle_pin()
        self.drag_origin = None

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.pin_button.configure(text="PINNED" if self.pinned else "PIN", fg="#d3b44c" if self.pinned else "#9b947b")
        if not self.pinned: self._schedule_close()
    def _cancel_close(self, _e=None):
        if self.close_job:
            try: self.root.after_cancel(self.close_job)
            except tk.TclError: pass
            self.close_job = None
    def _schedule_close(self, _e=None):
        self._cancel_close()
        if not self.pinned: self.close_job = self.root.after(650, self._close_if_outside)
    def _close_if_outside(self):
        self.close_job = None
        try:
            x,y = self.root.winfo_pointerxy()
            for w in (self.pet,self.chat_window):
                if w.state() != "withdrawn" and w.winfo_rootx() <= x <= w.winfo_rootx()+w.winfo_width() and w.winfo_rooty() <= y <= w.winfo_rooty()+w.winfo_height(): return
            self.chat_window.withdraw()
            if not self._dictation_reply_pending:
                self._set_dictation_active(False)
        except tk.TclError: pass
    def _place_chat(self):
        width, height = 360, 124
        x = self.pet.winfo_x() + self.pet.winfo_width() - width
        y = self.pet.winfo_y() + self.pet.winfo_height() - 8
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.chat_window.geometry(
            f"{width}x{height}+{max(0, min(x, sw-width))}+{max(0, min(y, sh-height-8))}"
        )
    def show_chat(self, focus=False):
        if self.hidden: self.hidden=False; self.pet.deiconify()
        self._place_chat(); self.chat_window.deiconify(); self.chat_window.lift(); self.pet.lift()
        if focus: self.chat_window.after(80,self.entry.focus_force)
    def minimize_chat(self):
        self.chat_window.withdraw()
        if not self._dictation_reply_pending:
            self._set_dictation_active(False)
    def hide(self):
        if not self._dictation_reply_pending:
            self._set_dictation_active(False)
        if self.call_active:
            self.set_call_active(False)
        self.hidden = True
        self.chat_window.withdraw()
        self.pet.withdraw()

    def _dispatch_shortcut_events(self):
        """Deliver worker-thread hotkey events from Tk's main event loop."""
        shortcut = getattr(self, "_shortcut", None)
        if shortcut is not None:
            while True:
                try:
                    event, value = shortcut.events.get_nowait()
                except queue.Empty:
                    break
                if event == "keyboard_unavailable":
                    self._shortcut_unavailable(value)
                elif event == "mouse_unavailable":
                    print(f"[VOICE MOUSE BUTTON] Mouse Button 5 unavailable (Windows error {value}).")
                elif event in ("hotkey", "mouse5"):
                    self.on_hotkey()
        try:
            self.root.after(60, self._dispatch_shortcut_events)
        except tk.TclError:
            pass

    def _shortcut_unavailable(self, _error):
        print(f"[VOICE HOTKEY] Ctrl+Alt+Space unavailable (Windows error {_error}); Mouse Button 5 may still work.")
        self.status.configure(text="MOUSE 5 OR FOCUS MAANAV · CTRL+ALT+SPACE")
        try:
            self.root.bind_all("<Control-Alt-space>", lambda _event: self.on_hotkey(), add="+")
        except tk.TclError:
            pass

    def _register_hotkey(self):
        if os.name == "nt":
            self._shortcut = _GlobalShortcut(
                self.on_hotkey,
                self._shortcut_unavailable,
            )
            self._shortcut.start()
            self._hotkey_registered = self._shortcut.registered
            self._mouse_button_5_registered = self._shortcut.mouse_registered
            self.root.after(60, self._dispatch_shortcut_events)
        else:
            self._hotkey_registered = False
            self._mouse_button_5_registered = False
            self.root.bind_all("<Control-Alt-space>", lambda _event: self.on_hotkey(), add="+")

    @staticmethod
    def _is_fullscreen_external_window(user32, hwnd, screen_w, screen_h):
        """Ignore the Windows desktop shell so Show Desktop never hides Nesuko."""
        class_name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if class_name.value in {"Progman", "WorkerW"}:
            return False
        process_id = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
        no_caption = not bool(style & 0x00C00000)  # WS_CAPTION
        covers_screen = (
            rect.left <= 8 and rect.top <= 8
            and rect.right >= screen_w - 8 and rect.bottom >= screen_h - 8
        )
        return process_id.value != os.getpid() and no_caption and covers_screen

    def _watch_fullscreen(self):
        try:
            if os.name == "nt":
                user32 = ctypes.windll.user32
                user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                    if self._is_fullscreen_external_window(user32, hwnd, screen_w, screen_h):
                        self.pet.withdraw()
                        self.chat_window.withdraw()
                    elif not self.hidden:
                        self.pet.deiconify()
                        self.pet.attributes("-topmost", True)
                        self.pet.lift()
        except Exception:
            pass
        try:
            self.root.after(1100, self._watch_fullscreen)
        except tk.TclError:
            pass

    def _set_dictation_active(self, active):
        active = bool(active)
        if active == self.dictation_active:
            return
        self.dictation_active = active
        if self.dictation_mode is not None:
            try:
                self.dictation_mode(active)
            except Exception as error:
                self.status.configure(text="DICTATION HANDOFF FAILED")
                print("Wispr microphone handoff failed:", error)

    def focus_for_wispr(self):
        self._pet_mood = "listening"
        self.show_chat(focus=True)
        self.status.configure(text="WISPR FLOW · DICTATE, THEN SEND")
        self.entry.focus_force()
        self._set_dictation_active(True)

    def _play_chirp(self, kind="reply"):
        """Play a soft, note-bent electronic chirp; never synthesize speech."""
        if not getattr(self, "pet_sounds_enabled", lambda: True)():
            return
        notes = {
            "hello": ((640, 880, 85), (880, 1320, 105)),
            "input": ((820, 1040, 65),),
            "thinking": ((560, 740, 75), (740, 620, 80)),
            "talk": ((920, 1320, 70), (1320, 1580, 90)),
            "reply": ((940, 1380, 75), (1320, 1680, 95), (1120, 1480, 80)),
            "error": ((620, 470, 95), (470, 340, 110)),
            "pet": ((900, 1180, 70), (1180, 1510, 85), (990, 1390, 75)),
        }.get(kind, ((820, 1040, 65),))

        def make_wave():
            rate = 22050
            samples = bytearray()
            for start_hz, end_hz, duration_ms in notes:
                count = int(rate * duration_ms / 1000)
                for index in range(count):
                    progress = index / max(1, count - 1)
                    envelope = min(1.0, progress / 0.12, (1.0 - progress) / 0.24)
                    frequency = start_hz + (end_hz - start_hz) * progress
                    phase = 2 * math.pi * frequency * index / rate
                    tone = math.sin(phase) * 0.78 + math.sin(phase * 2.01) * 0.22
                    samples.extend(struct.pack("<h", int(5200 * envelope * tone)))
                samples.extend(b"\x00\x00" * int(rate * 0.018))
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                wav.writeframes(samples)
            return output.getvalue()

        def play():
            if os.name != "nt" or winsound is None:
                return
            try:
                winsound.PlaySound(make_wave(), winsound.SND_MEMORY | winsound.SND_SYNC)
            except (ImportError, RuntimeError, OSError):
                pass

        try:
            self._sound_executor.submit(play)
        except RuntimeError:
            pass

    def show_text_turn(self, user_text, answer):
        """Show a hotkey-driven voice reply as a desktop toast and pet chirp."""
        self._show_notification(answer)
        self.status.configure(text="TEXT REPLY · PET CHIRP")
        self._play_chirp("reply")

    def show_voice_error(self, message):
        self.show_chat()
        self._show_notification(message)
        self.status.configure(text="I COULDN'T HEAR THAT · TRY AGAIN")
        self._play_chirp("error")

    def _show_notification(self, message):
        """Show assistant output as a compact desktop toast, not a transcript."""
        self._dismiss_notification()
        text = str(message).strip()
        if len(text) > 420:
            text = text[:417].rsplit(" ", 1)[0] + "…"
        toast = tk.Toplevel(self.root)
        self.notification_window = toast
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg="#d3b44c")
        card = tk.Frame(toast, bg="#17160f", padx=12, pady=9)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        top = tk.Frame(card, bg="#17160f")
        top.pack(fill="x")
        tk.Label(top, text="NESUKO", bg="#17160f", fg="#d3b44c",
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        close_button = tk.Button(
            top, text="×", command=self._dismiss_notification,
            bg="#17160f", fg="#9b947b", activebackground="#17160f",
            relief="flat", bd=0, padx=5, pady=0, cursor="hand2",
        )
        close_button.pack(side="right")
        label = tk.Label(
            card, text=text, bg="#17160f", fg="#f0ecdc", justify="left",
            anchor="nw", wraplength=310, font=("Segoe UI", 10),
        )
        label.pack(fill="both", expand=True, pady=(5, 0))
        toast.update_idletasks()
        width = 350
        height = min(220, max(82, card.winfo_reqheight() + 4))
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        toast.geometry(f"{width}x{height}+{max(8, screen_w-width-18)}+{max(8, screen_h-height-52)}")
        self._notification_job = toast.after(7000, self._dismiss_notification)

    def _dismiss_notification(self):
        if self._notification_job is not None:
            try:
                self.root.after_cancel(self._notification_job)
            except tk.TclError:
                pass
            self._notification_job = None
        notification = self.notification_window
        self.notification_window = None
        if notification is not None:
            try:
                notification.destroy()
            except tk.TclError:
                pass

    def send(self, _e=None, *, image_path=None, message=None):
        message = self.entry.get().strip() if message is None else str(message).strip()
        if not message and not image_path:
            return "break"
        self.entry.delete(0, "end")
        self._play_chirp("input")
        self._pet_mood = "thinking"
        self.status.configure(text="THINKING · ROUTING")
        cancel_event = self.request_begin()
        self._dictation_reply_pending = self.dictation_active
        threading.Thread(
            target=self._answer,
            args=(message, image_path, cancel_event),
            name="MaanavCompanionReply",
            daemon=True,
        ).start()
        return "break"

    def _answer(self, message, image_path, cancel_event):
        try:
            sid = self.session_id()
            prompt = message or "Please describe this screen and help with what you see."
            answer = self.ask_ai(prompt, image_path=image_path, session_id=sid)
            if cancel_event is not None and cancel_event.is_set():
                answer = "I stopped that reply."
                self.add_turn(prompt, "[Response generation stopped by the user.]", session_id=sid)
                should_speak = False
            else:
                self.add_turn(prompt, answer, session_id=sid)
                should_speak = True
            self.on_turn(sid)
            self.root.after(0, self._answer_ready, answer, cancel_event, should_speak)
        except Exception as error:
            self.root.after(0, self._answer_ready, "I hit an error: " + str(error), cancel_event, False)

    def _answer_ready(self, answer, cancel_event=None, should_speak=True):
        self._show_notification(answer)
        self.status.configure(
            text="WISPR FLOW · WAITING FOR NESUKO" if self.dictation_active
            else self.ready_status
        )
        if should_speak:
            self._play_chirp("reply")
        else:
            self._play_chirp("error")
        if self.request_finish(cancel_event):
            self._dictation_reply_pending = False
            self._set_dictation_active(False)

    @staticmethod
    def grab_screen_image():
        """Prefer all-monitor capture, then fall back to the primary screen."""
        from PIL import ImageGrab

        try:
            return ImageGrab.grab(all_screens=True)
        except Exception as all_screen_error:
            try:
                return ImageGrab.grab()
            except Exception as primary_screen_error:
                raise RuntimeError(
                    "All-screen capture failed ("
                    + str(all_screen_error)
                    + "); primary-screen capture failed ("
                    + str(primary_screen_error)
                    + ")."
                ) from primary_screen_error

    def capture_screen(self):
        self.show_chat(focus=True)
        try:
            image = self.grab_screen_image()
            handle,path=tempfile.mkstemp(prefix="maanav-screen-",suffix=".png"); os.close(handle); image.save(path,"PNG")
            self.send(message=self.entry.get().strip() or "Please describe what is on my screen and help with what you see.",image_path=path)
            threading.Thread(target=self._remove_later,args=(path,),daemon=True).start()
        except Exception as error:
            self._show_notification("Screen capture failed: " + str(error))

    def _decision_request(self, title, body):
        """Ask a blocking question from a worker while keeping all Tk calls on its thread."""
        completed = threading.Event()
        answer = [False]
        try:
            self.root.after(0, self._show_decision_dialog, title, body, answer, completed)
        except (tk.TclError, RuntimeError):
            return False
        if not completed.wait(90):
            return False
        return answer[0]

    def _show_decision_dialog(self, title, body, answer, completed):
        try:
            dialog = tk.Toplevel(self.root)
            dialog.title(title)
            dialog.configure(bg="#10100d")
            dialog.attributes("-topmost", True)
            dialog.resizable(True, True)
            dialog.geometry("500x390")
            tk.Label(
                dialog, text=title.upper(), bg="#17160f", fg="#e3bb54",
                font=("Segoe UI", 10, "bold"), anchor="w",
            ).pack(fill="x", padx=12, pady=(12, 7))
            text = tk.Text(
                dialog, wrap="word", height=13, bg="#10100d", fg="#eee9d8",
                insertbackground="#eee9d8", relief="flat", font=("Segoe UI", 10),
            )
            text.pack(fill="both", expand=True, padx=12, pady=6)
            text.insert("1.0", body)
            text.configure(state="disabled")
            buttons = tk.Frame(dialog, bg="#10100d")
            buttons.pack(fill="x", padx=12, pady=(5, 12))

            def finish(approved):
                if completed.is_set():
                    return
                answer[0] = bool(approved)
                completed.set()
                try:
                    dialog.destroy()
                except tk.TclError:
                    pass

            tk.Button(
                buttons, text="CANCEL", command=lambda: finish(False),
                bg="#24221c", fg="#e6dfcf", relief="flat", padx=16, pady=7,
            ).pack(side="right", padx=(6, 0))
            tk.Button(
                buttons, text="APPROVE", command=lambda: finish(True),
                bg="#806a2d", fg="white", relief="flat", padx=16, pady=7,
            ).pack(side="right")
            dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
            dialog.grab_set()
            dialog.focus_force()
            dialog.after(90000, lambda: finish(False))
        except (tk.TclError, RuntimeError):
            answer[0] = False
            completed.set()

    def _confirm_screen_sharing(self, task):
        return self._decision_request(
            "Share screen for this task?",
            "Maanav will send a screenshot of your primary display and your task to OpenAI GPT-6 Astra. "
            "It will send updated screenshots as the task proceeds. This uses your OpenAI API account and "
            "may incur API charges. Screen content is treated as untrusted.\n\n"
            f"Task:\n{task}\n\nContinue?",
        )

    def _approve_computer_actions(self, task, actions):
        from computer_use import summarize_actions

        return self._decision_request(
            "Approve these computer actions?",
            f"Task:\n{task}\n\nRequested actions:\n{summarize_actions(actions)}\n\n"
            "Review every click, key, and text entry. Cancel if any action is unexpected or would expose, "
            "send, purchase, delete, or overwrite something.",
        )

    def _call_ui_and_wait(self, callback):
        complete = threading.Event()
        result = [False]

        def invoke():
            try:
                callback()
                result[0] = True
            except (tk.TclError, RuntimeError):
                result[0] = False
            finally:
                complete.set()

        try:
            self.root.after(0, invoke)
        except (tk.TclError, RuntimeError):
            return False
        return complete.wait(5.0) and result[0]

    def _hide_for_computer_task(self):
        def hide_windows():
            self._computer_window_state = {
                "root": self.root.state(),
                "pet": self.pet.state(),
                "chat": self.chat_window.state(),
            }
            self._dismiss_notification()
            self.chat_window.withdraw()
            self.pet.withdraw()
            self.root.withdraw()
        if not self._call_ui_and_wait(hide_windows):
            raise RuntimeError("Maanav could not clear its windows before the screen-use task.")

    def _restore_after_computer_task(self):
        state = self._computer_window_state
        if not state:
            return

        def restore_windows():
            if state["root"] == "iconic":
                self.root.iconify()
            elif state["root"] == "normal":
                self.root.deiconify()
            else:
                self.root.withdraw()
            if state["pet"] == "normal" and not self.hidden:
                self.pet.deiconify()
            else:
                self.pet.withdraw()
            if state["chat"] == "normal" and not self.hidden:
                self.chat_window.deiconify()
            else:
                self.chat_window.withdraw()
            self._computer_window_state = None
        self._call_ui_and_wait(restore_windows)

    def start_computer_task(self):
        if self._computer_task_active:
            if self._computer_task_cancel is not None:
                self._computer_task_cancel.set()
                self.status.configure(text="STOP REQUESTED · WAITING FOR CURRENT MODEL STEP")
                self.use_pc_button.configure(text="STOPPING")
            return "break"
        task = self.entry.get().strip()
        if not task:
            self.status.configure(text="TYPE A TASK FIRST · THEN USE PC")
            return "break"
        if self.computer_task is None:
            self._show_notification("Computer control is unavailable in this build.")
            return "break"
        self.entry.delete(0, "end")
        self.status.configure(text="WAITING FOR SCREEN-SHARING APPROVAL")
        self._pet_mood = "thinking"
        self._computer_task_active = True
        self._computer_task_cancel = threading.Event()
        self.use_pc_button.configure(text="STOP PC")
        cancel_event = self._computer_task_cancel

        def run():
            try:
                result = self.computer_task(
                    task,
                    confirm_sharing=self._confirm_screen_sharing,
                    approve_actions=self._approve_computer_actions,
                    status=lambda value: self.root.after(0, self.status.configure, {"text": value}),
                    on_start=self._hide_for_computer_task,
                    on_finish=self._restore_after_computer_task,
                    cancel_event=cancel_event,
                )
            except Exception as error:
                result = "Computer task couldn't start: " + str(error)
            try:
                self.root.after(0, self._computer_task_finished, task, result)
            except tk.TclError:
                pass

        threading.Thread(target=run, name="MaanavComputerUse", daemon=True).start()
        return "break"

    def _computer_task_finished(self, task, result):
        self._computer_task_active = False
        self._computer_task_cancel = None
        self.use_pc_button.configure(text="USE PC")
        self.status.configure(text=self.ready_status)
        self._pet_mood = "idle"
        self.add_turn(task, result, session_id=self.session_id())
        self.on_turn(self.session_id())
        self._show_notification(result)
    @staticmethod
    def _remove_later(path):
        time.sleep(300)
        try: Path(path).unlink(missing_ok=True)
        except OSError: pass
    def call_voice(self):
        # Pet interaction is text-first: Wispr online, local one-shot speech
        # recognition offline, followed by a text reply and a sound cue.
        self._pet_mood = "listening"
        self._play_chirp("hello")
        self.on_hotkey()

    def set_call_active(self, active):
        active = bool(active)
        if active == self.call_active:
            return
        self.call_active = active
        if self.call_mode is not None:
            self.call_mode(active)
        self.call_button.configure(
            text="END CALL" if active else "CALL",
            fg="#f0a36a" if active else "#d3b44c",
        )
        self.status.configure(
            text="CALL ACTIVE · SPEAK NATURALLY" if active else self.ready_status
        )

    def close(self):
        self._dictation_reply_pending = False
        self._dismiss_notification()
        self._set_dictation_active(False)
        if self.call_active:
            self.set_call_active(False)
        shortcut = getattr(self, "_shortcut", None)
        if shortcut is not None:
            shortcut.stop()
        self._sound_executor.shutdown(wait=False, cancel_futures=True)
        for w in (self.chat_window,self.pet):
            try: w.destroy()
            except tk.TclError: pass
