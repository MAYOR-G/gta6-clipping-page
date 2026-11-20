#!/usr/bin/env python3
"""
Step 7: Deterministic 2-Hour CEO Loop Runner (ceo_runner.py)

Runs the zero-cost deterministic parts of every 2-hour cycle:
  1. Health Check & Stuck-Post Protocol (scripts/stuck_check.py --apply-pauses)
  2. Disk Hygiene (delete raw files older than 24h)
  3. Rebuild Live Dashboard (scripts/dashboard_data.py)
  4. Quiet-Hours Gate (23:00-07:00 WAT stays silent unless critical alert; 07:00 WAT sends recap)
  5. Optional Telegram notification if TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID are set

Pair this with Antigravity `/schedule` (using routines/ceo_routine_prompt.md) for the full
AI CEO loop (scouting, visual contact-sheet picking, cutting, QA, and research).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from dashboard_data import build_dashboard_payload, update_dashboard_html  # noqa: E402
from stuck_check import run_health_check  # noqa: E402
from telegram_deliver import send_message  # noqa: E402

WAT = timezone(timedelta(hours=1))
RAW_DIR = ROOT_DIR / "raw"
MAX_RAW_AGE_SECONDS = 24 * 3600


def clean_old_raw_files(raw_dir: Path = RAW_DIR) -> list[str]:
    removed: list[str] = []
    if not raw_dir.exists():
        return removed
    now = time.time()
    for p in raw_dir.rglob("*"):
        if p.is_file() and (now - p.stat().st_mtime) > MAX_RAW_AGE_SECONDS:
            p.unlink(missing_ok=True)
            removed.append(str(p.relative_to(ROOT_DIR)))
    return removed


def in_quiet_hours(now_wat: datetime) -> bool:
    return now_wat.hour >= 23 or now_wat.hour < 7


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic 2-hour CEO maintenance & alert cycle.")
    parser.add_argument("--notify-telegram", action="store_true", help="Send update to Telegram (respects quiet hours)")
    parser.add_argument("--force-recap", action="store_true", help="Send recap even during quiet hours")
    args = parser.parse_args()

    now_wat = datetime.now(WAT)
    health = run_health_check(apply_pauses=True)
    removed_raw = clean_old_raw_files()
    payload = build_dashboard_payload()
    update_dashboard_html(payload)

    quiet = in_quiet_hours(now_wat)
    is_urgent = bool(health.get("urgent_alert_even_overnight"))
    is_7am_recap = now_wat.hour == 7

    summary_lines = [
        f"GTA 6 CEO Cycle — {now_wat.strftime('%Y-%m-%d %H:%M WAT')}",
        f"Total Views: {payload['totals']['views']:,} | Shares: {payload['totals']['shares']:,} | Posts: {payload['totals']['posts']}",
        f"Stuck Posts: {len(health['stuck_posts'])} | Paused All: {health['pause_state']['paused_all']}",
        f"Raw Cleanup (>24h): {len(removed_raw)} file(s) removed",
    ]
    if health["recommended_actions"]:
        summary_lines.append("Alerts:\n- " + "\n- ".join(health["recommended_actions"]))
    summary_text = "\n".join(summary_lines)

    notified = False
    if args.notify_telegram and os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        if is_urgent or is_7am_recap or args.force_recap or not quiet:
            send_message(summary_text)
            notified = True

    print(json.dumps({
        "ran_at_wat": now_wat.isoformat(),
        "quiet_hours": quiet,
        "urgent": is_urgent,
        "raw_cleaned": len(removed_raw),
        "dashboard_updated": True,
        "telegram_notified": notified,
        "summary": summary_text,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
