#!/usr/bin/env python3
"""
Free Telegram Bot Delivery Helper (telegram_deliver.py)

100% FREE ($0) structured daily delivery to your phone via @BotFather.
For each clip, sends 5 clean, tap-to-copy messages in exact order:
  1. Header + Recommended Posting Time (WAT) + Platforms
  2. Rendered 1080x1920 MP4 Video (sendVideo)
  3. Cover Image (sendPhoto)
  4. Standalone Copy-Paste Caption (1 tap to copy)
  5. Standalone Copy-Paste Instagram First Comment (1 tap to copy)

Tracks delivered post_ids in data/telegram_sent.json so --all-queued never sends
the same clip twice. Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from env vars
only and NEVER prints TELEGRAM_BOT_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
QUEUE_DIR = ROOT_DIR / "queue"
SENT_LOG_PATH = ROOT_DIR / "data" / "telegram_sent.json"
DAILY_DROPS_PATH = ROOT_DIR / "data" / "daily_drops.json"


def _require_env() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment variables (free via @BotFather)."
        )
    return token, chat_id


def _multipart_post(endpoint: str, field_name: str, file_path: Path, extra_fields: dict[str, str], timeout: int = 180) -> dict[str, Any]:
    token, chat_id = _require_env()
    boundary = "----TelegramClipBoundary987654"
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()

    parts: list[bytes] = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode("utf-8")
    ]
    for k, v in extra_fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        + file_bytes
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    body = b"".join(parts)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{endpoint}",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(2.5 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def send_message(text: str, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return {"dry_run": True, "type": "message", "preview": text[:160]}
    token, chat_id = _require_env()
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        method="POST",
    )
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(2.0 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def send_video(video_path: Path, caption: str = "", dry_run: bool = False) -> dict[str, Any]:
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if dry_run:
        return {"dry_run": True, "type": "video", "file": video_path.name, "size_mb": round(video_path.stat().st_size / 1e6, 2)}
    extra = {"supports_streaming": "true"}
    if caption:
        extra["caption"] = caption[:1020]
    return _multipart_post("sendVideo", "video", video_path, extra, timeout=180)


def send_photo(photo_path: Path, caption: str = "", dry_run: bool = False) -> dict[str, Any]:
    if not photo_path.exists():
        raise FileNotFoundError(f"Cover image not found: {photo_path}")
    if dry_run:
        return {"dry_run": True, "type": "photo", "file": photo_path.name}
    extra = {"caption": caption[:1020]} if caption else {}
    return _multipart_post("sendPhoto", "photo", photo_path, extra, timeout=60)


def load_sent_log(path: Path = SENT_LOG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"delivered": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_sent_log(data: dict[str, Any], path: Path = SENT_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_daily_drops(path: Path = DAILY_DROPS_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"days": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"days": {}}


def save_daily_drops(data: dict[str, Any], path: Path = DAILY_DROPS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def get_today_drop_status(wat_date_str: str, path: Path = DAILY_DROPS_PATH) -> dict[str, Any]:
    data = load_daily_drops(path)
    return (data.get("days") or {}).get(wat_date_str) or {
        "date_wat": wat_date_str,
        "status": "pending",
        "clips_delivered": 0,
        "target_clips": 0,
        "post_ids": [],
        "slot_used": None,
        "attempts": [],
    }


def record_daily_drop_attempt(
    wat_date_str: str,
    slot_label: str,
    post_ids: list[str],
    target_count: int,
    status: str,
    note: str = "",
    path: Path = DAILY_DROPS_PATH,
) -> dict[str, Any]:
    data = load_daily_drops(path)
    days = data.setdefault("days", {})
    existing = days.get(wat_date_str) or {
        "date_wat": wat_date_str,
        "status": "pending",
        "clips_delivered": 0,
        "target_clips": target_count,
        "post_ids": [],
        "slot_used": None,
        "attempts": [],
    }
    combined_ids = list(existing.get("post_ids") or [])
    for pid in post_ids:
        if pid not in combined_ids:
            combined_ids.append(pid)

    existing["target_clips"] = max(int(existing.get("target_clips") or 0), int(target_count))
    existing["post_ids"] = combined_ids
    existing["clips_delivered"] = len(combined_ids)
    existing["status"] = status
    if post_ids and not existing.get("slot_used"):
        existing["slot_used"] = slot_label
    elif status == "completed" and not existing.get("slot_used"):
        existing["slot_used"] = slot_label
    existing["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    existing.setdefault("attempts", []).append({
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "slot_label": slot_label,
        "status": status,
        "added_posts": post_ids,
        "note": note,
    })
    days[wat_date_str] = existing
    # Keep last 60 days so file stays compact
    if len(days) > 60:
        for old_k in sorted(days.keys())[:-60]:
            days.pop(old_k, None)
    save_daily_drops(data, path)
    return existing


def deliver_bundle(
    post_dir: Path,
    index: int = 1,
    total: int = 1,
    dry_run: bool = False,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send 1 clip in clean structured Telegram format with separate YouTube Shorts title vs IG/TikTok caption."""
    manifests = {}
    for pdir in sorted(post_dir.iterdir()):
        mp = pdir / "manifest.json"
        if mp.is_file():
            manifests[pdir.name] = json.loads(mp.read_text(encoding="utf-8"))
    if not manifests:
        raise FileNotFoundError(f"No platform manifests found in {post_dir}")

    primary = manifests.get("instagram") or next(iter(manifests.values()))
    post_id = primary["post_id"]
    video_path = Path(primary["video_path"])
    cover_path = Path(primary["cover_frame"])
    caption = primary["caption"]
    slot = primary.get("scheduled_time_local") or primary.get("scheduled_time_utc", "")
    ig_fc = (manifests.get("instagram") or {}).get("ig_first_comment")
    yt_title = (manifests.get("youtube") or {}).get("youtube_title")
    platforms_str = ", ".join(p.upper() for p in manifests.keys())
    m_info = meta or {}
    quality_bits = []
    if m_info.get("stranger_score") is not None:
        quality_bits.append(f"Stranger Score: {m_info['stranger_score']}/10")
    if m_info.get("signals"):
        quality_bits.append(f"Signals: {', '.join(m_info['signals'])}")
    if m_info.get("campaign_id"):
        quality_bits.append(f"Campaign: {m_info['campaign_id']}")

    header_lines = [
        "==============================",
        f"CLIP {index} OF {total} — {post_id}",
        f"Recommended Post Time: {slot}",
        f"Platforms: {platforms_str}",
        "YouTube Upload Checklist: Set Category = Gaming | Use YT Title message ONLY",
        "TikTok AI Label: OFF (Not AI-generated)",
    ]
    if quality_bits:
        header_lines.append(" | ".join(quality_bits))
    header_lines.append("==============================")
    header = "\n".join(header_lines)

    steps: dict[str, Any] = {}
    steps["1_header"] = send_message(header, dry_run=dry_run)

    # 2. Rendered Video MP4
    steps["2_video"] = send_video(video_path, caption=f"{post_id} — Video ({slot})", dry_run=dry_run)

    # 3. Cover Image
    if cover_path.exists():
        steps["3_cover"] = send_photo(cover_path, caption=f"{post_id} — Cover Frame", dry_run=dry_run)
    else:
        steps["3_cover"] = {"skipped": "cover not found"}

    # 4. YouTube Shorts Title FIRST & clearly labeled so it never gets mixed with IG/TikTok captions
    if yt_title:
        steps["4_yt_title_label"] = send_message(
            f"YOUTUBE SHORTS TITLE ONLY ({post_id}) — tap message below (set Category = Gaming):",
            dry_run=dry_run,
        )
        steps["5_yt_title"] = send_message(yt_title, dry_run=dry_run)
        cap_lines = [ln.strip() for ln in caption.splitlines() if ln.strip()]
        credit_and_tags = "\n".join(ln for ln in cap_lines[1:] if ln) if len(cap_lines) > 1 else caption
        steps["6_yt_description"] = send_message(
            f"YT Shorts Description ({post_id}):\n{credit_and_tags} #shorts",
            dry_run=dry_run,
        )

    # 7. Copy-Paste TikTok / Instagram Caption (clean standalone message so 1 tap copies it)
    steps["7_caption_label"] = send_message(
        f"TIKTOK & INSTAGRAM CAPTION ({post_id}) — tap message below:",
        dry_run=dry_run,
    )
    steps["8_caption"] = send_message(caption, dry_run=dry_run)

    # 9. Copy-Paste IG First Comment
    if ig_fc:
        steps["9_ig_first_comment"] = send_message(ig_fc, dry_run=dry_run)

    return {"post_id": post_id, "slot": slot, "steps": steps}


