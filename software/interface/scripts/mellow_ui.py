import os, sys, glob, wave, datetime, queue, threading, subprocess
import tkinter as tk
import numpy as np

# Optional mic backend
try:
    import sounddevice as sd
    _IMPORT_ERR = None
except Exception as e:
    sd = None
    _IMPORT_ERR = e

MENU_ITEMS = ["Discussion", "Voices", "Bluetooth", "Devices", "Logs", "Settings"]
REC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "voice_input", "recordings"))
os.makedirs(REC_DIR, exist_ok=True)


# -----------------------
# Audio recorder (float32 capture -> int16 WAV on save)
# -----------------------
class Recorder:
    def __init__(self, samplerate=None, channels=1, dtype="float32"):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype  # float32 in stream to simplify RMS calculations
        self.q = queue.Queue()
        self.stream = None
        self.frames = []
        self.filepath = None
        self.worker = None
        self.samples = 0
        self.level = 0.0  # 0..1 RMS

    def _callback(self, indata, frames, time, status):
        if status:
            print("sounddevice status:", status, flush=True)
        # indata is float32 [-1, 1] because dtype="float32"
        self.q.put(indata.copy())

    def start(self, out_dir=REC_DIR):
        os.makedirs(out_dir, exist_ok=True)
        # samplerate: prefer device's default
        if self.samplerate is None:
            indev = sd.default.device[0] if sd and sd.default.device is not None else None
            info = sd.query_devices(indev) if sd and indev is not None else sd.query_devices(kind="input")
            self.samplerate = int(info.get("default_samplerate", 48000) or 48000)

        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.filepath = os.path.join(out_dir, f"REC-{ts}.wav")
        self.frames.clear()
        self.samples = 0
        self.level = 0.0

        self.stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            dtype=self.dtype,
            callback=self._callback,
        )
        self.stream.start()

        # Drain queue in background
        self.worker = threading.Thread(target=self._drain, daemon=True)
        self.worker.start()
        return self.filepath

    def _drain(self):
        # accumulate and compute instantaneous RMS for meter
        while self.stream and self.stream.active:
            try:
                block = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            self.frames.append(block)
            self.samples += len(block)
            # RMS across all channels
            if block.ndim == 2:
                block = np.mean(block, axis=1)
            rms = float(np.sqrt(np.mean(np.square(block))))  # 0..1 for float32
            # smooth a bit so it doesn’t flicker
            self.level = 0.7 * self.level + 0.3 * min(1.0, rms * 1.5)

    def stop(self):
        if not self.stream:
            return None
        self.stream.stop()
        self.stream.close()
        self.stream = None

        if not self.frames:
            return None

        # Concatenate, convert to int16, write WAV
        audio_f32 = np.concatenate(self.frames, axis=0)
        if audio_f32.ndim == 2 and audio_f32.shape[1] > 1:
            audio_f32 = np.mean(audio_f32, axis=1)  # mono for now

        audio_i16 = np.clip(audio_f32, -1.0, 1.0)
        audio_i16 = (audio_i16 * 32767.0).astype(np.int16)

        with wave.open(self.filepath, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.samplerate)
            wf.writeframes(audio_i16.tobytes())

        return self.filepath

    # helper accessors
    def seconds(self) -> float:
        if self.samplerate and self.samples:
            return self.samples / float(self.samplerate)
        return 0.0

    def meter_level(self) -> float:
        return float(self.level)


