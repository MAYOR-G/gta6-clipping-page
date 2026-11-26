#!/usr/bin/env python3
"""
Automated Daily 10:00 AM WAT Cloud Pipeline (daily_cloud_pipeline.py)

Designed for 100% FREE execution on GitHub Actions (Ubuntu + ffmpeg + yt-dlp + Pillow):
  1. Sync live account/post stats into scoreboard.csv (scripts/sync_stats.py)
  2. Run stuck-post protocol & auto-pause checks (scripts/stuck_check.py --apply-pauses)
  3. Scout fresh (<24h, English) GTA 6 clips via free Twitch Helix API
     (requires TWITCH_CLIENT_ID & TWITCH_CLIENT_SECRET in GitHub Secrets)
  4. Write original hook, punchline, caption, and IG first comment via free OpenRouter AI
     (optional OPENROUTER_API_KEY) with strict guardrail validation + deterministic fallback
  5. Cut up to DAILY_CLIP_COUNT clips targeting ~20 seconds each via scripts/clip.py
     using verified per-streamer crop presets (data/streamer_crops.json)
  6. Validate & queue via scripts/safe_publish.py with spaced WAT posting slots (3h apart)
  7. Deliver every clip to Telegram in structured 5-part format via scripts/telegram_deliver.py:
       Header + Recommended WAT Slot -> Video MP4 -> Cover Image -> Caption -> IG First Comment
  8. Append rows to scoreboard.csv & log.md, rebuild dashboard/index.html, and clean up raw/MP4 files

Security: reads all keys strictly from environment variables and NEVER prints any secret.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import csv
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PIL import Image

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from ai_optimizer import STRATEGY_PATH, run_optimizer  # noqa: E402
from clip import EMOJI_RE, probe_duration, render_clip  # noqa: E402
from dashboard_data import build_dashboard_payload, update_dashboard_html  # noqa: E402
from stream_clipper_engine import (  # noqa: E402
    apply_campaign_caption_fixes,
    build_dead_air_free_keep_ranges,
    check_campaign_rules,
    detect_audio_peak_signals,
    detect_silences,
    score_moment_for_strangers,
    transcribe_with_words,
    words_to_kinetic_srt,
    write_clips_report_md,
)
from stuck_check import run_health_check  # noqa: E402
from telegram_deliver import (  # noqa: E402
    deliver_bundle,
    get_today_drop_status,
    load_sent_log,
    record_daily_drop_attempt,
    save_sent_log,
    send_message,
)

WAT = timezone(timedelta(hours=1))
DATA_DIR = ROOT_DIR / "data"
SEEN_PATH = DATA_DIR / "seen_clips.json"
CROPS_PATH = DATA_DIR / "streamer_crops.json"
CAMPAIGNS_PATH = DATA_DIR / "campaigns.json"
LAST_REPORT_PATH = DATA_DIR / "last_clip_report.json"
CLIPS_REPORT_MD = ROOT_DIR / "clips" / "report.md"
SCOREBOARD_PATH = ROOT_DIR / "scoreboard.csv"
LOG_PATH = ROOT_DIR / "log.md"
RAW_DIR = ROOT_DIR / "raw" / "cloud"
SPECS_DIR = ROOT_DIR / "specs" / "cloud"
RENDERS_DIR = ROOT_DIR / "renders" / "cloud"
PROOFS_DIR = ROOT_DIR / "proofs"

# Recommended WAT posting slots starting 3h+ after 10:00 AM WAT (avoids 05:30-07:00 WAT)
DAILY_WAT_SLOTS = [
    (13, 30),
    (16, 30),
    (19, 30),
    (22, 30),
    (1, 30),   # next day
    (4, 30),   # next day
    (7, 30),   # next day
    (10, 30),  # next day
]

UNSAFE_TITLE_RE = re.compile(
    r"\b(nsfw|naked|strip|strip club|lap dance|private dance|bricked|sex|porn|boob|titty|onlyfans|ass|slur|leak|datamine|spoiler|ending|final mission|giveaway|no commentary|full game walkthrough)\b",
    flags=re.IGNORECASE,
)

EXCLUDED_CHANNELS = {"mkiceandfire", "ug_z", "ugz"}

# Default zero-key Twitch streamer watchlist (downloads cleanly on GitHub Actions with ZERO YouTube bot blocks!)
# Override or expand anytime via GitHub Repository Variable: TWITCH_STREAMERS="ohnepixel,xqc,caseoh_,..."
DEFAULT_TWITCH_STREAMERS = [
    "caseoh_",
    "kaicenat",
    "xqc",
    "ohnepixel",
    "jynxzi",
    "squeex",
    "fanum",
    "summit1g",
    "agent00",
    "jasontheween",
    "lirik",
    "lacy",
    "esfandtv",
    "hasanabi",
]

# YouTube-primary / YouTube-exclusive GTA 6 streamers (NOT primarily on Twitch — used with --source youtube locally on Mac!)
YOUTUBE_ONLY_STREAMERS = [
    "IShowSpeed",
    "TypicalGamer",
    "DarkViperAU",
    "Valkyrae",
    "Ludwig",
    "TimTheTatman",
    "CouRage",
    "Caylus",
    "Wynnsanity",
    "LazarBeam",
    "Berleezy",
    "RDCworldGaming",
    "Slogo",
    "DrDisrespect",
]

# Local-only fallback YouTube searches (skipped in cloud when Twitch direct already supplies clips)
YT_SCOUT_QUERIES = [
    "ishowspeed gta 6 funny clip",
    "typical gamer gta 6 funny clip",
    "darkviperau gta 6 funny clip",
    "valkyrae gta 6 funny clip",
    "ludwig gta 6 funny clip",
    "timthetatman gta 6 funny clip",
    "couragejd gta 6 funny clip",
    "caylus gta 6 funny",
    "wynnsanity gta 6 funny",
    "lazarbeam gta 6 funny",
    "berleezy gta 6 funny",
    "rdcworldgaming gta 6 funny",
]


def load_local_dotenv() -> None:
    """Load local .env when running on Mac (no-op in GitHub Actions where env vars come from Secrets)."""
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def next_post_number() -> int:
    highest = 0
    if SCOREBOARD_PATH.exists():
        with SCOREBOARD_PATH.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                m = re.match(r"GTA-(\d+)", (row.get("post_id") or "").strip())
                if m:
                    highest = max(highest, int(m.group(1)))
    ledger = load_json(DATA_DIR / "publish_ledger.json", {"entries": []})
    for e in ledger.get("entries", []):
        m = re.match(r"GTA-(\d+)", (e.get("post_id") or "").strip())
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def twitch_get_app_token(client_id: str, client_secret: str) -> str:
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode("utf-8")
    req = urllib.request.Request("https://id.twitch.tv/oauth2/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]


def twitch_api_get(path: str, params: dict[str, str], client_id: str, token: str) -> dict[str, Any]:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"https://api.twitch.tv/helix{path}?{qs}",
        headers={"Client-Id": client_id, "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def scout_twitch_gta6_clips(client_id: str, client_secret: str, limit: int = 30) -> list[dict[str, Any]]:
    """Fetch top English GTA 6 Twitch clips created within the last 24 hours."""
    token = twitch_get_app_token(client_id, client_secret)
    game_id = None
    for name in ("Grand Theft Auto VI", "GTA VI", "GTA 6"):
        res = twitch_api_get("/games", {"name": name}, client_id, token)
        items = res.get("data") or []
        if items:
            game_id = items[0]["id"]
            break
    if not game_id:
        raise RuntimeError("Could not resolve Twitch game_id for Grand Theft Auto VI.")

    now_utc = datetime.now(timezone.utc)
    started_at = (now_utc - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ended_at = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    clips_res = twitch_api_get(
        "/clips",
        {
            "game_id": game_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "first": str(min(limit, 100)),
        },
        client_id,
        token,
    )
    filtered = []
    for c in clips_res.get("data") or []:
        if (c.get("language") or "").lower() != "en":
            continue
        dur = float(c.get("duration") or 0)
        if dur < 18.0:
            continue
        title = c.get("title") or ""
        if UNSAFE_TITLE_RE.search(title):
            continue
        filtered.append(c)
    return filtered


def get_twitch_streamers() -> list[str]:
    streamers = list(DEFAULT_TWITCH_STREAMERS)
    extra = os.environ.get("TWITCH_STREAMERS", "").strip()
    if extra:
        for item in extra.split(","):
            h = item.strip().lstrip("@").lower()
            if h and h not in streamers:
                streamers.append(h)
    return streamers


def scout_twitch_direct_clips(
    limit: int = 20,
    min_duration: float = 24.0,
    priority_streamers: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Zero-key, zero-2FA parallel Twitch clip scouting via yt-dlp.
    Pulls top clips per streamer, sorts by REAL Twitch view_count DESC (certified viral moments first!),
    boosts AI-learned priority streamers from data/strategy.json, and interleaves round-robin.
    """
    seen_ids: set[str] = set()
    by_streamer: dict[str, list[dict[str, Any]]] = {}
    streamers = get_twitch_streamers()
    rng = random.Random(datetime.now(WAT).strftime("%Y-%m-%d"))
    rng.shuffle(streamers)
    if priority_streamers:
        prio_set = [s.lower() for s in priority_streamers if s.lower() in streamers]
        rest = [s for s in streamers if s not in prio_set]
        streamers = prio_set + rest

    def fetch_streamer(args_pair: tuple[str, str]) -> tuple[str, list[dict[str, Any]]]:
        handle, time_range = args_pair
        url = f"https://www.twitch.tv/{handle}/clips?filter=clips&range={time_range}"
        try:
            out = subprocess.check_output(
                ["yt-dlp", "--flat-playlist", "--playlist-end", "10", "--dump-json", "--no-warnings", url],
                text=True,
                timeout=15,
            )
        except Exception:
            return handle, []
        batch = []
        for line in out.strip().splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            clip_url = d.get("url") or d.get("webpage_url") or ""
            cid = str(d.get("id") or "")
            dur = float(d.get("duration") or 0)
            views = int(d.get("view_count") or 0)
            title = d.get("title") or ""
            if not cid or not clip_url or dur < min_duration or dur > 65.0:
                continue
            if UNSAFE_TITLE_RE.search(title):
                continue
            batch.append({
                "id": f"tw_{cid}",
                "broadcaster_name": handle,
                "source_platform": "Twitch",
                "title": title,
                "url": clip_url,
                "duration": dur,
                "view_count": views,
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
        # Rank by real Twitch view_count DESC so the most viral clips get checked FIRST!
        batch.sort(key=lambda x: (x["view_count"], -abs(x["duration"] - 24.0)), reverse=True)
        return handle, batch

    for time_range in ("24hr", "7d"):
        if sum(len(v) for v in by_streamer.values()) >= limit:
            break
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(fetch_streamer, [(h, time_range) for h in streamers]))
        for handle, batch in results:
            bucket = by_streamer.setdefault(handle, [])
            for item in batch:
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    bucket.append(item)

    # Round-robin interleave: 1 clip from every streamer before taking a 2nd from anyone
    interleaved: list[dict[str, Any]] = []
    max_depth = max((len(v) for v in by_streamer.values()), default=0)
    for idx in range(max_depth):
        for handle in streamers:
            bucket = by_streamer.get(handle) or []
            if idx < len(bucket):
                interleaved.append(bucket[idx])
                if len(interleaved) >= limit:
                    return interleaved
    return interleaved


def get_scout_queries() -> list[str]:
    """Combine default GTA 6 streamer queries with any custom EXTRA_SCOUT_QUERIES (comma-separated) for other viral niches."""
    queries = list(YT_SCOUT_QUERIES)
    extra = os.environ.get("EXTRA_SCOUT_QUERIES", "").strip()
    if extra:
        for item in extra.split(","):
            q = item.strip()
            if q and q not in queries:
                queries.append(q)
    return queries


def scout_youtube_gta6_clips(
    limit: int = 24,
    streamers: list[str] | None = None,
    min_duration: float = 24.0,
) -> list[dict[str, Any]]:
    """
    Zero-key FREE YouTube scouting via yt-dlp (best run locally on Mac where YouTube does not gate datacenter IPs).
    Supports tagging specific YouTube-exclusive streamers or randomly shuffling YOUTUBE_ONLY_STREAMERS round-robin.
    """
    seen_ids: set[str] = set()
    buckets: list[list[dict[str, Any]]] = []
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y%m%d")

    if streamers:
        target_names = [s.lstrip("@").strip() for s in streamers if s.strip()]
    else:
        target_names = list(YOUTUBE_ONLY_STREAMERS)
        rng = random.Random(f"yt-{datetime.now(WAT).strftime('%Y-%m-%d-%H')}")
        rng.shuffle(target_names)

    known_all = list(dict.fromkeys(target_names + YOUTUBE_ONLY_STREAMERS))

    for name in target_names:
        bucket: list[dict[str, Any]] = []
        for query in (f"ytsearch8:{name} gta 6 funny clip", f"ytsearch8:{name} gta 6 clip"):
            try:
                out = subprocess.check_output(
                    ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", query],
                    text=True,
                    timeout=25,
                )
            except Exception:
                continue

            for line in out.strip().splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                vid_id = d.get("id")
                dur = float(d.get("duration") or 0)
                title = d.get("title") or ""
                channel = d.get("channel") or d.get("uploader") or name
                ch_norm = re.sub(r"[^a-z0-9_]", "", channel.lower())
                if not vid_id or vid_id in seen_ids:
                    continue
                if dur < min_duration or dur > 240.0:
                    continue
                if ch_norm in EXCLUDED_CHANNELS or UNSAFE_TITLE_RE.search(title):
                    continue

                inferred = name
                for known in known_all:
                    if known.lower() in title.lower() or known.lower() in channel.lower():
                        inferred = known
                        break

                seen_ids.add(vid_id)
                bucket.append({
                    "id": f"yt_{vid_id}",
                    "broadcaster_name": inferred,
                    "source_platform": "YouTube",
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={vid_id}",
                    "duration": dur,
                    "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "min_upload_date": cutoff_date,
                })
            if bucket:
                break
        if bucket:
            bucket.sort(key=lambda x: abs(float(x.get("duration") or 36.0) - 38.0))
            buckets.append(bucket)
        if len(buckets) >= max(limit, 8):
            break

    # Interleave round-robin across streamers so each clip comes from a different YouTube creator
    interleaved: list[dict[str, Any]] = []
    max_len = max((len(b) for b in buckets), default=0)
    for idx in range(max_len):
        for b in buckets:
            if idx < len(b):
                interleaved.append(b[idx])
                if len(interleaved) >= limit:
                    return interleaved
    return interleaved


def clean_burned_text(text: str, fallback: str) -> str:
    cleaned = EMOJI_RE.sub("", text or "").replace("—", " ").replace("--", " ").strip()
    cleaned = re.sub(r"[^A-Za-z0-9 ':!.,?-]+", "", cleaned).strip()
    return (cleaned[:54].upper()) if cleaned else fallback


def build_4frame_strip_b64(video_path: Path, src_dur: float) -> str | None:
    """Extract 4 frames (20%, 45%, 70%, 90%) into a compact 2x2 JPEG base64 string for fast free vision verification."""
    try:
        grid = Image.new("RGB", (640, 360), (15, 15, 18))
        positions = [(0, 0), (320, 0), (0, 180), (320, 180)]
        fracs = (0.20, 0.45, 0.70, 0.90)
        for (px, py), frac in zip(positions, fracs):
            ts = max(0.3, min(src_dur - 0.3, src_dur * frac))
            raw = subprocess.check_output([
                "ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(video_path),
                "-frames:v", "1", "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2",
                "-f", "image2pipe", "-vcodec", "mjpeg", "-"
            ], stderr=subprocess.DEVNULL, timeout=15)
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            grid.paste(im, (px, py))
        buf = io.BytesIO()
        grid.save(buf, format="JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def probe_video_dimensions(video_path: Path) -> tuple[int, int]:
    """Return (width, height) of the video stream via ffprobe."""
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(video_path),
    ], text=True, timeout=15).strip()
    parts = out.splitlines()[0].split("x")
    return int(parts[0]), int(parts[1])


