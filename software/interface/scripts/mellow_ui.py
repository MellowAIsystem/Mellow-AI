import tkinter as tk
import os, datetime, queue, threading, wave
import numpy as np
try:
    import sounddevice as sd
    _IMPORT_ERR = None
except Exception as e:
    sd = None
    _IMPORT_ERR = e

MENU_ITEMS = ["Discussion","Voices","Bluetooth","Devices","Logs","Settings"]
REC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "voice_input", "recordings"))

class Recorder:
    def __init__(self, samplerate=None, channels=1, dtype="int16"):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.q = queue.Queue()
        self.stream = None
        self.frames = []
        self.filepath = None
        self.worker = None

    def _callback(self, indata, frames, time, status):
        if status:
            print("sounddevice status:", status, flush=True)
        self.q.put(indata.copy())

    def start(self, out_dir=REC_DIR):
        os.makedirs(out_dir, exist_ok=True)
        if self.samplerate is None:
            in_dev = sd.default.device[0]
            info = sd.query_devices(in_dev)
            self.samplerate = int(info.get("default_samplerate", 48000) or 48000)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.filepath = os.path.join(out_dir, f"REC-{ts}.wav")
        self.frames = []
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
            wf.writeframes(audio.tobytes())
        return self.filepath

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mellow UI (Mic capture build)")
        self.geometry("900x540")
        self.configure(bg="#111")
        self.is_recording = False
        self.recorder = Recorder() if sd else None

        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        top = tk.Frame(self, bg="#0c0c0c", height=56); top.grid(row=0, column=0, columnspan=2, sticky="nsew"); top.grid_propagate(False)
        tk.Label(top, text="Mellow", fg="#f2f2f2", bg="#0c0c0c", font=("Helvetica", 18, "bold")).pack(side="left", padx=16)
        self.rec_btn = tk.Button(top, text="●  Record", fg="#fff", bg="#333", activebackground="#444", relief="flat", padx=14, pady=8, command=self.toggle_recording)
        self.rec_btn.pack(side="right", padx=16, pady=8)

        sidebar = tk.Frame(self, bg="#1a1a1a", width=220); sidebar.grid(row=1, column=0, sticky="ns"); sidebar.grid_propagate(False)
        self.menu = tk.Listbox(sidebar, activestyle="none", highlightthickness=0, fg="#e0e0e0", bg="#1a1a1a", selectbackground="#333", selectforeground="#fff", border=0, height=12)
        for item in MENU_ITEMS: self.menu.insert(tk.END, f"  {item}")
        self.menu.pack(fill="both", expand=True, padx=8, pady=(16,8))
        self.menu.bind("<<ListboxSelect>>", self.on_select)

        self.content = tk.Frame(self, bg="#0f0f0f"); self.content.grid(row=1, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1); self.content.grid_rowconfigure(0, weight=1)
        self.title_lbl = tk.Label(self.content, text="Welcome to Mellow", bg="#0f0f0f", fg="#fafafa", font=("Helvetica", 20, "bold")); self.title_lbl.pack(pady=24)
        self.body = tk.Label(self.content, text="", bg="#0f0f0f", fg="#c9c9c9", justify="left", font=("Helvetica", 12), anchor="nw"); self.body.pack(padx=24, anchor="nw")

        self.menu.selection_set(0)
        self.on_select(None)

    def toggle_recording(self):
        if not sd:
            self.set_status(f"Mic backend missing: pip install sounddevice (error: {_IMPORT_ERR})"); return
        if not self.is_recording:
            try:
                path = self.recorder.start(REC_DIR)
            except Exception as e:
                self.set_status("Failed to access microphone. Allow mic for Terminal/Python in System Settings → Privacy & Security → Microphone.\n\nError: "+str(e)); return
            self.is_recording = True
            self.rec_btn.configure(text="■  Stop", bg="#b30000")
            self.set_status(f"Recording… Saving to:\n{path}")
        else:
            path = self.recorder.stop(); self.is_recording = False
            self.rec_btn.configure(text="●  Record", bg="#333")
            self.set_status(f"Stopped. Saved:\n{path}" if path else "Stopped. (No audio captured)")

    def set_status(self, msg: str):
        if self.menu.curselection() and MENU_ITEMS[self.menu.curselection()[0]] == "Discussion":
            self.body.config(text=msg + "\n\n" + self.render_screen("Discussion"))
        else:
            self.body.config(text=msg + "\n\nOpen 'Discussion' to view status.")

    def on_select(self, _):
        idx = self.menu.curselection()
        if not idx: return
        name = MENU_ITEMS[idx[0]]
        self.title_lbl.config(text=name)
        self.body.config(text=self.render_screen(name))

    def render_screen(self, name: str) -> str:
        if name == "Discussion":
            return "\n".join([
                f"• Files save under: {REC_DIR}",
                "• Uses your mic’s native sample rate automatically.",
            ])
        if name == "Voices": return "Choose the speaking voice (placeholder)."
        if name == "Bluetooth": return "Pair headphones/speakers (placeholder)."
        if name == "Devices": return "Manage connected devices (placeholder)."
        if name == "Logs": return "Browse past sessions (placeholder)."
        if name == "Settings": return "General settings (placeholder)."
        return ""

if __name__ == "__main__":
    App().mainloop()
