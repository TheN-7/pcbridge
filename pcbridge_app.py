"""
PC Bridge - desktop control app

A small system tray app for running the PC Bridge server without ever
opening a terminal. Click the tray icon to open a tiny window that shows
whether the server is running, its address, and the PIN your phone needs
-- with buttons to start/stop it and change the PIN or shared folder.

Dev:      python pcbridge_app.py
Packaged: see README-DESKTOP.md for the PyInstaller command that turns
          this into a single portable PCBridge.exe.

How this file is used, in two different ways:
  1. Normal launch (no arguments) -- shows the tray icon + window.
  2. `pcbridge_app.py --server` -- skips all of that and just runs the
     actual FastAPI/uvicorn server, blocking, exactly like `python
     server.py` would. The tray app launches *this exact file* with that
     flag as a subprocess, so Start/Stop can cleanly start/kill it. A
     packaged single-file EXE has no separate server.py sitting on disk
     to relaunch, so re-invoking itself with a flag is the standard way
     to give a frozen app a "worker" mode.
"""

import json
import secrets
import socket
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

BASE_DIR = Path(__file__).resolve().parent

# Midnight theme -- matches static/style.css, so the desktop control
# window doesn't look like a completely different product from the app.
BG = "#16191d"
CARD = "#1c1f24"
BORDER = "#2a2f36"
TEXT = "#f4f5f7"
MUTED = "#8b93a1"
BLUE = "#38bdf8"
ON_BLUE = "#0c2733"
GREEN = "#4ade80"
RED = "#f87171"


def app_dir() -> Path:
    """Where config.json lives -- the real EXE folder, not the temp folder
    a frozen single-file build extracts itself into. Mirrors server.py's
    app_dir() so both processes agree on where settings are stored."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return BASE_DIR


CONFIG_PATH = app_dir() / "config.json"


def load_config() -> dict:
    default = {"root_dir": str(Path.home()), "port": 8000, "pin": None}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            default.update({k: v for k, v in data.items() if v is not None})
        except Exception:
            pass
    if not default.get("pin"):
        default["pin"] = f"{secrets.randbelow(1000000):06d}"
    return default


def save_config(config: dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ---------- server subprocess management ----------

class ServerProcess:
    """Owns the actual server subprocess. Runs it as a separate process
    (rather than importing server.py in-thread) so Stop is a clean kill
    with no need to reach into uvicorn's internals to shut it down."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.log_path = app_dir() / "pcbridge.log"
        self._log_file = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.running:
            return

        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--server"]
        else:
            cmd = [sys.executable, str(BASE_DIR / "pcbridge_app.py"), "--server"]

        creationflags = 0
        if sys.platform == "win32":
            # Stops a console window from flashing up behind the tray app.
            creationflags = subprocess.CREATE_NO_WINDOW

        self._log_file = open(self.log_path, "a", buffering=1, encoding="utf-8")
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(app_dir()),
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

    def stop(self):
        if not self.running:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None


server = ServerProcess()


# ---------- tray icon ----------

def build_tray_image():
    from PIL import Image

    icon_path = BASE_DIR / "static" / "icons" / "icon-192.png"
    try:
        return Image.open(icon_path)
    except Exception:
        # Bundled icon missing for some reason -- fall back to a flat
        # square in the app's accent color rather than crashing the tray.
        return Image.new("RGB", (64, 64), BLUE)


