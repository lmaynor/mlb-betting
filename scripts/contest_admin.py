"""contest_admin -- set up and run a Bankroll Challenge from a terminal. Uses MLB_DB_URL (or --db-url; a sqlite:/// URL works for a dry run).

  python3 scripts/contest_admin.py create  --slug bowls-2026 --name "Bowl Bankroll" --sport ncaaf
  python3 scripts/contest_admin.py invite  --slug bowls-2026 --code WELCOME --max-uses 25
  python3 scripts/contest_admin.py refresh --slug bowls-2026 --include "Bowl|Playoff|Championship"    # fetch ESPN, update the board, grade finals
  python3 scripts/contest_admin.py status  --slug bowls-2026                                           # entries, bets, ledger check
  python3 scripts/contest_admin.py rename  --slug bowls-2026 --entry 3 --name "New Name"               # organiser override
  python3 scripts/contest_admin.py adjust  --slug bowls-2026 --entry 3 --cents -5000 --note "duplicate bet refunded"

Nothing here places a bet or moves real money. `refresh` is the same pass the scheduler runs through POST /contest-refresh.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import sqlalchemy as sa  # noqa: E402

from contest import refresh as refresh_mod  # noqa: E402
from contest import store  # noqa: E402
from contest.rules import ContestRules  # noqa: E402
from contest.schema import ensure_schema  # noqa: E402


def engine_for(url: str | None):
    url = url or os.environ.get("MLB_DB_URL")
    if not url:
        raise SystemExit("set MLB_DB_URL or pass --db-url")
    e = sa.create_engine(url)
    ensure_schema(e)
    return e


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db-url")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("create", "invite", "refresh", "status", "rename", "adjust"):
        p = sub.add_parser(name)
        p.add_argument("--slug", required=True)
        if name == "create":
            p.add_argument("--name", required=True)
            p.add_argument("--sport", default="ncaaf", choices=["ncaaf", "ncaab"])
        if name == "invite":
            p.add_argument("--code", required=True)
            p.add_argument("--max-uses", type=int, default=25)
        if name == "refresh":
            p.add_argument("--include")
            p.add_argument("--days-back", type=int, default=2)
            p.add_argument("--days-ahead", type=int, default=14)
        if name in ("rename", "adjust"):
            p.add_argument("--entry", type=int, required=True)
        if name == "rename":
            p.add_argument("--name", required=True)
        if name == "adjust":
            p.add_argument("--cents", type=int, required=True)
            p.add_argument("--note", required=True)
    a = ap.parse_args(argv)
    e = engine_for(a.db_url)
    now = datetime.now(timezone.utc)
    if a.cmd == "create":
        cid = store.create_contest(e, a.slug, a.name, ContestRules(sport=a.sport), now)
        print(f"created contest {a.slug!r} (id {cid}, sport {a.sport}). Invites: contest_admin.py invite --slug {a.slug} --code ...")
        return 0
    c = store.contest_by_slug(e, a.slug)
    if c is None:
        raise SystemExit(f"no contest {a.slug!r}")
    if a.cmd == "invite":
        store.create_invite(e, c["id"], a.code, a.max_uses, now)
        print(f"invite {a.code!r} created ({a.max_uses} uses)")
    elif a.cmd == "refresh":
        print(json.dumps(refresh_mod.refresh(e, a.slug, now, a.include, a.days_back, a.days_ahead), indent=2, default=str))
    elif a.cmd == "status":
        lb = store.leaderboard(e, c["id"])
        bad = store.reconcile(e, c["id"])
        print(f"{c['name']} [{c['status']}]: {len(lb)} entries, {len(store.games_of(e, c['id']))} games, {len(store.load_board(e, c['id']))} stored quotes")
        for r in lb[:20]:
            print(f"  {r['rank']:>2}. {r['display_name']:<22} bankroll {r['bankroll_cents'] / 100:>10,.2f}  open {r['open_risk_cents'] / 100:>9,.2f}  {r['status']}")
        print("ledger check:", "balanced" if not bad else f"MISMATCH {bad}")
    elif a.cmd == "rename":
        print("renamed to", store.admin_rename(e, a.entry, a.name))
    elif a.cmd == "adjust":
        print("new balance (cents):", store.adjust(e, a.entry, a.cents, a.note, now))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
