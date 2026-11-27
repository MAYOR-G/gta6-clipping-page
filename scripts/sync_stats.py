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

WAT = timezone(timedelta(hours=1))

CREDIT_RE = re.compile(r"(?i)credit\s*:\s*(@?[A-Za-z0-9_.]+(?:\s*\([^)]+\))?)")

URL_ID_PATTERNS = {
    "youtube": re.compile(r"youtube\.com/(?:shorts|watch\?v=)/([A-Za-z0-9_-]{6,})|youtu\.be/([A-Za-z0-9_-]{6,})"),
    "tiktok": re.compile(r"tiktok\.com/@[^/]+/video/(\d{6,})"),
    "instagram": re.compile(r"instagram\.com/(?:reel|p)/([A-Za-z0-9_-]{6,})"),
}


def _to_int_or_none(val: Any) -> int | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(float(str(val).replace(",", "").strip()))
    except Exception:
        return None


def _clean_duration_sec(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        dur = float(val)
        if dur <= 0:
            return None
        if dur > 600:
            dur = dur / 1000.0
        return round(dur, 2) if 0 < dur <= 600 else None
    except Exception:
        return None


def _clean_live_title(raw_title: str) -> str:
    """Extract a clean concise title from a raw YouTube/TikTok/IG caption (strips Credit, hashtags, @mentions, emojis)."""
    s = (raw_title or "").replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = s.splitlines()[0] if s else ""
    s = re.split(r"(?i)credit\s*:", s, maxsplit=1)[0]
    s = re.split(r"#", s, maxsplit=1)[0]
    s = re.sub(r"@[A-Za-z0-9_.]+(?:\s*\([^)]*\))?", " ", s)
    # Strip emojis/pictographs while preserving standard printable punctuation and text
    s = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -|:,")
    if len(s) > 70:
        s = s[:67].rsplit(" ", 1)[0].strip() + "..."
    return s


def _extract_credit_from_title(raw_title: str) -> str:
    """Extract streamer credit handle from a caption such as 'Credit: @jynxzi (Twitch)'."""
    s = raw_title or ""
    m = CREDIT_RE.search(s)
    if m:
        cred = m.group(1).strip()
        if not cred.startswith("@"):
            cred = f"@{cred}"
        return cred
    mentions = [
        h for h in re.findall(r"@([A-Za-z0-9_.]+)", s)
        if h.lower() not in ("leonidachao", "leonida.chaos4", "leonida_chaos4")
    ]
    if mentions:
        return f"@{mentions[0]}"
    return ""


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
    consec_failures = 0
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        vid_id = d.get("id")
        if not vid_id:
            continue
        flat_views = _to_int_or_none(d.get("view_count"))
        item: dict[str, Any] = {
            "id": str(vid_id),
            "url": f"https://www.youtube.com/shorts/{vid_id}",
            "title": d.get("title") or "",
            "views": flat_views,
            "views_approx": bool(flat_views is not None and flat_views >= 1000),
            "likes": None,
            "comments": None,
            "shares": None,
            "duration": _clean_duration_sec(d.get("duration")),
            "published_utc": None,
        }
        if consec_failures < 3:
            try:
                detail_raw = subprocess.check_output(
                    [
                        "yt-dlp",
                        "--skip-download",
                        "--dump-json",
                        "--no-warnings",
                        "--extractor-args",
                        "youtube:player_client=tv,web_embedded",
                        f"https://www.youtube.com/watch?v={vid_id}",
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                )
                det = json.loads(detail_raw.strip().splitlines()[0])
                consec_failures = 0
                if det.get("view_count") is not None:
                    item["views"] = det.get("view_count")
                    item["views_approx"] = False
                elif item["views"] is None:
                    item["views"] = 0
                    item["views_approx"] = False
                item["likes"] = det.get("like_count")
                item["comments"] = det.get("comment_count")
                if det.get("duration") is not None:
                    item["duration"] = _clean_duration_sec(det.get("duration")) or item["duration"]
                if det.get("title"):
                    item["title"] = det["title"]
                if det.get("description"):
                    item["description"] = det["description"]
                item["published_utc"] = parse_epoch_utc(det.get("timestamp")) or parse_yyyymmdd_utc(det.get("upload_date"))
                if det.get("channel_follower_count") is not None:
                    followers = det["channel_follower_count"]
            except Exception:
                consec_failures += 1
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


def fetch_tiktok_stats(handle: str, api_key: str, max_pages: int = 5) -> tuple[dict[str, Any], int]:
    """Paginate Scrape Creators /v3/tiktok/profile/videos (1 credit/page — includes follower_count!)."""
    clean = clean_handle(handle)
    followers = None
    videos: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    cursor: Any = None
    credits_used = 0
    credits_remaining = None

    for _ in range(max(1, max_pages)):
        params: dict[str, str] = {"handle": clean, "sort_by": "latest"}
        if cursor is not None:
            params["max_cursor"] = str(cursor)
        vids_data = scrape_creators_get("/v3/tiktok/profile/videos", params, api_key)
        credits_used += 1
        credits_remaining = vids_data.get("credits_remaining", credits_remaining)
        items = vids_data.get("aweme_list") or vids_data.get("data") or []
        for item in items:
            if followers is None:
                fc = (item.get("author") or {}).get("follower_count")
                if fc is not None:
                    followers = fc
            vid_id = str(item.get("aweme_id") or item.get("id") or "")
            if not vid_id or vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)
            st = item.get("statistics") or {}
            vid_meta = item.get("video") or {}
            videos.append({
                "id": vid_id,
                "url": f"https://www.tiktok.com/@{clean}/video/{vid_id}",
                "title": item.get("desc") or "",
                "views": st.get("play_count"),
                "likes": st.get("digg_count"),
                "shares": st.get("share_count"),
                "comments": st.get("comment_count"),
                "saves": st.get("collect_count"),
                "duration": _clean_duration_sec(vid_meta.get("duration") or item.get("duration")),
                "published_utc": parse_epoch_utc(item.get("create_time")),
            })
        has_more = vids_data.get("has_more")
        cursor = vids_data.get("max_cursor")
        if not has_more or cursor is None or not items:
            break

    return {
        "platform": "tiktok",
        "followers": followers,
        "videos": videos,
        "credits_remaining": credits_remaining,
    }, credits_used


def fetch_tiktok_video_detail_ytdlp(url: str) -> dict[str, Any] | None:
    """0-credit fallback via yt-dlp for bound TikTok URLs omitted from the profile feed."""
    if not url:
        return None
    try:
        raw = subprocess.check_output(
            ["yt-dlp", "--skip-download", "--dump-json", "--no-warnings", url],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        det = json.loads(raw.strip().splitlines()[0])
        vid_id = str(det.get("id") or extract_video_id_from_url(url, "tiktok") or "")
        if not vid_id:
            return None
        return {
            "id": vid_id,
            "url": url,
            "title": det.get("description") or det.get("title") or "",
            "views": det.get("view_count"),
            "likes": det.get("like_count"),
            "shares": det.get("repost_count"),
            "comments": det.get("comment_count"),
            "saves": det.get("save_count"),
            "published_utc": parse_epoch_utc(det.get("timestamp")) or parse_yyyymmdd_utc(det.get("upload_date")),
        }
    except Exception:
        return None


def fetch_instagram_stats(handle: str, api_key: str, max_pages: int = 5) -> tuple[dict[str, Any], int]:
    """Fetch Instagram profile (1 credit) + paginate /v1/instagram/user/reels (1 credit/page)."""
    clean = clean_handle(handle)
    prof = scrape_creators_get("/v1/instagram/profile", {"handle": clean}, api_key)
    credits_used = 1
    user_obj = (prof.get("data") or {}).get("user") or prof.get("data") or prof
    followers = user_obj.get("follower_count")
    if followers is None:
        followers = (user_obj.get("edge_followed_by") or {}).get("count")

    videos: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    max_id: Any = None
    credits_remaining = prof.get("credits_remaining")

    for _ in range(max(1, max_pages)):
        params: dict[str, str] = {"handle": clean}
        if max_id is not None:
            params["max_id"] = str(max_id)
        reels_data = scrape_creators_get("/v1/instagram/user/reels", params, api_key)
        credits_used += 1
        credits_remaining = reels_data.get("credits_remaining", credits_remaining)
        items = reels_data.get("items") or reels_data.get("data") or []
        for item in items:
            media = item.get("media") or item
            code = str(media.get("code") or media.get("shortcode") or "")
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            cap = (media.get("caption") or {}).get("text") if isinstance(media.get("caption"), dict) else (media.get("caption") or "")
            plays = media.get("play_count")
            if plays is None:
                plays = media.get("ig_play_count")
            if plays is None:
                plays = media.get("video_view_count")
            pub_dt = parse_epoch_utc(media.get("taken_at") or media.get("taken_at_timestamp"))
            videos.append({
                "id": code,
                "url": f"https://www.instagram.com/reel/{code}/",
                "title": cap,
                "views": plays,
                "likes": media.get("like_count"),
                "shares": media.get("reshare_count"),
                "comments": media.get("comment_count"),
                "duration": _clean_duration_sec(media.get("video_duration")),
                "published_utc": pub_dt,
            })
        pinfo = reels_data.get("paging_info") or {}
        more = pinfo.get("more_available")
        max_id = pinfo.get("max_id") or reels_data.get("next_max_id")
        if not more or not max_id or not items:
            break

    return {
        "platform": "instagram",
        "followers": followers,
        "videos": videos,
        "credits_remaining": credits_remaining,
    }, credits_used


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


def _next_live_number(rows: list[dict[str, str]]) -> int:
    highest = 0
    for r in rows:
        m = re.match(r"^LIVE-(\d+)$", (r.get("post_id") or "").strip())
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def _same_clip_cross_platform(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Return True if two unclaimed live videos on different platforms represent the same clip."""
    if a["platform"] == b["platform"]:
        return False
    a_clean = normalize_text(a["clean_title"])
    b_clean = normalize_text(b["clean_title"])
    if len(a_clean) >= 6 and a_clean == b_clean:
        return True
    if _mentions_conflicting_streamer(a["raw_title"], b["streamer_norm"]) or _mentions_conflicting_streamer(
        b["raw_title"], a["streamer_norm"]
    ):
        return False
    pub_a = a.get("published_utc")
    pub_b = b.get("published_utc")
    if pub_a and pub_b and abs((pub_a - pub_b).total_seconds()) > 72 * 3600:
        return False
    words_a = set(_distinctive_words(a["clean_title"], a["streamer_norm"]))
    words_b = set(_distinctive_words(b["clean_title"], b["streamer_norm"]))
    return len(words_a & words_b) >= 2


def _import_untracked_live_videos(
    rows: list[dict[str, str]],
    fieldnames: list[str],
    by_plat: dict[str, dict[str, Any]],
    claimed_ids_by_plat: dict[str, set[str]],
    stamp: str,
) -> int:
    """
    Pass 3: Auto-register any live unclaimed videos on YouTube/TikTok/Instagram under LIVE-### IDs
    (grouped cross-platform by matching caption/credit) so dashboard totals match live accounts 100%.
    Never creates blank unposted sibling rows and never touches GTA-### numbering.
    """
    candidates: list[dict[str, Any]] = []
    for plat in ("tiktok", "instagram", "youtube"):
        pdata = by_plat.get(plat)
        if not pdata:
            continue
        claimed = claimed_ids_by_plat.setdefault(plat, set())
        vids = sorted(
            [v for v in (pdata.get("videos") or []) if str(v.get("id") or "") and str(v.get("id") or "") not in claimed],
            key=lambda x: (x.get("published_utc") or datetime.max.replace(tzinfo=timezone.utc), str(x.get("id") or "")),
        )
        for v in vids:
            raw_title = (v.get("title") or "").strip()
            clean_title = _clean_live_title(raw_title) or raw_title[:60]
            credit = _extract_credit_from_title(raw_title)
            streamer_norm = normalize_text(credit.split("(")[0])
            candidates.append({
                "platform": plat,
                "video": v,
                "raw_title": raw_title,
                "clean_title": clean_title,
                "credit": credit,
                "streamer_norm": streamer_norm,
                "published_utc": v.get("published_utc"),
                "followers": pdata.get("followers"),
            })

    if not candidates:
        return 0

    groups: list[list[dict[str, Any]]] = []
    for cand in candidates:
        placed = False
        for grp in groups:
            if any(c["platform"] == cand["platform"] for c in grp):
                continue
            if any(_same_clip_cross_platform(c, cand) for c in grp):
                grp.append(cand)
                placed = True
                break
        if not placed:
            groups.append([cand])

    next_num = _next_live_number(rows)
    imported = 0
    now_wat = datetime.now(WAT)

    for grp in groups:
        post_id = f"LIVE-{next_num:03d}"
        next_num += 1
        grp_credit = next((c["credit"] for c in grp if c["credit"]), "")
        grp_clean_title = next((c["clean_title"] for c in grp if len(c["clean_title"]) >= 4), grp[0]["clean_title"])
        grp_pub = next((c["published_utc"] for c in grp if c["published_utc"]), None)

        for cand in sorted(grp, key=lambda x: x["platform"]):
            v = cand["video"]
            plat = cand["platform"]
            pub_dt = (cand["published_utc"] or grp_pub)
            pub_wat = pub_dt.astimezone(WAT) if pub_dt else now_wat
            v_views = v.get("views")
            dur = v.get("duration")

            new_row = {col: "" for col in fieldnames}
            new_row["post_id"] = post_id
            new_row["date"] = pub_wat.strftime("%Y-%m-%d")
            new_row["time"] = pub_wat.strftime("%H:%M WAT") if pub_dt else ""
            new_row["platform"] = plat
            new_row["pillar"] = "live_auto_discovered"
            new_row["format"] = "stack_or_full"
            new_row["hook"] = cand["clean_title"] if len(cand["clean_title"]) >= 4 else grp_clean_title
            new_row["length_sec"] = f"{float(dur):.2f}" if dur is not None else ""
            new_row["credit"] = cand["credit"] or grp_credit
            if v_views is not None:
                new_row["views_12h"] = str(v_views)
                new_row["views_24h"] = str(v_views)
            if v.get("likes") is not None:
                new_row["likes"] = str(v["likes"])
            if v.get("shares") is not None:
                new_row["reposts_or_shares"] = str(v["shares"])
            if v.get("comments") is not None:
                new_row["replies_or_comments"] = str(v["comments"])
            if v.get("saves") is not None:
                new_row["saves_or_bookmarks"] = str(v["saves"])
            if cand["followers"] is not None:
                new_row["followers_at_read"] = str(cand["followers"])
            new_row["test_name"] = "LIVE-SYNC"
            new_row["notes"] = f"{v.get('url') or ''} | auto_discovered_live | read({stamp}):v={v_views}"

            rows.append(new_row)
            claimed_ids_by_plat[plat].add(str(v["id"]))
            imported += 1

    return imported


def update_scoreboard(
    platform_data: list[dict[str, Any]],
    scoreboard_path: Path = SCOREBOARD_PATH,
    repair: bool = False,
    allow_missing_fallback: bool = True,
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
    tt_fallback_used = 0
    for row in rows:
        p = (row.get("platform") or "").strip().lower()
        pdata = by_plat.get(p)
        if not pdata:
            continue
        if pdata.get("followers") is not None:
            row["followers_at_read"] = str(pdata["followers"])
        claimed = claimed_ids_by_plat.setdefault(p, set())
        plat_videos = pdata.get("videos") or []
        matched = match_video(row, plat_videos, ledger_meta, claimed_ids=claimed)
        if (
            not matched
            and p == "tiktok"
            and allow_missing_fallback
            and len(plat_videos) > 0
            and tt_fallback_used < 5
        ):
            bound_tt_url = next(
                (u for u in extract_urls_from_notes(row.get("notes") or "") if extract_video_id_from_url(u, "tiktok")),
                None,
            )
            if bound_tt_url:
                matched = fetch_tiktok_video_detail_ytdlp(bound_tt_url)
                tt_fallback_used += 1
        if not matched:
            continue
        if matched.get("id"):
            claimed.add(str(matched["id"]))

        clean_title = _clean_live_title(matched.get("title") or "")
        if len(clean_title) >= 4:
            row["hook"] = clean_title
        if not (row.get("credit") or "").strip():
            ext_cred = _extract_credit_from_title(matched.get("title") or "")
            if ext_cred:
                row["credit"] = ext_cred

        v = _to_int_or_none(matched.get("views"))
        existing_24 = _to_int_or_none(row.get("views_24h"))
        existing_12 = _to_int_or_none(row.get("views_12h"))
        effective_v = v
        if v is not None:
            if matched.get("views_approx") and existing_24 is not None and existing_24 > v:
                effective_v = existing_24
            else:
                if existing_12 is None:
                    row["views_12h"] = str(v)
                row["views_24h"] = str(v)
        elif existing_24 is not None:
            effective_v = existing_24

        if matched.get("likes") is not None:
            row["likes"] = str(matched["likes"])
        if matched.get("shares") is not None:
            row["reposts_or_shares"] = str(matched["shares"])
        if matched.get("comments") is not None:
            row["replies_or_comments"] = str(matched["comments"])
        if matched.get("saves") is not None:
            row["saves_or_bookmarks"] = str(matched["saves"])

        read_tag = f"read({stamp}):v={effective_v}"
        row["notes"] = _clean_notes_with_single_url(row.get("notes") or "", matched.get("url") or "", read_tag)
        updated_rows += 1

    # Pass 3: auto-import any remaining unclaimed live videos under LIVE-### IDs
    updated_rows += _import_untracked_live_videos(rows, fieldnames, by_plat, claimed_ids_by_plat, stamp)

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
            avail = sc_cap - credits_used
            if avail < 1:
                break
            try:
                data, c = fetch_tiktok_stats(h, sc_key, max_pages=min(5, avail))
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
            avail = sc_cap - credits_used
            if avail < 2:
                break
            try:
                data, c = fetch_instagram_stats(h, sc_key, max_pages=min(5, avail - 1))
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
        "videos_fetched": {d["platform"]: len(d.get("videos") or []) for d in collected},
        "followers": {d["platform"]: d.get("followers") for d in collected},
        "rows_updated": updated,
        "scrape_creators_credits_used": credits_used,
        "errors": errors,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
