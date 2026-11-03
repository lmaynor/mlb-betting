"""soccer.game.run_daily -- the daily soccer game-line scorer (Phase 1: EPL + UCL match markets).

Flow (all $0 data): Understat league pulls give played matches (the ratings fit; xg_variant "understat_shot_v1")
and the unplayed fixtures; ClubElo gives the anchor prior; the Dixon-Coles fit prices each fixture from ONE
scoreline matrix (soccer.game.matrix_pricer). The payload is written to EVSweep/soccer_games/predictions_latest.json,
the same {sport}_games shape the other engines serve through mlb.runners.public_api.get_game_projections, and
the per-book price board is refreshed alongside it (ev.game_odds_board.run("soccer")).

EVERY row is stamped validated: False. Soccer ships as unvalidated analysis until the walk-forward backtest
(soccer.game.backtest) against Pinnacle closing lines and live forward CLV clear the promotion bar; nothing
here feeds the Official Card.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone

from soccer.config import ACTIVE_SOCCER_LEAGUES, ENGINE_LABEL, ENGINE_NAME, PREDICTIONS_KEY, XG_VARIANT
from soccer.data import ingest
from soccer.game import dixon_coles
from soccer.game.matrix_pricer import price_match

log = logging.getLogger("soccer.run_daily")


def game_key(league_date: date, away: str, home: str) -> str:
    return f"soccer:{league_date.isoformat()}:{away}@{home}"


def build_predictions(fixtures: list[dict], ratings_by_league: dict[str, dixon_coles.Ratings],
                      elo: dict[str, float] | None = None, market_probs: dict | None = None,
                      now: datetime | None = None) -> dict:
    """Fixtures + fitted ratings -> the predictions payload. A fixture whose clubs are not both rated is
    skipped (fail closed). The Elo sanity guard runs when both the ClubElo prior and a market consensus
    (de-vigged three-way) are available for the game."""
    now = now or datetime.now(timezone.utc)
    priced: list[dict] = []
    games: list[dict] = []
    for fx in fixtures:
        ratings = ratings_by_league.get(fx["league"])
        if ratings is None:
            continue
        lam = ratings.expected_goals(fx["home"], fx["away"])
        if lam is None:
            log.warning("soccer run_daily: skipping %s vs %s (unrated club)", fx["home"], fx["away"])
            continue
        matrix, probs = price_match(lam[0], lam[1], ratings.rho)
        gk = game_key(fx["date"], fx["away"], fx["home"])
        p_model = (probs["p_home"], probs["p_draw"], probs["p_away"])
        warn = None
        if elo and fx["home"] in elo and fx["away"] in elo:
            p_elo = ingest.elo_probs(elo[fx["home"]], elo[fx["away"]])
            p_mkt = (market_probs or {}).get(gk)
            p_model, warn = dixon_coles.elo_sanity(p_model, p_elo, p_mkt)
            if warn:
                # the guard moved the 1X2: re-derive the double-chance legs from the guarded triple; the matrix
                # markets (totals / BTTS / handicaps) are not Elo-guarded and stay as priced.
                probs = {**probs, "p_home": p_model[0], "p_draw": p_model[1], "p_away": p_model[2],
                         "p_1x": p_model[0] + p_model[1], "p_x2": p_model[2] + p_model[1],
                         "p_12": p_model[0] + p_model[2]}
        commence = fx.get("datetime") or datetime(fx["date"].year, fx["date"].month, fx["date"].day,
                                                  tzinfo=timezone.utc)
        row = {
            "game_key": gk, "game_date": fx["date"].isoformat(), "league": fx["league"],
            "away": fx["away"], "home": fx["home"],
            "commence": commence.isoformat() if isinstance(commence, datetime) else str(commence),
            "lambda_home": round(lam[0], 4), "lambda_away": round(lam[1], 4), "rho": ratings.rho,
            "xg_variant": XG_VARIANT,
            "p_home": probs["p_home"], "p_draw": probs["p_draw"], "p_away": probs["p_away"],
            "p_1x": probs["p_1x"], "p_x2": probs["p_x2"], "p_12": probs["p_12"],
            "p_home_dnb": probs["p_home_dnb"], "p_away_dnb": probs["p_away_dnb"],
            "totals": probs["totals"], "p_btts_yes": probs["p_btts_yes"], "p_btts_no": probs["p_btts_no"],
            "handicaps": probs["handicaps"],
            "matrix": [[round(v, 5) for v in row_] for row_ in matrix],
            "validated": False,
        }
        if warn:
            row["sanity_warn"] = warn
        priced.append(row)
        games.append({"game_key": gk, "away": fx["away"], "home": fx["home"], "start": row["commence"],
                      "league": fx["league"]})
    priced.sort(key=lambda r: r["commence"])
    return {
        "sport": "soccer", "engine": ENGINE_NAME, "engine_version": 1, "label": ENGINE_LABEL,
        "xg_variant": XG_VARIANT, "validated": False,
        "ts": now.isoformat(), "status": "live" if priced else "awaiting_fixtures",
        "markets": ["ml", "total", "btts", "dc"],
        "count": len(priced), "rows": [], "priced_games": priced, "games": games,
    }


def _season_start_year(on: date) -> int:
    """European season start year for a date: Aug-Dec -> that year, Jan-Jul -> the previous year."""
    return on.year if on.month >= 8 else on.year - 1


def run_soccer_predictions(now: datetime | None = None, write: bool = False, fetch=None) -> dict:
    """Pull, fit and price the active leagues; optionally persist predictions + the odds board."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    season = _season_start_year(today)
    elo = {}
    try:
        elo = ingest.fetch_clubelo_ratings(today, fetch=fetch)
    except Exception:  # noqa: BLE001 -- the prior is an anchor, not a hard dependency
        log.warning("soccer run_daily: ClubElo pull failed; fitting without the Elo prior")
    ratings_by_league: dict[str, dixon_coles.Ratings] = {}
    fixtures: list[dict] = []
    for league in ACTIVE_SOCCER_LEAGUES:
        matches: list[dict] = []
        for yr in (season - 1, season):
            try:
                matches.extend(ingest.fetch_understat_matches(league, yr, fetch=fetch))
            except Exception:  # noqa: BLE001 -- one bad pull must not kill the other league
                log.exception("soccer run_daily: understat pull failed for %s %s", league, yr)
        played = [m for m in matches if m["is_result"]]
        try:
            ratings_by_league[league] = dixon_coles.fit_ratings(played, today, elo=elo or None)
        except ValueError:
            log.warning("soccer run_daily: no played matches to fit %s", league)
            continue
        fixtures.extend(m for m in matches
                        if not m["is_result"] and m["datetime"] >= now.replace(tzinfo=None))
    payload = build_predictions(fixtures, ratings_by_league, elo=elo or None, now=now)
    if write:
        try:
            from mlb_core import storage
            storage.write_bytes(json.dumps(payload, default=str).encode(), PREDICTIONS_KEY)
        except Exception:  # noqa: BLE001
            log.exception("soccer run_daily: predictions write failed")
        try:
            from ev import game_odds_board
            game_odds_board.run("soccer", now=now, write=True)
        except Exception:  # noqa: BLE001 -- the board is a second surface; never fail the scorer on it
            log.exception("soccer run_daily: odds board refresh failed")
    return payload


def read_cached_game_predictions() -> dict | None:
    """Latest stored payload, or None. NEVER recomputes (a recompute replays the whole fit), mirroring
    nhl.game.run_daily.read_cached_game_predictions for per-event callers like the Kalshi leg builder."""
    try:
        from mlb_core import storage
        if storage.exists(PREDICTIONS_KEY):
            return json.loads(storage.read_bytes(PREDICTIONS_KEY).decode("utf-8"))
    except Exception:  # noqa: BLE001
        log.exception("could not read cached soccer predictions at %s", PREDICTIONS_KEY)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Soccer Dixon-Coles game-line scorer (EPL + UCL, UNVALIDATED)")
    parser.add_argument("--write-gcs", action="store_true", help="Persist output to GCS/disk")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    payload = run_soccer_predictions(write=args.write_gcs)
    print(json.dumps({"status": payload["status"], "priced_games": payload["count"],
                      "validated": payload["validated"]}, indent=2))


if __name__ == "__main__":
    main()
