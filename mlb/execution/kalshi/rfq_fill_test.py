"""MAK-02 $21 RFQ fill-test quoter, pure gate pipeline, exposure book, and flag sources.

Safety invariants:
- Boot default is zero live quote attempts: BOTH `KALSHI_RFQ_FILL_TEST_ENABLE=1` AND the
  remote/local ENABLE flag must be active (and STOP absent and flags readable).
- Production client construction (`build_prod_client`) is additionally gated behind
  `KALSHI_PROD_QUOTES_ARMED=1` in `KalshiClient`.
- Every evaluation, skip, quote, fill, withheld confirmation, and settlement is written to
  an append-only JSONL `DecisionLog`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping

from ev.kalshi_rfq_filter import FilterConfig, RFQFilterState, classify_rfq_record
from ev.kalshi_rfq_pricer import QuoteDecision, parse_rfq
from mlb.execution.kalshi.client import KalshiAPIError, ProductionNotEnabled
from mlb.execution.kalshi.espn_tipoff import GameStateSnapshot, TipoffGate
from mlb.execution.kalshi.rfq_client import RFQProdQuoteClient
from mlb.execution.kalshi.rfq_maker import (
    RFQLedger,
    RFQMaker,
    _decimal_or_none,
    _excluded_series_class,
    _float_or_none,
    _series_of,
)
from mlb.execution.kalshi.sizing import kelly_fraction

log = logging.getLogger("kalshi_exec.rfq_fill_test")

ENABLE_ENV = "KALSHI_RFQ_FILL_TEST_ENABLE"
FLAG_PREFIX = "Kalshi/rfq_fill_test"
ENABLE_KEY = FLAG_PREFIX + "/ENABLE"
STOP_KEY = FLAG_PREFIX + "/STOP"


@dataclass(frozen=True)
class FillTestConfig:
    per_quote_collateral_cap: float = 5.00
    aggregate_cap_abs: float = 21.00
    f0: float = 0.25                      # ratified base Kelly scalar (spec Section 3)
    tier_b_sizing: float = 0.75           # ratified Tier B live sizing (spec Section 4)
    tipoff_buffer_sec: float = 600.0      # chosen parameter: 10-minute tip-off buffer
    snapshot_max_age_sec: float = 180.0
    poll_interval_sec: float = 60.0


class GcsFlagSource:
    """GCS/storage-backed ENABLE/STOP flag reader; unreadable fails closed as stopped."""

    def __init__(
        self,
        enable_key: str = ENABLE_KEY,
        stop_key: str = STOP_KEY,
        exists_fn: Callable[[str], bool] | None = None,
    ):
        self.enable_key = enable_key
        self.stop_key = stop_key
        self._exists_fn = exists_fn

    def flags(self) -> tuple[bool, bool, bool]:
        try:
            exists_fn = self._exists_fn
            if exists_fn is None:
                from mlb_core.storage import exists as exists_fn
            enabled = bool(exists_fn(self.enable_key))
            stopped = bool(exists_fn(self.stop_key))
            return (enabled, stopped, True)
        except Exception:  # noqa: BLE001 -- fail closed on unreadable flag store
            return (False, True, False)


class FileFlagSource:
    """Local-directory ENABLE/STOP flag reader; missing directory fails closed as stopped."""

    def __init__(self, directory: str | os.PathLike[str]):
        self.directory = Path(directory)

    def flags(self) -> tuple[bool, bool, bool]:
        try:
            if not self.directory.is_dir():
                return (False, True, False)
            enabled = (self.directory / "ENABLE").exists()
            stopped = (self.directory / "STOP").exists()
            return (enabled, stopped, True)
        except Exception:  # noqa: BLE001 -- fail closed on unreadable directory
            return (False, True, False)


@dataclass(frozen=True)
class FireView:
    rfq_id: str
    primary_class: str
    disposition: str
    fire_tier: str | None
    taper_weight: float | None
    flag_names: tuple[str, ...]
    collateral: float | None
    contracts: float | None
    no_bid: float | None
    p_yes_true: float | None
    game_keys: tuple[str, ...]
    ts: float


@dataclass(frozen=True)
class QuoteEvaluation:
    rfq_id: str
    outcome: str
    reason: str
    tier: str | None
    collateral: float | None
    kelly_ceiling: float
    headroom: float
    committed_after: float


@dataclass
class ExposureBook:
    open_quotes: dict[str, float] = field(default_factory=dict)
    filled_unsettled: dict[str, float] = field(default_factory=dict)

    def committed(self) -> float:
        return sum(self.open_quotes.values()) + sum(self.filled_unsettled.values())

    def headroom(self, balance: float, cfg: FillTestConfig) -> float:
        return min(balance, cfg.aggregate_cap_abs) - self.committed()

    def add_open(self, quote_id: str, collateral: float) -> None:
        self.open_quotes[quote_id] = float(collateral)

    def mark_filled(self, quote_id: str) -> None:
        if quote_id in self.open_quotes:
            self.filled_unsettled[quote_id] = self.open_quotes.pop(quote_id)

    def settle(self, quote_id: str) -> None:
        self.filled_unsettled.pop(quote_id, None)

    def release(self, quote_id: str) -> None:
        self.open_quotes.pop(quote_id, None)


def evaluate_fire(
    view: FireView,
    *,
    cfg: FillTestConfig,
    balance: float,
    committed: float,
    gate_ok: bool,
    gate_reason: str,
) -> QuoteEvaluation:
    """Pure 10-step gate pipeline in exact spec order (first failure wins)."""
    headroom = min(balance, cfg.aggregate_cap_abs) - committed

    def _skip(reason: str, ceiling: float = 0.0) -> QuoteEvaluation:
        return QuoteEvaluation(
            rfq_id=view.rfq_id,
            outcome="SKIPPED",
            reason=reason,
            tier=view.fire_tier,
            collateral=view.collateral,
            kelly_ceiling=ceiling,
            headroom=headroom,
            committed_after=committed,
        )

    if view.primary_class != "DENOMINATOR":
        return _skip("NOT_DENOMINATOR")
    if view.disposition != "ADMITTED_FIRE":
        return _skip(f"DISPOSITION_{view.disposition}")
    if "NON_SPORTS" in view.flag_names:
        return _skip("NON_SPORTS")
    if view.taper_weight is None:
        return _skip("TAPER_UNKNOWN")
    if view.taper_weight <= 0:
        return _skip("STALE_ZERO")
    if view.collateral is None or view.collateral <= 0 or view.contracts is None:
        return _skip("SIZE_UNKNOWN")

    if view.p_yes_true is None or view.no_bid is None:
        ceiling = 0.0
    else:
        q = 1.0 - float(view.p_yes_true)
        kf = kelly_fraction(q, float(view.no_bid))
        sizing = cfg.tier_b_sizing if view.fire_tier == "B" else 1.0
        ceiling = balance * cfg.f0 * float(view.taper_weight) * sizing * kf

    if view.collateral > ceiling + 1e-9:
        return _skip("KELLY_CEILING", ceiling)
    if view.collateral > cfg.per_quote_collateral_cap + 1e-9:
        return _skip("PER_QUOTE_CAP", ceiling)
    if view.collateral > headroom + 1e-9:
        return _skip("AGGREGATE_CAP", ceiling)
    if not gate_ok:
        return _skip(gate_reason, ceiling)

    return QuoteEvaluation(
        rfq_id=view.rfq_id,
        outcome="QUOTED",
        reason="",
        tier=view.fire_tier,
        collateral=view.collateral,
        kelly_ceiling=ceiling,
        headroom=headroom,
        committed_after=committed + view.collateral,
    )


class DecisionLog:
    """Append-only JSONL log of every fill-test evaluation and lifecycle event."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        storage_prefix: str | None = None,
        writer: Callable[[bytes, str], None] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.path = str(path) if path is not None else None
        self.storage_prefix = storage_prefix
        self._writer = writer
        self._clock = clock
        self._buf: list[str] = []
        self._seq = 0
        self._lock = threading.Lock()

    def record(self, entry: dict) -> dict:
        payload = dict(entry)
        payload["kind"] = "fill_test_decision"
        payload.setdefault("ts", self._clock())
        line = json.dumps(payload, default=str, sort_keys=True, ensure_ascii=True) + "\n"
        with self._lock:
            if self.path is not None:
                parent = os.path.dirname(self.path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="ascii") as fh:
                    fh.write(line)
            if self.storage_prefix is not None:
                self._buf.append(line)
        return payload

    def flush(self) -> int:
        if self.storage_prefix is None:
            return 0
        with self._lock:
            if not self._buf:
                return 0
            lines, self._buf = self._buf, []
            self._seq += 1
            seq = self._seq
        ts = datetime.fromtimestamp(self._clock(), timezone.utc)
        key = f"{self.storage_prefix}/{ts.date().isoformat()}/{ts.strftime('%H%M%S')}_{seq:05d}.jsonl"
        try:
            writer = self._writer
            if writer is None:
                from mlb_core.storage import write_bytes as writer
            writer("".join(lines).encode("ascii"), key)
        except Exception as exc:  # noqa: BLE001 -- preserve buffer on failed flush
            log.warning("decision log flush to %s failed (%d lines kept): %r", key, len(lines), exc)
            with self._lock:
                self._buf = lines + self._buf
            return 0
        return len(lines)


