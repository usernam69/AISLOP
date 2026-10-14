"""Daily analytics + engagement for @SubtleSpawns.

- Pulls views/likes/comments for every uploaded Short into stats.csv
- Prints what works: average views by topic, series and upload hour, plus the top titles
  (make_short.py feeds the top titles back into the script prompt)
- Posts each Short's question as the first comment once the video is public
- Drafts replies to new viewer comments in replies.md for you to review (never posts them)

Usage:  python stats.py
"""
import csv, json, os
from collections import defaultdict

from make_short import MILESTONE, OUT_DIR, PRIVACY, STATS, VIEWER_QS, get_youtube, llm, parse_json, read_runs, write_runs

STAT_FIELDS = ["date", "time", "video_id", "title", "topic", "series", "privacy", "views", "likes", "comments", "hook"]


def fetch_stats(yt, rows):
    ids = [r["video_id"] for r in rows if r.get("video_id")]
    info = {}
    for i in range(0, len(ids), 50):
        for v in yt.videos().list(part="statistics,status", id=",".join(ids[i:i + 50])).execute().get("items", []):
            info[v["id"]] = v
    out = []
    for r in rows:
        v = info.get(r.get("video_id"))
        if not v:
            continue
        s = v["statistics"]
        out.append({"date": r["date"], "time": r["time"], "video_id": v["id"], "title": r["title"], "topic": r["topic"],
                    "series": r["series"], "privacy": v["status"]["privacyStatus"], "views": s.get("viewCount", "0"),
                    "likes": s.get("likeCount", "0"), "comments": s.get("commentCount", "0"), "hook": r.get("hook", "")})
    with open(STATS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=STAT_FIELDS)
        w.writeheader()
        w.writerows(out)
    return out


