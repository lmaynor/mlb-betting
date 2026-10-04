"""
mlb.execution.kalshi.mapping -- Kalshi market -> proposition key (STRICT).

A wrong mapping is a wrong BET, not a wrong paper row. This repo has already shipped this bug
class (HR Under graded as 1+ HR wins; nrfi YES/NO vs YRFI/NRFI silently inverted/zero-matched).
So this reuses the SAME parsing as the capture that produced the signal
(mlb_core.odds.kalshi.parse_event_ticker / market_outcome, kalshi_to_history.SERIES_MAP
semantics) and then ASSERTS the market's own title/strike text agrees with the interpretation.
Anything that does not match exactly is a MapFail, never a guess.

v1 allowlist (design doc s1): KXMLBGAME (moneyline), KXMLBRFI (NRFI/YRFI), KXMLBTOTAL (totals).
NOT mapped: KXMLBSPREAD -- Kalshi's "SD wins by over 1.5" is NOT the books' "SD +1.5" (they are
different propositions that the existing kalshi_vs_books join would key identically); needs a
dedicated, verified mapping before it is enabled. Player props: v2 (thin Kalshi depth).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mlb_core.odds import kalshi
from mlb.execution.kalshi.price_grid import parse_price_ranges

# series -> (canonical odds_history market, kind)
SERIES = {
    "KXMLBGAME": ("game_ml", "ml"),
    "KXMLBRFI": ("nrfi_ou", "rfi"),
    "KXMLBTOTAL": ("game_total", "over"),
}


@dataclass(frozen=True)
class Leg:
    series: str
    ticker: str
    event_ticker: str
    market: str                   # odds_history market
    game_pk: int
    game_date: str
    away: str
    home: str
    selection_yes: str            # what BUYING YES on this Kalshi market bets on
    selection_no: str             # what BUYING NO bets on
    line: float | None
    start_utc: datetime           # scheduled first pitch
    ranges: tuple                 # price grid
    exchange_index: int | None
    title: str


@dataclass(frozen=True)
class MapFail:
    reason: str                   # REJ_UNMAPPED | REJ_AMBIGUOUS | REJ_RULES_DIFFER
    detail: str = ""


def map_market(series: str, mk: dict, resolve_game_pk) -> Leg | MapFail:
    spec = SERIES.get(series)
    if spec is None:
        return MapFail("REJ_UNMAPPED", f"series {series} not enabled")
    market, kind = spec

    event = mk.get("event_ticker") or ""
    tail = event.split("-", 1)[1] if "-" in event else ""
    m = kalshi._EVENT_RE.match(tail)
    if m and m.group(6):                                   # trailing G<n> = doubleheader game n
        return MapFail("REJ_AMBIGUOUS", f"doubleheader suffix {m.group(6)}")
    game_date, away, home = kalshi.parse_event_ticker(event)
    if not game_date or not away or not home:
        return MapFail("REJ_UNMAPPED", f"cannot parse event ticker {event!r}")
    start = kalshi.event_start_utc(event)
    if start is None:
        return MapFail("REJ_UNMAPPED", f"cannot parse start time {event!r}")
    game_pk = resolve_game_pk(game_date, away, home)
    if game_pk is None:
        return MapFail("REJ_UNMAPPED", f"no game_pk for {game_date} {away}@{home}")

    title = (mk.get("title") or "").strip()
    sel_yes = sel_no = None
    line = None

    if kind == "ml":
        team = kalshi.market_outcome(mk)
        sub = (mk.get("yes_sub_title") or "").strip()
        if not title.endswith(" wins") or not sub or sub not in (mk.get("rules_primary") or ""):
            return MapFail("REJ_RULES_DIFFER", f"unexpected ML wording {title!r}")
        if team == home:
            sel_yes, sel_no = "HOME", "AWAY"
        elif team == away:
            sel_yes, sel_no = "AWAY", "HOME"
        else:
            return MapFail("REJ_UNMAPPED", f"outcome {team!r} not in {away}@{home}")
    elif kind == "rfi":
        if "1st inning" not in title or "0.5" not in title or "Over" not in title:
            return MapFail("REJ_RULES_DIFFER", f"unexpected RFI wording {title!r}")
        sel_yes, sel_no = "YRFI", "NRFI"            # yes = a run scores in the 1st
    elif kind == "over":
        try:
            line = float(mk.get("floor_strike"))
        except (TypeError, ValueError):
            return MapFail("REJ_UNMAPPED", "no floor_strike")
        if mk.get("strike_type") != "greater" or not title.startswith("Over ") or f"{line:g}" not in title:
            return MapFail("REJ_RULES_DIFFER", f"unexpected total wording {title!r} / {mk.get('strike_type')!r}")
        sel_yes, sel_no = "OVER", "UNDER"

    ex = mk.get("exchange_index")
    return Leg(
        series=series, ticker=mk["ticker"], event_ticker=event, market=market, game_pk=int(game_pk),
        game_date=game_date, away=away, home=home, selection_yes=sel_yes, selection_no=sel_no,
        line=line, start_utc=start, ranges=tuple(parse_price_ranges(mk.get("price_ranges"))),
        exchange_index=int(ex) if ex is not None else None, title=title,
    )
