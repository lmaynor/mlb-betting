"""
nba.game.run_daily -- Daily quantitative NBA game-line prediction and pricing runner.

Produces institutional-grade fair values, market edges, and confluence detection for:
  * NBA Moneyline (NBA_ML)
  * NBA Point Spread (NBA_SPREAD)
  * NBA Game Total (NBA_TOTAL)

Outputs:
  * EVSweep/nba_games/predictions_latest.json
  * EVSweep/nba_games/archive/{date}.json

Usage:
  PYTHONPATH=. python -m nba.game.run_daily [--write-gcs] [--date YYYY-MM-DD]
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
from nba.config import GAMES_MASTER, TEAM_BOX_MASTER
from nba.game.features import FeatureStore, MatchupFeatures, build_team_game_stats_from_raw
from nba.game.kalman_ratings import KalmanTeamTracker
from nba.game.model import DistributionalGameEngine, PricedGame, PricedMarket
from nba.teams import TEAMS, team_code

log = logging.getLogger("nba.game.run_daily")
_CT = ZoneInfo("America/Chicago")
_ET = ZoneInfo("America/New_York")

OUT_KEY = "EVSweep/nba_games/predictions_latest.json"
ARCHIVE_PREFIX = "EVSweep/nba_games/archive"


def load_historical_boxscores() -> pd.DataFrame:
    """Load team boxscores master if available, or return empty DataFrame."""
    for key in [TEAM_BOX_MASTER, "NBA/team_boxscores_master.csv"]:
        if storage.exists(key):
            try:
                df = storage.read_csv(key)
                if not df.empty:
                    log.info("Loaded %d team boxscores from %s", len(df), key)
                    return df
            except Exception as exc:
                log.warning("Could not read boxscores at %s: %s", key, exc)
    return pd.DataFrame()


def load_live_game_odds(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Load upcoming NBA game lines (h2h, spreads, totals) from banked odds or live aggregator.
    """
    now = now or datetime.now(timezone.utc)
    # Check OddsAccum/basketball_nba/latest_game_lines.json or latest.json
    for key in [
        "OddsAccum/basketball_nba/latest_game_lines.json",
        "OddsAccum/basketball_nba/latest.json",
        "Odds/bettingpros_live/nba/latest.json",
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


def load_live_injuries() -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch current NBA injuries by team code.
    Fails closed: if network or parsing fails, returns empty dict.
    """
    try:
        from nba.data import espn_injuries
        df = espn_injuries.fetch_injuries()
        if df.empty or not df.attrs.get("ok", True):
            return {}
        by_team: Dict[str, List[Dict[str, Any]]] = {}
        for _, row in df.iterrows():
            t_abbr = team_code(str(row.get("team_abbrev", "")))
            if not t_abbr:
                continue
            by_team.setdefault(t_abbr, []).append({
                "player": row.get("player"),
                "status": row.get("status"),
                "detail": row.get("detail"),
                "raw_status": row.get("raw_status"),
            })
        return by_team
    except Exception as exc:
        log.warning("Could not load live injuries: %s", exc)
        return {}


def extract_game_market_odds(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse bookmakers for h2h, spreads, and totals from a ParlayAPI or BettingPros event.
    """
    out: Dict[str, Any] = {"h2h": {}, "spreads": {}, "totals": {}}
    books = event.get("bookmakers") or event.get("books") or []
    away_team = str(event.get("away_team", ""))
    home_team = str(event.get("home_team", ""))

    h_code = team_code(home_team)
    a_code = team_code(away_team)
    if not h_code or not a_code:
        return out

    # Collect across books
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
                    p_map[team_code(str(o.get("name")))] = o.get("price")
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
                    c = team_code(str(o.get("name")))
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


def run_nba_game_predictions(
    target_date: Optional[str] = None,
    write_gcs: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Execute full quantitative game-line prediction pipeline for the upcoming NBA slate.
    """
    now = now or datetime.now(timezone.utc)
    ct_today = now.astimezone(_CT).strftime("%Y-%m-%d")
    date_str = target_date or ct_today

    # 1. Load historical training data
    df_box = load_historical_boxscores()
    hist_stats = build_team_game_stats_from_raw(df_box)

    kalman = KalmanTeamTracker()
    feature_store = FeatureStore()
    engine = DistributionalGameEngine()

    # Sort games chronologically for zero-leakage training
    hist_stats.sort(key=lambda g: (g.game_date, g.game_id))
    for g in hist_stats:
        if str(g.game_date)[:10] < date_str:
            if g.is_home:
                # Find matching opponent row to update Kalman
                kalman.update_game(
                    g.team, g.opp, g.points, g.opp_points, g.possessions, g.minutes, g.game_date
                )
            feature_store.add_games([g])

    # 2. Load live data
    injuries = load_live_injuries()
    events = load_live_game_odds(now)

    games_meta = []
    board_rows = []
    priced_games = []

    for ev in events:
        away = team_code(str(ev.get("away_team", "")))
        home = team_code(str(ev.get("home_team", "")))
        if not away or not home:
            continue

        commence = str(ev.get("commence_time") or "")
        game_key = f"nba:{date_str}:{away}@{home}"

        games_meta.append({
            "game_key": game_key,
            "away": away,
            "home": home,
            "start": commence,
        })

        k_pred = kalman.predict_game(home, away, date_str)
        m_feat = feature_store.extract_matchup_features(home, away, date_str, injuries)
        mkt_odds = extract_game_market_odds(ev)

        pg = engine.price_full_game(game_key, k_pred, m_feat, market_odds=mkt_odds)
        priced_games.append(pg)

        # Convert markets to SportProjectionRow format for /picks/games and /picks/nba
        for m in pg.markets:
            is_over = m.side in ("over", "home")
            stat_name = "moneyline" if m.system == "NBA_ML" else ("spread" if m.system == "NBA_SPREAD" else "total")
            row = {
                "sport": "nba",
                "system": m.system,
                "stat": stat_name,
                "stat_label": m.market_label,
                "player": f"{away} @ {home}",
                "player_norm": f"{away.lower()}_{home.lower()}_{m.system.lower()}_{m.side}",
                "team": home if m.side == "home" else (away if m.side == "away" else None),
                "opp": away if m.side == "home" else (home if m.side == "away" else None),
                "game_key": game_key,
                "commence": commence,
                "side": m.side,
                "line": m.line,
                "has_line": m.line is not None,
                "proj_mean": round(pg.mu_margin if m.system != "NBA_TOTAL" else pg.mu_total, 2),
                "p_model_side": round(m.p_model, 4),
                "p_mkt_side": round(m.p_fair, 4) if m.p_fair is not None else None,
                "n_books": m.n_books,
                "best_over_odds": m.best_odds if is_over else None,
                "best_over_book": m.best_book if is_over else None,
                "best_under_odds": m.best_odds if not is_over else None,
                "best_under_book": m.best_book if not is_over else None,
                "model_edge": round(m.model_edge, 4) if m.model_edge is not None else None,
                "market_ev": round(m.market_ev, 4) if m.market_ev is not None else None,
                "confluence": m.confluence,
                "beezy_score": m.beezy_score,
                "push_prob": round(m.push_prob, 4),
            }
            board_rows.append(row)

    payload = {
        "sport": "nba",
        "market_type": "game",
        "engine": "kalman_joint_t_v1",
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
        log.info("Saved NBA game predictions to %s", OUT_KEY)

    return payload


def get_nba_game_predictions(
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
    return run_nba_game_predictions(target_date=date, write_gcs=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="NBA Quantitative Game-Line Engine Scorer")
    parser.add_argument("--write-gcs", action="store_true", help="Persist output to GCS/disk")
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    res = run_nba_game_predictions(target_date=args.date, write_gcs=args.write_gcs)
    print(f"Generated {res['count']} market predictions across {len(res['games'])} games (status={res['status']})")


if __name__ == "__main__":
    main()
