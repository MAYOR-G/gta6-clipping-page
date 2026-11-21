#!/usr/bin/env python3
"""
Step 7 (Prompt 10): Stuck-Post Protocol & Distribution Health Check (stuck_check.py)

Rules enforced per platform (instagram, tiktok, youtube):
  1. Any live post with < 20 plays after >= 1 hour since publish is flagged STUCK.
  2. Checks publish_ledger.json vs scoreboard.csv to flag scheduled posts past due that
     never got confirmed live.
  3. 1 stuck post on a platform -> recommend checking IG Account Status / TikTok Studio,
     deleting stuck original, and reposting ONCE as a fresh cut (never same file SHA-256).
  4. 2 stuck posts in a row on one platform -> auto-pause that platform for 24h (via
     data/pause_state.json), keep other platforms running, and flag human.
  5. All active platforms stuck simultaneously -> trigger global pause (paused_all=True)
     and flag immediate urgent alert (even overnight).
  6. 4+ consecutive Instagram Reels stalling in the same low play band (200-260) -> flag
     IG Account Status & originality ladder audit (EXP-R6).

Every pause written here is reversible in one command via: python3 scripts/unpause.py
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
SCOREBOARD_PATH = ROOT_DIR / "scoreboard.csv"
LEDGER_PATH = ROOT_DIR / "data" / "publish_ledger.json"
PAUSE_PATH = ROOT_DIR / "data" / "pause_state.json"

STUCK_PLAYS_THRESHOLD = 20
STUCK_MIN_AGE_HOURS = 1.0
STUCK_MIN_AGE_BY_PLATFORM = {
    "instagram": 1.0,
    "tiktok": 1.0,
    "youtube": 12.0,  # New YouTube Shorts channels often take 2-12h for their first seed batch
}
IG_CEILING_LOW = 190
IG_CEILING_HIGH = 265
IG_CEILING_STREAK = 4
PLATFORMS = ("instagram", "tiktok", "youtube")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(date_str: str, time_str: str) -> datetime | None:
    if not date_str or not time_str:
        return None
    clean_t = time_str.replace("WAT", "").strip()
    try:
        dt = datetime.fromisoformat(f"{date_str.strip()}T{clean_t}")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=1)))  # WAT (UTC+1)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_iso(val: str) -> datetime | None:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=1)))
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_int(val: str | None) -> int | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(float(str(val).replace(",", "").strip()))
    except ValueError:
        return None


def latest_views(row: dict[str, str]) -> int | None:
    for col in ("views_72h", "views_24h", "views_12h"):
        v = parse_int(row.get(col))
        if v is not None:
            return v
    return None


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def run_health_check(
    scoreboard_path: Path = SCOREBOARD_PATH,
    ledger_path: Path = LEDGER_PATH,
    pause_path: Path = PAUSE_PATH,
    apply_pauses: bool = False,
) -> dict[str, Any]:
    now = utc_now()
    rows: list[dict[str, str]] = []
    if scoreboard_path.exists():
        with scoreboard_path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

    ledger = load_json(ledger_path, {"entries": []})
    pause_state = load_json(
        pause_path,
        {"paused_all": False, "reason": None, "platforms": {}, "last_profile_edit_utc": None, "history": []},
    )

    by_platform: dict[str, list[dict[str, Any]]] = {p: [] for p in PLATFORMS}
    for r in rows:
        p = (r.get("platform") or "").strip().lower()
        if p not in by_platform:
            continue
        dt = parse_dt(r.get("date", ""), r.get("time", ""))
        views = latest_views(r)
        notes = r.get("notes") or ""
        is_live = ("http://" in notes or "https://" in notes) or (views is not None)
        age_h = (now - dt).total_seconds() / 3600.0 if dt else 0.0
        min_age = STUCK_MIN_AGE_BY_PLATFORM.get(p, STUCK_MIN_AGE_HOURS)
        is_stuck = bool(is_live and views is not None and age_h >= min_age and views < STUCK_PLAYS_THRESHOLD)
        by_platform[p].append({
            "post_id": r.get("post_id"),
            "dt_utc": dt.isoformat() if dt else None,
            "age_hours": round(age_h, 2),
            "views": views,
            "is_live": is_live,
            "is_stuck": is_stuck,
            "hook": r.get("hook"),
        })

    # Check ledger entries whose scheduled time passed > 30 min ago without a live URL in scoreboard
    live_keys = {
        (r.get("post_id"), (r.get("platform") or "").strip().lower())
        for r in rows
        if ("http" in (r.get("notes") or "") or latest_views(r) is not None)
    }
    unconfirmed_scheduled: list[dict[str, Any]] = []
    for entry in ledger.get("entries", []):
        sched = parse_iso(entry.get("scheduled_time_utc", ""))
        if not sched:
            continue
        if (now - sched).total_seconds() >= 1800:
            key = (entry.get("post_id"), (entry.get("platform") or "").lower())
            if key not in live_keys and entry.get("status") != "queued_local":
                unconfirmed_scheduled.append({
                    "post_id": entry.get("post_id"),
                    "platform": entry.get("platform"),
                    "scheduled_time_utc": entry.get("scheduled_time_utc"),
                    "status": entry.get("status"),
                })

    stuck_posts: list[dict[str, Any]] = []
    platforms_with_stuck_latest: list[str] = []
    platforms_two_in_a_row: list[str] = []
    actions: list[str] = []
    ig_band_alert = False

    for p, plist in by_platform.items():
        live_posts = [x for x in plist if x["is_live"]]
        live_posts.sort(key=lambda x: x["dt_utc"] or "")
        for item in live_posts:
            if item["is_stuck"]:
                stuck_posts.append({"platform": p, **item})

        if live_posts and live_posts[-1]["is_stuck"]:
            platforms_with_stuck_latest.append(p)
        if len(live_posts) >= 2 and live_posts[-1]["is_stuck"] and live_posts[-2]["is_stuck"]:
            platforms_two_in_a_row.append(p)

        if p == "instagram" and len(live_posts) >= IG_CEILING_STREAK:
            last_n = live_posts[-IG_CEILING_STREAK:]
            if all(
                x["views"] is not None and IG_CEILING_LOW <= x["views"] <= IG_CEILING_HIGH
                for x in last_n
            ):
                ig_band_alert = True
                actions.append(
                    f"IG_CEILING_ALERT: Last {IG_CEILING_STREAK} Instagram Reels stopped in {IG_CEILING_LOW}-{IG_CEILING_HIGH} band. "
                    "Check IG Account Status and trigger EXP-R6 (Originality Ladder) before rewriting hooks."
                )

    urgent_overnight = False
    active_platforms = [p for p in PLATFORMS if any(x["is_live"] for x in by_platform[p])]
    if len(active_platforms) >= 2 and set(active_platforms).issubset(set(platforms_with_stuck_latest)):
        urgent_overnight = True
        actions.append(
            "CRITICAL: All active platforms are stuck simultaneously -> PAUSE ALL and alert human immediately (even overnight)."
        )
        if apply_pauses:
            pause_state["paused_all"] = True
            pause_state["reason"] = "All active platforms stuck simultaneously"
            pause_state.setdefault("history", []).append({
                "at_utc": now.isoformat(),
                "action": "auto_pause_all",
                "reason": pause_state["reason"],
            })
    else:
        already_paused = set(pause_state.get("paused_pairs", []))
        newly_paused = False
        for p in platforms_two_in_a_row:
            live_p = [x for x in by_platform[p] if x["is_live"]]
            live_p.sort(key=lambda x: x["dt_utc"] or "")
            pair_key = f"{p}:{live_p[-2]['post_id']}:{live_p[-1]['post_id']}" if len(live_p) >= 2 else p
            if pair_key in already_paused:
                continue
            actions.append(
                f"PAUSE_PLATFORM: {p} has 2 stuck posts in a row ({pair_key}) -> pause {p} for 24h, keep other platforms running, flag human."
            )
            if apply_pauses:
                until = (now + timedelta(hours=24)).isoformat()
                pause_state.setdefault("platforms", {})[p] = {
                    "paused": True,
                    "until_utc": until,
                    "reason": f"2 consecutive stuck posts ({pair_key})",
                }
                pause_state.setdefault("paused_pairs", []).append(pair_key)
                pause_state.setdefault("history", []).append({
                    "at_utc": now.isoformat(),
                    "action": f"auto_pause_{p}_24h",
                    "pair": pair_key,
                    "until_utc": until,
                })
                newly_paused = True

    if apply_pauses and (urgent_overnight or newly_paused):
        save_json(pause_path, pause_state)

    return {
        "checked_at_utc": now.isoformat(),
        "stuck_posts": stuck_posts,
        "unconfirmed_scheduled": unconfirmed_scheduled,
        "platforms_two_in_a_row": platforms_two_in_a_row,
        "ig_ceiling_alert": ig_band_alert,
        "urgent_alert_even_overnight": urgent_overnight,
        "recommended_actions": actions,
        "pause_state": {
            "paused_all": pause_state.get("paused_all", False),
            "platforms": pause_state.get("platforms", {}),
            "last_profile_edit_utc": pause_state.get("last_profile_edit_utc"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run stuck-post health check per platform.")
    parser.add_argument("--apply-pauses", action="store_true", help="Automatically update data/pause_state.json if thresholds trip")
    parser.add_argument("--scoreboard", type=Path, default=SCOREBOARD_PATH)
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--pause-file", type=Path, default=PAUSE_PATH)
    args = parser.parse_args()

    result = run_health_check(args.scoreboard, args.ledger, args.pause_file, apply_pauses=args.apply_pauses)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
