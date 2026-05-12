from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn


APP_NAME = "QR Desk"
APP_VERSION = "1.0"
HOST = "127.0.0.1"
PORT = 8000
APP_URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{APP_URL}/api/health"

_SERVER: uvicorn.Server | None = None
_SERVER_THREAD: threading.Thread | None = None
_SERVER_LOCK = threading.Lock()
_TRAY_ICON: Any = None


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT_DIR = app_root()
os.environ.setdefault("QRDESK_ROOT_DIR", str(ROOT_DIR))
os.chdir(ROOT_DIR)


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", ROOT_DIR))
    return base.joinpath(*parts)


def server_running() -> bool:
    return bool(_SERVER_THREAD and _SERVER_THREAD.is_alive())


def health_ready() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=0.35):
            return True
    except Exception:
        return False


def wait_until_ready(timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if health_ready():
            return True
        time.sleep(0.25)
    return False


def open_when_ready() -> None:
    wait_until_ready()
    webbrowser.open(APP_URL)


def _run_server() -> None:
    global _SERVER
    import qrdesk_app

    config = uvicorn.Config(
        qrdesk_app.app,
        host=HOST,
        port=PORT,
        log_level="warning",
        access_log=False,
    )
    _SERVER = uvicorn.Server(config)
    _SERVER.run()


def start_server(open_after_start: bool = False) -> None:
    global _SERVER_THREAD
    with _SERVER_LOCK:
        if server_running():
            if open_after_start:
                threading.Thread(target=open_when_ready, daemon=True).start()
            return
        _SERVER_THREAD = threading.Thread(target=_run_server, name="QRDeskServer", daemon=True)
        _SERVER_THREAD.start()
    if open_after_start:
        threading.Thread(target=open_when_ready, daemon=True).start()


def stop_server() -> None:
    global _SERVER, _SERVER_THREAD
    with _SERVER_LOCK:
        server = _SERVER
        thread = _SERVER_THREAD
        if server is not None:
            server.should_exit = True
    if thread and thread.is_alive():
        thread.join(timeout=8)
    with _SERVER_LOCK:
        if not (_SERVER_THREAD and _SERVER_THREAD.is_alive()):
            _SERVER = None
            _SERVER_THREAD = None


def restart_server() -> None:
    stop_server()
    start_server(open_after_start=False)
    show_splash_until_ready(open_browser=True)


def open_app() -> None:
    start_server(open_after_start=True)


def quit_app(icon: Any = None) -> None:
    stop_server()
    if icon is not None:
        try:
            icon.stop()
        except Exception:
            pass
    sys.exit(0)


def startup_shortcut_path() -> Path:
    appdata = Path(os.environ.get("APPDATA", str(Path.home())))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "QR Desk.lnk"


def startup_target() -> tuple[str, str]:
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve()), ""
    return sys.executable, f'"{Path(__file__).resolve()}"'


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_hidden_powershell(script: str) -> None:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        creationflags=flags,
    )


def is_startup_enabled() -> bool:
    return startup_shortcut_path().exists()


def set_startup_enabled(enabled: bool) -> None:
    shortcut = startup_shortcut_path()
    if not enabled:
        try:
            shortcut.unlink()
        except FileNotFoundError:
            pass
        return

    shortcut.parent.mkdir(parents=True, exist_ok=True)
    target, args = startup_target()
    icon = resource_path("assets", "qrdesk_icon.ico")
    script = f"""
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut({powershell_quote(str(shortcut))})
$s.TargetPath = {powershell_quote(target)}
$s.Arguments = {powershell_quote(args)}
$s.WorkingDirectory = {powershell_quote(str(ROOT_DIR))}
$s.IconLocation = {powershell_quote(str(icon))}
$s.Description = 'Launch QR Desk at Windows startup'
$s.Save()
"""
    run_hidden_powershell(script)


def toggle_startup(icon: Any = None) -> None:
    set_startup_enabled(not is_startup_enabled())
    if icon is not None:
        try:
            icon.update_menu()
        except Exception:
            pass


def create_icon_image():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None

    icon_file = resource_path("assets", "qrdesk_icon.png")
    if icon_file.exists():
        return Image.open(icon_file)

    image = Image.new("RGBA", (256, 256), (20, 36, 54, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, 238, 238), radius=44, fill=(238, 246, 252, 255))
    draw.rounded_rectangle((42, 42, 214, 214), radius=28, fill=(24, 42, 63, 255))
    for x, y in [(64, 64), (150, 64), (64, 150)]:
        draw.rounded_rectangle((x, y, x + 42, y + 42), radius=8, fill=(237, 246, 252, 255))
        draw.rounded_rectangle((x + 11, y + 11, x + 31, y + 31), radius=4, fill=(40, 98, 154, 255))
    for x, y, w, h in [(150, 150, 22, 22), (184, 150, 18, 52), (150, 184, 52, 18)]:
        draw.rounded_rectangle((x, y, x + w, y + h), radius=5, fill=(89, 170, 137, 255))
    try:
        font = ImageFont.truetype("seguisb.ttf", 34)
    except Exception:
        font = None
    draw.text((75, 112), "QR", fill=(238, 246, 252, 255), font=font)
    return image


