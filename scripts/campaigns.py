#!/usr/bin/env python3
"""
Clipping Campaigns & Earnings Tracker (campaigns.py)

Manages all 11 clipping platforms (Whop, Clipur, Clipster, ClipStake, DareBay, Vues,
Clipping.io, Clipfy, Clippers HQ, Clipping.exe, Starlet), individual campaigns,
and per-clip submissions & payouts.

Usage examples:
  python3 scripts/campaigns.py status
  python3 scripts/campaigns.py join-platform whop --status active --handle "@leonidachao"
  python3 scripts/campaigns.py add-campaign --id WHOP-GTA-1 --platform whop --name "GTA 6 Launch Bounties" --creator "@Streamer" --cpm 1.50 --cap 150 --min-views 1000 --max-sec 30 --niche gta6
  python3 scripts/campaigns.py log-submission --campaign WHOP-GTA-1 --post-id GTA-021 --social youtube --url "https://youtube.com/shorts/..." --views 12500 --status approved
  python3 scripts/campaigns.py mark-paid SUB-001
  python3 scripts/campaigns.py sync-views
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
CAMPAIGNS_PATH = ROOT_DIR / "data" / "campaigns.json"
SCOREBOARD_PATH = ROOT_DIR / "scoreboard.csv"


def load_campaigns_data(path: Path = CAMPAIGNS_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"updated_at_utc": None, "currency": "USD", "platforms": [], "campaigns": [], "submissions": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_campaigns_data(data: dict[str, Any], path: Path = CAMPAIGNS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _to_int(val: Any) -> int:
    try:
        return int(float(str(val or 0).replace(",", "").strip()))
    except Exception:
        return 0


def compute_submission_earnings(
    sub: dict[str, Any],
    campaign: dict[str, Any] | None,
    platform: dict[str, Any] | None,
) -> dict[str, float]:
    """Calculate gross and net USD earnings for one submission respecting thresholds, caps, and platform fees."""
    status = str(sub.get("status") or "pending").lower()
    if status == "rejected":
        return {"gross_usd": 0.0, "net_usd": 0.0, "paid_usd": 0.0, "pending_usd": 0.0}

    if sub.get("manual_payout_usd") is not None:
        net = round(float(sub["manual_payout_usd"]), 2)
        return {
            "gross_usd": net,
            "net_usd": net,
            "paid_usd": net if status == "paid" else 0.0,
            "pending_usd": net if status != "paid" else 0.0,
        }

    views = max(0, int(sub.get("verified_views") or 0))
    cpm = float((campaign or {}).get("cpm_usd") or (platform or {}).get("typical_cpm_usd") or 0.0)
    min_views = int((campaign or {}).get("min_views_threshold") or 0)
    cap_usd = float((campaign or {}).get("per_clip_cap_usd") or 0.0)
    fee_pct = float((campaign or {}).get("fee_pct") if (campaign and campaign.get("fee_pct") is not None) else (platform or {}).get("fee_pct") or 0.0)

    if views < min_views or cpm <= 0:
        gross = 0.0
    else:
        gross = (views / 1000.0) * cpm
        if cap_usd > 0:
            gross = min(gross, cap_usd)

    net = round(gross * max(0.0, 1.0 - fee_pct / 100.0), 2)
    gross = round(gross, 2)
    return {
        "gross_usd": gross,
        "net_usd": net,
        "paid_usd": net if status == "paid" else 0.0,
        "pending_usd": net if status in ("pending", "approved") else 0.0,
    }


def compute_earnings_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Aggregate individual platform, individual campaign, and cumulative earnings."""
    platforms = data.get("platforms") or []
    campaigns = data.get("campaigns") or []
    submissions = data.get("submissions") or []

    plat_map = {p["id"]: p for p in platforms}
    camp_map = {c["id"]: c for c in campaigns}

    plat_acc: dict[str, dict[str, Any]] = {}
    for p in platforms:
        plat_acc[p["id"]] = {
            "id": p["id"],
            "name": p["name"],
            "url": p.get("url", ""),
            "status": p.get("status", "not_joined"),
            "handle": p.get("handle", ""),
            "typical_cpm_usd": float(p.get("typical_cpm_usd") or 0.0),
            "fee_pct": float(p.get("fee_pct") or 0.0),
            "min_payout_usd": float(p.get("min_payout_usd") or 0.0),
            "payout_rail": p.get("payout_rail", ""),
            "niche_fit": p.get("niche_fit", ""),
            "onboarding_steps": p.get("onboarding_steps") or [],
            "notes": p.get("notes", ""),
            "campaigns_count": 0,
            "submissions_count": 0,
            "verified_views": 0,
            "paid_usd": 0.0,
            "pending_usd": 0.0,
            "total_usd": 0.0,
        }

    camp_acc: dict[str, dict[str, Any]] = {}
    for c in campaigns:
        pid = c.get("platform_id", "")
        if pid in plat_acc:
            plat_acc[pid]["campaigns_count"] += 1
        camp_acc[c["id"]] = {
            **c,
            "platform_name": (plat_map.get(pid) or {}).get("name", pid),
            "submissions_count": 0,
            "verified_views": 0,
            "paid_usd": 0.0,
            "pending_usd": 0.0,
            "total_usd": 0.0,
        }

    enriched_subs: list[dict[str, Any]] = []
    total_paid = 0.0
    total_pending = 0.0
    total_views = 0

    for sub in submissions:
        cid = sub.get("campaign_id", "")
        camp = camp_map.get(cid)
        pid = sub.get("platform_id") or (camp or {}).get("platform_id", "")
        plat = plat_map.get(pid)
        calc = compute_submission_earnings(sub, camp, plat)
        views = max(0, int(sub.get("verified_views") or 0))
        if str(sub.get("status") or "").lower() != "rejected":
            total_views += views
        total_paid += calc["paid_usd"]
        total_pending += calc["pending_usd"]

        if pid in plat_acc:
            pa = plat_acc[pid]
            pa["submissions_count"] += 1
            if str(sub.get("status") or "").lower() != "rejected":
                pa["verified_views"] += views
            pa["paid_usd"] = round(pa["paid_usd"] + calc["paid_usd"], 2)
            pa["pending_usd"] = round(pa["pending_usd"] + calc["pending_usd"], 2)
            pa["total_usd"] = round(pa["paid_usd"] + pa["pending_usd"], 2)

        if cid in camp_acc:
            ca = camp_acc[cid]
            ca["submissions_count"] += 1
            if str(sub.get("status") or "").lower() != "rejected":
                ca["verified_views"] += views
            ca["paid_usd"] = round(ca["paid_usd"] + calc["paid_usd"], 2)
            ca["pending_usd"] = round(ca["pending_usd"] + calc["pending_usd"], 2)
            ca["total_usd"] = round(ca["paid_usd"] + ca["pending_usd"], 2)

        enriched_subs.append({**sub, **calc, "platform_id": pid})

    total_accumulated = round(total_paid + total_pending, 2)
    joined_count = sum(1 for p in plat_acc.values() if p["status"] in ("applied", "active"))
    active_camp_count = sum(1 for c in camp_acc.values() if str(c.get("status", "active")).lower() == "active")
    avg_cpm = round((total_accumulated / (total_views / 1000.0)), 2) if total_views > 0 else 0.0

    return {
        "currency": data.get("currency", "USD"),
        "total_accumulated_usd": total_accumulated,
        "total_paid_usd": round(total_paid, 2),
        "total_pending_usd": round(total_pending, 2),
        "total_verified_views": total_views,
        "avg_effective_cpm": avg_cpm,
        "joined_platforms_count": joined_count,
        "total_platforms_count": len(plat_acc),
        "active_campaigns_count": active_camp_count,
        "by_platform": list(plat_acc.values()),
        "by_campaign": list(camp_acc.values()),
        "submissions": enriched_subs,
    }


