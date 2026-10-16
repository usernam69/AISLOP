"""Daily tech-news Short for @SubtleSpawns.

Pulls tech headlines, writes a script (Claude via OpenRouter, Gemini as free backup),
fact-checks it, voices it (ElevenLabs, then free voices), renders a 1080x1920 Short over a
random slice of background gameplay with word-by-word subtitles, key-word photos, a progress
bar and ducked music, then optionally uploads it to YouTube.

Usage:
    python make_short.py                  # render only
    python make_short.py --upload         # render + upload (or hold for review if REVIEW_MODE=1)
    python make_short.py --upload-latest  # upload the newest rendered Short that isn't uploaded yet
"""
import argparse, asyncio, calendar, csv, datetime as dt, html, json, os, random, re, shutil, subprocess, sys, time
from pathlib import Path

import edge_tts, feedparser, imageio_ffmpeg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

BG_VIDEO = Path(os.getenv("BG_VIDEO", HERE.parent / "6 hour Minecraft parkour run (no falls, study, sleep).mp4"))
BG_DIR = HERE / "backgrounds"       # extra background gameplay .mp4 files (optional)
OUT_DIR = HERE / "output"
READY_DIR = HERE / "ready_to_post"  # copies + captions for TikTok / Reels
MUSIC_DIR = HERE / "music"          # royalty-free .mp3 files; optional mood subfolders: tense/ upbeat/ calm/
STATE = HERE / "used_headlines.json"
RUNS = HERE / "runs.csv"
STATS = HERE / "stats.csv"
TOKEN = HERE / "token.json"
TOKEN_ISSUED = HERE / "token_issued.txt"
VOICE = os.getenv("VOICE", "en-US-AndrewMultilingualNeural")
PRIVACY = os.getenv("YT_PRIVACY", "private")   # switch to "public" once your API audit is approved
REVIEW_MODE = os.getenv("REVIEW_MODE") == "1"  # render daily, upload only after you run --upload-latest
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
UA = {"User-Agent": "AISLOP/1.0 (SubtleSpawns YouTube Shorts)"}
FEEDS = [
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://techcrunch.com/feed/",
    "https://www.engadget.com/rss.xml",
    "https://www.wired.com/feed/rss",
    "https://9to5mac.com/feed/",
    "https://www.androidauthority.com/feed/",
    "https://www.tomshardware.com/feeds/all",
]
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
RUN_FIELDS = ["date", "time", "folder", "title", "topic", "series", "link", "video_id", "images", "voice", "privacy", "comment_posted", "hook"]

