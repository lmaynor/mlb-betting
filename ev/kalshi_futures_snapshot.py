"""ev/kalshi_futures_snapshot.py -- BNK-14: Kalshi futures daily snapshotter.

Collects daily snapshots of public Kalshi futures and outright markets across
six leagues (NBA, NFL, MLB, NHL, NCAAF, NCAAB, WNBA) and banks them to GCS:

    gs://<bucket>/Futures/kalshi/dt=<YYYY-MM-DD>/snapshot.parquet
    gs://<bucket>/Futures/kalshi/dt=<YYYY-MM-DD>/manifest.json

Read-only public market data only:
    - NO Kalshi API credentials, NO auth headers.
    - NO order placement, NO portfolio/account calls.
    - User-Agent header: "Beezy Research (beezy.fyi; research@beezy.fyi)"

Consumer:
    ev/futures_sleeve.py (Module 8: Slow Markets / Futures Swing Sleeve).
    The sleeve's FuturesSnapshot is {venue, market, outcome, q, quote_ts} and
    bank_futures_board groups by (venue, market). Our banked rows are the raw
    superset of that shape, preserving separate bid, ask, and last prices.

Incomplete-is-not-invalid doctrine:
    - Dormant series (zero open events) are recorded in manifest with row_count 0.
    - Ask-only or one-sided markets (e.g. bid 0.0000, ask 0.0100) are recorded
      with yes_bid = 0.0 (never nulled).
    - Missing volume/open interest or strike fields remain null without dropping rows.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import requests

logger = logging.getLogger(__name__)

PUBLIC_BASE = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "Beezy Research (beezy.fyi; research@beezy.fyi)"
CHICAGO_TZ = ZoneInfo("America/Chicago")

# Draft series are excluded from futures sleeve banking (not sleeve input)
EXCLUDED_DRAFT_PREFIXES = ("KXNBADRAFT", "KXNFLDRAFT")

# Discovery prefixes for dynamic pattern families
DISCOVERY_PREFIXES = (
    "KXLEADER",      # Stat leaders across leagues
    "KXMLBWINS-",    # MLB per-team win totals
    "KXNFLSEASON",   # NFL season-total ladders
    "KXNCAAFSEASON", # NCAAF season-total ladders
    "KXMARMAD",      # Tournament rounds & march madness
)

# NCAAB regular-season conference series explicitly named in memo
DISCOVERY_NCAAB_REG_CONFERENCES = (
    "KXBIG10REG",
    "KXACCREG",
    "KXBIG12REG",
    "KXBIGEASTREG",
    "KXA10REG",
    "KXAACREG",
    "KXMWREG",
)

# -----------------------------------------------------------------------------
# Explicit series catalog (77 from probe_summary.json + 33 from memo = 110)
# -----------------------------------------------------------------------------
EXPLICIT_SERIES: dict[str, dict[str, str]] = {
    # NBA
    "KXNBA": {"league": "nba", "family": "champion"},
    "KXNBAEAST": {"league": "nba", "family": "conference"},
    "KXNBAWEST": {"league": "nba", "family": "conference"},
    "KXNBAATLANTIC": {"league": "nba", "family": "division"},
    "KXNBACENTRAL": {"league": "nba", "family": "division"},
    "KXNBASOUTHEAST": {"league": "nba", "family": "division"},
    "KXNBANORTHWEST": {"league": "nba", "family": "division"},
    "KXNBAPACIFIC": {"league": "nba", "family": "division"},
    "KXNBASOUTHWEST": {"league": "nba", "family": "division"},
    "KXNBAPLAYOFF": {"league": "nba", "family": "playoffs"},
    "KXNBAWINS": {"league": "nba", "family": "win_totals"},
    "KXNBAMVP": {"league": "nba", "family": "awards"},
    "KXNBADPOY": {"league": "nba", "family": "awards"},
    "KXNBAROY": {"league": "nba", "family": "awards"},
    "KXNBAMIMP": {"league": "nba", "family": "awards"},
    "KXNBASIXTH": {"league": "nba", "family": "awards"},
    "KXNBACLUTCH": {"league": "nba", "family": "awards"},
    "KXNBASTATLEADER": {"league": "nba", "family": "stat_leader"},
    "KXNBADRAFT1": {"league": "nba", "family": "draft"},
    "KXNBADRAFTOU": {"league": "nba", "family": "draft"},
    "KXNBAFINMVP": {"league": "nba", "family": "awards"},

    # NFL
    "KXSB": {"league": "nfl", "family": "champion"},
    "KXNFLAFCCHAMP": {"league": "nfl", "family": "conference"},
    "KXNFLNFCCHAMP": {"league": "nfl", "family": "conference"},
    "KXNFLAFCNORTH": {"league": "nfl", "family": "division"},
    "KXNFLAFCSOUTH": {"league": "nfl", "family": "division"},
    "KXNFLAFCEAST": {"league": "nfl", "family": "division"},
    "KXNFLAFCWEST": {"league": "nfl", "family": "division"},
    "KXNFLNFCNORTH": {"league": "nfl", "family": "division"},
    "KXNFLNFCSOUTH": {"league": "nfl", "family": "division"},
    "KXNFLNFCEAST": {"league": "nfl", "family": "division"},
    "KXNFLNFCWEST": {"league": "nfl", "family": "division"},
    "KXNFLPLAYOFF": {"league": "nfl", "family": "playoffs"},
    "KXNFLWINS": {"league": "nfl", "family": "win_totals"},
    "KXNFLWINS-DEN": {"league": "nfl", "family": "win_totals"},
    "KXNFLMVP": {"league": "nfl", "family": "awards"},
    "KXNFLOPOTY": {"league": "nfl", "family": "awards"},
    "KXNFLOPOY": {"league": "nfl", "family": "awards"},
    "KXNFLDPOY": {"league": "nfl", "family": "awards"},
    "KXNFLOROTY": {"league": "nfl", "family": "awards"},
    "KXNFLDROTY": {"league": "nfl", "family": "awards"},
    "KXNFLDROY": {"league": "nfl", "family": "awards"},
    "KXNFLCPOTY": {"league": "nfl", "family": "awards"},
    "KXNFLSBMVP": {"league": "nfl", "family": "awards"},
    "KXLEADERNFLPYDS": {"league": "nfl", "family": "stat_leader"},
    "KXNFLSEASONPASSYDS": {"league": "nfl", "family": "season_totals"},
    "KXNFLDRAFT1ST": {"league": "nfl", "family": "draft"},

    # MLB
    "KXMLB": {"league": "mlb", "family": "champion"},
    "KXMLBAL": {"league": "mlb", "family": "conference"},
    "KXMLBNL": {"league": "mlb", "family": "conference"},
    "KXMLBALEAST": {"league": "mlb", "family": "division"},
    "KXMLBALCENT": {"league": "mlb", "family": "division"},
    "KXMLBALWEST": {"league": "mlb", "family": "division"},
    "KXMLBNLEAST": {"league": "mlb", "family": "division"},
    "KXMLBNLCENT": {"league": "mlb", "family": "division"},
    "KXMLBNLWEST": {"league": "mlb", "family": "division"},
    "KXMLBPLAYOFFS": {"league": "mlb", "family": "playoffs"},
    "KXMLBWINS-NYY": {"league": "mlb", "family": "win_totals"},
    "KXMLBALMVP": {"league": "mlb", "family": "awards"},
    "KXMLBNLMVP": {"league": "mlb", "family": "awards"},
    "KXMLBALCY": {"league": "mlb", "family": "awards"},
    "KXMLBNLCY": {"league": "mlb", "family": "awards"},
    "KXMLBALROTY": {"league": "mlb", "family": "awards"},
    "KXMLBNLROTY": {"league": "mlb", "family": "awards"},
    "KXLEADERMLBHR": {"league": "mlb", "family": "stat_leader"},

    # NHL
    "KXNHL": {"league": "nhl", "family": "champion"},
    "KXNHLEAST": {"league": "nhl", "family": "conference"},
    "KXNHLWEST": {"league": "nhl", "family": "conference"},
    "KXNHLATLANTIC": {"league": "nhl", "family": "division"},
    "KXNHLMETROPOLITAN": {"league": "nhl", "family": "division"},
    "KXNHLCENTRAL": {"league": "nhl", "family": "division"},
    "KXNHLPACIFIC": {"league": "nhl", "family": "division"},
    "KXNHLPRES": {"league": "nhl", "family": "presidents_trophy"},
    "KXNHLPLAYOFF": {"league": "nhl", "family": "playoffs"},
    "KXNHLSEASONPTS": {"league": "nhl", "family": "season_points"},
    "KXNHLHART": {"league": "nhl", "family": "awards"},
    "KXNHLMVP": {"league": "nhl", "family": "awards"},
    "KXNHLNORRIS": {"league": "nhl", "family": "awards"},
    "KXNHLVEZINA": {"league": "nhl", "family": "awards"},
    "KXNHLCALDER": {"league": "nhl", "family": "awards"},
    "KXCONNSMYTHE": {"league": "nhl", "family": "awards"},
    "KXNHLROSS": {"league": "nhl", "family": "awards"},
    "KXNHLRICHARD": {"league": "nhl", "family": "awards"},

    # NCAAF
    "KXNCAAFPLAYOFF": {"league": "ncaaf", "family": "cfp"},
    "KXNCAAFTOPSEED": {"league": "ncaaf", "family": "top_seed"},
    "KXNCAAFWINS": {"league": "ncaaf", "family": "win_totals"},
    "KXHEISMAN": {"league": "ncaaf", "family": "awards"},

    # NCAAB
    "KXMARMAD": {"league": "ncaab", "family": "champion"},
    "KXMARMADROUND": {"league": "ncaab", "family": "round_reach"},
    "KXMARMAD1SEED": {"league": "ncaab", "family": "top_seed"},
    "KXBIG10REG": {"league": "ncaab", "family": "conference"},
    "KXACCREG": {"league": "ncaab", "family": "conference"},
    "KXBIG12REG": {"league": "ncaab", "family": "conference"},
    "KXBIGEASTREG": {"league": "ncaab", "family": "conference"},
    "KXA10REG": {"league": "ncaab", "family": "conference"},
    "KXAACREG": {"league": "ncaab", "family": "conference"},
    "KXMWREG": {"league": "ncaab", "family": "conference"},
    "KXNCAAMBWINS": {"league": "ncaab", "family": "win_totals"},
    "KXNCAAMBNAISMITH": {"league": "ncaab", "family": "naismith"},

    # WNBA
    "KXWNBA": {"league": "wnba", "family": "champion"},
    "KXWNBAEAST": {"league": "wnba", "family": "conference"},
    "KXWNBAWEST": {"league": "wnba", "family": "conference"},
    "KXWNBAPLAYOFF": {"league": "wnba", "family": "playoffs"},
    "KXWNBAWINS": {"league": "wnba", "family": "win_totals"},
    "KXWNBAMVP": {"league": "wnba", "family": "awards"},
    "KXWNBADPOY": {"league": "wnba", "family": "awards"},
    "KXWNBAROY": {"league": "wnba", "family": "awards"},
    "KXWNBAMIMP": {"league": "wnba", "family": "awards"},
    "KXWNBA6POY": {"league": "wnba", "family": "awards"},
    "KXLEADERWNBAPTS": {"league": "wnba", "family": "stat_leader"},
}

OUTPUT_COLUMNS = [
    "venue",
    "series_ticker",
    "event_ticker",
    "market",
    "market_ticker",
    "outcome",
    "yes_sub_title",
    "strike_type",
    "floor_strike",
    "cap_strike",
    "yes_bid",
    "yes_ask",
    "last_price",
    "volume_fp",
    "open_interest_fp",
    "status",
    "fee_type",
    "fee_multiplier",
    "league",
    "family",
    "captured_at",
    "dt",
]


def is_draft_series(ticker: str) -> bool:
    """Check if series is draft-related (excluded from sleeve banking)."""
    return any(ticker.startswith(p) for p in EXCLUDED_DRAFT_PREFIXES)


def matches_discovery_pattern(ticker: str) -> bool:
    """Check if series ticker matches any runtime discovery pattern."""
    if is_draft_series(ticker):
        return False
    if any(ticker.startswith(p) for p in DISCOVERY_PREFIXES):
        return True
    if ticker in DISCOVERY_NCAAB_REG_CONFERENCES:
        return True
    if ticker.startswith("KX") and ticker.endswith("REG") and ticker in DISCOVERY_NCAAB_REG_CONFERENCES:
        return True
    return False


def infer_metadata(ticker: str) -> tuple[str, str]:
    """Infer (league, family) for a given series ticker."""
    if ticker in EXPLICIT_SERIES:
        info = EXPLICIT_SERIES[ticker]
        return info["league"], info["family"]

    if is_draft_series(ticker):
        league = "nba" if ticker.startswith("KXNBA") else "nfl"
        return league, "draft"

    # Stat leaders
    if ticker.startswith("KXLEADER"):
        sub = ticker[len("KXLEADER"):]
        if sub.startswith("MLB"):
            return "mlb", "stat_leader"
        if sub.startswith("NFL"):
            return "nfl", "stat_leader"
        if sub.startswith("NBA"):
            return "nba", "stat_leader"
        if sub.startswith("WNBA"):
            return "wnba", "stat_leader"
        if sub.startswith("NHL"):
            return "nhl", "stat_leader"
        return "other", "stat_leader"

    # MLB win totals
    if ticker.startswith("KXMLBWINS-"):
        return "mlb", "win_totals"

    # Season totals
    if ticker.startswith("KXNFLSEASON"):
        return "nfl", "season_totals"
    if ticker.startswith("KXNCAAFSEASON"):
        return "ncaaf", "season_totals"

    # March Madness / tournament reach
    if ticker.startswith("KXMARMAD"):
        if "ROUND" in ticker:
            return "ncaab", "round_reach"
        if "1SEED" in ticker:
            return "ncaab", "top_seed"
        return "ncaab", "champion"

    # NCAAB regular season conference outrights
    if ticker in DISCOVERY_NCAAB_REG_CONFERENCES or (ticker.startswith("KX") and ticker.endswith("REG")):
        return "ncaab", "conference"

    # Generic heuristics
    league = "other"
    if "NBA" in ticker:
        league = "nba"
    elif "NFL" in ticker:
        league = "nfl"
    elif "MLB" in ticker:
        league = "mlb"
    elif "NHL" in ticker:
        league = "nhl"
    elif "WNBA" in ticker:
        league = "wnba"
    elif "NCAAF" in ticker:
        league = "ncaaf"
    elif "NCAA" in ticker:
        league = "ncaab"

    return league, "other"


def _parse_float(val: Any) -> float | None:
    """Parse float from string or number, keeping 0.0 as 0.0, returning None if absent."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def build_market_row(
    market: dict[str, Any],
    series_ticker: str,
    event_ticker: str,
    fee_type: str | None,
    fee_multiplier: float | None,
    captured_at: str,
    dt: str,
    league: str,
    family: str,
) -> dict[str, Any]:
    """Build a single normalized market row for the futures snapshot."""
    yes_sub_title = market.get("yes_sub_title")
    m_ticker = market.get("ticker", "")
    ev_ticker = market.get("event_ticker") or event_ticker

    return {
        "venue": "kalshi",
        "series_ticker": series_ticker,
        "event_ticker": ev_ticker,
        "market": ev_ticker,  # Sleeve groups by event_ticker
        "market_ticker": m_ticker,
        "outcome": yes_sub_title or "",
        "yes_sub_title": yes_sub_title,
        "strike_type": market.get("strike_type"),
        "floor_strike": _parse_float(market.get("floor_strike")),
        "cap_strike": _parse_float(market.get("cap_strike")),
        "yes_bid": _parse_float(market.get("yes_bid_dollars")),
        "yes_ask": _parse_float(market.get("yes_ask_dollars")),
        "last_price": _parse_float(market.get("last_price_dollars")),
        "volume_fp": _parse_float(market.get("volume_fp")),
        "open_interest_fp": _parse_float(market.get("open_interest_fp")),
        "status": market.get("status", ""),
        "fee_type": fee_type,
        "fee_multiplier": fee_multiplier,
        "league": league,
        "family": family,
        "captured_at": captured_at,
        "dt": dt,
    }


