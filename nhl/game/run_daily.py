"""
nhl.game.run_daily -- Daily quantitative NHL game-line prediction and pricing runner.

Produces institutional-grade fair values, market edges, and confluence detection for:
  * NHL Moneyline (NHL_ML)
  * NHL Puck Line / Spread (NHL_SPREAD, -1.5 / +1.5)
  * NHL Game Total (NHL_TOTAL, 5.5 / 6.0 / 6.5)

Outputs:
  * EVSweep/nhl_games/predictions_latest.json
  * EVSweep/nhl_games/archive/{date}.json

Usage:
  PYTHONPATH=. python -m nhl.game.run_daily [--write-gcs] [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from mlb_core import storage
from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_two_way
from nhl.config import GAMES_MASTER, GOALIE_BOX_MASTER, TEAM_BOX_MASTER, team_abbrev
from nhl.game.features import FeatureStore, MatchupFeatures, build_team_game_stats_from_raw
from nhl.game.goalie_model import GoalieTracker, build_goalie_tracker_from_df
from nhl.game.kalman_ratings import KalmanTeamTracker
from nhl.game.matrix_pricer import DistributionalNHLMatrixEngine, PricedGame, PricedMarket

log = logging.getLogger("nhl.game.run_daily")
_CT = ZoneInfo("America/Chicago")
_ET = ZoneInfo("America/New_York")

OUT_KEY = "EVSweep/nhl_games/predictions_latest.json"
ARCHIVE_PREFIX = "EVSweep/nhl_games/archive"


def load_historical_boxscores() -> pd.DataFrame:
    """Load team boxscores master if available, or return empty DataFrame."""
    for key in [TEAM_BOX_MASTER, "NHL/team_boxscores_master.csv"]:
        if storage.exists(key):
            try:
                df = storage.read_csv(key)
                if not df.empty:
                    log.info("Loaded %d team boxscores from %s", len(df), key)
                    return df
            except Exception as exc:
                log.warning("Could not read boxscores at %s: %s", key, exc)
    return pd.DataFrame()


def load_historical_goalies() -> pd.DataFrame:
    """Load goalie boxscores master if available, or return empty DataFrame."""
    for key in [GOALIE_BOX_MASTER, "NHL/goalie_boxscores_master.csv"]:
        if storage.exists(key):
            try:
                df = storage.read_csv(key)
                if not df.empty:
                    log.info("Loaded %d goalie boxscores from %s", len(df), key)
                    return df
            except Exception as exc:
                log.warning("Could not read goalie boxscores at %s: %s", key, exc)
    return pd.DataFrame()


def load_live_game_odds(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Load upcoming NHL game lines (h2h, spreads, totals) from banked odds or live aggregator.
    """
    now = now or datetime.now(timezone.utc)
    for key in [
        "OddsAccum/icehockey_nhl/latest_game_lines.json",
        "OddsAccum/icehockey_nhl/latest.json",
        "Odds/bettingpros_live/nhl/latest.json",
    ]:
        if storage.exists(key):
            try:
                raw = storage.read_bytes(key)
                data = json.loads(raw.decode("utf-8"))
                events = data.get("events") or data.get("rows") or []
                if events:
                    log.info("Loaded %d events from %s", len(events), key)
                    return events
            except Exception as exc:
                log.warning("Failed reading odds from %s: %s", key, exc)
    return []


