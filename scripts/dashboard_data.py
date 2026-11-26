#!/usr/bin/env python3
"""
Step 7 (Prompt 8): Dashboard Data Builder (dashboard_data.py)

Rebuilds the single marked JSON data block inside dashboard/index.html from:
  - scoreboard.csv
  - learning.md (active experiments table)
  - data/publish_ledger.json (posting queue & YouTube titles)
  - data/pause_state.json + stuck_check.py (stuck-post flags & pause state)
  - data/campaigns.json + campaigns.py (11-platform & campaign earnings tracker)
  - data/daily_drops.json (4-window Telegram fallback automation history)
  - data/last_clip_report.json (stream-clipper quality & stranger-score report)

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from campaigns import compute_earnings_summary, load_campaigns_data  # noqa: E402
from stuck_check import run_health_check  # noqa: E402
from telegram_deliver import load_daily_drops  # noqa: E402

WAT = timezone(timedelta(hours=1))
SCOREBOARD_PATH = ROOT_DIR / "scoreboard.csv"
LEARNING_PATH = ROOT_DIR / "learning.md"
LEDGER_PATH = ROOT_DIR / "data" / "publish_ledger.json"
DAILY_METRICS_PATH = ROOT_DIR / "data" / "daily_metrics.json"
STRATEGY_PATH = ROOT_DIR / "data" / "strategy.json"
LAST_REPORT_PATH = ROOT_DIR / "data" / "last_clip_report.json"
DASHBOARD_HTML = ROOT_DIR / "dashboard" / "index.html"

START_MARKER = "/* === DASHBOARD_DATA_START === */"
END_MARKER = "/* === DASHBOARD_DATA_END === */"

SCHEDULE_WINDOWS = [
    {"id": "primary", "label": "09:37 WAT (Primary)", "time_wat": "09:37", "cron": "37 8 * * *"},
    {"id": "backup_1", "label": "12:17 WAT (Backup 1)", "time_wat": "12:17", "cron": "17 11 * * *"},
    {"id": "backup_2", "label": "15:43 WAT (Backup 2)", "time_wat": "15:43", "cron": "43 14 * * *"},
    {"id": "backup_3", "label": "19:19 WAT (Backup 3)", "time_wat": "19:19", "cron": "19 18 * * *"},
]


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


def parse_read_history_by_day(notes: str) -> dict[str, int]:
    """Extract latest view count per UTC date from read(YYYY-MM-DDTHH:MMZ):v=NNN tags in notes."""
    out: dict[str, int] = {}
    for day_str, val_str in re.findall(r"read\((\d{4}-\d{2}-\d{2})T\d{2}:\d{2}Z\):v=(\d+)", notes or ""):
        try:
            out[day_str] = max(out.get(day_str, 0), int(val_str))
        except ValueError:
            pass
    return out


def update_daily_metrics(
    rows: list[dict[str, str]],
    platform_totals: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Maintain real daily platform snapshots in data/daily_metrics.json (views, likes, shares, followers, posts).
    Backfills historical days from scoreboard.csv read(...) logs if missing, and refreshes today's live snapshot.
    """
    saved: dict[str, Any] = {"days": {}}
    if DAILY_METRICS_PATH.exists():
        try:
            saved = json.loads(DAILY_METRICS_PATH.read_text(encoding="utf-8"))
        except Exception:
            saved = {"days": {}}
    days_map: dict[str, dict[str, dict[str, int]]] = saved.setdefault("days", {})

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_dates: set[str] = {today_str}
    per_row_reads: list[tuple[str, str, dict[str, int], dict[str, str]]] = []
    for r in rows:
        plat = (r.get("platform") or "").strip().lower()
        d_str = (r.get("date") or "").strip()
        reads = parse_read_history_by_day(r.get("notes") or "")
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d_str) and d_str <= today_str:
            all_dates.add(d_str)
        for rd in reads:
            if rd <= today_str:
                all_dates.add(rd)
        per_row_reads.append((plat, d_str, reads, r))

    sorted_dates = sorted(all_dates)
    for d in sorted_dates:
        if d == today_str:
            days_map[d] = {
                p: {
                    "views": int(platform_totals[p]["views"]),
                    "likes": int(platform_totals[p]["likes"]),
                    "shares": int(platform_totals[p]["shares"]),
                    "followers": int(platform_totals[p]["followers"]),
                    "posts": int(platform_totals[p]["posts"]),
                }
                for p in ("youtube", "tiktok", "instagram")
            }
            continue
        if d in days_map:
            continue
        snap: dict[str, dict[str, int]] = {
            p: {"views": 0, "likes": 0, "shares": 0, "followers": 0, "posts": 0}
            for p in ("youtube", "tiktok", "instagram")
        }
        for plat, post_date, reads, r in per_row_reads:
            if plat not in snap:
                continue
            if post_date and post_date <= d:
                snap[plat]["posts"] += 1
            prior_days = [rd for rd in reads if rd <= d]
            if prior_days:
                v_at_d = reads[max(prior_days)]
                snap[plat]["views"] += v_at_d
                final_v = max(1, to_int(r.get("views_72h")) or to_int(r.get("views_24h")) or to_int(r.get("views_12h")) or 0)
                ratio = min(1.0, v_at_d / final_v) if final_v > 0 else 0.0
                snap[plat]["likes"] += round((to_int(r.get("likes")) or 0) * ratio)
                snap[plat]["shares"] += round((to_int(r.get("reposts_or_shares")) or 0) * ratio)
        days_map[d] = snap

    DAILY_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAILY_METRICS_PATH.write_text(json.dumps({"days": dict(sorted(days_map.items()))}, indent=2) + "\n", encoding="utf-8")

    series: list[dict[str, Any]] = []
    prev: dict[str, dict[str, int]] | None = None
    for d in sorted(days_map.keys()):
        cur = days_map[d]
        entry: dict[str, Any] = {"date": d}
        for p in ("youtube", "tiktok", "instagram"):
            cp = cur.get(p) or {"views": 0, "likes": 0, "shares": 0, "followers": 0, "posts": 0}
            pp = (prev.get(p) if prev else None) or {"views": 0, "likes": 0, "shares": 0, "followers": 0, "posts": 0}
            entry[p] = {
                "views": int(cp.get("views", 0)),
                "views_delta": max(0, int(cp.get("views", 0)) - int(pp.get("views", 0))),
                "likes": int(cp.get("likes", 0)),
                "likes_delta": max(0, int(cp.get("likes", 0)) - int(pp.get("likes", 0))),
                "shares": int(cp.get("shares", 0)),
                "followers": int(cp.get("followers", 0)),
                "posts": int(cp.get("posts", 0)),
            }
        series.append(entry)
        prev = cur
    return series


