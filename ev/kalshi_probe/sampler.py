"""Sampler for the Kalshi pre-game liquidity probe (RUL-20261122 Step 1).

READ-ONLY by construction: only unauthenticated GETs against Kalshi's
public data host (events, markets, orderbook). No order, RFQ, or any other
authenticated / binding call exists in this module.

Window: a game is in window when its scheduled start (the market's
``occurrence_datetime``) is between T-4h and T-15m from the sample time.
HARD STOP: no sampling at or after HARD_STOP_UTC (end of the Nov 23, 2026
slate's windows). After the hard stop the job refuses to run at all.

Banking: append-only JSONL chunks in GCS, one create-only object per run:
  gs://<bucket>/Kalshi/probe/dt=<ET date>/run-<UTC ts>-<run_id>.jsonl
Every run writes a NEW object (``if_generation_match=0``); prior runs are
never overwritten.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from ev.kalshi_probe.classifier import classify, parse_orderbook

USER_AGENT = "Beezy Research (beezy.fyi; research@beezy.fyi)"
API_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# Series under probe: NBA player props + NBA game lines. Game moneyline is
# KXNBAGAME ("NBA Game"), spread KXNBASPREAD, total KXNBATOTAL -- verified
# against the live catalog on 2026-11-22. NHL SOG / NHL saves are excluded
# per RUL-20261122.
SERIES_FAMILIES = {
    "KXNBAPTS": "NBA_POINTS",
    "KXNBAREB": "NBA_REBOUNDS",
    "KXNBA3PT": "NBA_THREES",
    "KXNBAGAME": "NBA_GAME_MONEYLINE",
    "KXNBASPREAD": "NBA_GAME_SPREAD",
    "KXNBATOTAL": "NBA_GAME_TOTAL",
}
TARGET_SERIES = tuple(SERIES_FAMILIES)

WINDOW_OPEN_BEFORE = timedelta(hours=4)     # T-4h
WINDOW_CLOSE_BEFORE = timedelta(minutes=15)  # T-15m

# HARD STOP: end of the Nov 23, 2026 slate's pre-game windows.
HARD_STOP_UTC = datetime(2026, 11, 24, 8, 0, 0, tzinfo=timezone.utc)

DEFAULT_BUCKET = "concrete-crow-445205-m4-mlb-data"
ET = ZoneInfo("America/New_York")


def parse_ts(value):
    """Parse an ISO-8601 timestamp to an aware UTC datetime (or None)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_hard_stopped(now):
    return now >= HARD_STOP_UTC


def in_window(start, now):
    """True when `start` is in [now+T-15m, now+T-4h] (the pre-game window)."""
    if start is None:
        return False
    return now + WINDOW_CLOSE_BEFORE <= start <= now + WINDOW_OPEN_BEFORE


def any_game_in_window(starts, now):
    return any(in_window(s, now) for s in starts)


