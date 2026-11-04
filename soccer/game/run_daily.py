"""soccer.game.run_daily -- the daily soccer game-line scorer (Phase 1: EPL + UCL match markets).

Flow (all $0 data):
  1. The odds board is built FIRST (ev.game_odds_board.run("soccer")): its 3-way de-vigged consensus is the
     market reference the Elo sanity guard checks against, so it must exist before pricing, not after.
  2. Understat DOMESTIC league pulls (EPL, La Liga, Bundesliga, Serie A, Ligue 1 -- Understat has no UCL page)
     give played matches (xg_variant "understat_shot_v1") and the domestic fixtures. A domestic league's own
     ratings fit on its own matches; UCL ratings are ONE pooled fit across all five domestic leagues,
     ClubElo-anchored, extended by the ClubElo fallback for UCL clubs the domestic sample never covered.
  3. Fixtures are the Understat unplayed schedule UNION the banked ParlayAPI slate (OddsAccum per league key):
     any match the books have posted is priced, with its real commence_time.
  4. The Dixon-Coles fit prices each fixture from ONE scoreline matrix (soccer.game.matrix_pricer).

The payload is written to EVSweep/soccer_games/predictions_latest.json, the same {sport}_games shape the other
engines serve through mlb.runners.public_api.get_game_projections. EVERY row is stamped validated: False.
Soccer ships as unvalidated analysis until the walk-forward backtest (soccer.game.backtest) against Pinnacle
closing lines and live forward CLV clear the promotion bar; nothing here feeds the Official Card.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from soccer.config import (ACTIVE_SOCCER_LEAGUES, DOMESTIC_UNDERSTAT_LEAGUES, ENGINE_LABEL, ENGINE_NAME,
                           FALLBACK_ENGINE_LABEL, FALLBACK_ENGINE_NAME, FALLBACK_XG_VARIANT, PREDICTIONS_KEY, SOCCER_LEAGUES, UCL_HFA_SCALE, XG_VARIANT)
from soccer.data import ingest
from soccer.data.reep_register import league_codes
from soccer.game import dixon_coles
from soccer.game.matrix_pricer import price_match

log = logging.getLogger("soccer.run_daily")

_ET = ZoneInfo("America/New_York")


def game_key(league_date: date, away: str, home: str) -> str:
    return f"soccer:{league_date.isoformat()}:{away}@{home}"


def build_predictions(fixtures: list[dict], ratings_by_league: dict[str, dixon_coles.Ratings],
                      elo: dict[str, float] | None = None, market_probs: dict | None = None,
                      now: datetime | None = None, xg_variant: str = XG_VARIANT) -> dict:
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
                # the guard moved the 1X2: re-derive the double-chance AND draw-no-bet legs from the guarded
                # triple; the matrix markets (totals / BTTS / handicaps) are not Elo-guarded and stay as priced.
                den = p_model[0] + p_model[2]
                probs = {**probs, "p_home": p_model[0], "p_draw": p_model[1], "p_away": p_model[2],
                         "p_1x": p_model[0] + p_model[1], "p_x2": p_model[2] + p_model[1],
                         "p_12": p_model[0] + p_model[2],
                         "p_home_dnb": p_model[0] / den if den > 0 else probs["p_home_dnb"],
                         "p_away_dnb": p_model[2] / den if den > 0 else probs["p_away_dnb"]}
        commence = fx.get("datetime") or datetime(fx["date"].year, fx["date"].month, fx["date"].day,
                                                  tzinfo=timezone.utc)
        row = {
            "game_key": gk, "game_date": fx["date"].isoformat(), "league": fx["league"],
            "away": fx["away"], "home": fx["home"],
            "commence": commence.isoformat() if isinstance(commence, datetime) else str(commence),
            "lambda_home": round(lam[0], 4), "lambda_away": round(lam[1], 4), "rho": ratings.rho,
            "xg_variant": xg_variant,
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
        "xg_variant": xg_variant, "validated": False,
        "ts": now.isoformat(), "status": "live" if priced else "awaiting_fixtures",
        "markets": ["ml", "total", "btts", "dc"],
        "count": len(priced), "rows": [], "priced_games": priced, "games": games,
    }


def _season_start_year(on: date) -> int:
    """European season start year for a date: Aug-Dec -> that year, Jan-Jul -> the previous year."""
    return on.year if on.month >= 8 else on.year - 1


def _refresh_board(now: datetime, write: bool) -> dict | None:
    """Build the soccer odds board FIRST (see the module docstring): build_predictions needs its 3-way market
    consensus for the Elo sanity guard. Guarded: a board failure never fails the scorer -- the guard simply
    has no market reference this run and cannot trip, exactly as when a game has no board row."""
    try:
        from ev import game_odds_board
        return game_odds_board.run("soccer", now=now, write=write)
    except Exception:  # noqa: BLE001
        log.exception("soccer run_daily: odds board refresh failed")
        return None


def _market_consensus(board: dict | None) -> dict[str, tuple[float, float, float]]:
    """game_key -> the 3-way power-de-vigged market consensus (f_home, f_draw, f_away) from the board's ml
    rows: the mean of each book's own de-vigged triple, renormalized. A book missing a leg cannot de-vig a
    three-way market and is skipped; a game with no complete book quote gets no entry."""
    from mlb_core.odds.utils import american_to_implied_prob, devig_three_way
    out: dict[str, tuple[float, float, float]] = {}
    for row in (board or {}).get("rows") or []:
        if row.get("market") != "ml":
            continue
        triples = []
        for b in row.get("books") or []:
            if b.get("a") is None or b.get("b") is None or b.get("draw") is None:
                continue
            try:
                triples.append(devig_three_way(american_to_implied_prob(b["b"]),
                                               american_to_implied_prob(b["draw"]),
                                               american_to_implied_prob(b["a"]), method="power"))
            except ValueError:
                continue
        if triples:
            mean = tuple(sum(t[i] for t in triples) / len(triples) for i in range(3))
            s = sum(mean)
            out[row["game_key"]] = tuple(v / s for v in mean)
    return out


def _banked_fixtures(now: datetime) -> list[dict]:
    """Upcoming fixtures from the banked ParlayAPI game-line slates (OddsAccum, one snapshot per league key),
    in the same fixture shape as the Understat unplayed rows. The posted slate is the ground truth for what
    will actually be priced: every book-posted match appears here with its real commence_time, including UCL
    fixtures (Understat publishes no UCL schedule at all). Club names resolve through the Reep bridge with the
    board's own resolver order (fail closed: an unresolved club drops the fixture, never a guessed join)."""
    from nba.game import odds_feed
    from soccer.data.reep_register import resolve_club
    out: list[dict] = []
    for league in ACTIVE_SOCCER_LEAGUES:
        try:
            events, _meta = odds_feed.read_banked_game_lines(SOCCER_LEAGUES[league]["parlay_key"], now)
        except Exception:  # noqa: BLE001 -- one league's snapshot must not kill the other's fixtures
            log.exception("soccer run_daily: banked slate read failed for %s", league)
            continue
        for ev in events or []:
            try:
                start = datetime.fromisoformat(str(ev.get("commence_time") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if start <= now:
                continue
            home = resolve_club(str(ev.get("home_team", "")), provider="parlayapi") \
                or resolve_club(str(ev.get("home_team", "")))
            away = resolve_club(str(ev.get("away_team", "")), provider="parlayapi") \
                or resolve_club(str(ev.get("away_team", "")))
            if not home or not away:
                continue
            out.append({"league": league, "date": start.astimezone(_ET).date(), "datetime": start,
                        "home": home, "away": away})
    return out


def _pooled_ratings(league: str, pool_played: list[dict], today: date,
                    elo: dict[str, float]) -> dixon_coles.Ratings | None:
    """Ratings for a league with no Understat page of its own (UCL): ONE Dixon-Coles fit across the played
    matches of all five domestic leagues (a club's attack/defense is earned domestically; ClubElo's single
    cross-European scale anchors the pool so the ratings are comparable across leagues), extended by the
    ClubElo fallback for league clubs the domestic sample never covered, with home advantage scaled down for
    cross-league fixtures (soccer.config.UCL_HFA_SCALE). When the pool itself is empty (every domestic pull
    failed), the Elo-prior-only ratings are the last resort; with neither, the league is unrated this run."""
    codes = league_codes(league)
    ratings: dixon_coles.Ratings | None = None
    try:
        ratings = dixon_coles.fit_ratings(pool_played, today, elo=elo or None)
    except ValueError:
        try:
            ratings = dixon_coles.elo_only_ratings(codes, elo or None, today)
        except ValueError:
            log.warning("soccer run_daily: no pool matches and no ClubElo coverage to rate %s", league)
            return None
    ratings = dixon_coles.ratings_with_elo_fallback(ratings, codes, elo or None)
    ratings.hfa_scale = UCL_HFA_SCALE
    return ratings


def _football_data_fallback(league: str, season: int, fetch=None) -> list[dict]:
    """A domestic league's played matches from football-data.co.uk (two seasons), shaped like the Understat rows the fit reads. No xG:
    effective_goals then uses the shots-on-target proxy (or plain goals). Any failure returns what was loaded; never raises."""
    out: list[dict] = []
    for yr in (season - 1, season):
        try:
            for m in ingest.fetch_football_data_csv(league, yr, fetch=fetch):
                out.append({**m, "is_result": True, "datetime": datetime(m["date"].year, m["date"].month, m["date"].day, 12),
                            "xg_home": None, "xg_away": None})
        except Exception:  # noqa: BLE001 -- a missing season file must not take the other leagues down
            log.warning("soccer run_daily: football-data fallback pull failed for %s %s", league, yr, exc_info=True)
    return out


def run_soccer_predictions(now: datetime | None = None, write: bool = False, fetch=None) -> dict:
    """Pull, fit and price the active leagues; optionally persist predictions + the odds board."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    season = _season_start_year(today)
    board = _refresh_board(now, write)
    market_probs = _market_consensus(board)
    elo = {}
    try:
        elo = ingest.fetch_clubelo_ratings(today, fetch=fetch)
    except Exception:  # noqa: BLE001 -- the prior is an anchor, not a hard dependency
        log.warning("soccer run_daily: ClubElo pull failed; fitting without the Elo prior")
    # Domestic Understat pulls: the active domestic leagues' own fits AND the cross-European pool behind UCL.
    domestic: dict[str, list[dict]] = {}
    understat_down: list[str] = []
    fallback_leagues: list[str] = []
    for league in DOMESTIC_UNDERSTAT_LEAGUES:
        matches: list[dict] = []
        for yr in (season - 1, season):
            try:
                matches.extend(ingest.fetch_understat_matches(league, yr, fetch=fetch))
            except ingest.UnderstatUnavailable as exc:
                understat_down.append(str(exc))
                log.warning("soccer run_daily: understat unavailable for %s %s: %s", league, yr, exc)
            except Exception:  # noqa: BLE001 -- one bad pull must not kill the other leagues or the pool
                log.exception("soccer run_daily: understat pull failed for %s %s", league, yr)
        if not matches and understat_down:
            matches = _football_data_fallback(league, season, fetch=fetch)
            if matches:
                fallback_leagues.append(league)
        domestic[league] = matches
    pool_played = [m for ms in domestic.values() for m in ms if m["is_result"]]
    ratings_by_league: dict[str, dixon_coles.Ratings] = {}
    for league in ACTIVE_SOCCER_LEAGUES:
        if SOCCER_LEAGUES[league]["understat_league"] is not None:
            played = [m for m in domestic.get(league, []) if m["is_result"]]
            try:
                ratings_by_league[league] = dixon_coles.fit_ratings(played, today, elo=elo or None)
            except ValueError:
                log.warning("soccer run_daily: no played matches to fit %s", league)
        else:
            pooled = _pooled_ratings(league, pool_played, today, elo)
            if pooled is not None:
                ratings_by_league[league] = pooled
    # Fixtures: the Understat unplayed schedule UNION the banked odds slate, deduped on (league, date, home, away).
    fixtures: list[dict] = []
    seen: set[tuple] = set()

    def _add_fixture(fx: dict) -> None:
        key = (fx["league"], fx["date"], fx["home"], fx["away"])
        if key not in seen:
            seen.add(key)
            fixtures.append(fx)

    for league in ACTIVE_SOCCER_LEAGUES:
        if SOCCER_LEAGUES[league]["understat_league"] is None:
            continue
        for m in domestic.get(league, []):
            if not m["is_result"] and m["datetime"] >= now.replace(tzinfo=None):
                _add_fixture(m)
    for fx in _banked_fixtures(now):
        _add_fixture(fx)
    variant = XG_VARIANT
    if fallback_leagues:
        variant = FALLBACK_XG_VARIANT if len(fallback_leagues) == len(DOMESTIC_UNDERSTAT_LEAGUES) else f"{XG_VARIANT}+{FALLBACK_XG_VARIANT}"
    payload = build_predictions(fixtures, ratings_by_league, elo=elo or None,
                                market_probs=market_probs, now=now, xg_variant=variant)
    if fallback_leagues:
        payload["engine"], payload["label"] = FALLBACK_ENGINE_NAME, FALLBACK_ENGINE_LABEL
        payload["fallback"] = {"source": "football-data.co.uk", "leagues": fallback_leagues,
                               "reason": f"Understat is not serving match data ({understat_down[0]})"}
    if not payload["priced_games"] and not ratings_by_league and understat_down:
        # No ratings could be fitted because Understat serves no match data: say so, not "awaiting_fixtures" (the fixtures exist).
        payload["status"] = "awaiting_data"
        payload["reason"] = f"Understat is not serving match data ({understat_down[0]}); no team ratings can be fitted"
    if write:
        try:
            from mlb_core import storage
            storage.write_bytes(json.dumps(payload, default=str).encode(), PREDICTIONS_KEY)
        except Exception:  # noqa: BLE001
            log.exception("soccer run_daily: predictions write failed")
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
