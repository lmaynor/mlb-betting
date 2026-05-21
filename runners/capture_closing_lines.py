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

    Returns {bet_id: (closing_american_odds, complement_american_odds)}.
    complement_american_odds is None for prop markets.
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

    closing: dict[int, tuple] = {}
    for _, bet in bets.iterrows():
        away = bet.get("away_team", "")
        home = bet.get("home_team", "")
        ev   = market_map.get((away, home))
        if ev is None:
            logger.debug(f"capture_closing: no SGO event for {away}@{home}")
            continue

        bet_type = (bet.get("bet_type") or "").upper()
        odds_val = None

        complement_val = None
        if bet_type in ("NRFI",):
            nrfi_info = sgo.extract_nrfi_odds({ev.get("id", ""): ev})
            for info in nrfi_info.values():
                odds_val = info.get("nrfi_odds")
                complement_val = info.get("yrfi_odds")
                break
        elif bet_type in ("YRFI",):
            nrfi_info = sgo.extract_nrfi_odds({ev.get("id", ""): ev})
            for info in nrfi_info.values():
                odds_val = info.get("yrfi_odds")
                complement_val = info.get("nrfi_odds")
                break
        elif bet_type in ("HOME", "AWAY"):
            # F5 stores bet_type as "HOME"/"AWAY" (not "F5_HOME"/"F5_AWAY")
            if (bet.get("system") or "").upper() == "F5":
                f5_info = sgo.extract_f5_ml_odds({ev.get("id", ""): ev})
                for info in f5_info.values():
                    if bet_type == "HOME":
                        odds_val = info.get("home_odds")
                        complement_val = info.get("away_odds")
                    else:
                        odds_val = info.get("away_odds")
                        complement_val = info.get("home_odds")
                    break

        elif (bet.get("system") or "").upper() == "HR":
            hr_props = sgo.extract_hr_props([ev])
            player_raw = (bet.get("player") or "")
            def _norm(s):
                import unicodedata as _ud
                n = _ud.normalize("NFD", str(s))
                n = "".join(c for c in n if _ud.category(c) != "Mn")
                return n.encode("ascii", "ignore").decode().lower().strip()
            player_norm = _norm(player_raw)
            for prop_name, prop_info in hr_props.items():
                if _norm(prop_name) == player_norm:
                    odds_val = prop_info.get("odds")
                    break

        elif bet_type.startswith(("K_", "OUTS_")):
            parts = bt_upper.split("_")
            market = parts[0]  # "K" or "OUTS"
            side = parts[1]    # "OVER" or "UNDER"
            try:
                line = float(parts[2])
            except (IndexError, ValueError):
                line = None
            extractor = sgo.extract_k_odds if market == "K" else sgo.extract_outs_odds
            props = extractor([ev])
            def _norm2(s):
                import unicodedata as _ud
                n = _ud.normalize("NFD", str(s))
                n = "".join(c for c in n if _ud.category(c) != "Mn")
                return n.encode("ascii", "ignore").decode().lower().strip()
            player_norm = _norm2(bet.get("player") or "")
            for prop_name, prop_info in props.items():
                if _norm2(prop_name) == player_norm:
                    if line is not None and prop_info.get("line") is not None:
                        if abs(float(prop_info["line"]) - line) > 0.01:
                            continue
                    odds_val = prop_info.get("over_odds") if side == "OVER" else prop_info.get("under_odds")
                    break

        if odds_val is not None:
            closing[int(bet["id"])] = (float(odds_val), float(complement_val) if complement_val is not None else None)

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
        tracker = BetTracker(os.environ["MLB_DB_URL"], system=system)
        try:
            c_odds, comp_odds = closing_map[bid]
            tracker.write_closing_line(bid, c_odds, complement_odds=comp_odds)
            # E10: compute line_move_pct from morning_odds
            try:
                from mlb_core.odds.line_movement import compute_line_move_pct
                from sqlalchemy import text as _lmt
                with tracker.engine.connect() as _lmc:
                    _morn = _lmc.execute(
                        _lmt("SELECT morning_odds FROM bets WHERE id=:id"),
                        {"id": bid}
                    ).fetchone()
                if _morn and _morn[0] is not None:
                    _lm = compute_line_move_pct(_morn[0], c_odds)
                    if _lm is not None:
                        with tracker.engine.begin() as _lmw:
                            _lmw.execute(
                                _lmt("UPDATE bets SET line_move_pct=:lm WHERE id=:id"),
                                {"lm": _lm, "id": bid}
                            )
            except Exception as _lme:
                logger.warning(f"line_move_pct failed for bet_id={bid}: {_lme}")
            captured += 1
        except Exception as e:
            logger.warning(f"capture_closing: failed to write closing line for bet_id={bid}: {e}")
            skipped += 1

    # Per-system breakdown for acceptance criterion (C05)
    by_sys = {}
    for _, bet in open_bets.iterrows():
        s = (bet.get("system") or "?").upper()
        bid = int(bet["id"])
        if bid in closing_map:
            by_sys[s] = by_sys.get(s, [0, 0]); by_sys[s][0] += 1
        else:
            by_sys[s] = by_sys.get(s, [0, 0]); by_sys[s][1] += 1
    sys_str = " ".join(f"{s}={v[0]}/{v[0]+v[1]}" for s, v in sorted(by_sys.items()))
    logger.info(f"capture_closing: {sys_str} | captured={captured} skipped={skipped}")
    return {
        "status":   "ok",
        "run_date": run_date,
        "captured": captured,
        "skipped":  skipped,
    }


def main():
    import json
import os, sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
