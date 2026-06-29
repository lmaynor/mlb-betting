"""
mlb_core.odds.parlay_adapter -- convert ParlayAPI payloads into SGO snapshot shape.

ParlayAPI (parlay-api.com, The-Odds-API shape) is the primary MLB odds provider.
Rather than rewrite the 9 runners + sgo.py extractors, this adapter synthesizes
the exact SGO event/odds/byBookmaker structure they already consume, so the live
path is untouched. `merge_events` then splices SGO-sourced inning markets
(NRFI/1I-3way/F5/F5-ML/F1H -- which ParlayAPI cannot express) into the same
per-game event, giving per-market provider fallback.

Pure transforms only (no network, no I/O) so it is unit-testable offline; the
two side effects it relies on -- team-name -> abbr (dk_scraper.resolve_team) and
(date, teams)/(name, team) -> ids (mlb_core.data.id_resolver) -- are cached and
themselves mockable.

Coverage (ParlayAPI -> SGO oddID):
  player_home_runs   -> batting_homeRuns-{MLBAM}-game-yn-yes      (HR)
  player_strikeouts  -> pitching_strikeouts-{MLBAM}-game-ou-{o,u} (K)
  player_pitcher_outs-> pitching_outs-{MLBAM}-game-ou-{o,u}       (OUTS)
  player_hits        -> batting_hits-{MLBAM}-game-ou-{o,u}        (BATTER_HITS)
  player_total_bases -> batting_totalBases-{MLBAM}-game-ou-{o,u}  (BATTER_TB)
  player_earned_runs -> pitching_earnedRuns-{MLBAM}-game-ou-{o,u} (PITCHER_ER)
  h2h                -> points-{home,away}-game-ml-{home,away}    (GAME)
SGO-only (kept via merge, never synthesized here): 1i / 1ix5 / 1h markets.
"""

from __future__ import annotations

import logging

from mlb_core.data import id_resolver
from mlb_core.odds.dk_scraper import resolve_team

logger = logging.getLogger(__name__)

# ParlayAPI player-prop market key -> (sgo oddID prefix, statID, kind).
# kind: "hr_yn" (one yn-yes entry from the over side) | "ou" (over+under entries).
PROP_MARKET_MAP = {
    "player_home_runs":    ("batting_homeRuns", "batting_homeRuns", "hr_yn"),
    "player_strikeouts":   ("pitching_strikeouts", "pitching_strikeouts", "ou"),
    "player_pitcher_outs": ("pitching_outs", "pitching_outs", "ou"),
    "player_pitching_outs": ("pitching_outs", "pitching_outs", "ou"),  # alias (verified live)
    "player_hits":         ("batting_hits", "batting_hits", "ou"),
    "player_total_bases":  ("batting_totalBases", "batting_totalBases", "ou"),
    "player_earned_runs":  ("pitching_earnedRuns", "pitching_earnedRuns", "ou"),
}

# ParlayAPI / The-Odds-API book key -> SGO onshore book key (must land in
# mlb_core.odds.sgo.ONSHORE_BOOKS; others are dropped by the extractors anyway).
BOOK_MAP = {
    "draftkings": "draftkings",
    "fanduel": "fanduel",
    "betmgm": "betmgm",
    "caesars": "caesars",
    "williamhill_us": "caesars",   # Caesars (William Hill US) alias
    "espnbet": "espnbet",          # extractor canonicalizes espnbet -> thescore
    "thescore": "thescore",
    "pointsbet": "pointsbet",
    "pointsbetus": "pointsbet",
}

# SGO-only inning-market oddID prefixes spliced from SGO during merge.
_SGO_INNING_PREFIXES = ("points-all-1i-", "points-away-1i-", "points-home-1i-",
                        "points-all-1ix5-", "points-home-1ix5-", "points-away-1ix5-",
                        "points-home-1h-", "points-away-1h-")


def _abbr(team_full: str) -> str:
    return resolve_team(team_full) if team_full else ""


def _side_of(outcome_name: str) -> str:
    n = (outcome_name or "").strip().lower()
    if n.startswith("over"):
        return "over"
    if n.startswith("under"):
        return "under"
    return ""


