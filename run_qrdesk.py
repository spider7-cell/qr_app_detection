from __future__ import annotations

import threading
import time
import urllib.request
import webbrowser

import uvicorn


HOST = "127.0.0.1"
PORT = 8000
APP_URL = f"http://{HOST}:{PORT}"


def open_browser_when_ready() -> None:
    health_url = f"{APP_URL}/api/health"
    for _ in range(80):
        try:
            with urllib.request.urlopen(health_url, timeout=0.4):
                webbrowser.open(APP_URL)
                return
        except Exception:
            time.sleep(0.25)
    webbrowser.open(APP_URL)


if __name__ == "__main__":
    threading.Thread(target=open_browser_when_ready, daemon=True).start()
    uvicorn.run("qrdesk_app:app", host=HOST, port=PORT, reload=False)