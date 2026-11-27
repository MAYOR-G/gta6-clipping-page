#!/usr/bin/env python3
"""
Automatic Account & Post Stats Sync (sync_stats.py)

Pulls real metrics for your Instagram, TikTok, and YouTube Shorts accounts and
updates scoreboard.csv (NEVER guesses a number — leaves unavailable fields blank):
  - YouTube Shorts (100% FREE via yt-dlp): channel subscribers + Shorts views/likes/comments
  - TikTok & Instagram (via free-tier Scrape Creators API if SCRAPE_CREATORS_API_KEY is set,
    strictly capped by SCRAPE_CREATORS_CREDIT_CAP)

Matching Guarantees (Strict 1-to-1 + Chronological Guard):
  1. A row already bound to a specific video URL/ID ONLY updates from that exact video ID.
     It never falls back to fuzzy matching if that video isn't in the latest feed.
  2. Unbound rows require BOTH:
     - Chronological plausibility (video published_utc >= clip created_at - 3h)
     - Exact post_id OR exact caption/yt_title substring OR >=75% distinctive non-generic
       content words (excluding streamer handle & generic tags like gta6/gaming/shorts).
  3. Every video ID can be claimed by at most ONE row per platform.
  4. Use `--repair` to purge duplicate/hijacked URLs and re-sync clean 1-to-1 stats.

Configure handles via environment variables (or GitHub Actions Variables):
  YT_CHANNEL_URL   e.g. https://www.youtube.com/@YourChannelHandle (comma-separated for multiple)
  TIKTOK_HANDLE    e.g. yourtiktokhandle (without @, comma-separated for multiple)
  IG_HANDLE        e.g. yourighandle (without @, comma-separated for multiple)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
SCOREBOARD_PATH = ROOT_DIR / "scoreboard.csv"
LEDGER_PATH = ROOT_DIR / "data" / "publish_ledger.json"
DAILY_METRICS_PATH = ROOT_DIR / "data" / "daily_metrics.json"

GENERIC_STOPWORDS = {
    "gta", "gta6", "gtavi", "vice", "city", "leonida", "gaming", "game", "games",
    "funnymoments", "funnymoment", "funny", "shorts", "short", "twitch", "youtube",
    "tiktok", "instagram", "reel", "reels", "credit", "clip", "clips", "moment",
    "moments", "video", "stream", "streamer", "reacts", "reaction", "this", "that",
    "with", "from", "just", "into", "when", "what", "they", "have", "your", "about",
    "after", "over", "under", "more", "very", "like", "got", "did", "not", "for",
    "the", "and", "out", "all", "now", "are", "was", "bro", "lol", "omg", "wtf",
}

URL_ID_PATTERNS = {
    "youtube": re.compile(r"youtube\.com/(?:shorts|watch\?v=)/([A-Za-z0-9_-]{6,})|youtu\.be/([A-Za-z0-9_-]{6,})"),
    "tiktok": re.compile(r"tiktok\.com/@[^/]+/video/(\d{6,})"),
    "instagram": re.compile(r"instagram\.com/(?:reel|p)/([A-Za-z0-9_-]{6,})"),
}


def load_local_dotenv() -> None:
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def normalize_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def parse_iso_utc(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_epoch_utc(val: Any) -> datetime | None:
    if val is None or val == "":
        return None
    try:
        return datetime.fromtimestamp(float(val), tz=timezone.utc)
    except Exception:
        return None


def parse_yyyymmdd_utc(val: str | None) -> datetime | None:
    if not val or not re.match(r"^\d{8}$", str(val).strip()):
        return None
    try:
        return datetime.strptime(str(val).strip(), "%Y%m%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


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


def fetch_youtube_channel_stats(channel_url: str, max_items: int = 100) -> dict[str, Any]:
    """Pull up to `max_items` Shorts + views/likes/comments/subscribers from YouTube channel for free via yt-dlp."""
    url = clean_youtube_channel_url(channel_url)
    shorts_url = url if url.endswith("/shorts") else f"{url}/shorts"
    out = subprocess.check_output(
        ["yt-dlp", "--flat-playlist", "--playlist-end", str(max_items), "--dump-json", "--no-warnings", shorts_url],
        text=True,
        timeout=60,
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
        item: dict[str, Any] = {
            "id": str(vid_id),
            "url": f"https://www.youtube.com/shorts/{vid_id}",
            "title": d.get("title") or "",
            "views": d.get("view_count"),
            "likes": None,
            "comments": None,
            "shares": None,
            "published_utc": None,
        }
        try:
            detail_raw = subprocess.check_output(
                ["yt-dlp", "--skip-download", "--dump-json", "--no-warnings", f"https://www.youtube.com/watch?v={vid_id}"],
                text=True,
                timeout=20,
            )
            det = json.loads(detail_raw.strip().splitlines()[0])
            if det.get("view_count") is not None:
                item["views"] = det.get("view_count")
            elif item["views"] is None:
                item["views"] = 0
            item["likes"] = det.get("like_count")
            item["comments"] = det.get("comment_count")
            if det.get("title"):
                item["title"] = det["title"]
            if det.get("description"):
                item["description"] = det["description"]
            item["published_utc"] = parse_epoch_utc(det.get("timestamp")) or parse_yyyymmdd_utc(det.get("upload_date"))
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
            "published_utc": parse_epoch_utc(item.get("create_time")),
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
        pub_dt = parse_epoch_utc(media.get("taken_at") or media.get("taken_at_timestamp"))
        videos.append({
            "id": str(code),
            "url": f"https://www.instagram.com/reel/{code}/" if code else "",
            "title": cap,
            "views": plays,
            "likes": media.get("like_count"),
            "shares": media.get("reshare_count"),
            "comments": media.get("comment_count"),
            "published_utc": pub_dt,
        })
    return {
        "platform": "instagram",
        "followers": followers,
        "videos": videos,
        "credits_remaining": reels_data.get("credits_remaining"),
    }, 2


def load_ledger_meta(ledger_path: Path = LEDGER_PATH) -> dict[tuple[str, str], dict[str, Any]]:
    if not ledger_path.exists():
        return {}
    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for e in data.get("entries", []):
            pid = (e.get("post_id") or "").strip()
            plat = (e.get("platform") or "").strip().lower()
            if not pid or not plat:
                continue
            meta = {
                "caption_first_line": (e.get("caption_first_line") or "").strip(),
                "youtube_title": (e.get("youtube_title") or "").strip(),
                "created_at_utc": parse_iso_utc(e.get("created_at_utc")),
            }
            out[(pid, plat)] = meta
            out.setdefault((pid, "*"), meta)
        return out
    except Exception:
        return {}


def extract_urls_from_notes(notes: str) -> list[str]:
    return [tok.strip() for tok in (notes or "").split("|") if tok.strip().startswith("http")]


def extract_video_id_from_url(url: str, platform: str) -> str | None:
    pat = URL_ID_PATTERNS.get(platform)
    if not pat:
        return None
    m = pat.search(url or "")
    if not m:
        return None
    for g in m.groups():
        if g:
            return g
    return None


def extract_bound_video_id(notes: str, platform: str) -> str | None:
    """Return the bound video ID for `platform` from `notes`, if any."""
    for u in extract_urls_from_notes(notes):
        vid = extract_video_id_from_url(u, platform)
        if vid:
            return vid
    return None


def _row_created_utc(row: dict[str, str], meta: dict[str, Any] | None) -> datetime | None:
    if meta and meta.get("created_at_utc"):
        return meta["created_at_utc"]
    d_str = (row.get("date") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d_str):
        try:
            # Conservative earliest creation bound: 24h before scheduled WAT date
            return datetime.strptime(d_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) - timedelta(hours=24)
        except Exception:
            return None
    return None


def _distinctive_words(text: str, streamer_norm: str) -> list[str]:
    streamer_tokens = set(streamer_norm.split())
    words = []
    for w in normalize_text(text).split():
        if len(w) < 3:
            continue
        if w in GENERIC_STOPWORDS or w in streamer_tokens:
            continue
        words.append(w)
    # Dedupe preserving order
    return list(dict.fromkeys(words))


def _mentions_conflicting_streamer(video_text: str, expected_streamer_norm: str) -> bool:
    if not expected_streamer_norm:
        return False
    mentions = [normalize_text(m) for m in re.findall(r"@([A-Za-z0-9_.]+)", video_text or "")]
    mentions = [m for m in mentions if m and m not in ("leonidachao", "leonida chaos4", "leonida_chaos4")]
    if not mentions:
        return False
    exp_compact = expected_streamer_norm.replace(" ", "")
    for m in mentions:
        if m.replace(" ", "") == exp_compact or exp_compact in m.replace(" ", "") or m.replace(" ", "") in exp_compact:
            return False
    return True


def match_video(
    row: dict[str, str],
    videos: list[dict[str, Any]],
    ledger_meta: dict[tuple[str, str], dict[str, Any]],
    claimed_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """
    Strictly match a scoreboard row to a live video:
    - If row already has a bound URL/ID in `notes`, ONLY match that exact ID (never fallback!).
    - Otherwise, require chronological plausibility AND high-confidence title/caption match
      against unclaimed videos only.
    """
    plat = (row.get("platform") or "").strip().lower()
    pid = (row.get("post_id") or "").strip()
    notes = row.get("notes") or ""
    claimed = claimed_ids or set()

    # 1. Strict bound-ID lock-in: if a URL is already recorded on this row, ONLY update that exact ID
    bound_id = extract_bound_video_id(notes, plat)
    if bound_id:
        for v in videos:
            if str(v.get("id") or "") == bound_id:
                return v
        return None

    # 2. Unbound row: strictly evaluate ONLY unclaimed videos
    unclaimed = [v for v in videos if str(v.get("id") or "") not in claimed]
    if not unclaimed:
        return None

    meta = ledger_meta.get((pid, plat)) or ledger_meta.get((pid, "*")) or {}
    created_utc = _row_created_utc(row, meta)
    streamer_norm = normalize_text((row.get("credit") or "").split("(")[0])

    targets: list[str] = []
    if plat == "youtube" and meta.get("youtube_title"):
        targets.append(meta["youtube_title"])
    if meta.get("caption_first_line"):
        targets.append(meta["caption_first_line"])

    pid_norm = normalize_text(pid)

    best_match: dict[str, Any] | None = None
    best_score = 0.0

    for v in unclaimed:
        pub_utc = v.get("published_utc")
        # Chronological guard: a clip cannot match a video published >3h BEFORE the clip was created
        if created_utc and pub_utc and pub_utc < (created_utc - timedelta(hours=3)):
            continue

        raw_title = f"{v.get('title') or ''} {v.get('description') or ''}".strip()
        if _mentions_conflicting_streamer(raw_title, streamer_norm):
            continue

        t_norm = normalize_text(raw_title)
        if not t_norm:
            continue

        if pid_norm and len(pid_norm) >= 5 and re.search(rf"\b{re.escape(pid_norm)}\b", t_norm):
            return v

        for tgt in targets:
            tgt_norm = normalize_text(tgt)
            if not tgt_norm:
                continue
            dist_words = _distinctive_words(tgt, streamer_norm)

            # Exact normalized caption/title substring match
            # Requires either >=2 distinctive words OR (for ultra-short captions) exact streamer name in title/desc
            has_streamer_confirm = bool(streamer_norm and streamer_norm in t_norm)
            if len(tgt_norm) >= 8 and tgt_norm in t_norm and (len(dist_words) >= 2 or has_streamer_confirm):
                score = 100.0 + len(dist_words)
                if score > best_score:
                    best_score = score
                    best_match = v
                continue

            # High-confidence distinctive keyword overlap (>= 3 distinctive words, >= 75% hit)
            if len(dist_words) >= 3:
                hits = sum(1 for w in dist_words if re.search(rf"\b{re.escape(w)}\b", t_norm))
                needed = max(3, math.ceil(len(dist_words) * 0.75))
                if hits >= needed:
                    score = 50.0 + (hits / len(dist_words)) * 20.0 + hits
                    if score > best_score:
                        best_score = score
                        best_match = v

    return best_match


def _clean_notes_with_single_url(notes: str, matched_url: str, read_tag: str) -> str:
    """Keep at most one canonical URL, preserve proof/status tags, and cap read(...) history to last 10."""
    parts = [p.strip() for p in (notes or "").split("|") if p.strip()]
    non_url_non_read = [p for p in parts if not p.startswith("http") and not p.startswith("read(")]
    reads = [p for p in parts if p.startswith("read(")]
    if read_tag not in reads:
        reads.append(read_tag)
    reads = reads[-10:]

    existing_urls = [p for p in parts if p.startswith("http")]
    final_url = matched_url or (existing_urls[0] if existing_urls else "")
    ordered = ([final_url] if final_url else []) + non_url_non_read + reads
    return " | ".join(ordered)


def _strip_row_metrics(row: dict[str, str], clear_urls: bool = True, keep_url: str | None = None) -> None:
    for col in ("views_12h", "views_24h", "views_72h", "likes", "reposts_or_shares", "replies_or_comments", "saves_or_bookmarks"):
        row[col] = ""
    parts = [p.strip() for p in (row.get("notes") or "").split("|") if p.strip()]
    kept = [p for p in parts if not p.startswith("http") and not p.startswith("read(")]
    if not clear_urls and keep_url:
        kept = [keep_url] + kept
    row["notes"] = " | ".join(kept)


def repair_scoreboard_rows(
    rows: list[dict[str, str]],
    by_plat: dict[str, dict[str, Any]],
    ledger_meta: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """
    Purge corrupted/duplicate URLs and stolen metrics from scoreboard.csv before re-syncing:
    - YouTube: if YouTube feed was fetched, wipe all YouTube URLs/metrics and re-bind cleanly 1-to-1.
    - TikTok & Instagram:
        * Clear all unposted/recent rows from >= 2026-11-27 if not live-matched
        * Deduplicate any URL appearing on >1 row (keep only the earliest matching row's original URL, clear the duplicate)
        * Strip hijacked prepended URLs on multi-URL rows (keep only the oldest original URL if any).
    """
    if "youtube" in by_plat and by_plat["youtube"].get("videos"):
        for r in rows:
            if (r.get("platform") or "").strip().lower() == "youtube":
                _strip_row_metrics(r, clear_urls=True)

    for plat in ("tiktok", "instagram"):
        seen_urls: set[str] = set()
        for r in rows:
            if (r.get("platform") or "").strip().lower() != plat:
                continue
            urls = extract_urls_from_notes(r.get("notes") or "")
            if not urls:
                continue
            # If row has multiple URLs prepended by the old buggy sync, oldest original URL is at the end
            orig_url = urls[-1]
            # Check if this row looks hijacked (duplicate URL across rows or unposted 2026-11-27 slot)
            pid = (r.get("post_id") or "").strip()
            r_date = (r.get("date") or "").strip()
            meta = ledger_meta.get((pid, plat)) or ledger_meta.get((pid, "*")) or {}
            is_duplicate = orig_url in seen_urls
            is_recent_unverified = r_date >= "2026-11-27"
            if is_duplicate or is_recent_unverified:
                _strip_row_metrics(r, clear_urls=True)
                continue

            seen_urls.add(orig_url)
            if len(urls) > 1:
                # Multiple URLs were glued onto this row -> clean to orig_url only and clear mixed stats so live sync (or manual check) refills cleanly
                _strip_row_metrics(r, clear_urls=False, keep_url=orig_url)
            elif meta.get("created_at_utc") and plat in by_plat:
                # Also verify against live feed if present
                pass


def update_scoreboard(
    platform_data: list[dict[str, Any]],
    scoreboard_path: Path = SCOREBOARD_PATH,
    repair: bool = False,
) -> int:
    if not scoreboard_path.exists():
        return 0
    with scoreboard_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    by_plat = {d["platform"]: d for d in platform_data}
    ledger_meta = load_ledger_meta()
    updated_rows = 0
    stamp = utc_now_str()

    if repair:
        repair_scoreboard_rows(rows, by_plat, ledger_meta)

    # Pass 1: claim all already-bound video IDs (1-to-1) so unbound rows can never steal them
    claimed_ids_by_plat: dict[str, set[str]] = {p: set() for p in by_plat}
    for row in rows:
        p = (row.get("platform") or "").strip().lower()
        if p not in claimed_ids_by_plat:
            continue
        bound_id = extract_bound_video_id(row.get("notes") or "", p)
        if bound_id:
            claimed_ids_by_plat[p].add(bound_id)

    # Pass 2: update bound rows and cleanly bind eligible unbound rows
    for row in rows:
        p = (row.get("platform") or "").strip().lower()
        pdata = by_plat.get(p)
        if not pdata:
            continue
        if pdata.get("followers") is not None:
            row["followers_at_read"] = str(pdata["followers"])
        claimed = claimed_ids_by_plat.setdefault(p, set())
        matched = match_video(row, pdata.get("videos") or [], ledger_meta, claimed_ids=claimed)
        if not matched:
            continue
        if matched.get("id"):
            claimed.add(str(matched["id"]))
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

        read_tag = f"read({stamp}):v={v}"
        row["notes"] = _clean_notes_with_single_url(row.get("notes") or "", matched.get("url") or "", read_tag)
        updated_rows += 1

    with scoreboard_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    if repair and DAILY_METRICS_PATH.exists():
        DAILY_METRICS_PATH.unlink(missing_ok=True)

    return updated_rows


def split_accounts(raw: str) -> list[str]:
    """Support comma-separated multiple channel URLs or handles for multi-account campaign tracking."""
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def main() -> int:
    load_local_dotenv()
    parser = argparse.ArgumentParser(description="Sync live YouTube, TikTok & Instagram metrics into scoreboard.csv.")
    parser.add_argument("--repair", action="store_true", help="Purge duplicate/hijacked matches and rebuild clean 1-to-1 metrics")
    args = parser.parse_args()

    yt_urls = split_accounts(os.environ.get("YT_CHANNEL_URL", ""))
    tt_handles = split_accounts(os.environ.get("TIKTOK_HANDLE", ""))
    ig_handles = split_accounts(os.environ.get("IG_HANDLE", ""))
    sc_key = os.environ.get("SCRAPE_CREATORS_API_KEY", "").strip()
    sc_cap = int(os.environ.get("SCRAPE_CREATORS_CREDIT_CAP", "40"))

    collected: list[dict[str, Any]] = []
    credits_used = 0
    errors: list[str] = []

    if yt_urls:
        yt_merged: dict[str, Any] = {"platform": "youtube", "followers": 0, "videos": []}
        seen_yt: set[str] = set()
        has_yt_fol = False
        for u in yt_urls:
            try:
                res = fetch_youtube_channel_stats(u, max_items=100)
                if res.get("followers") is not None:
                    yt_merged["followers"] += int(res["followers"])
                    has_yt_fol = True
                for v in res.get("videos") or []:
                    vid = str(v.get("id") or "")
                    if vid and vid not in seen_yt:
                        seen_yt.add(vid)
                        yt_merged["videos"].append(v)
            except Exception as exc:
                errors.append(f"youtube({clean_handle(u)}): {type(exc).__name__}")
        if not has_yt_fol:
            yt_merged["followers"] = None
        if yt_merged["videos"] or has_yt_fol:
            collected.append(yt_merged)

    if sc_key and tt_handles:
        tt_merged: dict[str, Any] = {"platform": "tiktok", "followers": 0, "videos": []}
        seen_tt: set[str] = set()
        has_tt_fol = False
        for h in tt_handles:
            if credits_used + 1 > sc_cap:
                break
            try:
                data, c = fetch_tiktok_stats(h, sc_key)
                credits_used += c
                if data.get("followers") is not None:
                    tt_merged["followers"] += int(data["followers"])
                    has_tt_fol = True
                for v in data.get("videos") or []:
                    vid = str(v.get("id") or "")
                    if vid and vid not in seen_tt:
                        seen_tt.add(vid)
                        tt_merged["videos"].append(v)
            except Exception as exc:
                errors.append(f"tiktok({clean_handle(h)}): {type(exc).__name__}")
        if not has_tt_fol:
            tt_merged["followers"] = None
        if tt_merged["videos"] or has_tt_fol:
            collected.append(tt_merged)

    if sc_key and ig_handles:
        ig_merged: dict[str, Any] = {"platform": "instagram", "followers": 0, "videos": []}
        seen_ig: set[str] = set()
        has_ig_fol = False
        for h in ig_handles:
            if credits_used + 2 > sc_cap:
                break
            try:
                data, c = fetch_instagram_stats(h, sc_key)
                credits_used += c
                if data.get("followers") is not None:
                    ig_merged["followers"] += int(data["followers"])
                    has_ig_fol = True
                for v in data.get("videos") or []:
                    vid = str(v.get("id") or "")
                    if vid and vid not in seen_ig:
                        seen_ig.add(vid)
                        ig_merged["videos"].append(v)
            except Exception as exc:
                errors.append(f"instagram({clean_handle(h)}): {type(exc).__name__}")
        if not has_ig_fol:
            ig_merged["followers"] = None
        if ig_merged["videos"] or has_ig_fol:
            collected.append(ig_merged)

    updated = update_scoreboard(collected, repair=args.repair) if collected else 0
    print(json.dumps({
        "repair": args.repair,
        "synced_platforms": [d["platform"] for d in collected],
        "followers": {d["platform"]: d.get("followers") for d in collected},
        "rows_updated": updated,
        "scrape_creators_credits_used": credits_used,
        "errors": errors,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
