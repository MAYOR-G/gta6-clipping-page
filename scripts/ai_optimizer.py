#!/usr/bin/env python3
"""
Self-Improving AI Performance Optimizer (ai_optimizer.py)

100% FREE ($0/mo) via OpenRouter free models (nvidia/nemotron-3.5-super-vl:free /
nvidia/nemotron-3.5-lightning:free / openrouter/free) with deterministic analytics fallback.

Every day (and on every 6h sync), this script:
  1. Reads scoreboard.csv across YouTube Shorts, TikTok, and Instagram Reels.
  2. Computes real performance benchmarks:
       - Top-performing clips vs. underperforming flops
       - Per-streamer median & max plays
       - Duration bucket performance (short 12-22s vs medium 23-35s)
       - Platform health (views, likes, shares, followers, like rate, share rate)
  3. Sends that structured scorecard to free OpenRouter AI to synthesize actionable
     editing/scouting rules into data/strategy.json (consumed automatically by
     scripts/daily_cloud_pipeline.py AND displayed on dashboard/index.html).
  4. Appends concise dated insights to learning.md without duplicating the same day.

Security: reads OPENROUTER_API_KEY strictly from env vars / local .env and NEVER prints keys.
"""

from __future__ import annotations

import csv
import json
import os
import re
import statistics
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
SCOREBOARD_PATH = ROOT_DIR / "scoreboard.csv"
STRATEGY_PATH = ROOT_DIR / "data" / "strategy.json"
LEARNING_PATH = ROOT_DIR / "learning.md"


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


def to_int(val: str | None) -> int:
    if val is None or str(val).strip() == "":
        return 0
    try:
        return int(float(str(val).replace(",", "").strip()))
    except ValueError:
        return 0


def to_float(val: str | None) -> float:
    if val is None or str(val).strip() == "":
        return 0.0
    try:
        return float(str(val).strip())
    except ValueError:
        return 0.0


def clean_streamer_handle(credit: str) -> str:
    raw = (credit or "").split("(")[0].strip().lstrip("@").strip()
    return raw.lower()


def compute_scorecard_summary(scoreboard_path: Path = SCOREBOARD_PATH) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    if scoreboard_path.exists():
        with scoreboard_path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

    by_clip: dict[str, dict[str, Any]] = {}
    by_streamer: dict[str, list[int]] = {}
    by_platform: dict[str, dict[str, int]] = {
        p: {"views": 0, "likes": 0, "shares": 0, "comments": 0, "followers": 0, "live_posts": 0}
        for p in ("youtube", "tiktok", "instagram")
    }
    short_views: list[int] = []   # <= 22s
    medium_views: list[int] = []  # > 22s

    for r in rows:
        pid = (r.get("post_id") or "").strip()
        plat = (r.get("platform") or "").strip().lower()
        views = to_int(r.get("views_72h")) or to_int(r.get("views_24h")) or to_int(r.get("views_12h"))
        likes = to_int(r.get("likes"))
        shares = to_int(r.get("reposts_or_shares"))
        comments = to_int(r.get("replies_or_comments"))
        followers = to_int(r.get("followers_at_read"))
        length_sec = to_float(r.get("length_sec"))
        notes = r.get("notes") or ""
        is_live = ("http" in notes) or views > 0

        if plat in by_platform:
            bp = by_platform[plat]
            bp["views"] += views
            bp["likes"] += likes
            bp["shares"] += shares
            bp["comments"] += comments
            bp["followers"] = max(bp["followers"], followers)
            if is_live:
                bp["live_posts"] += 1

        if pid not in by_clip:
            by_clip[pid] = {
                "post_id": pid,
                "credit": r.get("credit", ""),
                "streamer": clean_streamer_handle(r.get("credit", "")),
                "hook": r.get("hook", ""),
                "length_sec": length_sec,
                "total_views": 0,
                "yt_views": 0,
                "tt_views": 0,
                "ig_views": 0,
                "total_likes": 0,
                "total_shares": 0,
                "is_live": False,
            }
        c = by_clip[pid]
        c["total_views"] += views
        c["total_likes"] += likes
        c["total_shares"] += shares
        if plat == "youtube":
            c["yt_views"] = views
        elif plat == "tiktok":
            c["tt_views"] = views
        elif plat == "instagram":
            c["ig_views"] = views
        if is_live:
            c["is_live"] = True

    live_clips = [c for c in by_clip.values() if c["is_live"]]
    for c in live_clips:
        if c["streamer"]:
            by_streamer.setdefault(c["streamer"], []).append(c["total_views"])
        if c["length_sec"] > 0:
            if c["length_sec"] <= 22.0:
                short_views.append(c["total_views"])
            else:
                medium_views.append(c["total_views"])

    live_clips.sort(key=lambda x: x["total_views"], reverse=True)
    winners = live_clips[:5]
    flops = sorted([c for c in live_clips if c["total_views"] <= 150], key=lambda x: x["total_views"])[:5]

    streamer_ranking = sorted(
        [
            {
                "streamer": s,
                "clips": len(vs),
                "avg_views": round(sum(vs) / len(vs), 1),
                "max_views": max(vs),
            }
            for s, vs in by_streamer.items()
        ],
        key=lambda x: (x["avg_views"], x["max_views"]),
        reverse=True,
    )

    median_clip_views = round(statistics.median([c["total_views"] for c in live_clips]), 1) if live_clips else 0.0
    return {
        "total_clips_live": len(live_clips),
        "total_clips_tracked": len(by_clip),
        "median_clip_views": median_clip_views,
        "by_platform": by_platform,
        "duration_comparison": {
            "short_12_22s_count": len(short_views),
            "short_12_22s_median": round(statistics.median(short_views), 1) if short_views else 0.0,
            "medium_23_35s_count": len(medium_views),
            "medium_23_35s_median": round(statistics.median(medium_views), 1) if medium_views else 0.0,
        },
        "top_winners": winners,
        "bottom_flops": flops,
        "streamer_ranking": streamer_ranking[:8],
    }


