"""Daily tech-news Short for @SubtleSpawns.

Pulls tech headlines, writes a script (Claude), voices it (free Edge TTS),
renders a 1080x1920 Short over a random slice of the parkour footage with
burned-in word-by-word subtitles, and optionally uploads it to YouTube.

Usage:
    python make_short.py            # render only
    python make_short.py --upload   # render + upload
"""
import argparse, asyncio, datetime as dt, html, json, os, random, re, subprocess, sys
from pathlib import Path

import edge_tts, feedparser, imageio_ffmpeg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

BG_VIDEO = Path(os.getenv("BG_VIDEO", HERE.parent / "6 hour Minecraft parkour run (no falls, study, sleep).mp4"))
OUT_DIR = HERE / "output"
MUSIC_DIR = HERE / "music"          # drop royalty-free .mp3 files here (optional)
STATE = HERE / "used_headlines.json"
VOICE = os.getenv("VOICE", "en-US-AndrewMultilingualNeural")
PRIVACY = os.getenv("YT_PRIVACY", "private")   # switch to "public" once your API audit is approved
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FEEDS = [
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://techcrunch.com/feed/",
    "https://www.engadget.com/rss.xml",
]


def fetch_headlines(n=8):
    used = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    items = []
    for url in FEEDS:
        for e in feedparser.parse(url).entries[:6]:
            if e.get("link") not in used and not re.search(r"(?i)\b(elections?|electoral|donat\w*|republicans?|democrats?|trump|senate|congress|box office|movies?)\b", e.title):
                summary = html.unescape(re.sub("<[^>]+>", "", e.get("summary", ""))).replace("[â€¦]", "")[:400]
                items.append({"title": html.unescape(e.title), "link": e.link, "summary": summary})
    return items[:n]


def clean_meta(meta, headlines):
    """Fill missing fields and pin the link to a real headline so it's remembered as used."""
    if not meta.get("script"):
        raise ValueError("AI reply has no script")
    links = {h["link"] for h in headlines}
    if meta.get("link") not in links:
        said = set(re.findall(r"\w+", (meta.get("title", "") + " " + meta["script"]).lower()))
        best = max(headlines, key=lambda h: len(said & set(re.findall(r"\w+", h["title"].lower()))))
        meta["link"] = best["link"]
    title = meta.get("title") or meta["script"].split(".")[0][:60]
    meta["title"] = title if "#shorts" in title.lower() else title + " #shorts"
    meta.setdefault("hook_card", "Tech news in 40 seconds")
    meta.setdefault("description", f"{meta['title']}\nSource: {meta['link']}\n#tech #technews #shorts")
    if not isinstance(meta.get("tags"), list):
        meta["tags"] = ["tech news", "technology", "shorts"]
    meta["visuals"] = [v for v in meta.get("visuals") or [] if isinstance(v, dict) and v.get("word") and v.get("query")]
    return meta


def write_script(headlines):
    prompt = f"""You write YouTube Shorts for SubtleSpawns, a tech news & tips channel.
Tone: professional yet engaging, for a tech-savvy audience. No emojis in the script.
Pick the ONE most interesting technology story below (gadgets, AI, software, security, science tech). Never pick politics, elections, donations, celebrities or movies and write a 40-50 second voiceover (110-130 words):
- First sentence is a scroll-stopping hook (a surprising fact or question), max 12 words.
- Then explain what happened and why it matters, plain language.
- End with one practical tip or takeaway, then "Follow for daily tech news."
Also write SEO metadata.

Headlines:
{json.dumps(headlines, indent=1)}

Reply with ONLY JSON:
{{"link": "...", "hook_card": "3-6 word on-screen hook", "script": "...",
"title": "under 70 chars, keyword first, ends with #shorts",
"description": "2-3 keyword-rich sentences, then a source line, then 5 hashtags",
"tags": ["10-15 search keywords"],
"visuals": [{{"word": "one key word copied exactly from the script", "query": "2-4 word photo search for it"}}]}}
For "visuals" give 4-6 items: the main nouns (companies, products, objects), in script order, never in the first sentence."""
    if os.getenv("OPENROUTER_API_KEY"):
        import httpx
        # paid Claude first; free model if the account has no credits
        for model in [os.getenv("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5"), "google/gemma-4-31b-it:free"]:
            r = httpx.post("https://openrouter.ai/api/v1/chat/completions", timeout=120,
                           headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                           json={"model": model, "max_tokens": 4000, "reasoning": {"max_tokens": 800}, "messages": [{"role": "user", "content": prompt}]})
            if r.status_code == 200:
                print("Script written by", model)
                choice = r.json()["choices"][0]
                text = choice["message"].get("content") or ""
                try:
                    return json.loads(text[text.find("{"): text.rfind("}") + 1])
                except ValueError:
                    (OUT_DIR / "last_bad_reply.json").write_text(json.dumps(r.json(), indent=1), encoding="utf-8")
                    print(f"{model} reply unparseable (finish_reason={choice.get('finish_reason')})")
            print(f"{model} failed ({r.status_code}): {r.text[:200]}")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("No working AI key - using a basic template script.")
        h = headlines[0]
        return {"link": h["link"], "hook_card": "Tech news in 40 seconds",
                "script": f"Here's the tech story everyone's talking about. {h['title']}. {h['summary'][:500]} Follow for daily tech news.",
                "title": (h['title'] if len(h['title']) <= 60 else h['title'][:60].rsplit(' ', 1)[0]) + " #shorts", "description": f"{h['title']}\nSource: {h['link']}\n#tech #technews #shorts",
                "tags": ["tech news", "technology", "shorts"]}
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model="claude-sonnet-5-5", max_tokens=1500, messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


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
    words = [(float(t), raw[i + 1][0] if i + 1 < len(raw) else float(t) + 0.4, w) for i, (t, w) in enumerate(raw)]
    return words


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


