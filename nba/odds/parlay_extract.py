"""Flatten ParlayAPI prop responses.

ParlayAPI prop outcomes differ from The Odds API: `name` is "Over <player>" /
"Under <player>" and `description` is the player name. Output rows use the SAME
schema as nba.odds.extract.flatten_player_props, so extract.best_book_props and
the game-line helpers are reused unchanged.
"""
from nba.config import PARLAY_PROP_MARKETS


# Some ParlayAPI markets arrive under more than one key for the same concept;
# collapse those to one short name so the data is not fragmented. Pitcher "outs
# recorded" comes back as both player_pitcher_outs and player_pitching_outs
# (verified against a live payload 2026-06-16).
_MARKET_ALIASES = {
    "player_pitcher_outs": "outs",
    "player_pitching_outs": "outs",
}


def _market_short(sport: str, key: str) -> str:
    """Map a ParlayAPI market key to a short name (e.g. player_points->points)."""
    if key in _MARKET_ALIASES:
        return _MARKET_ALIASES[key]
    if key.startswith("player_"):
        return key[len("player_"):]
    return key


def _side(outcome_name: str) -> str:
    n = (outcome_name or "").strip().lower()
    if n.startswith("over"):
        return "over"
    if n.startswith("under"):
        return "under"
    return ""


def flatten_parlay_props(event_obj: dict, sport: str = "") -> list:
    """Single-event ParlayAPI odds object -> one row per (book, player, market, line).

    Row schema matches nba.odds.extract.flatten_player_props:
      event_id, commence_time, event_date, home_team, away_team, player, market,
      line, over_odds, under_odds, book.
    """
    if not event_obj:
        return []
    eid = event_obj.get("id")
    home = event_obj.get("home_team")
    away = event_obj.get("away_team")
    commence = event_obj.get("commence_time") or ""
    rows = []
    for book in event_obj.get("bookmakers", []) or []:
        bkey = book.get("key", "")
        for market in book.get("markets", []) or []:
            mkey = market.get("key", "")
            if not mkey.startswith("player_"):
                continue
            short = _market_short(sport, mkey)
            by_player = {}
            for o in market.get("outcomes", []) or []:
                player = o.get("description")
                side = _side(o.get("name"))
                price = o.get("price")
                point = o.get("point")
                if not player or not side or price is None or point is None:
                    continue
                rec = by_player.setdefault((player, point),
                                           {"line": point, "over_odds": None, "under_odds": None})
                rec[f"{side}_odds"] = int(price)
            for (player, _ln), rec in by_player.items():
                if rec["over_odds"] is None or rec["under_odds"] is None:
                    continue
                rows.append({
                    "event_id": eid,
                    "commence_time": commence,
                    "event_date": commence[:10],
                    "home_team": home,
                    "away_team": away,
                    "player": player,
                    "market": short,
                    "line": rec["line"],
                    "over_odds": rec["over_odds"],
                    "under_odds": rec["under_odds"],
                    "book": bkey,
                })
    return rows
