#!/usr/bin/env python3
"""
Step 7 (Prompt 8): Dashboard Data Builder (dashboard_data.py)

Rebuilds the single marked JSON data block inside dashboard/index.html from:
  - scoreboard.csv
  - learning.md (active experiments table)
  - data/publish_ledger.json (posting queue)
  - data/pause_state.json + stuck_check.py (stuck-post flags & pause state)

Swaps ONLY the content between:
  /* === DASHBOARD_DATA_START === */
  /* === DASHBOARD_DATA_END === */
so dashboard/index.html requires zero network access and refreshes cleanly every run.
"""

from __future__ import annotations

import csv
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from stuck_check import run_health_check  # noqa: E402

SCOREBOARD_PATH = ROOT_DIR / "scoreboard.csv"
LEARNING_PATH = ROOT_DIR / "learning.md"
LEDGER_PATH = ROOT_DIR / "data" / "publish_ledger.json"
DASHBOARD_HTML = ROOT_DIR / "dashboard" / "index.html"

START_MARKER = "/* === DASHBOARD_DATA_START === */"
END_MARKER = "/* === DASHBOARD_DATA_END === */"


def to_int(val: str | None) -> int | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(float(str(val).replace(",", "").strip()))
    except ValueError:
        return None


def to_float(val: str | None) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(str(val).strip())
    except ValueError:
        return None


def parse_experiments(learning_md: Path) -> list[dict[str, str]]:
    if not learning_md.exists():
        return []
    exps: list[dict[str, str]] = []
    for line in learning_md.read_text(encoding="utf-8").splitlines():
        if line.startswith("| **EXP-"):
            cols = [c.strip().replace("**", "") for c in line.strip("|").split("|")]
            if len(cols) >= 6:
                exps.append({
                    "id": cols[0],
                    "status": cols[1],
                    "variable": cols[2],
                    "sample": cols[3],
                    "metric": cols[4],
                    "kill_condition": cols[5],
                })
    return exps


