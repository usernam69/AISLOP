"""SubtleSpawns Studio.exe: starts app.py with your installed Python and shows it in its own window (Edge app mode).

Build:  python -m PyInstaller --onefile --windowed --name "SubtleSpawns Studio" --icon web/icon.ico studio_launcher.py
Keep the .exe in the AISLOP folder (it runs app.py from the folder it sits in).
"""
import ctypes, os, shutil, subprocess, sys, time, urllib.request, webbrowser
from pathlib import Path

URL = "http://127.0.0.1:5055"
HERE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
EDGE = [Path(os.environ.get(v, "")) / "Microsoft/Edge/Application/msedge.exe" for v in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData")]


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
            ctypes.windll.user32.MessageBoxW(0, f"Put this .exe in the AISLOP folder (next to app.py).\nIt is in {HERE}.",
                                             "SubtleSpawns Studio", 0x10)
            return
        python = shutil.which("pythonw") or shutil.which("python")
        if not python:
            ctypes.windll.user32.MessageBoxW(0, "Python isn't installed (or isn't on PATH).", "SubtleSpawns Studio", 0x10)
            return
        (HERE / "output").mkdir(exist_ok=True)
        log_path = HERE / "output" / "studio_server.log"
        with open(log_path, "w", encoding="utf-8") as log:
            server = subprocess.Popen([python, "app.py"], cwd=HERE, creationflags=subprocess.CREATE_NO_WINDOW,
                                      stdout=log, stderr=subprocess.STDOUT, env=os.environ | {"STUDIO_AUTO_EXIT": "1"})
        deadline = time.time() + 90   # a cold start (antivirus scanning, first import) can be slow
        while not up():
            if server.poll() is not None or time.time() > deadline:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
                ctypes.windll.user32.MessageBoxW(0, "The Studio couldn't start:\n\n" + (tail or "no output"), "SubtleSpawns Studio", 0x10)
                server.kill()
                return
            time.sleep(0.3)
    edge = next((p for p in EDGE if p.exists()), None)
    if edge:
        # Edge relaunches itself and lingers in the background, so it can't tell us when the window closes;
        # the app watches for the page's pings instead and exits on its own (STUDIO_AUTO_EXIT).
        subprocess.Popen([str(edge), f"--app={URL}", f"--user-data-dir={HERE / 'output' / 'studio_window'}",
                          "--window-size=1200,850", "--no-first-run"])
    else:
        webbrowser.open(URL)
    # the server exits by itself a couple of minutes after the window (or tab) closes


if __name__ == "__main__":
    main()