def show_splash_until_ready(open_browser: bool = True) -> None:
    try:
        import tkinter as tk
    except Exception:
        if open_browser:
            open_when_ready()
        else:
            wait_until_ready()
        return

    splash = tk.Tk()
    splash.title(APP_NAME)
    splash.overrideredirect(True)
    splash.attributes("-topmost", True)
    width, height = 430, 230
    x = int((splash.winfo_screenwidth() - width) / 2)
    y = int((splash.winfo_screenheight() - height) / 2)
    splash.geometry(f"{width}x{height}+{x}+{y}")
    splash.configure(bg="#17324a")

    card = tk.Frame(splash, bg="#eef6fc", padx=28, pady=24)
    card.place(relx=0.5, rely=0.5, anchor="center", width=380, height=178)
    tk.Label(card, text="QR Desk", bg="#eef6fc", fg="#17324a", font=("Segoe UI", 24, "bold")).pack(anchor="w")
    tk.Label(card, text=f"v{APP_VERSION} starting...", bg="#eef6fc", fg="#53657f", font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 18))
    status = tk.Label(card, text="Starting scanner workspace", bg="#eef6fc", fg="#2c5b77", font=("Segoe UI", 11, "bold"))
    status.pack(anchor="w")
    bar = tk.Canvas(card, width=320, height=8, bg="#d7e6f2", highlightthickness=0)
    bar.pack(anchor="w", pady=(14, 0))

    start = time.time()

    def poll() -> None:
        elapsed = time.time() - start
        fill = min(320, int((elapsed % 1.8) / 1.8 * 320))
        bar.delete("all")
        bar.create_rectangle(0, 0, fill, 8, fill="#4c8fc7", width=0)
        if health_ready() or elapsed > 20:
            splash.destroy()
            if open_browser:
                webbrowser.open(APP_URL)
            return
        splash.after(120, poll)

    splash.after(120, poll)
    splash.mainloop()


def run_tray() -> None:
    global _TRAY_ICON
    try:
        import pystray
    except Exception:
        run_control_window()
        return

    image = create_icon_image()
    if image is None:
        run_control_window()
        return

    menu = pystray.Menu(
        pystray.MenuItem("Open", lambda icon, item: open_app(), default=True),
        pystray.MenuItem("Stop", lambda icon, item: stop_server()),
        pystray.MenuItem("Restart", lambda icon, item: restart_server()),
        pystray.MenuItem("Start with Windows", lambda icon, item: toggle_startup(icon), checked=lambda item: is_startup_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, item: quit_app(icon)),
    )
    _TRAY_ICON = pystray.Icon(APP_NAME, image, APP_NAME, menu)
    start_server(open_after_start=False)
    show_splash_until_ready(open_browser=True)
    _TRAY_ICON.run()


def run_control_window() -> None:
    import tkinter as tk
    from tkinter import ttk

    start_server(open_after_start=False)
    show_splash_until_ready(open_browser=True)
    root = tk.Tk()
    root.title(APP_NAME)
    root.resizable(False, False)
    root.geometry("350x285")

    frame = ttk.Frame(root, padding=22)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="QR Desk", font=("Segoe UI", 18, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Desktop controls", foreground="#53657f").pack(anchor="w", pady=(0, 16))

    startup_var = tk.BooleanVar(value=is_startup_enabled())

    def sync_startup() -> None:
        set_startup_enabled(bool(startup_var.get()))

    ttk.Button(frame, text="Open", command=open_app).pack(fill="x", pady=4)
    ttk.Button(frame, text="Stop", command=stop_server).pack(fill="x", pady=4)
    ttk.Button(frame, text="Restart", command=restart_server).pack(fill="x", pady=4)
    ttk.Checkbutton(frame, text="Start with Windows", variable=startup_var, command=sync_startup).pack(anchor="w", pady=(10, 4))
    ttk.Button(frame, text="Quit", command=lambda: quit_app()).pack(fill="x", pady=(16, 4))

    root.protocol("WM_DELETE_WINDOW", lambda: quit_app())
    root.mainloop()


if __name__ == "__main__":
    run_tray()

