"""
nfl.game.run_daily -- Daily quantitative NFL game-line prediction and pricing runner.

Produces institutional-grade fair values, market edges, and confluence detection for:
  * NFL Moneyline (NFL_ML)
  * NFL Point Spread (NFL_SPREAD)
  * NFL Game Total (NFL_TOTAL)

Outputs:
  * EVSweep/nfl_games/predictions_latest.json
  * EVSweep/nfl_games/archive/{date}.json

Usage:
  PYTHONPATH=. python -m nfl.game.run_daily [--write-gcs] [--date YYYY-MM-DD]

All outputs are labeled validated=False and UNVALIDATED until walk-forward CLV evidence
clears the adoption bar (minimum 100 games, CLV t-stat > 2.0).
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
from nba.game.odds_feed import (
    read_banked_game_lines,
    events_for_day,
    persist_payload,
    safe_price,
    UNVALIDATED_LABEL,
    MAX_AGE_MIN,
)
from nfl.config import current_season
from nfl.data.read import load as nfl_load
from nfl.game.kalman_ratings import KalmanNFLTracker
from nfl.game.model import NFLGameEngine
from nfl.game.pbp_features import (
    PBPFeatureStore,
    aggregate_team_game_pbp,
    compute_matchup_pbp_features,
)

log = logging.getLogger("nfl.game.run_daily")
_ET = ZoneInfo("America/New_York")

ENGINE_VERSION = "2026-11-02.1"
OUT_KEY = "EVSweep/nfl_games/predictions_latest.json"
ARCHIVE_PREFIX = "EVSweep/nfl_games/archive"
PARLAY_API_KEY = "americanfootball_nfl"

# NFL team abbreviation normalizer (mirrors kalman_ratings aliases)
_NFL_ALIASES: Dict[str, str] = {
    "JAC": "JAX", "LA": "LAR", "STL": "LAR", "OAK": "LV", "SD": "LAC",
    "WSH": "WAS", "GBP": "GB", "KAN": "KC", "NWE": "NE", "TBB": "TB",
    "SFO": "SF", "NOR": "NO", "LVR": "LV", "LAR": "LAR",
}


def nfl_team_code(name: str) -> Optional[str]:
    """Map a raw bookmaker team name to the canonical 2-3 letter NFL code."""
    if not name:
        return None
    s = str(name).strip().upper()
    # Common full-name fragments
    _NAME_MAP = {
        "49ERS": "SF", "BEARS": "CHI", "BENGALS": "CIN", "BILLS": "BUF",
        "BRONCOS": "DEN", "BROWNS": "CLE", "BUCCANEERS": "TB", "CARDINALS": "ARI",
        "CHARGERS": "LAC", "CHIEFS": "KC", "COLTS": "IND", "COMMANDERS": "WAS",
        "COWBOYS": "DAL", "DOLPHINS": "MIA", "EAGLES": "PHI", "FALCONS": "ATL",
        "GIANTS": "NYG", "JAGUARS": "JAX", "JETS": "NYJ", "LIONS": "DET",
        "PACKERS": "GB", "PANTHERS": "CAR", "PATRIOTS": "NE", "RAIDERS": "LV",
        "RAMS": "LAR", "RAVENS": "BAL", "SAINTS": "NO", "SEAHAWKS": "SEA",
        "STEELERS": "PIT", "TEXANS": "HOU", "TITANS": "TEN", "VIKINGS": "MIN",
    }
    for frag, code in _NAME_MAP.items():
        if frag in s:
            return code
    # Try direct abbreviation lookup (whole string, or space-split parts)
    candidates = [s] + s.split(" ")
    for p in candidates:
        p = p.strip()
        if not p:
            continue
        p2 = _NFL_ALIASES.get(p, p)
        if p2 in KalmanNFLTracker.NFL_TEAMS:
            return p2
    return None


def load_nfl_pbp_history(seasons: Optional[List[int]] = None) -> pd.DataFrame:
    """Load nflverse PBP parquets from GCS. Returns empty DataFrame on failure."""
    if seasons is None:
        cy = current_season()
        seasons = list(range(max(2018, cy - 5), cy + 1))
    try:
        dfs = []
        for s in seasons:
            try:
                df = nfl_load("pbp", seasons=[s])
                if not df.empty:
                    dfs.append(df)
            except Exception as exc:
                log.debug("Could not load PBP for season %s: %s", s, exc)
        if not dfs:
            return pd.DataFrame()
        combined = pd.concat(dfs, ignore_index=True)
        log.info("Loaded %d PBP rows from %d seasons", len(combined), len(dfs))
        return combined
    except Exception as exc:
        log.warning("PBP load failed: %s", exc)
        return pd.DataFrame()


def load_nfl_schedules(seasons: Optional[List[int]] = None) -> pd.DataFrame:
    """Load nflverse schedules for recent seasons."""
    if seasons is None:
        cy = current_season()
        seasons = list(range(max(2018, cy - 5), cy + 1))
    try:
        df = nfl_load("schedules", seasons=seasons)
        log.info("Loaded %d schedule rows", len(df))
        return df
    except Exception as exc:
        log.warning("Could not load NFL schedules: %s", exc)
        return pd.DataFrame()


def fit_kalman_and_pbp(
    pbp: pd.DataFrame,
    schedules: pd.DataFrame,
    before_date: str,
) -> Tuple[KalmanNFLTracker, PBPFeatureStore]:
    """
    Train Kalman + PBP stores strictly before `before_date`.
    Games on or after `before_date` are never fed into either store (zero leakage).
    """
    tracker = KalmanNFLTracker()
    store = PBPFeatureStore()

    if pbp.empty:
        log.warning("No PBP data; Kalman will run from cold-start priors")
        return tracker, store

    # Parse game_date from PBP (or schedules)
    date_col = None
    for c in ("game_date", "gameday", "date"):
        if c in pbp.columns:
            date_col = c
            break

    if date_col is None:
        log.warning("PBP has no date column; cannot sort for leakage guard")
        return tracker, store

    pbp_dated = pbp.copy()
    pbp_dated["_game_date_str"] = pbp_dated[date_col].astype(str).str[:10]

    # Filter strictly before target date
    past_pbp = pbp_dated[pbp_dated["_game_date_str"] < before_date]
    if past_pbp.empty:
        log.info("No historical PBP before %s; cold start", before_date)
        return tracker, store

    # Aggregate per-game PBP rows (one per team per game)
    game_rows = aggregate_team_game_pbp(past_pbp)
    log.info("Aggregated %d team-game PBP rows from %d pre-%s plays",
             len(game_rows), len(past_pbp), before_date)

    # Sort chronologically
    game_rows.sort(key=lambda g: (g.game_date, g.game_id, g.team))

    # Feed into Kalman and PBP store
    seen_game_teams: set = set()
    for g in game_rows:
        store.add_game(g)
        game_team_key = (g.game_id, g.team)
        if game_team_key in seen_game_teams:
            continue
        seen_game_teams.add(game_team_key)
        # Only update Kalman from the home team's perspective (to avoid double-update)
        if not g.is_home:
            continue
        # Find the away row for this game
        away_rows = [r for r in game_rows if r.game_id == g.game_id and not r.is_home]
        if not away_rows:
            continue
        a = away_rows[0]
        tracker.roll_season(g.game_date)
        tracker.update_game(
            home=g.team,
            away=a.team,
            home_score=g.team_score,
            away_score=a.team_score,
            home_pass_epa=g.ed_pass_epa,
            away_pass_epa=a.ed_pass_epa,
            home_rush_epa=g.ed_rush_epa,
            away_rush_epa=a.ed_rush_epa,
            home_plays=float(len(past_pbp[past_pbp["game_id"] == g.game_id])),
            away_plays=float(len(past_pbp[past_pbp["game_id"] == g.game_id])),
            kalman_margin=g.kalman_obs_margin,
            game_date=g.game_date,
        )

    tracker.roll_season(before_date)
    return tracker, store


def load_live_game_odds_nfl(
    now: Optional[datetime] = None,
    meta_out: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Load live NFL game odds from the banked ParlayAPI snapshot."""
    events, meta = read_banked_game_lines(PARLAY_API_KEY, now)
    if meta_out is not None:
        meta_out.update(meta)
    return events or []


