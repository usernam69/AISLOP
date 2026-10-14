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
        server = subprocess.Popen([python, "app.py"], cwd=HERE, creationflags=subprocess.CREATE_NO_WINDOW,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if up() or server.poll() is not None:
                break
            time.sleep(0.2)
    edge = next((p for p in EDGE if p.exists()), None)
    if edge:
        # its own profile folder keeps this window a separate process, so we know when it's closed
        subprocess.run([str(edge), f"--app={URL}", f"--user-data-dir={HERE / 'output' / 'studio_window'}",
                        "--window-size=1200,850", "--no-first-run"])
    else:
        webbrowser.open(URL)
        return   # no way to tell when a normal browser tab closes, so leave the server running
    if server:
        server.terminate()


if __name__ == "__main__":
    main()
