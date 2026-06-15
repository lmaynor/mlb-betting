"""Flatten The Odds API NBA responses into tabular rows + multi-book best-odds.

Player-prop parsing is refit from the nba-parlay-generator reference
(_parse_odds_response). best_book_props collapses per-book rows to one row per
(event, player, market, line) with the best price each side -- the same
multi-book pattern as MLB's _best_book_odds_int.

All odds are American ints. No network here -- pure transforms (unit-testable).
"""
from nba.config import ODDS_PROP_MARKETS


def _commence_date(commence_time: str) -> str:
    return (commence_time or "")[:10]


def flatten_player_props(odds_obj: dict) -> list:
    """Single-event odds object -> one row per (book, player, market, line).

    Row: event_id, commence_time, event_date, home_team, away_team, player,
         market, line, over_odds, under_odds, book.
    Skips outcomes missing a price, point, or the opposite side.
    """
    if not odds_obj:
        return []
    event_id = odds_obj.get("id")
    home = odds_obj.get("home_team")
    away = odds_obj.get("away_team")
    commence = odds_obj.get("commence_time")
    rows = []
    for book in odds_obj.get("bookmakers", []) or []:
        book_key = book.get("key", "")
        for market in book.get("markets", []) or []:
            market_name = ODDS_PROP_MARKETS.get(market.get("key", ""))
            if not market_name:
                continue
            by_player = {}
            for o in market.get("outcomes", []) or []:
                player = o.get("description")
                side = o.get("name")          # "Over" / "Under"
                price = o.get("price")
                point = o.get("point")
                if not player or price is None or point is None:
                    continue
                rec = by_player.setdefault((player, point),
                                           {"line": point, "over_odds": None, "under_odds": None})
                if side == "Over":
                    rec["over_odds"] = int(price)
                elif side == "Under":
                    rec["under_odds"] = int(price)
            for (player, _line), rec in by_player.items():
                if rec["over_odds"] is None or rec["under_odds"] is None:
                    continue
                rows.append({
                    "event_id": event_id,
                    "commence_time": commence,
                    "event_date": _commence_date(commence),
                    "home_team": home,
                    "away_team": away,
                    "player": player,
                    "market": market_name,
                    "line": rec["line"],
                    "over_odds": rec["over_odds"],
                    "under_odds": rec["under_odds"],
                    "book": book_key,
                })
    return rows


def _best(rows, field):
    """Best (highest) American odds across rows for one side, with its book.
    Higher American odds = better payout for the bettor on either sign."""
    best_val, best_book = None, None
    for r in rows:
        v = r.get(field)
        if v is None:
            continue
        if best_val is None or v > best_val:
            best_val, best_book = v, r["book"]
    return best_val, best_book


def best_book_props(rows: list) -> list:
    """Collapse per-book prop rows to one row per (event, player, market, line)
    with best_over / best_under and the book offering each."""
    groups = {}
    for r in rows:
        key = (r["event_id"], r["player"], r["market"], r["line"])
        groups.setdefault(key, []).append(r)
    out = []
    for (event_id, player, market, line), grp in groups.items():
        bo, bob = _best(grp, "over_odds")
        bu, bub = _best(grp, "under_odds")
        ref = grp[0]
        out.append({
            "event_id": event_id,
            "event_date": ref["event_date"],
            "home_team": ref["home_team"],
            "away_team": ref["away_team"],
            "player": player,
            "market": market,
            "line": line,
            "best_over": bo,
            "best_over_book": bob,
            "best_under": bu,
            "best_under_book": bub,
            "n_books": len(grp),
        })
    return out


def flatten_game_lines(events: list) -> list:
    """/odds slate list -> one row per (event, book, market_outcome).

    Row: event_id, commence_time, event_date, home_team, away_team, book,
         market (h2h/spreads/totals), outcome (team name or Over/Under),
         price, point (None for h2h).
    """
    rows = []
    for ev in events or []:
        event_id = ev.get("id")
        home = ev.get("home_team")
        away = ev.get("away_team")
        commence = ev.get("commence_time")
        for book in ev.get("bookmakers", []) or []:
            book_key = book.get("key", "")
            for market in book.get("markets", []) or []:
                mkey = market.get("key", "")
                for o in market.get("outcomes", []) or []:
                    if o.get("price") is None:
                        continue
                    rows.append({
                        "event_id": event_id,
                        "commence_time": commence,
                        "event_date": _commence_date(commence),
                        "home_team": home,
                        "away_team": away,
                        "book": book_key,
                        "market": mkey,
                        "outcome": o.get("name"),
                        "price": int(o["price"]),
                        "point": o.get("point"),
                    })
    return rows
