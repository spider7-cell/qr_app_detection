from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


APP_NAME = "QR Desk"
APP_VERSION = "1.0"
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / APP_NAME
START_MENU_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME
DESKTOP_DIR = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


def bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def payload_dir() -> Path:
    return bundle_root() / APP_NAME


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_hidden_powershell(script: str) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        creationflags=flags,
    )


def create_shortcut(path: Path, target: Path, description: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    icon = INSTALL_DIR / "_internal" / "assets" / "qrdesk_icon.ico"
    script = f"""
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut({powershell_quote(str(path))})
$s.TargetPath = {powershell_quote(str(target))}
$s.WorkingDirectory = {powershell_quote(str(INSTALL_DIR))}
$s.IconLocation = {powershell_quote(str(icon))}
$s.Description = {powershell_quote(description)}
$s.Save()
"""
    run_hidden_powershell(script)


def remove_old_stop_shortcut() -> None:
    for folder in [DESKTOP_DIR, START_MENU_DIR]:
        old = folder / "Stop QR Desk.lnk"
        try:
            old.unlink()
        except FileNotFoundError:
            pass


def clear_install_dir_preserving_data() -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    for child in INSTALL_DIR.iterdir():
        if child.name.lower() == "app_data":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def copy_payload() -> None:
    source = payload_dir()
    if not source.exists():
        raise RuntimeError(f"Installer payload not found: {source}")
    clear_install_dir_preserving_data()
    for item in source.iterdir():
        destination = INSTALL_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def write_uninstaller() -> None:
    script = f"""@echo off
setlocal
set APPDIR={INSTALL_DIR}
echo Closing QR Desk if it is running is recommended before uninstalling.
pause
rmdir /s /q "%APPDIR%"
del "%USERPROFILE%\Desktop\QR Desk.lnk" 2>nul
rmdir /s /q "{START_MENU_DIR}" 2>nul
echo QR Desk removed.
pause
"""
    (INSTALL_DIR / "Uninstall QR Desk.bat").write_text(script, encoding="utf-8")


def show_message(title: str, message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(title, message)
        root.destroy()
    except Exception:
        pass


def install() -> None:
    copy_payload()
    write_uninstaller()
    exe = INSTALL_DIR / "QR Desk.exe"
    create_shortcut(DESKTOP_DIR / "QR Desk.lnk", exe, "Launch QR Desk")
    create_shortcut(START_MENU_DIR / "QR Desk.lnk", exe, "Launch QR Desk")
    remove_old_stop_shortcut()
    show_message("QR Desk Setup", f"QR Desk v{APP_VERSION} installed successfully.\n\nDesktop shortcut created.")


if __name__ == "__main__":
    try:
        install()
    except Exception as exc:
        show_message("QR Desk Setup", f"Installation failed:\n{exc}")
        raise