# -----------------------------------------------------------------------------
# HTTP Client
# -----------------------------------------------------------------------------
class KalshiFuturesClient:
    """Paced, retrying HTTP client for Kalshi public market data."""

    def __init__(
        self,
        base_url: str = PUBLIC_BASE,
        user_agent: str = USER_AGENT,
        pace_s: float = 0.05,
        max_retries: int = 3,
        session: requests.Session | None = None,
        fetch_json_fn: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.pace_s = pace_s
        self.max_retries = max_retries
        self.fetch_json_fn = fetch_json_fn
        self.request_count = 0

        if session is None:
            self.session = requests.Session()
            self.session.headers["User-Agent"] = user_agent
        else:
            self.session = session
            if "User-Agent" not in self.session.headers:
                self.session.headers["User-Agent"] = user_agent

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a GET request with pacing and 429 exponential backoff retries."""
        params = params or {}
        if self.fetch_json_fn is not None:
            self.request_count += 1
            return self.fetch_json_fn(path, params)

        if self.pace_s > 0:
            time.sleep(self.pace_s)

        url = f"{self.base_url}{path}"
        backoff = 1.0

        for attempt in range(self.max_retries + 1):
            self.request_count += 1
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    if attempt < self.max_retries:
                        time.sleep(backoff)
                        backoff *= 2.0
                        continue
                    resp.raise_for_status()

                if resp.status_code >= 500 and attempt < self.max_retries:
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue

                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ConnectionError) as exc:
                if attempt < self.max_retries:
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                raise exc

        raise RuntimeError(f"Exceeded max retries for {url}")

    def fetch_series_catalog(self) -> list[dict[str, Any]]:
        """Paginate all series from GET /series."""
        out = []
        cursor = None
        while True:
            params: dict[str, Any] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = self.get_json("/series", params=params)
            series_list = data.get("series", [])
            out.extend(series_list)
            cursor = data.get("cursor")
            if not cursor or not series_list:
                break
        return out

    def fetch_series(self, ticker: str) -> dict[str, Any]:
        """Fetch metadata for a single series from GET /series/<ticker>."""
        data = self.get_json(f"/series/{ticker}")
        return data.get("series") or {}

    def fetch_events_for_series(self, series_ticker: str) -> list[dict[str, Any]]:
        """Paginate open events with nested markets for a single series."""
        out = []
        cursor = None
        while True:
            params: dict[str, Any] = {
                "series_ticker": series_ticker,
                "with_nested_markets": "true",
                "status": "open",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            data = self.get_json("/events", params=params)
            events = data.get("events", [])
            out.extend(events)
            cursor = data.get("cursor")
            if not cursor or not events:
                break
        return out


# -----------------------------------------------------------------------------
# Snapshot collector
# -----------------------------------------------------------------------------
def discover_series(
    catalog: Sequence[dict[str, Any]],
    explicit_tickers: set[str],
) -> list[str]:
    """Discover dynamic series matching patterns from catalog, sorted deterministically."""
    discovered = set()
    for item in catalog:
        ticker = item.get("ticker", "")
        if matches_discovery_pattern(ticker) and not is_draft_series(ticker):
            discovered.add(ticker)
    return sorted(discovered)


def collect_snapshot(
    client: KalshiFuturesClient,
    target_series: Sequence[str] | None = None,
    dt: str | None = None,
    now_utc: datetime | None = None,
    catalog: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Collect futures market rows and manifest across the target series set.

    Returns:
        (rows, manifest_dict, failed_series)
    """
    now = now_utc or datetime.now(timezone.utc)
    captured_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if dt is None:
        dt = datetime.now(CHICAGO_TZ).strftime("%Y-%m-%d")

    # Series fees cache
    series_fees: dict[str, tuple[str | None, float | None]] = {}
    discovered_series: list[str] = []

    # 1. Determine series set
    if target_series is not None:
        active_series = sorted(set(target_series))
    else:
        # Full scan: catalog discovery
        try:
            if catalog is None:
                catalog = client.fetch_series_catalog()
            for s in catalog:
                t = s.get("ticker")
                if t:
                    series_fees[t] = (
                        s.get("fee_type"),
                        _parse_float(s.get("fee_multiplier")),
                    )
            discovered_set = set(discover_series(catalog, set(EXPLICIT_SERIES.keys())))
            discovered_series = sorted(discovered_set)
        except Exception as exc:
            logger.warning("Catalog discovery failed: %s; falling back to explicit set", exc)
            discovered_series = []

        all_candidates = set(EXPLICIT_SERIES.keys()) | set(discovered_series)
        active_series = sorted(t for t in all_candidates if not is_draft_series(t))

    rows: list[dict[str, Any]] = []
    per_series: dict[str, dict[str, Any]] = {}
    failed_series: list[str] = []
    families_summary: dict[str, dict[str, int]] = {}

    # 2. Iterate each series
    for ticker in active_series:
        league, family = infer_metadata(ticker)

        # Lookup or fetch fee metadata
        fee_type, fee_multiplier = series_fees.get(ticker, (None, None))
        if fee_type is None and fee_multiplier is None:
            try:
                s_meta = client.fetch_series(ticker)
                fee_type = s_meta.get("fee_type")
                fee_multiplier = _parse_float(s_meta.get("fee_multiplier"))
                series_fees[ticker] = (fee_type, fee_multiplier)
            except Exception:
                fee_type, fee_multiplier = None, None

        # Fetch open events with nested markets
        try:
            events = client.fetch_events_for_series(ticker)
            n_events = len(events)
            n_rows_series = 0

            for ev in events:
                ev_ticker = ev.get("event_ticker", "")
                markets = ev.get("markets", [])
                for mk in markets:
                    row = build_market_row(
                        market=mk,
                        series_ticker=ticker,
                        event_ticker=ev_ticker,
                        fee_type=fee_type,
                        fee_multiplier=fee_multiplier,
                        captured_at=captured_at,
                        dt=dt,
                        league=league,
                        family=family,
                    )
                    rows.append(row)
                    n_rows_series += 1

            per_series[ticker] = {
                "family": family,
                "league": league,
                "rows": n_rows_series,
                "row_count": n_rows_series,
                "events": n_events,
            }

            if family not in families_summary:
                families_summary[family] = {"series_count": 0, "rows": 0, "events": 0}
            families_summary[family]["series_count"] += 1
            families_summary[family]["rows"] += n_rows_series
            families_summary[family]["events"] += n_events

        except Exception as exc:
            logger.error("Failed fetching events for series %s: %s", ticker, exc)
            failed_series.append(ticker)
            per_series[ticker] = {
                "family": family,
                "league": league,
                "rows": 0,
                "row_count": 0,
                "events": 0,
                "error": str(exc),
            }
            if family not in families_summary:
                families_summary[family] = {"series_count": 0, "rows": 0, "events": 0}
            families_summary[family]["series_count"] += 1

    manifest = {
        "dt": dt,
        "captured_at": captured_at,
        "total_rows": len(rows),
        "total_requests": client.request_count,
        "per_series": per_series,
        "families": families_summary,
        "discovered_series": discovered_series,
        "failed_series": failed_series,
    }

    return rows, manifest, failed_series


# -----------------------------------------------------------------------------
# Storage & banking
# -----------------------------------------------------------------------------
def save_snapshot(
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    dt: str,
    bucket: str | None = None,
    out_dir: str | Path | None = None,
) -> tuple[str, str]:
    """Bank parquet and manifest locally or to GCS. Idempotent per dt."""
    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    parquet_rel = f"Futures/kalshi/dt={dt}/snapshot.parquet"
    manifest_rel = f"Futures/kalshi/dt={dt}/manifest.json"

    if out_dir:
        base_dir = Path(out_dir)
        target_dir = base_dir / f"Futures/kalshi/dt={dt}"
        target_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = target_dir / "snapshot.parquet"
        manifest_path = target_dir / "manifest.json"

        df.to_parquet(parquet_path, index=False)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        return str(parquet_path), str(manifest_path)

    gcs_bucket = bucket or os.environ.get("MLB_GCS_BUCKET") or os.environ.get("GCS_BUCKET")
    if not gcs_bucket:
        raise ValueError(
            "No output destination specified: provide --out-dir or set MLB_GCS_BUCKET / GCS_BUCKET env var"
        )

    from google.cloud import storage as gcs

    client = gcs.Client()
    b = client.bucket(gcs_bucket)

    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    parquet_bytes = buf.getvalue()

    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")

    blob_p = b.blob(parquet_rel)
    blob_p.upload_from_string(parquet_bytes, content_type="application/octet-stream")

    blob_m = b.blob(manifest_rel)
    blob_m.upload_from_string(manifest_bytes, content_type="application/json")

    return f"gs://{gcs_bucket}/{parquet_rel}", f"gs://{gcs_bucket}/{manifest_rel}"


# -----------------------------------------------------------------------------
# CLI entrypoint
# -----------------------------------------------------------------------------
def main(
    argv: Sequence[str] | None = None,
    client: KalshiFuturesClient | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Kalshi futures daily snapshotter (BNK-14)")
    parser.add_argument(
        "--date",
        dest="dt",
        default=None,
        help="Capture calendar date YYYY-MM-DD (default: today in America/Chicago)",
    )
    parser.add_argument(
        "--bucket",
        default=None,
        help="GCS bucket name (overrides MLB_GCS_BUCKET / GCS_BUCKET env vars)",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Write locally to DIR instead of GCS (for tests and dry runs)",
    )
    parser.add_argument(
        "--series",
        default=None,
        help="Comma-separated series tickers to restrict capture (e.g. KXNBA,KXSB)",
    )
    args = parser.parse_args(argv)

    dt = args.dt or datetime.now(CHICAGO_TZ).strftime("%Y-%m-%d")
    target_series = [s.strip() for s in args.series.split(",") if s.strip()] if args.series else None

    if client is None:
        client = KalshiFuturesClient()
    rows, manifest, failed_series = collect_snapshot(
        client=client,
        target_series=target_series,
        dt=dt,
    )

    # Print summary per series
    for ticker, info in sorted(manifest["per_series"].items()):
        if "error" in info:
            print(f"  {ticker:22} FAILED: {info['error']}")
        else:
            print(f"  {ticker:22} {info['events']:3} events, {info['rows']:4} markets ({info['family']}, {info['league']})")

    # Print final total
    print(
        f"Total: {len(manifest['per_series'])} series, "
        f"{manifest['total_rows']} markets across "
        f"{manifest['total_requests']} requests"
    )

    # Save output
    parquet_dst, manifest_dst = save_snapshot(
        rows=rows,
        manifest=manifest,
        dt=dt,
        bucket=args.bucket,
        out_dir=args.out_dir,
    )
    print(f"Saved snapshot -> {parquet_dst}")
    print(f"Saved manifest -> {manifest_dst}")

    if failed_series:
        print(f"WARNING: {len(failed_series)} series failed after retries: {failed_series}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
