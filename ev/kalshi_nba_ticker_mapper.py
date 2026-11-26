"""Fail-closed Kalshi NBA player-prop ticker & market mapper.

RUL-20261125 Half 2 / MAK-01, shadow-only.
Maps ONLY KXNBAREB (rebounds), KXNBA3PT (threes), and KXNBAPTS (points)
N+ contracts to exact half-point consensus lines (N - 0.5) and canonical
player identities. Any ambiguity or disagreement raises ValueError.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from ev.kalshi_props import kalshi_player_codes
from ev.props import norm_name
from ev.sports.nba import CODES as NBA_TEAM_CODES

SERIES_TO_STAT: dict[str, str] = {
    "KXNBAREB": "rebounds",
    "KXNBA3PT": "threes",
    "KXNBAPTS": "points",
}

_MONTHS: dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_EVENT_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")
_PARTICIPANT_RE = re.compile(r"^[A-Z]{2,}[0-9]*$")
_THRESHOLD_RE = re.compile(r"^\d+$")
_TITLE_RE = re.compile(r"^(.+?):\s*(\d+)\+\s*(points|rebounds|threes)\s*$")


@dataclass(frozen=True)
class MappedMarket:
    """Validated, fail-closed Kalshi NBA prop contract mapping."""

    ticker: str
    series: str
    stat: str
    event_date: date
    away: str
    home: str
    team: str
    participant_key: str
    player_name: str
    player_norm: str
    player_id: Any
    threshold_n: int
    consensus_line: float

    @property
    def identity_key(self) -> str:
        """Canonical identity key: resolved player_id or fallback participant_key."""
        if self.player_id is not None:
            return str(self.player_id)
        return self.participant_key


def _participant_matches_name(participant_key: str, player_name: str, player_norm: str) -> bool:
    code = re.sub(r"\d+$", "", participant_key).lower()
    if not code:
        return False
    codes = kalshi_player_codes(player_norm, display=player_name)
    if code in codes:
        return True
    # Single-word name fallback (rare, e.g., Nene)
    collapsed = re.sub(r"[^a-z]", "", player_norm)
    return bool(collapsed) and code == collapsed


def parse_ticker(
    ticker: str,
    market: dict[str, Any] | None = None,
    resolver: Callable[[str, str], Any] | None = None,
) -> MappedMarket:
    """Parse and validate a Kalshi NBA prop ticker (and optional market dict)."""
    if not isinstance(ticker, str) or not ticker:
        raise ValueError(f"Invalid empty ticker: {ticker!r}")
    parts = ticker.split("-")
    if len(parts) != 4:
        raise ValueError(f"Expected 4 ticker segments, got {ticker!r}")

    series, event_seg, player_seg, thresh_seg = parts
    stat = SERIES_TO_STAT.get(series)
    if stat is None:
        raise ValueError(f"Ineligible or unknown NBA prop series: {series!r}")

    em = _EVENT_RE.fullmatch(event_seg)
    if em is None:
        raise ValueError(f"Invalid event segment {event_seg!r} in {ticker!r}")
    yy_s, mon_s, dd_s, away, home = em.groups()
    month = _MONTHS.get(mon_s)
    if month is None:
        raise ValueError(f"Invalid month {mon_s!r} in {ticker!r}")
    try:
        event_date = date(2000 + int(yy_s), month, int(dd_s))
    except ValueError as exc:
        raise ValueError(f"Invalid calendar date in {ticker!r}: {exc}") from exc

    if away not in NBA_TEAM_CODES or home not in NBA_TEAM_CODES or away == home:
        raise ValueError(f"Invalid away/home teams ({away!r}, {home!r}) in {ticker!r}")

    if len(player_seg) < 5:
        raise ValueError(f"Invalid player segment {player_seg!r} in {ticker!r}")
    team = player_seg[:3]
    participant_key = player_seg[3:]
    if team not in (away, home):
        raise ValueError(f"Player team {team!r} not in game ({away}, {home}) in {ticker!r}")
    if _PARTICIPANT_RE.fullmatch(participant_key) is None:
        raise ValueError(f"Invalid participant_key {participant_key!r} in {ticker!r}")

    if _THRESHOLD_RE.fullmatch(thresh_seg) is None:
        raise ValueError(f"Invalid threshold segment {thresh_seg!r} in {ticker!r}")
    threshold_n = int(thresh_seg)
    if threshold_n < 1:
        raise ValueError(f"Threshold N must be >= 1, got {threshold_n} in {ticker!r}")
    consensus_line = float(threshold_n) - 0.5

    player_name = ""
    player_norm = ""
    if market is not None:
        m_ticker = market.get("ticker")
        if m_ticker is not None and m_ticker != ticker:
            raise ValueError(f"Market ticker {m_ticker!r} disagrees with {ticker!r}")

        title = (market.get("title") or "").strip()
        tm = _TITLE_RE.fullmatch(title)
        if tm is None:
            raise ValueError(f"Invalid or missing market title {title!r} for {ticker!r}")
        title_name, title_n_s, title_stat = tm.groups()
        if int(title_n_s) != threshold_n:
            raise ValueError(
                f"Title threshold {title_n_s} disagrees with ticker {threshold_n} for {ticker!r}"
            )
        if title_stat != stat:
            raise ValueError(
                f"Title stat {title_stat!r} disagrees with series stat {stat!r} for {ticker!r}"
            )

        player_name = title_name.strip()
        player_norm = norm_name(player_name)
        if not player_norm or not _participant_matches_name(participant_key, player_name, player_norm):
            raise ValueError(
                f"Title player {player_name!r} disagrees with participant_key {participant_key!r}"
            )

        raw_strike = market.get("floor_strike")
        if raw_strike is None:
            raise ValueError(f"Missing floor_strike in market dict for {ticker!r}")
        try:
            floor_strike = float(raw_strike)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid floor_strike {raw_strike!r} for {ticker!r}") from exc
        if abs(floor_strike - consensus_line) > 1e-9:
            raise ValueError(
                f"floor_strike {floor_strike} disagrees with consensus_line {consensus_line} for {ticker!r}"
            )

    player_id = resolver(player_norm, team) if (resolver is not None and player_norm) else None
    return MappedMarket(
        ticker=ticker,
        series=series,
        stat=stat,
        event_date=event_date,
        away=away,
        home=home,
        team=team,
        participant_key=participant_key,
        player_name=player_name,
        player_norm=player_norm,
        player_id=player_id,
        threshold_n=threshold_n,
        consensus_line=consensus_line,
    )


def parse_market(
    market: dict[str, Any],
    resolver: Callable[[str, str], Any] | None = None,
) -> MappedMarket:
    """Convenience wrapper extracting ticker, title, and floor_strike from a Kalshi market dict."""
    if not isinstance(market, dict):
        raise ValueError(f"market must be a dict, got {type(market).__name__}")
    ticker = market.get("ticker") or ""
    return parse_ticker(ticker, market=market, resolver=resolver)
