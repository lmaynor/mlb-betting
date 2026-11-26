"""Kalshi wide-spread NBA prop shadow harness, fill simulators, Parquet bank, and scorecard.

RUL-20261125 Half 2 / MAK-01, shadow-only.
Strictly read-only against Kalshi public market data; never submits, amends,
or cancels live orders.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ev.consensus_fv_anchor import AnchorResult, compute_anchor, from_ev_quotes
from ev.kalshi_nba_ticker_mapper import MappedMarket, SERIES_TO_STAT, parse_market
from ev.kalshi_props import kalshi_player_codes
from ev.kalshi_wide_quoter_rules import (
    ACTION_CANCEL,
    ACTION_HALT,
    ACTION_QUOTE_BOTH,
    ACTION_QUOTE_NO,
    ACTION_QUOTE_YES,
    ACTION_SKIP,
    ActiveQuoteView,
    BookState,
    MODE_IMPROVE_1C,
    MODE_JOIN_TOP,
    QuoterState,
    SERIES_FEE_TYPES,
    WideSpreadConfig,
    evaluate_cancel,
    evaluate_quote,
    maker_fee_cents,
)
from ev.maker_settlement import (
    DictResolver,
    Outcome,
    STATUS_LOSS,
    STATUS_WIN,
    settle_fill,
    z_cal,
)
from ev.props import Quote, norm_name, parse_event_props
from mlb.execution.kalshi.book import Book
from mlb.execution.kalshi.client import KalshiClient, PUBLIC_DATA_ENV

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

_EMPTY_STATUS_STRINGS = frozenset({"", "nan", "none"})

OPP_ARROW_SCHEMA = pa.schema(
    [
        # Contract Metadata
        ("opp_id", pa.string()),
        ("ts_utc", pa.timestamp("us", tz="UTC")),
        ("game_id", pa.string()),
        ("tip_ts_utc", pa.timestamp("us", tz="UTC")),
        ("minutes_to_tip", pa.float64()),
        ("kalshi_ticker", pa.string()),
        ("series", pa.string()),
        ("player_id", pa.string()),
        ("line_threshold", pa.float64()),
        # Kalshi Book State
        ("best_yes_bid", pa.int64()),
        ("best_yes_ask", pa.int64()),
        ("yes_bid_size", pa.float64()),
        ("yes_ask_size", pa.float64()),
        ("spread_cents", pa.int64()),
        ("is_two_sided", pa.bool_()),
        ("last_trade_price", pa.int64()),
        ("cum_volume", pa.float64()),
        ("interval_trade_volume", pa.float64()),
        # Consensus Anchor State
        ("fv_yes_prob", pa.float64()),
        ("n_fresh_books", pa.int64()),
        ("max_book_age_sec", pa.float64()),
        ("book_dispersion_pp", pa.float64()),
        ("constituent_books_json", pa.string()),
        # Quoter Output
        ("action", pa.string()),
        ("reason_code", pa.string()),
        ("sim_yes_bid", pa.int64()),
        ("sim_no_bid", pa.int64()),
        ("sim_size", pa.int64()),
        ("yes_queue_mode", pa.string()),
        ("no_queue_mode", pa.string()),
        ("ex_ante_net_edge_yes", pa.float64()),
        ("ex_ante_net_edge_no", pa.float64()),
        ("sim_inventory_net", pa.int64()),
        # Async Post-Fill / Settlement Columns
        ("fill_conservative_qty", pa.float64()),
        ("fill_secondary_qty", pa.float64()),
        ("fill_side", pa.string()),
        ("fill_price", pa.float64()),
        ("fill_ts_utc", pa.timestamp("us", tz="UTC")),
        ("fv_t_plus_15m", pa.float64()),
        ("fv_close_t_minus_5m", pa.float64()),
        ("kalshi_mid_close", pa.float64()),
        ("settled_outcome", pa.string()),
        ("realized_pnl_cents", pa.float64()),
    ]
)


class ReadOnlyViolationError(RuntimeError):
    """Raised when any write or non-GET operation is attempted on GuardedClient."""


class GuardedClient:
    """Strict read-only wrapper around KalshiClient exposing only GET market-data methods."""

    _READ_ONLY_ATTRS = frozenset({"env", "base_url"})

    def __init__(self, client: KalshiClient) -> None:
        object.__setattr__(self, "_client", client)

    def __setattr__(self, name: str, value: Any) -> None:
        raise ReadOnlyViolationError(f"GuardedClient is immutable/read-only; cannot set {name!r}")

    def __getattr__(self, name: str) -> Any:
        if name in self._READ_ONLY_ATTRS:
            return getattr(self._client, name)
        raise ReadOnlyViolationError(
            f"GuardedClient is strictly read-only; access to {name!r} is forbidden"
        )

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if str(method or "").upper() != "GET":
            raise ReadOnlyViolationError(
                f"GuardedClient refuses non-GET request: {method!r} {path!r}"
            )
        kwargs.pop("body", None)
        return self._client.request("GET", path, params=params, **kwargs)

    def get_markets(self, series_ticker: str, status: str = "open") -> list[dict[str, Any]]:
        return self._client.get_markets(series_ticker, status=status)

    def get_orderbook(self, ticker: str) -> Book:
        return self._client.get_orderbook(ticker)

    def get_series(self, series_ticker: str) -> dict[str, Any]:
        return self._client.get_series(series_ticker)

    def get_trades(
        self,
        ticker: str,
        min_ts: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"ticker": ticker, "limit": limit}
        if min_ts is not None:
            params["min_ts"] = int(min_ts)
        data = self.request("GET", "/markets/trades", params=params, auth=False)
        if not isinstance(data, dict):
            return []
        trades = data.get("trades")
        return list(trades) if isinstance(trades, list) else []


def espn_injury_status_map(frame: pd.DataFrame | None) -> dict[str, str]:
    """Extract {player_norm: status} from an espn_injuries frame using pinned pandas 2.2.3 guard.

    Iterates raw values and checks pd.isna() BEFORE str() coercion so None/NaN
    never turns into literal 'None' or 'nan'.
    """
    if frame is None or len(frame) == 0:
        return {}
    if frame.attrs.get("ok") is False:
        return {}
    if "player" not in frame.columns or "status" not in frame.columns:
        return {}

    out: dict[str, str] = {}
    for raw_player, raw_status in zip(frame["player"].tolist(), frame["status"].tolist()):
        if pd.isna(raw_player) or pd.isna(raw_status):
            continue
        p_str = str(raw_player).strip()
        s_str = str(raw_status).strip()
        if p_str.lower() in _EMPTY_STATUS_STRINGS or s_str.lower() in _EMPTY_STATUS_STRINGS:
            continue
        p_norm = norm_name(p_str)
        if p_norm:
            out[p_norm] = s_str
    return out


class ParlaySnapshotProvider:
    """Reads ParlayAPI NBA event JSON files from a directory and builds consensus anchors."""

    def __init__(
        self,
        snapshot_dir: str | Path | None = None,
        cfg: WideSpreadConfig = WideSpreadConfig(),
    ) -> None:
        raw_dir = snapshot_dir if snapshot_dir is not None else os.environ.get("KALSHI_WIDE_SNAPSHOT_DIR")
        self.snapshot_dir = Path(raw_dir) if raw_dir else None
        self.cfg = cfg

    def _load_quotes(self) -> list[Quote]:
        if self.snapshot_dir is None or not self.snapshot_dir.exists():
            return []
        quotes: list[Quote] = []
        for path in sorted(self.snapshot_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("Skipping unreadable snapshot %s: %r", path, exc)
                continue
            events: list[dict[str, Any]]
            if isinstance(payload, list):
                events = [e for e in payload if isinstance(e, dict)]
            elif isinstance(payload, dict):
                if isinstance(payload.get("events"), list):
                    events = [e for e in payload["events"] if isinstance(e, dict)]
                elif isinstance(payload.get("data"), list):
                    events = [e for e in payload["data"] if isinstance(e, dict)]
                else:
                    events = [payload]
            else:
                events = []
            for ev_obj in events:
                parsed_quotes, _ = parse_event_props(ev_obj, sport="nba")
                quotes.extend(parsed_quotes)
        return quotes

    def __call__(self, mapped: MappedMarket, now: datetime) -> AnchorResult:
        quotes = self._load_quotes()
        stat_quotes = [q for q in quotes if q.stat == mapped.stat and abs(float(q.point) - mapped.consensus_line) <= 1e-9]
        if mapped.player_norm:
            matching = [q for q in stat_quotes if q.player_norm == mapped.player_norm]
        else:
            p_code = "".join(ch for ch in mapped.participant_key if not ch.isdigit()).lower()
            matching = [
                q for q in stat_quotes
                if p_code in kalshi_player_codes(q.player_norm, display=q.player)
            ]
        lines = from_ev_quotes(matching, mapped.stat, mapped.consensus_line)
        return compute_anchor(lines, now, self.cfg)


@dataclass(frozen=True)
class OppRow:
    """Single cycle opportunity record matching the 5 spec field groups."""

    opp_id: str
    ts_utc: datetime
    game_id: str
    tip_ts_utc: datetime | None
    minutes_to_tip: float | None
    kalshi_ticker: str
    series: str
    player_id: str | None
    line_threshold: float
    best_yes_bid: int | None
    best_yes_ask: int | None
    yes_bid_size: float
    yes_ask_size: float
    spread_cents: int | None
    is_two_sided: bool
    last_trade_price: int | None
    cum_volume: float
    interval_trade_volume: float
    fv_yes_prob: float | None
    n_fresh_books: int
    max_book_age_sec: float | None
    book_dispersion_pp: float | None
    constituent_books_json: str
    action: str
    reason_code: str
    sim_yes_bid: int | None
    sim_no_bid: int | None
    sim_size: int
    yes_queue_mode: str | None
    no_queue_mode: str | None
    ex_ante_net_edge_yes: float | None
    ex_ante_net_edge_no: float | None
    sim_inventory_net: int
    fill_conservative_qty: float | None = None
    fill_secondary_qty: float | None = None
    fill_side: str | None = None
    fill_price: float | None = None
    fill_ts_utc: datetime | None = None
    fv_t_plus_15m: float | None = None
    fv_close_t_minus_5m: float | None = None
    kalshi_mid_close: float | None = None
    settled_outcome: str | None = None
    realized_pnl_cents: float | None = None


@dataclass
class _RestingQuote:
    side: str
    bid_c: int
    mode: str
    placed_ts: datetime
    initial_best_bid_size: float
    sim_size: int
    remaining: float
    sec_remaining: float
    cum_at_price: float = 0.0
    credited_excess: float = 0.0


@dataclass(frozen=True)
class _ParsedTrade:
    trade_id: str
    yes_price_c: int
    no_price_c: int
    size: float
    ts_utc: datetime


def _to_utc(dt: datetime | str | None) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    if isinstance(dt, (int, float)):
        val = float(dt)
        if val > 1e12:
            val /= 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc)
    if isinstance(dt, str):
        txt = dt.strip()
        if not txt:
            return None
        try:
            parsed = datetime.fromisoformat(txt.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _parse_trade(raw: dict[str, Any], fallback_ts: datetime) -> _ParsedTrade | None:
    if not isinstance(raw, dict):
        return None
    yes_c: int | None = None
    no_c: int | None = None
    if raw.get("yes_price") is not None:
        yes_c = int(round(float(raw["yes_price"])))
    elif raw.get("yes_price_dollars") is not None:
        yes_c = int(round(float(raw["yes_price_dollars"]) * 100.0))

    if raw.get("no_price") is not None:
        no_c = int(round(float(raw["no_price"])))
    elif raw.get("no_price_dollars") is not None:
        no_c = int(round(float(raw["no_price_dollars"]) * 100.0))

    if yes_c is None and no_c is not None:
        yes_c = 100 - no_c
    elif no_c is None and yes_c is not None:
        no_c = 100 - yes_c
    if yes_c is None or no_c is None:
        return None

    size_raw = raw.get("count_fp") if raw.get("count_fp") is not None else raw.get("count")
    if size_raw is None:
        size_raw = raw.get("size")
    try:
        size = float(size_raw)
    except (TypeError, ValueError):
        return None
    if size <= 0.0:
        return None

    ts_utc = _to_utc(raw.get("created_time") if raw.get("created_time") is not None else raw.get("ts"))
    if ts_utc is None:
        ts_utc = fallback_ts
    tid = str(raw.get("trade_id") or f"{ts_utc.isoformat()}:{yes_c}:{size}")
    return _ParsedTrade(trade_id=tid, yes_price_c=yes_c, no_price_c=no_c, size=size, ts_utc=ts_utc)


def _book_to_cents(book: Book) -> tuple[BookState, int | None, float]:
    by = book.best_bid("yes")
    bn = book.best_bid("no")
    by_c = int(round(by * 100.0)) if by is not None else None
    bn_c = int(round(bn * 100.0)) if bn is not None else None
    yes_bid_size = float(book.yes_bids[-1][1]) if book.yes_bids else 0.0
    no_bid_size = float(book.no_bids[-1][1]) if book.no_bids else 0.0
    yes_ask_c = (100 - bn_c) if bn_c is not None else None
    yes_ask_size = no_bid_size
    return BookState(
        best_yes_bid_c=by_c,
        best_no_bid_c=bn_c,
        yes_bid_size=yes_bid_size,
        no_bid_size=no_bid_size,
    ), yes_ask_c, yes_ask_size


class ShadowHarness:
    """30-second cycle shadow harness driving quoter rules and dual fill simulators."""

    def __init__(
        self,
        client: GuardedClient,
        anchor_provider: Callable[[MappedMarket, datetime], AnchorResult],
        injury_provider: Callable[[str], str | None] | None = None,
        tip_ts_provider: Callable[[MappedMarket, dict[str, Any]], datetime | None] | None = None,
        player_resolver: Callable[[str, str], Any] | None = None,
        cfg: WideSpreadConfig = WideSpreadConfig(),
        series_list: Sequence[str] = tuple(SERIES_FEE_TYPES.keys()),
    ) -> None:
        self.client = client
        self.anchor_provider = anchor_provider
        self.injury_provider = injury_provider
        self.tip_ts_provider = tip_ts_provider
        self.player_resolver = player_resolver
        self.cfg = cfg
        self.series_list = tuple(series_list)

        self._resting: dict[tuple[str, str], _RestingQuote] = {}
        self._prev_fv: dict[str, float] = {}
        self._fv_history: dict[str, list[tuple[datetime, float]]] = {}
        self._prev_status: dict[str, str | None] = {}
        self._cooldown_until: dict[str, datetime] = {}

        self._market_net: dict[str, int] = {}
        self._market_open_risk_usd: dict[str, float] = {}
        self._player_open_risk_usd: dict[str, float] = {}
        self._game_open_risk_usd: dict[str, float] = {}
        self._daily_open_risk_usd: float = 0.0
        self._daily_realized_loss_usd: float = 0.0

        self._seen_trades: dict[str, set[str]] = {}
        self._last_trade_ts: dict[str, int] = {}
        self._cum_volume: dict[str, float] = {}
        self._last_trade_price: dict[str, int | None] = {}

        # Async-column capture: fills whose fv_t_plus_15m / closing snapshots are still owed.
        self._fill_watch: dict[str, dict[str, Any]] = {}
        self._pending_updates: list[dict[str, Any]] = []

    def drain_updates(self) -> list[dict[str, Any]]:
        """Return (and clear) async-column updates captured since the last drain.

        Each update is {"opp_id": ..., optionally fv_t_plus_15m / fv_close_t_minus_5m /
        kalshi_mid_close} ready to pass to finalize_day(..., updates=...).
        """
        out = self._pending_updates
        self._pending_updates = []
        return out

    def _capture_async_columns(
        self,
        ticker: str,
        curr_fv: float | None,
        minutes_to_tip: float | None,
        book_obj: Book,
        now_utc: datetime,
    ) -> None:
        """Fill owed fv_t_plus_15m / T-5m close snapshots for earlier fills on this ticker."""
        for oid, w in list(self._fill_watch.items()):
            if w["ticker"] != ticker:
                continue
            upd: dict[str, Any] = {"opp_id": oid}
            if (
                not w["fv15_done"]
                and curr_fv is not None
                and now_utc >= w["fill_ts"] + timedelta(minutes=15)
            ):
                upd["fv_t_plus_15m"] = curr_fv
                w["fv15_done"] = True
            if (
                not w["close_done"]
                and curr_fv is not None
                and minutes_to_tip is not None
                and minutes_to_tip <= 5.0
            ):
                upd["fv_close_t_minus_5m"] = curr_fv
                mid = book_obj.yes_mid()
                if mid is not None:
                    upd["kalshi_mid_close"] = mid
                w["close_done"] = True
            if len(upd) > 1:
                self._pending_updates.append(upd)
            if w["fv15_done"] and w["close_done"]:
                self._fill_watch.pop(oid, None)

    def _record_primary_fill(
        self,
        ticker: str,
        player_key: str,
        game_id: str,
        side: str,
        qty: float,
        price_c: int,
    ) -> None:
        if qty <= 0:
            return
        qty_int = int(round(qty))
        prev_net = self._market_net.get(ticker, 0)
        new_net = prev_net + qty_int if side == "yes" else prev_net - qty_int
        self._market_net[ticker] = new_net

        # Approximate open risk change: adding to net increases open risk; reducing decreases.
        old_risk = self._market_open_risk_usd.get(ticker, 0.0)
        if abs(new_net) > abs(prev_net):
            added_contracts = abs(new_net) - abs(prev_net)
            delta_risk = added_contracts * (price_c / 100.0)
        elif abs(new_net) == 0:
            delta_risk = -old_risk
        else:
            frac_kept = abs(new_net) / max(1, abs(prev_net))
            delta_risk = old_risk * frac_kept - old_risk

        self._market_open_risk_usd[ticker] = max(0.0, old_risk + delta_risk)
        self._player_open_risk_usd[player_key] = max(
            0.0, self._player_open_risk_usd.get(player_key, 0.0) + delta_risk
        )
        self._game_open_risk_usd[game_id] = max(
            0.0, self._game_open_risk_usd.get(game_id, 0.0) + delta_risk
        )
        self._daily_open_risk_usd = max(0.0, self._daily_open_risk_usd + delta_risk)

    def _resolve_tip_ts(self, mapped: MappedMarket, market: dict[str, Any]) -> datetime | None:
        if self.tip_ts_provider is not None:
            dt = self.tip_ts_provider(mapped, market)
            if dt is not None:
                return _to_utc(dt)
        for key in ("occurrence_datetime", "tip_ts_utc", "commence_time", "expected_expiration_time", "close_time"):
            dt = _to_utc(market.get(key))
            if dt is not None:
                return dt
        return None

    def _fv_move_pp_5m(self, ticker: str, now_utc: datetime, current_fv: float | None) -> float | None:
        hist = self._fv_history.setdefault(ticker, [])
        cutoff = now_utc - timedelta(minutes=self.cfg.fv_jump_window_min)
        hist[:] = [(ts, fv) for ts, fv in hist if ts >= cutoff]
        if current_fv is None:
            return None
        move_pp = 0.0
        for _, old_fv in hist:
            diff = (current_fv - old_fv) * 100.0
            if abs(diff) > abs(move_pp):
                move_pp = diff
        hist.append((now_utc, current_fv))
        return move_pp if len(hist) >= 2 else None

    def _simulate_trade_fills(
        self,
        resting: _RestingQuote,
        trades: list[_ParsedTrade],
    ) -> tuple[float, float, datetime | None]:
        """Apply Primary Conservative and Secondary trade-based fill rules for one resting quote."""
        cons_filled = 0.0
        sec_filled = 0.0
        fill_ts: datetime | None = None
        denom = resting.initial_best_bid_size + resting.sim_size
        join_frac = (resting.sim_size / denom) if denom > 0.0 else (1.0 if resting.sim_size > 0 else 0.0)

        for tr in trades:
            if tr.ts_utc < resting.placed_ts:
                continue
            trade_price_c = tr.yes_price_c if resting.side == "yes" else tr.no_price_c
            if trade_price_c > resting.bid_c:
                continue

            # Secondary (diagnostic): no 5s denial; touch on IMPROVE_1C, proportional on JOIN_TOP
            if resting.sec_remaining > 0.0:
                if resting.mode == MODE_IMPROVE_1C:
                    sec_take = min(resting.sec_remaining, tr.size)
                else:
                    if trade_price_c < resting.bid_c:
                        sec_take = min(resting.sec_remaining, tr.size)
                    else:
                        sec_take = min(resting.sec_remaining, tr.size * join_frac)
                if sec_take > 0.0:
                    resting.sec_remaining -= sec_take
                    sec_filled += sec_take
                    fill_ts = fill_ts or tr.ts_utc

            # Primary Conservative (binding): deny benign fills < latency_denial_s after placement
            age_s = (tr.ts_utc - resting.placed_ts).total_seconds()
            if age_s < self.cfg.latency_denial_s:
                continue

            if resting.remaining > 0.0:
                if resting.mode == MODE_IMPROVE_1C:
                    prim_take = min(resting.remaining, tr.size)
                else:
                    if trade_price_c < resting.bid_c:
                        prim_take = min(resting.remaining, tr.size)
                    else:
                        resting.cum_at_price += tr.size
                        queue_clear = resting.initial_best_bid_size + resting.sim_size
                        total_excess = max(0.0, resting.cum_at_price - queue_clear)
                        new_excess = max(0.0, total_excess - resting.credited_excess)
                        prim_take = min(resting.remaining, new_excess)
                        resting.credited_excess += prim_take
                if prim_take > 0.0:
                    resting.remaining -= prim_take
                    cons_filled += prim_take
                    fill_ts = tr.ts_utc

        return cons_filled, sec_filled, fill_ts

    def run_once(self, now: datetime) -> list[OppRow]:
        """Execute one 30-second shadow evaluation cycle across open markets in the 3 series."""
        now_utc = _to_utc(now)
        assert now_utc is not None
        rows: list[OppRow] = []

        markets: list[dict[str, Any]] = []
        for series in self.series_list:
            try:
                markets.extend(self.client.get_markets(series, status="open"))
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed fetching markets for %s: %r", series, exc)

        for mk in markets:
            try:
                mapped = parse_market(mk, resolver=self.player_resolver)
            except ValueError as exc:
                log.warning("Rejected/unmapped Kalshi ticker %r: %s", mk.get("ticker"), exc)
                continue

            ticker = mapped.ticker
            game_id = f"nba:{mapped.event_date.isoformat()}:{mapped.away}@{mapped.home}"
            player_key = mapped.identity_key
            tip_ts = self._resolve_tip_ts(mapped, mk)
            minutes_to_tip = (tip_ts - now_utc).total_seconds() / 60.0 if tip_ts is not None else None

            book_obj = self.client.get_orderbook(ticker)
            book_state, yes_ask_c, yes_ask_size = _book_to_cents(book_obj)
            spread_c = (
                (yes_ask_c - int(book_state.best_yes_bid_c))
                if (yes_ask_c is not None and book_state.best_yes_bid_c is not None)
                else None
            )
            two_sided = book_obj.is_two_sided() and not book_obj.is_crossed()

            min_ts = self._last_trade_ts.get(ticker)
            raw_trades = self.client.get_trades(ticker, min_ts=min_ts)
            seen = self._seen_trades.setdefault(ticker, set())
            new_trades: list[_ParsedTrade] = []
            for rt in raw_trades:
                pt = _parse_trade(rt, fallback_ts=now_utc)
                if pt is None or pt.trade_id in seen:
                    continue
                seen.add(pt.trade_id)
                new_trades.append(pt)
                self._last_trade_price[ticker] = pt.yes_price_c
                ts_epoch = int(pt.ts_utc.timestamp())
                if min_ts is None or ts_epoch > min_ts:
                    self._last_trade_ts[ticker] = ts_epoch
            new_trades.sort(key=lambda t: t.ts_utc)
            interval_vol = sum(t.size for t in new_trades)
            self._cum_volume[ticker] = self._cum_volume.get(ticker, 0.0) + interval_vol

            anchor = self.anchor_provider(mapped, now_utc)
            curr_fv = anchor.fv_yes_prob if anchor.ok else None
            prev_fv = self._prev_fv.get(ticker)
            fv_move_5m = self._fv_move_pp_5m(ticker, now_utc, curr_fv)

            status = self.injury_provider(mapped.player_norm) if self.injury_provider is not None else None
            prev_status = self._prev_status.get(player_key)
            self._prev_status[player_key] = status

            fill_cons_qty: float | None = None
            fill_sec_qty: float | None = None
            fill_side: str | None = None
            fill_price: float | None = None
            fill_ts_utc: datetime | None = None

            # 1. Toxic-fill check FIRST for resting quotes (before cancel evaluation)
            if curr_fv is not None and prev_fv is not None:
                delta_pp = (curr_fv - prev_fv) * 100.0
                for side in ("yes", "no"):
                    rq = self._resting.get((ticker, side))
                    if rq is None or rq.remaining <= 0.0:
                        continue
                    adverse = (delta_pp <= -self.cfg.fv_jump_pp) if side == "yes" else (delta_pp >= self.cfg.fv_jump_pp)
                    if adverse:
                        forced = rq.remaining
                        rq.remaining = 0.0
                        self._resting.pop((ticker, side), None)
                        self._record_primary_fill(ticker, player_key, game_id, side, forced, rq.bid_c)
                        if fill_cons_qty is None:
                            fill_cons_qty = forced
                            fill_sec_qty = 0.0
                            fill_side = side
                            fill_price = float(rq.bid_c)
                            fill_ts_utc = now_utc

            if curr_fv is not None:
                self._prev_fv[ticker] = curr_fv

            # Async-column capture for fills banked in earlier cycles on this ticker
            self._capture_async_columns(ticker, curr_fv, minutes_to_tip, book_obj, now_utc)

            # 2. Cancel evaluation for remaining resting quotes
            cancelled_this_cycle = False
            cancel_reason = ""
            has_resting = any(
                (ticker, s) in self._resting and self._resting[(ticker, s)].remaining > 0.0
                for s in ("yes", "no")
            )
            if has_resting and tip_ts is not None:
                rq_y = self._resting.get((ticker, "yes"))
                rq_n = self._resting.get((ticker, "no"))
                view = ActiveQuoteView(
                    best_yes_bid_c=book_state.best_yes_bid_c,
                    best_no_bid_c=book_state.best_no_bid_c,
                    yes_bid_c=rq_y.bid_c if rq_y else None,
                    no_bid_c=rq_n.bid_c if rq_n else None,
                )
                c_dec = evaluate_cancel(
                    view,
                    anchor.ok,
                    anchor.reason,
                    prev_status,
                    status,
                    fv_move_5m,
                    now_utc,
                    tip_ts,
                    self.cfg,
                )
                if c_dec.cancel:
                    cancelled_this_cycle = True
                    cancel_reason = c_dec.reason_code
                    self._resting.pop((ticker, "yes"), None)
                    self._resting.pop((ticker, "no"), None)
                    if c_dec.cooldown_until is not None:
                        self._cooldown_until[player_key] = c_dec.cooldown_until

            # 3. Trade-based fills for surviving resting quotes
            for side in ("yes", "no"):
                rq = self._resting.get((ticker, side))
                if rq is None:
                    continue
                c_qty, s_qty, tr_ts = self._simulate_trade_fills(rq, new_trades)
                if c_qty > 0.0:
                    self._record_primary_fill(ticker, player_key, game_id, side, c_qty, rq.bid_c)
                if (c_qty > 0.0 or s_qty > 0.0) and fill_side is None:
                    fill_cons_qty = c_qty
                    fill_sec_qty = s_qty
                    fill_side = side
                    fill_price = float(rq.bid_c)
                    fill_ts_utc = tr_ts
                if rq.remaining <= 0.0:
                    self._resting.pop((ticker, side), None)

            # 4. New quote decision
            q_state = QuoterState(
                market_net_contracts=self._market_net.get(ticker, 0),
                player_open_risk_usd=self._player_open_risk_usd.get(player_key, 0.0),
                game_open_risk_usd=self._game_open_risk_usd.get(game_id, 0.0),
                daily_open_risk_usd=self._daily_open_risk_usd,
                daily_realized_loss_usd=self._daily_realized_loss_usd,
                cooldown_until=self._cooldown_until.get(player_key),
                series=mapped.series,
            )
            if tip_ts is None:
                q_dec = evaluate_quote(
                    book_state,
                    None,
                    q_state,
                    now_utc,
                    now_utc + timedelta(minutes=self.cfg.window_open_min),
                    self.cfg,
                    series=mapped.series,
                )
                # Override reason when tip_ts is missing
                q_dec = type(q_dec)(action=ACTION_SKIP, reason_code="missing_tip_ts")
            else:
                q_dec = evaluate_quote(
                    book_state,
                    curr_fv,
                    q_state,
                    now_utc,
                    tip_ts,
                    self.cfg,
                    series=mapped.series,
                )

            # Update resting quotes for next cycle
            desired_sides: dict[str, tuple[int, str, float]] = {}
            if q_dec.action in (ACTION_QUOTE_BOTH, ACTION_QUOTE_YES) and q_dec.yes_bid_c is not None and q_dec.yes_mode is not None:
                desired_sides["yes"] = (q_dec.yes_bid_c, q_dec.yes_mode, float(book_state.yes_bid_size))
            if q_dec.action in (ACTION_QUOTE_BOTH, ACTION_QUOTE_NO) and q_dec.no_bid_c is not None and q_dec.no_mode is not None:
                desired_sides["no"] = (q_dec.no_bid_c, q_dec.no_mode, float(book_state.no_bid_size))

            for side in ("yes", "no"):
                if side not in desired_sides:
                    self._resting.pop((ticker, side), None)
                else:
                    bid_c, mode, init_sz = desired_sides[side]
                    existing = self._resting.get((ticker, side))
                    if existing is None or existing.bid_c != bid_c:
                        self._resting[(ticker, side)] = _RestingQuote(
                            side=side,
                            bid_c=bid_c,
                            mode=mode,
                            placed_ts=now_utc,
                            initial_best_bid_size=init_sz,
                            sim_size=q_dec.size,
                            remaining=float(q_dec.size),
                            sec_remaining=float(q_dec.size),
                        )

            out_action = q_dec.action
            out_reason = q_dec.reason_code
            if not anchor.ok and q_dec.reason_code == "no_anchor":
                out_reason = anchor.reason
            if cancelled_this_cycle and q_dec.action == ACTION_SKIP:
                out_action = ACTION_CANCEL
                out_reason = cancel_reason

            books_json = json.dumps(
                [[b, round(p, 6), w] for b, p, w in anchor.books_used],
                separators=(",", ":"),
            )
            opp_id = f"{ticker}:{now_utc.strftime('%Y%m%dT%H%M%SZ')}"

            rows.append(
                OppRow(
                    opp_id=opp_id,
                    ts_utc=now_utc,
                    game_id=game_id,
                    tip_ts_utc=tip_ts,
                    minutes_to_tip=minutes_to_tip,
                    kalshi_ticker=ticker,
                    series=mapped.series,
                    player_id=player_key,
                    line_threshold=mapped.consensus_line,
                    best_yes_bid=int(book_state.best_yes_bid_c) if book_state.best_yes_bid_c is not None else None,
                    best_yes_ask=yes_ask_c,
                    yes_bid_size=book_state.yes_bid_size,
                    yes_ask_size=yes_ask_size,
                    spread_cents=spread_c,
                    is_two_sided=two_sided,
                    last_trade_price=self._last_trade_price.get(ticker),
                    cum_volume=self._cum_volume.get(ticker, 0.0),
                    interval_trade_volume=interval_vol,
                    fv_yes_prob=anchor.fv_yes_prob,
                    n_fresh_books=anchor.n_fresh_books,
                    max_book_age_sec=anchor.max_book_age_sec,
                    book_dispersion_pp=anchor.dispersion_pp,
                    constituent_books_json=books_json,
                    action=out_action,
                    reason_code=out_reason,
                    sim_yes_bid=q_dec.yes_bid_c,
                    sim_no_bid=q_dec.no_bid_c,
                    sim_size=q_dec.size,
                    yes_queue_mode=q_dec.yes_mode,
                    no_queue_mode=q_dec.no_mode,
                    ex_ante_net_edge_yes=q_dec.ex_ante_net_edge_yes,
                    ex_ante_net_edge_no=q_dec.ex_ante_net_edge_no,
                    sim_inventory_net=self._market_net.get(ticker, 0),
                    fill_conservative_qty=fill_cons_qty,
                    fill_secondary_qty=fill_sec_qty,
                    fill_side=fill_side,
                    fill_price=fill_price,
                    fill_ts_utc=fill_ts_utc,
                )
            )

            if fill_cons_qty is not None and fill_cons_qty > 0.0 and fill_side is not None:
                self._fill_watch[opp_id] = {
                    "ticker": ticker,
                    "fill_ts": fill_ts_utc or now_utc,
                    "fv15_done": False,
                    "close_done": False,
                }
        return rows


def rows_to_table(rows: Sequence[OppRow | Mapping[str, Any]]) -> pa.Table:
    """Convert OppRow/dict sequence to an arrow Table with the strict shadow schema."""
    records = [asdict(r) if isinstance(r, OppRow) else dict(r) for r in rows]
    if not records:
        return OPP_ARROW_SCHEMA.empty_table()
    return pa.Table.from_pylist(records, schema=OPP_ARROW_SCHEMA)


def write_part(
    rows: Sequence[OppRow | Mapping[str, Any]],
    out_dir: str | Path,
    dt: str | date,
    run_id: str,
    seq: int,
) -> Path:
    """Append-only Parquet part writer; create-only names, never overwrites existing parts."""
    dt_str = dt.isoformat() if isinstance(dt, date) else str(dt)
    part_dir = Path(out_dir) / f"dt={dt_str}"
    part_dir.mkdir(parents=True, exist_ok=True)
    part_path = part_dir / f"part-{run_id}-{seq}.parquet"
    if part_path.exists():
        raise FileExistsError(f"Refusing to overwrite immutable part file: {part_path}")
    table = rows_to_table(rows)
    tmp_path = part_dir / f".tmp-{run_id}-{seq}-{uuid.uuid4().hex}.parquet"
    pq.write_table(table, tmp_path)
    tmp_path.replace(part_path)
    return part_path


def finalize_day(
    out_dir: str | Path,
    dt: str | date,
    updates: pd.DataFrame | Sequence[Mapping[str, Any]] | None = None,
) -> Path:
    """Merge immutable part files (and any prior finalized rows) into opportunities-<date>.parquet."""
    dt_str = dt.isoformat() if isinstance(dt, date) else str(dt)
    base_dir = Path(out_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    part_dir = base_dir / f"dt={dt_str}"
    final_path = base_dir / f"opportunities-{dt_str}.parquet"

    frames: list[pd.DataFrame] = []
    if final_path.exists():
        frames.append(pd.read_parquet(final_path))
    if part_dir.exists():
        for pf in sorted(part_dir.glob("part-*.parquet")):
            frames.append(pd.read_parquet(pf))

    if frames:
        merged = pd.concat(frames, ignore_index=True)
        merged = merged.drop_duplicates(subset=["opp_id"], keep="first").reset_index(drop=True)
    else:
        merged = OPP_ARROW_SCHEMA.empty_table().to_pandas()

    if updates is not None:
        upd_df = updates.copy() if isinstance(updates, pd.DataFrame) else pd.DataFrame(list(updates))
        if len(upd_df) > 0 and "opp_id" in upd_df.columns and len(merged) > 0:
            async_cols = [
                "fill_conservative_qty",
                "fill_secondary_qty",
                "fill_side",
                "fill_price",
                "fill_ts_utc",
                "fv_t_plus_15m",
                "fv_close_t_minus_5m",
                "kalshi_mid_close",
                "settled_outcome",
                "realized_pnl_cents",
            ]
            # Multiple update dicts may target the same opp_id (e.g. fv_t_plus_15m in one
            # cycle, the T-5m close in another): collapse per opp_id keeping the last
            # non-null value PER COLUMN instead of dropping whole update rows.
            collapsed: dict[str, dict[str, Any]] = {}
            for _, urow in upd_df.iterrows():
                entry = collapsed.setdefault(urow["opp_id"], {})
                for col in async_cols:
                    if col in upd_df.columns and not pd.isna(urow.get(col)):
                        entry[col] = urow[col]
            upd_idx = pd.DataFrame.from_dict(collapsed, orient="index")
            for col in async_cols:
                if col not in upd_idx.columns:
                    continue
                mapped_vals = merged["opp_id"].map(upd_idx[col])
                mask = mapped_vals.notna()
                if mask.any():
                    merged.loc[mask, col] = mapped_vals[mask]

    table = pa.Table.from_pandas(merged, schema=OPP_ARROW_SCHEMA, preserve_index=False)
    tmp_path = base_dir / f".tmp-opportunities-{dt_str}-{uuid.uuid4().hex}.parquet"
    pq.write_table(table, tmp_path)
    tmp_path.replace(final_path)
    return final_path


def outcome_from_market(market: Mapping[str, Any]) -> str | None:
    """Extract official Kalshi settlement ('yes'|'no'|None) from a market object."""
    raw = str(market.get("result") or "").strip().lower()
    if raw in ("yes", "no"):
        return raw
    return None


def settle_bank(
    finalized_df: pd.DataFrame,
    outcomes: Mapping[str, str],
) -> tuple[pd.DataFrame, float | None, int]:
    """Backfill settled_outcome and realized_pnl_cents for filled rows via ev.maker_settlement.

    Returns (updated_df, z_cal_value, z_cal_n).
    """
    df = finalized_df.copy()
    if len(df) == 0:
        return df, None, 0

    resolver_map: dict[str, Outcome] = {}
    fills_to_settle: list[tuple[int, dict[str, Any]]] = []

    for idx in df.index:
        qty_raw = df.at[idx, "fill_conservative_qty"]
        if pd.isna(qty_raw) or float(qty_raw) <= 0.0:
            continue
        ticker = str(df.at[idx, "kalshi_ticker"])
        outcome_raw = outcomes.get(ticker)
        if outcome_raw is None:
            continue
        outcome_str = str(outcome_raw).strip().lower()
        if outcome_str not in ("yes", "no"):
            continue

        side = str(df.at[idx, "fill_side"] or "").strip().lower()
        if side not in ("yes", "no"):
            continue
        fill_price_c = float(df.at[idx, "fill_price"])
        qty = float(qty_raw)
        opp_id = str(df.at[idx, "opp_id"])
        series = str(df.at[idx, "series"])

        won = outcome_str == side
        resolver_map[opp_id] = Outcome(
            settlement_price=1.0 if won else 0.0,
            status=STATUS_WIN if won else STATUS_LOSS,
            source="kalshi_market_result",
            detail=f"result={outcome_str} side={side}",
            stat_value=None,
        )

        fv_close_raw = df.at[idx, "fv_close_t_minus_5m"]
        p_cons_close: float | None = None
        if not pd.isna(fv_close_raw):
            fv_yes = float(fv_close_raw)
            p_cons_close = fv_yes if side == "yes" else (1.0 - fv_yes)

        fills_to_settle.append(
            (
                idx,
                {
                    "order_id": opp_id,
                    "sport": "nba",
                    "stat": SERIES_TO_STAT.get(series, "points"),
                    "market_ticker": ticker,
                    "side": side,
                    "p_fill": fill_price_c / 100.0,
                    "fill_qty_units": qty,
                    "p_cons_close": p_cons_close,
                },
            )
        )

    resolver = DictResolver(resolver_map)
    ledger_records: list[dict[str, Any]] = []
    for idx, fill_dict in fills_to_settle:
        rec = settle_fill(fill_dict, resolver=resolver, venue="kalshi", mode="SHADOW")
        ledger_records.append(rec)
        ticker = str(df.at[idx, "kalshi_ticker"])
        df.at[idx, "settled_outcome"] = str(outcomes[ticker]).strip().lower()
        if rec["net_realized_pnl_usd"] is not None:
            df.at[idx, "realized_pnl_cents"] = float(rec["net_realized_pnl_usd"]) * 100.0

    z_val, z_n = z_cal(ledger_records)
    return df, z_val, z_n


def _et_slate(row: pd.Series) -> str | None:
    raw = row.get("tip_ts_utc")
    if pd.isna(raw):
        raw = row.get("ts_utc")
    dt = _to_utc(raw.to_pydatetime() if hasattr(raw, "to_pydatetime") else raw)
    return dt.astimezone(ET).date().isoformat() if dt is not None else None


def _signed_side_fv(fv_yes: float, side: str) -> float:
    return fv_yes if side == "yes" else (1.0 - fv_yes)


def _mean_and_tstat(vals: list[float]) -> tuple[float | None, float | None]:
    if not vals:
        return None, None
    mean_v = sum(vals) / len(vals)
    if len(vals) < 2:
        return mean_v, None
    var = sum((v - mean_v) ** 2 for v in vals) / (len(vals) - 1)
    if var <= 0.0:
        return mean_v, math.inf if mean_v > 0 else (-math.inf if mean_v < 0 else 0.0)
    se = math.sqrt(var / len(vals))
    return mean_v, mean_v / se


def scorecard(
    df: pd.DataFrame,
    cfg: WideSpreadConfig = WideSpreadConfig(),
) -> dict[str, Any]:
    """Compute spec section 3 metrics and evaluate section 4 graduation/kill bars."""
    if df is None or len(df) == 0:
        return {
            "n_rows": 0,
            "n_slates": 0,
            "n_fills": 0,
            "total_contracts": 0.0,
            "fills_per_slate": 0.0,
            "contracts_per_slate": 0.0,
            "active_quote_hours": 0.0,
            "fills_per_100_quote_hours": None,
            "closing_markout_c": None,
            "closing_markout_t_stat": None,
            "closing_markout_vs_mid_c": None,
            "pickoff_drift_pp": None,
            "settled_pnl_cents": 0.0,
            "roi_pct": None,
            "z_cal": None,
            "z_cal_n": 0,
            "by_series": {},
            "by_queue_mode": {},
            "skip_funnel": {},
            "skip_rate_staleness_quorum": None,
            "bars": {"window": "PENDING"},
            "kill_triggers": [],
            "series_drops": [],
            "overall_status": "PENDING",
        }

    work = df.copy()
    work["_slate"] = work.apply(_et_slate, axis=1)
    n_slates = int(work["_slate"].dropna().nunique())

    active_mask = work["action"].isin([ACTION_QUOTE_BOTH, ACTION_QUOTE_YES, ACTION_QUOTE_NO])
    active_quote_hours = float(active_mask.sum()) * (cfg.poll_interval_s / 3600.0)

    fills_mask = work["fill_conservative_qty"].notna() & (work["fill_conservative_qty"] > 0)
    fills = work[fills_mask]
    n_fills = int(len(fills))
    total_contracts = float(fills["fill_conservative_qty"].sum()) if n_fills > 0 else 0.0
    fills_per_slate = n_fills / n_slates if n_slates > 0 else 0.0
    contracts_per_slate = total_contracts / n_slates if n_slates > 0 else 0.0
    fills_per_100_qh = (n_fills / active_quote_hours) * 100.0 if active_quote_hours > 0.0 else None

    markouts: list[float] = []
    markouts_vs_mid: list[float] = []
    drifts: list[float] = []
    zcal_records: list[dict[str, Any]] = []
    settled_pnl_cents = 0.0
    collateral_cents = 0.0
    n_unresolved_filled = 0

    by_series_rows: dict[str, dict[str, Any]] = {}
    by_mode_rows: dict[str, dict[str, Any]] = {}

    for _, r in fills.iterrows():
        side = str(r.get("fill_side") or "").lower()
        if side not in ("yes", "no"):
            continue
        fill_p = float(r["fill_price"])
        qty = float(r["fill_conservative_qty"])
        series = str(r.get("series") or "")
        fee_c = maker_fee_cents(fill_p, fee_type=SERIES_FEE_TYPES.get(series, "quadratic"))

        q_mode = r.get("yes_queue_mode") if side == "yes" else r.get("no_queue_mode")
        q_mode_key = str(q_mode) if not pd.isna(q_mode) and q_mode else "UNKNOWN"

        s_bucket = by_series_rows.setdefault(
            series, {"n_fills": 0, "contracts": 0.0, "markouts": [], "drifts": []}
        )
        s_bucket["n_fills"] += 1
        s_bucket["contracts"] += qty

        m_bucket = by_mode_rows.setdefault(
            q_mode_key, {"n_fills": 0, "contracts": 0.0, "markouts": []}
        )
        m_bucket["n_fills"] += 1
        m_bucket["contracts"] += qty

        fv_close = r.get("fv_close_t_minus_5m")
        p_cons_close: float | None = None
        if not pd.isna(fv_close):
            side_close_prob = _signed_side_fv(float(fv_close), side)
            p_cons_close = side_close_prob
            mo = side_close_prob * 100.0 - fill_p - fee_c
            markouts.append(mo)
            s_bucket["markouts"].append(mo)
            m_bucket["markouts"].append(mo)

        mid_close = r.get("kalshi_mid_close")
        if not pd.isna(mid_close):
            side_mid_prob = _signed_side_fv(float(mid_close), side)
            markouts_vs_mid.append(side_mid_prob * 100.0 - fill_p - fee_c)

        fv_fill = r.get("fv_yes_prob")
        fv_15m = r.get("fv_t_plus_15m")
        if not pd.isna(fv_fill) and not pd.isna(fv_15m):
            p_fill_side = _signed_side_fv(float(fv_fill), side)
            p_15m_side = _signed_side_fv(float(fv_15m), side)
            drift_pp = (p_15m_side - p_fill_side) * 100.0
            drifts.append(drift_pp)
            s_bucket["drifts"].append(drift_pp)

        settled_out = r.get("settled_outcome")
        realized = r.get("realized_pnl_cents")
        if not pd.isna(settled_out) and str(settled_out).lower() in ("yes", "no"):
            won = str(settled_out).lower() == side
            if not pd.isna(realized):
                settled_pnl_cents += float(realized)
                collateral_cents += fill_p * qty
            zcal_records.append(
                {
                    "settlement_status": STATUS_WIN if won else STATUS_LOSS,
                    "settlement_price": 1.0 if won else 0.0,
                    "consensus_close": p_cons_close,
                }
            )
        else:
            n_unresolved_filled += 1

    mean_mo, mo_t = _mean_and_tstat(markouts)
    mean_mo_mid, _ = _mean_and_tstat(markouts_vs_mid)
    mean_drift, _ = _mean_and_tstat(drifts)
    roi_pct = (settled_pnl_cents / collateral_cents) * 100.0 if collateral_cents > 0.0 else None
    z_val, z_n = z_cal(zcal_records)

    by_series = {
        s: {
            "n_fills": b["n_fills"],
            "contracts": b["contracts"],
            "closing_markout_c": _mean_and_tstat(b["markouts"])[0],
            "pickoff_drift_pp": _mean_and_tstat(b["drifts"])[0],
        }
        for s, b in sorted(by_series_rows.items())
    }
    by_queue_mode = {
        m: {
            "n_fills": b["n_fills"],
            "contracts": b["contracts"],
            "closing_markout_c": _mean_and_tstat(b["markouts"])[0],
        }
        for m, b in sorted(by_mode_rows.items())
    }

    wide_mask = (
        work["is_two_sided"].fillna(False)
        & work["spread_cents"].notna()
        & (work["spread_cents"] >= cfg.spread_min_c)
        & (work["spread_cents"] <= cfg.spread_max_c)
    )
    wide_rows = work[wide_mask]
    n_wide = int(len(wide_rows))
    skip_funnel: dict[str, float] = {}
    skip_rate_sq: float | None = None
    if n_wide > 0:
        counts = wide_rows["reason_code"].value_counts()
        skip_funnel = {str(k): float(v) / n_wide for k, v in counts.items()}
        sq_count = int(wide_rows["reason_code"].isin(["quorum", "stale"]).sum())
        skip_rate_sq = sq_count / n_wide

    # Section 4 bar evaluation & kill triggers
    kill_triggers: list[str] = []
    series_drops: list[str] = []

    window_ready = n_slates >= cfg.min_slates and (
        n_fills >= cfg.min_fills_pooled
        or any(b["n_fills"] >= cfg.min_fills_series for b in by_series.values())
    )

    if mean_mo is not None and mo_t is not None and mean_mo >= cfg.markout_go_c and mo_t >= cfg.markout_t_stat_min:
        mo_bar = "PASS"
    elif mean_mo is not None:
        mo_bar = "FAIL"
    else:
        mo_bar = "PENDING"
    if n_slates >= cfg.min_slates and mean_mo is not None and mean_mo < cfg.markout_kill_c:
        kill_triggers.append("markout_below_kill")

    for s, b in by_series.items():
        if b["n_fills"] >= cfg.min_fills_series and b["closing_markout_c"] is not None:
            if b["closing_markout_c"] <= cfg.markout_series_drop_c:
                series_drops.append(f"{s}:markout<=0")
        if n_slates >= cfg.min_slates and b["pickoff_drift_pp"] is not None:
            if b["pickoff_drift_pp"] < cfg.drift_drop_pp:
                series_drops.append(f"{s}:drift<{cfg.drift_drop_pp}")

    if mean_drift is not None and mean_drift > cfg.drift_go_pp:
        drift_bar = "PASS"
    elif mean_drift is not None:
        drift_bar = "FAIL"
    else:
        drift_bar = "PENDING"
    if (
        n_slates >= cfg.drift_kill_early_slates
        and n_fills >= cfg.min_fills_series
        and mean_drift is not None
        and mean_drift < cfg.drift_kill_early_pp
    ):
        kill_triggers.append("early_pickoff_drift")

    vol_bar = "PASS" if (n_slates > 0 and fills_per_slate >= cfg.fills_per_slate_go) else "FAIL"
    if n_slates >= cfg.min_slates and n_fills < cfg.fills_kill_total:
        kill_triggers.append("empty_book_fill_volume")

    if skip_rate_sq is None:
        skip_bar = "PENDING"
    elif skip_rate_sq < cfg.skip_go:
        skip_bar = "PASS"
    else:
        skip_bar = "FAIL"
        if skip_rate_sq > cfg.skip_kill:
            kill_triggers.append("feed_unusable_skip_rate")

    if z_val is not None and z_val < cfg.zcal_floor:
        cal_bar = "FAIL"
        kill_triggers.append("z_cal_below_floor")
    elif n_unresolved_filled > 0 and n_fills > 0:
        cal_bar = "PENDING"
    else:
        cal_bar = "PASS"

    bars = {
        "window": "PASS" if window_ready else "PENDING",
        "closing_markout": mo_bar,
        "pickoff_drift": drift_bar,
        "fill_volume": vol_bar,
        "feed_usability": skip_bar,
        "settlement_calibration": cal_bar,
    }

    if kill_triggers:
        overall = "KILL"
    elif all(v == "PASS" for v in bars.values()):
        overall = "PASS"
    elif any(v == "FAIL" for v in bars.values()):
        overall = "FAIL"
    else:
        overall = "PENDING"

    return {
        "n_rows": int(len(work)),
        "n_slates": n_slates,
        "n_fills": n_fills,
        "total_contracts": total_contracts,
        "fills_per_slate": fills_per_slate,
        "contracts_per_slate": contracts_per_slate,
        "active_quote_hours": active_quote_hours,
        "fills_per_100_quote_hours": fills_per_100_qh,
        "closing_markout_c": mean_mo,
        "closing_markout_t_stat": mo_t,
        "closing_markout_vs_mid_c": mean_mo_mid,
        "pickoff_drift_pp": mean_drift,
        "settled_pnl_cents": settled_pnl_cents,
        "roi_pct": roi_pct,
        "z_cal": z_val,
        "z_cal_n": z_n,
        "by_series": by_series,
        "by_queue_mode": by_queue_mode,
        "skip_funnel": skip_funnel,
        "skip_rate_staleness_quorum": skip_rate_sq,
        "bars": bars,
        "kill_triggers": kill_triggers,
        "series_drops": series_drops,
        "overall_status": overall,
    }


def _fmt(val: float | None, spec: str, suffix: str = "") -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "n/a"
    return f"{format(val, spec)}{suffix}"


def format_scorecard(report: Mapping[str, Any]) -> str:
    """Format a scorecard dict into a human-readable summary report."""
    lines = [
        "KALSHI WIDE-SPREAD NBA PROP SHADOW SCORECARD (MAK-01)",
        "=" * 54,
        f"Overall Status      : {report.get('overall_status')}",
        f"Slates / Fills      : {report.get('n_slates')} slates | {report.get('n_fills')} fills ({report.get('total_contracts', 0.0):.0f} contracts)",
        f"Throughput          : {_fmt(report.get('fills_per_slate'), '.2f', ' fills/slate')} | {_fmt(report.get('fills_per_100_quote_hours'), '.2f', ' fills/100qh')}",
        f"Closing Markout     : {_fmt(report.get('closing_markout_c'), '+.3f', ' c')} (t={_fmt(report.get('closing_markout_t_stat'), '.2f')}) | vs mid={_fmt(report.get('closing_markout_vs_mid_c'), '+.3f', ' c')}",
        f"15m Pickoff Drift   : {_fmt(report.get('pickoff_drift_pp'), '+.3f', ' pp')}",
        f"Settled P&L / ROI   : {_fmt(report.get('settled_pnl_cents'), '+.2f', ' c')} | ROI={_fmt(report.get('roi_pct'), '+.2f', '%')} | Z_cal={_fmt(report.get('z_cal'), '+.3f')} (n={report.get('z_cal_n', 0)})",
        f"Skip Rate (Stale/Q) : {_fmt(report.get('skip_rate_staleness_quorum'), '.1%')}",
        "-" * 54,
        "Graduation Bars:",
    ]
    for name, status in (report.get("bars") or {}).items():
        lines.append(f"  {name:<24s}: {status}")
    if report.get("kill_triggers"):
        lines.append(f"Kill Triggers       : {', '.join(report['kill_triggers'])}")
    if report.get("series_drops"):
        lines.append(f"Series Drop Flags   : {', '.join(report['series_drops'])}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Read-only CLI runner for the Kalshi wide-spread NBA prop shadow harness."""
    cfg = WideSpreadConfig()
    parser = argparse.ArgumentParser(description="Kalshi wide-spread NBA prop shadow harness (read-only)")
    parser.add_argument("--out-dir", required=True, help="Parquet output directory")
    parser.add_argument(
        "--snapshot-dir",
        default=os.environ.get("KALSHI_WIDE_SNAPSHOT_DIR"),
        help="Directory containing ParlayAPI NBA event JSON snapshots",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=cfg.poll_interval_s,
        help="Seconds between poll cycles",
    )
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit")
    parser.add_argument("--max-cycles", type=int, default=None, help="Maximum poll cycles before exiting")
    args = parser.parse_args(argv)

    raw_client = KalshiClient(env=PUBLIC_DATA_ENV, key_id=None, private_key_pem=None)
    guarded = GuardedClient(raw_client)
    provider = ParlaySnapshotProvider(snapshot_dir=args.snapshot_dir, cfg=cfg)
    harness = ShadowHarness(client=guarded, anchor_provider=provider, cfg=cfg)

    run_id = uuid.uuid4().hex[:10]
    max_cycles = 1 if args.once else args.max_cycles
    seq = 0
    all_updates: list[dict[str, Any]] = []
    dates_written: set[date] = set()
    while max_cycles is None or seq < max_cycles:
        now_utc = datetime.now(timezone.utc)
        rows = harness.run_once(now_utc)
        all_updates.extend(harness.drain_updates())
        if rows:
            et_date = now_utc.astimezone(ET).date()
            write_part(rows, args.out_dir, et_date, run_id=run_id, seq=seq)
            dates_written.add(et_date)
        seq += 1
        if max_cycles is not None and seq >= max_cycles:
            break
        time.sleep(max(1, int(args.poll_seconds)))
    for et_date in sorted(dates_written):
        finalize_day(args.out_dir, et_date, updates=all_updates or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
