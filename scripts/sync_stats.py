#!/usr/bin/env python3
"""
Automatic Account & Post Stats Sync (sync_stats.py)

Pulls real metrics for your Instagram, TikTok, and YouTube Shorts accounts and
updates scoreboard.csv (NEVER guesses a number — leaves unavailable fields blank):
  - YouTube Shorts (100% FREE via yt-dlp): channel subscribers + Shorts views/likes/comments
  - TikTok & Instagram (via free-tier Scrape Creators API if SCRAPE_CREATORS_API_KEY is set,
    strictly capped by SCRAPE_CREATORS_CREDIT_CAP)

Configure handles via environment variables (or GitHub Actions Variables):
  YT_CHANNEL_URL   e.g. https://www.youtube.com/@YourChannelHandle
  TIKTOK_HANDLE    e.g. yourtiktokhandle (without @)
  IG_HANDLE        e.g. yourighandle (without @)
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
SCOREBOARD_PATH = ROOT_DIR / "scoreboard.csv"


def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def normalize_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def clean_handle(raw: str) -> str:
    """Accept either a bare handle ('leonida.chaos4', '@leonida.chaos4') or a full profile URL."""
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        parsed = urllib.parse.urlparse(s)
        parts = [p for p in parsed.path.split("/") if p]
        s = parts[0] if parts else ""
    return s.lstrip("@").split("?")[0].strip()


def clean_youtube_channel_url(raw: str) -> str:
    """Strip tracking query parameters like ?si=... from YouTube channel URLs."""
    s = (raw or "").strip()
    if not s:
        return ""
    if not s.startswith("http"):
        return f"https://www.youtube.com/@{s.lstrip('@')}"
    parsed = urllib.parse.urlparse(s)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def fetch_youtube_channel_stats(channel_url: str) -> dict[str, Any]:
    """Pull Shorts list + views/likes/comments/subscribers from YouTube channel for free via yt-dlp."""
    url = clean_youtube_channel_url(channel_url)
    shorts_url = url if url.endswith("/shorts") else f"{url}/shorts"
    out = subprocess.check_output(
        ["yt-dlp", "--flat-playlist", "--playlist-end", "25", "--dump-json", "--no-warnings", shorts_url],
        text=True,
        timeout=45,
    )
    videos = []
    followers = None
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        vid_id = d.get("id")
        if not vid_id:
            continue
        item = {
            "id": vid_id,
            "url": f"https://www.youtube.com/shorts/{vid_id}",
            "title": d.get("title") or "",
            "views": d.get("view_count"),
            "likes": None,
            "comments": None,
            "shares": None,
        }
        try:
            detail_raw = subprocess.check_output(
                ["yt-dlp", "--skip-download", "--dump-json", "--no-warnings", f"https://www.youtube.com/watch?v={vid_id}"],
                text=True,
                timeout=20,
            )
            det = json.loads(detail_raw.strip().splitlines()[0])
            item["views"] = det.get("view_count", item["views"])
            item["likes"] = det.get("like_count")
            item["comments"] = det.get("comment_count")
            if det.get("channel_follower_count") is not None:
                followers = det["channel_follower_count"]
        except Exception:
            pass
        videos.append(item)
    return {"platform": "youtube", "followers": followers, "videos": videos}


def scrape_creators_get(path: str, params: dict[str, str], api_key: str) -> dict[str, Any]:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"https://api.scrapecreators.com{path}?{qs}",
        headers={"x-api-key": api_key, "User-Agent": "gta6-sync-stats/1.0"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_tiktok_stats(handle: str, api_key: str) -> tuple[dict[str, Any], int]:
    """Use Scrape Creators /v3/tiktok/profile/videos (1 credit total — includes follower_count!)."""
    clean = clean_handle(handle)
    vids_data = scrape_creators_get("/v3/tiktok/profile/videos", {"handle": clean, "sort_by": "latest"}, api_key)
    followers = None
    videos = []
    for item in vids_data.get("aweme_list") or vids_data.get("data") or []:
        if followers is None:
            fc = (item.get("author") or {}).get("follower_count")
            if fc is not None:
                followers = fc
        st = item.get("statistics") or {}
        vid_id = item.get("aweme_id") or item.get("id") or ""
        videos.append({
            "id": str(vid_id),
            "url": f"https://www.tiktok.com/@{clean}/video/{vid_id}" if vid_id else "",
            "title": item.get("desc") or "",
            "views": st.get("play_count"),
            "likes": st.get("digg_count"),
            "shares": st.get("share_count"),
            "comments": st.get("comment_count"),
            "saves": st.get("collect_count"),
        })
    return {
        "platform": "tiktok",
        "followers": followers,
        "videos": videos,
        "credits_remaining": vids_data.get("credits_remaining"),
    }, 1


def fetch_instagram_stats(handle: str, api_key: str) -> tuple[dict[str, Any], int]:
    clean = clean_handle(handle)
    prof = scrape_creators_get("/v1/instagram/profile", {"handle": clean}, api_key)
    reels_data = scrape_creators_get("/v1/instagram/user/reels", {"handle": clean}, api_key)
    user_obj = (prof.get("data") or {}).get("user") or prof.get("data") or prof
    followers = user_obj.get("follower_count")
    if followers is None:
        followers = (user_obj.get("edge_followed_by") or {}).get("count")
    videos = []
    for item in reels_data.get("items") or reels_data.get("data") or []:
        media = item.get("media") or item
        code = media.get("code") or media.get("shortcode") or ""
        cap = (media.get("caption") or {}).get("text") if isinstance(media.get("caption"), dict) else (media.get("caption") or "")
        plays = media.get("play_count")
        if plays is None:
            plays = media.get("ig_play_count")
        if plays is None:
            plays = media.get("video_view_count")
        videos.append({
            "id": str(code),
            "url": f"https://www.instagram.com/reel/{code}/" if code else "",
            "title": cap,
            "views": plays,
            "likes": media.get("like_count"),
            "shares": media.get("reshare_count"),
            "comments": media.get("comment_count"),
        })
    return {
        "platform": "instagram",
        "followers": followers,
        "videos": videos,
        "credits_remaining": reels_data.get("credits_remaining"),
    }, 2


def load_ledger_captions() -> dict[tuple[str, str], str]:
    ledger_p = ROOT_DIR / "data" / "publish_ledger.json"
    if not ledger_p.exists():
        return {}
    try:
        data = json.loads(ledger_p.read_text(encoding="utf-8"))
        out: dict[tuple[str, str], str] = {}
        for e in data.get("entries", []):
            pid = (e.get("post_id") or "").strip()
            plat = (e.get("platform") or "").strip().lower()
            cap = (e.get("caption_first_line") or "").strip()
            if pid and plat and cap:
                out[(pid, plat)] = cap
        return out
    except Exception:
        return {}


def match_video(row: dict[str, str], videos: list[dict[str, Any]], ledger_caps: dict[tuple[str, str], str]) -> dict[str, Any] | None:
    notes = row.get("notes") or ""
    # 1. Exact URL / ID match in notes
    for v in videos:
        if v.get("url") and v["url"] in notes:
            return v
        if v.get("id") and len(v["id"]) >= 6 and v["id"] in notes:
            return v

    # 2. Match against exact caption first line stored in publish_ledger.json (most accurate!)
    pid = (row.get("post_id") or "").strip()
    plat = (row.get("platform") or "").strip().lower()
    cap_line = normalize_text(ledger_caps.get((pid, plat), ""))
    cap_words = [w for w in cap_line.split() if len(w) >= 4]
    if cap_words:
        for v in videos:
            t_norm = normalize_text(v.get("title", ""))
            if cap_line and cap_line in t_norm:
                return v
            if sum(1 for w in cap_words if w in t_norm) >= min(3, len(cap_words)):
                return v

    # 3. Fallback: post_id or hook + streamer keyword overlap
    hook_words = [w for w in normalize_text(row.get("hook", "")).split() if len(w) >= 4]
    credit_handle = normalize_text((row.get("credit") or "").split("(")[0])
    for v in videos:
        t_norm = normalize_text(v.get("title", ""))
        if pid.lower() in t_norm:
            return v
        if hook_words and sum(1 for w in hook_words if w in t_norm) >= 2:
            return v
        if credit_handle and credit_handle in t_norm and any(w in t_norm for w in cap_words[:2]):
            return v
    return None


def update_scoreboard(platform_data: list[dict[str, Any]], scoreboard_path: Path = SCOREBOARD_PATH) -> int:
    if not scoreboard_path.exists():
        return 0
    with scoreboard_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    by_plat = {d["platform"]: d for d in platform_data}
    ledger_caps = load_ledger_captions()
    updated_rows = 0
    stamp = utc_now_str()

    for row in rows:
        p = (row.get("platform") or "").strip().lower()
        pdata = by_plat.get(p)
        if not pdata:
            continue
        if pdata.get("followers") is not None:
            row["followers_at_read"] = str(pdata["followers"])
        matched = match_video(row, pdata.get("videos") or [], ledger_caps)
        if not matched:
            continue
        v = matched.get("views")
        if v is not None:
            if not row.get("views_12h"):
                row["views_12h"] = str(v)
            row["views_24h"] = str(v)
        if matched.get("likes") is not None:
            row["likes"] = str(matched["likes"])
        if matched.get("shares") is not None:
            row["reposts_or_shares"] = str(matched["shares"])
        if matched.get("comments") is not None:
            row["replies_or_comments"] = str(matched["comments"])
        if matched.get("saves") is not None:
            row["saves_or_bookmarks"] = str(matched["saves"])

        notes = row.get("notes") or ""
        if matched.get("url") and matched["url"] not in notes:
            notes = f"{matched['url']} | {notes}".strip(" |")
        read_tag = f"read({stamp}):v={v}"
        if read_tag not in notes:
            notes = f"{notes} | {read_tag}".strip(" |")
        row["notes"] = notes
        updated_rows += 1

    with scoreboard_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return updated_rows


def main() -> int:
    yt_url = os.environ.get("YT_CHANNEL_URL", "").strip()
    tt_handle = os.environ.get("TIKTOK_HANDLE", "").strip()
    ig_handle = os.environ.get("IG_HANDLE", "").strip()
    sc_key = os.environ.get("SCRAPE_CREATORS_API_KEY", "").strip()
    sc_cap = int(os.environ.get("SCRAPE_CREATORS_CREDIT_CAP", "40"))

    collected: list[dict[str, Any]] = []
    credits_used = 0
    errors: list[str] = []

    if yt_url:
        try:
            collected.append(fetch_youtube_channel_stats(yt_url))
        except Exception as exc:
            errors.append(f"youtube: {type(exc).__name__}")

    if sc_key and tt_handle and credits_used + 2 <= sc_cap:
        try:
            data, c = fetch_tiktok_stats(tt_handle, sc_key)
            collected.append(data)
            credits_used += c
        except Exception as exc:
            errors.append(f"tiktok: {type(exc).__name__}")

    if sc_key and ig_handle and credits_used + 2 <= sc_cap:
        try:
            data, c = fetch_instagram_stats(ig_handle, sc_key)
            collected.append(data)
            credits_used += c
        except Exception as exc:
            errors.append(f"instagram: {type(exc).__name__}")

    updated = update_scoreboard(collected) if collected else 0
    print(json.dumps({
        "synced_platforms": [d["platform"] for d in collected],
        "followers": {d["platform"]: d.get("followers") for d in collected},
        "rows_updated": updated,
        "scrape_creators_credits_used": credits_used,
        "errors": errors,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
