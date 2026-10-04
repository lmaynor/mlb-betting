"""
ev.kalshi_map -- Kalshi market -> proposition (STRICT, fail closed), for NFL / NBA / NHL.

A wrong mapping is a wrong BET. This repo has shipped that bug class repeatedly (HR Under graded as
1+ HR, nrfi YES/NO inverted, run-line "+N" vs "wins by over N"). So every mapping here is a lookup
PLUS independent assertions that the market's own text, ticker, and strike agree; any disagreement is
a MapFail, never a guess. Identity comes from the ticker's team codes against the league's complete
code table, never from titles (titles are city-only: "Los Angeles wins" is ambiguous in all 3 leagues).

Grammar (verified live 2026-10-04):
  event  KX<LG><KIND>-<YY><MON><DD><AWAY><HOME>        date in ET, NO start-time group
  GAME   market ticker ...-<CODE>         title "<Name> wins"
  SPREAD market ticker ...-<CODE><N>      "<Name> wins by over <N-0.5> points|goals"  (strike ladder)
  TOTAL  market ticker ...-<N>            "Full Game: Over <N-0.5> points|goals scored" (strike ladder)

Canonical selections follow the odds_history convention (each side carries ITS OWN line):
  GAME   yes -> ('HOME'|'AWAY', None)
  SPREAD yes -> (side, -L)  ("X wins by over L" == book X -L), no -> (other side, +L)
  TOTAL  yes -> ('OVER', L), no -> ('UNDER', L)
Half-point lines only (a whole-number line has a push the Kalshi contract does not).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mlb.execution.kalshi.mapping import MapFail
from mlb.execution.kalshi.price_grid import parse_price_ranges
from ev.sports.base import SportAdapter

_MONTHS = {m: i for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), start=1)}
_MON_NAMES = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
              "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
_EVENT_TAIL = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$")
_SCHEDULED = re.compile(r"originally scheduled for ([A-Z][a-z]{2}) (\d{1,2}), (\d{4})")
_SPREAD_TITLE = r"wins by (?:more than|over) {line} {unit}"


@dataclass(frozen=True)
class Leg:
    sport: str
    kind: str                    # 'ml' | 'spread' | 'total'
    market: str                  # canonical odds_history-style market
    series: str
    ticker: str
    event_ticker: str
    game_key: str                # '{sport}:{YYYY-MM-DD ET}:{AWAY}@{HOME}'
    game_date: str
    away: str
    home: str
    yes_sel: tuple               # (selection, line) bought by buying YES
    no_sel: tuple                # (selection, line) bought by buying NO
    line: float | None           # the strike (positive), None for ml
    ranges: tuple                # price grid
    exchange_index: int | None
    title: str


_MARKET = {"ml": "game_ml", "spread": "game_spread", "total": "game_total"}


def game_key(sport: str, game_date: str, away: str, home: str) -> str:
    return f"{sport}:{game_date}:{away}@{home}"


def split_teams(blob: str, codes) -> tuple[str, str] | None:
    """'LAVGK' -> ('LA','VGK') iff EXACTLY ONE (away, home) split uses known codes."""
    hits = [(blob[:i], blob[i:]) for i in range(1, len(blob))
            if blob[:i] in codes and blob[i:] in codes and blob[:i] != blob[i:]]
    return hits[0] if len(hits) == 1 else None


def parse_event(adapter: SportAdapter, event_ticker: str):
    """event ticker -> (game_date 'YYYY-MM-DD', away, home) or MapFail."""
    if not event_ticker or "-" not in event_ticker:
        return MapFail("REJ_UNMAPPED", f"bad event ticker {event_ticker!r}")
    prefix, tail = event_ticker.split("-", 1)
    if not prefix.startswith(adapter.kalshi_prefix):
        return MapFail("REJ_UNMAPPED", f"{prefix!r} is not a {adapter.sport} series")
    m = _EVENT_TAIL.match(tail)
    if not m:
        return MapFail("REJ_UNMAPPED", f"cannot parse event tail {tail!r}")
    yy, mon, dd, blob = m.groups()
    month = _MONTHS.get(mon)
    if not month:
        return MapFail("REJ_UNMAPPED", f"bad month in {tail!r}")
    teams = split_teams(blob, adapter.codes)
    if teams is None:
        return MapFail("REJ_AMBIGUOUS", f"team codes {blob!r} unknown or ambiguous for {adapter.sport}")
    return f"20{yy}-{month:02d}-{dd}", teams[0], teams[1]


def _check_rules(adapter: SportAdapter, mk: dict, game_date: str):
    """Common rules assertions. Returns None if OK else MapFail."""
    primary = mk.get("rules_primary") or ""
    if mk.get("market_type") != "binary":
        return MapFail("REJ_RULES_DIFFER", f"market_type {mk.get('market_type')!r}")
    if f"{adapter.league_phrase} game originally scheduled for" not in primary:
        return MapFail("REJ_RULES_DIFFER", f"rules do not name a {adapter.league_phrase} game")
    sched = _SCHEDULED.search(primary)
    if not sched:
        return MapFail("REJ_RULES_DIFFER", "no 'originally scheduled for' date in rules")
    mon, day, year = sched.groups()
    if f"{int(year)}-{_MON_NAMES.get(mon, 0):02d}-{int(day):02d}" != game_date:
        return MapFail("REJ_RULES_DIFFER", f"rules date {mon} {day} {year} != ticker date {game_date}")
    return None


def _half_point(mk: dict):
    try:
        line = float(mk.get("floor_strike"))
    except (TypeError, ValueError):
        return None
    return line if abs(line * 2 - round(line * 2)) < 1e-9 and abs(line - round(line)) > 1e-9 else None


def map_market(adapter: SportAdapter, kind: str, mk: dict):
    """One Kalshi market dict -> Leg, or MapFail (REJ_UNMAPPED / REJ_AMBIGUOUS / REJ_RULES_DIFFER)."""
    if kind not in _MARKET:
        return MapFail("REJ_UNMAPPED", f"unknown kind {kind!r}")
    if kind in adapter.disabled:
        return MapFail("REJ_RULES_DIFFER", f"{adapter.sport} {kind} disabled: {adapter.disabled[kind]}")

    event = mk.get("event_ticker") or ""
    if not event.startswith(adapter.series(kind) + "-"):
        return MapFail("REJ_UNMAPPED", f"event {event!r} is not series {adapter.series(kind)}")
    parsed = parse_event(adapter, event)
    if isinstance(parsed, MapFail):
        return parsed
    game_date, away, home = parsed

    bad = _check_rules(adapter, mk, game_date)
    if bad:
        return bad

    ticker = mk.get("ticker") or ""
    suffix = ticker[len(event) + 1:] if ticker.startswith(event + "-") else ""
    if not suffix:
        return MapFail("REJ_UNMAPPED", f"ticker {ticker!r} does not extend event {event!r}")
    title = (mk.get("title") or "").strip()
    sub = (mk.get("yes_sub_title") or "").strip()
    primary = mk.get("rules_primary") or ""
    unit = adapter.unit
    line = None

    if kind == "ml":
        code = suffix
        if code not in (away, home):
            return MapFail("REJ_UNMAPPED", f"outcome code {code!r} not in {away}@{home}")
        if not sub or title != f"{sub} wins" or not primary.startswith(f"If {sub} wins the "):
            return MapFail("REJ_RULES_DIFFER", f"unexpected ML wording {title!r}")
        if adapter.tie_policy == "half" and "resolve to $0.50 for each team" not in (mk.get("rules_secondary") or ""):
            return MapFail("REJ_RULES_DIFFER", "tie handling not the expected $0.50-per-team")
        yes_side = "HOME" if code == home else "AWAY"
        yes_sel, no_sel = (yes_side, None), ("AWAY" if yes_side == "HOME" else "HOME", None)

    elif kind == "spread":
        line = _half_point(mk)
        m = re.match(r"^([A-Z]+)(\d+)$", suffix)
        if line is None or mk.get("strike_type") != "greater" or not m:
            return MapFail("REJ_RULES_DIFFER", f"unexpected spread shape {suffix!r} strike={mk.get('floor_strike')!r}")
        code, num = m.group(1), int(m.group(2))
        if code not in (away, home):
            return MapFail("REJ_UNMAPPED", f"spread team {code!r} not in {away}@{home}")
        if num != int(line + 0.5):
            return MapFail("REJ_RULES_DIFFER", f"ticker strike {num} != floor_strike {line}")
        pat = re.compile(r"^.+ " + _SPREAD_TITLE.format(line=re.escape(f"{line:g}"), unit=unit) + r"\??$")
        if not pat.match(title) or not re.search(
                _SPREAD_TITLE.format(line=re.escape(f"{line:g}"), unit=unit), primary):
            return MapFail("REJ_RULES_DIFFER", f"unexpected spread wording {title!r}")
        yes_side = "HOME" if code == home else "AWAY"
        no_side = "AWAY" if yes_side == "HOME" else "HOME"
        yes_sel, no_sel = (yes_side, -line), (no_side, +line)

    else:  # total
        line = _half_point(mk)
        if line is None or mk.get("strike_type") != "greater" or not re.fullmatch(r"\d+", suffix):
            return MapFail("REJ_RULES_DIFFER", f"unexpected total shape {suffix!r} strike={mk.get('floor_strike')!r}")
        if int(suffix) != int(line + 0.5):
            return MapFail("REJ_RULES_DIFFER", f"ticker strike {suffix} != floor_strike {line}")
        pat = re.compile(rf"^Full Game: [Oo]ver {re.escape(f'{line:g}')} {unit} scored\??$")
        # rules wording differs by league: NFL "more than 68.5 points", NBA "over 257.5 total points"
        rules_pat = rf"(?:more than|over) {re.escape(f'{line:g}')} (?:total )?{unit}"
        if not pat.match(title) or not re.search(rules_pat, primary):
            return MapFail("REJ_RULES_DIFFER", f"unexpected total wording {title!r}")
        yes_sel, no_sel = ("OVER", line), ("UNDER", line)

    ex = mk.get("exchange_index")
    return Leg(
        sport=adapter.sport, kind=kind, market=_MARKET[kind], series=adapter.series(kind), ticker=ticker,
        event_ticker=event, game_key=game_key(adapter.sport, game_date, away, home), game_date=game_date,
        away=away, home=home, yes_sel=yes_sel, no_sel=no_sel, line=line,
        ranges=tuple(parse_price_ranges(mk.get("price_ranges"))),
        exchange_index=int(ex) if ex is not None else None, title=title,
    )