# one theme per weekday (Monday = 0); "news" False means the script doesn't come from a headline
SAVE = ' Before the follow line, say "Save this for later."'
# Each weekday rotates through its formats week by week (ISO week number).
SERIES = [
    [{"name": "AI Monday", "news": True, "brief": "Prefer an AI story."},
     {"name": "AI Tool Speedrun", "news": False, "brief": "Ignore the headlines. Show one real, free AI tool and one useful task it does, step by step, in under 40 seconds. Name the tool and where to get it." + SAVE},
     {"name": "Explain Like I'm 10", "news": True, "brief": "Take the most confusing tech story and explain it in words a 10-year-old understands, with one everyday comparison."}],
    [{"name": "Security Tuesday", "news": True, "brief": "Prefer a cybersecurity or privacy story."},
     {"name": "Scam of the Week", "news": True, "brief": "Pick a scam, fraud or phishing story (or a well-known current scam if none). Explain how the scam works and give 2 concrete signs to spot it." + SAVE}],
    [{"name": "Quiz Wednesday", "news": True, "brief": "Format: open with a quiz question about the story, reveal the answer near the end."},
     {"name": "Myth or Fact", "news": False, "brief": "Ignore the headlines. Hook with a popular tech belief as a question (e.g. 'Does charging overnight ruin your battery?'). Keep the answer for the last third, then explain why. Only use well-established facts."}],
    [{"name": "Tip Thursday", "news": False, "brief": "Ignore the headlines. Teach one evergreen, practical tech tip (phone, PC, privacy or productivity), step by step." + SAVE},
     {"name": "Settings to Change Today", "news": False, "brief": "Ignore the headlines. Teach one hidden iPhone, Android or Windows setting worth changing today: where it is (exact menu path) and why." + SAVE},
     {"name": "Shortcut Thursday", "news": False, "brief": "Ignore the headlines. Teach 3 real keyboard shortcuts (Windows, Mac or Chrome) that save time, each with the exact keys." + SAVE},
     {"name": "Countdown", "news": False, "brief": "Ignore the headlines. A top-3 countdown (e.g. '3 apps quietly draining your battery'), number 3 first, number 1 last. Only real, current examples." + SAVE},
     {"name": "Before and After", "news": False, "brief": "Ignore the headlines. 'Your phone/PC vs your phone/PC after these 2 changes': describe the problem, the 2 changes, and the result." + SAVE}],
    [{"name": "Myth-Busting Friday", "news": True, "brief": "Use the story to bust a popular tech myth connected to it."},
     {"name": "Tech in 1 Number", "news": True, "brief": "Build the Short around the single most striking number in the story: open with the number, then explain what it means."},
     {"name": "Prediction Friday", "news": True, "brief": "Pick a story about something still coming (a launch, a rollout, a court decision). Make one clear, reasoned prediction about what happens next and say when we'll know."}],
    [{"name": "Throwback Saturday", "news": False, "brief": "Ignore the headlines. Tell a 'this day in tech history' story for {today}: a real, well-documented event from this calendar date. Only use facts you are certain of."},
     {"name": "What Happened To", "news": False, "brief": "Ignore the headlines. Tell the rise and fall of one well-known discontinued tech product or service (e.g. Google Glass, Vine, Zune): the hype, what went wrong, the lesson. Only use facts you are certain of."}],
    [{"name": "Gadget Sunday", "news": True, "brief": "Prefer a gadget or hardware story."},
     {"name": "Is It Worth It", "news": True, "brief": "Pick a gadget or product story. Give a verdict: the price, the main pro, the main con, and who should (and shouldn't) buy it."}],
]
MORE_FORMATS = {0: [{'name': 'Tech Word of the Day', 'news': False, 'brief': 'Ignore the headlines. Explain one current tech buzzword (e.g. RAG, NPU, eSIM, passkey) in plain words with one everyday comparison.'}, {'name': 'AI Explained #{n}', 'news': False, 'brief': "Ignore the headlines. Episode {n} of a numbered series explaining one AI concept (tokens, training, hallucinations, agents...). Say 'AI Explained, number {n}' early. Pick a concept a beginner needs next."}, {'name': 'I Asked AI', 'news': True, 'brief': "Pick a story people have questions about. Frame it as 'I asked an AI chatbot about this', give the typical AI answer, then fact-check it against the real story: what it got right and wrong."}, {'name': 'Future Flashback', 'news': True, 'brief': "Pretend it's 2030 looking back: 'This headline from 2026 changed everything because...'. Make clear it's a thought experiment, then return to what is actually true today."}], 1: [{'name': 'Password Tip', 'news': True, 'brief': 'Tie one quick security habit (password manager, passkeys, 2FA app) to a breach or hack in the news.{save}'}, {'name': 'Delete This App', 'news': False, 'brief': 'Ignore the headlines. Name one real type of app or a specific well-known app that drains battery or harvests data, what it collects, and a safer alternative. Only use well-documented facts.{save}'}, {'name': 'Tech Court', 'news': True, 'brief': "Put a company 'on trial' for a decision in the story: the charge, the case for, the case against, then a verdict."}, {'name': 'Villain Arc', 'news': True, 'brief': "Tell a company's bad week like a dramatic movie trailer ('In a world where...'), then the real facts and what it means for users."}], 2: [{'name': 'Mystery Gadget', 'news': True, 'brief': 'Describe a product from the story with 3 clues without naming it; reveal the name in the last third.'}, {'name': 'Guess the Startup', 'news': True, 'brief': 'Pick a story about a company. Give 3 clues about it without naming it, reveal it in the last third, then the news.'}, {'name': "Don't Watch This", 'news': False, 'brief': "Ignore the headlines. Anti-hook format: open with 'Don't watch this if you like slow Wi-Fi' (or similar), then deliver one genuinely useful hidden tip.{save}"}, {'name': 'Rate the Headline', 'news': True, 'brief': "Read a clickbait-style version of the story's headline, rate it 1 to 10 for accuracy, then explain what is actually true."}, {'name': 'Dueling Headlines', 'news': True, 'brief': 'Pick a story that more than one outlet below covers (or that could be spun two ways). Show how two framings differ and what the facts are.'}], 3: [{'name': "You're Using It Wrong", 'news': False, 'brief': 'Ignore the headlines. Show the better way to use one everyday feature (AirDrop, Ctrl+F, Google search operators, screenshots...).{save}'}, {'name': 'Free Alternative', 'news': False, 'brief': 'Ignore the headlines. Name one popular paid app and a real free alternative (e.g. Photoshop to Photopea), what it does well and its catch.{save}'}, {'name': 'Battery Saver', 'news': False, 'brief': 'Ignore the headlines. One real setting or habit that adds hours of phone or laptop battery, with the exact menu path.{save}'}, {'name': 'Hidden Easter Egg', 'news': False, 'brief': 'Ignore the headlines. Show one real hidden feature, game or easter egg inside a popular app or OS, and how to find it.{save}'}, {'name': 'Should You Upgrade', 'news': True, 'brief': "Pick a new phone, chip or OS story and walk through a yes/no checklist: 'If you have X, skip it. If you need Y, upgrade.'"}, {'name': 'Spec Translator', 'news': True, 'brief': 'Pick a product story and translate its most confusing spec (mAh, nits, TOPS, refresh rate) into real-life meaning.'}], 4: [{'name': 'Speedrun the News', 'news': True, 'brief': "Speedrun format: cover the 5 most interesting headlines below in one Short, one punchy sentence or two each, numbered 'One... Two...'. Hook: 'Five tech stories in 40 seconds, go.' Put the most important link in 'link'."}, {'name': 'Hot Take', 'news': True, 'brief': 'Give one bold but defensible opinion about the story, the strongest argument against it, then ask viewers to pick a side.'}, {'name': 'Leak Watch', 'news': True, 'brief': 'Pick a rumor or leak story. Say what is claimed, how credible the source is, and when we will know for sure.'}, {'name': 'Fail of the Week', 'news': True, 'brief': 'Pick a story about a product, update or launch that went wrong: what happened, why, and what users should do.'}, {'name': 'Billionaire Tech Moves', 'news': True, 'brief': "Pick a story about a big tech CEO or company leader's decision and explain what it means for ordinary users."}, {'name': 'Time Capsule', 'news': True, 'brief': "Make one specific, checkable prediction about this story for one year from now. Say 'Screenshot this, we'll check in a year.' Put the prediction itself in 'prediction'."}, {'name': 'Tech Bingo', 'news': True, 'brief': 'Weekly bingo: list 4 predictable tech-news things (a leak, a lawsuit, an AI launch, a delay) and tick off which happened this week, using the headlines.'}], 5: [{'name': 'Story Time', 'news': False, 'brief': 'Ignore the headlines. Tell the dramatic, true backstory of one famous tech moment (the first iPhone reveal, Y2K, the first website). Only use facts you are certain of.'}, {'name': 'How It Works', 'news': False, 'brief': 'Ignore the headlines. Explain how one everyday technology works (GPS, Wi-Fi, touchscreens, QR codes) in under 45 seconds with one comparison.'}, {'name': 'Old vs New', 'news': False, 'brief': 'Ignore the headlines. Compare one product category 10 years ago vs today (phones, storage, cameras) with 2-3 real numbers.'}, {'name': 'Tech Horoscope', 'news': False, 'brief': "Ignore the headlines. A fun, clearly-for-fun 'which gadget matches your star sign' for 4 signs, then ask viewers their sign in the comments."}], 6: [{'name': 'Head-to-Head', 'news': True, 'brief': 'Pick a product story and compare it head-to-head with its main rival in 3 rounds (price, one key feature, who it is for), naming a winner per round.'}, {'name': 'Tier List', 'news': True, 'brief': 'Rank 5 products or services related to the story from S tier to F tier, revealing one at a time, S last.'}, {'name': 'Price Drop Alert', 'news': True, 'brief': 'Pick a gadget whose price changed or a deal story: the old vs new price, and whether to buy now or wait.'}, {'name': 'Weirdest Tech', 'news': True, 'brief': 'Pick the strangest gadget, patent or experiment in the headlines (or a well-known real one): what it is and whether it could catch on.'}, {'name': 'Startup in 30 Seconds', 'news': True, 'brief': 'Pick a story about a young company: what it does, who it is for, and its biggest risk, in 30 seconds.'}]}
for _d, _fs in MORE_FORMATS.items():
    for _f in _fs:
        _f["brief"] = _f["brief"].replace("{save}", SAVE)
    SERIES[_d] += _fs
# A/B test: hook styles rotate by day so the analytics can compare them
HOOK_STYLES = ["a surprising question", "a bold one-line statement", "a single punchy word on screen (hook_card is ONE word like 'Banned.')",
               "a reverse hook: start with the ending ('...and that is why your phone is slower'), then rewind"]
CAPSULES = HERE / "time_capsules.json"       # Time Capsule predictions to revisit in a year
MILESTONE = HERE / "milestone.json"          # written by stats.py when a subscriber milestone is passed
EVENT_WORDS = ("keynote", "unpacked", "wwdc", "apple event", "launch event", "made by google", "google i/o", "dev day")
VIEWER_QS = HERE / "viewer_questions.json"   # written by stats.py
PART2 = HERE / "part2.json"                  # tomorrow's cliffhanger follow-up


def pick_series(now, headlines):
    """Today's format: a planned Part 2, year in review, live event coverage, a viewer question, or the weekday rotation."""
    if PART2.exists():
        plan = json.loads(PART2.read_text(encoding="utf-8"))
        PART2.unlink()
        if plan.get("date") == now.date().isoformat():
            n = plan.get("part", 2)
            return {"name": f"Part {n}", "news": False, "part": n, "brief": f"This is Part {n} of a multi-part story. Open with 'Part {n}:' "
                    f"and deliver exactly this promised follow-up: {plan['brief']}"}
    if MILESTONE.exists():
        m = json.loads(MILESTONE.read_text(encoding="utf-8"))
        MILESTONE.unlink()
        return {"name": "Milestone", "news": False, "brief": f"Ignore the headlines. The channel just passed {m['subs']:,} subscribers. "
                "Thank viewers warmly, share one quick behind-the-scenes fact about how the channel makes daily Shorts, "
                "and ask what topic they want next."}
    if CAPSULES.exists():
        caps = json.loads(CAPSULES.read_text(encoding="utf-8"))
        due = [c for c in caps if c["date"] <= (now.date() - dt.timedelta(days=365)).isoformat()]
        if due:
            CAPSULES.write_text(json.dumps([c for c in caps if c is not due[0]], indent=1), encoding="utf-8")
            return {"name": "Time Capsule Opened", "news": False, "brief": "Ignore the headlines. A year ago today we predicted: "
                    f"'{due[0]['prediction']}' (about: {due[0]['title']}). Say what actually happened if you know it reliably; "
                    "if you are not sure, say so honestly and ask viewers to say in the comments whether it came true."}
    if now.month == 12 and now.day >= 30:
        titles = [r["title"].replace("#shorts", "").strip() for r in read_runs() if r["date"][:4] == str(now.year) and r.get("video_id")]
        return {"name": "Year in Review", "news": False, "brief": f"Ignore the headlines. Count down the 5 biggest tech stories of {now.year}, "
                "choosing from our own Shorts this year: " + json.dumps(titles[-60:])}
    if any(w in (h["title"] + " " + h["summary"]).lower() for h in headlines for w in EVENT_WORDS):
        return {"name": "Event Coverage", "news": True, "brief": "A big tech launch event is happening. Cover the single most important "
                "announcement from it: what was announced, price and release date if given, and whether it matters."}
    if VIEWER_QS.exists() and now.weekday() == 5:   # Saturdays answer a viewer when someone asked
        qs = json.loads(VIEWER_QS.read_text(encoding="utf-8"))
        if qs:
            q = qs.pop(0)
            VIEWER_QS.write_text(json.dumps(qs), encoding="utf-8")
            return {"name": "Viewer Question", "news": False, "brief": f"Ignore the headlines. Answer this question a viewer named {q['author']} "
                    f"asked in our comments, and thank them by name in the first sentence: {q['question']}"}
    rota = SERIES[now.weekday()]
    pick = dict(rota[now.isocalendar().week % len(rota)])
    if "{n}" in pick["name"]:   # numbered series: AI Explained #1, #2...
        base = pick["name"].split("#")[0]
        n = sum(r["series"].startswith(base) for r in read_runs() if r.get("video_id")) + 1
        pick["name"], pick["brief"] = pick["name"].replace("{n}", str(n)), pick["brief"].replace("{n}", str(n))
    return pick


