import os, sys, glob, wave, datetime, queue, threading, subprocess, json, time
import tkinter as tk
from tkinter import ttk, messagebox
import numpy as np

# Optional mic backend
try:
    import sounddevice as sd
    _IMPORT_ERR = None
except Exception as e:
    sd = None
    _IMPORT_ERR = e

# -----------------------
# Paths & constants
# -----------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REC_DIR = os.path.join(PROJECT_ROOT, "software", "voice_input", "recordings")
os.makedirs(REC_DIR, exist_ok=True)

CONFIG_DIR = os.path.join(PROJECT_ROOT, "software", "system_tools")
os.makedirs(CONFIG_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(CONFIG_DIR, "mellow_config.json")

MENU_ITEMS = ["Discussion", "Voices", "Bluetooth", "Devices", "Logs", "Settings"]

AUTOSAVE_INTERVAL_SEC = 15  # snapshot cadence during recording
DEFAULT_SR = 16000
DEFAULT_CH = 1
DEFAULT_DTYPE = "float32"

# -----------------------
# Small helpers
# -----------------------
def iso_now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "device_index": None,   # sounddevice index
        "samplerate": DEFAULT_SR,
        "channels": DEFAULT_CH,
        "dtype": DEFAULT_DTYPE,
    }

def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)

def list_input_devices():
    if sd is None:
        return []
    try:
        devs = sd.query_devices()
        out = []
        for i, d in enumerate(devs):
            if d.get("max_input_channels", 0) > 0:
                out.append({
                    "index": i,
                    "name": d.get("name", f"Device {i}"),
                    "hostapi": d.get("hostapi", None),
                    "sr_default": int(d.get("default_samplerate", DEFAULT_SR)),
                    "max_in": int(d.get("max_input_channels", 0)),
                })
        return out
    except Exception:
        return []

def latest_wav():
    files = glob.glob(os.path.join(REC_DIR, "REC-*.wav"))
    if not files: return None
    return sorted(files, key=os.path.getmtime, reverse=True)[0]

def play_wav(path):
    if not path or not os.path.exists(path): return
    if sys.platform == "darwin":
        subprocess.Popen(["afplay", path])
    elif sys.platform.startswith("linux"):
        subprocess.Popen(["paplay", path])  # or "aplay" depending on system
    else:
        messagebox.showinfo("Play", f"Please open manually:\n{path}")

