#!/usr/bin/env python3
"""
One-command pause/unpause & profile-quarantine manager (unpause.py).

Examples:
  python3 scripts/unpause.py --status
  python3 scripts/unpause.py --pause-all --reason "Both platforms stuck"
  python3 scripts/unpause.py --pause instagram --hours 24 --reason "2 stuck posts"
  python3 scripts/unpause.py --record-profile-edit
  python3 scripts/unpause.py                       # Undo ALL pauses in one command
  python3 scripts/unpause.py --platform instagram  # Undo pause for one platform
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PAUSE_PATH = ROOT_DIR / "data" / "pause_state.json"
PLATFORMS = ("instagram", "tiktok", "youtube")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"paused_all": False, "reason": None, "platforms": {}, "last_profile_edit_utc": None, "history": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Undo emergency pause in one command (or manage pause/quarantine state).")
    parser.add_argument("--status", action="store_true", help="Print current pause and quarantine state")
    parser.add_argument("--platform", choices=list(PLATFORMS), help="Unpause a single platform")
    parser.add_argument("--pause", choices=list(PLATFORMS), help="Pause a single platform")
    parser.add_argument("--pause-all", action="store_true", help="Trigger emergency global pause")
    parser.add_argument("--hours", type=float, default=24.0, help="Duration for single-platform pause (default: 24h)")
    parser.add_argument("--reason", default="manual action", help="Reason logged in pause history")
    parser.add_argument("--record-profile-edit", action="store_true", help="Start 2-hour profile edit quarantine")
    parser.add_argument("--clear-quarantine", action="store_true", help="Clear profile edit quarantine timestamp")
    parser.add_argument("--pause-file", type=Path, default=PAUSE_PATH)
    args = parser.parse_args()

    state = load_state(args.pause_file)
    ts = now_utc()

    if args.status:
        print(json.dumps(state, indent=2))
        return 0

    if args.record_profile_edit:
        state["last_profile_edit_utc"] = ts.isoformat()
        state.setdefault("history", []).append({"at_utc": ts.isoformat(), "action": "profile_edit_quarantine_2h", "reason": args.reason})
    elif args.clear_quarantine:
        state["last_profile_edit_utc"] = None
        state.setdefault("history", []).append({"at_utc": ts.isoformat(), "action": "clear_quarantine", "reason": args.reason})
    elif args.pause_all:
        state["paused_all"] = True
        state["reason"] = args.reason
        state.setdefault("history", []).append({"at_utc": ts.isoformat(), "action": "pause_all", "reason": args.reason})
    elif args.pause:
        until = (ts + timedelta(hours=args.hours)).isoformat()
        state.setdefault("platforms", {})[args.pause] = {"paused": True, "until_utc": until, "reason": args.reason}
        state.setdefault("history", []).append({"at_utc": ts.isoformat(), "action": f"pause_{args.pause}", "until_utc": until, "reason": args.reason})
    elif args.platform:
        state.setdefault("platforms", {})[args.platform] = {"paused": False, "until_utc": None, "reason": None}
        state.setdefault("history", []).append({"at_utc": ts.isoformat(), "action": f"unpause_{args.platform}", "reason": args.reason})
    else:
        # Default: undo all pauses in one command
        state["paused_all"] = False
        state["reason"] = None
        for p in PLATFORMS:
            state.setdefault("platforms", {})[p] = {"paused": False, "until_utc": None, "reason": None}
        state.setdefault("history", []).append({"at_utc": ts.isoformat(), "action": "unpause_all", "reason": args.reason})

    save_state(args.pause_file, state)
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