def build_classification_record(rfq_msg: dict, decision: QuoteDecision, ts: float) -> dict:
    """Build the tape-shaped record consumed by `classify_rfq_record`, mirroring `RFQTape.record_rfq`."""
    rfq_msg = rfq_msg or {}
    raw_legs = rfq_msg.get("mve_selected_legs") or rfq_msg.get("legs") or []
    leg_tickers = [str(l.get("market_ticker") or l.get("ticker") or "") for l in raw_legs if isinstance(l, dict)]
    market_ticker = str(rfq_msg.get("market_ticker") or "")
    target_dec = _decimal_or_none(rfq_msg.get("target_cost_dollars"))
    preview_10 = target_dec is not None and target_dec == Decimal("10.00")
    excluded = _excluded_series_class(leg_tickers or [market_ticker])
    if raw_legs:
        pairs = sorted(
            f"{t}|{str(l.get('side') or '').lower()}"
            for l, t in zip(raw_legs, leg_tickers)
            if isinstance(l, dict)
        )
        combo_id = hashlib.sha256(",".join(pairs).encode("ascii", "replace")).hexdigest()
    else:
        combo_id = hashlib.sha256(market_ticker.encode("ascii", "replace")).hexdigest()

    enriched_legs: list[dict] = []
    for snap in decision.legs or ():
        leg = dict(snap or {})
        if "series" not in leg:
            leg["series"] = _series_of(leg.get("ticker"))
        if "age_sec" not in leg:
            fv_ts = leg.get("fv_updated_ts")
            leg["age_sec"] = (ts - float(fv_ts)) if fv_ts is not None else None
        leg.setdefault("assumed_leg", False)
        leg.setdefault("pricing_source", "book")
        enriched_legs.append(leg)

    return {
        "rfq": rfq_msg,
        "rfq_id": str(rfq_msg.get("id") or rfq_msg.get("rfq_id") or ""),
        "legs": enriched_legs,
        "notes": decision.notes,
        "is_fire": decision.action == "QUOTE",
        "preview_10": preview_10,
        "excluded_series_class": excluded,
        "creator_id": rfq_msg.get("creator_id"),
        "combo_id": combo_id,
        "ts": ts,
        "target_cost_dollars_num": float(target_dec) if target_dec is not None else None,
        "contracts_fp_num": _float_or_none(rfq_msg.get("contracts_fp", rfq_msg.get("contracts"))),
        "n_legs": len(raw_legs) if raw_legs else 1,
        "in_denominator": (not preview_10 and excluded == "none"),
    }