def norm(w):
    return re.sub(r"\W", "", w.lower())


def fetch_images(visuals, words, work, total):
    """Free Creative Commons photos (Openverse) shown for 2s when their word is spoken."""
    import httpx
    shown, last_end = [], 3.4          # stay clear of the opening hook card
    for n, v in enumerate(visuals):
        target = norm(v["word"].split()[0])
        t = next((s for s, _, w in words if norm(w) == target and s >= last_end), None)
        if t is None or t + 1 > total:
            continue
        try:
            res = httpx.get("https://api.openverse.org/v1/images/", headers={"User-Agent": "AISLOP/1.0"}, timeout=30,
                            params={"q": v["query"], "license_type": "commercial", "page_size": 5}).json()["results"]
            pick = next(r for r in res if r["url"].lower().split("?")[0].endswith((".jpg", ".jpeg", ".png")))
            img = work / f"img{n}{Path(pick['url'].split('?')[0]).suffix.lower()}"
            img.write_bytes(httpx.get(pick["url"], headers={"User-Agent": "AISLOP/1.0"}, timeout=30, follow_redirects=True).raise_for_status().content)
        except Exception as e:
            print(f"No image for '{v['word']}' ({type(e).__name__})")
            continue
        end = min(t + 2.0, total)
        credit = f"\"{pick.get('title') or 'Photo'}\" by {pick.get('creator') or 'unknown'} ({pick['license'].upper()} {pick.get('license_version') or ''}) {pick.get('foreign_landing_url') or pick['url']}"
        shown.append((img, t, end, credit))
        last_end = end + 0.3
    print(f"Images: {len(shown)}")
    return shown


def ass_time(t):
    t = round(t, 2)  # round first so 59.996 can't print as "60.00"
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def make_subs(words, hook, path, total):
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial Black,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,3,5,80,80,0,1
Style: Hook,Arial Black,72,&H0000E5FF,&H00FFFFFF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,3,18,0,8,60,60,260,1
Style: Brand,Arial,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,2,40,40,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 1,{ass_time(0)},{ass_time(3.2)},Hook,,0,0,0,,{{\\fad(150,250)\\t(0,200,\\fscx110\\fscy110)\\t(200,400,\\fscx100\\fscy100)}}{hook.upper()}",
             f"Dialogue: 0,{ass_time(0)},{ass_time(total)},Brand,,0,0,0,,@SubtleSpawns"]
    # 3-word chunks, current word highlighted yellow, chunk pops in
    for i in range(0, len(words), 3):
        chunk = words[i:i + 3]
        for j, (s, e, _) in enumerate(chunk):
            end = chunk[j + 1][0] if j + 1 < len(chunk) else (words[i + 3][0] if i + 3 < len(words) else e + 0.4)
            txt = " ".join(("{\\c&H0000E5FF&}" + w + "{\\c&H00FFFFFF&}") if k == j else w
                           for k, (_, _, w) in enumerate(chunk))
            pop = "{\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}" if j == 0 else ""
            lines.append(f"Dialogue: 0,{ass_time(s)},{ass_time(end)},Cap,,0,0,0,,{pop}{txt.upper()}")
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


