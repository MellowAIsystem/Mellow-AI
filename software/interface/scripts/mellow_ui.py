import tkinter as tk

MENU_ITEMS = ["Discussion","Voices","Bluetooth","Devices","Logs","Settings"]

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mellow UI (Prototype)")
        self.geometry("900x540")
        self.configure(bg="#111")
        self.is_recording = False

        # layout
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)

        # top bar
        top = tk.Frame(self, bg="#0c0c0c", height=56)
        top.grid(row=0, column=0, columnspan=2, sticky="nsew")
        top.grid_propagate(False)

        title = tk.Label(top, text="Mellow", fg="#f2f2f2", bg="#0c0c0c",
                         font=("Helvetica", 18, "bold"))
        title.pack(side="left", padx=16)

        self.rec_btn = tk.Button(
            top,
            text="●  Record",
            fg="#fff", bg="#333", activebackground="#444", activeforeground="#fff",
            relief="flat", padx=14, pady=8,
            command=self.toggle_recording
        )
        self.rec_btn.pack(side="right", padx=16, pady=8)

        # sidebar
        sidebar = tk.Frame(self, bg="#1a1a1a", width=220)
        sidebar.grid(row=1, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        self.menu = tk.Listbox(sidebar, activestyle="none", highlightthickness=0,
                               fg="#e0e0e0", bg="#1a1a1a", selectbackground="#333",
                               selectforeground="#fff", border=0, height=12)
        for item in MENU_ITEMS:
            self.menu.insert(tk.END, f"  {item}")
        self.menu.pack(fill="both", expand=True, padx=8, pady=(16,8))
        self.menu.bind("<<ListboxSelect>>", self.on_select)

        # main content
        self.content = tk.Frame(self, bg="#0f0f0f")
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.title_lbl = tk.Label(self.content, text="Welcome to Mellow",
                                  bg="#0f0f0f", fg="#fafafa",
                                  font=("Helvetica", 20, "bold"))
        self.title_lbl.pack(pady=24)

        self.body = tk.Label(self.content, text="Select a menu item to get started.",
                             bg="#0f0f0f", fg="#c9c9c9", justify="left",
                             font=("Helvetica", 12), anchor="nw")
        self.body.pack(padx=24, anchor="nw")

        self.menu.selection_set(0)
        self.on_select(None)

    # ----- UI actions -----
    def toggle_recording(self):
        self.is_recording = not self.is_recording
        if self.is_recording:
            self.rec_btn.configure(text="■  Stop", bg="#b30000")
            self.set_status("Recording… (placeholder)")
        else:
            self.rec_btn.configure(text="●  Record", bg="#333")
            self.set_status("Stopped. (No audio captured yet)")

    def set_status(self, msg: str):
        if self.menu.curselection() and MENU_ITEMS[self.menu.curselection()[0]] == "Discussion":
            self.body.config(text=f"• {msg}\n\n" + self.render_screen("Discussion"))
        else:
            # hint user to go to Discussion
            self.body.config(text=f"{msg}\n\nOpen 'Discussion' to view recording status.")

    def on_select(self, _):
        idx = self.menu.curselection()
        if not idx:
            return
        name = MENU_ITEMS[idx[0]]
        self.title_lbl.config(text=name)
        self.body.config(text=self.render_screen(name))

    def render_screen(self, name: str) -> str:
        if name == "Discussion":
            lines = [
                "• Press Record to start a session (UI only for now).",
                "• Your logs will appear here (coming soon).",
            ]
            return "\n".join(lines)
        if name == "Voices":
            return "Choose the speaking voice (placeholder)."
        if name == "Bluetooth":
            return "Pair headphones/speakers (placeholder)."
        if name == "Devices":
            return "Manage connected devices (placeholder)."
        if name == "Logs":
            return "Browse past sessions (placeholder)."
        if name == "Settings":
            return "General settings (placeholder)."
        return ""

if __name__ == "__main__":
    App().mainloop()