def season_brief(today):
    """Extra instruction for shopping seasons."""
    md = (today.month, today.day)
    if (11, 20) <= md <= (12, 2):
        return " It's Black Friday season: if it fits, mention how to spot a fake 'discount' or whether to wait for a deal."
    if (12, 3) <= md <= (12, 23):
        return " It's holiday gift season: if it fits, frame the takeaway as a gift idea and price range."
    if (8, 1) <= md <= (9, 10):
        return " It's back-to-school season: if it fits, frame the takeaway for students."
    return ""
TOPICS = {  # subtitle highlight colour (ASS is &HBBGGRR) and hashtags per topic
    "ai": ("&H00FFB000", "#AI #ArtificialIntelligence #TechNews"),
    "security": ("&H004040FF", "#CyberSecurity #Privacy #TechNews"),
    "gadgets": ("&H0000E5FF", "#Gadgets #Tech #TechNews"),
    "software": ("&H0066FF66", "#Software #Apps #TechNews"),
    "science": ("&H00FF66CC", "#Science #Innovation #TechNews"),
    "business": ("&H0000A5FF", "#TechBusiness #BigTech #TechNews"),
}
NUMBER_STYLE = "{\\c&H0055FF55&\\fscx118\\fscy118}"


def notify(title, text):
    """Phone push via ntfy.sh when NTFY_TOPIC is set, plus a Windows toast (best effort)."""
    if os.getenv("NTFY_TOPIC"):
        try:
            import httpx
            httpx.post(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}", content=text.encode("utf-8"), timeout=20,
                       headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": "movie_camera"})
        except Exception:
            pass
    if os.name != "nt":
        return
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
          "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
          "$t = $x.GetElementsByTagName('text'); $t.Item(0).AppendChild($x.CreateTextNode($env:TOAST_TITLE)) | Out-Null;"
          "$t.Item(1).AppendChild($x.CreateTextNode($env:TOAST_TEXT)) | Out-Null;"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('SubtleSpawns').Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        # text goes in through environment variables so PowerShell never runs it as code
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30,
                       env=os.environ | {"TOAST_TITLE": title, "TOAST_TEXT": text[:250]})
    except Exception:
        pass


