"""
mlb_core.odds.bettingpros -- shared BettingPros API client + parsers.

Single source of truth for the BettingPros public JSON API used by both the
local CLI (scripts/bettingpros_api.py) and the Cloud Run Job runner
(mlb.runners.backfill_bettingpros). No file I/O here -- callers decide where
rows go (local CSV vs GCS).

The page-scrape path (Selenium) could only see the ~5 virtualized rows the
player-props page mounts in the DOM; this hits the JSON API directly
(events -> offers, paginated) and returns every player/game.

Market shapes (verified 2026-06-28 / 2024-05-01)
------------------------------------------------
  player_ou   1 offer/player; selections over/under.
  moneyline   1 offer/game; 2 team selections, no line.
  spread      1 offer/game; 2 team selections w/ +/- line.
  total       1 offer/game; selections over/under.
  team_total  1 offer/team (2/game); selections over/under.
  yesno       1 offer/game; selections yes/no.

`Consensus` is the market-wide consensus (best historical signal); `Open` and
`Best Odds` are also captured. Major books (DraftKings/FanDuel/...) populate on
settled dates only, not same-day. Player `Team` reflects current team, not
historical -- join on player name + date.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

try:
    import requests
except ImportError as exc:  # pragma: no cover - runtime guard
    raise SystemExit("Missing dependency: requests. pip install requests") from exc


API_BASE = "https://api.bettingpros.com/v3"
# Public web key embedded in the BettingPros frontend. If requests start
# returning 401/403, refresh from a browser network tab.
API_KEY = "CHi8Hy5CEE4khd46XNYL23dCFX96oUdw6qOt1Dnh"

# market_id -> (csv_name, kind). Verified against /v3/markets 2026-06-28.
MARKETS = {
    # --- player O/U props (same shape as HR) ---
    299: ("home_runs", "player_ou"),
    287: ("hits", "player_ou"),
    288: ("runs", "player_ou"),
    289: ("rbi", "player_ou"),
    403: ("hits_runs_rbis", "player_ou"),
    295: ("singles", "player_ou"),
    291: ("doubles", "player_ou"),
    292: ("triples", "player_ou"),
    293: ("total_bases", "player_ou"),
    294: ("steals", "player_ou"),
    285: ("strikeouts", "player_ou"),
    290: ("earned_runs", "player_ou"),
    404: ("hits_allowed", "player_ou"),
    408: ("walks_allowed", "player_ou"),
    405: ("outs_recorded", "player_ou"),
    # --- game lines ---
    122: ("moneyline", "moneyline"),
    176: ("run_line", "spread"),
    175: ("total_runs", "total"),
    # --- inning markets ---
    278: ("1st_inning_moneyline", "moneyline"),
    279: ("5th_inning_moneyline", "moneyline"),
    280: ("1st_inning_runs", "total"),
    281: ("5th_inning_runs", "total"),
    402: ("2nd_inning_runs", "total"),
    282: ("1st_inning_spread", "spread"),
    283: ("5th_inning_spread", "spread"),
    277: ("team_total_runs", "team_total"),
    407: ("fifth_inning_team_runs", "team_total"),
    286: ("first_to_score", "moneyline"),
    369: ("run_in_1st_inning", "yesno"),
}

GROUPS = {
    "player": [m for m, (_, k) in MARKETS.items() if k == "player_ou"],
    "lines": [122, 176, 175],
    "innings": [278, 279, 280, 281, 402, 282, 283, 277, 407, 286, 369],
}
GROUPS["all"] = GROUPS["player"] + GROUPS["lines"] + GROUPS["innings"]

# /v3/books 2026-06-28. Books outside this map (offshore/DFS) are dropped to
# keep schemas stable. ESPNBet rebranded to theScore Bet (33).
BOOK_ID_TO_NAME = {
    0: "Consensus", 12: "DraftKings", 10: "FanDuel", 24: "bet365", 19: "BetMGM",
    13: "Caesars", 49: "Hard Rock Bet", 33: "theScore Bet", 14: "Fanatics",
    39: "Fliff", 18: "BetRivers", 15: "SugarHouse", 27: "PartyCasino", 71: "PointsBet",
}

FIXED_BOOK_COLUMNS = [
    "Open", "Best Odds", "Consensus", "bet365", "DraftKings", "BetMGM",
    "FanDuel", "theScore Bet", "BetRivers", "SugarHouse", "PartyCasino",
    "Fliff", "Caesars", "PointsBet", "Hard Rock Bet", "ESPNBet", "Fanatics",
]

MLB_SEASON_RANGES = {
    2024: ("2024-03-20", "2024-11-03"),
    2025: ("2025-03-20", "2025-11-03"),
    2026: ("2026-03-20", "2026-11-03"),
    2027: ("2027-03-20", "2027-11-03"),
}


# -----------------------------------------------------------------------------
# HTTP
# -----------------------------------------------------------------------------

def make_session() -> "requests.Session":
    s = requests.Session()
    s.headers.update({
        "x-api-key": API_KEY,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Origin": "https://www.bettingpros.com",
        "Referer": "https://www.bettingpros.com/mlb/odds/",
        "Accept": "application/json",
    })
    return s


def api_get(sess, path: str, params: dict) -> dict:
    url = f"{API_BASE}/{path}"
    for attempt in range(4):
        resp = sess.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 * (attempt + 1))
            continue
        raise RuntimeError(f"{resp.status_code} {url} params={params}: {resp.text[:200]}")
    raise RuntimeError(f"giving up after retries: {url} params={params}")


def fetch_markets(sess) -> dict:
    """Return {market_id: short_label} for MLB markets (discovery/debug)."""
    data = api_get(sess, "markets", {"sport": "MLB"})
    return {m.get("id"): (m.get("meta") or {}).get("short_label", "")
            for m in (data.get("markets") or [])}


def fetch_events(sess, target_date: str) -> dict:
    """{event_id: {away, home, matchup}} -- offers join here for team info."""
    data = api_get(sess, "events", {"sport": "MLB", "date": target_date})
    out: dict = {}
    for e in data.get("events") or []:
        eid = e.get("id")
        if eid is None:
            continue
        home = e.get("home") or ""
        abbrs = []
        for part in e.get("participants") or []:
            ab = (part.get("team") or {}).get("abbreviation") or part.get("id") or ""
            if ab:
                abbrs.append(ab)
        away = next((a for a in abbrs if a != home), e.get("visitor") or "")
        if not home and len(abbrs) == 2:
            home = abbrs[0]
        matchup = f"{away} at {home}" if away and home else (e.get("event_name") or "")
        out[eid] = {"away": away, "home": home, "matchup": matchup}
    return out


def fetch_offers(sess, market_id: int, event_ids: list) -> list:
    if not event_ids:
        return []
    offers: list = []
    page = 1
    while True:
        data = api_get(sess, "offers", {
            "sport": "MLB",
            "market_id": market_id,
            "event_id": ":".join(str(e) for e in event_ids),
            "location": "ALL",
            "limit": 10,  # API hard-caps limit at 10; we paginate.
            "page": page,
        })
        batch = data.get("offers") or []
        offers.extend(batch)
        pg = data.get("_pagination") or {}
        if page >= (pg.get("total_pages") or 1) or not batch:
            break
        page += 1
        time.sleep(0.3)
    return offers


# -----------------------------------------------------------------------------
# Odds helpers
# -----------------------------------------------------------------------------

def _fmt_odds(cost) -> str:
    if cost is None:
        return ""
    try:
        n = int(cost)
    except (TypeError, ValueError):
        return str(cost)
    return f"+{n}" if n > 0 else str(n)


def _sel_odds(sel: dict):
    """({col_name: odds_str}, line) for one selection. Columns are a subset of
    FIXED_BOOK_COLUMNS: Open (opening_line), Best Odds (best:true line),
    Consensus (book id 0), and each mapped sportsbook."""
    odds: dict = {}
    line = None
    op = sel.get("opening_line") or {}
    if op.get("cost") is not None:
        odds["Open"] = _fmt_odds(op.get("cost"))
    if op.get("line") is not None:
        line = op.get("line")
    for book in sel.get("books") or []:
        lines = book.get("lines") or []
        if not lines:
            continue
        ln = lines[0]
        if ln.get("line") is not None:
            line = ln.get("line")
        cost = ln.get("cost")
        if cost is None:
            continue
        if ln.get("best"):
            odds["Best Odds"] = _fmt_odds(cost)
        name = BOOK_ID_TO_NAME.get(book.get("id"))
        if name and name in FIXED_BOOK_COLUMNS:
            odds[name] = _fmt_odds(cost)
    return odds, line


def _sel_by_line(sel: dict) -> dict:
    """{line: {book_col: odds_str}} for ONE selection, keyed by EACH quote's own line.

    Books quote DIFFERENT main lines for the same player (e.g. FanDuel posts TB 1.5
    while others post 0.5). The old _sel_odds collapsed these to a single line, mixing
    a book's 0.5-line price into a 1.5-line row (and vice versa) -- the root of the
    OVER/UNDER "mirror" corruption. This keeps each quote under its actual line."""
    out: dict = {}
    op = sel.get("opening_line") or {}
    oc, ol = op.get("cost"), op.get("line")
    if oc is not None and ol is not None:
        out.setdefault(ol, {})["Open"] = _fmt_odds(oc)
    for book in sel.get("books") or []:
        name = BOOK_ID_TO_NAME.get(book.get("id"))
        for ln in book.get("lines") or []:
            line, cost = ln.get("line"), ln.get("cost")
            if line is None or cost is None:
                continue
            if ln.get("best"):
                out.setdefault(line, {})["Best Odds"] = _fmt_odds(cost)
            if name and name in FIXED_BOOK_COLUMNS:
                out.setdefault(line, {})[name] = _fmt_odds(cost)
    return out


def _rows_by_line(base: dict, selections, sides: tuple, out: list) -> None:
    """Emit ONE row per distinct line (each book paired with its own-line over/under),
    appending to `out`. `base` = non-book meta; `sides` = ("Over","Under") etc."""
    by_line: dict = {}   # line -> {book_col -> {side -> odds_str}}
    for sel in selections or []:
        side = _side(sel)
        if side not in sides:
            continue
        for line, cols in _sel_by_line(sel).items():
            lb = by_line.setdefault(line, {})
            for col, odds in cols.items():
                lb.setdefault(col, {})[side] = odds
    for line in sorted(by_line, key=lambda x: (x is None, x)):
        row = dict(base)
        row["Line"] = str(line)
        _blank_books(row, list(sides))
        for col, side_odds in by_line[line].items():
            for side, odds in side_odds.items():
                row[f"{col}_{side}"] = odds
        out.append(row)


def _side(sel: dict):
    lab = (sel.get("selection") or sel.get("label") or "").lower()
    if lab.startswith("o"):
        return "Over"
    if lab.startswith("u"):
        return "Under"
    if lab.startswith("y"):
        return "Yes"
    if lab.startswith("n"):
        return "No"
    return None


def _blank_books(row: dict, suffixes: list) -> None:
    for suf in suffixes:
        for b in FIXED_BOOK_COLUMNS:
            row[f"{b}_{suf}"] = ""


def _assign(row: dict, odds: dict, suffix: str) -> None:
    for col, val in odds.items():
        row[f"{col}_{suffix}"] = val


def _book_cols(suffixes: list) -> list:
    return [f"{b}_{suf}" for suf in suffixes for b in FIXED_BOOK_COLUMNS]


# -----------------------------------------------------------------------------
# Headers + row builders (per kind)
# -----------------------------------------------------------------------------

def headers(kind: str) -> list:
    if kind == "player_ou":
        base = ["Date", "Player", "Player_Page", "Matchup", "Team", "Position", "Line"]
        return base + _book_cols(["Over", "Under"])
    if kind == "total":
        return ["Date", "Matchup", "Away", "Home", "Line"] + _book_cols(["Over", "Under"])
    if kind == "team_total":
        return ["Date", "Matchup", "Away", "Home", "Team", "Line"] + _book_cols(["Over", "Under"])
    if kind == "moneyline":
        return ["Date", "Matchup", "Away", "Home"] + _book_cols(["Away", "Home"])
    if kind == "spread":
        return ["Date", "Matchup", "Away", "Home", "Away_Line", "Home_Line"] + _book_cols(["Away", "Home"])
    if kind == "yesno":
        return ["Date", "Matchup", "Away", "Home", "Line"] + _book_cols(["Yes", "No"])
    raise ValueError(f"unknown kind {kind}")


def build_rows(kind: str, offers: list, ds: str, ev_map: dict) -> list:
    if kind == "player_ou":
        return _rows_player_ou(offers, ds, ev_map)
    if kind in ("total", "yesno"):
        return _rows_two_sided_game(offers, ds, ev_map, kind)
    if kind == "team_total":
        return _rows_team_total(offers, ds, ev_map)
    if kind in ("moneyline", "spread"):
        return _rows_team_pick(offers, ds, ev_map, kind)
    raise ValueError(f"unknown kind {kind}")


def _rows_player_ou(offers, ds, ev_map) -> list:
    out = []
    for o in offers:
        parts = o.get("participants") or []
        if not parts:
            continue
        p = parts[0]
        name = p.get("name") or ""
        if not name:
            continue
        player = p.get("player") or {}
        slug = player.get("slug") or ""
        ev = ev_map.get(o.get("event_id"), {})
        base = {
            "Date": ds, "Player": name,
            "Player_Page": f"/mlb/props/{slug}/" if slug else (p.get("link") or ""),
            "Matchup": ev.get("matchup", ""), "Team": player.get("team") or "",
            "Position": player.get("position") or "",
        }
        # one row per (player, line) -- books that quote different main lines no
        # longer collapse into one mislabeled row.
        _rows_by_line(base, o.get("selections"), ("Over", "Under"), out)
    return out


def _rows_two_sided_game(offers, ds, ev_map, kind) -> list:
    out = []
    for o in offers:
        ev = ev_map.get(o.get("event_id"), {})
        base = {"Date": ds, "Matchup": ev.get("matchup", ""),
                "Away": ev.get("away", ""), "Home": ev.get("home", "")}
        if kind == "total":
            # game totals also have per-book line divergence (8.5 vs 9) -> per-line rows
            _rows_by_line(base, o.get("selections"), ("Over", "Under"), out)
        else:  # yesno (NRFI etc.): no line, single row
            row = {**base, "Line": ""}
            _blank_books(row, ["Yes", "No"])
            for sel in o.get("selections") or []:
                side = _side(sel)
                if side not in ("Yes", "No"):
                    continue
                odds, _line = _sel_odds(sel)
                _assign(row, odds, side)
            out.append(row)
    return out


def _rows_team_total(offers, ds, ev_map) -> list:
    out = []
    for o in offers:
        ev = ev_map.get(o.get("event_id"), {})
        team = o.get("team_id") or ""
        if not team:
            parts = o.get("participants") or []
            team = parts[0].get("id") if parts else ""
        base = {"Date": ds, "Matchup": ev.get("matchup", ""),
                "Away": ev.get("away", ""), "Home": ev.get("home", ""), "Team": team}
        _rows_by_line(base, o.get("selections"), ("Over", "Under"), out)
    return out


def _rows_team_pick(offers, ds, ev_map, kind) -> list:
    """Moneyline / spread: one row per game, columns by Away/Home team."""
    out = []
    for o in offers:
        ev = ev_map.get(o.get("event_id"), {})
        home_abbr = ev.get("home", "")
        row = {"Date": ds, "Matchup": ev.get("matchup", ""),
               "Away": ev.get("away", ""), "Home": home_abbr}
        if kind == "spread":
            row["Away_Line"] = ""
            row["Home_Line"] = ""
        _blank_books(row, ["Away", "Home"])
        for sel in o.get("selections") or []:
            part = sel.get("participant") or ""
            suffix = "Home" if part and part == home_abbr else "Away"
            odds, line = _sel_odds(sel)
            if kind == "spread" and line is not None:
                row[f"{suffix}_Line"] = str(line)
            _assign(row, odds, suffix)
        out.append(row)
    return out


# -----------------------------------------------------------------------------
# Date utils + market resolution
# -----------------------------------------------------------------------------

def date_range(start: str, end: str):
    cur = datetime.strptime(start, "%Y-%m-%d").date()
    fin = datetime.strptime(end, "%Y-%m-%d").date()
    while cur <= fin:
        yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def in_season(ds: str) -> bool:
    d = datetime.strptime(ds, "%Y-%m-%d").date()
    bounds = MLB_SEASON_RANGES.get(d.year)
    if not bounds:
        return True
    lo = datetime.strptime(bounds[0], "%Y-%m-%d").date()
    hi = datetime.strptime(bounds[1], "%Y-%m-%d").date()
    return lo <= d <= hi


_NAME_TO_ID = {name: mid for mid, (name, _kind) in MARKETS.items()}


def resolve_markets(arg: str) -> list:
    out: list = []
    for tok in (arg or "all").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok in GROUPS:
            out.extend(GROUPS[tok])
        elif tok.isdigit() and int(tok) in MARKETS:
            out.append(int(tok))
        elif tok in _NAME_TO_ID:                       # accept market names too
            out.append(_NAME_TO_ID[tok])
        else:
            raise ValueError(
                f"unknown market token '{tok}'. groups: {list(GROUPS)}, "
                f"names: {sorted(_NAME_TO_ID)}, or ids in {sorted(MARKETS)}")
    seen = set()
    return [m for m in out if not (m in seen or seen.add(m))]


# -----------------------------------------------------------------------------
# Readers -- consume the partitioned GCS output written by
# mlb.runners.backfill_bettingpros (Odds/bettingpros/{market}/{date}.csv).
#
# These read THROUGH mlb_core.storage, so they work against GCS (when
# MLB_GCS_BUCKET/GCS_BUCKET is set) or local files transparently. They are the
# raw-store accessors the roadmap's P0.3 bettingpros_to_parquet.py builds on to
# normalize into the odds_history Parquet schema. pandas/storage are imported
# lazily so the lightweight scrape/CLI path does not require them.
#
# NOTE: reading is per-date-file (one storage round-trip per date). Fine for
# ad-hoc analysis; for repeated heavy scans, materialize to Parquet (P0.3).
# -----------------------------------------------------------------------------

DEFAULT_GCS_PREFIX = "Odds/bettingpros"


def market_name(market) -> str:
    """Accept a market id (int or digit-string) or a csv name; return csv name."""
    if isinstance(market, int) or (isinstance(market, str) and market.isdigit()):
        mid = int(market)
        if mid not in MARKETS:
            raise ValueError(f"unknown market id {mid}")
        return MARKETS[mid][0]
    names = {name for name, _ in MARKETS.values()}
    if market in names:
        return market
    raise ValueError(f"unknown market '{market}'. names: {sorted(names)}")


def list_market_dates(market, prefix: str = DEFAULT_GCS_PREFIX) -> list:
    """Sorted list of YYYY-MM-DD strings present for a market in the store."""
    from mlb_core import storage
    name = market_name(market)
    dates = []
    for key in storage.list_keys(f"{prefix}/{name}/"):
        stem = key.rsplit("/", 1)[-1]
        if stem.endswith(".csv"):
            dates.append(stem[:-4])
    return sorted(dates)


def read_market(market, start: str | None = None, end: str | None = None,
                prefix: str = DEFAULT_GCS_PREFIX):
    """Concatenate a market's partitioned daily CSVs into one DataFrame.

    start/end (YYYY-MM-DD, inclusive) filter the dates read. Returns an empty
    DataFrame with the right columns if no partitions match.
    """
    import pandas as pd
    from mlb_core import storage
    name = market_name(market)
    kind = next(k for n, k in MARKETS.values() if n == name)
    dates = list_market_dates(name, prefix)
    if start:
        dates = [d for d in dates if d >= start]
    if end:
        dates = [d for d in dates if d <= end]
    frames = []
    for d in dates:
        try:
            frames.append(storage.read_csv(f"{prefix}/{name}/{d}.csv", dtype=str))
        except Exception:  # noqa: BLE001 -- skip a corrupt/missing partition, keep going
            continue
    if not frames:
        return pd.DataFrame(columns=headers(kind))
    return pd.concat(frames, ignore_index=True)


def coverage_report(markets=None, prefix: str = DEFAULT_GCS_PREFIX) -> dict:
    """Per-market coverage: date count, first/last date, dates-per-season.

    The roadmap's coverage-gating discipline: a backtest should refuse/warn when
    a market's coverage for the requested window is thin. This surfaces that.
    """
    if markets is None:
        markets = [n for n, _ in MARKETS.values()]
    report = {}
    for m in markets:
        name = market_name(m)
        dates = list_market_dates(name, prefix)
        per_season: dict = {}
        for d in dates:
            yr = d[:4]
            per_season[yr] = per_season.get(yr, 0) + 1
        report[name] = {
            "n_dates": len(dates),
            "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None,
            "per_season": per_season,
        }
    return report