def extract_game_market_odds(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse bookmakers for h2h, spreads, and totals from a ParlayAPI or BettingPros event.
    """
    out: Dict[str, Any] = {"h2h": {}, "spreads": {}, "totals": {}}
    books = event.get("bookmakers") or event.get("books") or []
    away_team = str(event.get("away_team", ""))
    home_team = str(event.get("home_team", ""))

    h_code = team_abbrev(home_team)
    a_code = team_abbrev(away_team)
    if not h_code or not a_code:
        return out

    h2h_quotes = []
    spread_quotes = []
    total_quotes = []

    for b in books:
        b_name = b.get("key") or b.get("name") or "unknown"
        for mk in b.get("markets") or []:
            mk_key = mk.get("key")
            outcomes = mk.get("outcomes") or []
            if mk_key == "h2h" and len(outcomes) == 2:
                p_map = {}
                for o in outcomes:
                    p_map[team_abbrev(str(o.get("name")))] = o.get("price")
                if h_code in p_map and a_code in p_map and p_map[h_code] and p_map[a_code]:
                    h2h_quotes.append({
                        "book": b_name,
                        "home_odds": int(p_map[h_code]),
                        "away_odds": int(p_map[a_code]),
                    })
            elif mk_key == "spreads":
                p_map = {}
                line_val = None
                for o in outcomes:
                    c = team_abbrev(str(o.get("name")))
                    p_map[c] = o.get("price")
                    if c == h_code:
                        line_val = o.get("point")
                if h_code in p_map and a_code in p_map and line_val is not None:
                    spread_quotes.append({
                        "book": b_name,
                        "line": float(line_val),
                        "home_odds": int(p_map[h_code]),
                        "away_odds": int(p_map[a_code]),
                    })
            elif mk_key == "totals":
                p_map = {}
                line_val = None
                for o in outcomes:
                    s = str(o.get("name", "")).lower()
                    p_map[s] = o.get("price")
                    line_val = o.get("point")
                if "over" in p_map and "under" in p_map and line_val is not None:
                    total_quotes.append({
                        "book": b_name,
                        "line": float(line_val),
                        "over_odds": int(p_map["over"]),
                        "under_odds": int(p_map["under"]),
                    })

    # Consensus de-vig for h2h
    if h2h_quotes:
        devigged_h = []
        best_h_odds, best_h_book = -99999, None
        best_a_odds, best_a_book = -99999, None
        for q in h2h_quotes:
            ho, ao = q["home_odds"], q["away_odds"]
            fh, _ = devig_two_way(american_to_implied_prob(ho), american_to_implied_prob(ao), "proportional")
            devigged_h.append(fh)
            if ho > best_h_odds:
                best_h_odds, best_h_book = ho, q["book"]
            if ao > best_a_odds:
                best_a_odds, best_a_book = ao, q["book"]
        med_h = float(np.median(devigged_h))
        out["h2h"] = {
            "p_home_fair": med_h,
            "p_away_fair": 1.0 - med_h,
            "best_home_odds": best_h_odds if best_h_book else None,
            "best_home_book": best_h_book,
            "best_away_odds": best_a_odds if best_a_book else None,
            "best_away_book": best_a_book,
            "n_books": len(h2h_quotes),
        }

    # Consensus for spreads (modal line)
    if spread_quotes:
        lines = [q["line"] for q in spread_quotes]
        med_line = float(np.median(lines))
        matching = [q for q in spread_quotes if abs(q["line"] - med_line) < 1e-4]
        if matching:
            devigged_h = []
            best_h_odds, best_h_book = -99999, None
            best_a_odds, best_a_book = -99999, None
            for q in matching:
                ho, ao = q["home_odds"], q["away_odds"]
                fh, _ = devig_two_way(american_to_implied_prob(ho), american_to_implied_prob(ao), "proportional")
                devigged_h.append(fh)
                if ho > best_h_odds:
                    best_h_odds, best_h_book = ho, q["book"]
                if ao > best_a_odds:
                    best_a_odds, best_a_book = ao, q["book"]
            med_h = float(np.median(devigged_h))
            out["spreads"] = {
                "line": med_line,
                "p_home_fair": med_h,
                "p_away_fair": 1.0 - med_h,
                "best_home_odds": best_h_odds if best_h_book else None,
                "best_home_book": best_h_book,
                "best_away_odds": best_a_odds if best_a_book else None,
                "best_away_book": best_a_book,
                "n_books": len(matching),
            }

    # Consensus for totals (modal line)
    if total_quotes:
        lines = [q["line"] for q in total_quotes]
        med_line = float(np.median(lines))
        matching = [q for q in total_quotes if abs(q["line"] - med_line) < 1e-4]
        if matching:
            devigged_o = []
            best_o_odds, best_o_book = -99999, None
            best_u_odds, best_u_book = -99999, None
            for q in matching:
                oo, uo = q["over_odds"], q["under_odds"]
                fo, _ = devig_two_way(american_to_implied_prob(oo), american_to_implied_prob(uo), "proportional")
                devigged_o.append(fo)
                if oo > best_o_odds:
                    best_o_odds, best_o_book = oo, q["book"]
                if uo > best_u_odds:
                    best_u_odds, best_u_book = uo, q["book"]
            med_o = float(np.median(devigged_o))
            out["totals"] = {
                "line": med_line,
                "p_over_fair": med_o,
                "p_under_fair": 1.0 - med_o,
                "best_over_odds": best_o_odds if best_o_book else None,
                "best_over_book": best_o_book,
                "best_under_odds": best_u_odds if best_u_book else None,
                "best_under_book": best_u_book,
                "n_books": len(matching),
            }

    return out


def run_nhl_game_predictions(
    target_date: Optional[str] = None,
    write_gcs: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Execute daily NHL game prediction pipeline:
      1. Load historical boxscores & goalies up to target_date.
      2. Replay Kalman ratings & Goalie GSAx.
      3. Ingest upcoming games and market odds.
      4. Price Moneyline, Puck Line, and Game Totals with Dixon-Coles + ENG matrix.
      5. Save payload to EVSweep/nhl_games/predictions_latest.json.
    """
    now = now or datetime.now(timezone.utc)
    date_str = target_date or now.astimezone(_ET).strftime("%Y-%m-%d")

    # 1. Historical data replay
    df_boxscores = load_historical_boxscores()
    df_goalies = load_historical_goalies()

    feature_store = FeatureStore()
    kalman_tracker = KalmanTeamTracker()
    goalie_tracker = build_goalie_tracker_from_df(df_goalies)

    if df_boxscores is not None and not df_boxscores.empty:
        stats = build_team_game_stats_from_raw(df_boxscores)
        # Sort chronologically
        stats = sorted(stats, key=lambda s: s.game_date)
        prior_stats = [s for s in stats if s.game_date < date_str]

        for s in prior_stats:
            feature_store.add_game_stat(s)
            if s.is_home:
                # Update Kalman filter
                kalman_tracker.update_game(
                    home=s.team,
                    away=s.opp,
                    home_goals=s.goals,
                    away_goals=s.goals_against,
                    home_sog=s.sog,
                    away_sog=s.shots_against,
                    minutes=s.minutes,
                    game_date=s.game_date,
                )

    engine = DistributionalNHLMatrixEngine()

    # 2. Ingest slate / live odds
    events = load_live_game_odds(now)

    # Filter events for target date if commence_time present
    target_events: List[Dict[str, Any]] = []
    for ev in events:
        comm = ev.get("commence_time") or ev.get("start_time") or ""
        if comm:
            try:
                ev_date = datetime.fromisoformat(comm.replace("Z", "+00:00")).astimezone(_ET).strftime("%Y-%m-%d")
                if ev_date == date_str:
                    target_events.append(ev)
            except Exception:
                target_events.append(ev)
        else:
            target_events.append(ev)

    priced_games: List[PricedGame] = []
    board_rows: List[Dict[str, Any]] = []
    games_meta: List[Dict[str, Any]] = []

    for ev in target_events:
        home_n = str(ev.get("home_team", ""))
        away_n = str(ev.get("away_team", ""))
        h_code = team_abbrev(home_n)
        a_code = team_abbrev(away_n)
        if not h_code or not a_code:
            continue

        gk = f"nhl:{date_str}:{a_code}@{h_code}"

        # Goalie projections
        h_proj = goalie_tracker.project_team_goalie(h_code, date_str)
        a_proj = goalie_tracker.project_team_goalie(a_code, date_str)

        matchup = feature_store.build_matchup_features(
            home=h_code,
            away=a_code,
            game_date=date_str,
            home_goalie_gsax_60=h_proj.projected_gsax_60,
            away_goalie_gsax_60=a_proj.projected_gsax_60,
        )

        kalman_pred = kalman_tracker.predict_game(h_code, a_code, game_date=date_str)
        market_odds = extract_game_market_odds(ev)

        pg = engine.price_game(
            kalman_pred=kalman_pred,
            matchup=matchup,
            game_key=gk,
            market_odds=market_odds,
        )
        priced_games.append(pg)

        comm_time = ev.get("commence_time") or ev.get("start_time")
        games_meta.append({
            "game_key": gk,
            "home": h_code,
            "away": a_code,
            "home_name": home_n,
            "away_name": away_n,
            "commence_time": comm_time,
            "lambda_h": pg.lambda_h,
            "lambda_a": pg.lambda_a,
            "exp_total": pg.exp_total,
            "home_goalie": h_proj.starter_name,
            "away_goalie": a_proj.starter_name,
            "is_b2b_road": matchup.fatigue.is_b2b_road,
        })

        # Flatten into board rows
        for m in pg.markets:
            is_home_or_over = m.side in ("home", "over")
            row = {
                "id": f"{gk}:{m.system}:{m.side}:{m.line if m.line is not None else 'ml'}",
                "sport": "nhl",
                "system": m.system,
                "market_type": "game",
                "game_key": gk,
                "game_date": date_str,
                "team": h_code if m.side == "home" else (a_code if m.side == "away" else None),
                "opp": a_code if m.side == "home" else (h_code if m.side == "away" else None),
                "home_team": h_code,
                "away_team": a_code,
                "market": m.market_label,
                "side": m.side,
                "line": m.line,
                "p_model": round(m.p_model, 4),
                "p_fair": round(m.p_fair, 4) if m.p_fair is not None else None,
                "p_blended": round(m.p_blended, 4) if m.p_blended is not None else None,
                "n_books": m.n_books,
                "best_odds": m.best_odds,
                "best_book": m.best_book,
                "model_edge": round(m.model_edge, 4) if m.model_edge is not None else None,
                "market_ev": round(m.market_ev, 4) if m.market_ev is not None else None,
                "confluence": m.confluence,
                "beezy_score": m.beezy_score,
                "push_prob": round(m.push_prob, 4),
            }
            board_rows.append(row)

    payload = {
        "sport": "nhl",
        "market_type": "game",
        "engine": "dixon_coles_eng_matrix_v1",
        "ts": now.isoformat(),
        "date": date_str,
        "count": len(board_rows),
        "status": "live" if board_rows else "awaiting_lines",
        "games": games_meta,
        "rows": board_rows,
        "priced_games": [pg.to_dict() for pg in priced_games],
    }

    if write_gcs:
        raw_bytes = json.dumps(payload, indent=2).encode("utf-8")
        storage.write_bytes(raw_bytes, OUT_KEY)
        storage.write_bytes(raw_bytes, f"{ARCHIVE_PREFIX}/{date_str}.json")
        log.info("Saved NHL game predictions to %s", OUT_KEY)

    return payload


def get_nhl_game_predictions(
    date: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Public getter: reads latest cached prediction payload or recomputes if missing.
    """
    if not force and storage.exists(OUT_KEY):
        try:
            raw = storage.read_bytes(OUT_KEY)
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            log.warning("Could not parse %s: %s", OUT_KEY, exc)
    return run_nhl_game_predictions(target_date=date, write_gcs=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="NHL Quantitative Game-Line Engine Scorer")
    parser.add_argument("--write-gcs", action="store_true", help="Persist output to GCS/disk")
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    res = run_nhl_game_predictions(target_date=args.date, write_gcs=args.write_gcs)
    print(f"Generated {res['count']} market predictions across {len(res['games'])} games (status={res['status']})")


if __name__ == "__main__":
    main()
