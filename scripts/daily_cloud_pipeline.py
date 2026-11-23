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

from clip import EMOJI_RE, probe_duration, render_clip  # noqa: E402
from dashboard_data import build_dashboard_payload, update_dashboard_html  # noqa: E402
from stuck_check import run_health_check  # noqa: E402
from telegram_deliver import deliver_bundle, load_sent_log, save_sent_log, send_message  # noqa: E402

WAT = timezone(timedelta(hours=1))
DATA_DIR = ROOT_DIR / "data"
SEEN_PATH = DATA_DIR / "seen_clips.json"
CROPS_PATH = DATA_DIR / "streamer_crops.json"
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


def scout_twitch_direct_clips(limit: int = 20, min_duration: float = 24.0) -> list[dict[str, Any]]:
    """
    Zero-key, zero-2FA parallel Twitch clip scouting via yt-dlp.
    Shuffles streamer order daily and interleaves round-robin across streamers so
    every daily drop features DIFFERENT creators instead of repeating one streamer.
    """
    seen_ids: set[str] = set()
    by_streamer: dict[str, list[dict[str, Any]]] = {}
    streamers = get_twitch_streamers()
    # Deterministic daily shuffle so different streamers lead each day
    rng = random.Random(datetime.now(WAT).strftime("%Y-%m-%d"))
    rng.shuffle(streamers)

    def fetch_streamer(args_pair: tuple[str, str]) -> tuple[str, list[dict[str, Any]]]:
        handle, time_range = args_pair
        url = f"https://www.twitch.tv/{handle}/clips?filter=clips&range={time_range}"
        try:
            out = subprocess.check_output(
                ["yt-dlp", "--flat-playlist", "--playlist-end", "6", "--dump-json", "--no-warnings", url],
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
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
        # Prefer clips near ~32-45s (plenty of footage for a clean 30s payoff-first edit)
        batch.sort(key=lambda x: abs(x["duration"] - 36.0))
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


def generate_copy_with_openrouter(
    streamer: str,
    clip_title: str,
    strip_b64: str | None = None,
    require_gta6: bool = True,
    source_platform: str = "Twitch",
) -> dict[str, Any]:
    """
    Use fast free OpenRouter vision-language models (nemotron-3.5-super-vl:free / nemotron-3-nano-omni / openrouter/free)
    with reasoning disabled for ~1.5s responses to SEE the actual 4 video frames, verify real GTA gameplay
    (rejecting football/FIFA pitches, IRL, shooters, etc.), and write hooks/captions strictly grounded in the frames.
    """
    handle_clean = re.sub(r"[^A-Za-z0-9_]", "", streamer.lower()) or "streamer"
    fallback = {
        "is_gta6": True,
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

    prompt = (
        f"You are reviewing a 2x2 grid of 4 sequential frames from a gaming stream clip (today is Nov 2026 and GTA 6 just launched).\n"
        f"Streamer: @{streamer} (Twitch). Original clip title: {clip_title!r}.\n"
        f"CRITICAL ACCURACY RULES:\n"
        f"1. Describe ONLY what is LITERALLY visible in the 4 frames. NEVER invent cars, driving, police, Lucia, Jason, or physics glitches if they are NOT actually visible on screen.\n"
        f"2. Set is_gta6=true if the frames show Grand Theft Auto open-world gameplay (cars/highways, Vice City/Leonida/city streets, GTA characters/NPCs, police, open-world third-person/first-person GTA action — note: GTA 6 just released and resembles modern GTA V visually, so treat any modern GTA gameplay as is_gta6=true).\n"
        f"3. Set is_gta6=false for football/soccer/FIFA pitches, sports games, CS/Valorant/FPS shooters, Fortnite, Minecraft/2D/horror games, pure IRL/Just Chatting streams without GTA gameplay, or menu-only screens.\n"
        f"Return STRICT JSON ONLY with keys: is_gta6 (boolean), game_detected (short string), visual_summary (1 short sentence of what is literally on screen), hook, punchline, caption_line, ig_first_comment, send_test.\n"
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
            "temperature": 0.4,
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
) -> None:
    if not SCOREBOARD_PATH.exists():
        return
    with SCOREBOARD_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for plat in (platforms or ["instagram", "tiktok", "youtube"]):
            writer.writerow([
                post_id, date_str, time_str, plat, "cloud_auto_30s", "stack_or_full",
                hook, f"{length_sec:.2f}", credit,
                "", "", "", "", "", "", "", "", "EXP-R1",
                f"delivered_telegram | proof:{proof_rel}",
            ])


def append_log_entry(
    post_id: str,
    slot_str: str,
    streamer: str,
    source_url: str,
    created_at: str,
    copy: dict[str, str],
    length_sec: float,
    proof_rel: str,
) -> None:
    entry = (
        f"\n### {post_id} — @{streamer} (Cloud Auto 30s)\n"
        f"- **Status:** Cut & Delivered to Telegram\n"
        f"- **Recommended Slot (WAT):** `{slot_str}`\n"
        f"- **Platforms:** instagram, tiktok, youtube\n"
        f"- **Duration:** {length_sec:.2f}s (1080x1920, 30 fps)\n"
        f"- **Hook (Frame 1):** `{copy['hook']}`\n"
        f"- **Punchline:** `{copy['punchline']}`\n"
        f"- **Source URL & Created At:** `{source_url}` ({created_at})\n"
        f"- **Credit:** `@{streamer} (Twitch)`\n"
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


def main() -> int:
    load_local_dotenv()
    parser = argparse.ArgumentParser(description="Daily 10:00 AM WAT headless cloud & local YouTube-streamer pipeline.")
    parser.add_argument("--clip-count", type=int, default=int(os.environ.get("DAILY_CLIP_COUNT", "5")))
    parser.add_argument("--target-sec", type=float, default=float(os.environ.get("TARGET_CLIP_SEC", "30.0")))
    parser.add_argument("--source", choices=("auto", "twitch", "youtube"), default="auto",
                        help="Clip source: auto/twitch (cloud default) or youtube (local Mac YouTube-only streamers)")
    parser.add_argument("--streamers", type=str, default="",
                        help="Optional comma-separated streamer names/handles (e.g. 'IShowSpeed,TypicalGamer')")
    parser.add_argument("--dry-run", action="store_true", help="Do not send Telegram messages or mutate scoreboard")
    args = parser.parse_args()

    require_gta6 = os.environ.get("REQUIRE_GTA6_ONLY", "true").strip().lower() not in ("0", "false", "no")
    tagged_streamers = [s.strip() for s in args.streamers.split(",") if s.strip()]
    now_wat = datetime.now(WAT)
    print(f"=== Pipeline Starting: {now_wat.isoformat()} (source={args.source}, target={args.clip_count} clips @ ~{args.target_sec:.0f}s, require_gta6={require_gta6}) ===")

    # 1. Sync stats (skipped on --dry-run to save Scrape Creators credits)
    if not args.dry_run:
        subprocess.run([sys.executable, str(ROOT_DIR / "scripts/sync_stats.py")], check=False)

    # 2. Health check & stuck-post protocol (determine active non-paused platforms BEFORE downloading anything!)
    health = run_health_check(apply_pauses=not args.dry_run)
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

    if health.get("urgent_alert_even_overnight") and not args.dry_run:
        if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
            send_message("URGENT: All active platforms stuck simultaneously. Global pause triggered. Run scripts/unpause.py after checking Account Status.")

    # 3. Scout fresh clips ONLY if at least one platform is active
    tw_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    tw_sec = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    tg_ready = bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() and os.environ.get("TELEGRAM_CHAT_ID", "").strip())

    produced: list[dict[str, Any]] = []
    failure_reasons: list[str] = []
    seen = load_json(SEEN_PATH, {"clip_ids": []})
    seen_set = set(seen.get("clip_ids", []))
    crops = load_json(CROPS_PATH, {})
    candidates: list[dict[str, Any]] = []

    if not active_platforms:
        failure_reasons.append("All platforms are currently paused (check data/pause_state.json or run scripts/unpause.py).")
    elif args.source == "youtube":
        min_src = max(22.0, args.target_sec - 5.0)
        yt_cands = scout_youtube_gta6_clips(
            limit=max(16, args.clip_count * 6),
            streamers=tagged_streamers or None,
            min_duration=min_src,
        )
        print(f"[scout:youtube-local] Found {len(yt_cands)} YouTube-streamer clips across {len({c['broadcaster_name'] for c in yt_cands})} creators.")
        candidates.extend(yt_cands)
    else:
        if tw_id and tw_sec:
            try:
                candidates = scout_twitch_gta6_clips(tw_id, tw_sec, limit=40)
                print(f"[scout:twitch-api] Found {len(candidates)} fresh (<24h, English, >=18s) GTA 6 Twitch clips.")
            except Exception as exc:
                print(f"[warn] Twitch API scout failed ({type(exc).__name__}); using zero-key Twitch direct scout.", file=sys.stderr)

        if len(candidates) < args.clip_count * 4:
            min_src = max(20.0, args.target_sec - 5.0)
            tw_direct = scout_twitch_direct_clips(limit=max(24, args.clip_count * 6), min_duration=min_src)
            print(f"[scout:twitch-direct] Found {len(tw_direct)} round-robin Twitch clips across {len({c['broadcaster_name'] for c in tw_direct})} streamers.")
            candidates.extend(tw_direct)

        if args.source == "auto" and len(candidates) < args.clip_count:
            yt_cands = scout_youtube_gta6_clips(limit=12)
            print(f"[scout:youtube] Found {len(yt_cands)} YouTube fallback clips.")
            candidates.extend(yt_cands)

    if candidates and active_platforms:
        post_num = next_post_number()
        attempts = 0
        max_attempts = max(14, args.clip_count * 4)
        used_streamers_today: set[str] = set()

        for clip_meta in candidates:
            if len(produced) >= args.clip_count or attempts >= max_attempts:
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
                if src_dur < max(19.0, args.target_sec - 6.0):
                    raw_mp4.unlink(missing_ok=True)
                    seen_set.add(cid)
                    continue

                # Vision check on 4 real frames BEFORE cutting: verifies real GTA 6 & writes grounded copy!
                strip_b64 = build_4frame_strip_b64(raw_mp4, src_dur)
                copy = generate_copy_with_openrouter(
                    streamer,
                    clip_meta.get("title") or "",
                    strip_b64=strip_b64,
                    require_gta6=require_gta6,
                    source_platform=src_plat,
                )
                print(
                    f"[vision] {post_id} @{streamer}: is_gta6={copy['is_gta6']} ({copy['game_detected']}) | "
                    f"hook={copy['hook']!r} | seen={copy['visual_summary'][:80]!r}"
                )
                if require_gta6 and not copy.get("is_gta6", True):
                    print(f"[skip] {cid} (@{streamer}) rejected by vision check: detected {copy['game_detected']!r}, not GTA 6.")
                    raw_mp4.unlink(missing_ok=True)
                    seen_set.add(cid)
                    continue
                if UNSAFE_TITLE_RE.search(f"{copy.get('hook','')} {copy.get('visual_summary','')} {copy.get('caption','')}"):
                    print(f"[skip] {cid} (@{streamer}) rejected by content safety check on vision summary.")
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

                # Build ~30-second payoff-first spec:
                # 2.5s teaser from climax + 0.14s flash + ~26.5s setup-to-payoff + 0.8s slow-mo boost ~= 30.0s
                payoff_end = max(min(src_dur - 0.3, args.target_sec + 12.0), min(src_dur - 0.3, 24.0))
                teaser_dur = 2.5
                teaser_start = max(10.0, payoff_end - teaser_dur - 0.5)
                teaser_end = min(payoff_end, teaser_start + teaser_dur)

                body_raw_len = max(18.0, args.target_sec - teaser_dur - 0.14 - 0.8)
                body_start = max(0.0, payoff_end - body_raw_len)
                body_end = payoff_end

                slowmo_start = max(body_start + 8.0, payoff_end - 2.6)
                slowmo_end = min(body_end - 0.6, slowmo_start + 0.8)

                crop_cfg = crops.get(streamer_key)

                spec: dict[str, Any] = {
                    "src": str(raw_mp4.resolve()),
                    "out": str((RENDERS_DIR / f"{post_id}.mp4").resolve()),
                    "credit": f"@{streamer} ({src_plat})",
                    "layout": crop_cfg["layout"] if crop_cfg else "full",
                    "teaser": {"start": round(teaser_start, 2), "end": round(teaser_end, 2)},
                    "teaser_text": copy["hook"],
                    "keep": [[round(body_start, 2), round(body_end, 2)]],
                    "slowmo": [{"start": round(slowmo_start, 2), "end": round(slowmo_end, 2), "speed": 0.5}],
                    "punchlines": [
                        {
                            "start": round(max(body_start + 1.0, payoff_end - 2.4), 2),
                            "end": round(body_end, 2),
                            "text": copy["punchline"],
                        }
                    ],
                    "volume": 1.25,
                }
                if crop_cfg:
                    spec["facecam"] = crop_cfg["facecam"]
                    spec["game"] = crop_cfg["game"]

                SPECS_DIR.mkdir(parents=True, exist_ok=True)
                spec_path = SPECS_DIR / f"{post_id}.json"
                save_json(spec_path, spec)

                render_info = render_clip(spec_path, make_qa=True, make_sheet=False)
                out_mp4 = Path(render_info["out"])
                slot_iso, date_str, time_str = find_next_open_slot(now_wat)

                # Validate & queue through safe_publish.py (ONLY for active non-paused platforms!)
                pub_cmd = [
                    sys.executable, str(ROOT_DIR / "scripts/safe_publish.py"),
                    "--post-id", post_id,
                    "--video", str(out_mp4),
                    "--caption", copy["caption"],
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

                # Deliver to Telegram in structured 5-part format
                delivered_tg = False
                if tg_ready and not args.dry_run:
                    deliver_bundle(
                        ROOT_DIR / "queue" / post_id,
                        index=len(produced) + 1,
                        total=args.clip_count,
                        dry_run=False,
                    )
                    sent_log = load_sent_log()
                    sent_log.setdefault("delivered", {})[post_id] = {
                        "delivered_at_utc": datetime.now(timezone.utc).isoformat(),
                        "slot": slot_iso,
                    }
                    save_sent_log(sent_log)
                    delivered_tg = True

                if not args.dry_run:
                    # Always track all 3 platforms in scoreboard.csv so IG, TikTok & YouTube stay in sync
                    append_scoreboard_rows(
                        post_id, date_str, time_str, copy["hook"],
                        render_info["duration_sec"], f"@{streamer} ({src_plat})",
                        f"proofs/{post_id}_proof.png",
                        platforms=["instagram", "tiktok", "youtube"],
                    )
                    append_log_entry(
                        post_id, f"{date_str} {time_str}", streamer, clip_url,
                        clip_meta.get("created_at", ""), copy, render_info["duration_sec"],
                        f"proofs/{post_id}_proof.png",
                    )

                produced.append({
                    "post_id": post_id,
                    "streamer": streamer,
                    "duration_sec": render_info["duration_sec"],
                    "slot": f"{date_str} {time_str}",
                    "telegram_delivered": delivered_tg,
                })
                used_streamers_today.add(streamer_key)
                seen_set.add(cid)
                post_num += 1

                # Disk hygiene: remove heavy raw & rendered MP4 right after delivery so repo stays clean
                raw_mp4.unlink(missing_ok=True)
                if delivered_tg:
                    out_mp4.unlink(missing_ok=True)
            except Exception as exc:
                reason = f"{cid}: {type(exc).__name__}: {str(exc)[:140]}"
                print(f"[warn] Failed clip {reason}", file=sys.stderr)
                failure_reasons.append(reason)
                # Do NOT permanently blacklist on transient download errors (e.g. YouTube IP throttle)

        if not args.dry_run:
            save_json(SEEN_PATH, {"clip_ids": sorted(seen_set)[-500:]})

    # Clean up temporary cloud raw/render folders
    shutil.rmtree(RAW_DIR, ignore_errors=True)

    # 8. Rebuild offline-safe live dashboard (auto-deployed to GitHub Pages!)
    payload = build_dashboard_payload()
    update_dashboard_html(payload)

    # Send concise daily summary to Telegram
    if tg_ready and not args.dry_run:
        lines = [
            f"Daily 10 AM WAT Drop Complete ({now_wat.strftime('%Y-%m-%d')})",
            f"Clips delivered today: {len(produced)} (~{args.target_sec:.0f}s each)",
            f"Active platforms: {', '.join(active_platforms) or 'none'}"
            + (f" (Paused 24h: {', '.join(paused_platforms)})" if paused_platforms else ""),
            f"Total tracked views: {payload['totals']['views']:,} | Followers: {payload['totals']['followers']:,}",
        ]
        for p in produced:
            lines.append(f"- {p['post_id']} (@{p['streamer']}, {p['duration_sec']}s) -> Post at {p['slot']}")
        if not produced and failure_reasons:
            lines.append(f"Diagnostic (why 0 clips): {failure_reasons[0]}")
        send_message("\n".join(lines))

    print(json.dumps({"produced": produced, "failure_sample": failure_reasons[:3], "dashboard_updated": True, "dry_run": args.dry_run}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