def _resolve_pid(name: str, away_abbr: str, home_abbr: str, run_date: str):
    """Try the player's team both ways, then unique-name fallback (inside resolver)."""
    return (id_resolver.resolve_player_id(name, away_abbr, run_date)
            or id_resolver.resolve_player_id(name, home_abbr, run_date))


# ---------------------------------------------------------------------------
# Game moneyline (h2h) -> SGO game-ml entries
# ---------------------------------------------------------------------------

def _game_ml_odds(game_lines_event: dict, home_full: str, away_full: str) -> dict:
    home_books, away_books = {}, {}
    for book in game_lines_event.get("bookmakers") or []:
        sgo_book = BOOK_MAP.get(book.get("key", ""))
        if not sgo_book:
            continue
        for market in book.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            for o in market.get("outcomes") or []:
                price = o.get("price")
                if price is None:
                    continue
                if o.get("name") == home_full:
                    home_books[sgo_book] = {"odds": str(int(price)), "available": True}
                elif o.get("name") == away_full:
                    away_books[sgo_book] = {"odds": str(int(price)), "available": True}
    out = {}
    if home_books:
        out["points-home-game-ml-home"] = {
            "oddID": "points-home-game-ml-home", "statID": "points",
            "betTypeID": "ml", "sideID": "home", "byBookmaker": home_books,
        }
    if away_books:
        out["points-away-game-ml-away"] = {
            "oddID": "points-away-game-ml-away", "statID": "points",
            "betTypeID": "ml", "sideID": "away", "byBookmaker": away_books,
        }
    return out


# ---------------------------------------------------------------------------
# Player props -> SGO ou / hr-yn entries
# ---------------------------------------------------------------------------

def _props_odds(props_event: dict, away_abbr: str, home_abbr: str,
                run_date: str) -> tuple[dict, dict]:
    """Return (odds_entries, players) for a single ParlayAPI props event object."""
    # acc[(market_key, player)][side][sgo_book] = {"odds", "overUnder"}
    acc: dict = {}
    for book in (props_event or {}).get("bookmakers") or []:
        sgo_book = BOOK_MAP.get(book.get("key", ""))
        if not sgo_book:
            continue
        for market in book.get("markets") or []:
            mkey = market.get("key", "")
            if mkey not in PROP_MARKET_MAP:
                continue
            for o in market.get("outcomes") or []:
                player = o.get("description")
                side = _side_of(o.get("name"))
                price = o.get("price")
                point = o.get("point")
                if not player or not side or price is None or point is None:
                    continue
                slot = acc.setdefault((mkey, player), {"over": {}, "under": {}})
                slot[side][sgo_book] = {"odds": str(int(price)),
                                        "overUnder": str(point)}

    odds: dict = {}
    players: dict = {}
    for (mkey, player), sides in acc.items():
        prefix, stat_id, kind = PROP_MARKET_MAP[mkey]
        pid = _resolve_pid(player, away_abbr, home_abbr, run_date)
        if not pid:
            logger.info("parlay_adapter: unresolved player %r (%s)", player, mkey)
            continue
        pid = str(pid)
        players[pid] = {"name": player}

        if kind == "hr_yn":
            over = sides["over"]
            if not over:
                continue
            by_book = {b: {"odds": v["odds"], "available": True} for b, v in over.items()}
            oid = f"{prefix}-{pid}-game-yn-yes"
            odds[oid] = {"oddID": oid, "statID": stat_id, "betTypeID": "yn",
                         "sideID": "yes", "playerID": pid, "statEntityID": pid,
                         "byBookmaker": by_book}
        else:  # ou
            for side in ("over", "under"):
                book_prices = sides[side]
                if not book_prices:
                    continue
                by_book = {b: {"odds": v["odds"], "available": True,
                               "overUnder": v["overUnder"]}
                          for b, v in book_prices.items()}
                oid = f"{prefix}-{pid}-game-ou-{side}"
                odds[oid] = {"oddID": oid, "statID": stat_id, "betTypeID": "ou",
                             "sideID": side, "playerID": pid, "statEntityID": pid,
                             "byBookmaker": by_book}
    return odds, players