def extract_nfl_market_odds(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse h2h, spreads, and totals from a ParlayAPI NFL event."""
    out: Dict[str, Any] = {"h2h": {}, "spreads": {}, "totals": {}}
    books = event.get("bookmakers") or event.get("books") or []
    away_raw = str(event.get("away_team", ""))
    home_raw = str(event.get("home_team", ""))

    h_code = nfl_team_code(home_raw)
    a_code = nfl_team_code(away_raw)
    if not h_code or not a_code:
        return out

    h2h_quotes: List[Dict[str, Any]] = []
    spread_quotes: List[Dict[str, Any]] = []
    total_quotes: List[Dict[str, Any]] = []

    for b in books:
        b_name = b.get("key") or b.get("name") or "unknown"
        for mk in b.get("markets") or []:
            mk_key = mk.get("key")
            outcomes = mk.get("outcomes") or []
            if mk_key == "h2h" and len(outcomes) == 2:
                p_map: Dict[str, Any] = {}
                for o in outcomes:
                    c = nfl_team_code(str(o.get("name", "")))
                    p_map[c] = o.get("price") if c else None
                ho = safe_price(p_map.get(h_code))
                ao = safe_price(p_map.get(a_code))
                if ho is not None and ao is not None:
                    h2h_quotes.append({"book": b_name, "home_odds": ho, "away_odds": ao})
            elif mk_key == "spreads":
                p_map = {}
                line_val = None
                for o in outcomes:
                    c = nfl_team_code(str(o.get("name", "")))
                    p_map[c] = o.get("price") if c else None
                    if c == h_code:
                        line_val = o.get("point")
                ho = safe_price(p_map.get(h_code))
                ao = safe_price(p_map.get(a_code))
                if ho is not None and ao is not None and line_val is not None:
                    spread_quotes.append({
                        "book": b_name, "line": float(line_val), "home_odds": ho, "away_odds": ao
                    })
            elif mk_key == "totals":
                p_map = {}
                line_val = None
                for o in outcomes:
                    s = str(o.get("name", "")).lower()
                    p_map[s] = o.get("price")
                    line_val = o.get("point")
                oo = safe_price(p_map.get("over"))
                uo = safe_price(p_map.get("under"))
                if oo is not None and uo is not None and line_val is not None:
                    total_quotes.append({
                        "book": b_name, "line": float(line_val), "over_odds": oo, "under_odds": uo
                    })

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
            "p_home_fair": med_h, "p_away_fair": 1.0 - med_h,
            "best_home_odds": best_h_odds if best_h_book else None,
            "best_home_book": best_h_book,
            "best_away_odds": best_a_odds if best_a_book else None,
            "best_away_book": best_a_book,
            "n_books": len(h2h_quotes),
        }

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
                "line": med_line, "p_home_fair": med_h, "p_away_fair": 1.0 - med_h,
                "best_home_odds": best_h_odds if best_h_book else None,
                "best_home_book": best_h_book,
                "best_away_odds": best_a_odds if best_a_book else None,
                "best_away_book": best_a_book,
                "n_books": len(matching),
            }

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
                "line": med_line, "p_over_fair": med_o, "p_under_fair": 1.0 - med_o,
                "best_over_odds": best_o_odds if best_o_book else None,
                "best_over_book": best_o_book,
                "best_under_odds": best_u_odds if best_u_book else None,
                "best_under_book": best_u_book,
                "n_books": len(matching),
            }

    return out


def run_nfl_game_predictions(
    target_date: Optional[str] = None,
    write_gcs: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Full quantitative NFL game-line prediction pipeline.
    - Loads nflverse PBP + schedules (2018 to current season)
    - Trains Kalman filter + PBP feature store strictly before target_date (zero leakage)
    - Loads live NFL odds from OddsAccum/americanfootball_nfl/
    - Prices upcoming games with NFL_ML, NFL_SPREAD, NFL_TOTAL
    - All outputs labeled validated=False
    """
    now = now or datetime.now(timezone.utc)
    et_today = now.astimezone(_ET).strftime("%Y-%m-%d")
    date_str = target_date or et_today

    cy = current_season()
    seasons = list(range(max(2018, cy - 5), cy + 1))

    pbp = load_nfl_pbp_history(seasons)
    schedules = load_nfl_schedules(seasons)
    tracker, store = fit_kalman_and_pbp(pbp, schedules, before_date=date_str)

    engine = NFLGameEngine()

    odds_meta: Dict[str, Any] = {}
    events = load_live_game_odds_nfl(now, meta_out=odds_meta)
    odds_ok = bool(odds_meta.get("ok", bool(events)))
    n_loaded = len(events)
    events, _ = events_for_day(events, date_str)
    odds_meta["events_loaded"] = n_loaded
    odds_meta["events_for_day"] = len(events)

    games_meta = []
    board_rows = []
    priced_games = []

    for ev in events:
        away_raw = str(ev.get("away_team", ""))
        home_raw = str(ev.get("home_team", ""))
        away = nfl_team_code(away_raw)
        home = nfl_team_code(home_raw)
        if not away or not home:
            log.debug("Could not resolve team codes for %r @ %r; skipping", away_raw, home_raw)
            continue

        commence = str(ev.get("commence_time") or "")
        game_key = f"nfl:{date_str}:{away}@{home}"

        # Determine week from schedules if available
        season_num = cy
        week_num = 1
        if not schedules.empty and "gameday" in schedules.columns:
            row = schedules[
                (schedules.get("away_team", "").astype(str).apply(nfl_team_code) == away)
                & (schedules.get("home_team", "").astype(str).apply(nfl_team_code) == home)
                & (schedules.get("gameday", "").astype(str).str[:10] == date_str)
            ]
            if not row.empty:
                season_num = int(row.iloc[0].get("season", cy))
                week_num = int(row.iloc[0].get("week", 1))

        games_meta.append({
            "game_key": game_key,
            "away": away,
            "home": home,
            "start": commence,
            "season": season_num,
            "week": week_num,
        })

        tracker.roll_season(date_str)
        k_pred = tracker.predict_game(home, away, season_num, week_num, date_str)

        pbp_feat = compute_matchup_pbp_features(
            home=home,
            away=away,
            season=season_num,
            week=week_num,
            game_date=date_str,
            store=store,
            schedules=schedules if not schedules.empty else None,
        )

        mkt_odds = extract_nfl_market_odds(ev)
        pg = engine.price_game(game_key, k_pred, pbp_feat, market_odds=mkt_odds)
        priced_games.append(pg)

        for m in pg.markets:
            is_over_or_home = m.side in ("over", "home")
            stat_name = (
                "moneyline" if m.system == "NFL_ML"
                else ("spread" if m.system == "NFL_SPREAD" else "total")
            )
            row = {
                "sport": "nfl",
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
                "proj_mean": round(
                    pg.mu_margin if m.system != "NFL_TOTAL" else pg.mu_total, 2
                ),
                "p_model_side": round(m.p_model, 4),
                "p_mkt_side": round(m.p_fair, 4) if m.p_fair is not None else None,
                "n_books": m.n_books,
                "best_over_odds": m.best_odds if is_over_or_home else None,
                "best_over_book": m.best_book if is_over_or_home else None,
                "best_under_odds": m.best_odds if not is_over_or_home else None,
                "best_under_book": m.best_book if not is_over_or_home else None,
                "model_edge": round(m.model_edge, 4) if m.model_edge is not None else None,
                "market_ev": round(m.market_ev, 4) if m.market_ev is not None else None,
                "confluence": m.confluence,
                "beezy_score": m.beezy_score,
                "push_prob": round(m.push_prob, 4),
                "validated": False,
                "label": UNVALIDATED_LABEL,
            }
            board_rows.append(row)

    payload = {
        "sport": "nfl",
        "market_type": "game",
        "engine": "kalman_nfl_key_number_v1",
        "engine_version": ENGINE_VERSION,
        # NFL ML/spread/totals: walk-forward evaluation pending. model_edge is a model-vs-market
        # gap, not a tested edge. Consumers must show `label` next to any edge figure.
        "validated": False,
        "label": UNVALIDATED_LABEL,
        "stale": False,
        "ts": now.isoformat(),
        "date": date_str,
        "count": len(board_rows),
        "status": "live" if board_rows else "awaiting_lines",
        "lines_source": "oddsaccum_game_lines",
        "odds_meta": odds_meta,
        "games": games_meta,
        "rows": board_rows,
        "priced_games": [pg.to_dict() for pg in priced_games],
    }

    if write_gcs:
        return persist_payload(payload, OUT_KEY, ARCHIVE_PREFIX, odds_ok)

    return payload


def get_nfl_game_predictions(
    date: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Public getter: reads latest cached payload or recomputes if missing."""
    if not force and storage.exists(OUT_KEY):
        try:
            raw = storage.read_bytes(OUT_KEY)
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            log.warning("Could not parse %s: %s", OUT_KEY, exc)
    return run_nfl_game_predictions(target_date=date, write_gcs=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="NFL Quantitative Game-Line Engine")
    parser.add_argument("--write-gcs", action="store_true", help="Persist output to GCS")
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    res = run_nfl_game_predictions(target_date=args.date, write_gcs=args.write_gcs)
    print(
        f"Generated {res['count']} market predictions across {len(res['games'])} games "
        f"(status={res['status']})"
    )


if __name__ == "__main__":
    main()