def build_grid_frame_b64(video_path: Path, src_dur: float) -> str | None:
    """Extract 1 clean mid-clip 640x360 frame with a labeled 10% yellow coordinate grid for razor-sharp facecam detection."""
    try:
        from PIL import ImageDraw  # local import keeps top-level imports light

        ts = max(0.5, min(src_dur - 0.5, src_dur * 0.5))
        raw = subprocess.check_output([
            "ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(video_path),
            "-frames:v", "1", "-vf", "scale=640:360",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-"
        ], stderr=subprocess.DEVNULL, timeout=15)
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        draw = ImageDraw.Draw(im)
        for pct in range(10, 100, 10):
            x = int(640 * pct / 100)
            y = int(360 * pct / 100)
            draw.line([(x, 0), (x, 360)], fill=(255, 255, 0), width=1)
            draw.line([(0, y), (640, y)], fill=(255, 255, 0), width=1)
            draw.rectangle([x - 10, 0, x + 12, 12], fill=(0, 0, 0))
            draw.text((x - 8, 1), str(pct), fill=(255, 255, 0))
            draw.rectangle([0, y - 6, 18, y + 6], fill=(0, 0, 0))
            draw.text((2, y - 5), str(pct), fill=(255, 255, 0))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=75)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def normalize_box_pct(raw_box: Any) -> list[float] | None:
    """Normalize [x1, y1, x2, y2] from 0..1, 0..100, or 0..1000 into valid 0..100 percentages."""
    if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
        return None
    try:
        vals = [float(v) for v in raw_box]
    except Exception:
        return None
    mx = max(vals)
    if mx <= 1.0:
        vals = [v * 100.0 for v in vals]
    elif mx > 100.0:
        vals = [v / 10.0 for v in vals]
    x1, y1, x2, y2 = [max(0.0, min(100.0, v)) for v in vals]
    w_pct, h_pct = x2 - x1, y2 - y1
    if w_pct < 10.0 or h_pct < 12.0 or w_pct > 52.0 or h_pct > 58.0:
        return None
    return [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)]