# -----------------------
# UI
# -----------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mellow UI (Meters + Logs)")
        self.geometry("900x570")
        self.configure(bg="#111")
        self.is_recording = False
        self.recorder = Recorder() if sd else None

        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        # Top bar
        top = tk.Frame(self, bg="#0c0c0c", height=56)
        top.grid(row=0, column=0, columnspan=2, sticky="nsew")
        top.grid_propagate(False)

        tk.Label(
            top, text="Mellow", fg="#f2f2f2", bg="#0c0c0c",
            font=("Helvetica", 18, "bold")
        ).pack(side="left", padx=16)

        # Duration + level meter live while recording
        self.dur_lbl = tk.Label(top, text="00:00.0", bg="#0c0c0c", fg="#cccccc", font=("Helvetica", 12))
        self.dur_lbl.pack(side="right", padx=(6, 8))

        self.meter = tk.Canvas(top, width=120, height=12, bg="#222", highlightthickness=0)
        self.meter.pack(side="right", padx=(8, 6))
        # meter segments backdrop
        for x in range(0, 121, 20):
            self.meter.create_rectangle(x, 0, x+1, 12, fill="#333", outline="")

        self.play_btn = tk.Button(
            top, text="▶ Play last", bg="#444444", fg="#FFFFFF",
            activebackground="#555555", command=self.play_last
        )
        self.play_btn.pack(side="right", padx=8, pady=8)

        self.rec_btn = tk.Button(
            top, text="●  Record", fg="#fff", bg="#333",
            activebackground="#444", relief="flat", padx=14, pady=8,
            command=self.toggle_recording
        )
        self.rec_btn.pack(side="right", padx=8, pady=8)

        # Sidebar
        sidebar = tk.Frame(self, bg="#1a1a1a", width=220)
        sidebar.grid(row=1, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        self.menu = tk.Listbox(
            sidebar, activestyle="none", highlightthickness=0,
            fg="#e0e0e0", bg="#1a1a1a", selectbackground="#333",
            selectforeground="#fff", border=0, height=12
        )
        for item in MENU_ITEMS:
            self.menu.insert(tk.END, f"  {item}")
        self.menu.pack(fill="both", expand=True, padx=8, pady=(16, 8))
        self.menu.bind("<<ListboxSelect>>", self.on_select)

        # Content
        self.content = tk.Frame(self, bg="#0f0f0f")
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.title_lbl = tk.Label(
            self.content, text="Welcome to Mellow", bg="#0f0f0f",
            fg="#fafafa", font=("Helvetica", 20, "bold")
        )
        self.title_lbl.pack(pady=24)

        self.body = tk.Label(
            self.content, text="", bg="#0f0f0f", fg="#c9c9c9",
            justify="left", font=("Helvetica", 12), anchor="nw"
        )
        self.body.pack(padx=24, anchor="nw")

        self.logs_list = None  # will be created when Logs tab is active
        self.menu.selection_set(0)
        self.on_select(None)

        # UI tick timer
        self._tick()

    # -------- Actions
    def toggle_recording(self):
        if not sd:
            self.set_status(f"Mic backend missing: pip install sounddevice (error: {_IMPORT_ERR})")
            return
        if not self.is_recording:
            try:
                path = self.recorder.start(REC_DIR)
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
            self.rec_btn.configure(text="●  Record", bg="#333")
            self.set_status(f"Stopped. Saved:\n{path}" if path else "Stopped. (No audio captured)")
            # refresh Logs list if that tab is open
            if self.current_tab_name() == "Logs":
                self.populate_logs()

    def play_last(self):
        """Play most recent REC-*.wav using the OS player."""
        try:
            paths = sorted(
                glob.glob(os.path.join(REC_DIR, "REC-*.wav")),
                key=os.path.getmtime, reverse=True
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
                # Linux: prefer 'xdg-open' for default player, fallback to aplay
                try:
                    subprocess.Popen(["xdg-open", path])
                except Exception:
                    subprocess.Popen(["aplay", path])
        except Exception as e:
            self.set_status(f"Couldn't play file: {e}")

    # -------- Tabs / Content
    def current_tab_name(self):
        idx = self.menu.curselection()
        if not idx:
            return MENU_ITEMS[0]
        return MENU_ITEMS[idx[0]]

    def set_status(self, msg: str):
        tab = self.current_tab_name()
        if tab == "Discussion":
            self.body.config(text=msg + "\n\n" + self.render_screen("Discussion"))
        else:
            self.body.config(text=msg + "\n\nOpen 'Discussion' to view status.")

    def on_select(self, _):
        name = self.current_tab_name()
        self.title_lbl.config(text=name)
        # Destroy any logs widget if switching away
        if name != "Logs" and self.logs_list is not None:
            self.logs_list.master.destroy()
            self.logs_list = None

        if name == "Logs":
            self.build_logs_panel()
            self.populate_logs()
        else:
            # simple text body for other tabs
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
        if name == "Logs":       return ""  # handled by panel
        if name == "Settings":   return "General settings (placeholder)."
        return ""

    # -------- Logs panel
    def build_logs_panel(self):
        # Replace the body label with a small list + buttons
        if self.logs_list is not None:
            return

        holder = tk.Frame(self.content, bg="#0f0f0f")
        holder.pack(fill="both", expand=True, padx=24, pady=(0, 24))

        btnbar = tk.Frame(holder, bg="#0f0f0f")
        btnbar.pack(anchor="w", pady=(0, 8))
        tk.Button(btnbar, text="Refresh", command=self.populate_logs).pack(side="left")
        tk.Button(btnbar, text="Play selected", command=self.play_selected).pack(side="left", padx=(8, 0))
        tk.Button(btnbar, text="Reveal in Finder", command=self.reveal_selected).pack(side="left", padx=(8, 0))

        self.logs_list = tk.Listbox(holder, height=12, bg="#161616", fg="#e0e0e0", border=0)
        self.logs_list.pack(fill="both", expand=True)
        self.logs_list.bind("<Double-1>", lambda e: self.play_selected())

        # keep a small status text in the original label
        self.body.config(text="Double-click an item to play. Use 'Refresh' after new recordings.")

    def populate_logs(self):
        if not self.logs_list:
            return
        self.logs_list.delete(0, tk.END)
        files = sorted(glob.glob(os.path.join(REC_DIR, "REC-*.wav")), key=os.path.getmtime, reverse=True)
        for p in files[:200]:
            ts = datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M:%S")
            size_kb = os.path.getsize(p) // 1024
            self.logs_list.insert(tk.END, f"{os.path.basename(p)}   —  {ts}   —  {size_kb} KB")

    def _selected_path(self):
        if not self.logs_list:
            return None
        sel = self.logs_list.curselection()
        if not sel:
            return None
        name = self.logs_list.get(sel[0]).split("   —  ", 1)[0]
        return os.path.join(REC_DIR, name)

    def play_selected(self):
        p = self._selected_path()
        if not p:
            self.set_status("Select a recording first.")
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["afplay", p])
            elif sys.platform.startswith("win"):
                os.startfile(p)  # type: ignore[attr-defined]
            else:
                try:
                    subprocess.Popen(["xdg-open", p])
                except Exception:
                    subprocess.Popen(["aplay", p])
        except Exception as e:
            self.set_status(f"Couldn't play file: {e}")

    def reveal_selected(self):
        p = self._selected_path()
        if not p:
            self.set_status("Select a recording first.")
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", p])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", p])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(p)])
        except Exception as e:
            self.set_status(f"Couldn't reveal file: {e}")

    # -------- UI Tick (duration + meter)
    def _tick(self):
        # Duration
        if self.is_recording and self.recorder:
            secs = self.recorder.seconds()
            m = int(secs // 60)
            s = secs % 60
            self.dur_lbl.config(text=f"{m:02d}:{s:04.1f}")
        else:
            self.dur_lbl.config(text="00:00.0")

        # Meter
        self.meter.delete("lvl")
        lvl = 120 * (self.recorder.meter_level() if self.recorder else 0.0)
        self.meter.create_rectangle(0, 0, max(0, min(120, int(lvl))), 12, fill="#45c261", outline="", tags="lvl")

        # Re-render discussion text while recording (shows save path + tips)
        if self.current_tab_name() == "Discussion":
            self.body.config(text=self.render_screen("Discussion"))

        self.after(120, self._tick)


if __name__ == "__main__":
    App().mainloop()
