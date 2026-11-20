#!/usr/bin/env python3
"""
Step 4 (Prompt 4): Safe Publisher (safe_publish.py) — 100% Free Approach

Backends (selected via --backend or PUBLISHER_BACKEND env var):
  - queue  (default, $0, zero API keys): validates all guardrails, extracts cover frame,
           writes ready-to-post bundles per platform under queue/, and records in ledger.
  - postiz ($0 self-hosted open-source scheduler): uploads video & schedules to Instagram,
           TikTok, and YouTube Shorts via Postiz REST API using POSTIZ_API_KEY env var.

Hard Guardrails:
  - Hard-coded and asserted allowed account IDs per platform (IG, TikTok, YouTube Shorts)
  - Anti-double-post ledger (blocks duplicate post_id OR duplicate video SHA-256 per platform)
  - Caption validation:
      * Must include Credit: @... line
      * <= 5 hashtags
      * No em dashes (— or --)
      * No "official"
      * No leak / early-play / early access / free copy / giveaway / datamine / pirated / exploit
      * No trailing question mark on the opening caption line
      * No duplicate caption within 7 days
  - Marks TikTok posts explicitly as NOT AI-generated
  - Enforces >= 2.5h spacing per platform, active pause state, and 2h profile-edit quarantine
  - Supports scheduledTime, IG first comment, cover frame, and --dry-run
  - NEVER prints any API key or secret
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ==============================================================================
# HARD-CODED ALLOWED CLIP ACCOUNT IDs
# Update these ONLY when you connect your exact clip accounts. The publisher
# asserts every target account against this allowlist before any action.
# ==============================================================================
ALLOWED_ACCOUNT_IDS: dict[str, str] = {
    "instagram": "clip-ig-unconfigured",
    "tiktok": "clip-tiktok-unconfigured",
    "youtube": "clip-youtube-unconfigured",
}

VALID_PLATFORMS = tuple(ALLOWED_ACCOUNT_IDS.keys())
MIN_SPACING_HOURS = 2.5
PROFILE_QUARANTINE_HOURS = 2.0
CAPTION_DEDUPE_DAYS = 7

BANNED_PHRASES = [
    r"\bofficial\b",
    r"\bleak(?:ed|s)?\b",
    r"\bearly[\s-]?play\b",
    r"\bearly[\s-]?access\b",
    r"\bplay[\s-]?early\b",
    r"\bfree[\s-]?copy\b",
    r"\bgiveaway\b",
    r"\bdatamine(?:d|s)?\b",
    r"\bpirat(?:e|ed|ing)\b",
    r"\bexploit(?:s)?\b",
]

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
LEDGER_PATH = DATA_DIR / "publish_ledger.json"
PAUSE_PATH = DATA_DIR / "pause_state.json"
QUEUE_DIR = ROOT_DIR / "queue"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        # Default naive timestamps to WAT (UTC+1)
        dt = dt.replace(tzinfo=timezone(timedelta(hours=1)))
    return dt.astimezone(timezone.utc)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def assert_allowed_account(platform: str, account_id: str) -> None:
    if platform not in ALLOWED_ACCOUNT_IDS:
        raise AssertionError(f"Platform {platform!r} is not in allowed platforms {VALID_PLATFORMS}")
    expected = ALLOWED_ACCOUNT_IDS[platform]
    if account_id != expected:
        raise AssertionError(
            f"Refused: account_id {account_id!r} does not match hard-coded allowed ID "
            f"{expected!r} for platform {platform!r}."
        )


def validate_caption(caption: str, ledger: dict[str, Any], now_utc: datetime) -> list[str]:
    """Return list of hard errors. Empty list means caption passed all rules."""
    errors: list[str] = []
    text = (caption or "").strip()
    if not text:
        return ["Caption is empty."]

    # 1. Must have Credit line
    if not re.search(r"(?im)^credit:\s*@\S+", text):
        errors.append("Caption is missing a valid credit line (expected e.g. 'Credit: @creator (Twitch)').")

    # 2. Hashtag count <= 5
    hashtags = re.findall(r"(?:^|\s)(#[A-Za-z0-9_]+)", text)
    if len(hashtags) > 5:
        errors.append(f"Caption has {len(hashtags)} hashtags (maximum allowed is 5).")

    # 3. No em dashes (or double-hyphen em-dash substitute)
    if "\u2014" in text or "--" in text:
        errors.append("Caption contains an em dash ('—' or '--'), which is prohibited.")

    # 4. Banned words (official, leak, early-play, giveaway, etc.)
    for pattern in BANNED_PHRASES:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            errors.append(f"Caption contains prohibited term: {m.group(0)!r}.")

    # 5. Opening hook line must not end with a question mark
    first_line = text.splitlines()[0].strip()
    if first_line.endswith("?"):
        errors.append("Opening caption line ends with '?' (put questions in the IG first comment instead).")

    # 6. No repeated caption within 7 days
    norm = re.sub(r"\s+", " ", first_line.lower())
    cutoff = now_utc - timedelta(days=CAPTION_DEDUPE_DAYS)
    for rec in ledger.get("entries", []):
        prev_line = re.sub(r"\s+", " ", (rec.get("caption_first_line") or "").lower())
        rec_ts_str = rec.get("scheduled_time_utc") or rec.get("created_at_utc")
        if prev_line and prev_line == norm and rec_ts_str:
            try:
                rec_ts = parse_iso_dt(rec_ts_str)
                if rec_ts >= cutoff:
                    errors.append(
                        f"Caption opening line was already used within the last {CAPTION_DEDUPE_DAYS} days "
                        f"(post_id={rec.get('post_id')})."
                    )
                    break
            except Exception:
                pass

    return errors


def check_pause_and_quarantine(
    platforms: list[str],
    scheduled_utc: datetime,
    pause_state: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    now = utc_now()

    if pause_state.get("paused_all"):
        reason = pause_state.get("reason", "manual/emergency pause")
        errors.append(f"Global kill switch is ACTIVE ({reason}). Run scripts/unpause.py to resume.")

    for p in platforms:
        p_info = (pause_state.get("platforms") or {}).get(p) or {}
        if p_info.get("paused"):
            until_str = p_info.get("until_utc")
            if not until_str or parse_iso_dt(until_str) > now:
                errors.append(
                    f"Platform {p!r} is PAUSED ({p_info.get('reason', 'stuck-post pause')}, "
                    f"until={until_str or 'manual unpause'}). Run scripts/unpause.py --platform {p}."
                )

    last_edit = pause_state.get("last_profile_edit_utc")
    if last_edit:
        edit_dt = parse_iso_dt(last_edit)
        quarantine_end = edit_dt + timedelta(hours=PROFILE_QUARANTINE_HOURS)
        if scheduled_utc < quarantine_end:
            errors.append(
                f"Profile quarantine active: scheduled time {scheduled_utc.isoformat()} is within "
                f"{PROFILE_QUARANTINE_HOURS:.0f}h of profile edit at {edit_dt.isoformat()} "
                f"(earliest allowed: {quarantine_end.isoformat()})."
            )

    return errors


def check_ledger_and_spacing(
    post_id: str,
    video_sha256: str,
    platforms: list[str],
    scheduled_utc: datetime,
    ledger: dict[str, Any],
    force_spacing: bool = False,
) -> list[str]:
    errors: list[str] = []
    entries = ledger.get("entries", [])

    for p in platforms:
        for rec in entries:
            if rec.get("platform") != p:
                continue
            if rec.get("post_id") == post_id:
                errors.append(f"Double-post blocked: post_id {post_id!r} already submitted to {p!r}.")
            if rec.get("video_sha256") == video_sha256:
                errors.append(
                    f"Duplicate file blocked: exact video SHA-256 already submitted to {p!r} "
                    f"(as post_id={rec.get('post_id')}). Cut a fresh version before reposting."
                )
            if not force_spacing and rec.get("scheduled_time_utc"):
                try:
                    prev_dt = parse_iso_dt(rec["scheduled_time_utc"])
                    gap_hours = abs((scheduled_utc - prev_dt).total_seconds()) / 3600.0
                    if gap_hours < MIN_SPACING_HOURS:
                        errors.append(
                            f"Spacing violation on {p!r}: {gap_hours:.2f}h from {rec.get('post_id')} "
                            f"(minimum {MIN_SPACING_HOURS}h apart)."
                        )
                except Exception:
                    pass
    return errors


def extract_cover_frame(video_path: Path, out_png: Path, sec: float = 0.25) -> Path:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-ss", f"{sec:.3f}", "-i", str(video_path),
            "-frames:v", "1", str(out_png),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0 or not out_png.exists():
        raise RuntimeError(f"Failed to extract cover frame from {video_path}: {proc.stderr[-500:]}")
    return out_png


def postiz_upload_media(api_url: str, api_key: str, file_path: Path) -> dict[str, Any]:
    """Upload a video or image to self-hosted Postiz REST API without ever printing api_key."""
    boundary = "----SafePublishBoundary987654321"
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/upload",
        data=body,
        method="POST",
        headers={
            "Authorization": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def postiz_schedule_post(
    api_url: str,
    api_key: str,
    platform: str,
    account_id: str,
    media_ref: dict[str, Any],
    cover_ref: dict[str, Any] | None,
    caption: str,
    scheduled_utc: datetime,
    ig_first_comment: str | None,
    yt_title: str | None,
) -> dict[str, Any]:
    """Schedule a post via self-hosted Postiz API."""
    settings: dict[str, Any] = {}
    if platform == "tiktok":
        settings = {
            "__type": "tiktok",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "content_posting_method": "DIRECT_POST",
            "is_ai_generated": False,
            "brand_content_toggle": False,
            "brand_organic_toggle": False,
        }
    elif platform == "instagram":
        settings = {
            "__type": "instagram",
            "post_type": "reel",
        }
        if cover_ref and cover_ref.get("path"):
            settings["thumb"] = cover_ref["path"]
    elif platform == "youtube":
        settings = {
            "__type": "youtube",
            "title": (yt_title or caption.splitlines()[0])[:95],
            "type": "public",
        }

    value_item: dict[str, Any] = {
        "content": caption,
        "image": [media_ref],
    }
    if platform == "instagram" and ig_first_comment:
        value_item["comments"] = [{"content": ig_first_comment}]

    payload = {
        "type": "schedule",
        "date": scheduled_utc.isoformat(),
        "shortLink": False,
        "tags": [],
        "posts": [
            {
                "integration": {"id": account_id},
                "value": [value_item],
                "settings": settings,
            }
        ],
    }
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/posts",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def publish_to_queue(
    post_id: str,
    video_path: Path,
    video_sha256: str,
    platform: str,
    account_id: str,
    caption: str,
    scheduled_utc: datetime,
    scheduled_local_str: str,
    ig_first_comment: str | None,
    cover_path: Path,
    yt_title: str | None,
) -> dict[str, Any]:
    """Write a clean, validated ready-to-post bundle to queue/<post_id>/<platform>/."""
    bundle_dir = QUEUE_DIR / post_id / platform
    bundle_dir.mkdir(parents=True, exist_ok=True)

    cover_dst = bundle_dir / f"cover{cover_path.suffix}"
    shutil.copy2(cover_path, cover_dst)
    (bundle_dir / "caption.txt").write_text(caption.strip() + "\n", encoding="utf-8")

    manifest: dict[str, Any] = {
        "post_id": post_id,
        "platform": platform,
        "account_id": account_id,
        "status": "queued_local",
        "video_path": str(video_path.resolve()),
        "video_sha256": video_sha256,
        "cover_frame": str(cover_dst.resolve()),
        "scheduled_time_utc": scheduled_utc.isoformat(),
        "scheduled_time_local": scheduled_local_str,
        "caption": caption.strip(),
        "tiktok_ai_generated": False if platform == "tiktok" else None,
        "ig_first_comment": ig_first_comment if platform == "instagram" else None,
        "youtube_title": (yt_title or caption.splitlines()[0])[:95] if platform == "youtube" else None,
        "created_at_utc": utc_now().isoformat(),
    }
    if platform == "instagram" and ig_first_comment:
        (bundle_dir / "ig_first_comment.txt").write_text(ig_first_comment.strip() + "\n", encoding="utf-8")

    save_json(bundle_dir / "manifest.json", manifest)
    return {"backend": "queue", "bundle_dir": str(bundle_dir.resolve()), "status": "queued_local"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe free publisher with guardrails & anti-double-post ledger.")
    parser.add_argument("--post-id", required=True, help="Unique post ID (e.g. GTA-001)")
    parser.add_argument("--video", required=True, type=Path, help="Path to rendered 1080x1920 MP4")
    parser.add_argument("--caption", help="Caption text (or use --caption-file)")
    parser.add_argument("--caption-file", type=Path, help="Read caption from a text file")
    parser.add_argument(
        "--platforms",
        nargs="+",
        default=list(VALID_PLATFORMS),
        choices=list(VALID_PLATFORMS),
        help="Target platforms (default: instagram tiktok youtube)",
    )
    parser.add_argument("--scheduled-time", required=True, help="ISO-8601 time (e.g. 2026-11-20T16:30:00+01:00)")
    parser.add_argument("--ig-first-comment", default=None, help="Optional Instagram first comment")
    parser.add_argument("--yt-title", default=None, help="Optional YouTube Shorts title (defaults to caption line 1)")
    parser.add_argument("--cover-frame", type=Path, default=None, help="Path to cover frame PNG/JPG")
    parser.add_argument("--cover-sec", type=float, default=0.25, help="Auto-extract cover frame at this second")
    parser.add_argument(
        "--backend",
        choices=["queue", "postiz"],
        default=os.environ.get("PUBLISHER_BACKEND", "queue"),
        help="Free backend: 'queue' (local ready-to-post bundles) or 'postiz' (self-hosted Postiz API)",
    )
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH, help="Path to publish ledger JSON")
    parser.add_argument("--pause-file", type=Path, default=PAUSE_PATH, help="Path to pause/quarantine state JSON")
    parser.add_argument("--force-spacing", action="store_true", help="Override 2.5h spacing check")
    parser.add_argument("--dry-run", action="store_true", help="Validate everything without publishing or mutating ledger")
    args = parser.parse_args()

    # Load caption
    if args.caption_file:
        caption = args.caption_file.read_text(encoding="utf-8").strip()
    elif args.caption:
        caption = args.caption.strip()
    else:
        print("ERROR: provide --caption or --caption-file", file=sys.stderr)
        return 2

    if not args.video.exists():
        print(f"ERROR: video file not found: {args.video}", file=sys.stderr)
        return 2

    # Resolve & strictly assert hard-coded allowed account IDs
    env_override_map = {
        "instagram": os.environ.get("ALLOWED_IG_ACCOUNT_ID", ALLOWED_ACCOUNT_IDS["instagram"]),
        "tiktok": os.environ.get("ALLOWED_TIKTOK_ACCOUNT_ID", ALLOWED_ACCOUNT_IDS["tiktok"]),
        "youtube": os.environ.get("ALLOWED_YOUTUBE_ACCOUNT_ID", ALLOWED_ACCOUNT_IDS["youtube"]),
    }
    for p in args.platforms:
        assert_allowed_account(p, env_override_map[p])

    scheduled_utc = parse_iso_dt(args.scheduled_time)
    now_utc = utc_now()
    video_hash = sha256_file(args.video)

    ledger = load_json(args.ledger, {"entries": []})
    pause_state = load_json(args.pause_file, {"paused_all": False, "platforms": {}, "last_profile_edit_utc": None})

    # Run all guardrail checks
    errors: list[str] = []
    errors.extend(validate_caption(caption, ledger, now_utc))
    errors.extend(check_pause_and_quarantine(args.platforms, scheduled_utc, pause_state))
    errors.extend(
        check_ledger_and_spacing(
            args.post_id, video_hash, args.platforms, scheduled_utc, ledger, force_spacing=args.force_spacing
        )
    )

    if args.backend == "postiz":
        for p in args.platforms:
            if ALLOWED_ACCOUNT_IDS[p].endswith("-unconfigured"):
                errors.append(
                    f"Postiz backend requires a real connected account ID for {p!r} in ALLOWED_ACCOUNT_IDS."
                )
        if not os.environ.get("POSTIZ_API_KEY", "").strip():
            errors.append("Postiz backend requires POSTIZ_API_KEY environment variable (never printed).")

    if errors:
        print("PUBLISH BLOCKED BY GUARDRAILS:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    # Cover frame preparation
    cover_path = args.cover_frame
    temp_cover: Path | None = None
    if cover_path is not None:
        if not cover_path.exists():
            print(f"ERROR: cover frame not found: {cover_path}", file=sys.stderr)
            return 2
    elif not args.dry_run:
        temp_cover = DATA_DIR / "tmp_covers" / f"{args.post_id}_cover.png"
        cover_path = extract_cover_frame(args.video, temp_cover, sec=args.cover_sec)

    summary = {
        "mode": "DRY_RUN" if args.dry_run else "LIVE",
        "backend": args.backend,
        "post_id": args.post_id,
        "video": str(args.video.resolve()),
        "video_sha256": video_hash[:16] + "...",
        "platforms": {p: ALLOWED_ACCOUNT_IDS[p] for p in args.platforms},
        "scheduled_time_utc": scheduled_utc.isoformat(),
        "scheduled_time_input": args.scheduled_time,
        "tiktok_ai_generated": False if "tiktok" in args.platforms else None,
        "ig_first_comment": args.ig_first_comment if "instagram" in args.platforms else None,
        "cover_frame": str(cover_path) if cover_path else f"auto@{args.cover_sec}s",
        "caption_checks": "ALL_PASSED",
        "results": {},
    }

    if args.dry_run:
        print(json.dumps(summary, indent=2))
        return 0

    try:
        postiz_media = None
        postiz_cover = None
        if args.backend == "postiz":
            api_url = os.environ.get("POSTIZ_API_URL", "http://localhost:5000/public/v1")
            api_key = os.environ["POSTIZ_API_KEY"].strip()
            postiz_media = postiz_upload_media(api_url, api_key, args.video)
            if cover_path:
                postiz_cover = postiz_upload_media(api_url, api_key, cover_path)

        for p in args.platforms:
            acct_id = ALLOWED_ACCOUNT_IDS[p]
            assert_allowed_account(p, acct_id)
            if args.backend == "queue":
                res = publish_to_queue(
                    post_id=args.post_id,
                    video_path=args.video,
                    video_sha256=video_hash,
                    platform=p,
                    account_id=acct_id,
                    caption=caption,
                    scheduled_utc=scheduled_utc,
                    scheduled_local_str=args.scheduled_time,
                    ig_first_comment=args.ig_first_comment,
                    cover_path=cover_path,  # type: ignore[arg-type]
                    yt_title=args.yt_title,
                )
            else:
                api_url = os.environ.get("POSTIZ_API_URL", "http://localhost:5000/public/v1")
                api_key = os.environ["POSTIZ_API_KEY"].strip()
                res = postiz_schedule_post(
                    api_url=api_url,
                    api_key=api_key,
                    platform=p,
                    account_id=acct_id,
                    media_ref=postiz_media,  # type: ignore[arg-type]
                    cover_ref=postiz_cover,
                    caption=caption,
                    scheduled_utc=scheduled_utc,
                    ig_first_comment=args.ig_first_comment,
                    yt_title=args.yt_title,
                )

            summary["results"][p] = res
            ledger.setdefault("entries", []).append({
                "post_id": args.post_id,
                "platform": p,
                "account_id": acct_id,
                "backend": args.backend,
                "video_sha256": video_hash,
                "caption_first_line": caption.splitlines()[0].strip(),
                "scheduled_time_utc": scheduled_utc.isoformat(),
                "created_at_utc": now_utc.isoformat(),
                "status": res.get("status", "scheduled"),
            })

        save_json(args.ledger, ledger)
    finally:
        if temp_cover and temp_cover.exists():
            temp_cover.unlink(missing_ok=True)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