def build_youtube_diagnostics(clips: list[dict[str, Any]]) -> dict[str, Any]:
    yt_rows = [c for c in clips if c["platform"] == "youtube"]
    live_yt = [c for c in yt_rows if "http" in (c.get("notes") or "") or c["views"] > 0]
    low_or_zero = [c for c in live_yt if c["views"] < 20]
    breakouts = [c for c in live_yt if c["views"] >= 500]
    med_views = round(statistics.median([c["views"] for c in live_yt]), 1) if live_yt else 0.0
    lengths = [float(c["length_sec"]) for c in yt_rows if c.get("length_sec")]
    avg_len = round(sum(lengths) / len(lengths), 1) if lengths else 0.0
    long_30s = sum(1 for l in lengths if l >= 24.0)

    recommendations = [
        "Use Telegram Message 5 (standalone 40-62 char Shorts title) — never paste Credit/hashtags into the YouTube Title field.",
        "Keep Shorts in the 14-20s sweet spot with burned-in 2-3 word kinetic captions and zero >0.6s dead air.",
        "Enforce stranger comprehension >= 7/10 so viewers immediately understand the setup in the first 2 seconds.",
        "Post 3 high-conviction Shorts/day (spaced >= 2.5h apart) instead of flooding 5-8 uncaptioned 30s clips.",
    ]
    return {
        "total_shorts_tracked": len(yt_rows),
        "live_count": len(live_yt),
        "zero_or_low_count": len(low_or_zero),
        "breakout_count": len(breakouts),
        "median_views": med_views,
        "avg_length_sec": avg_len,
        "long_24s_plus_count": long_30s,
        "recommendations": recommendations,
    }


def build_automation_status() -> dict[str, Any]:
    now_wat = datetime.now(WAT)
    today_wat = now_wat.strftime("%Y-%m-%d")
    drops_data = load_daily_drops()
    days = drops_data.get("days") or {}
    today_rec = days.get(today_wat) or {
        "date_wat": today_wat,
        "status": "armed",
        "clips_delivered": 0,
        "target_clips": int(os.environ.get("DAILY_CLIP_COUNT", "3")),
        "post_ids": [],
        "slot_used": None,
        "attempts": [],
    }
    recent = [days[k] for k in sorted(days.keys(), reverse=True)[:14]]
    return {
        "today_wat": today_wat,
        "now_wat": now_wat.strftime("%Y-%m-%d %H:%M WAT"),
        "today_status": today_rec.get("status", "armed"),
        "today_slot_used": today_rec.get("slot_used"),
        "today_clips_delivered": int(today_rec.get("clips_delivered") or 0),
        "today_target_clips": int(today_rec.get("target_clips") or 3),
        "today_post_ids": today_rec.get("post_ids") or [],
        "schedule_windows": SCHEDULE_WINDOWS,
        "recent_drops": recent,
    }