def read_runs():
    if not RUNS.exists():
        return []
    with open(RUNS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_runs(rows):
    # merge with what's on disk so a run that started earlier (e.g. an app upload during the daily run) doesn't drop rows
    mine = {r["folder"] for r in rows}
    rows = sorted([r for r in read_runs() if r["folder"] not in mine] + list(rows), key=lambda r: r["folder"])
    with open(RUNS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RUN_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def cleanup_old(days=14):
    """Delete render folders older than two weeks so output/ doesn't fill the disk."""
    cutoff = time.time() - days * 86400
    for d in OUT_DIR.glob("20*") if OUT_DIR.exists() else []:
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def credit_check():
    if not os.getenv("OPENROUTER_API_KEY"):
        return
    import httpx
    try:
        d = httpx.get("https://openrouter.ai/api/v1/credits", timeout=20,
                      headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}).json()["data"]
        left = d["total_credits"] - d["total_usage"]
        print(f"OpenRouter credit left: ${left:.2f}")
        if left < float(os.getenv("MIN_CREDIT", "0.10")):
            os.environ["SKIP_PAID_LLM"] = "1"   # go straight to the free models instead of failing on each paid one
            print("Credit too low: using free models today.")
        if left < 0.25:
            notify("SubtleSpawns: low OpenRouter credit", f"${left:.2f} left. Gemini will write scripts until you top up.")
    except Exception as e:
        print(f"Credit check failed ({type(e).__name__})")


def login_age_warning():
    """Testing-mode Google apps lose their login after 7 days; warn before that happens."""
    if os.getenv("YT_APP_PUBLISHED") == "1" or not TOKEN_ISSUED.exists():
        return
    age = (dt.date.today() - dt.date.fromisoformat(TOKEN_ISSUED.read_text().strip())).days
    if age >= 6:
        msg = f"YouTube login is {age} days old and expires at 7. Publish the app in Google Cloud, or run make_short.py --upload once to sign in."
        print(msg)
        notify("SubtleSpawns: YouTube login expiring", msg)


def fetch_headlines(n=10, max_age_h=30):
    used = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    now = time.time()
    items = []
    for url in FEEDS:
        for e in feedparser.parse(url).entries[:6]:
            published = e.get("published_parsed") or e.get("updated_parsed")
            if published and now - calendar.timegm(published) > max_age_h * 3600:
                continue   # stale story
            if e.get("link") not in used and not re.search(r"(?i)\b(elections?|electoral|donat\w*|republicans?|democrats?|trump|senate|congress|box office|movies?)\b", e.title):
                summary = html.unescape(re.sub("<[^>]+>", "", e.get("summary", ""))).replace("[…]", "")[:400]
                items.append({"title": html.unescape(e.title), "link": e.link, "summary": summary})
    random.shuffle(items)   # mix sources so one feed doesn't dominate
    return items[:n]


def llm(prompt):
    """Text from Claude via OpenRouter, then free Gemini models. Returns None if all fail."""
    import httpx
    if os.getenv("OPENROUTER_API_KEY"):
        paid = [] if os.getenv("SKIP_PAID_LLM") else [os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-5.5"), "anthropic/claude-haiku-5.5"]
        for model in paid + ["google/gemma-4-31b-it:free"]:
            try:
                r = httpx.post("https://openrouter.ai/api/v1/chat/completions", timeout=120,
                               headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                               json={"model": model, "max_tokens": 10000, "reasoning": {"effort": "low"},
                                     "messages": [{"role": "user", "content": prompt}]})
                r.raise_for_status()
                text = r.json()["choices"][0]["message"].get("content") or ""
                if "{" in text:
                    print("Written by", model)
                    return text
                print(f"{model} gave an empty reply")
            except Exception as e:
                print(f"{model} failed ({getattr(getattr(e, 'response', None), 'status_code', type(e).__name__)})")
    if os.getenv("GEMINI_API_KEY"):
        # free backup; newest models are often overloaded, so try a few
        for model in ["gemini-flash-latest", "gemini-3-flash-preview", "gemini-3.1-flash-lite"]:
            try:
                r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                               headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, timeout=120,
                               json={"contents": [{"parts": [{"text": prompt}]}],
                                     "generationConfig": {"responseMimeType": "application/json"}})
                r.raise_for_status()
                text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
                print("Written by", model)
                return text
            except Exception as e:
                print(f"{model} failed ({getattr(getattr(e, 'response', None), 'status_code', type(e).__name__)})")
    return None


def parse_json(text):
    try:
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except (ValueError, AttributeError):
        return None


def channel_memory():
    """Recent titles (avoid repeats, allow follow-ups) and best performers (learn what works)."""
    recent = [r["title"] for r in read_runs()[-10:]]
    best = []
    if STATS.exists():
        with open(STATS, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("views", "").isdigit()]
        if len(rows) >= 5:
            best = [r["title"] for r in sorted(rows, key=lambda r: -int(r["views"]))[:5]]
    return recent, best


def pick_hook(today):
    """Rotate hook styles; once each has 3+ public Shorts, use the best one every other day."""
    hook = HOOK_STYLES[today.toordinal() % len(HOOK_STYLES)]
    if today.toordinal() % 2 or not STATS.exists():
        return hook
    with open(STATS, newline="", encoding="utf-8") as f:
        views = {}
        for r in csv.DictReader(f):
            if r.get("privacy") == "public" and r.get("hook"):
                views.setdefault(r["hook"], []).append(int(r["views"] or 0))
    short = {h.split(" (")[0].split(":")[0]: h for h in HOOK_STYLES}
    if all(len(views.get(k, [])) >= 3 for k in short):
        best = max(short, key=lambda k: sum(views[k]) / len(views[k]))
        print("Hook: using the best performer so far:", best)
        return short[best]
    return hook


def write_script(headlines, series):
    recent, best = channel_memory()
    today = dt.date.today()
    brief = series["brief"].replace("{today}", today.strftime("%B %d")) + season_brief(today)
    series["hook"] = pick_hook(today)
    if today.toordinal() % 4 == 0:   # now and then, tie the story to the parkour gameplay on screen
        brief += " Twist: work in one light metaphor linking the story to the parkour run on screen (a risky jump, a near miss, a speedrun)."
    prompt = f"""You write YouTube Shorts for SubtleSpawns, a tech news & tips channel. The host is "Spawn".
Tone: professional yet engaging, for a tech-savvy audience. No emojis in the script.
TODAY'S FORMAT: "{series['name']}". {brief}
The format above overrides the default structure below wherever they differ: follow it exactly, and make the title and hook show the format.
Unless the format says to ignore the headlines, pick the ONE most fitting technology story below (gadgets, AI, software, security, science tech). Never pick politics, elections, donations, celebrities or movies.
Write a 40-50 second voiceover (110-130 words):
- First sentence is a scroll-stopping hook, max 12 words. Today's hook style: {series['hook']}.
- Then explain what happened and why it matters, plain language.
- End with one practical tip or takeaway, then "Follow Spawn for daily tech news", phrased so it flows naturally back into the first sentence when the video loops.
Also write SEO metadata.
{"Recent Shorts (don't repeat them; a 'Part 2' is fine only if a headline is a genuinely new development): " + json.dumps(recent) if recent else ""}
{"Our best-performing titles so far (match their style): " + json.dumps(best) if best else ""}

Headlines:
{json.dumps(headlines, indent=1)}

Reply with ONLY JSON:
{{"link": "the chosen headline's link, or empty if not based on a headline", "hook_card": "3-6 word on-screen hook", "script": "...",
"title": "under 70 chars, keyword first, ends with #shorts",
"description": "2-3 keyword-rich sentences, then a source line, then 5 hashtags",
"tags": ["10-15 search keywords"],
"topic": "one of: ai, security, gadgets, software, science, business",
"mood": "one of: tense, upbeat, calm",
"comment_question": "a quick poll to pin as the first comment, answerable in one word or emoji, e.g. 'Would you buy this? Yes or no' or 'Did you know this? Yes / No'",
"prediction": "only for the Time Capsule format: the one-sentence prediction; else empty",
"part2": "only if the story is genuinely too big for one Short (a deep dive can run to Part 3): one sentence on what Part 2 tomorrow will cover (then end the script with 'Part 2 tomorrow.' instead of the loop line); otherwise empty",
"visuals": [{{"word": "one key word copied exactly from the script", "query": "2-4 word photo search for it", "wiki": "exact English Wikipedia article title if it is a specific company, product, person or place, else empty"}}]}}
For "visuals" give 10-14 items, roughly one every 3 seconds: the main nouns (companies, products, people, objects, places), in script order, never in the first sentence.
"query" must describe a concrete, photographable scene, never an abstract idea: for "booking" use "person booking flight on laptop", for "privacy" use "padlock on laptop keyboard", for "settings" use "smartphone settings screen"."""
    meta = parse_json(llm(prompt))
    if meta:
        return meta
    print("No working AI - using a basic template script.")
    h = headlines[0]
    return {"link": h["link"], "hook_card": "Tech news in 40 seconds", "_template": True,
            "script": f"Here's the tech story everyone's talking about. {h['title']}. {h['summary'][:500]} Follow Spawn for daily tech news.",
            "title": (h['title'] if len(h['title']) <= 60 else h['title'][:60].rsplit(' ', 1)[0]) + " #shorts",
            "description": f"{h['title']}\nSource: {h['link']}", "tags": ["tech news", "technology", "shorts"]}


def fact_check(meta, headlines):
    """Second AI pass: compare the script with the source summary and fix unsupported claims."""
    src = next((h for h in headlines if h["link"] == meta.get("link")), None)
    if not src or meta.get("_template"):
        return meta
    out = parse_json(llm(f"""Fact-check this YouTube Short script against its source.
Source headline: {src['title']}
Source summary: {src['summary']}

Script: {meta['script']}

Flag any specific claim (number, name, date, capability) that the source does not support or contradicts.
General background knowledge and the closing tip are fine. Reply with ONLY JSON:
{{"ok": true or false, "problems": "short list, or empty", "script": "if not ok, the corrected script in the same style and length; else empty"}}"""))
    if out and out.get("ok") is False and out.get("script"):
        print("Fact-check fixed:", out.get("problems"))
        meta["script"] = out["script"]
    elif out:
        print("Fact-check: ok")
    return meta


def clean_meta(meta, headlines, news):
    """Fill missing fields and pin the link to a real headline so it's remembered as used."""
    if not meta.get("script"):
        raise ValueError("AI reply has no script")
    news = news or meta.get("_template")
    links = {h["link"] for h in headlines}
    if not news:
        meta["link"] = ""
    elif meta.get("link") not in links and headlines:
        said = set(re.findall(r"\w+", (meta.get("title", "") + " " + meta["script"]).lower()))
        best = max(headlines, key=lambda h: len(said & set(re.findall(r"\w+", h["title"].lower()))))
        meta["link"] = best["link"]
    # braces/backslashes are styling commands in subtitles, so strip them from on-screen text
    for k in ("script", "hook_card"):
        if meta.get(k):
            meta[k] = re.sub(r"[{}\\]", "", meta[k])
    title = meta.get("title") or meta["script"].split(".")[0][:60]
    meta["title"] = title if "#shorts" in title.lower() else title + " #shorts"
    meta.setdefault("hook_card", "Tech news in 40 seconds")
    meta.setdefault("description", meta["title"] + (f"\nSource: {meta['link']}" if meta["link"] else ""))
    if not isinstance(meta.get("tags"), list):
        meta["tags"] = ["tech news", "technology", "shorts"]
    meta["topic"] = meta.get("topic") if meta.get("topic") in TOPICS else "gadgets"
    meta["mood"] = meta.get("mood") if meta.get("mood") in ("tense", "upbeat", "calm") else "upbeat"
    meta.setdefault("comment_question", "What do you think? Tell me below.")
    missing = [t for t in (TOPICS[meta["topic"]][1] + " #Shorts").split() if t.lower() not in meta["description"].lower()]
    if missing:
        meta["description"] += "\n" + " ".join(missing)
    meta["visuals"] = [v for v in meta.get("visuals") or [] if isinstance(v, dict) and v.get("word") and v.get("query")]
    return meta


async def tts(text, mp3):
    comm = edge_tts.Communicate(text, VOICE, rate="+12%", boundary="WordBoundary")
    words = []
    with open(mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append((start, start + chunk["duration"] / 1e7, chunk["text"]))
    return words


WIN_TTS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$v = $s.GetInstalledVoices() | % { $_.VoiceInfo.Name } | ? { $_ -match 'Zira|Aria|Jenny|Guy|David' } | Select -First 1
if ($v) { $s.SelectVoice($v) }
$s.Rate = 1
Register-ObjectEvent $s SpeakProgress -SourceIdentifier P | Out-Null
Register-ObjectEvent $s SpeakCompleted -SourceIdentifier C | Out-Null
$s.SetOutputToWaveFile($args[1])
[void]$s.SpeakAsync([IO.File]::ReadAllText($args[0]))
$out = @()
while ($true) {
  $e = Wait-Event -Timeout 120
  if (-not $e) { break }
  Remove-Event -EventIdentifier $e.EventIdentifier
  if ($e.SourceIdentifier -eq 'C') { break }
  $out += ,@($e.SourceEventArgs.AudioPosition.TotalSeconds, $e.SourceEventArgs.Text)
}
$s.Dispose()
ConvertTo-Json -InputObject $out -Compress
exit 0
"""


def tts_windows(text, wav):
    """Offline fallback: built-in Windows voice, word timings from SpeakProgress."""
    txt, ps1 = wav.with_suffix(".txt"), wav.with_suffix(".ps1")
    txt.write_text(text, encoding="utf-8")
    ps1.write_text(WIN_TTS, encoding="utf-8")
    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), str(txt), str(wav)],
                         capture_output=True, text=True).stdout  # exit code is unreliable here; check output instead
    if not out.strip() or not wav.exists():
        raise RuntimeError("Windows voice failed")
    raw = json.loads(out.strip().splitlines()[-1])
    return [(float(t), raw[i + 1][0] if i + 1 < len(raw) else float(t) + 0.4, w) for i, (t, w) in enumerate(raw)]


def tts_elevenlabs(text, mp3):
    """ElevenLabs voice; word timings rebuilt from its per-character alignment."""
    import base64, httpx
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{os.environ['ELEVENLABS_VOICE_ID']}/with-timestamps",
                   headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=180,
                   json={"text": text, "model_id": os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")})
    r.raise_for_status()
    d = r.json()
    mp3.write_bytes(base64.b64decode(d["audio_base64"]))
    a = d["alignment"]
    words, cur, start = [], "", None
    for ch, s0, e0 in zip(a["characters"], a["character_start_times_seconds"], a["character_end_times_seconds"]):
        if ch.isspace():
            if cur:
                words.append((start, end, cur))
            cur, start = "", None
        else:
            if start is None:
                start = s0
            cur, end = cur + ch, e0
    if cur:
        words.append((start, end, cur))
    return words


def elevenlabs_left():
    """Characters left this month on ElevenLabs (None if unknown)."""
    import httpx
    try:
        d = httpx.get("https://api.elevenlabs.io/v1/user/subscription", timeout=20,
                      headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}).json()
        return d["character_limit"] - d["character_count"]
    except Exception:
        return None


def voice_over(script, work):
    voice = work / "voice.mp3"
    try:
        left = elevenlabs_left() if os.getenv("ELEVENLABS_API_KEY") else None
        if left is not None and left < len(script):
            print(f"ElevenLabs has {left} characters left (need {len(script)}); using the free voice.")
            notify("SubtleSpawns: ElevenLabs used up", f"{left} characters left this month. Using the free voice until it resets.")
        elif os.getenv("ELEVENLABS_API_KEY") and os.getenv("ELEVENLABS_VOICE_ID"):
            try:
                words = tts_elevenlabs(script, voice)
                return voice, words, "elevenlabs"
            except Exception as e:
                print(f"ElevenLabs failed ({e}); trying free voice.")
        return voice, asyncio.run(tts(script, voice)), "edge"
    except Exception as e:
        print(f"Edge voice unavailable ({type(e).__name__}); using built-in Windows voice.")
        voice = work / "voice.wav"
        return voice, tts_windows(script, voice), "windows"


def norm(w):
    return re.sub(r"\W", "", w.lower())


def wiki_image(title):
    """Freely licensed lead image of a Wikipedia article (logos, products, people) plus its credit."""
    import httpx
    base = {"action": "query", "prop": "pageimages", "piprop": "thumbnail|name", "pithumbsize": 900,
            "pilicense": "free", "redirects": 1, "format": "json"}
    for extra in ({"titles": title}, {"generator": "search", "gsrsearch": title, "gsrlimit": 1}):
        pages = httpx.get("https://en.wikipedia.org/w/api.php", params=base | extra, headers=UA, timeout=30
                          ).json().get("query", {}).get("pages", {})
        page = next((p for p in pages.values() if p.get("thumbnail")), None)
        if page:
            break
    else:
        return None
    meta = httpx.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=30, params={
        "action": "query", "titles": "File:" + page["pageimage"], "prop": "imageinfo", "iiprop": "extmetadata", "format": "json"}
        ).json()["query"]["pages"]
    ext = next(iter(meta.values())).get("imageinfo", [{}])[0].get("extmetadata", {})
    artist = re.sub("<[^>]+>", "", ext.get("Artist", {}).get("value", "")).strip() or "Wikimedia Commons"
    lic = ext.get("LicenseShortName", {}).get("value", "free license")
    return {"url": page["thumbnail"]["source"], "credit": f"\"{page['pageimage']}\" by {artist} ({lic}) "
            f"https://commons.wikimedia.org/wiki/File:{page['pageimage'].replace(' ', '_')}"}


AI_IMAGES_OK = [True]
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "google/gemini-3.1-flash-lite-image")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
IMAGE_PROMPT = "Photorealistic editorial tech-news image, vivid lighting, no text, no logos, no watermarks: "


def ai_image(query, path):
    """AI-generated picture for generic words (no logos or text): Gemini API first, then OpenRouter."""
    import base64, httpx
    if os.getenv("GEMINI_API_KEY"):
        try:
            r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent",
                           headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, timeout=120,
                           json={"contents": [{"parts": [{"text": IMAGE_PROMPT + query}]}],
                                 "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "4:3"}}})
            r.raise_for_status()
            part = next(p for p in r.json()["candidates"][0]["content"]["parts"] if "inlineData" in p)
            path.write_bytes(base64.b64decode(part["inlineData"]["data"]))
            return
        except Exception as e:
            print(f"Gemini image failed ({getattr(getattr(e, 'response', None), 'status_code', type(e).__name__)}); trying OpenRouter")
    if not os.getenv("OPENROUTER_API_KEY"):
        raise RuntimeError("no image key")
    r = httpx.post("https://openrouter.ai/api/v1/chat/completions", timeout=120,
                   headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                   json={"model": IMAGE_MODEL, "modalities": ["image", "text"], "image_config": {"aspect_ratio": "4:3"},
                         "messages": [{"role": "user", "content": IMAGE_PROMPT + query}]})
    r.raise_for_status()
    url = r.json()["choices"][0]["message"]["images"][0]["image_url"]["url"]
    path.write_bytes(base64.b64decode(url.split(",", 1)[1]))


def commons_search(query, limit=3):
    """Keyless photo search on Wikimedia Commons (every file there is free for commercial use)."""
    import httpx
    pages = httpx.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=30, params={
        "action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": f"filetype:bitmap {query}",
        "gsrlimit": 6, "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 900, "format": "json"}
        ).json().get("query", {}).get("pages", {})
    out = []
    for p in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        info = (p.get("imageinfo") or [{}])[0]
        ext = info.get("extmetadata", {})
        if not info.get("thumburl") or re.search(r"(?i)logo|map|diagram|chart|flag", p["title"]):
            continue
        artist = re.sub("<[^>]+>", "", ext.get("Artist", {}).get("value", "")).strip() or "Wikimedia Commons"
        out.append({"url": info["thumburl"], "credit": f"\"{p['title'][5:]}\" by {artist} "
                    f"({ext.get('LicenseShortName', {}).get('value', 'free license')}) {info.get('descriptionurl', '')}"})
    return out[:limit]


def photo_candidates(v):
    """Photos for one key word, best source first; generated lazily so we only search as far as needed."""
    import httpx
    if v.get("wiki"):
        try:
            p = wiki_image(v["wiki"])
            if p:
                yield p, "Wikipedia"
        except Exception as e:
            print(f"Wikipedia lookup failed for '{v['wiki']}' ({type(e).__name__})")
    if os.getenv("PEXELS_API_KEY"):
        try:
            for ph in httpx.get("https://api.pexels.com/v1/search", headers={"Authorization": os.environ["PEXELS_API_KEY"]},
                                params={"query": v["query"], "per_page": 3, "orientation": "landscape"}, timeout=30
                                ).raise_for_status().json()["photos"]:
                yield {"url": ph["src"]["large"], "credit": f"Photo by {ph['photographer']} on Pexels {ph['url']}"}, "Pexels"
        except Exception as e:
            print(f"Pexels failed for '{v['word']}' ({type(e).__name__})")
    try:
        for p in commons_search(v["query"]):
            yield p, "Commons"
    except Exception as e:
        print(f"Commons search failed for '{v['word']}' ({type(e).__name__})")
    for q in dict.fromkeys([v["query"], v["word"]]):
        try:
            res = httpx.get("https://api.openverse.org/v1/images/", headers=UA, timeout=30,
                            params={"q": q, "license_type": "commercial", "page_size": 10, "category": "photograph",
                                    "aspect_ratio": "wide", "size": "large", "mature": "false"}).json()["results"]
        except Exception:
            continue
        for r in res:
            if r["url"].lower().split("?")[0].endswith((".jpg", ".jpeg", ".png")):
                yield ({"url": r["url"], "credit": f"\"{r.get('title') or 'Photo'}\" by {r.get('creator') or 'unknown'} "
                        f"({r['license'].upper()} {r.get('license_version') or ''}) {r.get('foreign_landing_url') or r['url']}"}, "Openverse")


def download(url, path):
    import httpx
    for attempt in range(3):   # Wikimedia rate-limits quick bursts; wait and retry
        resp = httpx.get(url, headers=UA, timeout=30, follow_redirects=True)
        if resp.status_code != 429:
            break
        time.sleep(2 + 3 * attempt)
    if not resp.raise_for_status().headers.get("content-type", "").startswith("image/"):
        raise ValueError("not an image")   # e.g. a 'photo removed' web page; ffmpeg would crash on it
    path.write_bytes(resp.content)


def fetch_images(visuals, words, work, total):
    """Photos shown when their word is spoken: Wikipedia for names, AI for generic words, then photo libraries."""
    shown, last_end = [], 3.4          # stay clear of the opening hook card
    for n, v in enumerate(visuals):
        target = norm(v["word"].split()[0])
        t = next((s for s, _, w in words if norm(w) == target and s >= last_end), None)
        if t is None or t + 1 > total:
            continue
        end = min(t + 1.8, total)
        if not v.get("wiki") and AI_IMAGES_OK[0] and (os.getenv("GEMINI_API_KEY") or os.getenv("OPENROUTER_API_KEY")):
            img = work / f"img{n}.png"
            try:
                ai_image(v["query"], img)
                print(f"AI image for '{v['word']}'")
                shown.append((img, t, end, None))
                last_end = end + 0.1
                continue
            except Exception as e:
                print(f"AI image failed for '{v['word']}' ({e.__class__.__name__}); using photo libraries")
                AI_IMAGES_OK[0] = False   # no quota/credit: don't retry for every word this run
        got = None
        # if a download fails, move on to the next candidate instead of dropping the word
        for tries, (pick, source) in enumerate(photo_candidates(v)):
            if tries >= 4:
                break
            img = work / f"img{n}{Path(pick['url'].split('?')[0]).suffix.lower() or '.jpg'}"
            try:
                download(pick["url"], img)
                got = (img, t, end, pick["credit"])
                print(f"{source} image for '{v['word']}'")
                break
            except Exception as e:
                print(f"{source} download failed for '{v['word']}' ({getattr(getattr(e, 'response', None), 'status_code', type(e).__name__)})")
        if got:
            shown.append(got)
            last_end = end + 0.1
        else:
            print(f"No image for '{v['word']}'")
        time.sleep(0.8)   # space out requests so Wikimedia doesn't rate-limit us
    print(f"Images: {len(shown)}")
    return shown


def ass_time(t):
    t = round(t, 2)  # round first so 59.996 can't print as "60.00"
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def make_subs(words, hook, path, total, highlight="&H0000E5FF", badge=""):
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial Black,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,3,5,80,80,0,1
Style: Badge,Arial Black,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00E07A2A,1,0,0,0,100,100,2,0,3,10,0,8,60,60,150,1
Style: Hook,Arial Black,72,&H0000E5FF,&H00FFFFFF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,3,18,0,8,60,60,260,1
Style: Brand,Arial,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,2,40,40,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 1,{ass_time(0)},{ass_time(3.2)},Hook,,0,0,0,,{{\\fad(150,250)\\t(0,200,\\fscx110\\fscy110)\\t(200,400,\\fscx100\\fscy100)}}{hook.upper()}",
             f"Dialogue: 0,{ass_time(0)},{ass_time(total)},Brand,,0,0,0,,@SubtleSpawns"]
    if badge:   # series label so regular viewers recognise the format
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(3.2)},Badge,,0,0,0,,{{\\fad(150,250)}}{badge.upper()}")

    def styled(w, current):
        if re.search(r"\d", w):   # prices and stats pop in green, slightly bigger
            return NUMBER_STYLE + w + "{\\c&H00FFFFFF&\\fscx100\\fscy100}"
        return ("{\\c" + highlight + "&}" + w + "{\\c&H00FFFFFF&}") if current else w

    # 3-word chunks, current word highlighted in the topic colour, chunk pops in
    for i in range(0, len(words), 3):
        chunk = words[i:i + 3]
        for j, (s, e, _) in enumerate(chunk):
            end = chunk[j + 1][0] if j + 1 < len(chunk) else (words[i + 3][0] if i + 3 < len(words) else e + 0.4)
            txt = " ".join(styled(w, k == j) for k, (_, _, w) in enumerate(chunk))
            pop = "{\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}" if j == 0 else ""
            lines.append(f"Dialogue: 0,{ass_time(s)},{ass_time(end)},Cap,,0,0,0,,{pop}{txt.upper()}")
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


def media_duration(path):
    out = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def whoosh_sound():
    """A soft whoosh made from filtered noise, generated once."""
    path = HERE / "assets" / "whoosh.wav"
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=0.4:c=pink:a=0.5",
                        "-af", "highpass=f=500,lowpass=f=6000,afade=t=in:d=0.15,afade=t=out:st=0.15:d=0.25",
                        "-ar", "44100", "-ac", "2", str(path)], check=True)
    return path


def pick_music(mood):
    if not MUSIC_DIR.exists():
        return None
    tracks = sorted((MUSIC_DIR / mood).glob("*.mp3")) or sorted(MUSIC_DIR.glob("*.mp3"))
    return random.choice(tracks) if tracks else None


def render(voice_mp3, subs, total, out_mp4, images=(), mood="upbeat"):
    # the cloud run only has the short clips in backgrounds/, not the 6-hour file
    bgs = [p for p in [BG_VIDEO] + (sorted(BG_DIR.glob("*.mp4")) if BG_DIR.exists() else []) if p.exists()]
    if os.getenv("BG_PICK") and (BG_DIR / os.environ["BG_PICK"]).exists():   # chosen in the Studio
        bgs = [BG_DIR / os.environ["BG_PICK"]]
    if not bgs:
        raise FileNotFoundError(f"No background video: set BG_VIDEO or put .mp4 clips in {BG_DIR}")
    bg = random.choice(bgs)
    speed = float(os.getenv("BG_SPEED", "1.5"))   # play the gameplay faster than real time
    start = random.uniform(5, max(5.0, media_duration(bg) - total * speed - 5))
    cmd = [FFMPEG, "-y", "-ss", f"{start:.1f}", "-t", f"{total * speed:.2f}", "-i", str(bg), "-i", str(voice_mp3)]
    vf = (f"[0:v]setpts=PTS/{speed},crop=ih*9/16:ih,scale=1080:1920,eq=brightness=-0.06:saturation=1.15,"
          # slow push-in keeps the frame moving
          "zoompan=z='min(1+0.0006*on,1.15)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=1080x1920:fps=30[bg]")
    last = "bg"
    for k, (img, s0, e0, _) in enumerate(images):
        cmd += ["-loop", "1", "-framerate", "30", "-t", f"{total:.2f}", "-i", str(img)]
        f0 = int(s0 * 30)
        # framed photo in the upper half with a slow Ken Burns zoom, fading in and out above the captions
        vf += (f";[{2 + k}:v]scale=800:500:force_original_aspect_ratio=decrease,pad=800:500:(ow-iw)/2:(oh-ih)/2:white,"
               f"zoompan=z='min(1+0.0016*max(in-{f0},0),1.08)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=800x500:fps=30,"
               f"pad=820:520:10:10:white,format=rgba,fade=in:st={s0:.2f}:d=0.15:alpha=1,fade=out:st={e0 - 0.15:.2f}:d=0.15:alpha=1[i{k}]"
               f";[{last}][i{k}]overlay=x=(W-w)/2:y=290:enable='between(t,{s0:.2f},{e0:.2f})'[o{k}]")
        last = f"o{k}"
    # progress bar along the bottom edge
    vf += (f";color=c=0xFFE500:s=1080x12:r=30:d={total:.2f}[pb];"
           f"[{last}][pb]overlay=x='-w+w*t/{total:.2f}':y=H-h[pbo];[pbo]ass={subs.name}[v]")
    idx = 2 + len(images)
    mix, af = ["[vo]"], []
    music = pick_music(mood)
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        # music ducks under the voice and comes back up in pauses
        af.append(f"[1:a]asplit=2[vo][sc];[{idx}:a]volume=0.3[mraw];"
                  "[mraw][sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=350[md]")
        mix.append("[md]")
        idx += 1
    else:
        af.append("[1:a]anull[vo]")
    if images:
        cmd += ["-i", str(whoosh_sound())]
        n = len(images)
        af.append(f"[{idx}:a]" + (f"asplit={n}" + "".join(f"[w{k}]" for k in range(n)) if n > 1 else "anull[w0]"))
        for k, (_, s0, _, _) in enumerate(images):
            ms = max(0, int((s0 - 0.12) * 1000))
            af.append(f"[w{k}]adelay=delays={ms}:all=1,volume=0.45[wd{k}]")
            mix.append(f"[wd{k}]")
    af.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0[a]")
    cmd += ["-filter_complex", vf + ";" + ";".join(af), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out_mp4)]
    subprocess.run(cmd, check=True, cwd=subs.parent)  # cwd so the ass filter path needs no escaping


def get_youtube():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    creds = None
    if TOKEN.exists():
        saved = json.loads(TOKEN.read_text())
        if set(SCOPES) <= set(saved.get("scopes") or []):   # older logins only had upload access
            creds = Credentials.from_authorized_user_info(saved, SCOPES)
    if not creds or not creds.valid:
        try:
            if not (creds and creds.expired and creds.refresh_token):
                raise RefreshError("no saved login with the needed permissions")
            creds.refresh(Request())
        except RefreshError as e:
            # Testing-mode apps lose their login after 7 days; sign in again
            if os.getenv("CI"):   # GitHub Actions can't open a browser; a fresh token.json secret is needed
                raise RuntimeError(f"YouTube sign-in expired ({e}). Sign in on your PC, then update the TOKEN_JSON secret.")
            print(f"YouTube sign-in needed ({e}); opening browser.")
            creds = InstalledAppFlow.from_client_secrets_file(HERE / "client_secret.json", SCOPES).run_local_server(port=0)
            TOKEN_ISSUED.write_text(dt.date.today().isoformat())
        TOKEN.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def add_to_playlist(yt, video_id, topic):
    name = f"SubtleSpawns: {topic.title() if topic != 'ai' else 'AI'} News"
    lists = yt.playlists().list(part="snippet", mine=True, maxResults=50).execute().get("items", [])
    pid = next((p["id"] for p in lists if p["snippet"]["title"] == name), None)
    if not pid:
        pid = yt.playlists().insert(part="snippet,status", body={
            "snippet": {"title": name, "description": f"Daily {topic} tech news Shorts from SubtleSpawns."},
            "status": {"privacyStatus": "public"}}).execute()["id"]
    yt.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": pid,
                              "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
    print("Added to playlist:", name)


def upload(mp4, meta):
    from googleapiclient.http import MediaFileUpload
    yt = get_youtube()
    body = {"snippet": {"title": meta["title"][:100], "description": meta["description"], "tags": meta["tags"],
                        "categoryId": "28"},  # Science & Technology
            "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}
    res = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(mp4), resumable=True)).execute()
    print(f"Uploaded: https://youtube.com/shorts/{res['id']} ({PRIVACY})")
    try:
        set_thumbnail(yt, res["id"], cover_frame(mp4, Path(mp4).with_name("cover.jpg")))
    except Exception as e:
        print(f"Cover frame failed ({type(e).__name__})")
    try:
        add_to_playlist(yt, res["id"], meta.get("topic", "gadgets"))
    except Exception as e:
        print(f"Playlist add failed ({type(e).__name__})")
    return res["id"]


def delete_local(mp4):
    """Once it's on YouTube, free the disk (the cloud keeps its copies for the weekly and monthly recaps)."""
    if not os.getenv("CI"):
        Path(mp4).unlink(missing_ok=True)
        for copy in READY_DIR.glob(f"{Path(mp4).parent.name} *.mp4"):   # the TikTok / Reels copy (Drive keeps its own)
            copy.unlink(missing_ok=True)
        print("Deleted the local copies:", mp4)


def upload_latest(folder=None):
    """Review mode: upload the newest rendered Short that hasn't been uploaded (or a specific folder)."""
    rows = read_runs()
    row = next((r for r in reversed(rows) if not r.get("video_id") and (OUT_DIR / r["folder"] / "short.mp4").exists()
                and (r["folder"] == folder if folder else r.get("privacy") in ("review", "pending"))), None)
    if not row:
        sys.exit("Nothing waiting for upload.")
    work = OUT_DIR / row["folder"]
    row["video_id"] = upload(work / "short.mp4", json.loads((work / "meta.json").read_text(encoding="utf-8")))
    row["privacy"] = PRIVACY
    write_runs(rows)
    delete_local(work / "short.mp4")


def cover_frame(mp4, jpg):
    """Most eye-catching frame from the first 3 seconds (badge and hook on screen) as the cover image."""
    subprocess.run([FFMPEG, "-y", "-t", "3", "-i", str(mp4), "-vf", "thumbnail=60", "-frames:v", "1", "-q:v", "2", str(jpg)],
                   check=True, capture_output=True)
    return jpg


def set_thumbnail(yt, video_id, jpg):
    from googleapiclient.http import MediaFileUpload
    try:
        yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(jpg))).execute()
        print("Cover image set")
    except Exception as e:   # needs a phone-verified channel
        print(f"Cover image not set ({type(e).__name__})")