def build_tray_icon(on_open, on_toggle, on_quit):
    import pystray

    return pystray.Icon(
        "pcbridge",
        build_tray_image(),
        "PC Bridge",
        menu=pystray.Menu(
            pystray.MenuItem("Open PC Bridge", on_open, default=True),
            pystray.MenuItem(
                lambda item: "Stop server" if server.running else "Start server",
                on_toggle,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        ),
    )


# ---------- window ----------

class App:
    def __init__(self):
        self.config = load_config()
        if not self.config.get("pin"):
            self.config["pin"] = f"{secrets.randbelow(1000000):06d}"
            save_config(self.config)

        self.root = tk.Tk()
        self.root.title("PC Bridge")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        self._build_ui()
        self.icon = build_tray_icon(self.on_tray_open, self.on_tray_toggle, self.on_quit)
        threading.Thread(target=self.icon.run, daemon=True).start()

        self.refresh()

    # ---- layout ----

    def _section(self, parent):
        frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="x", padx=16, pady=(0, 12))
        return frame

    def _build_ui(self):
        tk.Label(
            self.root, text="PC Bridge", bg=BG, fg=TEXT, font=("Segoe UI", 15, "bold")
        ).pack(anchor="w", padx=16, pady=(16, 10))

        status_frame = self._section(self.root)
        row = tk.Frame(status_frame, bg=CARD)
        row.pack(fill="x", padx=12, pady=12)
        self.status_dot = tk.Label(row, text="●", bg=CARD, fg=RED, font=("Segoe UI", 12))
        self.status_dot.pack(side="left")
        self.status_label = tk.Label(
            row, text="Stopped", bg=CARD, fg=TEXT, font=("Segoe UI", 11, "bold")
        )
        self.status_label.pack(side="left", padx=(6, 0))

        self.address_label = tk.Label(
            status_frame, text="", bg=CARD, fg=BLUE, font=("Segoe UI", 10), cursor="hand2"
        )
        self.address_label.pack(anchor="w", padx=12, pady=(0, 10))
        self.address_label.bind("<Button-1>", self.on_copy_address)

        self.toggle_btn = tk.Button(
            status_frame,
            text="Start server",
            command=self.on_toggle_clicked,
            bg=BLUE,
            fg=ON_BLUE,
            activebackground=BLUE,
            activeforeground=ON_BLUE,
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=6,
        )
        self.toggle_btn.pack(anchor="w", padx=12, pady=(0, 12))

        pin_frame = self._section(self.root)
        tk.Label(
            pin_frame, text="PIN your phone enters", bg=CARD, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w", padx=12, pady=(12, 2))
        pin_row = tk.Frame(pin_frame, bg=CARD)
        pin_row.pack(fill="x", padx=12, pady=(0, 12))
        self.pin_var = tk.StringVar(value=self.config["pin"])
        self.pin_entry = tk.Entry(
            pin_row,
            textvariable=self.pin_var,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Consolas", 12),
            width=10,
        )
        self.pin_entry.pack(side="left", ipady=4)
        tk.Button(
            pin_row, text="Randomize", command=self.on_randomize_pin,
            bg=BORDER, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 9),
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            pin_row, text="Save", command=self.on_save_pin,
            bg=BORDER, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 9),
        ).pack(side="left", padx=(6, 0))

        folder_frame = self._section(self.root)
        tk.Label(
            folder_frame, text="Shared folder", bg=CARD, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w", padx=12, pady=(12, 2))
        self.folder_label = tk.Label(
            folder_frame, text=self.config["root_dir"], bg=CARD, fg=TEXT,
            font=("Segoe UI", 9), wraplength=280, justify="left",
        )
        self.folder_label.pack(anchor="w", padx=12, pady=(0, 8))
        tk.Button(
            folder_frame, text="Change folder...", command=self.on_change_folder,
            bg=BORDER, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 9),
        ).pack(anchor="w", padx=12, pady=(0, 12))

        self.hint_label = tk.Label(
            self.root, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=300, justify="left"
        )
        self.hint_label.pack(anchor="w", padx=16, pady=(0, 14))

    # ---- state / actions ----

    def refresh(self):
        """The recurring poll loop -- reschedules itself. Call this once,
        from __init__, and never again; anything that wants an immediate
        UI update after an action should call _update_display() instead,
        or this would stack up an extra parallel polling loop every time."""
        self._update_display()
        self.root.after(1500, self.refresh)

    def _update_display(self):
        running = server.running
        self.status_dot.config(fg=GREEN if running else RED)
        self.status_label.config(text="Running" if running else "Stopped")
        self.toggle_btn.config(text="Stop server" if running else "Start server")
        if running:
            self.address_label.config(
                text=f"http://{lan_ip()}:{self.config.get('port', 8000)}"
            )
        else:
            self.address_label.config(text="Not reachable while stopped")

    def on_toggle_clicked(self):
        if server.running:
            server.stop()
        else:
            server.start()
            # Give it a moment, then check it actually stayed up (a bad
            # config or a port already in use makes it exit immediately).
            self.root.after(800, self._check_started)
        self._update_display()

    def _check_started(self):
        if not server.running:
            messagebox.showerror(
                "PC Bridge",
                "The server didn't stay running -- it may have exited "
                f"immediately. Check {server.log_path} for details (e.g. "
                "the port may already be in use).",
            )
        self._update_display()

    def on_copy_address(self, event=None):
        text = self.address_label.cget("text")
        if not text.startswith("http://"):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.hint_label.config(text="Address copied to clipboard.")

    def on_randomize_pin(self):
        self.pin_var.set(f"{secrets.randbelow(1000000):06d}")

    def on_save_pin(self):
        new_pin = self.pin_var.get().strip()
        if len(new_pin) < 4:
            messagebox.showwarning("PC Bridge", "PIN should be at least 4 characters.")
            return
        self.config["pin"] = new_pin
        save_config(self.config)
        self._maybe_offer_restart()

    def on_change_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.config["root_dir"])
        if not chosen:
            return
        self.config["root_dir"] = chosen
        save_config(self.config)
        self.folder_label.config(text=chosen)
        self._maybe_offer_restart()

    def _maybe_offer_restart(self):
        if not server.running:
            self.hint_label.config(text="Saved. Starts using this the next time you start the server.")
            return
        if messagebox.askyesno(
            "PC Bridge", "The server is running. Restart it now so this change takes effect?"
        ):
            server.stop()
            server.start()
            self.root.after(800, self._check_started)
        else:
            self.hint_label.config(text="Saved. Restart the server for this to take effect.")
        self._update_display()

    # ---- window show/hide, tray callbacks ----

    def hide_window(self):
        self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_tray_open(self, icon=None, item=None):
        self.root.after(0, self.show_window)

    def on_tray_toggle(self, icon=None, item=None):
        self.root.after(0, self.on_toggle_clicked)

    def on_quit(self, icon=None, item=None):
        def _quit():
            server.stop()
            self.icon.stop()
            self.root.destroy()

        self.root.after(0, _quit)

    def run(self):
        self.root.mainloop()


def main():
    App().run()


if __name__ == "__main__":
    if "--server" in sys.argv:
        import server

        server.run()
    else:
        main()