def detect_facecam_with_openrouter(grid_b64: str | None, streamer: str) -> dict[str, Any]:
    """Use free OpenRouter vision models on the 10% coordinate-grid frame to detect & locate a real human facecam."""
    default = {"has_facecam": False, "facecam_corner": "none", "facecam_box_pct": None, "checked": False}
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key or not grid_b64:
        return default

    primary_model = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free").strip()
    models = []
    for m in (
        primary_model,
        "nvidia/nemotron-3.5-super-vl:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "openrouter/free",
        "google/gemma-4-31b-it:free",
    ):
        if m and m not in models:
            models.append(m)

    prompt = (
        f"This 16:9 gaming frame from @{streamer} has a yellow 10% coordinate grid (0 to 100 along X left-to-right and Y top-to-bottom).\n"
        "Detect whether there is a REAL human streamer facecam/webcam box overlaid on the screen.\n"
        "Return STRICT JSON ONLY: {\"has_facecam\": bool, \"facecam_corner\": \"top-left|top-right|bottom-left|bottom-right|middle-left|middle-right|none\", \"facecam_box_pct\": [x1, y1, x2, y2] or null}.\n"
        "Rules:\n"
        "- Use the yellow 0-100 grid numbers to give the tight bounding box [x1, y1, x2, y2] of ONLY the real human streamer's webcam rectangle.\n"
        "- EXCLUDE chat overlays, donation banners outside the webcam, and EXCLUDE the game minimap!\n"
        "- If there is NO real human webcam overlay (pure gameplay or animated avatar only), set has_facecam=false and facecam_box_pct=null."
    )

    for model in models:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{grid_b64}"}},
            ]}],
            "temperature": 0.1,
            "reasoning": {"effort": "none"},
        }
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/MAYOR-G/gta6-clipping-page",
                    "X-Title": "Leonida Chaos Facecam Detector",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            raw = body["choices"][0]["message"].get("content") or ""
            if not raw.strip():
                continue
            m_json = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            parsed = json.loads(m_json.group(0) if m_json else raw)
            has_cam = bool(parsed.get("has_facecam", False))
            box_pct = normalize_box_pct(parsed.get("facecam_box_pct")) if has_cam else None
            return {
                "has_facecam": bool(has_cam and box_pct is not None),
                "facecam_corner": str(parsed.get("facecam_corner") or "none").strip().lower(),
                "facecam_box_pct": box_pct,
                "checked": True,
                "model_used": model,
            }
        except Exception:
            continue
    return default