DRIVE_TOKEN = HERE / "drive_token.json"


def drive_login():
    """One-time: allow uploads to your Google Drive (only to files this app creates)."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    creds = InstalledAppFlow.from_client_secrets_file(HERE / "client_secret.json",
                                                      ["https://www.googleapis.com/auth/drive.file"]).run_local_server(port=0)
    DRIVE_TOKEN.write_text(creds.to_json())
    print(f"Saved {DRIVE_TOKEN.name}. For the cloud, paste it into a DRIVE_TOKEN_JSON secret.")


def drive_drop(files):
    """Upload to the 'SubtleSpawns Ready' Drive folder, which Repurpose.io can watch as a source."""
    if not DRIVE_TOKEN.exists():
        return
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    try:
        creds = Credentials.from_authorized_user_info(json.loads(DRIVE_TOKEN.read_text()))
        if not creds.valid:
            creds.refresh(Request())
        drive = build("drive", "v3", credentials=creds)
        name = os.getenv("DRIVE_FOLDER", "SubtleSpawns Ready")
        found = drive.files().list(q=f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                                   fields="files(id)").execute()["files"]
        folder = found[0]["id"] if found else drive.files().create(
            body={"name": name, "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()["id"]
        for f in files:
            drive.files().create(body={"name": f.name, "parents": [folder]}, media_body=MediaFileUpload(str(f), resumable=True)).execute()
        print(f"Copied to Google Drive folder '{name}'")
    except Exception as e:
        print(f"Drive copy failed ({type(e).__name__}: {e})")


def save_ready_copy(mp4, meta, stamp):
    """Copy + caption + cover for TikTok / Instagram Reels, also dropped in Google Drive when set up."""
    READY_DIR.mkdir(exist_ok=True)
    name = re.sub(r"[^\w\- ]", "", meta["title"].replace("#shorts", "")).strip()[:60]
    shutil.copy2(mp4, READY_DIR / f"{stamp} {name}.mp4")
    try:
        cover_frame(mp4, READY_DIR / f"{stamp} {name}.jpg")
    except Exception as e:
        print(f"Cover frame failed ({type(e).__name__})")
    title = meta["title"].replace("#shorts", "").strip()
    tags = " ".join("#" + re.sub(r"\W", "", t) for t in meta.get("tags", [])[:5])
    (READY_DIR / f"{stamp} {name}.txt").write_text(
        f"TIKTOK\n{title} {tags} #fyp #technews #learnontiktok\n\n"
        f"INSTAGRAM REELS\n{title}\n\n{meta['description'].split(chr(10))[0]}\n\n{tags} #reels #technews #techtips\n\n"
        f"FULL DESCRIPTION\n{meta['description']}", encoding="utf-8")
    drive_drop([f for f in (READY_DIR / f"{stamp} {name}.mp4", READY_DIR / f"{stamp} {name}.txt", READY_DIR / f"{stamp} {name}.jpg")
                if f.exists()])


def make_short(do_upload):
    cleanup_old()
    credit_check()
    login_age_warning()
    now = dt.datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H%M")
    work = OUT_DIR / stamp
    work.mkdir(parents=True, exist_ok=True)
    headlines = fetch_headlines()
    if not headlines:
        sys.exit("No new headlines found.")
    series = pick_series(now, headlines)
    print("Series:", series["name"])
    meta = clean_meta(write_script(headlines, series), headlines, series["news"])
    if meta["link"]:
        meta = clean_meta(fact_check(meta, headlines), headlines, True)
    (work / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("Title:", meta["title"])

    voice, words, voice_name = voice_over(meta["script"], work)
    print("Voice:", voice_name)
    total = min(words[-1][1] + 0.8, 59.0)
    make_subs(words, meta["hook_card"], work / "subs.ass", total, TOPICS[meta["topic"]][0], series["name"])
    images = fetch_images(meta["visuals"], words, work, total)
    if any(c for *_, c in images):
        meta["description"] += "\n\nImages:\n" + "\n".join(c for *_, c in images if c)
    (work / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    mp4 = work / "short.mp4"
    render(voice, work / "subs.ass", total, mp4, images, meta["mood"])
    print("Rendered:", mp4)
    save_ready_copy(mp4, meta, stamp)

    part = series.get("part", 1) + 1
    if meta.get("part2") and part <= 3:   # up to a three-part deep dive
        PART2.write_text(json.dumps({"date": (now.date() + dt.timedelta(days=1)).isoformat(), "brief": meta["part2"], "part": part}),
                         encoding="utf-8")
        print(f"Part {part} planned for tomorrow:", meta["part2"])
    if meta.get("prediction") and series["name"] == "Time Capsule":
        caps = json.loads(CAPSULES.read_text(encoding="utf-8")) if CAPSULES.exists() else []
        CAPSULES.write_text(json.dumps(caps + [{"date": now.date().isoformat(), "prediction": meta["prediction"],
                                                "title": meta["title"]}], indent=1), encoding="utf-8")
    if meta["link"]:
        used = json.loads(STATE.read_text()) if STATE.exists() else []
        STATE.write_text(json.dumps(used + [meta["link"]]))
    row = {"date": now.date().isoformat(), "time": now.strftime("%H:%M"), "folder": stamp, "title": meta["title"],
           "topic": meta["topic"], "series": series["name"], "link": meta["link"], "video_id": "",
           "images": len(images), "voice": voice_name, "privacy": "", "comment_posted": "",
           "hook": series.get("hook", "").split(" (")[0].split(":")[0]}
    if do_upload:
        row["privacy"] = "review" if REVIEW_MODE else "pending"   # render-only test runs stay blank
    rows = read_runs() + [row]
    write_runs(rows)   # log before uploading so a failed upload can be retried with --upload-latest
    if do_upload and REVIEW_MODE:
        notify("SubtleSpawns: Short ready for review", f"{meta['title']} - watch it in output\\{stamp}, then run make_short.py --upload-latest")
    elif do_upload:
        row["video_id"], row["privacy"] = upload(mp4, meta), PRIVACY
        write_runs(rows)
        delete_local(mp4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--upload-latest", action="store_true")
    ap.add_argument("--upload-folder", help="upload this output folder's Short")
    ap.add_argument("--check-login", action="store_true", help="make sure the YouTube sign-in still works")
    ap.add_argument("--drive-login", action="store_true", help="allow copying Shorts to Google Drive")
    args = ap.parse_args()
    try:
        if args.drive_login:
            drive_login()
        elif args.check_login:
            ch = get_youtube().channels().list(part="snippet", mine=True).execute()["items"][0]
            print("YouTube sign-in OK:", ch["snippet"]["title"])
        elif args.upload_folder or args.upload_latest:
            upload_latest(args.upload_folder)
        else:
            make_short(args.upload)
    except SystemExit:
        raise
    except Exception as e:
        notify("SubtleSpawns: YouTube sign-in broken" if args.check_login else "SubtleSpawns: daily Short failed",
               f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
