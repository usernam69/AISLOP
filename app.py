"""SubtleSpawns control panel: watch, upload and make Shorts, see analytics, read reply drafts.

Run:  python app.py   then open http://127.0.0.1:5055  (only reachable from this PC)
"""
import csv, json, os, re, subprocess, sys, threading, time
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from make_short import BG_DIR, OUT_DIR, SERIES, STATS, read_runs

HERE = Path(__file__).resolve().parent
JOB_LOG = OUT_DIR / "app_job.log"
FOLDER = re.compile(r"^\d{4}-\d\d-\d\d_\d{4}$")
JOBS = {  # button -> command
    "make": ["make_short.py"],
    "make_upload": ["make_short.py", "--upload"],
    "stats": ["stats.py"],
    "recap": ["recap.py"],
    "monthly": ["recap.py", "--month"],
    "check_login": ["make_short.py", "--check-login"],
}
REPO = "usernam69/AISLOP"
app = Flask(__name__, static_folder=str(HERE / "web"), static_url_path="/static")
job = {"proc": None, "kind": None}
last_seen = [time.time()]   # the page pings while its window is open
lock = threading.Lock()


@app.get("/")
def index():
    return send_file(HERE / "web" / "index.html")


@app.get("/api/runs")
def runs():
    out = []
    for r in reversed(read_runs()):
        work = OUT_DIR / r["folder"]
        meta = json.loads((work / "meta.json").read_text(encoding="utf-8")) if (work / "meta.json").exists() else {}
        out.append(r | {"has_video": (work / "short.mp4").exists(), "description": meta.get("description", ""),
                        "script": meta.get("script", "")})
    return jsonify(out)


@app.get("/api/stats")
def stats():
    if not STATS.exists():
        return jsonify([])
    with open(STATS, newline="", encoding="utf-8") as f:
        return jsonify(list(csv.DictReader(f)))


@app.get("/api/replies")
def replies():
    p = HERE / "replies.md"
    return jsonify({"text": p.read_text(encoding="utf-8") if p.exists() else ""})


@app.get("/video/<folder>")
def video(folder):
    path = OUT_DIR / folder / "short.mp4"
    if not FOLDER.match(folder) or not path.exists():
        abort(404)
    return send_file(path, mimetype="video/mp4", conditional=True)


@app.get("/api/backgrounds")
def backgrounds():
    return jsonify(sorted(p.name for p in BG_DIR.glob("*.mp4")) if BG_DIR.exists() else [])


@app.get("/api/cloud")
def cloud():
    """Last cloud runs (public GitHub API) and the next few days' formats."""
    import datetime as dt
    import httpx
    try:
        r = httpx.get(f"https://api.github.com/repos/{REPO}/actions/workflows/daily-short.yml/runs?per_page=8", timeout=10).json()
        runs = [{"when": x["created_at"], "event": x["event"], "status": x["status"], "result": x["conclusion"], "url": x["html_url"]}
                for x in r.get("workflow_runs", [])]
    except Exception as e:
        runs, r = [], {"message": f"Couldn't reach GitHub ({type(e).__name__})"}
    days = []
    for k in range(1, 8):
        d = dt.date.today() + dt.timedelta(days=k)
        rota = SERIES[d.weekday()]
        days.append({"date": d.isoformat(), "day": d.strftime("%a"), "format": rota[d.isocalendar().week % len(rota)]["name"].replace(" #{n}", "")})
    return jsonify({"runs": runs, "error": r.get("message") if not runs else None, "upcoming": days,
                    "actions_url": f"https://github.com/{REPO}/actions", "secrets_url": f"https://github.com/{REPO}/settings/secrets/actions"})


@app.post("/api/ping")
def ping():
    last_seen[0] = time.time()
    return "", 204


def auto_exit():
    """Studio window mode: quit once the window has been closed for 2 minutes and no job is running."""
    while True:
        time.sleep(15)
        busy = job["proc"] is not None and job["proc"].poll() is None
        if not busy and time.time() - last_seen[0] > 120:
            os._exit(0)


@app.get("/api/job")
def job_status():
    with lock:
        running = job["proc"] is not None and job["proc"].poll() is None
        code = None if running or job["proc"] is None else job["proc"].returncode
    log = JOB_LOG.read_text(encoding="utf-8", errors="replace")[-6000:] if JOB_LOG.exists() else ""
    # hide ffmpeg's noisy encoder lines
    log = "\n".join(l for l in log.splitlines() if not re.match(r"\s*(\[|frame=|Input #|Output #|Stream|Metadata|Duration|Last message repeated|.*pixel format used)", l))
    return jsonify({"running": running, "kind": job["kind"], "exit_code": code, "log": log})


@app.post("/api/job")
def start_job():
    body = request.get_json(force=True)
    kind, folder = body.get("kind"), body.get("folder")
    if kind == "upload":
        if not folder or not FOLDER.match(folder):
            abort(400)
        args = ["make_short.py", "--upload-folder", folder]
    elif kind in JOBS:
        args = JOBS[kind]
    else:
        abort(400)
    extra = {}
    if kind in ("make", "make_upload"):
        bg, speed = body.get("bg") or "", str(body.get("speed") or "")
        if bg and bg in backgrounds().get_json():
            extra["BG_PICK"] = bg
        if speed in ("1", "1.25", "1.5", "2"):
            extra["BG_SPEED"] = speed
    with lock:
        if job["proc"] is not None and job["proc"].poll() is None:
            return jsonify({"error": "Another job is still running."}), 409
        OUT_DIR.mkdir(exist_ok=True)
        with open(JOB_LOG, "w", encoding="utf-8") as log:  # the child keeps its own copy of the handle
            job["proc"] = subprocess.Popen([sys.executable, "-u", *args], cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                                           env=os.environ | {"PYTHONIOENCODING": "utf-8"} | extra,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # no console pop-ups
        job["kind"] = kind
    return jsonify({"started": kind})


if __name__ == "__main__":
    print("SubtleSpawns control panel: http://127.0.0.1:5055")
    if os.getenv("STUDIO_AUTO_EXIT") == "1":
        threading.Thread(target=auto_exit, daemon=True).start()
    app.run(host="127.0.0.1", port=5055, debug=False)