def build_fire_view(rfq_msg: dict, decision: QuoteDecision, stamp: dict, ts: float) -> FireView:
    """Normalize a live (rfq_msg, QuoteDecision, classifier stamp) tuple into a `FireView`."""
    rfq_msg = rfq_msg or {}
    rfq_id = str(rfq_msg.get("id") or rfq_msg.get("rfq_id") or decision.rfq_id or "")
    game_keys = tuple(
        sorted({str(leg["game_key"]) for leg in (decision.legs or ()) if isinstance(leg, dict) and leg.get("game_key")})
    )
    return FireView(
        rfq_id=rfq_id,
        primary_class=str(stamp["primary_class"]),
        disposition=str(stamp["disposition"]),
        fire_tier=stamp.get("fire_tier"),
        taper_weight=stamp.get("taper_weight"),
        flag_names=tuple(stamp.get("flag_names") or ()),
        collateral=decision.collateral,
        contracts=decision.contracts,
        no_bid=decision.no_bid,
        p_yes_true=decision.p_yes_true,
        game_keys=game_keys,
        ts=ts,
    )


class DryRunClient:
    """Zero-network offline stub client recording quote attempts and returning synthetic ids."""

    def __init__(self, balance: float = 21.00):
        self.balance = float(balance)
        self.attempts: list[dict] = []
        self.confirms: list[dict] = []
        self.deletes: list[str] = []

    def create_quote(self, rfq_id: str, yes_bid_fp: str, no_bid_fp: str, **kw) -> dict:
        self.attempts.append({"rfq_id": rfq_id, "yes_bid_fp": yes_bid_fp, "no_bid_fp": no_bid_fp, **kw})
        return {"id": f"dryrun-{len(self.attempts)}"}

    def confirm_quote(self, quote_id: str, rfq_id: str | None = None) -> dict:
        self.confirms.append({"quote_id": quote_id, "rfq_id": rfq_id})
        return {}

    def delete_quote(self, quote_id: str) -> dict:
        self.deletes.append(quote_id)
        return {}

    def get_balance(self) -> dict:
        return {"balance_dollars": f"{self.balance:.2f}"}


