"""
ev.kalshi_props -- Kalshi NFL player-prop markets -> proposition (STRICT, fail closed).

Grammar (verified live 2026-10-04):
  event   KXNFL<STAT>-<YY><MON><DD><AWAY><HOME>
  market  <event>-<TEAM><PLAYERCODE>-<N>      e.g. KXNFLRECYDS-26OCT04INDWAS-INDTWARREN84-50
  title   "<Player Name>: <N>+ <unit>"        strike_type greater, floor_strike N-0.5
  rules   "If <name> records <N>+ <unit> in the <A> vs <B> (Pro Football|professional football) game
           originally scheduled for <Mon D, YYYY>, then the market resolves to Yes."
  secondary: "...active but never takes a snap, the market settles to the fair market price..." (a void)

YES == the book's OVER at point N-0.5; NO == UNDER. Anything that does not match exactly is a MapFail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from mlb.execution.kalshi.mapping import MapFail
from mlb.execution.kalshi.price_grid import parse_price_ranges
from ev.kalshi_map import _MON_NAMES, _SCHEDULED, game_key, parse_event
from ev.props import norm_name
from ev.sports import NFL

# series -> (canonical stat, unit wording in title/rules)
PROP_SERIES = {
    "KXNFLPASSYDS": ("pass_yds", "passing yards"),
    "KXNFLRSHYDS": ("rush_yds", "rushing yards"),
    "KXNFLRECYDS": ("rec_yds", "receiving yards"),
    "KXNFLREC": ("receptions", "receptions"),
    "KXNFLPASSTDS": ("pass_tds", "passing touchdowns"),
}
_LEAGUE = re.compile(r"(?:Pro Football|professional football) game originally scheduled for")


@dataclass(frozen=True)
class PropLeg:
    series: str
    ticker: str
    event_ticker: str
    stat: str
    player: str
    player_norm: str
    team: str                    # player's team code (from the ticker)
    n: int                       # "N+"
    strike: float                # N - 0.5 == the book's over point
    game_key: str
    game_date: str
    away: str
    home: str
    ranges: tuple
    exchange_index: int | None
    title: str
    # ---- executor-compat fields (gates.evaluate / intent_id read these off a "leg") ----
    start_utc: datetime | None = None          # filled from the book event's commence_time
    selection_yes: str = "OVER"
    selection_no: str = "UNDER"

    @property
    def market(self) -> str:                   # unique per player+stat so intent ids never collide
        return f"nfl_{self.stat}:{self.player_norm}"

    @property
    def game_pk(self) -> str:
        return self.game_key

    @property
    def line(self) -> float:
        return self.strike

    def with_start(self, start_utc: datetime) -> "PropLeg":
        from dataclasses import replace
        return replace(self, start_utc=start_utc)


def map_prop_market(mk: dict):
    """One Kalshi NFL prop market dict -> PropLeg | MapFail."""
    event = mk.get("event_ticker") or ""
    series = event.split("-", 1)[0]
    spec = PROP_SERIES.get(series)
    if spec is None:
        return MapFail("REJ_UNMAPPED", f"series {series!r} not an enabled NFL prop series")
    stat, unit = spec
    parsed = parse_event(NFL, event)
    if isinstance(parsed, MapFail):
        return parsed
    game_date, away, home = parsed

    if mk.get("market_type") != "binary" or mk.get("strike_type") != "greater":
        return MapFail("REJ_RULES_DIFFER", f"market_type/strike_type {mk.get('market_type')!r}/{mk.get('strike_type')!r}")
    try:
        strike = float(mk.get("floor_strike"))
    except (TypeError, ValueError):
        return MapFail("REJ_UNMAPPED", "no floor_strike")
    n = int(round(strike + 0.5))
    if abs(strike - (n - 0.5)) > 1e-9 or n < 1:
        return MapFail("REJ_RULES_DIFFER", f"floor_strike {strike} is not an N-0.5 half point")

    title = (mk.get("title") or "").strip()
    m = re.fullmatch(rf"(.+): (\d+)\+ {re.escape(unit)}", title)
    if not m or int(m.group(2)) != n:
        return MapFail("REJ_RULES_DIFFER", f"unexpected prop title {title!r}")
    player = m.group(1).strip()
    if (mk.get("yes_sub_title") or "").strip() != f"{player}: {n}+":
        return MapFail("REJ_RULES_DIFFER", f"yes_sub_title {mk.get('yes_sub_title')!r}")

    ticker = mk.get("ticker") or ""
    tail = ticker[len(event) + 1:] if ticker.startswith(event + "-") else ""
    pm = re.fullmatch(r"([A-Z]+\d*)-(\d+)", tail) if tail else None
    if not pm or int(pm.group(2)) != n:
        return MapFail("REJ_RULES_DIFFER", f"ticker {ticker!r} strike suffix != title N={n}")
    code = pm.group(1)
    team = next((t for t in (away, home) if code.startswith(t)), None)
    if team is None:
        return MapFail("REJ_UNMAPPED", f"player code {code!r} does not start with {away} or {home}")

    primary = mk.get("rules_primary") or ""
    if not primary.startswith(f"If {player} records {n}+ {unit} in the ") or not _LEAGUE.search(primary):
        return MapFail("REJ_RULES_DIFFER", "rules_primary wording")
    sched = _SCHEDULED.search(primary)
    if not sched:
        return MapFail("REJ_RULES_DIFFER", "no scheduled date in rules")
    mon, day, year = sched.groups()
    if f"{int(year)}-{_MON_NAMES.get(mon, 0):02d}-{int(day):02d}" != game_date:
        return MapFail("REJ_RULES_DIFFER", f"rules date != ticker date {game_date}")
    if "never takes a snap" not in (mk.get("rules_secondary") or ""):
        return MapFail("REJ_RULES_DIFFER", "rules_secondary lacks the DNP/never-takes-a-snap void clause")

    ex = mk.get("exchange_index")
    return PropLeg(
        series=series, ticker=ticker, event_ticker=event, stat=stat, player=player, player_norm=norm_name(player),
        team=team, n=n, strike=strike, game_key=game_key("nfl", game_date, away, home), game_date=game_date,
        away=away, home=home, ranges=tuple(parse_price_ranges(mk.get("price_ranges"))),
        exchange_index=int(ex) if ex is not None else None, title=title)