# ---------------------------------------------------------------------------
# Event synthesis + merge
# ---------------------------------------------------------------------------

def parlay_to_sgo_event(game_lines_event: dict, props_event: dict | None,
                        run_date: str) -> dict | None:
    """One ParlayAPI game (+ its props) -> one SGO-shaped event, or None if the
    game_pk can't be resolved (unjoinable -> drop rather than emit)."""
    home_full = game_lines_event.get("home_team") or ""
    away_full = game_lines_event.get("away_team") or ""
    home_abbr, away_abbr = _abbr(home_full), _abbr(away_full)
    if not home_abbr or not away_abbr:
        logger.info("parlay_adapter: unresolved team(s) %r/%r", away_full, home_full)
        return None
    game_pk = id_resolver.resolve_game_pk(run_date, away_abbr, home_abbr)
    if not game_pk:
        logger.info("parlay_adapter: no game_pk for %s@%s %s", away_abbr, home_abbr, run_date)
        return None

    odds = _game_ml_odds(game_lines_event, home_full, away_full)
    p_odds, players = _props_odds(props_event, away_abbr, home_abbr, run_date)
    odds.update(p_odds)

    return {
        "eventID": str(game_pk),
        "status": {"startsAt": game_lines_event.get("commence_time", "")},
        "teams": {
            "away": {"names": {"medium": away_full, "long": away_full, "short": away_abbr}},
            "home": {"names": {"medium": home_full, "long": home_full, "short": home_abbr}},
        },
        "players": players,
        "odds": odds,
        "_teams_abbr": (away_abbr, home_abbr),   # internal, for merge keying
    }


def parlay_slate_to_sgo_events(game_lines: list, props_by_event_id: dict,
                               run_date: str) -> list:
    """Adapt a full ParlayAPI slate. props_by_event_id maps ParlayAPI event id ->
    its get_event_props object."""
    out = []
    for ev in game_lines or []:
        adapted = parlay_to_sgo_event(ev, props_by_event_id.get(ev.get("id")), run_date)
        if adapted:
            out.append(adapted)
    return out


def _sgo_key(event: dict, run_date: str) -> tuple | None:
    """Key an SGO event by (away_abbr, home_abbr) via resolve_team on medium names."""
    teams = event.get("teams") or {}
    away = resolve_team((teams.get("away") or {}).get("names", {}).get("medium", ""))
    home = resolve_team((teams.get("home") or {}).get("names", {}).get("medium", ""))
    if not away or not home:
        return None
    return (away, home)


def merge_events(parlay_events: list, sgo_events: list, run_date: str) -> list:
    """Merge per game keyed by (away_abbr, home_abbr).

    - In both: ParlayAPI event is the base; splice SGO inning oddIDs into it.
    - ParlayAPI-only: kept (covered markets; inning runners find nothing -> skip).
    - SGO-only: kept as fallback, with eventID re-stamped to the resolved game_pk
      so batter-prop runners (which match int(event_id)==int(game_pk)) still join.
    """
    sgo_by_key: dict = {}
    for ev in sgo_events or []:
        k = _sgo_key(ev, run_date)
        if k:
            sgo_by_key[k] = ev

    merged = []
    seen_keys = set()
    for pev in parlay_events or []:
        k = pev.pop("_teams_abbr", None) or _sgo_key(pev, run_date)
        seen_keys.add(k)
        sev = sgo_by_key.get(k)
        if sev:
            inning = {oid: e for oid, e in (sev.get("odds") or {}).items()
                      if any(oid.startswith(p) for p in _SGO_INNING_PREFIXES)}
            pev.setdefault("odds", {}).update(inning)
        merged.append(pev)

    # SGO-only games: keep whole as fallback, re-stamp eventID to game_pk.
    for k, sev in sgo_by_key.items():
        if k in seen_keys:
            continue
        away_abbr, home_abbr = k
        gp = id_resolver.resolve_game_pk(run_date, away_abbr, home_abbr)
        if gp:
            sev = dict(sev)
            sev["eventID"] = str(gp)
        merged.append(sev)

    return merged