def compute_stack_crops(src_w: int, src_h: int, box_pct: list[float]) -> tuple[list[int], list[int]]:
    """
    Convert a detected [x1, y1, x2, y2] percentage facecam box into exact pixel [x, y, w, h] crops
    for stack layout (top 40% = 1080x720 crisp streamer facecam, bottom 60% = 1080x1200 clean gameplay).
    """
    x1, y1, x2, y2 = box_pct
    # Trim ~1% inward to exclude harsh webcam border lines while keeping full face/headroom
    inset_x = min(1.0, (x2 - x1) * 0.04)
    inset_y = min(1.0, (y2 - y1) * 0.04)
    fx1 = int(src_w * max(0.0, x1 + inset_x) / 100.0)
    fy1 = int(src_h * max(0.0, y1 + inset_y) / 100.0)
    fx2 = int(src_w * min(100.0, x2 - inset_x) / 100.0)
    fy2 = int(src_h * min(100.0, y2 - inset_y) / 100.0)

    fw = max(120, (fx2 - fx1) // 2 * 2)
    fh = max(100, (fy2 - fy1) // 2 * 2)
    fx = max(0, min(src_w - fw, fx1 // 2 * 2))
    fy = max(0, min(src_h - fh, fy1 // 2 * 2))

    # Gameplay crop (target ~0.90 aspect ratio to match 1080x1200 bottom section, avoiding facecam & chat edges)
    gy = int(src_h * 0.04) // 2 * 2
    gh = int(src_h * 0.88) // 2 * 2
    target_gw = int(gh * 0.92) // 2 * 2  # close to 1080:1200 without over-zooming

    cam_cx = (x1 + x2) / 2.0
    if cam_cx < 50.0:
        # Facecam is on the LEFT -> push gameplay window to the right of the webcam, keeping right-side Twitch chat out
        safe_left = int(src_w * min(0.32, (x2 / 100.0) * 0.92))
        safe_right = int(src_w * 0.88)
    else:
        # Facecam is on the RIGHT -> keep gameplay on left/center clear of the webcam
        safe_left = int(src_w * 0.05)
        safe_right = int(src_w * max(0.68, (x1 / 100.0) * 1.02))

    avail_w = max(320, (safe_right - safe_left) // 2 * 2)
    gw = min(target_gw, avail_w) // 2 * 2
    gx = (safe_left + (avail_w - gw) // 2) // 2 * 2
    gx = max(0, min(src_w - gw, gx))
    return [fx, fy, fw, fh], [gx, gy, gw, gh]


def generate_copy_with_openrouter(
    streamer: str,
    clip_title: str,
    strip_b64: str | None = None,
    require_gta6: bool = True,
    source_platform: str = "Twitch",
    strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Use fast free OpenRouter vision-language models (nemotron-3.5-super-vl:free / nemotron-3-nano-omni / openrouter/free)
    with reasoning disabled for ~1.5s responses to SEE the actual 4 video frames, verify real GTA gameplay,
    score virality (1-10) & scene energy (rejecting boring phone/menu/idle clips), and write hooks/captions
    guided by data/strategy.json from the self-improving AI optimizer.
    """
    handle_clean = re.sub(r"[^A-Za-z0-9_]", "", streamer.lower()) or "streamer"
    fallback = {
        "is_gta6": True,
        "virality_score": 7,
        "scene_energy": "high_action",
        "reject_reason": "",
        "game_detected": "GTA 6" if require_gta6 else "Gaming",
        "visual_summary": f"Clip from @{streamer}: {clip_title}",
        "hook": clean_burned_text("GTA 6 CHAOS HIT INSTANTLY" if require_gta6 else "THIS STREAMER MOMENT WENT WILD", "THIS STREAMER MOMENT WENT WILD"),
        "punchline": "ZERO CHILL ON STREAM",
        "caption": f"{'gta 6' if require_gta6 else handle_clean} did not let him breathe 💀\n\nCredit: @{streamer} ({source_platform})\n\n#{'gta6' if require_gta6 else 'gaming'} #{handle_clean} #streamerclips #gaming #funnymoments",
        "ig_first_comment": "Be honest, how would you have reacted right there?",
        "send_test": "Send to your gaming friend: 'bro watch his face at the end'",
    }

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return fallback

    primary_model = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free").strip()
    vision_models = []
    for m in (
        primary_model,
        "nvidia/nemotron-3.5-super-vl:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "openrouter/free",
        "google/gemma-4-31b-it:free",
    ):
        if m and m not in vision_models:
            vision_models.append(m)
    text_fallbacks = ["nvidia/nemotron-3.5-lightning:free", "openrouter/free"]

    strat = strategy or {}
    win_str = "; ".join(strat.get("winning_patterns") or ["instant-karma crashes, chases, sudden fails, shocked reactions"])
    avoid_str = "; ".join(strat.get("avoid_patterns") or ["opening phone/menus, slow walking, idle talking with no payoff"])
    hook_guide = strat.get("hook_style_guidance") or "Lead with a punchy tension/irony hook that makes viewers stop scrolling."

    prompt = (
        f"You are reviewing a 2x2 grid of 4 sequential frames from a gaming stream clip (today is Nov 2026 and GTA 6 just launched).\n"
        f"Streamer: @{streamer} ({source_platform}). Original clip title: {clip_title!r}.\n"
        f"SELF-IMPROVING PAGE STRATEGY (learned from our live YouTube/TikTok/IG analytics):\n"
        f"- DOUBLE DOWN ON: {win_str}\n"
        f"- STRICTLY REJECT: {avoid_str}\n"
        f"- HOOK STYLE: {hook_guide}\n"
        f"CRITICAL ACCURACY & VIRALITY RULES:\n"
        f"1. Describe ONLY what is LITERALLY visible in the 4 frames. NEVER invent cars, driving, police, Lucia, Jason, or physics glitches if they are NOT actually visible on screen.\n"
        f"2. Set is_gta6=true if the frames show Grand Theft Auto open-world gameplay (cars/highways, Vice City/Leonida/city streets, GTA characters/NPCs, police, open-world third-person/first-person GTA action — treat any modern GTA gameplay as is_gta6=true).\n"
        f"3. Set is_gta6=false for football/soccer/FIFA pitches, sports games, CS/Valorant/FPS shooters, Fortnite, Minecraft/2D/horror games, pure IRL/Just Chatting streams without GTA gameplay, or menu-only screens.\n"
        f"4. BE STRICT ON VIRALITY (virality_score 1-10 & scene_energy):\n"
        f"   - Give 8-10 ('high_action' or 'funny_reaction') ONLY for visually gripping, chaotic, or laugh-out-loud moments (crashes, chases, explosions, wild NPC fights, robberies, hilarious physics fails, huge shocked streamer reaction).\n"
        f"   - Give 1-5 ('slow_menu' or 'boring_idle') for opening an in-game phone/map/UI, browsing menus/shops quietly, standing around talking, or slow walking/driving with no obvious chaos or visual payoff.\n"
        f"Return STRICT JSON ONLY with keys: is_gta6 (boolean), virality_score (int 1-10), scene_energy ('high_action'|'funny_reaction'|'slow_menu'|'boring_idle'), reject_reason (string, empty if good), game_detected (short string), visual_summary (1 short sentence of what is literally on screen), hook, punchline, caption_line, ig_first_comment, send_test.\n"
        f"Copy rules:\n"
        f"- hook: 4 to 7 words ALL CAPS, NO emoji, accurately matches what is ACTUALLY in the frames (include 'GTA 6' ONLY if is_gta6 is true).\n"
        f"- punchline: 2 to 5 words ALL CAPS, NO emoji, matching the actual reaction/moment.\n"
        f"- caption_line: 3 to 7 words lowercase friend's voice matching the REAL moment on screen, NO question mark, NO em dash, NO 'official'/'leak'/'giveaway', at most 1 emoji.\n"
        f"- ig_first_comment: 1 honest short question viewers can answer about themselves based on what actually happened.\n"
        f"- send_test: who a viewer sends this to and what they type.\n"
    )

    attempts_list: list[tuple[str, bool]] = [(m, True) for m in vision_models if strip_b64] + [(m, False) for m in text_fallbacks]
    for model, use_vision in attempts_list:
        if use_vision and strip_b64:
            content: Any = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{strip_b64}"}},
            ]
        else:
            content = prompt
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.35,
            "reasoning": {"effort": "none"},
        }
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/MAYOR-G/gta6-clipping-page",
                    "X-Title": "Leonida Chaos Vision Pipeline",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            raw = body["choices"][0]["message"].get("content") or ""
            if not raw.strip():
                continue
            m_json = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            parsed = json.loads(m_json.group(0) if m_json else raw)

            game_det = str(parsed.get("game_detected") or "Gaming").strip()
            if use_vision:
                is_gta6 = bool(parsed.get("is_gta6", False)) or ("gta" in game_det.lower()) or ("grand theft auto" in game_det.lower())
            else:
                is_gta6 = True
            try:
                v_score = max(1, min(10, int(parsed.get("virality_score") or 7)))
            except Exception:
                v_score = 7
            scene_energy = str(parsed.get("scene_energy") or "high_action").strip().lower()
            reject_reason = str(parsed.get("reject_reason") or "").strip()

            hook = clean_burned_text(parsed.get("hook", ""), fallback["hook"])
            punch = clean_burned_text(parsed.get("punchline", ""), fallback["punchline"])
            cap_line = (parsed.get("caption_line") or "bro did not expect that 💀").strip()
            cap_line = cap_line.replace("—", " ").replace("--", " ").rstrip("?").strip()
            if re.search(r"\b(official|leak|early|giveaway|free copy)\b", cap_line, re.I):
                cap_line = "bro did not expect that 💀"

            tag1 = "#gta6" if is_gta6 else f"#{re.sub(r'[^a-z0-9]', '', game_det.lower()) or 'gaming'}"
            tag3 = "#vicecity" if is_gta6 else ("#youtubeclips" if source_platform.lower() == "youtube" else "#twitchclips")
            caption = f"{cap_line}\n\nCredit: @{streamer} ({source_platform})\n\n{tag1} #{handle_clean} {tag3} #gaming #funnymoments"
            return {
                "is_gta6": is_gta6,
                "virality_score": v_score,
                "scene_energy": scene_energy,
                "reject_reason": reject_reason,
                "game_detected": "GTA 6" if is_gta6 else game_det,
                "visual_summary": str(parsed.get("visual_summary") or clip_title).strip(),
                "hook": hook,
                "punchline": punch,
                "caption": caption,
                "ig_first_comment": (parsed.get("ig_first_comment") or fallback["ig_first_comment"]).strip(),
                "send_test": (parsed.get("send_test") or fallback["send_test"]).strip(),
                "model_used": model,
            }
        except Exception:
            continue
    return fallback


def find_next_open_slot(now_wat: datetime, ledger_path: Path = DATA_DIR / "publish_ledger.json") -> tuple[str, str, str]:
    """Pick the earliest upcoming WAT slot that is >= 2.6h away from EVERY scheduled post in the ledger."""
    ledger = load_json(ledger_path, {"entries": []})
    booked_utc: list[datetime] = []
    for e in ledger.get("entries", []):
        ts = e.get("scheduled_time_utc")
        if ts:
            try:
                booked_utc.append(datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc))
            except Exception:
                pass

    # Scan the next 5 days of preferred WAT slots in chronological order
    candidates: list[datetime] = []
    for day_offset in range(0, 6):
        d = (now_wat + timedelta(days=day_offset)).date()
        for hour, minute in sorted(DAILY_WAT_SLOTS):
            dt_wat = datetime(d.year, d.month, d.day, hour, minute, tzinfo=WAT)
            if dt_wat > now_wat + timedelta(minutes=30):
                candidates.append(dt_wat)

    candidates.sort()
    for cand_wat in candidates:
        cand_utc = cand_wat.astimezone(timezone.utc)
        if all(abs((cand_utc - b).total_seconds()) >= 2.6 * 3600 for b in booked_utc):
            return cand_wat.isoformat(), cand_wat.strftime("%Y-%m-%d"), cand_wat.strftime("%H:%M WAT")

    fallback_wat = now_wat + timedelta(hours=3)
    return fallback_wat.isoformat(), fallback_wat.strftime("%Y-%m-%d"), fallback_wat.strftime("%H:%M WAT")


def download_clip(url: str, out_mp4: Path) -> Path:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--no-warnings",
        "-o", str(out_mp4),
        url,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    if proc.returncode != 0 or not out_mp4.exists():
        err_tail = (proc.stderr or proc.stdout or "unknown download error").strip().splitlines()[-1][:220]
        raise RuntimeError(err_tail)
    return out_mp4


def append_scoreboard_rows(
    post_id: str,
    date_str: str,
    time_str: str,
    hook: str,
    length_sec: float,
    credit: str,
    proof_rel: str,
    platforms: list[str] | None = None,
    pillar: str = "stream_clipper_18s",
    extra_note: str = "",
) -> None:
    if not SCOREBOARD_PATH.exists():
        return
    note_str = f"delivered_telegram | proof:{proof_rel}"
    if extra_note:
        note_str = f"{note_str} | {extra_note}"
    with SCOREBOARD_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for plat in (platforms or ["instagram", "tiktok", "youtube"]):
            writer.writerow([
                post_id, date_str, time_str, plat, pillar, "stack_or_full",
                hook, f"{length_sec:.2f}", credit,
                "", "", "", "", "", "", "", "", "EXP-R1",
                note_str,
            ])


def append_log_entry(
    post_id: str,
    slot_str: str,
    streamer: str,
    src_plat: str,
    source_url: str,
    created_at: str,
    copy: dict[str, Any],
    length_sec: float,
    proof_rel: str,
    signals: list[str] | None = None,
    has_captions: bool = False,
) -> None:
    sig_str = ", ".join(signals or ["viewer_clip"])
    entry = (
        f"\n### {post_id} — @{streamer} (Stream Clipper {length_sec:.1f}s)\n"
        f"- **Status:** Cut & Delivered to Telegram\n"
        f"- **Recommended Slot (WAT):** `{slot_str}`\n"
        f"- **Platforms:** instagram, tiktok, youtube\n"
        f"- **Duration:** {length_sec:.2f}s (1080x1920, 30 fps) | **Captions:** {'kinetic word-synced' if has_captions else 'hook+punchline'}\n"
        f"- **Stranger Score:** {copy.get('stranger_score', copy.get('virality_score', 7))}/10 | **Signals:** {sig_str}\n"
        f"- **Hook (Frame 1):** `{copy['hook']}`\n"
        f"- **YouTube Shorts Title:** `{copy.get('yt_title', '')}`\n"
        f"- **Punchline:** `{copy['punchline']}`\n"
        f"- **Source URL & Created At:** `{source_url}` ({created_at})\n"
        f"- **Credit:** `@{streamer} ({src_plat})`\n"
        f"- **Proof Frame:** `{proof_rel}`\n"
        f"- **Send Test:** {copy['send_test']}\n"
        f"- **IG First Comment:** `{copy['ig_first_comment']}`\n"
    )
    if LOG_PATH.exists():
        text = LOG_PATH.read_text(encoding="utf-8")
        marker = "## Scout Rejects Log"
        if marker in text:
            text = text.replace(marker, entry + "\n---\n\n" + marker, 1)
        else:
            text += entry
        LOG_PATH.write_text(text, encoding="utf-8")


def load_active_campaign(campaign_id: str) -> dict[str, Any] | None:
    if not campaign_id or not CAMPAIGNS_PATH.exists():
        return None
    try:
        data = json.loads(CAMPAIGNS_PATH.read_text(encoding="utf-8"))
        for camp in data.get("campaigns") or []:
            if str(camp.get("id") or "").strip().lower() == campaign_id.strip().lower():
                return camp
    except Exception:
        return None
    return None


def main() -> int:
    load_local_dotenv()
    parser = argparse.ArgumentParser(description="Daily WAT headless cloud & local YouTube-streamer pipeline.")
    parser.add_argument("--clip-count", type=int, default=int(os.environ.get("DAILY_CLIP_COUNT", "3")))
    parser.add_argument("--target-sec", type=str, default=os.environ.get("TARGET_CLIP_SEC", "18"),
                        help="Target clip duration in seconds (default: 18, or 'auto' to follow AI optimizer)")
    parser.add_argument("--source", choices=("auto", "twitch", "youtube"), default="auto",
                        help="Clip source: auto/twitch (cloud default) or youtube (local Mac YouTube-only streamers)")
    parser.add_argument("--streamers", type=str, default="",
                        help="Optional comma-separated streamer names/handles (e.g. 'IShowSpeed,TypicalGamer')")
    parser.add_argument("--niche", choices=("gta6", "gaming"), default=os.environ.get("CONTENT_NICHE", "gta6"),
                        help="Content niche: gta6 (strict GTA 6 filter) or gaming (broader viral gaming/campaign clips)")
    parser.add_argument("--campaign", type=str, default=os.environ.get("ACTIVE_CAMPAIGN_ID", ""),
                        help="Optional active campaign ID from data/campaigns.json")
    parser.add_argument("--slot-label", type=str, default="Manual / Ad-hoc",
                        help="Automation window label (e.g. '09:37 WAT (Primary)')")
    parser.add_argument("--force-drop", action="store_true",
                        help="Bypass daily idempotency gate and send clips even if today's WAT drop already ran")
    parser.add_argument("--dry-run", action="store_true", help="Do not send Telegram messages or mutate scoreboard")
    args = parser.parse_args()

    require_gta6 = (args.niche == "gta6") and (os.environ.get("REQUIRE_GTA6_ONLY", "true").strip().lower() not in ("0", "false", "no"))
    require_facecam = os.environ.get("REQUIRE_FACECAM", "true").strip().lower() not in ("0", "false", "no")
    active_campaign = load_active_campaign(args.campaign)
    tagged_streamers = [s.strip() for s in args.streamers.split(",") if s.strip()]
    now_wat = datetime.now(WAT)
    wat_date_str = now_wat.strftime("%Y-%m-%d")

    # 0. Daily Telegram Drop Idempotency Gate (multi-window fallback safety!)
    today_status = get_today_drop_status(wat_date_str)
    already_delivered = int(today_status.get("clips_delivered") or 0)
    target_clips = max(1, int(args.clip_count))
    if not args.dry_run and not args.force_drop:
        if today_status.get("status") == "completed" or already_delivered >= target_clips:
            print(
                f"[idempotency] Daily drop already completed for {wat_date_str} WAT "
                f"via {today_status.get('slot_used') or 'earlier window'} "
                f"({already_delivered} clips: {today_status.get('post_ids')}). Skipping duplicate run."
            )
            record_daily_drop_attempt(
                wat_date_str,
                args.slot_label,
                [],
                target_clips,
                "completed",
                note="Skipped duplicate window (already dropped today)",
            )
            payload = build_dashboard_payload()
            update_dashboard_html(payload)
            return 0
    remaining_target = target_clips if args.force_drop else max(1, target_clips - already_delivered)
    if already_delivered > 0 and not args.force_drop and not args.dry_run:
        print(f"[idempotency] Resuming partial drop for {wat_date_str} WAT: {already_delivered}/{target_clips} already sent, producing {remaining_target} more.")

    print(
        f"=== Pipeline Starting: {now_wat.isoformat()} (slot={args.slot_label!r}, source={args.source}, "
        f"niche={args.niche}, target={remaining_target} clips @ {args.target_sec}s, "
        f"require_gta6={require_gta6}, require_facecam={require_facecam}, campaign={args.campaign or 'none'}) ==="
    )

    # 1. Sync stats (skipped on --dry-run to save Scrape Creators credits)
    if not args.dry_run:
        subprocess.run([sys.executable, str(ROOT_DIR / "scripts/sync_stats.py")], check=False)

    # 1.5 Self-improving AI optimizer: analyze scoreboard.csv -> update data/strategy.json & learning.md
    strategy: dict[str, Any] = load_json(STRATEGY_PATH, {})
    try:
        strategy = run_optimizer()
        print(
            f"[ai-optimizer] Updated strategy ({strategy.get('model_used')}): "
            f"virality_gate>={strategy.get('min_virality_score', 7)} | "
            f"priority={strategy.get('priority_streamers', [])[:4]}"
        )
    except Exception as exc:
        print(f"[warn] AI optimizer skipped ({type(exc).__name__}); using saved strategy.", file=sys.stderr)

    env_min_v = os.environ.get("MIN_VIRALITY_SCORE", "").strip()
    min_virality_score = int(env_min_v) if env_min_v.isdigit() else int(strategy.get("min_virality_score", 7))
    if str(args.target_sec).strip().lower() == "auto":
        target_sec = float(strategy.get("recommended_target_sec", 18))
    else:
        target_sec = float(args.target_sec)
    target_sec = max(14.0, min(35.0, target_sec))
    if active_campaign and active_campaign.get("max_length_sec"):
        target_sec = min(target_sec, float(active_campaign["max_length_sec"]))
    print(f"[strategy-active] Effective target_sec={target_sec:.0f}s | min_stranger_score>={min_virality_score}/10")

    # 2. Health check (advisory by default so Telegram fallback windows keep working)
    health = run_health_check(apply_pauses=False)
    pause_state = health.get("pause_state") or {}
    now_utc = datetime.now(timezone.utc)

    active_platforms: list[str] = []
    if not pause_state.get("paused_all"):
        for plat in ("instagram", "tiktok", "youtube"):
            pinfo = (pause_state.get("platforms") or {}).get(plat) or {}
            if pinfo.get("paused"):
                until_s = pinfo.get("until_utc")
                if not until_s:
                    continue
                try:
                    if datetime.fromisoformat(until_s.replace("Z", "+00:00")) > now_utc:
                        continue
                except Exception:
                    continue
            active_platforms.append(plat)

    paused_platforms = [p for p in ("instagram", "tiktok", "youtube") if p not in active_platforms]
    if paused_platforms:
        print(f"[health] Paused platform(s) skipped this run: {paused_platforms} | Active: {active_platforms}")

    # 3. Scout fresh clips ONLY if at least one platform is active
    tw_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    tw_sec = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    tg_ready = bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() and os.environ.get("TELEGRAM_CHAT_ID", "").strip())

    produced: list[dict[str, Any]] = []
    report_items: list[dict[str, Any]] = []
    failure_reasons: list[str] = []
    seen = load_json(SEEN_PATH, {"clip_ids": []})
    seen_set = set(seen.get("clip_ids", []))
    crops = load_json(CROPS_PATH, {})
    candidates: list[dict[str, Any]] = []

    if not active_platforms:
        failure_reasons.append("All platforms are currently paused (check data/pause_state.json or run scripts/unpause.py).")
    elif args.source == "youtube":
        min_src = max(14.0, target_sec - 4.0)
        yt_cands = scout_youtube_gta6_clips(
            limit=max(16, remaining_target * 6),
            streamers=tagged_streamers or None,
            min_duration=min_src,
        )
        print(f"[scout:youtube-local] Found {len(yt_cands)} YouTube-streamer clips across {len({c['broadcaster_name'] for c in yt_cands})} creators.")
        candidates.extend(yt_cands)
    else:
        if tw_id and tw_sec and args.niche == "gta6":
            try:
                candidates = scout_twitch_gta6_clips(tw_id, tw_sec, limit=40)
                print(f"[scout:twitch-api] Found {len(candidates)} fresh (<24h, English, >=18s) GTA 6 Twitch clips.")
            except Exception as exc:
                print(f"[warn] Twitch API scout failed ({type(exc).__name__}); using zero-key Twitch direct scout.", file=sys.stderr)

        if len(candidates) < remaining_target * 4:
            min_src = max(14.0, target_sec - 4.0)
            tw_direct = scout_twitch_direct_clips(
                limit=max(30, remaining_target * 8),
                min_duration=min_src,
                priority_streamers=strategy.get("priority_streamers"),
            )
            print(f"[scout:twitch-direct] Found {len(tw_direct)} view-ranked round-robin Twitch clips across {len({c['broadcaster_name'] for c in tw_direct})} streamers.")
            candidates.extend(tw_direct)

        if args.source == "auto" and len(candidates) < remaining_target:
            yt_cands = scout_youtube_gta6_clips(limit=12)
            print(f"[scout:youtube] Found {len(yt_cands)} YouTube fallback clips.")
            candidates.extend(yt_cands)

    if candidates and active_platforms:
        post_num = next_post_number()
        attempts = 0
        max_attempts = max(14, remaining_target * 5)
        used_streamers_today: set[str] = set()

        for clip_meta in candidates:
            if len(produced) >= remaining_target or attempts >= max_attempts:
                break
            cid = clip_meta["id"]
            if cid in seen_set:
                continue

            streamer = clip_meta.get("broadcaster_name") or "streamer"
            streamer_key = streamer.lower()
            # Enforce max 1 clip per streamer per day so every daily drop has diverse creators!
            if streamer_key in used_streamers_today:
                continue
            attempts += 1

            src_plat = clip_meta.get("source_platform", "Twitch")
            clip_url = clip_meta.get("url") or f"https://clips.twitch.tv/{cid}"
            post_id = f"GTA-{post_num:03d}"

            try:
                raw_mp4 = RAW_DIR / f"{cid}.mp4"
                download_clip(clip_url, raw_mp4)
                src_dur = probe_duration(raw_mp4)
                if src_dur < max(13.5, target_sec - 4.5):
                    raw_mp4.unlink(missing_ok=True)
                    seen_set.add(cid)
                    continue

                # Signals collected for this clip (Twitch clip views + audio peak energy)
                clip_signals: list[str] = ["twitch_clip_views" if src_plat == "Twitch" else "youtube_search"]
                if int(clip_meta.get("view_count") or 0) >= 500:
                    clip_signals.append(f"views:{clip_meta['view_count']}")
                audio_peaks = detect_audio_peak_signals(raw_mp4)
                if audio_peaks:
                    clip_signals.append("audio_peak")

                # Transcribe speech with word timestamps for kinetic captions + stranger scoring
                transcript = transcribe_with_words(raw_mp4)
                words = transcript.get("words") or []
                print(f"[whisper] {post_id} @{streamer}: backend={transcript['backend']} words={len(words)}")

                # Vision + transcript check on 4 real frames BEFORE cutting:
                # scores stranger comprehension (0-10), verifies game, picks setup->payoff window, writes clean YT title!
                strip_b64 = build_4frame_strip_b64(raw_mp4, src_dur)
                copy = score_moment_for_strangers(
                    streamer=streamer,
                    clip_title=clip_meta.get("title") or "",
                    src_dur=src_dur,
                    strip_b64=strip_b64,
                    words=words,
                    require_gta6=require_gta6,
                    source_platform=src_plat,
                    strategy=strategy,
                    campaign=active_campaign,
                )
                s_score = int(copy.get("stranger_score") or copy.get("virality_score") or 7)
                v_score = int(copy.get("virality_score") or s_score)
                s_energy = str(copy.get("scene_energy") or "high_action")
                print(
                    f"[vision] {post_id} @{streamer}: is_gta6={copy['is_gta6']} ({copy['game_detected']}) | "
                    f"stranger={s_score}/10 virality={v_score}/10 ({s_energy}) | "
                    f"hook={copy['hook']!r} | yt_title={copy.get('yt_title','')!r}"
                )
                if require_gta6 and not copy.get("is_gta6", True):
                    print(f"[skip] {cid} (@{streamer}) rejected by vision check: detected {copy['game_detected']!r}, not GTA 6.")
                    raw_mp4.unlink(missing_ok=True)
                    seen_set.add(cid)
                    continue
                if min(s_score, v_score) < min_virality_score or s_energy in ("slow_menu", "boring_idle", "needs_context"):
                    reason = copy.get("reject_reason") or f"low stranger/virality score ({s_energy}, s={s_score}, v={v_score} < {min_virality_score})"
                    print(f"[skip] {cid} (@{streamer}) rejected by quality gate: {reason}")
                    raw_mp4.unlink(missing_ok=True)
                    seen_set.add(cid)
                    continue
                if UNSAFE_TITLE_RE.search(f"{copy.get('hook','')} {copy.get('visual_summary','')} {copy.get('caption','')}"):
                    print(f"[skip] {cid} (@{streamer}) rejected by content safety check on vision summary.")
                    raw_mp4.unlink(missing_ok=True)
                    seen_set.add(cid)
                    continue

                # Dynamic facecam detection on a 10% grid frame -> ensures 40% streamer face on top + 60% gameplay below!
                src_w, src_h = probe_video_dimensions(raw_mp4)
                if src_h > src_w and require_facecam:
                    print(f"[skip] {cid} (@{streamer}) rejected: source is already vertical ({src_w}x{src_h}), not widescreen stream w/ facecam.")
                    raw_mp4.unlink(missing_ok=True)
                    seen_set.add(cid)
                    continue

                grid_b64 = build_grid_frame_b64(raw_mp4, src_dur)
                cam_info = detect_facecam_with_openrouter(grid_b64, streamer)
                print(
                    f"[facecam] {post_id} @{streamer} ({src_w}x{src_h}): has_facecam={cam_info['has_facecam']} "
                    f"corner={cam_info['facecam_corner']} box_pct={cam_info['facecam_box_pct']}"
                )

                facecam_crop: list[int] | None = None
                game_crop: list[int] | None = None
                if cam_info["has_facecam"] and cam_info["facecam_box_pct"]:
                    facecam_crop, game_crop = compute_stack_crops(src_w, src_h, cam_info["facecam_box_pct"])
                elif not cam_info["checked"]:
                    crop_cfg = crops.get(streamer_key)
                    if crop_cfg and crop_cfg.get("layout") == "stack":
                        sx, sy = src_w / 1920.0, src_h / 1080.0
                        fc = crop_cfg["facecam"]
                        gc = crop_cfg["game"]
                        facecam_crop = [int(fc[0] * sx) // 2 * 2, int(fc[1] * sy) // 2 * 2, int(fc[2] * sx) // 2 * 2, int(fc[3] * sy) // 2 * 2]
                        game_crop = [int(gc[0] * sx) // 2 * 2, int(gc[1] * sy) // 2 * 2, int(gc[2] * sx) // 2 * 2, int(gc[3] * sy) // 2 * 2]

                if require_facecam and (not facecam_crop or not game_crop):
                    print(f"[skip] {cid} (@{streamer}) rejected: no real streamer facecam detected (REQUIRE_FACECAM=true).")
                    raw_mp4.unlink(missing_ok=True)
                    seen_set.add(cid)
                    continue

                # Save visual proof frame (middle of clip)
                PROOFS_DIR.mkdir(parents=True, exist_ok=True)
                proof_png = PROOFS_DIR / f"{post_id}_proof.png"
                subprocess.run([
                    "ffmpeg", "-y", "-ss", f"{min(src_dur * 0.5, src_dur - 1.0):.2f}",
                    "-i", str(raw_mp4), "-frames:v", "1", str(proof_png),
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # Build tight setup-to-payoff window with dead-air removal (`silencedetect`)
                payoff_end = float(copy.get("payoff_end_sec") or min(src_dur - 0.25, max(target_sec + 4.0, min(src_dur - 0.25, 24.0))))
                payoff_end = max(13.0, min(src_dur - 0.2, payoff_end))
                teaser_dur = 2.2
                teaser_start = max(2.5, payoff_end - teaser_dur - 0.35)
                teaser_end = min(payoff_end, teaser_start + teaser_dur)

                body_raw_len = max(10.5, target_sec - teaser_dur - 0.14 - 0.65)
                body_start = max(0.0, payoff_end - body_raw_len)
                if copy.get("setup_start_sec") is not None:
                    ai_start = float(copy["setup_start_sec"])
                    if 8.0 <= (payoff_end - ai_start) <= body_raw_len + 4.0:
                        body_start = max(0.0, ai_start)
                body_end = payoff_end

                silences = detect_silences(raw_mp4)
                keep_ranges = build_dead_air_free_keep_ranges(
                    window_start=body_start,
                    window_end=body_end,
                    silences=silences,
                    words=words,
                    protected_window=(max(body_start, payoff_end - 3.0), payoff_end),
                )

                slowmo_start = max(keep_ranges[0][0] + 4.0, payoff_end - 2.2)
                slowmo_end = min(keep_ranges[-1][1] - 0.4, slowmo_start + 0.65)

                SPECS_DIR.mkdir(parents=True, exist_ok=True)
                srt_path = None
                if words:
                    srt_cand = SPECS_DIR / f"{post_id}.srt"
                    srt_path = words_to_kinetic_srt(words, srt_cand, max_words=3, max_dur=1.35, uppercase=True)

                use_stack = bool(facecam_crop and game_crop)
                spec: dict[str, Any] = {
                    "src": str(raw_mp4.resolve()),
                    "out": str((RENDERS_DIR / f"{post_id}.mp4").resolve()),
                    "credit": f"@{streamer} ({src_plat})",
                    "layout": "stack" if use_stack else "full",
                    "teaser": {"start": round(teaser_start, 2), "end": round(teaser_end, 2)},
                    "teaser_text": copy["hook"],
                    "keep": keep_ranges,
                    "slowmo": [{"start": round(slowmo_start, 2), "end": round(slowmo_end, 2), "speed": 0.5}] if slowmo_end > slowmo_start + 0.25 else [],
                    "punchlines": [
                        {
                            "start": round(max(keep_ranges[0][0] + 1.0, payoff_end - 2.2), 2),
                            "end": round(keep_ranges[-1][1], 2),
                            "text": copy["punchline"],
                        }
                    ],
                    "caption_style": "kinetic",
                    "loudnorm": True,
                    "volume": 1.25,
                }
                if srt_path and srt_path.exists():
                    spec["subtitles"] = str(srt_path.resolve())
                if use_stack:
                    spec["facecam"] = facecam_crop
                    spec["game"] = game_crop

                spec_path = SPECS_DIR / f"{post_id}.json"
                save_json(spec_path, spec)

                render_info = render_clip(spec_path, make_qa=True, make_sheet=False)
                out_mp4 = Path(render_info["out"])
                slot_iso, date_str, time_str = find_next_open_slot(now_wat)

                # Apply campaign compliance fixes & checks if a clipping campaign is active
                final_caption = apply_campaign_caption_fixes(copy["caption"], active_campaign) if active_campaign else copy["caption"]
                camp_check = check_campaign_rules(
                    {
                        "duration_sec": render_info["duration_sec"],
                        "caption": final_caption,
                        "yt_title": copy.get("yt_title", ""),
                        "platforms": active_platforms,
                    },
                    active_campaign,
                )
                if active_campaign and not camp_check["passed"]:
                    print(f"[warn] {post_id} campaign check flags: {camp_check['checks']}", file=sys.stderr)

                # Validate & queue through safe_publish.py (passing clean separate --yt-title!)
                pub_cmd = [
                    sys.executable, str(ROOT_DIR / "scripts/safe_publish.py"),
                    "--post-id", post_id,
                    "--video", str(out_mp4),
                    "--caption", final_caption,
                    "--yt-title", copy.get("yt_title") or copy["hook"].title(),
                    "--platforms", *active_platforms,
                    "--scheduled-time", slot_iso,
                    "--ig-first-comment", copy["ig_first_comment"],
                ]
                if args.dry_run:
                    pub_cmd.append("--dry-run")
                pub_res = subprocess.run(pub_cmd, capture_output=True, text=True)
                if pub_res.returncode != 0:
                    err_line = pub_res.stderr.strip().splitlines()[-1] if pub_res.stderr.strip() else "guardrail blocked"
                    print(f"[skip] {post_id} blocked by safe_publish guardrails: {err_line}", file=sys.stderr)
                    failure_reasons.append(f"{post_id} publish: {err_line}")
                    raw_mp4.unlink(missing_ok=True)
                    out_mp4.unlink(missing_ok=True)
                    continue

                # Once safe_publish commits this post_id to the ledger, advance post_num so retries never collide
                post_num += 1

                # Deliver to Telegram in clean structured format (separate YT Shorts title!)
                delivered_tg = False
                if tg_ready and not args.dry_run:
                    deliver_bundle(
                        ROOT_DIR / "queue" / post_id,
                        index=already_delivered + len(produced) + 1,
                        total=max(target_clips, already_delivered + remaining_target),
                        dry_run=False,
                        meta={
                            "stranger_score": s_score,
                            "signals": clip_signals,
                            "campaign_id": active_campaign.get("id") if active_campaign else None,
                        },
                    )
                    sent_log = load_sent_log()
                    sent_log.setdefault("delivered", {})[post_id] = {
                        "delivered_at_utc": datetime.now(timezone.utc).isoformat(),
                        "slot": slot_iso,
                    }
                    save_sent_log(sent_log)
                    delivered_tg = True

                if not args.dry_run:
                    append_scoreboard_rows(
                        post_id, date_str, time_str, copy["hook"],
                        render_info["duration_sec"], f"@{streamer} ({src_plat})",
                        f"proofs/{post_id}_proof.png",
                        platforms=["instagram", "tiktok", "youtube"],
                        pillar=f"stream_clipper_{int(round(target_sec))}s",
                        extra_note=f"stranger={s_score}/10 | captions={'yes' if srt_path else 'no'}",
                    )
                    append_log_entry(
                        post_id, f"{date_str} {time_str}", streamer, src_plat, clip_url,
                        clip_meta.get("created_at", ""), copy, render_info["duration_sec"],
                        f"proofs/{post_id}_proof.png",
                        signals=clip_signals,
                        has_captions=bool(srt_path),
                    )

                item_record = {
                    "post_id": post_id,
                    "file": f"{post_id}.mp4",
                    "streamer": streamer,
                    "source_platform": src_plat,
                    "duration_sec": render_info["duration_sec"],
                    "timestamp": f"{keep_ranges[0][0]:.1f}s-{keep_ranges[-1][1]:.1f}s",
                    "signals": clip_signals,
                    "stranger_score": s_score,
                    "virality_score": v_score,
                    "has_captions": bool(srt_path),
                    "whisper_backend": transcript.get("backend", "none"),
                    "hook": copy["hook"],
                    "yt_title": copy.get("yt_title", ""),
                    "campaign_check": camp_check,
                    "slot": f"{date_str} {time_str}",
                    "telegram_delivered": delivered_tg,
                }
                produced.append(item_record)
                report_items.append(item_record)
                used_streamers_today.add(streamer_key)
                seen_set.add(cid)

                # Disk hygiene: remove heavy raw & rendered MP4 right after delivery so repo stays clean
                raw_mp4.unlink(missing_ok=True)
                if delivered_tg:
                    out_mp4.unlink(missing_ok=True)
            except Exception as exc:
                reason = f"{cid}: {type(exc).__name__}: {str(exc)[:140]}"
                print(f"[warn] Failed clip {reason}", file=sys.stderr)
                failure_reasons.append(reason)

        if not args.dry_run:
            save_json(SEEN_PATH, {"clip_ids": sorted(seen_set)[-500:]})

    # Clean up temporary cloud raw folder
    shutil.rmtree(RAW_DIR, ignore_errors=True)

    # Save structured quality report + Markdown report
    if report_items and not args.dry_run:
        save_json(LAST_REPORT_PATH, {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "slot_label": args.slot_label,
            "niche": args.niche,
            "campaign_id": args.campaign or None,
            "clips": report_items,
        })
        write_clips_report_md(report_items, CLIPS_REPORT_MD)

    # Record today's daily drop status for multi-window idempotency & dashboard display
    if not args.dry_run:
        delivered_ids_this_run = [p["post_id"] for p in produced if p.get("telegram_delivered")]
        total_now = already_delivered + len(delivered_ids_this_run)
        if total_now >= target_clips:
            drop_status = "completed"
        elif total_now > 0:
            drop_status = "partial"
        else:
            drop_status = "failed"
        record_daily_drop_attempt(
            wat_date_str,
            args.slot_label,
            delivered_ids_this_run,
            target_clips,
            drop_status,
            note="; ".join(failure_reasons[:2]) if failure_reasons else f"Delivered {len(delivered_ids_this_run)} clip(s)",
        )

    # 8. Rebuild offline-safe live dashboard (auto-deployed to GitHub Pages!)
    payload = build_dashboard_payload()
    update_dashboard_html(payload)

    # Send concise daily summary to Telegram
    if tg_ready and not args.dry_run:
        lines = [
            f"Daily WAT Drop Summary ({wat_date_str} | {args.slot_label})",
            f"Clips delivered this run: {len(produced)} (~{target_sec:.0f}s each, stranger_score>={min_virality_score}/10)",
            f"Active platforms: {', '.join(active_platforms) or 'none'}"
            + (f" (Paused: {', '.join(paused_platforms)})" if paused_platforms else ""),
            f"Total tracked views: {payload['totals']['views']:,} | Followers: {payload['totals']['followers']:,}",
        ]
        for p in produced:
            lines.append(
                f"- {p['post_id']} (@{p['streamer']}, {p['duration_sec']}s, "
                f"score {p['stranger_score']}/10, captions={'YES' if p['has_captions'] else 'NO'}) -> {p['slot']}"
            )
        if not produced and failure_reasons:
            lines.append(f"Diagnostic (why 0 clips): {failure_reasons[0]}")
        send_message("\n".join(lines))

    print(json.dumps({"produced": produced, "failure_sample": failure_reasons[:3], "dashboard_updated": True, "dry_run": args.dry_run}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