def deliver_all_queued(dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    sent_log = load_sent_log()
    delivered = sent_log.setdefault("delivered", {})

    pending_dirs: list[tuple[str, Path]] = []
    if QUEUE_DIR.exists():
        for post_dir in sorted(QUEUE_DIR.iterdir()):
            if not post_dir.is_dir():
                continue
            post_id = post_dir.name
            if not force and post_id in delivered:
                continue
            any_m = next(post_dir.glob("*/manifest.json"), None)
            if not any_m:
                continue
            m = json.loads(any_m.read_text(encoding="utf-8"))
            pending_dirs.append((m.get("scheduled_time_utc", ""), post_dir))

    pending_dirs.sort(key=lambda x: x[0])
    results = []
    total = len(pending_dirs)
    for idx, (_, post_dir) in enumerate(pending_dirs, start=1):
        res = deliver_bundle(post_dir, index=idx, total=total, dry_run=dry_run)
        results.append(res)
        if not dry_run:
            delivered[post_dir.name] = {
                "delivered_at_utc": datetime.now(timezone.utc).isoformat(),
                "slot": res["slot"],
            }
            save_sent_log(sent_log)

    return {"delivered_count": len(results), "dry_run": dry_run, "items": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Structured 5-part Telegram delivery for queued clips.")
    parser.add_argument("--bundle-dir", type=Path, help="Deliver one specific queue/<post_id> folder")
    parser.add_argument("--all-queued", action="store_true", help="Deliver all unsent bundles in queue/ in slot order")
    parser.add_argument("--force", action="store_true", help="Re-send even if already in data/telegram_sent.json")
    parser.add_argument("--message", help="Send a plain text summary/alert message")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        if args.all_queued:
            res = deliver_all_queued(dry_run=args.dry_run, force=args.force)
        elif args.bundle_dir:
            res = deliver_bundle(args.bundle_dir, index=1, total=1, dry_run=args.dry_run)
            if not args.dry_run:
                sent_log = load_sent_log()
                sent_log.setdefault("delivered", {})[args.bundle_dir.name] = {
                    "delivered_at_utc": datetime.now(timezone.utc).isoformat(),
                    "slot": res["slot"],
                }
                save_sent_log(sent_log)
        elif args.message:
            res = send_message(args.message, dry_run=args.dry_run)
        else:
            print("Provide --all-queued, --bundle-dir, or --message", file=sys.stderr)
            return 2
        print(json.dumps(res, indent=2))
        return 0
    except Exception as exc:
        print(f"Telegram delivery error: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
