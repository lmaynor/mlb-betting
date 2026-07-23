"""
mlb_core.odds.kalshi -- Kalshi exchange client + MLB market normalizer.

Kalshi is a CFTC-regulated event EXCHANGE (order book), not a sportsbook. Its
mid-price is a no-vig fair-probability estimate, which is exactly the "sharp
reference" the soft-line +EV strategy is starved for (see the 2026-07 profit
review). We ingest it as one synthetic book (book="kalshi", source="kalshi")
into the odds_history store, with fair_prob = order-book mid.

Market-DATA endpoints are PUBLIC -- NO auth / api key required for anything in
this module. (Trading/portfolio calls would need the API-key-ID + RSA request
signing; we do not place orders here.)

Confirmed via live probes 2026-07-22 (new-schema fields carry _dollars/_fp
suffixes; the legacy yes_bid/volume ints are absent):
  yes_bid_dollars, yes_ask_dollars, no_bid_dollars, no_ask_dollars  (str "0.4700")
  yes_bid_size_fp, yes_ask_size_fp, volume_fp, open_interest_fp     (str contracts)
  floor_strike (threshold for N+ markets), event_ticker, title, status, close_time

Event tickers encode date + teams, e.g. "KXMLBHR-26JUL231507TBTOR" ->
2026-07-23, away=TB, home=TOR (away first; a trailing "G2" marks doubleheader g2).
"""

from __future__ import annotations

import re

import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"

_session = requests.Session()
_session.headers["User-Agent"] = "beezy-kalshi/1.0"

# Kalshi MLB team abbreviations (superset incl. variants id_resolver aliases:
# AZ->ARI, ATH->OAK). Used to split concatenated teams out of an event_ticker.
KALSHI_ABBREVS = {
    "LAA", "ARI", "AZ", "BAL", "BOS", "CHC", "CIN", "CLE", "COL", "DET", "HOU",
    "KC", "LAD", "WSH", "NYM", "OAK", "ATH", "PIT", "SD", "SEA", "SF", "STL",
    "TB", "TEX", "TOR", "MIN", "PHI", "ATL", "CWS", "MIA", "NYY", "MIL",
}

_MONTHS = {"JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05",
           "JUN": "06", "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10",
           "NOV": "11", "DEC": "12"}

# event_ticker tail after the series prefix: <YY><MON><DD><HHMM><TEAMS>[G<n>]
_EVENT_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})(\d{4})([A-Z]+?)(G\d+)?$")


def ff(x) -> float:
    """Kalshi numeric fields are strings ('0.4700'); coerce, None/'' -> 0.0."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def get(path: str, **params):
    r = _session.get(f"{BASE}{path}", params=params, timeout=30)
    if r.status_code >= 400:
        return None
    return r.json()


def fetch_active_markets(series_ticker: str) -> list:
    """All status=='active' markets for a series (paginated via cursor)."""
    out, cursor = [], None
    for _ in range(40):
        p = {"series_ticker": series_ticker, "status": "open", "limit": 1000}
        if cursor:
            p["cursor"] = cursor
        data = get("/markets", **p)
        if not data:
            break
        out += [m for m in data.get("markets", []) if m.get("status") == "active"]
        cursor = data.get("cursor")
        if not cursor:
            break
    return out


def _split_teams(blob: str):
    """'TBTOR' -> ('TB','TOR'). Tries a 2- and 3-char away split; returns the
    UNIQUE valid (away, home) or (None, None) if ambiguous/unknown."""
    hits = []
    for i in (2, 3):
        away, home = blob[:i], blob[i:]
        if away in KALSHI_ABBREVS and home in KALSHI_ABBREVS:
            hits.append((away, home))
    return hits[0] if len(hits) == 1 else (None, None)


def parse_event_ticker(event_ticker: str):
    """('KXMLBHR-26JUL231507TBTOR') -> (game_date 'YYYY-MM-DD', away, home).
    Returns (None, None, None) if it doesn't parse. Teams may be (None, None)
    on an ambiguous split even when the date is good."""
    if not event_ticker or "-" not in event_ticker:
        return None, None, None
    tail = event_ticker.split("-", 1)[1]
    m = _EVENT_RE.match(tail)
    if not m:
        return None, None, None
    yy, mon, dd, _hhmm, teams, _dh = m.groups()
    mm = _MONTHS.get(mon)
    if not mm:
        return None, None, None
    game_date = f"20{yy}-{mm}-{dd}"
    away, home = _split_teams(teams)
    return game_date, away, home


def market_outcome(market: dict) -> str:
    """The team/side a team-ML or spread market's YES refers to, from the
    market ticker's last segment: 'KXMLBGAME-...-ATL' -> 'ATL',
    'KXMLBSPREAD-...-ATL2' -> 'ATL', '...-TIE' -> 'TIE'."""
    seg = (market.get("ticker") or "").rsplit("-", 1)[-1]
    return re.sub(r"\d+$", "", seg)  # strip trailing spread number


def player_from_title(market: dict) -> str:
    """'Vladimir Guerrero Jr.: 2+ home runs?' -> 'Vladimir Guerrero Jr.'."""
    t = market.get("yes_sub_title") or market.get("title") or ""
    return t.split(":", 1)[0].strip()


def prices(market: dict) -> dict:
    """Top-of-book for both sides. mid = no-vig fair prob; ask = executable
    (vig-inclusive analog) cost to take that side; sizes/volume/oi for depth."""
    yb, ya = ff(market.get("yes_bid_dollars")), ff(market.get("yes_ask_dollars"))
    nb, na = ff(market.get("no_bid_dollars")), ff(market.get("no_ask_dollars"))
    yes_mid = (yb + ya) / 2 if (yb and ya) else None
    no_mid = (nb + na) / 2 if (nb and na) else (1 - yes_mid if yes_mid is not None else None)
    return {
        "yes_bid": yb, "yes_ask": ya, "no_bid": nb, "no_ask": na,
        "yes_mid": yes_mid, "no_mid": no_mid,
        "yes_bid_size": ff(market.get("yes_bid_size_fp")),
        "yes_ask_size": ff(market.get("yes_ask_size_fp")),
        "volume": ff(market.get("volume_fp")),
        "open_interest": ff(market.get("open_interest_fp")),
    }


def prob_to_american(p: float):
    """Prob in (0,1) -> American odds int; None if degenerate."""
    if not p or p <= 0 or p >= 1:
        return None
    return round(100 * (1 - p) / p) if p < 0.5 else -round(100 * p / (1 - p))
