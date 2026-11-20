#!/usr/bin/env python3
"""
Step 1 (Prompt 1): Research the GTA 6 funny-moments clip lane.

Pulls recent high-performing posts across 6-8 search terms from:
  - TikTok & Instagram via Scrape Creators API (if SCRAPE_CREATORS_API_KEY is set)
  - YouTube Shorts via yt-dlp (always free, no key required)

Outputs median plays, share rate, like rate, and comment rate by video length
and caption style, plus the top 10 posts by share rate (or like rate on YT Shorts
where share count is private). Always labels output as a WINNERS' SAMPLE.

Security: reads API keys only from environment variables and NEVER prints them.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TERMS = [
    "gta 6 funny",
    "gta 6 streamer",
    "gta 6 npc",
    "gta 6 cops",
    "gta 6 lucia",
    "gta 6 fails",
    "ishowspeed gta 6",
    "ohnepixel gta 6",
]


def classify_length_bucket(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "unknown"
    if seconds < 12:
        return "<12s"
    if seconds <= 16:
        return "12-16s (sweet spot)"
    if seconds <= 20:
        return "17-20s"
    if seconds <= 35:
        return "21-35s"
    return "36-60s"


def classify_caption_style(text: str) -> str:
    clean = (text or "").strip()
    if not clean:
        return "empty/minimal"
    words = [w for w in clean.split() if not w.startswith("#")]
    if clean.endswith("?") or "?" in " ".join(words):
        return "question"
    if len(words) <= 6:
        return "short_punchy (1-6 words)"
    if len(words) <= 14:
        return "medium (7-14 words)"
    return "long (15+ words)"


def safe_median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def scrape_creators_get(path: str, params: dict[str, str], api_key: str) -> dict[str, Any]:
    qs = urllib.parse.urlencode(params)
    url = f"https://api.scrapecreators.com{path}?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "x-api-key": api_key,
            "User-Agent": "gta6-lane-research/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pull_tiktok_via_scrape_creators(terms: list[str], api_key: str, credit_cap: int) -> tuple[list[dict[str, Any]], int]:
    posts: list[dict[str, Any]] = []
    credits_used = 0
    for term in terms:
        if credits_used >= credit_cap:
            break
        try:
            data = scrape_creators_get(
                "/v1/tiktok/search/keyword",
                {"query": term, "sort_by": "most-liked", "date_posted": "yesterday"},
                api_key,
            )
            credits_used += 1
            items = data.get("search_item_list") or data.get("data") or []
            for entry in items:
                aweme = entry.get("aweme_info") or entry
                stats = aweme.get("statistics") or {}
                plays = stats.get("play_count") or 0
                likes = stats.get("digg_count") or 0
                shares = stats.get("share_count") or 0
                comments = stats.get("comment_count") or 0
                dur_ms = (aweme.get("video") or {}).get("duration") or 0
                dur_sec = round(dur_ms / 1000.0, 1) if dur_ms > 200 else float(dur_ms)
                author = (aweme.get("author") or {}).get("unique_id") or "unknown"
                vid_id = aweme.get("aweme_id") or aweme.get("id") or ""
                posts.append({
                    "platform": "tiktok",
                    "term": term,
                    "id": str(vid_id),
                    "author": f"@{author}",
                    "caption": aweme.get("desc") or "",
                    "duration_sec": dur_sec,
                    "plays": plays,
                    "likes": likes,
                    "shares": shares,
                    "comments": comments,
                    "url": f"https://www.tiktok.com/@{author}/video/{vid_id}" if vid_id else "",
                })
        except Exception as exc:
            print(f"[warn] TikTok term '{term}' failed: {type(exc).__name__}", file=sys.stderr)
    return posts, credits_used


def pull_youtube_shorts(terms: list[str], per_term: int = 8) -> list[dict[str, Any]]:
    def search_term(term: str) -> list[dict[str, Any]]:
        query = f"ytsearch{per_term}:{term} #shorts"
        try:
            out = subprocess.check_output(
                ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", query],
                text=True,
                timeout=25,
            )
        except Exception:
            return []
        found = []
        for line in out.strip().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            dur = d.get("duration")
            if dur and dur <= 65:
                found.append({
                    "platform": "youtube_shorts",
                    "term": term,
                    "id": d.get("id"),
                    "author": d.get("channel") or d.get("uploader") or "unknown",
                    "caption": d.get("title") or "",
                    "duration_sec": float(dur),
                    "plays": d.get("view_count") or 0,
                    "likes": None,
                    "shares": None,  # YouTube Shorts share counts are private
                    "comments": None,
                    "url": f"https://www.youtube.com/shorts/{d.get('id')}",
                })
        return found

    def enrich(post: dict[str, Any]) -> dict[str, Any]:
        try:
            out = subprocess.check_output(
                [
                    "yt-dlp",
                    "--skip-download",
                    "--dump-json",
                    "--no-warnings",
                    f"https://www.youtube.com/watch?v={post['id']}",
                ],
                text=True,
                timeout=15,
            )
            d = json.loads(out.strip().splitlines()[0])
            post["plays"] = d.get("view_count") or post["plays"]
            post["likes"] = d.get("like_count")
            post["comments"] = d.get("comment_count")
            post["upload_date"] = d.get("upload_date")
            post["duration_sec"] = float(d.get("duration") or post["duration_sec"])
        except Exception:
            pass
        return post

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        nested = list(ex.map(search_term, terms))

    dedup: dict[str, dict[str, Any]] = {}
    for batch in nested:
        for p in batch:
            if p["id"] and p["id"] not in dedup:
                dedup[p["id"]] = p

    candidates = sorted(dedup.values(), key=lambda x: x["plays"], reverse=True)[:24]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        enriched = list(ex.map(enrich, candidates))
    return enriched


def compute_rates(post: dict[str, Any]) -> dict[str, Any]:
    plays = post.get("plays") or 0
    likes = post.get("likes")
    shares = post.get("shares")
    comments = post.get("comments")
    post["share_rate"] = round((shares / plays) * 100, 3) if (shares is not None and plays > 0) else None
    post["like_rate"] = round((likes / plays) * 100, 3) if (likes is not None and plays > 0) else None
    post["comment_rate"] = round((comments / plays) * 100, 3) if (comments is not None and plays > 0) else None
    post["length_bucket"] = classify_length_bucket(post.get("duration_sec"))
    post["caption_style"] = classify_caption_style(post.get("caption", ""))
    return post


def summarize_group(posts: list[dict[str, Any]], key: str) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for p in posts:
        buckets.setdefault(p[key], []).append(p)
    summary = {}
    for bucket, items in sorted(buckets.items()):
        summary[bucket] = {
            "count": len(items),
            "median_plays": safe_median([i["plays"] for i in items if i["plays"]]),
            "median_share_rate_pct": safe_median([i["share_rate"] for i in items if i["share_rate"] is not None]),
            "median_like_rate_pct": safe_median([i["like_rate"] for i in items if i["like_rate"] is not None]),
            "median_comment_rate_pct": safe_median([i["comment_rate"] for i in items if i["comment_rate"] is not None]),
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="GTA 6 lane research scanner (winners' sample).")
    parser.add_argument("--terms", nargs="*", default=DEFAULT_TERMS)
    parser.add_argument("--out", default="research/lane_scan.json")
    parser.add_argument("--skip-youtube", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("SCRAPE_CREATORS_API_KEY", "").strip()
    credit_cap = int(os.environ.get("SCRAPE_CREATORS_CREDIT_CAP", "40"))

    all_posts: list[dict[str, Any]] = []
    credits_used = 0

    if api_key:
        tt_posts, credits_used = pull_tiktok_via_scrape_creators(args.terms, api_key, credit_cap)
        all_posts.extend(tt_posts)
    else:
        print("[info] SCRAPE_CREATORS_API_KEY not set; skipping TikTok/IG API pull.", file=sys.stderr)

    if not args.skip_youtube:
        yt_posts = pull_youtube_shorts(args.terms)
        all_posts.extend(yt_posts)

    all_posts = [compute_rates(p) for p in all_posts]

    # Sort top 10 by share_rate when available, otherwise like_rate
    top_10 = sorted(
        all_posts,
        key=lambda x: (x["share_rate"] if x["share_rate"] is not None else -1, x["like_rate"] or -1, x["plays"]),
        reverse=True,
    )[:10]

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sample_label": "WINNERS_SAMPLE (biased toward top/most-liked posts — use for ratios, not baseline view targets)",
        "reuse_rule": "Reuse NOTHING from these posts. Research only.",
        "scrape_creators_credits_used": credits_used,
        "total_posts": len(all_posts),
        "by_length_bucket": summarize_group(all_posts, "length_bucket"),
        "by_caption_style": summarize_group(all_posts, "caption_style"),
        "top_10": top_10,
        "posts": all_posts,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} ({len(all_posts)} posts, {credits_used} Scrape Creators credits used)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