def build_dashboard_payload() -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    if SCOREBOARD_PATH.exists():
        with SCOREBOARD_PATH.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

    clips = []
    platform_totals: dict[str, dict[str, Any]] = {
        p: {"views": 0, "likes": 0, "shares": 0, "comments": 0, "posts": 0, "followers": 0, "series": []}
        for p in ("instagram", "tiktok", "youtube")
    }

    for r in rows:
        p = (r.get("platform") or "").strip().lower()
        views = to_int(r.get("views_72h")) or to_int(r.get("views_24h")) or to_int(r.get("views_12h")) or 0
        likes = to_int(r.get("likes")) or 0
        shares = to_int(r.get("reposts_or_shares")) or 0
        comments = to_int(r.get("replies_or_comments")) or 0
        saves = to_int(r.get("saves_or_bookmarks")) or 0
        followers = to_int(r.get("followers_at_read"))
        length_sec = to_float(r.get("length_sec")) or 0.0

        share_rate = round((shares / views) * 100, 2) if views > 0 else 0.0
        like_rate = round((likes / views) * 100, 2) if views > 0 else 0.0
        comment_rate = round((comments / views) * 100, 2) if views > 0 else 0.0

        clip_item = {
            "post_id": r.get("post_id", ""),
            "date": r.get("date", ""),
            "time": r.get("time", ""),
            "platform": p,
            "pillar": r.get("pillar", ""),
            "format": r.get("format", ""),
            "hook": r.get("hook", ""),
            "length_sec": length_sec,
            "credit": r.get("credit", ""),
            "views": views,
            "likes": likes,
            "shares": shares,
            "comments": comments,
            "saves": saves,
            "share_rate": share_rate,
            "like_rate": like_rate,
            "comment_rate": comment_rate,
            "test_name": r.get("test_name", ""),
            "notes": r.get("notes", ""),
        }
        clips.append(clip_item)

        if p in platform_totals:
            pt = platform_totals[p]
            pt["views"] += views
            pt["likes"] += likes
            pt["shares"] += shares
            pt["comments"] += comments
            pt["posts"] += 1
            if followers is not None:
                pt["followers"] = max(pt["followers"], followers)
            pt["series"].append({"label": f"{r.get('post_id','')} ({r.get('date','')})", "views": views})

    live_clips = [c for c in clips if c["views"] > 0]
    top_clips = sorted(live_clips, key=lambda x: (x["share_rate"], x["views"]), reverse=True)[:5]

    # Plain-English "What's working / What's not"
    working: list[str] = []
    not_working: list[str] = []
    if not live_clips:
        working.append("Pipeline ready: 3 test clips cut & approved, queued cleanly across Instagram, TikTok, and YouTube Shorts.")
        not_working.append("No live analytics reads yet — post the first 3 approved clips and log 2h / 24h numbers in scoreboard.csv.")
    else:
        med_views = statistics.median([c["views"] for c in live_clips])
        med_share = statistics.median([c["share_rate"] for c in live_clips])
        best = top_clips[0]
        working.append(
            f"Top performer: {best['post_id']} ({best['platform']}) — {best['views']:,} plays, "
            f"{best['share_rate']}% share rate ({best['credit']})."
        )
        if med_share >= 1.0:
            working.append(f"Account median share rate ({med_share:.2f}%) is clearing the 1.0% growth target.")
        else:
            not_working.append(
                f"Median share rate is {med_share:.2f}% (below 1.0% target) — double down on relatable / NPC chaos clips."
            )
        low_clips = [c for c in live_clips if c["views"] < med_views * 0.5]
        if low_clips:
            not_working.append(f"{len(low_clips)} post(s) sat below 50% of account median ({int(med_views):,} plays).")

    # Posting queue from ledger
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8")) if LEDGER_PATH.exists() else {"entries": []}
    queue_entries = ledger.get("entries", [])[-15:]

    # Health check & stuck flags
    health = run_health_check(apply_pauses=False)

    # Monetization readiness (2-week audience-only gate)
    launch_date = datetime(2026, 11, 20, tzinfo=timezone.utc)
    days_live = max(0, (datetime.now(timezone.utc) - launch_date).days)
    max_followers = max((pt["followers"] for pt in platform_totals.values()), default=0)
    monetization = {
        "phase": "Phase 1 — Audience Only (Days 1-14)" if days_live < 14 else "Phase 2 — Eligible for Monetization Review",
        "days_completed": days_live,
        "days_required_phase1": 14,
        "triggers": [
            {"stream": "Whop Content Rewards ($1-$1.50/1K)", "requirement": "14d audience baseline (no follower min)", "ready": days_live >= 14},
            {"stream": "Instagram Gifts", "requirement": "500+ IG followers", "ready": platform_totals["instagram"]["followers"] >= 500},
            {"stream": "Amazon / Logitech Bio Affiliates", "requirement": "14d + consistent reach on 1 app", "ready": days_live >= 14 and sum(p["views"] for p in platform_totals.values()) >= 10000},
            {"stream": "First Sponsors / Media Kit ($10-$25/1K)", "requirement": "10,000+ followers on 1 app", "ready": max_followers >= 10000},
            {"stream": "TikTok Creator Rewards (>60s clips)", "requirement": "10K TT followers + 100K 30d views + >60s format", "ready": platform_totals["tiktok"]["followers"] >= 10000},
        ],
    }

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "page_name": os.environ.get("PAGE_NAME", "Leonida Chaos"),
        "timezone": "WAT (UTC+1)",
        "platforms": platform_totals,
        "totals": {
            "views": sum(p["views"] for p in platform_totals.values()),
            "likes": sum(p["likes"] for p in platform_totals.values()),
            "shares": sum(p["shares"] for p in platform_totals.values()),
            "comments": sum(p["comments"] for p in platform_totals.values()),
            "followers": sum(p["followers"] for p in platform_totals.values()),
            "posts": len(clips),
        },
        "clips": clips,
        "top_clips": top_clips,
        "working": working,
        "not_working": not_working,
        "experiments": parse_experiments(LEARNING_PATH),
        "queue": queue_entries,
        "health": health,
        "monetization": monetization,
    }


def update_dashboard_html(payload: dict[str, Any], html_path: Path = DASHBOARD_HTML) -> None:
    if not html_path.exists():
        raise FileNotFoundError(f"Dashboard HTML not found: {html_path}")
    html = html_path.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        flags=re.DOTALL,
    )
    if not pattern.search(html):
        raise ValueError(f"Markers {START_MARKER} / {END_MARKER} not found in {html_path}")
    block = f"{START_MARKER}\nconst DASHBOARD_DATA = {json.dumps(payload, indent=2)};\n{END_MARKER}"
    updated = pattern.sub(lambda _: block, html, count=1)
    html_path.write_text(updated, encoding="utf-8")


def main() -> int:
    payload = build_dashboard_payload()
    update_dashboard_html(payload)
    print(f"Dashboard refreshed: {DASHBOARD_HTML} ({payload['totals']['posts']} clip rows, {len(payload['queue'])} queued)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
