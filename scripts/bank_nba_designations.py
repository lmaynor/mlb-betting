#!/usr/bin/env python3
"""scripts/bank_nba_designations.py -- guarded production runner for banking NBA injury designations
(the official NBA injury report) via nba.data.nba_injury_report.backfill.

WHAT EXISTS vs WHAT THIS ADDS: the parser/fetcher/banker already live in nba/data/nba_injury_report.py
(report_urls, fetch_report, parse_words/parse_pdf, bank_key/bank_report, resumable backfill with
_missing.json tracking, and its own module CLI). This script is only the production entry point around
backfill(): a configurable day set + stamp hours, a --source switch, and a hard execution gate. It adds
no parsing or banking logic of its own.

SOURCE STORY:
  * --source official-pdf (default): the league's official report PDFs (ak-static.cms.nba.com), parsed by
    pdftotext word geometry and banked -- parsed CSV + raw PDF -- under NBA/injuries/official/. This is
    the historical + decision-time source: EVERY published version is banked with its report_ts_et, never
    collapsed to latest, because decision-time honesty depends on the version that existed at decision
    time. A team absent from a report means "nothing reported", never "healthy"; NOT YET SUBMITTED rows
    are banked as team-level flags.
  * --source sportsdataverse: SportsDataverse republishes the same reports as per-version rows
    (report_datetime + current_status + ...). The adapter interface lives in
    nba/data/designation_sources.py and is deliberately NOT implemented (its full column schema is not
    pinned), so an execute run for this source fails closed with that explanation. The official PDF stays
    primary regardless: it is the durable source of truth and the raw PDFs are banked beside the parses.
  * The going-forward LIVE source is separate and already wired: nba/data/injury_bank.py banks ESPN's
    live injury list (change-only snapshots under NBA/injuries/espn/, state_asof() to read them back).
    nba.data.designation_sources.espn_snapshot_to_designations aligns an ESPN snapshot to the same
    COLUMNS frame for live-vs-historical comparison; it does not replace either banker.

DISABLED BY DEFAULT (Luc, 2026-11-11: "do the build but don't trigger the run yet"):
  Without BOTH --execute AND the environment variable MINUTES_RUN_ENABLED=1 this script performs ZERO
  network fetches and ZERO storage writes: it prints the plan and exits 0. Missing either gate is not an
  error -- plan-only is the default state of this runner until the run is explicitly triggered.

POPPLER / DEPLOY NOTE:
  The official parser shells out to `pdftotext -bbox` (poppler-utils) and fails closed with
  nba_injury_report.PdfToolMissing when the binary is absent -- never a guessed parse. The repo
  Dockerfile installs poppler-utils (added with this runner), so the backfill can run in that image;
  it also runs on any host where /usr/bin/pdftotext exists (the dev VM has it). Do NOT run the backfill
  from an image/host without poppler and expect partial results: every report will fail closed.

Usage (plan only -- safe anywhere, no fetches, no writes):
    python3 scripts/bank_nba_designations.py --days 2026-04-01,2026-04-02
    python3 scripts/bank_nba_designations.py --start 2026-04-01 --end 2026-04-07 --all-hours
    python3 scripts/bank_nba_designations.py --box-dir <hoopr mirror dir> --seasons 2025 2026

Trigger (later, deliberately -- BOTH gates):
    MINUTES_RUN_ENABLED=1 python3 scripts/bank_nba_designations.py --execute --box-dir <hoopr mirror dir> --seasons 2025 2026
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # runnable as `python scripts/...` from a checkout

from nba.data import nba_injury_report as R

log = logging.getLogger("bank_nba_designations")

TARGET_PREFIX = R.PREFIX                       # NBA/injuries/official
DEFAULT_HOURS = R.DEFAULT_STAMP_HOURS          # (13, 17) ET -- the ~1:30 PM / ~5:30 PM reports
ALL_HOURS = tuple(range(24))
SOURCES = ("official-pdf", "sportsdataverse")
ENABLE_ENV = "MINUTES_RUN_ENABLED"


# ----------------------------------------------------------------------------------------------- planning
def _daterange(start: str, end: str) -> list:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    if d1 < d0:
        raise ValueError(f"--end {end} is before --start {start}")
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def resolve_days(args) -> list:
    """The day set, from exactly one source: --days, else --start/--end, else hoopR game days
    (the module CLI's default: game days derived from the mirrored ESPN player box)."""
    if args.days:
        return sorted({d.strip() for d in args.days.split(",") if d.strip()})
    if args.start or args.end:
        if not (args.start and args.end):
            raise ValueError("--start and --end must be given together")
        return _daterange(args.start, args.end)
    if args.box_dir and args.seasons:
        return R.game_days_from_box(args.box_dir, args.seasons)
    raise ValueError("one day source is required: --days, --start/--end, or --box-dir + --seasons")


def resolve_hours(args) -> tuple:
    if args.all_hours:
        return ALL_HOURS
    if args.hours:
        return tuple(sorted(set(args.hours)))
    return tuple(DEFAULT_HOURS)


def _hoopr_season(day_iso: str) -> int:
    """hoopR season (end year) of an ET date: Aug-Dec -> next year, Jan-Jul -> this year."""
    d = date.fromisoformat(day_iso)
    return d.year + 1 if d.month >= 8 else d.year


def build_plan(days, hours, source: str = "official-pdf") -> dict:
    """Pure plan arithmetic (no fetches, no storage): what an execute run WOULD ask for."""
    n = len(days) * len(hours)
    if source == "sportsdataverse":
        seasons = sorted({_hoopr_season(d) for d in days})
        est = f"{len(seasons)} season file(s) {seasons} (one per hoopR season spanned)"
        reports = n
    else:
        est = f"{n} report fetches (each tries up to 2 URL variants: hourly + quarter-hour names)"
        reports = n
    return {"source": source, "target_prefix": TARGET_PREFIX, "days": list(days), "n_days": len(days),
            "hours": list(hours), "n_hours": len(hours), "n_reports": reports, "est_requests": est}


def format_plan(plan: dict) -> str:
    days = plan["days"]
    span = f"{days[0]} .. {days[-1]}" if days else "(none)"
    return "\n".join([
        f"source: {plan['source']}",
        f"target prefix: {plan['target_prefix']}",
        f"{plan['n_days']} days x {plan['n_hours']} stamps = {plan['n_reports']} reports",
        f"days: {span} ({plan['n_days']})",
        f"stamp hours (ET): {', '.join(str(h) for h in plan['hours'])}",
        f"est. requests: {plan['est_requests']}",
    ])


def execution_allowed(execute_flag: bool, environ) -> tuple:
    """(allowed, reason). BOTH gates are required; the reason names the missing gate."""
    if not execute_flag:
        return False, "no --execute flag"
    if environ.get(ENABLE_ENV) != "1":
        return False, f"{ENABLE_ENV} is not set to 1"
    return True, "both gates present (--execute and MINUTES_RUN_ENABLED=1)"


# ----------------------------------------------------------------------------------------------- main
def main(argv=None, environ=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", help="comma list of ET game days, e.g. 2026-04-01,2026-04-02")
    ap.add_argument("--start", help="first ET day (YYYY-MM-DD) of an inclusive range; needs --end")
    ap.add_argument("--end", help="last ET day (YYYY-MM-DD) of an inclusive range; needs --start")
    ap.add_argument("--box-dir", help="hoopR mirror dir with player_boxscores/ (game days come from it)")
    ap.add_argument("--seasons", nargs="+", type=int, help="hoopR seasons (year the season ends), with --box-dir")
    ap.add_argument("--hours", nargs="+", type=int, help="ET stamp hours (24h); default 13 17")
    ap.add_argument("--all-hours", action="store_true", help="every hourly stamp 0-23 ET (overrides --hours)")
    ap.add_argument("--source", choices=SOURCES, default="official-pdf",
                    help="official-pdf (default): league PDFs via nba_injury_report; sportsdataverse: "
                         "adapter interface only, not implemented (fails closed on execute)")
    ap.add_argument("--delay", type=float, default=0.25, help="seconds between fetches when executing")
    ap.add_argument("--execute", action="store_true",
                    help="actually fetch + bank. Also requires env MINUTES_RUN_ENABLED=1; without both, plan only")
    a = ap.parse_args(argv)
    environ = os.environ if environ is None else environ
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        days = resolve_days(a)
    except ValueError as exc:
        ap.error(str(exc))
    hours = resolve_hours(a)
    plan = build_plan(days, hours, a.source)
    print(format_plan(plan))

    allowed, reason = execution_allowed(a.execute, environ)
    if not allowed:
        print(f"mode: PLAN ONLY ({reason}); zero fetches, zero writes. "
              f"To execute: {ENABLE_ENV}=1 python3 scripts/bank_nba_designations.py --execute ...")
        return 0

    print("mode: EXECUTE (both gates present)")
    if a.source == "sportsdataverse":
        from nba.data import designation_sources
        try:
            designation_sources.fetch_sportsdataverse(sorted({_hoopr_season(d) for d in days}))
        except NotImplementedError as exc:
            print(f"FAILED: {exc}")
            return 1
        return 0                                                        # unreachable until implemented
    res = R.backfill(days, hours, a.delay,
                     progress=lambda o: log.info("progress %s", {k: (len(v) if isinstance(v, list) else v)
                                                                 for k, v in o.items()}))
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()}))
    for f in res["failed"][:20]:
        print("FAILED", f)
    return 0 if not res["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
