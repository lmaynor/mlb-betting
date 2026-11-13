#!/usr/bin/env python3
"""scripts/derive_first_scorer_outcomes.py -- guarded production runner for first-scorer outcomes.

Derives, from already-mirrored play-by-play:
  * NBA first basket, BOTH settlement variants (first point incl. free throws, and first field
    goal) plus per-team first scorers -- nba.data.first_basket_outcomes, from NBA/hoopr/pbp
    (mirrored once the pbp flip PR lands; seasons are hoopR end-years, back to 2019 by default).
  * NFL first touchdown scorer -- nfl.data.first_td_outcomes, from NFL/nflverse/pbp.

Outputs (per season): FirstScorer/outcomes/basketball_nba/first_basket_{season}.parquet and
FirstScorer/outcomes/americanfootball_nfl/first_td_{season}.parquet, plus a last_run.json
sentinel per sport. No API calls, no credits: this job only reads mirrored PBP and writes
derived parquet.

DISABLED BY DEFAULT (Luc, 2026-11-11: build it, do not provision yet):
  Without BOTH --execute AND the environment variable FIRST_SCORER_OUTCOMES_ENABLED=1 this
  script reads NOTHING and writes NOTHING: it prints the plan and exits 0. Provisioning
  (deploy/setup_first_scorer_outcomes.sh) follows merge, on Luc's explicit go-ahead.

Usage (plan only -- safe anywhere):
    python3 scripts/derive_first_scorer_outcomes.py --sport basketball_nba --seasons 2025 2026
    python3 scripts/derive_first_scorer_outcomes.py --sport americanfootball_nfl --seasons 2024 2025

Trigger (later, deliberately -- BOTH gates):
    FIRST_SCORER_OUTCOMES_ENABLED=1 python3 scripts/derive_first_scorer_outcomes.py --execute \
        --sport basketball_nba --seasons 2019-2027
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # runnable as `python scripts/...` from a checkout

log = logging.getLogger("derive_first_scorer_outcomes")
ENABLE_ENV = "FIRST_SCORER_OUTCOMES_ENABLED"
SPORTS = ("basketball_nba", "americanfootball_nfl")
NBA_DEFAULT_SEASONS = list(range(2019, 2028))        # hoopR end-years: 2019-20 .. 2026-27
NFL_DEFAULT_SEASONS = list(range(2018, 2027))


def _parse_seasons(text):
    out = []
    for part in text:
        lo, _, hi = str(part).partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return sorted(set(out))


def build_plan(sport: str, seasons) -> dict:
    if sport == "basketball_nba":
        src, prefix = "NBA/hoopr/pbp (espn_nba_pbp mirror)", "FirstScorer/outcomes/basketball_nba/"
    else:
        src, prefix = "NFL/nflverse/pbp (nflverse mirror)", "FirstScorer/outcomes/americanfootball_nfl/"
    return {"sport": sport, "source": src, "seasons": list(seasons), "target_prefix": prefix,
            "variants": "NBA: first point (FT-inclusive) + first field goal + per-team; NFL: first TD scorer"}


def format_plan(plan: dict) -> str:
    return "\n".join([
        f"sport: {plan['sport']}",
        f"source: {plan['source']}",
        f"seasons: {plan['seasons'][0]}-{plan['seasons'][-1]} ({len(plan['seasons'])})",
        f"target prefix: {plan['target_prefix']}",
        f"variants: {plan['variants']}",
    ])


def execution_allowed(execute_flag: bool, environ) -> tuple:
    """(allowed, reason). BOTH gates are required; the reason names the missing gate."""
    if not execute_flag:
        return False, "no --execute flag"
    if environ.get(ENABLE_ENV) != "1":
        return False, f"{ENABLE_ENV} is not set to 1"
    return True, f"both gates present (--execute and {ENABLE_ENV}=1)"


def run_nba(seasons, local_dir=None) -> dict:
    from mlb_core import storage
    from nba.data import first_basket_outcomes as O
    total = 0
    for s in seasons:
        df = O.build_outcomes([s], local_dir=local_dir)
        if df.empty:
            log.warning("NBA season %s: no outcomes (pbp not mirrored yet?)", s)
            continue
        storage.write_parquet(df, O.outcome_key(s))
        total += len(df)
        log.info("NBA season %s: %d games -> %s", s, len(df), O.outcome_key(s))
    return {"sport": "basketball_nba", "games": total, "seasons": list(seasons)}


def run_nfl(seasons) -> dict:
    from mlb_core import storage
    from nfl.data import first_td_outcomes as O
    from nfl.data.read import load
    total = 0
    for s in seasons:
        pbp = load("pbp", seasons=[s], strict=False)
        df = O.derive_first_td(pbp)
        if df.empty:
            log.warning("NFL season %s: no outcomes (pbp not mirrored yet?)", s)
            continue
        storage.write_parquet(df, O.outcome_key(s))
        total += len(df)
        log.info("NFL season %s: %d games -> %s", s, len(df), O.outcome_key(s))
    return {"sport": "americanfootball_nfl", "games": total, "seasons": list(seasons)}


def main(argv=None, environ=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport", choices=SPORTS, required=True)
    ap.add_argument("--seasons", nargs="+", help="e.g. 2025 2026 or 2019-2027; default: full spike window")
    ap.add_argument("--local-dir", help="NBA only: local hoopR mirror dir (offline runs/tests)")
    ap.add_argument("--execute", action="store_true",
                    help=f"actually derive + write. Also requires env {ENABLE_ENV}=1; without both, plan only")
    a = ap.parse_args(argv)
    environ = os.environ if environ is None else environ
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    seasons = _parse_seasons(a.seasons) if a.seasons else (
        NBA_DEFAULT_SEASONS if a.sport == "basketball_nba" else NFL_DEFAULT_SEASONS)
    plan = build_plan(a.sport, seasons)
    print(format_plan(plan))
    allowed, reason = execution_allowed(a.execute, environ)
    if not allowed:
        print(f"mode: PLAN ONLY ({reason}); zero reads, zero writes. "
              f"To execute: {ENABLE_ENV}=1 python3 scripts/derive_first_scorer_outcomes.py --execute --sport {a.sport}")
        return 0
    print("mode: EXECUTE (both gates present)")
    result = run_nba(seasons, a.local_dir) if a.sport == "basketball_nba" else run_nfl(seasons)
    print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