def deterministic_strategy(summary: dict[str, Any]) -> dict[str, Any]:
    winners = summary.get("top_winners") or []
    ranking = summary.get("streamer_ranking") or []
    priority_streamers = [r["streamer"] for r in ranking[:5] if r.get("max_views", 0) >= 150] or ["ohnepixel", "caseoh_", "jynxzi", "xqc"]
    top_desc = (
        f"{winners[0]['post_id']} ({winners[0]['credit']}, {winners[0]['length_sec']:.0f}s) hit {winners[0]['total_views']:,} total views"
        if winners else "Awaiting initial live view reads"
    )
    return {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_used": "deterministic-fallback",
        "min_virality_score": 7,
        "priority_streamers": priority_streamers,
        "winning_patterns": [
            "Instant-karma vehicle crashes, police chases, robberies, and explosive physics fails within the first 2 seconds",
            "High-energy shocked facecam reactions stacked cleanly in the top 40% frame",
            "Tight comedic hooks that name a relatable fail or setup before the payoff hits",
        ],
        "avoid_patterns": [
            "Slow in-game phone/UI menus, inventory browsing, or reading text on screen",
            "Aimless walking/driving with no climax, punchline, or sudden chaos",
            "No-facecam gameplay or static Just Chatting commentary without GTA 6 action",
        ],
        "hook_style_guidance": "Lead with an immediate tension/irony premise (4-7 ALL CAPS words) that sets up the visual payoff without describing it literally.",
        "daily_brief": f"{top_desc}. Prioritize high-action instant-karma & loud-reaction clips (>=7/10 virality) and reject slow menu/phone scenes.",
        "scorecard_snapshot": {
            "total_clips_live": summary["total_clips_live"],
            "median_clip_views": summary["median_clip_views"],
            "top_post_id": winners[0]["post_id"] if winners else None,
            "top_post_views": winners[0]["total_views"] if winners else 0,
            "short_vs_medium": summary["duration_comparison"],
        },
    }


