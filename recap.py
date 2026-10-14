"""Weekly recap: stitch the last 7 days of Shorts into one video for @SubtleSpawns.

Usage:
    python recap.py            # render output/recap_<date>.mp4
    python recap.py --upload   # render + upload
"""
import argparse, csv, datetime as dt, json, subprocess, sys
from collections import Counter

from make_short import FFMPEG, OUT_DIR, PRIVACY, STATS, get_youtube, read_runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--month", action="store_true", help="horizontal 'best of the month' video from the top 10 Shorts")
    args = ap.parse_args()

    days = 31 if args.month else 7
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = [r for r in read_runs() if r["date"] >= since and r.get("video_id") and (OUT_DIR / r["folder"] / "short.mp4").exists()]
    if args.month:
        views = {s["video_id"]: int(s["views"] or 0) for s in csv.DictReader(open(STATS, encoding="utf-8"))} if STATS.exists() else {}
        rows = sorted(sorted(rows, key=lambda r: -views.get(r["video_id"], 0))[:10], key=lambda r: r["folder"])
    if len(rows) < 2:
        print(f"Only {len(rows)} uploaded Short(s) in the last {days} days with the video still saved; need at least 2. Nothing to do yet.")
        return
    clips = [OUT_DIR / r["folder"] / "short.mp4" for r in rows]
    out = OUT_DIR / f"{'monthly' if args.month else 'recap'}_{dt.date.today().isoformat()}.mp4"
    cmd = [FFMPEG, "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    if args.month:   # 16:9: the Short in the middle over a blurred, zoomed copy of itself
        graph = "".join(f"[{i}:v]split[a{i}][b{i}];[a{i}]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,boxblur=30,"
                        f"eq=brightness=-0.15[bg{i}];[b{i}]scale=-2:1080[fg{i}];[bg{i}][fg{i}]overlay=(W-w)/2:0,setsar=1[v{i}];"
                        for i in range(len(clips)))
        graph += "".join(f"[v{i}][{i}:a]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=1[v][a]"
    else:
        graph = "".join(f"[{i}:v][{i}:a]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=1[v][a]"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
            "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    print("Rendered:", out)

    topics = [t for t, _ in Counter(r["topic"] for r in rows).most_common(3)]
    month = (dt.date.today() - dt.timedelta(days=15)).strftime("%B %Y")
    title = f"Top {len(rows)} Tech Stories of {month} | SubtleSpawns Monthly" if args.month else f"This Week in Tech: {', '.join(t.upper() if t == 'ai' else t.title() for t in topics)} | SubtleSpawns Weekly Recap"
    desc = ((f"The most-watched SubtleSpawns tech Shorts of {month} in one video." if args.month else
             "Every SubtleSpawns tech Short from this week in one video.") + "\n\n" +
            "\n".join(f"- {r['title'].replace('#shorts', '').strip()}" for r in rows) +
            "\n\nFollow for daily tech news.\n#TechNews #" + ("MonthlyRecap" if args.month else "WeeklyRecap") + " #Technology")
    (out.with_suffix(".json")).write_text(json.dumps({"title": title, "description": desc}, indent=2), encoding="utf-8")
    if args.upload:
        from googleapiclient.http import MediaFileUpload
        res = get_youtube().videos().insert(part="snippet,status", body={
            "snippet": {"title": title[:100], "description": desc, "categoryId": "28",
                        "tags": ["tech news", "weekly recap", "technology", "AI news", "SubtleSpawns"]},
            "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}},
            media_body=MediaFileUpload(str(out), resumable=True)).execute()
        print(f"Uploaded: https://youtube.com/watch?v={res['id']} ({PRIVACY})")


if __name__ == "__main__":
    main()
