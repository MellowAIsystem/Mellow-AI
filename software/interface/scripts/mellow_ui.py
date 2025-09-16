import tkinter as tk

MENU_ITEMS = ["Discussion","Voices","Bluetooth","Devices","Logs","Settings"]

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mellow UI (Prototype)")
        self.geometry("800x500")
        self.configure(bg="#111")
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = tk.Frame(self, bg="#1a1a1a", width=220)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        tk.Label(sidebar, text="Mellow", fg="#f2f2f2", bg="#1a1a1a",
                 font=("Helvetica", 18, "bold"), pady=16).pack(anchor="w", padx=16)

        self.menu = tk.Listbox(sidebar, activestyle="none", highlightthickness=0,
                               fg="#e0e0e0", bg="#1a1a1a", selectbackground="#333",
                               selectforeground="#fff", border=0)
        for item in MENU_ITEMS:
            self.menu.insert(tk.END, f"  {item}")
        self.menu.pack(fill="both", expand=True, padx=8, pady=8)
        self.menu.bind("<<ListboxSelect>>", self.on_select)

        self.content = tk.Frame(self, bg="#0f0f0f")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.title_lbl = tk.Label(self.content, text="Welcome to Mellow", bg="#0f0f0f", fg="#fafafa",
                                  font=("Helvetica", 20, "bold"))
        self.title_lbl.pack(pady=24)

        self.body = tk.Label(self.content, text="Select a menu item to get started.",
                             bg="#0f0f0f", fg="#c9c9c9", justify="left", font=("Helvetica", 12))
        self.body.pack(padx=24, anchor="nw")

        self.menu.selection_set(0)
        self.on_select(None)

    def on_select(self, _):
        idx = self.menu.curselection()
        if not idx:
            return
        name = MENU_ITEMS[idx[0]]
        self.title_lbl.config(text=name)
        self.body.config(text=self.render_screen(name))

    def render_screen(self, name: str) -> str:
        if name == "Discussion":
            return ("• Press ⏺ to start a session (coming soon)\n"
                    "• Your logs will appear here.")
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
