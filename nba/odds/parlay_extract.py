"""Flatten ParlayAPI prop responses.

ParlayAPI prop outcomes differ from The Odds API: `name` is "Over <player>" /
"Under <player>" and `description` is the player name. Output rows use the SAME
schema as nba.odds.extract.flatten_player_props, so extract.best_book_props and
the game-line helpers are reused unchanged.
"""
import re

from nba.config import PARLAY_PROP_MARKETS

# Some books append the player's team to the description: "Alex Ovechkin (WSH)" (bovada, live 2026-10-14).
_TEAM_TAIL = re.compile(r"\s*\(([A-Za-z]{2,4})\)\s*$")


def split_team_tail(raw: str):
    """'Alex Ovechkin (WSH)' -> ('Alex Ovechkin', 'WSH'); no suffix -> (raw, None)."""
    raw = (raw or "").strip()
    m = _TEAM_TAIL.search(raw)
    return (raw[: m.start()].strip(), m.group(1).upper()) if m else (raw, None)


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
    if n.startswith("over") or n.startswith("yes"):   # yes/no props (MLB home runs)
        return "over"
    if n.startswith("under") or n.startswith("no"):
        return "under"
    return ""


def _is_team_row(desc: str, home: str, away: str) -> bool:
    """True when a 'player' prop outcome is really a TEAM line.

    ParlayAPI's NHL player_goals market carries team-total rows (name "Over Washington Capitals",
    description "Washington Capitals", point 6.5) mixed in with the skater rows (verified live 2026-10-14).
    Without this filter a team total lands in the player table as a fake 6.5-goal skater."""
    d = (desc or "").strip().lower()
    return bool(d) and d in {(home or "").strip().lower(), (away or "").strip().lower()}


def flatten_parlay_props(event_obj: dict, sport: str = "", include_one_sided: bool = False) -> list:
    """Single-event ParlayAPI odds object -> one row per (book, player, market, line).

    Row schema matches nba.odds.extract.flatten_player_props:
      event_id, commence_time, event_date, home_team, away_team, player, market,
      line, over_odds, under_odds, book.

    include_one_sided=False (default, MLB behaviour): a quote needs BOTH sides to become a row.
    include_one_sided=True: a one-sided quote is kept with the missing side None. NHL anytime-goal / points
    markets are "Yes"-only prices at point 0 (live 2026-10-14), so the hockey accumulator needs this.
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
                player, team_hint = split_team_tail(o.get("description"))
                side = _side(o.get("name"))
                price = o.get("price")
                point = o.get("point")
                point_raw = point
                # NHL anytime markets: a one-sided "Yes" arrives with point 0.5, 0.0 or None depending on the book
                # -- all mean "1 or more". Normalize to 0.5 (include_one_sided mode only, so MLB's home_runs
                # Yes/No @0.5 path is untouched); `point_raw` keeps what the book actually sent.
                if (include_one_sided and point in (None, 0.0) and side == "over"
                        and (o.get("name") or "").strip().lower() == "yes"):
                    point = 0.5
                if not player or not side or price is None or point is None:
                    continue
                # skip non-player junk some books emit (templates, matchup strings)
                if "{" in player or "}" in player or " @ " in player:
                    continue
                if _is_team_row(player, home, away):
                    continue
                rec = by_player.setdefault((player, point),
                                           {"line": point, "over_odds": None, "under_odds": None,
                                            "team_hint": team_hint, "point_raw": point_raw})
                rec[f"{side}_odds"] = int(price)
            for (player, _ln), rec in by_player.items():
                two_sided = rec["over_odds"] is not None and rec["under_odds"] is not None
                if not two_sided and not include_one_sided:
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
                    "two_sided": two_sided,
                    "team_hint": rec["team_hint"],
                    "point_raw": rec["point_raw"],
                })
    return rows
