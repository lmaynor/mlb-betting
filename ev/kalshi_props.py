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
from ev.sports import ADAPTERS, NFL

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


# ------------------------------------------------------------------------------------------------ ticker-only parsing (RFQ legs)
# An RFQ combo leg is just a market_ticker; resolving it must not need Kalshi's REST catalog (403'd on Cloud Run egress, and dozens
# of calls). The grammar below was read from REAL tickers and probed against the public API on 2026-10-26:
#   <SERIES>-<YY><MON><DD><AWAY><HOME>-<TEAM><INITIAL><SURNAME LETTERS><JERSEY>-<N>
#   KXNBA3PT-26OCT25DETPHI-DETDROBINSON55-1      KXNBAAST-26OCT25LACOKC-OKCSGILGEOUSALEXANDER2-2
#   KXNFLRECYDS-26OCT25KCSEA-SEAJSMITHNJIGBA11-50   KXNHLPTS-26OCT26DETEDM-DETMBRANDSEGGNYGRD28-2   (Nygard -> NYGRD: non-ASCII dropped)
# Series NOT found on Kalshi (probed, do not exist): KXNHLSOG/SHOTS, KXNBAPR/PA/RA, KXNFLANYTD, KXNHLBLK. Add one with register_series().
KALSHI_PROP_SERIES_BY_SPORT = {
    "nfl": {"KXNFLPASSYDS": "pass_yds", "KXNFLRSHYDS": "rush_yds", "KXNFLRECYDS": "rec_yds", "KXNFLREC": "receptions",
            "KXNFLPASSTDS": "pass_tds", "KXNFLPASSINT": "interceptions", "KXNFLPASSATT": "pass_attempts", "KXNFLTD": "anytime_td"},
    "nba": {"KXNBAPTS": "points", "KXNBAREB": "rebounds", "KXNBAAST": "assists", "KXNBA3PT": "threes", "KXNBAPRA": "pra",
            "KXNBASTL": "steals", "KXNBABLK": "blocks"},
    "nhl": {"KXNHLGOAL": "goals", "KXNHLPTS": "points", "KXNHLAST": "assists", "KXNHLSAVE": "saves"},
}
_SERIES_INDEX: dict = {}
# stats whose only valid rung is "1+": Kalshi's N+ touchdowns ladder has no matching two-sided sportsbook market above 1
_ONLY_N1 = {("nfl", "anytime_td")}


def _rebuild_series_index() -> None:
    _SERIES_INDEX.clear()
    for sp, tbl in KALSHI_PROP_SERIES_BY_SPORT.items():
        for series, stat in tbl.items():
            _SERIES_INDEX[series] = (sp, stat)


_rebuild_series_index()


def register_series(sport: str, series: str, stat: str) -> None:
    """Add a prop series (e.g. after probing a new one on Kalshi). `stat` must be that sport's canonical ev.props stat name."""
    if sport not in ADAPTERS or not series.startswith(ADAPTERS[sport].kalshi_prefix):
        raise ValueError(f"series {series!r} does not belong to {sport!r}")
    KALSHI_PROP_SERIES_BY_SPORT.setdefault(sport, {})[series] = stat
    _rebuild_series_index()


@dataclass(frozen=True)
class ParsedPropTicker:
    ticker: str
    series: str
    sport: str
    game_key: str
    game_date: str
    team: str                    # the player's team (Kalshi code), exactly one of the event's two
    player_code: str             # lowercase letters of the ticker's player token, jersey digits stripped ('jtatum')
    jersey: str
    stat: str
    n: int                       # "N+"  ==  sportsbook Over (N - 0.5)
    strike: float


_PLAYER_TOKEN = re.compile(r"([A-Z]+)(\d*)")


def parse_prop_ticker(ticker: str):
    """Kalshi player-prop market_ticker -> ParsedPropTicker, or MapFail (never a guess)."""
    parts = (ticker or "").split("-")
    if len(parts) != 4:
        return MapFail("REJ_UNMAPPED", f"not a <series>-<event>-<player>-<n> ticker: {ticker!r}")
    series, tail, token, n_str = parts
    spec = _SERIES_INDEX.get(series)
    if spec is None:
        return MapFail("REJ_UNMAPPED", f"series {series!r} is not a registered prop series")
    sport, stat = spec
    parsed = parse_event(ADAPTERS[sport], f"{series}-{tail}")
    if isinstance(parsed, MapFail):
        return parsed
    game_date, away, home = parsed
    teams = [t for t in (away, home) if token.startswith(t)]
    if len(teams) != 1:
        return MapFail("REJ_AMBIGUOUS" if teams else "REJ_UNMAPPED", f"player token {token!r} does not start with exactly one of {away}/{home}")
    team = teams[0]
    m = _PLAYER_TOKEN.fullmatch(token[len(team):])
    if not m or len(m.group(1)) < 2 or not n_str.isdigit() or int(n_str) < 1:
        return MapFail("REJ_UNMAPPED", f"cannot parse player code / N from {ticker!r}")
    n = int(n_str)
    if (sport, stat) in _ONLY_N1 and n != 1:
        return MapFail("REJ_UNMAPPED", f"{series} only maps the 1+ rung (got {n}+)")
    return ParsedPropTicker(ticker=ticker, series=series, sport=sport, game_key=game_key(sport, game_date, away, home),
                            game_date=game_date, team=team, player_code=m.group(1).lower(), jersey=m.group(2), stat=stat,
                            n=n, strike=float(n) - 0.5)


_GENERATIONAL = {"jr", "sr", "ii", "iii", "iv", "v"}


def kalshi_player_codes(player_norm: str, display: str | None = None) -> set[str]:
    """Every plausible Kalshi player code (lowercase, jersey excluded) for a sportsbook player: first initial + surname letters.
    Variants: the accent-folded name (player_norm), the name with non-ASCII letters DROPPED (Kalshi: Nygard -> 'nygrd'), and each with the
    generational suffix appended when the display name carries one. Hyphens/apostrophes vanish ('gilgeousalexander')."""
    out: set[str] = set()
    names = [(player_norm or "").split()]
    if display:
        raw = re.sub(r"\([A-Za-z]{2,4}\)\s*$", "", display).replace("'", "").replace("\u2019", "").replace(".", "")
        toks = [re.sub(r"[^A-Za-z]", "", t) for t in re.split(r"[\s-]+", raw) if t]
        names.append([t.lower() for t in toks if t])
    for toks in names:
        suffix = [t for t in toks if t in _GENERATIONAL]
        core = [t for t in toks if t not in _GENERATIONAL]
        if len(core) < 2:
            continue
        base = core[0][0] + "".join(core[1:])
        out.add(base)
        if suffix:
            out.add(base + "".join(suffix))
    return {c for c in out if len(c) >= 2}


def match_player_code(team: str, player_code: str, candidates, displays: dict | None = None) -> str | None:
    """The ONE sportsbook player_norm among `candidates` (the players priced in this game / on `team`) whose Kalshi code equals
    `player_code`; None on zero OR several matches (two 'J. Williams' on a team must never be guessed between).
    `displays` ({player_norm: display name}) adds the non-ASCII-dropped variants."""
    code = re.sub(r"[^a-z]", "", (player_code or "").lower())
    if not code:
        return None
    hits = {pn for pn in candidates if code in kalshi_player_codes(pn, (displays or {}).get(pn))}
    return next(iter(hits)) if len(hits) == 1 else None
