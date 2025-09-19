import os, sys, glob, datetime, queue, threading, time, wave, subprocess
import tkinter as tk
import numpy as np

# ---- Audio backend -----------------------------------------------------------
try:
    import sounddevice as sd
    _IMPORT_ERR = None
except Exception as e:
    sd = None
    _IMPORT_ERR = e

MENU_ITEMS = ["Discussion", "Voices", "Bluetooth", "Devices", "Logs", "Settings"]
REC_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "voice_input", "recordings")
)

# ---- Recorder ---------------------------------------------------------------
class Recorder:
    """Simple WAV recorder; auto-detects device sample rate if not provided."""
    def __init__(self, samplerate=None, channels=1, dtype="int16"):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.q = queue.Queue()
        self.stream = None
        self.frames = []
        self.filepath = None
        self.worker = None
        self.level_rms = 0.0  # 0..1 approx

    def _callback(self, indata, frames, time_info, status):
        if status:
            # Prints glitch/overflow info; helpful for debugging
            print("sounddevice status:", status, flush=True)
        # Keep a copy of raw audio
        block = indata.copy()
        self.q.put(block)

        # Update instantaneous RMS level (thread-safe enough for reading)
        try:
            arr = block.astype(np.float32)
            # Normalize based on dtype (int16 most common)
            if self.dtype == "int16":
                arr /= 32768.0
            rms = float(np.sqrt(np.mean(arr * arr)) if arr.size else 0.0)
            # Clamp to [0, 1.5] then to [0,1] for a bit of headroom
            self.level_rms = max(0.0, min(rms / 1.0, 1.0))
        except Exception:
            self.level_rms = 0.0

    def start(self, out_dir=REC_DIR):
        os.makedirs(out_dir, exist_ok=True)
        if self.samplerate is None and sd:
            in_dev = sd.default.device[0]
            info = sd.query_devices(in_dev)
            self.samplerate = int(info.get("default_samplerate", 48000) or 48000)

        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.filepath = os.path.join(out_dir, f"REC-{ts}.wav")
        self.frames = []
        self.level_rms = 0.0

        self.stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            dtype=self.dtype,
            callback=self._callback,
        )
        self.stream.start()
        self.worker = threading.Thread(target=self._drain, daemon=True)
        self.worker.start()
        return self.filepath

    def _drain(self):
        while self.stream and self.stream.active:
            try:
                block = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            self.frames.append(block)

    def stop(self):
        if not self.stream:
            return None
        self.stream.stop()
        self.stream.close()
        self.stream = None
        if not self.frames:
            return None

        audio = np.concatenate(self.frames, axis=0)
        with wave.open(self.filepath, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # int16
            wf.setframerate(self.samplerate)
            wf.writeframes(audio.astype(np.int16).tobytes())
        return self.filepath

    def get_level(self) -> float:
        return float(self.level_rms or 0.0)


# ---- App UI -----------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mellow UI (Mic capture build)")
        self.geometry("960x560")
        self.configure(bg="#111")

        self.is_recording = False
        self.record_start_ts: float | None = None
        self.recorder = Recorder() if sd else None

        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        # Top bar
        top = tk.Frame(self, bg="#0c0c0c", height=56)
        top.grid(row=0, column=0, columnspan=2, sticky="nsew")
        top.grid_propagate(False)

        tk.Label(top, text="Mellow", fg="#f2f2f2", bg="#0c0c0c",
                 font=("Helvetica", 18, "bold")).pack(side="left", padx=16)

        # Level meter (simple horizontal bar)
        meter_wrap = tk.Frame(top, bg="#0c0c0c")
        meter_wrap.pack(side="right", padx=(0, 12))
        self.level_canvas = tk.Canvas(meter_wrap, width=140, height=12,
                                      bg="#1e1e1e", highlightthickness=0)
        self.level_canvas.pack(side="left")
        self.level_rect = self.level_canvas.create_rectangle(0, 0, 1, 12, fill="#2ecc71", width=0)

        # Timer label
        self.timer_lbl = tk.Label(top, text="00:00", fg="#cfcfcf", bg="#0c0c0c",
                                  font=("Helvetica", 13, "bold"))
        self.timer_lbl.pack(side="right", padx=(0, 12))

        # Record/Stop
        self.rec_btn = tk.Button(top, text="●  Record", fg="#fff", bg="#333",
                                 activebackground="#444", relief="flat",
                                 padx=14, pady=8, command=self.toggle_recording)
        self.rec_btn.pack(side="right", padx=(0, 16), pady=8)

        # Sidebar
        sidebar = tk.Frame(self, bg="#1a1a1a", width=220)
        sidebar.grid(row=1, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        self.menu = tk.Listbox(
            sidebar, activestyle="none", highlightthickness=0,
            fg="#e0e0e0", bg="#1a1a1a", selectbackground="#333",
            selectforeground="#fff", border=0, height=16
        )
        for item in MENU_ITEMS:
            self.menu.insert(tk.END, f"  {item}")
        self.menu.pack(fill="both", expand=True, padx=8, pady=(16, 8))
        self.menu.bind("<<ListboxSelect>>", self.on_select)

        # Content area (we’ll swap widgets depending on tab)
        self.content = tk.Frame(self, bg="#0f0f0f")
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.title_lbl = tk.Label(self.content, text="Welcome to Mellow",
                                  bg="#0f0f0f", fg="#fafafa",
                                  font=("Helvetica", 20, "bold"))
        self.title_lbl.pack(pady=24)

        self.body = tk.Label(self.content, text="", bg="#0f0f0f",
                             fg="#c9c9c9", justify="left",
                             font=("Helvetica", 12), anchor="nw")
        self.body.pack(padx=24, anchor="nw")

        # Logs view (created/destroyed on demand)
        self.logs_view = None

        # Play last button (top bar helper)
        self.play_btn = tk.Button(
            top, text="▶ Play last", bg="#444444", fg="#FFFFFF",
            activebackground="#555555", command=self.play_last
        )
        self.play_btn.pack(side="right", padx=8, pady=8)

        # Initial screen
        self.menu.selection_set(0)
        self.on_select(None)

        # Periodic UI updater for timer + meter
        self.after(100, self._update_ui)

    # --- Recording controls ---------------------------------------------------
    def toggle_recording(self):
        if not sd:
            self.set_status(f"Mic backend missing: pip install sounddevice (error: {_IMPORT_ERR})")
            return

        if not self.is_recording:
            try:
                path = self.recorder.start(REC_DIR)
                self.record_start_ts = time.time()
            except Exception as e:
                self.set_status(
                    "Failed to access microphone. Allow mic for Terminal/Python in "
                    "System Settings → Privacy & Security → Microphone.\n\nError: " + str(e)
                )
                return
            self.is_recording = True
            self.rec_btn.configure(text="■  Stop", bg="#b30000")
            self.set_status(f"Recording… Saving to:\n{path}")
        else:
            path = self.recorder.stop()
            self.is_recording = False
            self.record_start_ts = None
            self.rec_btn.configure(text="●  Record", bg="#333")
            self.set_status(f"Stopped. Saved:\n{path}" if path else "Stopped. (No audio captured)")

    def _update_ui(self):
        # Timer
        if self.is_recording and self.record_start_ts:
            elapsed = int(time.time() - self.record_start_ts)
            mm, ss = divmod(elapsed, 60)
            self.timer_lbl.config(text=f"{mm:02d}:{ss:02d}")
        else:
            self.timer_lbl.config(text="00:00")

        # Level meter
        level = self.recorder.get_level() if (self.is_recording and self.recorder) else 0.0
        width = max(1, int(140 * max(0.0, min(level, 1.0))))
        self.level_canvas.coords(self.level_rect, 0, 0, width, 12)

        # Schedule next tick
        self.after(100, self._update_ui)

    # --- Playback helpers -----------------------------------------------------
    def play_last(self):
        """Play the most recent REC-*.wav using the OS player."""
        try:
            paths = sorted(
                glob.glob(os.path.join(REC_DIR, "REC-*.wav")),
                key=os.path.getmtime,
                reverse=True,
            )
            if not paths:
                self.set_status("No recordings yet.")
                return
            path = paths[0]
            self.set_status(f"Playing: {os.path.basename(path)}")
            if sys.platform == "darwin":
                subprocess.Popen(["afplay", path])
            elif sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["aplay", path])
        except Exception as e:
            self.set_status(f"Couldn't play file: {e}")

    # --- Screens --------------------------------------------------------------
    def set_status(self, msg: str):
        # Update only when the 'Discussion' screen is visible
        if self.menu.curselection() and MENU_ITEMS[self.menu.curselection()[0]] == "Discussion":
            self.body.config(text=msg + "\n\n" + self.render_screen("Discussion"))
        else:
            self.body.config(text=msg + "\n\nOpen 'Discussion' to view status.")

    def on_select(self, _):
        idx = self.menu.curselection()
        if not idx:
            return
        name = MENU_ITEMS[idx[0]]
        self.title_lbl.config(text=name)

        # Swap logs view in/out
        if name == "Logs":
            self._show_logs_view()
            return
        else:
            self._destroy_logs_view()

        # Default label content for other tabs
        self.body.config(text=self.render_screen(name))

    def render_screen(self, name: str) -> str:
        if name == "Discussion":
            return "\n".join([
                f"• Files save under: {REC_DIR}",
                "• Uses your mic’s native sample rate automatically.",
            ])
        if name == "Voices":     return "Choose the speaking voice (placeholder)."
        if name == "Bluetooth":  return "Pair headphones/speakers (placeholder)."
        if name == "Devices":    return "Manage connected devices (placeholder)."
        if name == "Logs":       return ""  # handled by _show_logs_view
        if name == "Settings":   return "General settings (placeholder)."
        return ""

    # --- Logs tab -------------------------------------------------------------
    def _show_logs_view(self):
        if self.logs_view:
            self._refresh_logs_list()
            return

        # Hide the label body when logs are shown
        self.body.pack_forget()

        vf = tk.Frame(self.content, bg="#0f0f0f")
        vf.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.logs_view = vf

        # List of recent wavs
        self.logs_list = tk.Listbox(vf, activestyle="none", height=12,
                                    fg="#e0e0e0", bg="#171717",
                                    selectbackground="#333", selectforeground="#fff",
                                    border=0)
        self.logs_list.pack(fill="both", expand=True, side="top")
        self.logs_list.bind("<Double-1>", self._play_selected)

        # Buttons
        btns = tk.Frame(vf, bg="#0f0f0f")
        btns.pack(fill="x", pady=12)
        tk.Button(btns, text="▶ Play selected", bg="#444", fg="#fff",
                  activebackground="#555", relief="flat",
                  command=self._play_selected).pack(side="left", padx=(0, 8))
        tk.Button(btns, text="Open folder", bg="#444", fg="#fff",
                  activebackground="#555", relief="flat",
                  command=self._open_folder).pack(side="left")

        self._refresh_logs_list()

    def _destroy_logs_view(self):
        if self.logs_view:
            self.logs_view.destroy()
            self.logs_view = None
            # restore the body label
            self.body.pack(padx=24, anchor="nw")

    def _refresh_logs_list(self):
        if not self.logs_view:
            return
        paths = sorted(glob.glob(os.path.join(REC_DIR, "REC-*.wav")),
                       key=os.path.getmtime, reverse=True)[:30]
        self._log_paths = paths  # keep mapping
        self.logs_list.delete(0, tk.END)
        for p in paths:
            meta = self._wav_meta(p)
            self.logs_list.insert(tk.END, f"{os.path.basename(p)}  —  {meta}")

    def _wav_meta(self, path: str) -> str:
        """Return short info like '48kHz · 00:03'."""
        try:
            with wave.open(path, "rb") as wf:
                fr = wf.getframerate()
                n = wf.getnframes()
                dur = int(n / max(1, fr))
                mm, ss = divmod(dur, 60)
                return f"{fr//1000}kHz · {mm:02d}:{ss:02d}"
        except Exception:
            return "unknown"

    def _play_selected(self, *_):
        if not getattr(self, "_log_paths", None):
            return
        sel = self.logs_list.curselection()
        if not sel:
            return
        path = self._log_paths[sel[0]]
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["afplay", path])
            elif sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["aplay", path])
        except Exception as e:
            self.set_status(f"Couldn't play: {e}")

    def _open_folder(self):
        folder = REC_DIR
        if sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        elif sys.platform.startswith("win"):
            os.startfile(folder)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", folder])


if __name__ == "__main__":
    App().mainloop()