# -----------------------
# Recorder
#   float32 capture -> int16 WAV on save
#   autosave snapshots every AUTOSAVE_INTERVAL_SEC
# -----------------------
class Recorder:
    def __init__(self, samplerate, channels, dtype, device_index=None):
        self.samplerate = int(samplerate)
        self.channels = int(channels)
        self.dtype = dtype
        self.device_index = device_index

        self.q = queue.Queue()
        self.frames_f32 = []     # list of float32 frames (numpy arrays)
        self.running = False
        self.start_ts = None

        self.autosave_thread = None
        self.autosave_stop = threading.Event()
        self.take_basename = None  # e.g., REC-20250919-142233

    def _callback(self, indata, frames, time_info, status):
        if status:
            # Dropouts etc; we still store what we get
            pass
        if self.running:
            self.q.put(indata.copy())

    def start(self):
        if sd is None:
            raise RuntimeError(f"sounddevice import error: {_IMPORT_ERR}")
        self.frames_f32.clear()
        self.q.queue.clear()
        self.running = True
        self.start_ts = time.time()
        self.take_basename = datetime.datetime.now().strftime("REC-%Y%m%d-%H%M%S")

        # Open input stream
        self.stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            dtype=self.dtype,
            device=self.device_index,
            callback=self._callback,
        )
        self.stream.start()

        # Autosave snapshots
        self.autosave_stop.clear()
        self.autosave_thread = threading.Thread(target=self._autosave_worker, daemon=True)
        self.autosave_thread.start()

    def stop(self):
        if not self.running:
            return None, None
        self.running = False

        # drain queue
        while not self.q.empty():
            self.frames_f32.append(self.q.get())

        # stop input stream
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

        # stop autosave
        self.autosave_stop.set()
        if self.autosave_thread and self.autosave_thread.is_alive():
            self.autosave_thread.join(timeout=2)

        # consolidate and write final WAV + sidecar
        wav_path = os.path.join(REC_DIR, f"{self.take_basename}.wav")
        sidecar_path = os.path.join(REC_DIR, f"{self.take_basename}.json")
        duration = self._write_wav(wav_path)
        self._write_sidecar(sidecar_path, wav_path, duration, autosave=False)

        # clean autosaves
        for apath in glob.glob(os.path.join(REC_DIR, f"AUTOSAVE-{self.take_basename}-*.wav")):
            try: os.remove(apath)
            except Exception: pass

        return wav_path, duration

    def poll_into_buffer(self):
        """Pull queued chunks into frames_f32; return (sec, rms) for UI."""
        while True:
            try:
                self.frames_f32.append(self.q.get_nowait())
            except queue.Empty:
                break
        elapsed = (time.time() - self.start_ts) if self.start_ts else 0.0
        if self.frames_f32:
            latest = self.frames_f32[-1]
            latest_i16 = np.clip(latest, -1.0, 1.0)
            latest_i16 = (latest_i16 * 32767).astype(np.int16)
            rms = float(np.sqrt(np.mean(latest_i16.astype(np.float32) ** 2))) / 32767.0
        else:
            rms = 0.0
        return elapsed, rms

    def _write_wav(self, out_path, frames_override=None):
        """Write float32 frames as int16 WAV; return duration seconds."""
        frames = frames_override if frames_override is not None else self.frames_f32
        if not frames:
            with wave.open(out_path, "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(2)
                wf.setframerate(self.samplerate)
            return 0.0

        pcm = np.concatenate(frames, axis=0)
        pcm = np.clip(pcm, -1.0, 1.0)
        pcm_i16 = (pcm * 32767).astype(np.int16)

        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # int16
            wf.setframerate(self.samplerate)
            wf.writeframes(pcm_i16.tobytes())

        return pcm_i16.shape[0] / float(self.samplerate)

    def _write_sidecar(self, sidecar_path, wav_path, duration_sec, autosave: bool):
        meta = {
            "filename": os.path.basename(wav_path),
            "path": wav_path,
            "created_at": iso_now(),
            "samplerate": self.samplerate,
            "channels": self.channels,
            "dtype": self.dtype,
            "frames": int(duration_sec * self.samplerate),
            "duration_sec": float(duration_sec),
            "device_index": self.device_index,
            "device_name": None,
            "autosave": autosave,
        }
        for d in list_input_devices():
            if d["index"] == self.device_index:
                meta["device_name"] = d["name"]
                break

        tmp = sidecar_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        os.replace(tmp, sidecar_path)

    def _autosave_worker(self):
        last_dump = 0.0
        while not self.autosave_stop.is_set():
            now = time.time()
            if now - last_dump >= AUTOSAVE_INTERVAL_SEC:
                frames_copy = list(self.frames_f32)
                name = f"AUTOSAVE-{self.take_basename}-{int(now - self.start_ts):04d}s.wav"
                path = os.path.join(REC_DIR, name)
                dur = self._write_wav(path, frames_override=frames_copy)
                sidecar = path.replace(".wav", ".json")
                self._write_sidecar(sidecar, path, dur, autosave=True)
                last_dump = now
            time.sleep(0.2)

# -----------------------
# UI App
# -----------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mellow UI (offline)")
        self.geometry("860x560")

        self.cfg = load_config()
        self.recorder = None
        self.recording = False

        self._build_layout()
        self._refresh_logs()
        self._tick()  # periodic UI updates

    # ----- UI Layout -----
    def _build_layout(self):
        # Top bar
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(side=tk.TOP, fill=tk.X)

        self.btn_record = ttk.Button(top, text="● Record", command=self._on_record)
        self.btn_record.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_stop = ttk.Button(top, text="■ Stop", command=self._on_stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_timer = ttk.Label(top, text="00:00")
        self.lbl_timer.pack(side=tk.LEFT, padx=(8, 16))

        self.rms_var = tk.DoubleVar(value=0.0)
        self.rms_bar = ttk.Progressbar(top, orient="horizontal", length=180, mode="determinate", variable=self.rms_var, maximum=1.0)
        self.rms_bar.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Separator(top, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=8)

        self.btn_play_last = ttk.Button(top, text="▶ Play last", command=self._on_play_last)
        self.btn_play_last.pack(side=tk.LEFT, padx=(0, 6))

        # status
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(top, textvariable=self.status_var, foreground="#666").pack(side=tk.RIGHT)

        # Sidebar + content
        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        sidebar = ttk.Frame(body, width=180)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        self.content = ttk.Frame(body, padding=(10, 10))
        self.content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Sidebar buttons
        self.tab_var = tk.StringVar(value="Discussion")
        for m in MENU_ITEMS:
            b = ttk.Radiobutton(sidebar, text=m, value=m, variable=self.tab_var, command=self._render_tab)
            b.pack(anchor="w", padx=10, pady=6)

        self._render_tab()

    # ----- Tabs -----
    def _render_tab(self):
        for w in self.content.winfo_children():
            w.destroy()
        tab = self.tab_var.get()

        if tab == "Discussion":
            ttk.Label(self.content, text="Status: Ready. Tips: click ● Record to start; ■ Stop to save.", wraplength=560).pack(anchor="w")
        elif tab == "Logs":
            self._build_logs_tab()
        elif tab == "Devices":
            self._build_devices_tab()
        else:
            ttk.Label(self.content, text=f"{tab} (placeholder)").pack(anchor="w")

    def _build_logs_tab(self):
        top = ttk.Frame(self.content)
        top.pack(fill=tk.X)

        self.btn_refresh = ttk.Button(top, text="Refresh", command=self._refresh_logs)
        self.btn_refresh.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_play_sel = ttk.Button(top, text="Play selected", command=self._on_play_selected)
        self.btn_play_sel.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_reveal = ttk.Button(top, text="Reveal in Finder", command=self._on_reveal_selected)
        self.btn_reveal.pack(side=tk.LEFT, padx=(0, 6))

        # NEW: Transcribe selected (offline)
        self.btn_transcribe = ttk.Button(top, text="Transcribe selected (offline)", command=self._on_transcribe_selected)
        self.btn_transcribe.pack(side=tk.LEFT, padx=(0, 6))

        # listbox
        self.logs_list = tk.Listbox(self.content, height=18)
        self.logs_list.pack(fill=tk.BOTH, expand=True, pady=(8,0))

        self._refresh_logs()

    def _refresh_logs(self):
        if not hasattr(self, "logs_list"):
            return
        self.logs_list.delete(0, tk.END)
        files = sorted(
            glob.glob(os.path.join(REC_DIR, "REC-*.wav")) +
            glob.glob(os.path.join(REC_DIR, "AUTOSAVE-*.wav")),
            key=os.path.getmtime, reverse=True
        )
        for f in files:
            size_mb = os.path.getsize(f) / (1024*1024.0)
            ts = datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S")
            self.logs_list.insert(tk.END, f"{os.path.basename(f)}   ({size_mb:.2f} MB, {ts})")

    def _build_devices_tab(self):
        wrap = ttk.Frame(self.content)
        wrap.pack(fill=tk.BOTH, expand=True)

        ttk.Label(wrap, text="Select input device for recording:").pack(anchor="w")

        cols = ("index", "name", "sr_default", "max_in")
        tree = ttk.Treeview(wrap, columns=cols, show="headings", height=10)
        for c, label in zip(cols, ["Index", "Name", "Default SR", "Max In Ch"]):
            tree.heading(c, text=label)
            tree.column(c, width=120 if c != "name" else 360)
        tree.pack(fill=tk.BOTH, expand=True, pady=(6, 6))

        devices = list_input_devices()
        for d in devices:
            tree.insert("", tk.END, values=(d["index"], d["name"], d["sr_default"], d["max_in"]))

        cur_idx = self.cfg.get("device_index", None)
        if cur_idx is not None:
            for iid in tree.get_children():
                vals = tree.item(iid, "values")
                if str(vals[0]) == str(cur_idx):
                    tree.selection_set(iid)
                    tree.see(iid)
                    break

        def save_sel():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Devices", "Select a device row first.")
                return
            vals = tree.item(sel[0], "values")
            dev_index = int(vals[0])
            self.cfg["device_index"] = dev_index
            save_config(self.cfg)
            messagebox.showinfo("Devices", f"Selected input device #{dev_index}\n\n{vals[1]}")

        ttk.Button(wrap, text="Use selected device", command=save_sel).pack(anchor="e")

        ttk.Label(wrap, text=f"Current: device_index={self.cfg.get('device_index')}, sr={self.cfg.get('samplerate')}, ch={self.cfg.get('channels')}",
                  foreground="#666").pack(anchor="w", pady=(8,0))

    # ----- Top bar actions -----
    def _on_record(self):
        if self.recording:
            return
        if _IMPORT_ERR:
            messagebox.showerror("Audio", f"sounddevice import error:\n{_IMPORT_ERR}")
            return
        try:
            self.recorder = Recorder(
                samplerate=self.cfg.get("samplerate", DEFAULT_SR),
                channels=self.cfg.get("channels", DEFAULT_CH),
                dtype=self.cfg.get("dtype", DEFAULT_DTYPE),
                device_index=self.cfg.get("device_index", None)
            )
            self.recorder.start()
            self.recording = True
            self.btn_record.config(state=tk.DISABLED)
            self.btn_stop.config(state=tk.NORMAL)
            self.status_var.set("Recording…")
        except Exception as e:
            messagebox.showerror("Record", f"Failed to start:\n{e}")

    def _on_stop(self):
        if not self.recording:
            return
        try:
            path, dur = self.recorder.stop()
            self.recording = False
            self.btn_record.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.DISABLED)
            self._refresh_logs()
            self.status_var.set("Saved")
            messagebox.showinfo("Saved", f"Saved:\n{path}\n\nDuration: {dur:.1f}s\nSidecar: {os.path.basename(path).replace('.wav','.json')}")
        except Exception as e:
            messagebox.showerror("Stop", f"Failed to stop/save:\n{e}")

    def _on_play_last(self):
        p = latest_wav()
        if not p:
            messagebox.showinfo("Play", "No recordings yet.")
            return
        play_wav(p)

    # ----- Logs actions -----
    def _selected_path(self):
        if not hasattr(self, "logs_list"):
            return None
        sel = self.logs_list.curselection()
        if not sel:
            return None
        name = self.logs_list.get(sel[0]).split()[0]
        return os.path.join(REC_DIR, name)

    def _on_play_selected(self):
        p = self._selected_path()
        if p and os.path.exists(p):
            play_wav(p)

    def _on_reveal_selected(self):
        p = self._selected_path()
        if not p: return
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", p])
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", os.path.dirname(p)])
        else:
            messagebox.showinfo("Reveal", os.path.dirname(p))

    # ----- Transcribe (NEW) -----
    def _on_transcribe_selected(self):
        p = self._selected_path()
        if not p:
            messagebox.showinfo("Transcribe", "Select a recording first.")
            return
        if not os.path.exists(p):
            messagebox.showerror("Transcribe", "File not found on disk.")
            return

        try:
            self.btn_transcribe.config(state=tk.DISABLED)
        except Exception:
            pass
        self.status_var.set("Transcribing…")
        threading.Thread(target=self._transcribe_worker, args=(p,), daemon=True).start()

    def _transcribe_worker(self, wav_path: str):
        try:
            try:
                from faster_whisper import WhisperModel
            except Exception:
                self.after(0, lambda: messagebox.showerror(
                    "Transcribe",
                    "faster-whisper is not installed.\n\n"
                    "In Terminal run:\n"
                    "source .venv/bin/activate && python3 -m pip install faster-whisper==1.0.3"
                ))
                return

            model_size = os.environ.get("MELLOW_WHISPER_SIZE", "base")
            compute_type = os.environ.get("MELLOW_WHISPER_COMPUTE", "int8")  # or "float32"
            model = WhisperModel(model_size, device="cpu", compute_type=compute_type)

            segments, info = model.transcribe(wav_path, language=None, vad_filter=True)

            txt_out = wav_path.replace(".wav", ".txt")
            with open(txt_out, "w", encoding="utf-8") as f:
                for seg in segments:
                    line = seg.text.strip()
                    if line:
                        f.write(line + "\n")

            meta = {
                "source": os.path.basename(wav_path),
                "model": model_size,
                "language": getattr(info, "language", None),
                "duration": getattr(info, "duration", None),
                "created_at": iso_now(),
            }
            with open(wav_path.replace(".wav", ".transcribe.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

            def done():
                self.status_var.set("Transcribed")
                messagebox.showinfo("Transcribe", f"Transcript saved:\n{txt_out}")
                if sys.platform == "darwin":
                    try:
                        subprocess.Popen(["open", txt_out])
                    except Exception:
                        pass
            self.after(0, done)

        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Transcribe", f"Failed: {e}"))
        finally:
            try:
                self.after(0, lambda: self.btn_transcribe.config(state=tk.NORMAL))
            except Exception:
                pass

    # ----- periodic UI updates -----
    def _tick(self):
        if self.recording and self.recorder:
            elapsed, rms = self.recorder.poll_into_buffer()
            m = int(elapsed // 60)
            s = int(elapsed % 60)
            self.lbl_timer.config(text=f"{m:02d}:{s:02d}")
            self.rms_var.set(max(0.0, min(1.0, rms)))
        self.after(100, self._tick)

# -----------------------
# Main
# -----------------------
if __name__ == "__main__":
    if _IMPORT_ERR:
        print("Warning: sounddevice failed to import:", _IMPORT_ERR, file=sys.stderr)
        print("UI will launch but recording won't work.", file=sys.stderr)
    app = App()
    app.mainloop()