def build_dashboard_payload() -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    if SCOREBOARD_PATH.exists():
        with SCOREBOARD_PATH.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8")) if LEDGER_PATH.exists() else {"entries": []}
    yt_titles_by_id: dict[str, str] = {}
    for e in ledger.get("entries", []):
        if e.get("platform") == "youtube" and e.get("youtube_title"):
            yt_titles_by_id[e.get("post_id", "")] = e["youtube_title"]

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
        pid = r.get("post_id", "")

        clip_item = {
            "post_id": pid,
            "date": r.get("date", ""),
            "time": r.get("time", ""),
            "platform": p,
            "pillar": r.get("pillar", ""),
            "format": r.get("format", ""),
            "hook": r.get("hook", ""),
            "yt_title": yt_titles_by_id.get(pid, ""),
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
            pt["series"].append({"label": f"{pid} ({r.get('date','')})", "views": views})

    live_clips = [c for c in clips if c["views"] > 0]
    top_clips = sorted(live_clips, key=lambda x: (x["share_rate"], x["views"]), reverse=True)[:5]

    working: list[str] = []
    not_working: list[str] = []
    if not live_clips:
        working.append("Stream-clipper pipeline ready: kinetic word captions, dead-air cuts, and clean Shorts titles enabled.")
        not_working.append("No live analytics reads yet — post the queued clips and run sync_stats.py.")
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
                f"Median share rate is {med_share:.2f}% (below 1.0% target) — prioritize instant-comprehension hooks."
            )
        low_clips = [c for c in live_clips if c["views"] < med_views * 0.5]
        if low_clips:
            not_working.append(f"{len(low_clips)} post(s) sat below 50% of account median ({int(med_views):,} plays).")

    queue_entries = ledger.get("entries", [])[-15:]
    health = run_health_check(apply_pauses=False)

    launch_date = datetime(2026, 11, 20, tzinfo=timezone.utc)
    days_live = max(0, (datetime.now(timezone.utc) - launch_date).days)
    max_followers = max((pt["followers"] for pt in platform_totals.values()), default=0)
    monetization = {
        "phase": "Phase 1 — Audience Only (Days 1-14)" if days_live < 14 else "Phase 2 — Eligible for Clipping Campaigns",
        "days_completed": days_live,
        "days_required_phase1": 14,
        "triggers": [
            {"stream": "Whop & Clipping Networks ($1-$3/1K)", "requirement": "No follower minimum — follow brief & submit live links", "ready": True},
            {"stream": "Instagram Gifts", "requirement": "500+ IG followers", "ready": platform_totals["instagram"]["followers"] >= 500},
            {"stream": "Bio Affiliates", "requirement": "14d + consistent reach on 1 app", "ready": days_live >= 14 and sum(p["views"] for p in platform_totals.values()) >= 10000},
            {"stream": "First Sponsors / Media Kit ($10-$25/1K)", "requirement": "10,000+ followers on 1 app", "ready": max_followers >= 10000},
            {"stream": "TikTok Creator Rewards (>60s clips)", "requirement": "10K TT followers + 100K 30d views + >60s format", "ready": platform_totals["tiktok"]["followers"] >= 10000},
        ],
    }

    timeseries = update_daily_metrics(rows, platform_totals)
    strategy: dict[str, Any] = {}
    if STRATEGY_PATH.exists():
        try:
            strategy = json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))
        except Exception:
            strategy = {}

    last_report: dict[str, Any] = {}
    if LAST_REPORT_PATH.exists():
        try:
            last_report = json.loads(LAST_REPORT_PATH.read_text(encoding="utf-8"))
        except Exception:
            last_report = {}

    earnings = compute_earnings_summary(load_campaigns_data())
    automation = build_automation_status()
    youtube_diagnostics = build_youtube_diagnostics(clips)

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
        "timeseries": timeseries,
        "strategy": strategy,
        "clips": clips,
        "top_clips": top_clips,
        "working": working,
        "not_working": not_working,
        "experiments": parse_experiments(LEARNING_PATH),
        "queue": queue_entries,
        "health": health,
        "monetization": monetization,
        "earnings": earnings,
        "automation": automation,
        "youtube_diagnostics": youtube_diagnostics,
        "last_report": last_report,
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
    print(
        f"Dashboard refreshed: {DASHBOARD_HTML} ({payload['totals']['posts']} clip rows, "
        f"${payload['earnings']['total_accumulated_usd']:.2f} tracked earnings)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
