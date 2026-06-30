"""
mlb_core.odds.parlay_adapter -- convert ParlayAPI payloads into SGO snapshot shape.

ParlayAPI (parlay-api.com, The-Odds-API shape) is the primary MLB odds provider.
Rather than rewrite the 9 runners + sgo.py extractors, this adapter synthesizes
the exact SGO event/odds/byBookmaker structure they already consume, so the live
path is untouched. `merge_events` then splices SGO-sourced inning markets
(NRFI/1I-3way/F5/F5-ML/F1H -- which ParlayAPI cannot express) into the same
per-game event, giving per-market provider fallback.

Pure transforms only (no network, no I/O) so it is unit-testable offline; the
side effects it relies on -- team-name -> abbr (dk_scraper.resolve_team) and
(date, teams)/(name, team) -> ids (mlb_core.data.id_resolver) -- are cached and
mockable.

Each event's game date is derived from its own commence_time (ET), so a slate
that spans today + tomorrow (late-night pulls, when next-day lines post ~9pm ET)
resolves every game's game_pk correctly. Merge keys on eventID (== game_pk), so
same-matchup-on-consecutive-days does not collide.

Coverage (ParlayAPI -> SGO oddID): player_home_runs (yes/no) -> HR; player_
strikeouts/outs/hits/total_bases/earned_runs (ou) -> K/OUTS/HITS/TB/ER; h2h ->
GAME ml. Books: every US book (denylist via sgo.OFFSHORE_BOOKS); offshore dropped.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from mlb_core.data import id_resolver
from mlb_core.odds.dk_scraper import resolve_team
from mlb_core.odds.sgo import BOOK_CANONICAL, OFFSHORE_BOOKS

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")

# ParlayAPI player-prop market key -> (sgo oddID prefix, statID, kind).
# kind: "hr_yn" (one yn-yes entry from the over/yes side) | "ou" (over+under).
PROP_MARKET_MAP = {
    "player_home_runs":    ("batting_homeRuns", "batting_homeRuns", "hr_yn"),
    "player_strikeouts":   ("pitching_strikeouts", "pitching_strikeouts", "ou"),
    "player_outs":         ("pitching_outs", "pitching_outs", "ou"),   # live key
    "player_pitcher_outs": ("pitching_outs", "pitching_outs", "ou"),   # alias
    "player_pitching_outs": ("pitching_outs", "pitching_outs", "ou"),  # alias
    "player_hits":         ("batting_hits", "batting_hits", "ou"),
    "player_total_bases":  ("batting_totalBases", "batting_totalBases", "ou"),
    "player_earned_runs":  ("pitching_earnedRuns", "pitching_earnedRuns", "ou"),
}

# SGO-only inning-market oddID prefixes spliced from SGO during merge.
_SGO_INNING_PREFIXES = ("points-all-1i-", "points-away-1i-", "points-home-1i-",
                        "points-all-1ix5-", "points-home-1ix5-", "points-away-1ix5-",
                        "points-home-1h-", "points-away-1h-")


def _canon_book(key: str) -> str | None:
    """Canonical book name for a ParlayAPI book key, or None if offshore (every
    US book qualifies; only sgo.OFFSHORE_BOOKS is dropped)."""
    k = (key or "").lower()
    if not k or k in OFFSHORE_BOOKS:
        return None
    return BOOK_CANONICAL.get(k, k)


def _abbr(team_full: str) -> str:
    return resolve_team(team_full) if team_full else ""


def _side_of(outcome_name: str) -> str:
    """O/U props -> 'Over/Under <player>'; yes/no props (home runs) -> 'Yes'/'No'.
    Map yes->over / no->under so the hr_yn synthesizer picks the yes side."""
    n = (outcome_name or "").strip().lower()
    if n.startswith("over") or n.startswith("yes"):
        return "over"
    if n.startswith("under") or n.startswith("no"):
        return "under"
    return ""


def _event_et_date(commence_time: str, fallback: str) -> str:
    """ET game date (YYYY-MM-DD) from an ISO commence_time; fallback if missing."""
    if not commence_time:
        return fallback
    try:
        s = str(commence_time).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.astimezone(_ET).date().isoformat()
    except Exception:  # noqa: BLE001
        return fallback


def _resolve_pid(name: str, away_abbr: str, home_abbr: str, date: str, game_pk=None):
    return (id_resolver.resolve_player_id(name, away_abbr, date, game_pk)
            or id_resolver.resolve_player_id(name, home_abbr, date, game_pk))


# ---------------------------------------------------------------------------
# Game moneyline (h2h) + player props -> SGO entries
# ---------------------------------------------------------------------------

def _game_ml_odds(game_lines_event: dict, home_full: str, away_full: str) -> dict:
    home_books, away_books = {}, {}
    for book in game_lines_event.get("bookmakers") or []:
        bk = _canon_book(book.get("key", ""))
        if not bk:
            continue
        for market in book.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            for o in market.get("outcomes") or []:
                price = o.get("price")
                if price is None:
                    continue
                if o.get("name") == home_full:
                    home_books[bk] = {"odds": str(int(price)), "available": True}
                elif o.get("name") == away_full:
                    away_books[bk] = {"odds": str(int(price)), "available": True}
    out = {}
    if home_books:
        out["points-home-game-ml-home"] = {
            "oddID": "points-home-game-ml-home", "statID": "points",
            "betTypeID": "ml", "sideID": "home", "byBookmaker": home_books}
    if away_books:
        out["points-away-game-ml-away"] = {
            "oddID": "points-away-game-ml-away", "statID": "points",
            "betTypeID": "ml", "sideID": "away", "byBookmaker": away_books}
    return out


def _props_odds(props_event: dict, away_abbr: str, home_abbr: str,
                date: str, game_pk=None) -> tuple[dict, dict]:
    acc: dict = {}
    for book in (props_event or {}).get("bookmakers") or []:
        bk = _canon_book(book.get("key", ""))
        if not bk:
            continue
        for market in book.get("markets") or []:
            mkey = market.get("key", "")
            if mkey not in PROP_MARKET_MAP:
                continue
            for o in market.get("outcomes") or []:
                player = o.get("description")
                side = _side_of(o.get("name"))
                price, point = o.get("price"), o.get("point")
                if not player or not side or price is None or point is None:
                    continue
                if not id_resolver.is_player_name(player):   # drop template/matchup junk
                    continue
                slot = acc.setdefault((mkey, player), {"over": {}, "under": {}})
                slot[side][bk] = {"odds": str(int(price)), "overUnder": str(point)}

    odds, players = {}, {}
    for (mkey, player), sides in acc.items():
        prefix, stat_id, kind = PROP_MARKET_MAP[mkey]
        pid = _resolve_pid(player, away_abbr, home_abbr, date, game_pk)
        if not pid:
            logger.info("parlay_adapter: unresolved player %r (%s)", player, mkey)
            continue
        pid = str(pid)
        players[pid] = {"name": player}
        if kind == "hr_yn":
            over = sides["over"]
            if not over:
                continue
            oid = f"{prefix}-{pid}-game-yn-yes"
            odds[oid] = {"oddID": oid, "statID": stat_id, "betTypeID": "yn",
                         "sideID": "yes", "playerID": pid, "statEntityID": pid,
                         "byBookmaker": {b: {"odds": v["odds"], "available": True}
                                         for b, v in over.items()}}
        else:
            for side in ("over", "under"):
                bp = sides[side]
                if not bp:
                    continue
                oid = f"{prefix}-{pid}-game-ou-{side}"
                odds[oid] = {"oddID": oid, "statID": stat_id, "betTypeID": "ou",
                             "sideID": side, "playerID": pid, "statEntityID": pid,
                             "byBookmaker": {b: {"odds": v["odds"], "available": True,
                                                 "overUnder": v["overUnder"]}
                                             for b, v in bp.items()}}
    return odds, players


def parlay_to_sgo_event(game_lines_event: dict, props_event: dict | None,
                        default_date: str) -> dict | None:
    """One ParlayAPI game (+ its props) -> one SGO-shaped event, or None if the
    game_pk can't be resolved. Game date comes from the event's own commence_time
    (ET); default_date is only a fallback."""
    home_full = game_lines_event.get("home_team") or ""
    away_full = game_lines_event.get("away_team") or ""
    home_abbr, away_abbr = _abbr(home_full), _abbr(away_full)
    if not home_abbr or not away_abbr:
        logger.info("parlay_adapter: unresolved team(s) %r/%r", away_full, home_full)
        return None
    commence = game_lines_event.get("commence_time", "")
    game_date = _event_et_date(commence, default_date)
    game_pk = id_resolver.resolve_game_pk(game_date, away_abbr, home_abbr)
    if not game_pk:
        logger.info("parlay_adapter: no game_pk for %s@%s %s", away_abbr, home_abbr, game_date)
        return None

    odds = _game_ml_odds(game_lines_event, home_full, away_full)
    p_odds, players = _props_odds(props_event, away_abbr, home_abbr, game_date, game_pk)
    odds.update(p_odds)

    return {
        "eventID": str(game_pk),
        "status": {"startsAt": commence},
        "teams": {
            "away": {"names": {"medium": away_full, "long": away_full, "short": away_abbr}},
            "home": {"names": {"medium": home_full, "long": home_full, "short": home_abbr}},
        },
        "players": players,
        "odds": odds,
    }


def parlay_slate_to_sgo_events(game_lines: list, props_by_event_id: dict,
                               default_date: str) -> list:
    out = []
    for ev in game_lines or []:
        adapted = parlay_to_sgo_event(ev, props_by_event_id.get(ev.get("id")), default_date)
        if adapted:
            out.append(adapted)
    return out


def merge_events(parlay_events: list, sgo_events: list) -> list:
    """Merge per game keyed by eventID (== game_pk for both providers).

    - In both: ParlayAPI event is the base; splice SGO inning oddIDs into it.
    - ParlayAPI-only: kept (covered markets; inning runners skip).
    - SGO-only: kept whole as fallback (its eventID is already the game_pk).
    """
    sgo_by_id = {ev.get("eventID"): ev for ev in (sgo_events or []) if ev.get("eventID")}
    merged = []
    for pev in parlay_events or []:
        sev = sgo_by_id.pop(pev.get("eventID"), None)
        if sev:
            inning = {oid: e for oid, e in (sev.get("odds") or {}).items()
                      if any(oid.startswith(p) for p in _SGO_INNING_PREFIXES)}
            pev.setdefault("odds", {}).update(inning)
        merged.append(pev)
    merged.extend(sgo_by_id.values())   # SGO-only fallback games
    return merged


def inning_odds_only(events: list) -> dict:
    """Map eventID -> {oddID: entry} keeping only SGO inning markets. Used to
    carry inning markets forward across ParlayAPI-only snapshots (when SGO is
    not re-fetched) so inning runners never see an empty book."""
    out = {}
    for ev in events or []:
        eid = ev.get("eventID")
        inning = {oid: e for oid, e in (ev.get("odds") or {}).items()
                  if any(oid.startswith(p) for p in _SGO_INNING_PREFIXES)}
        if eid and inning:
            out[eid] = inning
    return out