def bg_duration():
    out = subprocess.run([FFMPEG, "-i", str(BG_VIDEO)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def render(voice_mp3, subs, total, out_mp4, images=()):
    start = random.uniform(30, bg_duration() - total - 30)
    music = sorted(MUSIC_DIR.glob("*.mp3")) if MUSIC_DIR.exists() else []
    cmd = [FFMPEG, "-y", "-ss", f"{start:.1f}", "-t", f"{total:.2f}", "-i", str(BG_VIDEO), "-i", str(voice_mp3)]
    vf = ("[0:v]crop=ih*9/16:ih,scale=1080:1920,eq=brightness=-0.06:saturation=1.15,"
          # slow push-in keeps the frame moving
          f"zoompan=z='min(1+0.0006*on,1.15)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=1080x1920:fps=30"
          "[bg]")
    last = "bg"
    for k, (img, s0, e0, _) in enumerate(images):
        cmd += ["-loop", "1", "-t", f"{total:.2f}", "-i", str(img)]
        # framed photo in the upper half, fades in/out, above the captions
        vf += (f";[{2 + k}:v]scale=820:520:force_original_aspect_ratio=decrease,pad=iw+20:ih+20:10:10:white,"
               f"format=rgba,fade=in:st={s0:.2f}:d=0.15:alpha=1,fade=out:st={e0 - 0.15:.2f}:d=0.15:alpha=1[i{k}]"
               f";[{last}][i{k}]overlay=x=(W-w)/2:y=290:enable='between(t,{s0:.2f},{e0:.2f})'[o{k}]")
        last = f"o{k}"
    vf += f";[{last}]ass={subs.name}[v]"
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(random.choice(music))]
        af = f"[{2 + len(images)}:a]volume=0.12[m];[1:a][m]amix=inputs=2:duration=first:normalize=0[a]"
    else:
        af = "[1:a]anull[a]"
    cmd += ["-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out_mp4)]
    subprocess.run(cmd, check=True, cwd=subs.parent)  # cwd so the ass filter path needs no escaping


def upload(mp4, meta):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    tok = HERE / "token.json"
    creds = Credentials.from_authorized_user_file(tok, scopes) if tok.exists() else None
    if not creds or not creds.valid:
        try:
            if not (creds and creds.expired and creds.refresh_token):
                raise RefreshError("no saved login")
            creds.refresh(Request())
        except RefreshError as e:
            # Testing-mode apps lose their login after 7 days; sign in again
            print(f"YouTube login expired ({e}); opening browser to sign in.")
            creds = InstalledAppFlow.from_client_secrets_file(HERE / "client_secret.json", scopes).run_local_server(port=0)
        tok.write_text(creds.to_json())
    yt = build("youtube", "v3", credentials=creds)
    body = {"snippet": {"title": meta["title"][:100], "description": meta["description"], "tags": meta["tags"],
                        "categoryId": "28"},  # Science & Technology
            "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}
    res = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(mp4), resumable=True)).execute()
    print(f"Uploaded: https://youtube.com/shorts/{res['id']} ({PRIVACY})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true")
    args = ap.parse_args()

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    work = OUT_DIR / stamp
    work.mkdir(parents=True, exist_ok=True)

    headlines = fetch_headlines()
    if not headlines:
        sys.exit("No new headlines found.")
    meta = clean_meta(write_script(headlines), headlines)
    (work / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("Title:", meta["title"])

    voice = work / "voice.mp3"
    try:
        if os.getenv("ELEVENLABS_API_KEY") and os.getenv("ELEVENLABS_VOICE_ID"):
            try:
                words = tts_elevenlabs(meta["script"], voice)
                print("Voice: ElevenLabs")
            except Exception as e:
                print(f"ElevenLabs failed ({e}); trying free voice.")
                words = asyncio.run(tts(meta["script"], voice))
        else:
            words = asyncio.run(tts(meta["script"], voice))
    except Exception as e:
        print(f"Edge voice unavailable ({type(e).__name__}); using built-in Windows voice.")
        voice = work / "voice.wav"
        words = tts_windows(meta["script"], voice)
    total = min(words[-1][1] + 0.8, 59.0)
    make_subs(words, meta["hook_card"], work / "subs.ass", total)
    mp4 = work / "short.mp4"
    images = fetch_images(meta["visuals"], words, work, total)
    if images:
        meta["description"] += "\n\nImages:\n" + "\n".join(c for *_, c in images)
        (work / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    render(voice, work / "subs.ass", total, mp4, images)
    print("Rendered:", mp4)

    used = json.loads(STATE.read_text()) if STATE.exists() else []
    STATE.write_text(json.dumps(used + [meta["link"]]))
    if args.upload:
        upload(mp4, meta)


if __name__ == "__main__":
    main()
