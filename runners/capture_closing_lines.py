"""
runners/capture_closing_lines.py — Capture closing lines for open bets (T08).

Runs at T-5 min before each game's scheduled first pitch (ideally via a
per-game Cloud Scheduler job or by firing this script against the morning
and evening SGO snapshots just before game time).

For every bet in the DB that is:
  - kelly_triggered = TRUE
  - result IS NULL (not yet settled)
  - closing_odds IS NULL (not yet captured)
  - game_date == today

Fetches the current SGO snapshot and writes the closing odds + CLV.

Scheduled via Cloud Scheduler: fire this endpoint at ~14:55 UTC and ~20:55 UTC
(5 min before the prediction runs, same schedule as snapshot-odds). This means
the "closing" line is actually the last snapshot before game time, which is
the best proxy available without a dedicated closing-line API.

Called by main.py POST /capture-closing — add this endpoint when wiring up.

Entrypoint: python -m runners.capture_closing_lines
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

SYSTEMS = ["NRFI", "HR", "F5", "K", "OUTS"]


def _load_open_bets(run_date: str) -> pd.DataFrame:
    """Load all open bets for today with no closing line yet."""
    from mlb_core.tracking.bet_tracker import _make_engine
    from sqlalchemy import text

    engine = _make_engine("unused")
    with engine.connect() as conn:
        df = pd.read_sql(
            text("""
                SELECT id, system, game_pk, bet_type, away_team, home_team,
                       odds, market_prob, book
                  FROM bets
                 WHERE game_date   = :d
                   AND kelly_triggered = TRUE
                   AND result      IS NULL
                   AND closing_odds IS NULL
            """),
            conn, params={"d": run_date},
        )
    return df


def _get_closing_odds_from_snapshot(
    bets: pd.DataFrame,
) -> dict[int, float]:
    """
    Pull the current SGO snapshot and match each open bet to its market odds.

    Returns {bet_id: closing_american_odds}.
    """
    from mlb_core.odds import sgo
    from mlb_core.odds.dk_scraper import resolve_team

    events = sgo.load_snapshot("Odds/sgo/latest.json")
    if not events:
        logger.warning("capture_closing: SGO snapshot empty — cannot capture closing lines")
        return {}

    # Build lookup by (away_abbr, home_abbr) -> market odds per bet_type
    market_map: dict = {}
    for ev in events:
        away_abbr = resolve_team(ev.get("away_team", ""))
        home_abbr = resolve_team(ev.get("home_team", ""))
        if not away_abbr or not home_abbr:
            continue
        key = (away_abbr, home_abbr)
        market_map[key] = ev

    closing: dict[int, float] = {}
    for _, bet in bets.iterrows():
        away = bet.get("away_team", "")
        home = bet.get("home_team", "")
        ev   = market_map.get((away, home))
        if ev is None:
            logger.debug(f"capture_closing: no SGO event for {away}@{home}")
            continue

        bet_type = (bet.get("bet_type") or "").upper()
        odds_val = None

        if bet_type in ("NRFI",):
            nrfi_info = sgo.extract_nrfi_odds({ev.get("id", ""): ev})
            for info in nrfi_info.values():
                odds_val = info.get("nrfi_odds")
                break
        elif bet_type in ("YRFI",):
            nrfi_info = sgo.extract_nrfi_odds({ev.get("id", ""): ev})
            for info in nrfi_info.values():
                odds_val = info.get("yrfi_odds")
                break
        elif bet_type in ("F5_HOME", "F5_AWAY"):
            f5_info = sgo.extract_f5_odds({ev.get("id", ""): ev})
            for info in f5_info.values():
                odds_val = info.get("home_odds") if bet_type == "F5_HOME" else info.get("away_odds")
                break
        # HR and K props are player-level — closing line capture for props
        # requires matching by player name; skipped in v1 of this script.
        # Add player-level matching in T08 follow-up.

        if odds_val is not None:
            closing[int(bet["id"])] = float(odds_val)

    return closing


def run(run_date: str = None) -> dict:
    """Capture closing lines for all open bets today. Returns summary dict."""
    run_date = run_date or date.today().isoformat()
    logger.info(f"capture_closing: starting for {run_date}")

    open_bets = _load_open_bets(run_date)
    if open_bets.empty:
        logger.info("capture_closing: no open bets without closing line")
        return {"status": "ok", "run_date": run_date, "captured": 0, "skipped": 0}

    logger.info(f"capture_closing: {len(open_bets)} open bets to process")

    closing_map = _get_closing_odds_from_snapshot(open_bets)

    from mlb_core.tracking.bet_tracker import BetTracker

    captured = 0
    skipped  = 0
    for _, bet in open_bets.iterrows():
        bid = int(bet["id"])
        if bid not in closing_map:
            skipped += 1
            continue
        # Use a system-agnostic tracker instance — write_closing_line only
        # needs the engine, not system filtering.
        system = bet.get("system", "NRFI")
        tracker = BetTracker("unused", system=system)
        try:
            tracker.write_closing_line(bid, closing_map[bid])
            captured += 1
        except Exception as e:
            logger.warning(f"capture_closing: failed to write closing line for bet_id={bid}: {e}")
            skipped += 1

    logger.info(f"capture_closing: captured={captured} skipped={skipped}")
    return {
        "status":   "ok",
        "run_date": run_date,
        "captured": captured,
        "skipped":  skipped,
    }


def main():
    import json, sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