def synthesize_strategy_with_openrouter(summary: dict[str, Any]) -> dict[str, Any]:
    fallback = deterministic_strategy(summary)
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key or summary.get("total_clips_live", 0) == 0:
        return fallback

    models = [
        "nvidia/nemotron-3.5-super-vl:free",
        "nvidia/nemotron-3.5-lightning:free",
        "openrouter/free",
    ]
    prompt = (
        "You are the Growth Optimization AI for a faceless GTA 6 streamer clipping page (Leonida Chaos) posting on YouTube Shorts, TikTok, and Instagram Reels.\n"
        "Analyze this REAL performance scorecard from scoreboard.csv and produce concrete self-improving rules for the next clip drop:\n"
        f"{json.dumps(summary, indent=2)}\n\n"
        "Return STRICT JSON ONLY with keys:\n"
        "- winning_patterns: array of 3 crisp, specific bullet strings explaining WHY the top clips outperformed (hook style, action type, pacing, duration)\n"
        "- avoid_patterns: array of 3 crisp, specific bullet strings naming exact clip types/scenes to REJECT based on the 2-6 view flops\n"
        "- priority_streamers: array of 4 to 6 lowercase streamer handles (from winners or high-energy GTA 6 creators) to scout first\n"
        "- hook_style_guidance: 1 concrete sentence telling the copywriter how to write higher-retention Frame-1 hooks\n"
        "- min_virality_score: integer 7 or 8 (strictness threshold out of 10)\n"
        "- daily_brief: 2 concise, plain-English sentences summarizing today's biggest win, biggest leak, and exact adjustment for tomorrow\n"
        "No markdown fences, no emojis, strict JSON only."
    )

    for model in models:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
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
                    "X-Title": "Leonida Chaos Self-Improving Optimizer",
                },
            )
            with urllib.request.urlopen(req, timeout=22) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            raw = body["choices"][0]["message"].get("content") or ""
            if not raw.strip():
                continue
            m_json = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            parsed = json.loads(m_json.group(0) if m_json else raw)

            win_p = [str(x).strip() for x in (parsed.get("winning_patterns") or []) if str(x).strip()][:3]
            avoid_p = [str(x).strip() for x in (parsed.get("avoid_patterns") or []) if str(x).strip()][:3]
            prio = [re.sub(r"[^a-z0-9_]", "", str(x).lower()) for x in (parsed.get("priority_streamers") or [])]
            prio = [x for x in prio if x][:6]
            min_v = int(parsed.get("min_virality_score") or 7)
            min_v = max(6, min(8, min_v))

            return {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "model_used": model,
                "min_virality_score": min_v,
                "priority_streamers": prio or fallback["priority_streamers"],
                "winning_patterns": win_p or fallback["winning_patterns"],
                "avoid_patterns": avoid_p or fallback["avoid_patterns"],
                "hook_style_guidance": str(parsed.get("hook_style_guidance") or fallback["hook_style_guidance"]).strip(),
                "daily_brief": str(parsed.get("daily_brief") or fallback["daily_brief"]).strip(),
                "scorecard_snapshot": fallback["scorecard_snapshot"],
            }
        except Exception:
            continue
    return fallback


def append_learning_log(strategy: dict[str, Any], learning_path: Path = LEARNING_PATH) -> None:
    if not learning_path.exists():
        return
    day_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    marker = f"### AI Self-Improvement Review ({day_tag})"
    text = learning_path.read_text(encoding="utf-8")
    if marker in text:
        return
    entry = (
        f"\n{marker}\n"
        f"- **Model:** `{strategy.get('model_used')}` | **Virality Gate:** `>= {strategy.get('min_virality_score', 7)}/10`\n"
        f"- **Brief:** {strategy.get('daily_brief')}\n"
        f"- **Priority Creators:** {', '.join(strategy.get('priority_streamers') or [])}\n"
        f"- **Double Down:** {'; '.join(strategy.get('winning_patterns') or [])}\n"
        f"- **Reject / Avoid:** {'; '.join(strategy.get('avoid_patterns') or [])}\n"
    )
    learning_path.write_text(text.rstrip() + "\n" + entry, encoding="utf-8")


def run_optimizer() -> dict[str, Any]:
    load_local_dotenv()
    summary = compute_scorecard_summary()
    strategy = synthesize_strategy_with_openrouter(summary)
    STRATEGY_PATH.parent.mkdir(parents=True, exist_ok=True)
    STRATEGY_PATH.write_text(json.dumps(strategy, indent=2) + "\n", encoding="utf-8")
    append_learning_log(strategy)
    return strategy


def main() -> int:
    strategy = run_optimizer()
    print(json.dumps(strategy, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
