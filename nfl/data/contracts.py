"""Data contracts for the mirrored nflverse datasets: grain, sign, coverage, freshness, cross-table reconciliation.

    python -m nfl.data.contracts [--season 2026] [--today 2026-10-16] [--no-write]

Why this exists. The ingest engine (nfl.data.ingest) proves each FILE is a valid parquet with the expected columns and
did not shrink. It cannot tell that a file is a week behind, that a game is missing from one table but present in
another, or that nflverse silently changed what a column means. Every feature, grader and projection in this stack
joins those tables, so a quiet hole becomes a wrong number downstream. These checks are the table-level guarantees the
consumers rely on (the pattern is ported from fantasy-football-ai's silver-layer contracts; the thresholds below are
the ones measured on the real mirror, 2023-2026, not guesses):

  * grain            (player_id, game_id) unique in player_stats, (team, game_id) in team_stats, game_id in schedules
  * sign             counting stats are never negative
  * coverage         every FINAL game of the season is present in player_stats, team_stats and pbp (error when the
                     game is 2+ days old, warning at 1 day -- nflverse publishes within hours of the final whistle)
  * reconcile        per (game, team): sum of player_stats equals team_stats EXACTLY for passing / rushing / receiving
                     yards, carries and attempts (measured: 0 mismatches over 1,868 team-games)
  * scoring          fantasy_points_ppr equals the published PPR formula for offensive players (measured 99.97% over
                     2023-2026) -- a semantic-drift tripwire, not a scoring engine
  * schedule results every game whose kickoff date is behind us has a result (else the mirror stopped moving)
  * schedule lines   games kicking off within 7 days carry a spread and total (warning: the features need them)

Output: a report dict {"ok", "status", "checks": [...]} (also written to NFL/contracts_latest.json by the refresh
pipeline). `ok` is False only when a check FAILED; warnings never flip it. monitor_ops alerts on ok == False.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

import numpy as np
import pandas as pd

from nfl import config

logger = logging.getLogger(__name__)

PASS, WARN, FAIL = "pass", "warn", "fail"
CONTRACTS_KEY = f"{config.NFL_PREFIX}/contracts_latest.json"

# Overdue thresholds, in days after the game's `gameday` (ET calendar date).
WARN_AFTER_DAYS = 1
FAIL_AFTER_DAYS = 2

NONNEG_COLS = ("completions", "attempts", "passing_tds", "passing_interceptions", "sacks_suffered", "carries",
               "rushing_tds", "receptions", "targets", "receiving_tds")
OFFENSE_ACT_COLS = ("completions", "attempts", "passing_yards", "passing_tds", "carries", "rushing_yards",
                    "rushing_tds", "receptions", "targets", "receiving_yards", "receiving_tds")
RECONCILE_COLS = ("passing_yards", "rushing_yards", "receiving_yards", "carries", "attempts")
PPR_MIN_MATCH = 0.995          # offensive players; measured ~1.000
PPR_POSITIONS = ("QB", "RB", "FB", "WR", "TE")
LINE_HORIZON_DAYS = 7
MAX_LISTED = 12                # cap on examples carried in a check's detail


def _check(name: str, status: str, **detail) -> dict:
    return {"name": name, "status": status, "ok": status != FAIL, "detail": detail}


def _examples(items) -> list:
    items = sorted(str(i) for i in items)
    return items[:MAX_LISTED] + ([f"... +{len(items) - MAX_LISTED} more"] if len(items) > MAX_LISTED else [])


def _gameday(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.date


# --------------------------------------------------------------------------------------------------------------
# pure checks (DataFrames in, one check dict out)
# --------------------------------------------------------------------------------------------------------------

def check_grain(schedules: pd.DataFrame, player_stats: pd.DataFrame, team_stats: pd.DataFrame) -> list:
    out = []
    for name, df, key in (("grain_schedules", schedules, ["game_id"]),
                          ("grain_player_stats", player_stats, ["player_id", "game_id"]),
                          ("grain_team_stats", team_stats, ["team", "game_id"])):
        missing = [c for c in key if c not in df.columns]
        if missing:
            out.append(_check(name, FAIL, reason=f"missing key columns {missing}"))
            continue
        null_mask = df[key].isna().any(axis=1)
        phantom = 0
        if name == "grain_player_stats" and null_mask.any():
            # nflverse writes ONE team-level row with a null player_id per week (verified 2022-2026: 22 per
            # season, 5 so far in 2026): zero offensive stats, only team penalties / safeties. Harmless; a null-id
            # row that carries OFFENSIVE stats is a real defect.
            off = [c for c in OFFENSE_ACT_COLS if c in df.columns]
            empty = (df[off].apply(pd.to_numeric, errors="coerce").fillna(0).abs().sum(axis=1) == 0) if off \
                else pd.Series(True, index=df.index)
            phantom = int((null_mask & empty).sum())
            null_mask = null_mask & ~empty
        nulls = int(null_mask.sum())
        dups = int(df[~df[key].isna().any(axis=1)].duplicated(key).sum())
        out.append(_check(name, PASS if not (dups or nulls) else FAIL, key=key, rows=len(df), duplicates=dups,
                          null_keys=nulls, **({"phantom_zero_rows": phantom} if phantom else {})))
    return out


def check_nonnegative(player_stats: pd.DataFrame) -> dict:
    bad = {}
    for c in NONNEG_COLS:
        if c in player_stats.columns:
            n = int((pd.to_numeric(player_stats[c], errors="coerce") < 0).sum())
            if n:
                bad[c] = n
    return _check("nonnegative_counting_stats", PASS if not bad else FAIL, negative_rows=bad)


def final_games(schedules: pd.DataFrame, season: int) -> pd.DataFrame:
    s = schedules[(schedules["season"] == season) & schedules["result"].notna()].copy()
    s["_day"] = _gameday(s["gameday"])
    return s


def _overdue_status(missing_days: pd.Series, today: date) -> str:
    """FAIL if any missing game is >= FAIL_AFTER_DAYS old, WARN if only >= WARN_AFTER_DAYS, else PASS."""
    if missing_days.empty:
        return PASS
    age = missing_days.map(lambda d: (today - d).days if d is not None and d == d else FAIL_AFTER_DAYS)
    if (age >= FAIL_AFTER_DAYS).any():
        return FAIL
    return WARN if (age >= WARN_AFTER_DAYS).any() else PASS


def check_coverage(name: str, finals: pd.DataFrame, present_game_ids, today: date, required: int = 1) -> dict:
    """Every final game must appear in a table. `present_game_ids` may be a Series/set of game_id."""
    present = set(present_game_ids)
    miss = finals[~finals["game_id"].isin(present)]
    status = _overdue_status(miss["_day"], today) if len(miss) else PASS
    return _check(name, status, final_games=len(finals), missing=len(miss), missing_games=_examples(miss["game_id"]))


def check_team_rows_per_game(finals: pd.DataFrame, team_stats: pd.DataFrame, today: date) -> dict:
    """A final game must have exactly two team_stats rows (one per side)."""
    n = team_stats.groupby("game_id").size()
    got = finals["game_id"].map(n).fillna(0).astype(int)
    bad = finals[got != 2]
    status = _overdue_status(bad["_day"], today) if len(bad) else PASS
    return _check("team_stats_two_sides", status, final_games=len(finals), wrong=len(bad),
                  games=_examples(bad["game_id"]))


def check_reconcile(player_stats: pd.DataFrame, team_stats: pd.DataFrame) -> dict:
    cols = [c for c in RECONCILE_COLS if c in player_stats.columns and c in team_stats.columns]
    if not cols:
        return _check("reconcile_player_vs_team", FAIL, reason="no comparable columns")
    ps = player_stats.copy()
    for c in cols:
        ps[c] = pd.to_numeric(ps[c], errors="coerce")
    g = ps.groupby(["game_id", "team"])[cols].sum(min_count=1).reset_index()
    m = g.merge(team_stats[["game_id", "team"] + cols], on=["game_id", "team"], suffixes=("", "_team"))
    if m.empty:
        return _check("reconcile_player_vs_team", FAIL, reason="no (game, team) overlap between the tables")
    mism = {}
    worst = []
    for c in cols:
        d = (m[c] - m[f"{c}_team"].astype(float)).abs()
        n = int((d > 0).sum())
        if n:
            mism[c] = n
            worst += [f"{r.game_id}/{r.team}:{c}" for r in m.loc[d > 0].head(3).itertuples()]
    return _check("reconcile_player_vs_team", PASS if not mism else FAIL, team_games=len(m), mismatches=mism,
                  examples=_examples(worst))


def ppr_formula(ps: pd.DataFrame) -> pd.Series:
    """The published nflverse PPR scoring: 0.04/pass yd, 4/pass TD, -2/INT, 0.1/rush+rec yd, 6/TD, 1/rec, -2/fumble
    lost, 2/two-point conversion, 6/special-teams or fumble-recovery TD."""
    def n(c):
        return pd.to_numeric(ps[c], errors="coerce").fillna(0.0) if c in ps.columns else 0.0
    return (n("passing_yards") * 0.04 + n("passing_tds") * 4 - 2 * n("passing_interceptions")
            + n("rushing_yards") * 0.1 + 6 * (n("rushing_tds") + n("receiving_tds")) + n("receiving_yards") * 0.1
            + n("receptions") - 2 * (n("sack_fumbles_lost") + n("rushing_fumbles_lost") + n("receiving_fumbles_lost"))
            + 2 * (n("passing_2pt_conversions") + n("rushing_2pt_conversions") + n("receiving_2pt_conversions"))
            + 6 * n("special_teams_tds") + 6 * n("fumble_recovery_tds"))


def check_ppr_scoring(player_stats: pd.DataFrame) -> dict:
    if "fantasy_points_ppr" not in player_stats.columns:
        return _check("scoring_ppr_formula", FAIL, reason="fantasy_points_ppr missing")
    ps = player_stats[player_stats["position"].isin(PPR_POSITIONS)] if "position" in player_stats.columns \
        else player_stats
    if ps.empty:
        return _check("scoring_ppr_formula", WARN, reason="no offensive rows")
    d = (ppr_formula(ps) - pd.to_numeric(ps["fantasy_points_ppr"], errors="coerce")).abs()
    match = float((d < 0.011).mean())
    return _check("scoring_ppr_formula", PASS if match >= PPR_MIN_MATCH else FAIL, rows=len(ps),
                  match_rate=round(match, 5), threshold=PPR_MIN_MATCH)


def check_schedule_results_fresh(schedules: pd.DataFrame, today: date) -> dict:
    """A game whose kickoff date is behind us must have a result. Without this the coverage checks only ever look at
    games nflverse ALREADY marked final, so a mirror that stopped updating would pass them forever."""
    s = schedules.copy()
    s["_day"] = _gameday(s["gameday"])
    over = s[s["result"].isna() & s["_day"].notna() & (s["_day"] <= today - timedelta(days=WARN_AFTER_DAYS))]
    status = _overdue_status(over["_day"], today) if len(over) else PASS
    return _check("schedule_results_fresh", status, overdue=len(over), games=_examples(over["game_id"]))


def check_schedule_lines(schedules: pd.DataFrame, today: date) -> dict:
    s = schedules.copy()
    s["_day"] = _gameday(s["gameday"])
    up = s[s["result"].isna() & s["_day"].notna() & (s["_day"] >= today)
           & (s["_day"] <= today + timedelta(days=LINE_HORIZON_DAYS))]
    if up.empty:
        return _check("schedule_lines_upcoming", PASS, upcoming=0, note="no games in the next week")
    miss = up[up["spread_line"].isna() | up["total_line"].isna()]
    return _check("schedule_lines_upcoming", PASS if miss.empty else WARN, upcoming=len(up), missing_lines=len(miss),
                  games=_examples(miss["game_id"]))


def expected_through(schedules: pd.DataFrame, season: int, today: date) -> Optional[dict]:
    """Newest (season, week) whose every game is final AND at least WARN_AFTER_DAYS old: where the data MUST be."""
    s = schedules[schedules["season"] == season].copy()
    s["_day"] = _gameday(s["gameday"])
    due = s[s["_day"].notna() & (s["_day"] <= today - timedelta(days=WARN_AFTER_DAYS))]
    if due.empty:
        return None
    done = due.groupby("week")["result"].apply(lambda r: bool(r.notna().all()))
    weeks = done[done].index
    return {"season": season, "week": int(weeks.max())} if len(weeks) else None


# --------------------------------------------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------------------------------------------

def _default_loader():
    from nfl.data.read import load
    return load


def run_contracts(season: Optional[int] = None, today: Optional[date] = None, loader: Optional[Callable] = None,
                  write: bool = False) -> dict:
    """Load the mirrored frames for `season` and evaluate every contract. Never raises: a load failure is a FAIL
    check, so a missing mirror is reported instead of crashing the refresh job."""
    load = loader or _default_loader()
    today = today or datetime.now(timezone.utc).date()
    season = int(season) if season else config.current_season(today)
    checks = []
    try:
        sched = load("schedules")
        ps = load("player_stats", seasons=[season])
        ts = load("team_stats", seasons=[season])
    except Exception as exc:            # noqa: BLE001
        report = _report([_check("load_core_tables", FAIL, error=f"{type(exc).__name__}: {exc}")], season, today, None)
        return _maybe_write(report, write)

    checks += check_grain(sched[sched["season"] == season], ps, ts)
    checks.append(check_nonnegative(ps))
    finals = final_games(sched, season)
    checks.append(check_coverage("coverage_player_stats", finals, ps["game_id"], today))
    checks.append(check_coverage("coverage_team_stats", finals, ts["game_id"], today))
    checks.append(check_team_rows_per_game(finals, ts, today))
    try:
        pbp_ids = load("pbp", seasons=[season], columns=["game_id"])["game_id"].unique()
        checks.append(check_coverage("coverage_pbp", finals, pbp_ids, today))
    except Exception as exc:            # noqa: BLE001
        checks.append(_check("coverage_pbp", WARN, error=f"{type(exc).__name__}: {exc}"))
    try:
        sn = load("snap_counts", seasons=[season], columns=["game_id"], strict=True)
        checks.append(_downgrade(check_coverage("coverage_snap_counts", finals, sn["game_id"].unique(), today)))
    except Exception as exc:            # noqa: BLE001
        checks.append(_check("coverage_snap_counts", WARN, error=f"{type(exc).__name__}: {exc}"))
    checks.append(check_reconcile(ps, ts))
    checks.append(check_ppr_scoring(ps))
    checks.append(check_schedule_results_fresh(sched[sched["season"] == season], today))
    checks.append(check_schedule_lines(sched[sched["season"] == season], today))
    return _maybe_write(_report(checks, season, today, expected_through(sched, season, today)), write)


def _downgrade(chk: dict) -> dict:
    """snap_counts come from PFR on its own cadence: a late file is a warning, never a failure."""
    if chk["status"] == FAIL:
        chk = {**chk, "status": WARN, "ok": True}
    return chk


def _report(checks: list, season: int, today: date, through: Optional[dict]) -> dict:
    n_fail = sum(c["status"] == FAIL for c in checks)
    n_warn = sum(c["status"] == WARN for c in checks)
    return {"ok": n_fail == 0, "status": FAIL if n_fail else (WARN if n_warn else PASS), "season": season,
            "today": today.isoformat(), "expected_through": through, "n_fail": n_fail, "n_warn": n_warn,
            "checks": checks, "generated_at": datetime.now(timezone.utc).isoformat()}


def _maybe_write(report: dict, write: bool) -> dict:
    if write:
        try:
            from mlb_core import storage
            storage.write_bytes(json.dumps(report, indent=1, default=str).encode(), CONTRACTS_KEY)
        except Exception as exc:        # noqa: BLE001
            logger.warning("could not write %s: %s", CONTRACTS_KEY, exc)
    return report


def summarize(report: dict) -> str:
    """One line per non-passing check (what monitor_ops / the job log shows)."""
    lines = [f"NFL contracts: {report['status']} ({report['n_fail']} fail, {report['n_warn']} warn)"]
    for c in report["checks"]:
        if c["status"] != PASS:
            lines.append(f"  [{c['status']}] {c['name']}: {json.dumps(c['detail'], default=str)[:300]}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", type=int)
    ap.add_argument("--today", help="YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--no-write", action="store_true", help="do not write NFL/contracts_latest.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    today = date.fromisoformat(args.today) if args.today else None
    report = run_contracts(args.season, today, write=not args.no_write)
    print(summarize(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