def sync_views_from_scoreboard(data: dict[str, Any], scoreboard_path: Path = SCOREBOARD_PATH) -> int:
    """Update submission verified_views from scoreboard.csv by post_id + social platform or matching URL."""
    if not scoreboard_path.exists():
        return 0
    rows_by_key: dict[tuple[str, str], int] = {}
    rows_by_url: dict[str, int] = {}
    with scoreboard_path.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            pid = (r.get("post_id") or "").strip().upper()
            plat = (r.get("platform") or "").strip().lower()
            views = _to_int(r.get("views_72h")) or _to_int(r.get("views_24h")) or _to_int(r.get("views_12h"))
            if pid and plat:
                rows_by_key[(pid, plat)] = max(rows_by_key.get((pid, plat), 0), views)
            notes = r.get("notes") or ""
            for tok in notes.split("|"):
                tok = tok.strip()
                if tok.startswith("http"):
                    rows_by_url[tok] = max(rows_by_url.get(tok, 0), views)

    updated = 0
    for sub in data.get("submissions") or []:
        url = (sub.get("post_url") or "").strip()
        pid = (sub.get("post_id") or "").strip().upper()
        social = (sub.get("social_platform") or "").strip().lower()
        new_views = None
        if url and url in rows_by_url:
            new_views = rows_by_url[url]
        elif (pid, social) in rows_by_key:
            new_views = rows_by_key[(pid, social)]
        if new_views is not None and new_views > int(sub.get("verified_views") or 0):
            sub["verified_views"] = new_views
            sub["last_synced_utc"] = datetime.now(timezone.utc).isoformat()
            updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage clipping platforms, campaigns, submissions & earnings.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show individual platform, campaign, and cumulative earnings")

    p_join = sub.add_parser("join-platform", help="Update join status for one of the 11 clipping platforms")
    p_join.add_argument("platform_id", help="Platform ID (e.g. whop, clipur, clipster, vues, starlet)")
    p_join.add_argument("--status", choices=("not_joined", "applied", "active", "paused"), required=True)
    p_join.add_argument("--handle", default=None)
    p_join.add_argument("--notes", default=None)

    p_add = sub.add_parser("add-campaign", help="Register a clipping campaign you joined")
    p_add.add_argument("--id", required=True, help="Unique campaign ID (e.g. WHOP-GTA-1)")
    p_add.add_argument("--platform", required=True, help="Platform ID (e.g. whop)")
    p_add.add_argument("--name", required=True, help="Campaign name")
    p_add.add_argument("--creator", default="", help="Creator or brand name")
    p_add.add_argument("--cpm", type=float, required=True, help="Payout rate per 1,000 views (USD)")
    p_add.add_argument("--cap", type=float, default=150.0, help="Per-clip payout cap (USD)")
    p_add.add_argument("--min-views", type=int, default=1000, help="Minimum views to unlock payout")
    p_add.add_argument("--max-sec", type=int, default=30, help="Max clip duration allowed by brief")
    p_add.add_argument("--niche", choices=("gta6", "gaming"), default="gta6")
    p_add.add_argument("--required-tags", nargs="*", default=[])
    p_add.add_argument("--required-text", default="")
    p_add.add_argument("--banned-terms", nargs="*", default=[])
    p_add.add_argument("--allowed-platforms", nargs="*", default=["youtube", "tiktok", "instagram"])

    p_log = sub.add_parser("log-submission", help="Log a clip submitted to a campaign")
    p_log.add_argument("--campaign", required=True, help="Campaign ID")
    p_log.add_argument("--post-id", required=True, help="Clip Post ID (e.g. GTA-021)")
    p_log.add_argument("--social", choices=("youtube", "tiktok", "instagram"), required=True)
    p_log.add_argument("--url", default="", help="Live post URL submitted to campaign")
    p_log.add_argument("--views", type=int, default=0, help="Current verified views")
    p_log.add_argument("--status", choices=("pending", "approved", "paid", "rejected"), default="pending")

    p_paid = sub.add_parser("mark-paid", help="Mark a submission as paid")
    p_paid.add_argument("submission_id")
    p_paid.add_argument("--amount", type=float, default=None, help="Optional exact net payout USD")

    sub.add_parser("sync-views", help="Sync submission views from scoreboard.csv")

    args = parser.parse_args()
    data = load_campaigns_data()

    if args.cmd == "status":
        print(json.dumps(compute_earnings_summary(data), indent=2))
        return 0

    if args.cmd == "join-platform":
        found = next((p for p in data.get("platforms") or [] if p["id"] == args.platform_id.lower()), None)
        if not found:
            print(f"Unknown platform_id: {args.platform_id}", file=sys.stderr)
            return 2
        found["status"] = args.status
        if args.handle is not None:
            found["handle"] = args.handle
        if args.notes is not None:
            found["notes"] = args.notes
        save_campaigns_data(data)
        print(json.dumps(found, indent=2))
        return 0

    if args.cmd == "add-campaign":
        camp = {
            "id": args.id,
            "platform_id": args.platform.lower(),
            "name": args.name,
            "creator": args.creator,
            "status": "active",
            "niche": args.niche,
            "cpm_usd": args.cpm,
            "per_clip_cap_usd": args.cap,
            "min_views_threshold": args.min_views,
            "max_length_sec": args.max_sec,
            "required_tags": args.required_tags,
            "required_caption_text": args.required_text,
            "banned_terms": args.banned_terms,
            "allowed_platforms": args.allowed_platforms,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        camps = data.setdefault("campaigns", [])
        camps[:] = [c for c in camps if c.get("id") != args.id]
        camps.append(camp)
        save_campaigns_data(data)
        print(json.dumps(camp, indent=2))
        return 0

    if args.cmd == "log-submission":
        camp = next((c for c in data.get("campaigns") or [] if c.get("id") == args.campaign), None)
        if not camp:
            print(f"Campaign {args.campaign!r} not found. Add it first with add-campaign.", file=sys.stderr)
            return 2
        subs = data.setdefault("submissions", [])
        sub_id = f"SUB-{len(subs) + 1:03d}"
        rec = {
            "id": sub_id,
            "campaign_id": args.campaign,
            "platform_id": camp["platform_id"],
            "post_id": args.post_id,
            "social_platform": args.social,
            "post_url": args.url,
            "verified_views": args.views,
            "status": args.status,
            "submitted_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        subs.append(rec)
        save_campaigns_data(data)
        print(json.dumps(rec, indent=2))
        return 0

    if args.cmd == "mark-paid":
        sub_rec = next((s for s in data.get("submissions") or [] if s.get("id") == args.submission_id), None)
        if not sub_rec:
            print(f"Submission {args.submission_id!r} not found.", file=sys.stderr)
            return 2
        sub_rec["status"] = "paid"
        if args.amount is not None:
            sub_rec["manual_payout_usd"] = round(args.amount, 2)
        sub_rec["paid_at_utc"] = datetime.now(timezone.utc).isoformat()
        save_campaigns_data(data)
        print(json.dumps(sub_rec, indent=2))
        return 0

    if args.cmd == "sync-views":
        n = sync_views_from_scoreboard(data)
        if n > 0:
            save_campaigns_data(data)
        print(json.dumps({"submissions_updated": n}, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
