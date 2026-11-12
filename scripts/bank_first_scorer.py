#!/usr/bin/env python3
"""scripts/bank_first_scorer.py -- guarded production runner for first-scorer price banking
(NBA first basket + first team basket; NFL first TD scorer) via nba.odds.first_scorer.

DISABLED BY DEFAULT (Luc, 2026-11-11: build it, do not provision or spend credits yet):
  Without BOTH --execute AND the environment variable FIRST_SCORER_BANK_ENABLED=1 this script
  constructs NO ParlayAPI client, makes ZERO API calls and ZERO storage writes: it prints the
  plan and exits 0. Provisioning (deploy/setup_first_scorer_banking.sh) is a separate step that
  follows merge, on Luc's explicit go-ahead.

Usage (plan only -- safe anywhere):
    python3 scripts/bank_first_scorer.py --sport basketball_nba
    python3 scripts/bank_first_scorer.py --sport americanfootball_nfl --snapshot T-30

Trigger (later, deliberately -- BOTH gates):
    FIRST_SCORER_BANK_ENABLED=1 python3 scripts/bank_first_scorer.py --execute --sport basketball_nba
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # runnable as `python scripts/...` from a checkout

from nba.config import FIRST_SCORER_MARKETS, FIRST_SCORER_MIN_OUTCOMES, FIRST_SCORER_PREFIX

log = logging.getLogger("bank_first_scorer")
ENABLE_ENV = "FIRST_SCORER_BANK_ENABLED"
SPORTS = tuple(FIRST_SCORER_MARKETS)
SNAPSHOTS = ("T-120", "T-30", "T-5")


def build_plan(sport: str, snapshot: str | None = None) -> dict:
    markets = list(FIRST_SCORER_MARKETS[sport])
    return {
        "sport": sport, "markets": markets, "snapshot": snapshot or "auto (per-event window)",
        "snapshots": list(SNAPSHOTS), "completeness_gate": f">= {FIRST_SCORER_MIN_OUTCOMES} listed outcomes per book",
        "target_prefix": f"{FIRST_SCORER_PREFIX}/{sport}/",
        "worst_case_credits": "1 per (event x market) per snapshot; NBA ~66/night, NFL ~48/week",
    }


def format_plan(plan: dict) -> str:
    return "\n".join([
        f"sport: {plan['sport']}",
        f"markets: {', '.join(plan['markets'])}",
        f"snapshot: {plan['snapshot']} (windows T-120 / T-30 / T-5)",
        f"completeness gate: {plan['completeness_gate']}",
        f"target prefix: {plan['target_prefix']}",
        f"worst-case credits: {plan['worst_case_credits']}",
    ])


def execution_allowed(execute_flag: bool, environ) -> tuple:
    """(allowed, reason). BOTH gates are required; the reason names the missing gate."""
    if not execute_flag:
        return False, "no --execute flag"
    if environ.get(ENABLE_ENV) != "1":
        return False, f"{ENABLE_ENV} is not set to 1"
    return True, f"both gates present (--execute and {ENABLE_ENV}=1)"


def main(argv=None, environ=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport", choices=SPORTS, default="basketball_nba")
    ap.add_argument("--snapshot", choices=SNAPSHOTS, default=None,
                    help="restrict to one snapshot window; default auto-selects per event")
    ap.add_argument("--max-credits", type=int, default=0, help="per-run credit cap (ledger guard)")
    ap.add_argument("--execute", action="store_true",
                    help=f"actually call ParlayAPI + bank. Also requires env {ENABLE_ENV}=1; without both, plan only")
    a = ap.parse_args(argv)
    environ = os.environ if environ is None else environ
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    plan = build_plan(a.sport, a.snapshot)
    print(format_plan(plan))
    allowed, reason = execution_allowed(a.execute, environ)
    if not allowed:
        print(f"mode: PLAN ONLY ({reason}); zero API calls, zero writes. "
              f"To execute: {ENABLE_ENV}=1 python3 scripts/bank_first_scorer.py --execute --sport {a.sport}")
        return 0
    print("mode: EXECUTE (both gates present)")
    from nba.odds.first_scorer import accumulate_micro
    result = accumulate_micro(a.sport, snapshot=a.snapshot, max_credits=a.max_credits)
    print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
