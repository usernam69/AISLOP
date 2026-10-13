"""SubtleSpawns Studio.exe: starts app.py with your installed Python and shows it in its own window.

Build:  python -m PyInstaller --onefile --windowed --name "SubtleSpawns Studio" --icon web/icon.ico studio_launcher.py
Keep the .exe in the AISLOP folder (it runs app.py from the folder it sits in).
"""
import shutil, subprocess, sys, time, urllib.request
from pathlib import Path

import webview

URL = "http://127.0.0.1:5055"
HERE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def up():
    try:
        urllib.request.urlopen(URL + "/api/job", timeout=1)
        return True
    except Exception:
        return False


def main():
    server = None
    if not up():   # not already open in another window
        if not (HERE / "app.py").exists():
            webview.create_window("SubtleSpawns Studio", html=f"<p style='font:16px system-ui;padding:20px'>Put this .exe in the "
                                  f"AISLOP folder (next to app.py). It is in {HERE}.</p>")
            webview.start()
            return
        python = shutil.which("pythonw") or shutil.which("python")
        server = subprocess.Popen([python, "app.py"], cwd=HERE, creationflags=subprocess.CREATE_NO_WINDOW,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if up() or server.poll() is not None:
                break
            time.sleep(0.2)
    webview.create_window("SubtleSpawns Studio", URL, width=1200, height=850, min_size=(420, 500))
    webview.start()
    if server:
        server.terminate()


if __name__ == "__main__":
    main()