class FillTestQuoter:
    """Live/offline fill-test RFQ quoter wrapping pricer, classifier, tip-off gate, and exposure book."""

    def __init__(
        self,
        pricer,
        client,
        ledger: RFQLedger,
        maker: RFQMaker,
        gate: TipoffGate,
        snapshot_provider: Callable[[], GameStateSnapshot | None],
        flag_source,
        balance_provider: Callable[[], float],
        decision_log: DecisionLog,
        cfg: FillTestConfig = FillTestConfig(),
        clock: Callable[[], float] = time.time,
        env: Mapping[str, str] | None = None,
    ):
        self.pricer = pricer
        self.client = client
        self.ledger = ledger
        self.maker = maker
        self.gate = gate
        self.snapshot_provider = snapshot_provider
        self.flag_source = flag_source
        self.balance_provider = balance_provider
        self.decision_log = decision_log
        self.cfg = cfg
        self._clock = clock
        self._env = os.environ if env is None else env
        self.balance = 0.0
        self.exposure = ExposureBook()
        self._filter_state = RFQFilterState()
        self._filter_cfg = FilterConfig()
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            self.balance = float(self.balance_provider())
            enabled, stopped, readable = self.flag_source.flags()
            self.decision_log.record({
                "event": "SESSION_START",
                "rfq_id": "",
                "outcome": "SESSION_START",
                "reason": "",
                "balance": self.balance,
                "enabled": enabled,
                "stopped": stopped,
                "readable": readable,
                "env_enabled": self._env.get(ENABLE_ENV) == "1",
            })

    def _skip_result(self, rfq_id: str, reason: str, collateral: float | None = None) -> QuoteEvaluation:
        committed = self.exposure.committed()
        headroom = self.exposure.headroom(self.balance, self.cfg)
        return QuoteEvaluation(
            rfq_id=rfq_id,
            outcome="SKIPPED",
            reason=reason,
            tier=None,
            collateral=collateral,
            kelly_ceiling=0.0,
            headroom=headroom,
            committed_after=committed,
        )

    def _sync_open_exposure(self) -> None:
        for qid in list(self.exposure.open_quotes):
            q = self.ledger.get_quote(qid)
            if q is None or q["state"] not in ("open", "accepted", "confirmed"):
                self.exposure.release(qid)

    def on_event(self, typ: str, msg: dict, received_ts: float | None = None):
        received_ts = self._clock() if received_ts is None else received_ts
        with self._lock:
            if typ == "rfq_created":
                return self.on_rfq_created(msg, received_ts)
            if typ == "quote_accepted":
                return self.on_quote_accepted(msg, received_ts)
            if typ == "quote_executed":
                return self.on_quote_executed(msg)
            if typ == "rfq_deleted":
                return self.on_rfq_deleted(msg)
        return None

    def on_rfq_created(self, msg: dict, received_ts: float | None = None) -> QuoteEvaluation | None:
        with self._lock:
            now = self._clock()
            rfq_id_raw = str((msg or {}).get("id") or (msg or {}).get("rfq_id") or "")
            enabled, stopped, readable = self.flag_source.flags()
            if not readable:
                self.decision_log.record({
                    "event": "EVALUATION",
                    "rfq_id": rfq_id_raw,
                    "outcome": "SKIPPED",
                    "reason": "FLAG_UNREADABLE",
                    "ts": now,
                })
                return self._skip_result(rfq_id_raw, "FLAG_UNREADABLE")
            if stopped:
                self.decision_log.record({
                    "event": "EVALUATION",
                    "rfq_id": rfq_id_raw,
                    "outcome": "SKIPPED",
                    "reason": "STOPPED",
                    "ts": now,
                })
                return self._skip_result(rfq_id_raw, "STOPPED")
            if not enabled or self._env.get(ENABLE_ENV) != "1":
                self.decision_log.record({
                    "event": "EVALUATION",
                    "rfq_id": rfq_id_raw,
                    "outcome": "SKIPPED",
                    "reason": "DISABLED",
                    "ts": now,
                })
                return self._skip_result(rfq_id_raw, "DISABLED")

            rfq = parse_rfq(msg)
            if not rfq.rfq_id:
                return None
            if self.ledger.quote_for_rfq(rfq.rfq_id, "live"):
                return None

            d = self.pricer.price(rfq, now)
            if not d.quoted:
                self.decision_log.record({
                    "event": "EVALUATION",
                    "rfq_id": rfq.rfq_id,
                    "outcome": "SKIPPED",
                    "reason": d.reason,
                    "collateral": d.collateral,
                    "ts": now,
                })
                return self._skip_result(rfq.rfq_id, d.reason, d.collateral)

            record = build_classification_record(msg, d, now)
            stamp = classify_rfq_record(record, self._filter_state, self._filter_cfg)
            view = build_fire_view(msg, d, stamp, now)
            gate_ok, gate_reason = self.gate.check(view.game_keys, now, self.snapshot_provider())
            ev = evaluate_fire(
                view,
                cfg=self.cfg,
                balance=self.balance,
                committed=self.exposure.committed(),
                gate_ok=gate_ok,
                gate_reason=gate_reason,
            )
            self.decision_log.record({
                "event": "EVALUATION",
                "rfq_id": ev.rfq_id,
                "outcome": ev.outcome,
                "reason": ev.reason,
                "tier": ev.tier,
                "collateral": ev.collateral,
                "kelly_ceiling": ev.kelly_ceiling,
                "headroom": ev.headroom,
                "committed_after": ev.committed_after,
                "ts": now,
            })
            if ev.outcome != "QUOTED":
                return ev

            try:
                resp = self.client.create_quote(rfq.rfq_id, d.yes_bid_fp, d.no_bid_fp)
            except KalshiAPIError as exc:
                self.decision_log.record({
                    "event": "SUBMIT_ERROR",
                    "rfq_id": rfq.rfq_id,
                    "outcome": "SUBMIT_ERROR",
                    "reason": "SUBMIT_ERROR",
                    "error": str(exc)[:200],
                    "ts": now,
                })
                return replace(ev, outcome="SKIPPED", reason="SUBMIT_ERROR", committed_after=self.exposure.committed())
            except Exception as exc:  # noqa: BLE001 -- ambiguous failure: do not record quote
                self.decision_log.record({
                    "event": "SUBMIT_ERROR",
                    "rfq_id": rfq.rfq_id,
                    "outcome": "SUBMIT_ERROR",
                    "reason": "SUBMIT_ERROR",
                    "error": repr(exc)[:200],
                    "ts": now,
                })
                return replace(ev, outcome="SKIPPED", reason="SUBMIT_ERROR", committed_after=self.exposure.committed())

            quote_id = str((resp or {}).get("id") or "")
            held = "no" if d.yes_bid <= 0 else "yes" if d.no_bid <= 0 else "both"
            self.ledger.add_quote(
                quote_id=quote_id,
                rfq_id=rfq.rfq_id,
                mode="live",
                kind=d.kind,
                market_ticker=rfq.market_ticker,
                held_side=held,
                yes_bid=d.yes_bid,
                no_bid=d.no_bid,
                contracts=d.contracts,
                collateral=d.collateral,
                ev=d.ev_per_contract,
                p_yes_true=d.p_yes_true,
                legs=d.legs,
                ts_epoch=now,
            )
            self.exposure.add_open(quote_id, d.collateral)
            self.decision_log.record({
                "event": "QUOTED",
                "rfq_id": rfq.rfq_id,
                "quote_id": quote_id,
                "outcome": "QUOTED",
                "reason": "",
                "tier": ev.tier,
                "collateral": d.collateral,
                "kelly_ceiling": ev.kelly_ceiling,
                "headroom": ev.headroom,
                "committed_after": self.exposure.committed(),
                "ts": now,
            })
            return ev

    def on_quote_accepted(self, msg: dict, received_ts: float | None = None) -> str:
        with self._lock:
            now = self._clock()
            _, stopped, readable = self.flag_source.flags()
            if stopped or not readable:
                # Deliberately accept the missed-confirmation penalty rather than committing
                # live capital after STOP is tripped or when the flag store is unreadable.
                why = "STOPPED" if stopped else "FLAG_UNREADABLE"
                self.decision_log.record({
                    "event": "CONFIRM_WITHHELD_STOPPED",
                    "rfq_id": str((msg or {}).get("rfq_id") or ""),
                    "quote_id": str((msg or {}).get("quote_id") or ""),
                    "outcome": "CONFIRM_WITHHELD_STOPPED",
                    "reason": why,
                    "ts": now,
                })
                return "CONFIRM_WITHHELD_STOPPED"
            res = self.maker.on_quote_accepted(msg, received_ts=received_ts)
            self._sync_open_exposure()
            return res

    def on_quote_executed(self, msg: dict) -> str:
        with self._lock:
            res = self.maker.on_quote_executed(msg)
            if res == "FILLED":
                quote_id = str((msg or {}).get("quote_id") or "")
                self.exposure.mark_filled(quote_id)
                self.balance = float(self.balance_provider())
                q = self.ledger.get_quote(quote_id)
                self.decision_log.record({
                    "event": "FILLED",
                    "rfq_id": q["rfq_id"] if q else str((msg or {}).get("rfq_id") or ""),
                    "quote_id": quote_id,
                    "outcome": "FILLED",
                    "reason": "",
                    "balance": self.balance,
                    "committed_after": self.exposure.committed(),
                    "ts": self._clock(),
                })
            return res

    def on_rfq_deleted(self, msg: dict) -> str:
        with self._lock:
            rfq_id = str((msg or {}).get("id") or (msg or {}).get("rfq_id") or "")
            # quotes are always recorded under mode "live" (see on_rfq_created)
            q = self.ledger.quote_for_rfq(rfq_id, "live") if rfq_id else None
            res = self.maker.on_rfq_deleted(msg)
            if res == "EXPIRED" and q is not None:
                self.exposure.release(q["quote_id"])
            return res

    def settle(self, quote_id: str, won: bool) -> None:
        with self._lock:
            q = self.ledger.get_quote(quote_id)
            self.exposure.settle(quote_id)
            self.decision_log.record({
                "event": "SETTLED",
                "rfq_id": q["rfq_id"] if q else "",
                "quote_id": quote_id,
                "outcome": "SETTLED",
                "reason": "",
                "won": bool(won),
                "committed_after": self.exposure.committed(),
                "ts": self._clock(),
            })


def build_prod_client(
    key_id: str | None,
    private_key_pem: bytes | None,
    env: Mapping[str, str] | None = None,
) -> RFQProdQuoteClient:
    """Only supported factory for `RFQProdQuoteClient` in the fill-test path."""
    environ = os.environ if env is None else env
    if environ.get(ENABLE_ENV) != "1":
        raise ProductionNotEnabled(f"production fill-test client disabled: set {ENABLE_ENV}=1")
    return RFQProdQuoteClient(key_id, private_key_pem)