class KalshiPublicClient:
    """Unauthenticated GET-only client for Kalshi's public data host."""

    def __init__(self, session=None, base=API_BASE, timeout=20):
        self.session = session or requests.Session()
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _get(self, path, params=None):
        resp = self.session.get(
            f"{self.base}{path}", params=params,
            headers={"User-Agent": USER_AGENT}, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def list_events(self, series_ticker, limit=200):
        events, cursor = [], None
        while True:
            params = {"series_ticker": series_ticker, "status": "open",
                      "limit": limit, "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/events", params=params)
            events.extend(data.get("events") or [])
            cursor = data.get("cursor")
            if not cursor or not data.get("events"):
                return events

    def get_orderbook(self, ticker, depth=10):
        return self._get(f"/markets/{ticker}/orderbook",
                         params={"depth": depth})


def market_start(market):
    return parse_ts(market.get("occurrence_datetime")
                    or market.get("expected_expiration_time"))


def build_row(market, series, event, book_payload, now, in_win):
    parsed = parse_orderbook(book_payload)
    start = market_start(market)
    return {
        "ticker": market.get("ticker"),
        "series": series,
        "family": SERIES_FAMILIES.get(series, series),
        "event_ticker": market.get("event_ticker")
        or (event or {}).get("event_ticker"),
        "event_title": (event or {}).get("title"),
        "market_title": market.get("title"),
        "scheduled_start_utc": start.isoformat() if start else None,
        "sample_ts_utc": now.isoformat(),
        "best_bid": parsed["best_bid"],
        "best_ask": parsed["best_ask"],
        "ask_size_contracts": parsed["ask_size_contracts"],
        "ask_dollar_size": parsed["ask_dollar_size"],
        "bid_size_contracts": parsed["bid_size_contracts"],
        "spread": parsed["spread"],
        "fee_multiplier": market.get("fee_multiplier"),
        "fee_type": market.get("fee_type"),
        "yes_ask_dollars": market.get("yes_ask_dollars"),
        "yes_bid_dollars": market.get("yes_bid_dollars"),
        "source": "BOOK",
        "classification": classify(parsed),
        "in_window": bool(in_win),
    }


def discover(client, series_list=TARGET_SERIES):
    """Return [(series, event, market), ...] for all open target markets."""
    found = []
    for series in series_list:
        for event in client.list_events(series):
            for market in event.get("markets") or []:
                found.append((series, event, market))
    return found


def collect_rows(client, now, snapshot_if_empty=False, sleep_s=0.0):
    """Collect sample rows.

    Normal mode: only markets whose game is in the pre-game window.
    Snapshot mode (``snapshot_if_empty`` and nothing in window): every open
    target-series market on the Nov 22/23 ET slates, marked
    ``in_window=False`` -- used once at arming time to prove the banking
    path end to end.
    Returns (rows, mode) where mode is "window" | "snapshot" | "none".
    """
    found = discover(client)
    in_win = [(s, e, m) for (s, e, m) in found
              if in_window(market_start(m), now)]
    if in_win:
        targets, mode = in_win, "window"
    elif snapshot_if_empty:
        target_dates = {"2026-11-22", "2026-11-23"}
        targets = []
        for (s, e, m) in found:
            start = market_start(m)
            if start and start.astimezone(ET).date().isoformat() in target_dates:
                targets.append((s, e, m))
        mode = "snapshot" if targets else "none"
    else:
        return [], "none"
    rows = []
    for (series, event, market) in targets:
        try:
            book = client.get_orderbook(market["ticker"])
        except Exception:
            book = {}
        rows.append(build_row(market, series, event, book, now,
                              in_window(market_start(market), now)))
        if sleep_s:
            time.sleep(sleep_s)
    return rows, mode


def bank_rows(rows, now, bucket=None, run_id=None, storage_client=None):
    """Write rows as a NEW create-only JSONL object; return the gs:// URI."""
    from google.cloud import storage

    bucket_name = bucket or os.environ.get("MLB_GCS_BUCKET") \
        or os.environ.get("GCS_BUCKET") or DEFAULT_BUCKET
    run_id = run_id or uuid.uuid4().hex[:12]
    dt_et = now.astimezone(ET).date().isoformat()
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    name = f"Kalshi/probe/dt={dt_et}/run-{ts}-{run_id}.jsonl"
    client = storage_client or storage.Client()
    blob = client.bucket(bucket_name).blob(name)
    payload = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    blob.upload_from_string(payload, content_type="application/json",
                            if_generation_match=0)
    return f"gs://{bucket_name}/{name}"


def run_probe(now=None, client=None, snapshot_if_empty=False,
              do_bank=True, **bank_kwargs):
    """One probe run. Returns a summary dict.

    Exit semantics are the caller's: hard stop -> {"status": "hard_stop"};
    nothing in window and no snapshot -> {"status": "no_op"} with NO
    orderbook calls made (discovery only decides the no-op).
    """
    now = now or datetime.now(timezone.utc)
    if is_hard_stopped(now):
        return {"status": "hard_stop", "hard_stop_utc": HARD_STOP_UTC.isoformat(),
                "now_utc": now.isoformat(), "rows": 0}
    client = client or KalshiPublicClient()
    rows, mode = collect_rows(client, now,
                              snapshot_if_empty=snapshot_if_empty)
    if not rows:
        return {"status": "no_op", "mode": mode, "rows": 0,
                "now_utc": now.isoformat()}
    uri = bank_rows(rows, now, **bank_kwargs) if do_bank else None
    counts = {}
    for r in rows:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1
    return {"status": "sampled", "mode": mode, "rows": len(rows),
            "classifications": counts, "banked_to": uri,
            "now_utc": now.isoformat()}