def report(stats):
    public = [s for s in stats if s["privacy"] == "public"]
    print(f"\n{len(stats)} Shorts tracked, {len(public)} public, {sum(int(s['views']) for s in stats)} total views")
    for key, label in (("topic", "Topic"), ("series", "Series"), ("hour", "Upload hour")):
        groups = defaultdict(list)
        for s in public:
            groups[s["time"][:2] + ":00" if key == "hour" else s[key]].append(int(s["views"]))
        if groups:
            print(f"\nAverage views by {label.lower()}:")
            for k, v in sorted(groups.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
                print(f"  {k:<22} {sum(v) / len(v):>8.0f}  ({len(v)} Shorts)")
    if public:
        print("\nTop titles:")
        for s in sorted(public, key=lambda s: -int(s["views"]))[:5]:
            print(f"  {int(s['views']):>7}  {s['title']}")


def post_pending_comments(yt, rows, stats):
    status = {s["video_id"]: s["privacy"] for s in stats}
    changed = False
    for r in rows:
        if not r.get("video_id") or r.get("comment_posted") or status.get(r["video_id"]) != "public":
            continue   # YouTube doesn't allow comments on private videos
        meta_file = OUT_DIR / r["folder"] / "meta.json"
        question = json.loads(meta_file.read_text(encoding="utf-8")).get("comment_question") if meta_file.exists() else None
        if not question:
            continue
        try:
            yt.commentThreads().insert(part="snippet", body={"snippet": {"videoId": r["video_id"],
                                       "topLevelComment": {"snippet": {"textOriginal": question}}}}).execute()
            r["comment_posted"] = "yes"
            changed = True
            print(f"Posted first comment on {r['video_id']}: {question}")
        except Exception as e:
            print(f"Comment failed on {r['video_id']} ({type(e).__name__})")
    if changed:
        write_runs(rows)


def draft_replies(yt, stats):
    me = yt.channels().list(part="id", mine=True).execute()["items"][0]["id"]
    todo = []
    for s in [s for s in stats if s["privacy"] == "public" and int(s["comments"]) > 0][-10:]:
        threads = yt.commentThreads().list(part="snippet", videoId=s["video_id"], maxResults=20, order="time").execute()
        for t in threads.get("items", []):
            c = t["snippet"]["topLevelComment"]["snippet"]
            if t["snippet"]["totalReplyCount"] == 0 and c.get("authorChannelId", {}).get("value") != me:
                todo.append({"video": s["title"], "author": c["authorDisplayName"], "comment": " ".join(c["textOriginal"].split())[:300],
                             "link": f"https://youtube.com/shorts/{s['video_id']}", "thread": t["id"]})
    for q in save_viewer_questions(todo):
        reply_to_question(yt, q)
    if not todo:
        print("\nNo new comments to reply to.")
        return
    drafts = parse_json(llm("You are Spawn, the friendly, knowledgeable host of the SubtleSpawns tech Shorts channel. "
                            "Draft a short, warm reply (1-2 sentences, no hashtags) to each viewer comment. Skip spam or abuse "
                            'by giving an empty reply. Reply with ONLY JSON: {"replies": ["...", ...]} in the same order.\n\n'
                            + json.dumps(todo[:15], indent=1))) or {}
    lines = ["# Reply drafts (review, then post the ones you like on YouTube)\n"]
    for item, reply in zip(todo, drafts.get("replies", [])):
        if reply:
            lines.append(f"**{item['author']}** on [{item['video']}]({item['link']}):\n> {item['comment']}\n\nDraft: {reply}\n")
    (OUT_DIR.parent / "replies.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nDrafted {len(lines) - 1} replies in replies.md")


def save_viewer_questions(todo):
    """Questions viewers ask become Saturday 'Viewer Question' Shorts."""
    qs = json.loads(VIEWER_QS.read_text(encoding="utf-8")) if VIEWER_QS.exists() else []
    seen = {q["question"] for q in qs} | set(json.loads(VIEWER_QS.with_suffix(".done").read_text(encoding="utf-8"))
                                             if VIEWER_QS.with_suffix(".done").exists() else [])
    new = [{"author": t["author"], "question": t["comment"]} for t in todo
           if "?" in t["comment"] and len(t["comment"]) > 15 and t["comment"] not in seen]
    if new:
        VIEWER_QS.write_text(json.dumps(qs + new, indent=1), encoding="utf-8")
        VIEWER_QS.with_suffix(".done").write_text(json.dumps(sorted(seen | {q["question"] for q in new})), encoding="utf-8")
        print(f"Saved {len(new)} viewer question(s) for Saturday Shorts")
    return [t for t in todo if t["comment"] in {q["question"] for q in new}]


def reply_to_question(yt, item):
    """Answer a viewer's question in the comments right away: it's coming in a Saturday Short."""
    if os.getenv("AUTO_REPLY_QUESTIONS", "1") != "1":
        return
    text = f"Great question, {item['author'].lstrip('@')}! We'll answer it in a Saturday Viewer Question Short. Subscribe so you don't miss it."
    try:
        yt.comments().insert(part="snippet", body={"snippet": {"parentId": item["thread"], "textOriginal": text}}).execute()
        print(f"Replied to {item['author']}'s question")
    except Exception as e:
        print(f"Reply failed ({type(e).__name__})")


START_HERE = "Start Here: Best of SubtleSpawns"


def update_start_here(yt, stats):
    """Keep a 'Start here' playlist of the 10 most-viewed public Shorts."""
    top = [s["video_id"] for s in sorted((s for s in stats if s["privacy"] == "public"), key=lambda s: -int(s["views"]))[:10]]
    if len(top) < 3:
        return
    lists = yt.playlists().list(part="snippet", mine=True, maxResults=50).execute().get("items", [])
    pid = next((p["id"] for p in lists if p["snippet"]["title"] == START_HERE), None)
    if not pid:
        pid = yt.playlists().insert(part="snippet,status", body={
            "snippet": {"title": START_HERE, "description": "New here? Start with the most-watched SubtleSpawns tech Shorts."},
            "status": {"privacyStatus": "public"}}).execute()["id"]
    items = yt.playlistItems().list(part="snippet", playlistId=pid, maxResults=50).execute().get("items", [])
    have = {i["snippet"]["resourceId"]["videoId"]: i["id"] for i in items}
    for vid, item_id in have.items():
        if vid not in top:
            yt.playlistItems().delete(id=item_id).execute()
    for vid in top:
        if vid not in have:
            yt.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": pid,
                                      "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
    print(f"'{START_HERE}' playlist: top {len(top)} Shorts")


MILESTONES = [100, 500, 1000, 5000, 10000, 50000, 100000, 500000, 1000000]


def check_milestone(yt):
    """When subscribers pass a milestone, tomorrow's Short is a thank-you."""
    subs = int(yt.channels().list(part="statistics", mine=True).execute()["items"][0]["statistics"].get("subscriberCount", 0))
    done = MILESTONE.with_suffix(".done")
    reached = json.loads(done.read_text()) if done.exists() else [m for m in MILESTONES if m <= subs]   # first run: don't celebrate old ones
    new = [m for m in MILESTONES if m <= subs and m not in reached]
    if new:
        MILESTONE.write_text(json.dumps({"subs": max(new)}))
        print(f"Milestone: {max(new):,} subscribers! Tomorrow's Short says thanks.")
    done.write_text(json.dumps(sorted(set(reached) | set(new))))
    print(f"Subscribers: {subs:,}")


def republish(yt, rows):
    """YouTube can lock uploads from unaudited apps as private; flip them back to public."""
    ids = [r["video_id"] for r in rows if r.get("video_id")][-50:]
    if not ids:
        return
    for v in yt.videos().list(part="status", id=",".join(ids)).execute().get("items", []):
        if v["status"]["privacyStatus"] == "private" and v["status"]["uploadStatus"] == "processed":
            try:
                yt.videos().update(part="status", body={"id": v["id"], "status": {
                    "privacyStatus": "public", "selfDeclaredMadeForKids": False}}).execute()
                print(f"Made {v['id']} public")
            except Exception as e:
                print(f"Couldn't make {v['id']} public ({type(e).__name__}) - do it in YouTube Studio")


def main():
    rows = read_runs()
    if not any(r.get("video_id") for r in rows):
        print("No uploaded Shorts yet.")
        return
    yt = get_youtube()
    if PRIVACY == "public":
        republish(yt, rows)
    stats = fetch_stats(yt, rows)
    report(stats)
    post_pending_comments(yt, rows, stats)
    try:
        check_milestone(yt)
    except Exception as e:
        print(f"Milestone check failed ({type(e).__name__})")
    try:
        update_start_here(yt, stats)
    except Exception as e:
        print(f"Start Here playlist failed ({type(e).__name__})")
    try:
        draft_replies(yt, stats)
    except Exception as e:
        print(f"Reply drafting failed ({type(e).__name__})")


if __name__ == "__main__":
    main()
