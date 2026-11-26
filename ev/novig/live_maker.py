"""MAK-03 Novig live passive-maker quoter, pure gate pipeline, exposure book, and flag sources.

Safety invariants:
- Boot default is zero live quote attempts: BOTH `NOVIG_LIVE_MAKER_ENABLE=1` AND the
  remote/local ENABLE flag must be active (and STOP absent and flags readable).
- Production client construction (`build_prod_client`) requires `NOVIG_LIVE_MAKER_ENABLE=1`
  and is additionally gated behind `NOVIG_PROD_ARMED=1` inside `NovigClient`.
- `LiveMakerVenue` refuses any client whose `env != "production"` (`NovigProductionNotEnabled`).
- `aggregate_cap_usd=None` fails closed: live preconditions refuse and `evaluate_quote`
  skips with `AGGREGATE_CAP_UNSET`.
- Reconcile-don't-resend: at most one POST per intent; ambiguous 5xx/network errors reconcile
  via `fills_for(client_id)` or return `UNKNOWN`, never resending.
- Every session start, evaluation, quote, placement error, cancel, fill, and settlement is
  recorded to an append-only JSONL `DecisionLog`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import requests

from ev.maker_settlement import (
    NHLSogResolver,
    STATUS_LOSS,
    STATUS_VOID,
    STATUS_WIN,
    settle_fill,
)
from ev.novig import maker
from ev.novig.client import (
    PROD_ENV,
    PROD_READONLY_ENV,
    NovigAPIError,
    NovigClient,
    NovigProductionNotEnabled,
    snap_price,
)
from ev.novig.maker_venue import MAX_PAGES, UNIT, _OPEN_STATUSES
from ev.novig.paper_orders import leg_key

log = logging.getLogger("ev.novig.live_maker")

ENABLE_ENV = "NOVIG_LIVE_MAKER_ENABLE"
LIVE_ENV = "NOVIG_LIVE_MAKER_LIVE"
FLAG_PREFIX = "Novig/live_maker"
ENABLE_KEY = FLAG_PREFIX + "/ENABLE"
STOP_KEY = FLAG_PREFIX + "/STOP"
CONFIG_PREFIX = "NOVIG_LIVE_MAKER_"

RUNGS = ("rung_1c", "rung_2c", "rung_3c", "control")
_ET = ZoneInfo("America/New_York")
_SETTLED_STATUSES = frozenset({STATUS_WIN, STATUS_LOSS, STATUS_VOID})


@dataclass(frozen=True)
class LiveMakerConfig:
    per_quote_cap_usd: float = 5.00        # hard cap on one quote's liability (cost)
    quote_liability_usd: float = 5.00      # sizing target per quote before caps
    aggregate_cap_usd: float | None = None # total committed cap; None = unset (fail closed)
    rung_offsets_c: tuple[float, ...] = (1.0, 2.0, 3.0)
    rung_allocations: tuple[float, ...] = (0.30, 0.30, 0.20, 0.20)   # (1c, 2c, 3c, control)
    clean_arm_disp_max: float = 0.02
    poll_interval_sec: float = 30.0
    settlement_sweep_sec: float = 60.0
    boxscores_csv: str | None = None       # NHLSogResolver path for the settlement sweep


def _coerce_config_field(name: str, raw: str, default: Any) -> Any:
    if name == "aggregate_cap_usd":
        return float(raw)
    if name == "boxscores_csv":
        return str(raw)
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, tuple):
        return tuple(float(s.strip()) for s in raw.split(",") if s.strip())
    return raw


def load_live_maker_config(env: Mapping[str, str] | None = None) -> LiveMakerConfig:
    """Load LiveMakerConfig from NOVIG_LIVE_MAKER_<FIELD> environment overrides."""
    environ = os.environ if env is None else env
    defaults = LiveMakerConfig()
    kwargs: dict[str, Any] = {}
    for f in fields(LiveMakerConfig):
        raw = environ.get(f"{CONFIG_PREFIX}{f.name.upper()}")
        if raw not in (None, ""):
            kwargs[f.name] = _coerce_config_field(f.name, raw, getattr(defaults, f.name))
    return LiveMakerConfig(**kwargs)


# ------------------------------------------------------------------ rung assignment & pricing
def assign_rung(
    game_key: str,
    league: str,
    stat: str,
    cfg: LiveMakerConfig = LiveMakerConfig(),
) -> str:
    """Deterministic cluster assignment across (game_key, league, stat) using sha256."""
    payload = f"{game_key}|{league}|{stat}".encode("ascii", "replace")
    digest = hashlib.sha256(payload).digest()
    frac = int.from_bytes(digest[:8], "big") / float(1 << 64)
    cum = 0.0
    for rung, alloc in zip(RUNGS, cfg.rung_allocations):
        cum += float(alloc)
        if frac < cum:
            return rung
    return RUNGS[-1]


def rung_bid(
    *,
    rung: str,
    p_cons_side: float,
    fair_floor: float | None,
    delta_control: float | None,
    novig_bid_side: float | None,
    disp: float,
    cfg: LiveMakerConfig = LiveMakerConfig(),
) -> tuple[float | None, str]:
    """Compute the rung's passive bid price, or (None, reason) if paused/behind book."""
    if rung == "control":
        if fair_floor is None or delta_control is None:
            return (None, "NO_FAIR")
        bid = maker.quote_bid(fair_floor, delta_control, novig_bid_side)
        return (bid, "" if bid is not None else "BID_BEHIND_BOOK")

    if rung not in RUNGS:
        raise ValueError(f"unknown rung {rung!r}")
    idx = RUNGS.index(rung)
    if disp > cfg.clean_arm_disp_max:
        return (None, "DISPERSION_PAUSE")
    offset = float(cfg.rung_offsets_c[idx]) / 100.0
    bid = maker.quote_bid(p_cons_side, offset, novig_bid_side)
    return (bid, "" if bid is not None else "BID_BEHIND_BOOK")


# ------------------------------------------------------------------ pure gate pipeline
@dataclass(frozen=True)
class CandidateView:
    game_key: str
    league: str
    stat: str
    side: str | None
    mode: str
    p_cons_side: float
    fair_floor: float | None
    delta_control: float | None
    disp: float
    novig_bid_side: float | None
    novig_ask_side: float | None
    rung: str


@dataclass(frozen=True)
class LiveQuoteEvaluation:
    outcome: str
    reason: str
    rung: str
    bid: float | None
    contracts: int
    cost: float
    headroom: float
    committed_after: float


def view_from_decision(
    candidate: maker.MakerCandidate,
    decision: maker.MakerDecision,
    cfg: LiveMakerConfig = LiveMakerConfig(),
) -> CandidateView:
    """Normalize a (MakerCandidate, MakerDecision) pair into a side-specific CandidateView."""
    side = decision.side
    if side == "no":
        p_cons_side = 1.0 - float(candidate.p_cons)
        bid_side = (1.0 - float(candidate.novig_ask_over)) if candidate.novig_ask_over is not None else None
        ask_side = (1.0 - float(candidate.novig_bid_over)) if candidate.novig_bid_over is not None else None
    else:
        p_cons_side = float(candidate.p_cons)
        bid_side = float(candidate.novig_bid_over) if candidate.novig_bid_over is not None else None
        ask_side = float(candidate.novig_ask_over) if candidate.novig_ask_over is not None else None

    rung = assign_rung(candidate.game_key, candidate.league, candidate.stat, cfg)
    return CandidateView(
        game_key=candidate.game_key,
        league=candidate.league,
        stat=candidate.stat,
        side=side,
        mode=decision.mode,
        p_cons_side=p_cons_side,
        fair_floor=decision.fair_floor,
        delta_control=decision.delta,
        disp=float(candidate.disp),
        novig_bid_side=bid_side,
        novig_ask_side=ask_side,
        rung=rung,
    )


def evaluate_quote(
    view: CandidateView,
    *,
    cfg: LiveMakerConfig,
    balance: float,
    committed: float,
) -> LiveQuoteEvaluation:
    """Pure 7-step gate pipeline in exact spec order (first failure wins)."""
    committed_f = float(committed)
    balance_f = float(balance)
    headroom = (
        min(balance_f, float(cfg.aggregate_cap_usd)) - committed_f
        if cfg.aggregate_cap_usd is not None
        else 0.0
    )

    def _skip(
        reason: str,
        *,
        bid: float | None = None,
        contracts: int = 0,
        cost: float = 0.0,
    ) -> LiveQuoteEvaluation:
        return LiveQuoteEvaluation(
            outcome="SKIPPED",
            reason=reason,
            rung=view.rung,
            bid=bid,
            contracts=contracts,
            cost=cost,
            headroom=headroom,
            committed_after=committed_f,
        )

    if view.mode != "MAKER_PAPER":
        return _skip(f"MODE_{view.mode}")

    bid, bid_reason = rung_bid(
        rung=view.rung,
        p_cons_side=view.p_cons_side,
        fair_floor=view.fair_floor,
        delta_control=view.delta_control,
        novig_bid_side=view.novig_bid_side,
        disp=view.disp,
        cfg=cfg,
    )
    if bid is None:
        return _skip(bid_reason)

    if view.novig_ask_side is not None and bid >= view.novig_ask_side:
        return _skip("CROSSES_ASK", bid=bid)

    target_liab = min(float(cfg.quote_liability_usd), float(cfg.per_quote_cap_usd))
    contracts = maker.quote_contracts(bid, target_liab)
    if contracts <= 0:
        return _skip("SIZE_ZERO", bid=bid, contracts=0, cost=0.0)

    cost = round(contracts * bid, 6)
    if cost > float(cfg.per_quote_cap_usd) + 1e-9:
        return _skip("PER_QUOTE_CAP", bid=bid, contracts=contracts, cost=cost)

    if cfg.aggregate_cap_usd is None:
        return _skip("AGGREGATE_CAP_UNSET", bid=bid, contracts=contracts, cost=cost)

    if cost > headroom + 1e-9:
        return _skip("AGGREGATE_CAP", bid=bid, contracts=contracts, cost=cost)

    return LiveQuoteEvaluation(
        outcome="QUOTED",
        reason="",
        rung=view.rung,
        bid=bid,
        contracts=contracts,
        cost=cost,
        headroom=headroom,
        committed_after=round(committed_f + cost, 6),
    )


# ------------------------------------------------------------------ exposure book
@dataclass
class ExposureBook:
    """Per-order split exposure accounting (resting + filled-unsettled)."""

    orders: dict[str, dict[str, float]] = field(default_factory=dict)

    def committed(self) -> float:
        return sum(float(v["resting"]) + float(v["filled"]) for v in self.orders.values())

    def headroom(self, balance: float, cfg: LiveMakerConfig) -> float:
        if cfg.aggregate_cap_usd is None:
            return 0.0
        return min(float(balance), float(cfg.aggregate_cap_usd)) - self.committed()

    def add_open(self, order_id: str, cost: float) -> None:
        self.orders[order_id] = {"resting": float(cost), "filled": 0.0}

    def apply_fill(self, order_id: str, filled_cost: float) -> None:
        entry = self.orders.get(order_id)
        if entry is None or filled_cost <= 0:
            return
        move = min(float(entry["resting"]), float(filled_cost))
        entry["resting"] = max(0.0, float(entry["resting"]) - move)
        entry["filled"] = float(entry["filled"]) + move

    def settle(self, order_id: str) -> None:
        entry = self.orders.get(order_id)
        if entry is None:
            return
        entry["filled"] = 0.0
        self.drop(order_id)

    def release(self, order_id: str) -> None:
        entry = self.orders.get(order_id)
        if entry is None:
            return
        entry["resting"] = 0.0
        self.drop(order_id)

    def drop(self, order_id: str) -> None:
        entry = self.orders.get(order_id)
        if entry is None:
            return
        if entry["resting"] <= 1e-12 and entry["filled"] <= 1e-12:
            self.orders.pop(order_id, None)


# ------------------------------------------------------------------ flag sources
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


# ------------------------------------------------------------------ decision log
class DecisionLog:
    """Append-only JSONL log of every live-maker evaluation and lifecycle event."""

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
        payload["kind"] = "novig_live_maker_decision"
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


# ------------------------------------------------------------------ venues
def client_id_for_live(intent_id: str) -> str:
    """Deterministic UUID per live maker intent."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ev-novig-live-maker/" + intent_id))


class LiveMakerVenue:
    """Resting live placement against production Novig API (reconcile-don't-resend)."""

    def __init__(self, client: NovigClient):
        if getattr(client, "env", None) != PROD_ENV:
            raise NovigProductionNotEnabled(
                f"LiveMakerVenue requires a {PROD_ENV!r} client, got {getattr(client, 'env', None)!r}"
            )
        self.client = client

    def place_resting(
        self,
        leg,
        *,
        intent_id: str,
        side: str,
        price: float,
        contracts_units: float,
    ) -> dict:
        """Place a single post-only (PO) resting bid. At most one POST; never resends on ambiguity."""
        cid = client_id_for_live(intent_id)
        qty = int(round(contracts_units * UNIT))
        if qty <= 0:
            return {
                "status": "ERROR",
                "order_id": None,
                "client_id": cid,
                "market_id": leg.market_id,
                "qty": 0,
                "price": None,
                "error": "qty rounds to zero",
                "raw": {},
            }

        outcome_id = leg.over_id if side == "yes" else leg.under_id
        snapped = snap_price(price, "down")
        meta = {
            "market_id": leg.market_id,
            "outcome_id": outcome_id,
            "qty": qty,
            "price": snapped,
            "description": getattr(leg, "description", ""),
        }

        try:
            resp = self.client.place_order(outcome_id, snapped, qty, "PO", client_id=cid)
        except NovigAPIError as e:
            if e.status < 500:
                return {
                    "status": "ERROR",
                    "order_id": None,
                    "client_id": cid,
                    "market_id": leg.market_id,
                    "qty": qty,
                    "price": snapped,
                    "error": str(e)[:300],
                    "raw": dict(meta, rejected=str(e)[:300]),
                }
            return self._reconcile_ambiguous(cid, leg.market_id, qty, snapped, meta, repr(e)[:300])
        except (requests.RequestException, OSError) as e:
            return self._reconcile_ambiguous(cid, leg.market_id, qty, snapped, meta, repr(e)[:300])

        order_id = (resp or {}).get("orderId") or (resp or {}).get("id")
        return {
            "status": "OPEN",
            "order_id": str(order_id) if order_id is not None else None,
            "client_id": cid,
            "market_id": leg.market_id,
            "qty": qty,
            "price": snapped,
            "raw": resp,
        }

    def _reconcile_ambiguous(
        self,
        cid: str,
        market_id: str,
        qty: int,
        price: str,
        meta: dict,
        why: str,
    ) -> dict:
        """After a 5xx or connection error: check fills by clientId before declaring UNKNOWN."""
        try:
            fills = self.fills_for(cid)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "UNKNOWN",
                "order_id": None,
                "client_id": cid,
                "market_id": market_id,
                "qty": qty,
                "price": price,
                "error": f"{why}; reconcile failed: {exc!r}"[:400],
                "raw": dict(meta, ambiguous=why),
            }

        if fills:
            oid = fills[0].get("orderId")
            return {
                "status": "OPEN",
                "reconciled": True,
                "order_id": str(oid) if oid is not None else None,
                "client_id": cid,
                "market_id": market_id,
                "qty": qty,
                "price": price,
                "raw": dict(meta, ambiguous=why, reconciled=True, fills=fills),
            }

        return {
            "status": "UNKNOWN",
            "order_id": None,
            "client_id": cid,
            "market_id": market_id,
            "qty": qty,
            "price": price,
            "error": f"{why}; no fill found by clientId (not re-sent)"[:400],
            "raw": dict(meta, ambiguous=why),
        }

    def cancel(self, order_id: str) -> dict:
        return self.client.cancel_order(order_id)

    def get_order(self, order_id: str) -> dict:
        return self.client.get_order(order_id)

    def list_open_orders(self) -> list[dict]:
        out: list[dict] = []
        q: dict[str, Any] = {}
        for _ in range(MAX_PAGES):
            page = self.client.list_orders(**q) or {}
            items = page.get("items", [])
            for o in items:
                st = str(o.get("status") or "").upper()
                if st and st not in _OPEN_STATUSES:
                    continue
                out.append(o)
            nxt = page.get("next")
            if not nxt or not items:
                break
            q = {"after": nxt}
        return out

    def fills_for(self, client_id: str) -> list[dict]:
        out: list[dict] = []
        q: dict[str, Any] = {}
        for _ in range(MAX_PAGES):
            page = self.client.fills(**q) or {}
            items = page.get("items", [])
            out.extend([f for f in items if f.get("clientId") == client_id])
            nxt = page.get("next")
            if not nxt or not items:
                break
            q = {"after": nxt}
        return out


class DryRunVenue:
    """Zero-network offline stub venue recording attempts and returning synthetic order ids."""

    def __init__(self) -> None:
        self.attempts: list[dict] = []
        self.cancels: list[str] = []

    def place_resting(
        self,
        leg,
        *,
        intent_id: str,
        side: str,
        price: float,
        contracts_units: float,
    ) -> dict:
        cid = client_id_for_live(intent_id)
        qty = int(round(contracts_units * UNIT))
        snapped = snap_price(price, "down")
        self.attempts.append(
            {
                "intent_id": intent_id,
                "client_id": cid,
                "market_id": leg.market_id,
                "side": side,
                "price": snapped,
                "qty": qty,
                "contracts_units": contracts_units,
            }
        )
        return {
            "status": "OPEN",
            "order_id": f"dryrun-{len(self.attempts)}",
            "client_id": cid,
            "market_id": leg.market_id,
            "qty": qty,
            "price": snapped,
            "raw": {},
        }

    def cancel(self, order_id: str) -> dict:
        self.cancels.append(order_id)
        return {}

    def get_order(self, order_id: str) -> dict:
        return {}

    def list_open_orders(self) -> list[dict]:
        return []

    def fills_for(self, client_id: str) -> list[dict]:
        return []


# ------------------------------------------------------------------ time helpers
def _ts_float(val: float | datetime) -> float:
    if isinstance(val, datetime):
        dt = val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return float(val)


def _ts_dt(val: float | datetime) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(float(val), tz=timezone.utc)


# ------------------------------------------------------------------ quoter
class NovigLiveMaker:
    """Novig live passive-maker quoter wrapping rung assignment, gates, exposure, and settlement."""

    def __init__(
        self,
        venue,
        flag_source,
        balance_provider: Callable[[], float],
        decision_log: DecisionLog,
        cfg: LiveMakerConfig = LiveMakerConfig(),
        clock: Callable[[], float] = time.time,
        env: Mapping[str, str] | None = None,
        resolver=None,
    ):
        self.venue = venue
        self.flag_source = flag_source
        self.balance_provider = balance_provider
        self.decision_log = decision_log
        self.cfg = cfg
        self._clock = clock
        self._env = os.environ if env is None else env
        self.resolver = resolver
        self.balance = 0.0
        self.exposure = ExposureBook()
        self._open_meta: dict[str, dict[str, Any]] = {}
        self._pending_settlement_logged: set[str] = set()
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            self.balance = float(self.balance_provider())
            enabled, stopped, readable = self.flag_source.flags()
            self.decision_log.record(
                {
                    "event": "SESSION_START",
                    "outcome": "SESSION_START",
                    "reason": "",
                    "enabled": enabled,
                    "stopped": stopped,
                    "readable": readable,
                    "balance": self.balance,
                    "env_enabled": self._env.get(ENABLE_ENV) == "1",
                    "aggregate_cap_usd": self.cfg.aggregate_cap_usd,
                }
            )

    def _skip_result(
        self,
        *,
        rung: str,
        reason: str,
    ) -> LiveQuoteEvaluation:
        return LiveQuoteEvaluation(
            outcome="SKIPPED",
            reason=reason,
            rung=rung,
            bid=None,
            contracts=0,
            cost=0.0,
            headroom=self.exposure.headroom(self.balance, self.cfg),
            committed_after=self.exposure.committed(),
        )

    def on_candidate(
        self,
        candidate: maker.MakerCandidate,
        leg,
        *,
        now: float | datetime | None = None,
        sharp_at_post: Mapping[str, float] | None = None,
        book_fair: Mapping[str, float] | None = None,
    ) -> LiveQuoteEvaluation | None:
        with self._lock:
            now_f = _ts_float(self._clock() if now is None else now)
            rung = assign_rung(candidate.game_key, candidate.league, candidate.stat, self.cfg)

            enabled, stopped, readable = self.flag_source.flags()
            if not readable:
                self.decision_log.record(
                    {
                        "event": "EVALUATION",
                        "game_key": candidate.game_key,
                        "league": candidate.league,
                        "stat": candidate.stat,
                        "rung": rung,
                        "outcome": "SKIPPED",
                        "reason": "FLAG_UNREADABLE",
                        "ts": now_f,
                    }
                )
                return self._skip_result(rung=rung, reason="FLAG_UNREADABLE")
            if stopped:
                self.decision_log.record(
                    {
                        "event": "EVALUATION",
                        "game_key": candidate.game_key,
                        "league": candidate.league,
                        "stat": candidate.stat,
                        "rung": rung,
                        "outcome": "SKIPPED",
                        "reason": "STOPPED",
                        "ts": now_f,
                    }
                )
                return self._skip_result(rung=rung, reason="STOPPED")
            if not enabled or self._env.get(ENABLE_ENV) != "1":
                self.decision_log.record(
                    {
                        "event": "EVALUATION",
                        "game_key": candidate.game_key,
                        "league": candidate.league,
                        "stat": candidate.stat,
                        "rung": rung,
                        "outcome": "SKIPPED",
                        "reason": "DISABLED",
                        "ts": now_f,
                    }
                )
                return self._skip_result(rung=rung, reason="DISABLED")

            decision = maker.decide(candidate)
            lk = leg_key(leg)
            if decision.side is not None:
                for meta in self._open_meta.values():
                    if (
                        not meta.get("terminal")
                        and meta.get("leg_key") == lk
                        and meta.get("side") == decision.side
                    ):
                        self.decision_log.record(
                            {
                                "event": "EVALUATION",
                                "game_key": candidate.game_key,
                                "league": candidate.league,
                                "stat": candidate.stat,
                                "side": decision.side,
                                "rung": rung,
                                "outcome": "SKIPPED",
                                "reason": "ALREADY_RESTING",
                                "ts": now_f,
                            }
                        )
                        return self._skip_result(rung=rung, reason="ALREADY_RESTING")

            view = view_from_decision(candidate, decision, self.cfg)
            ev = evaluate_quote(
                view,
                cfg=self.cfg,
                balance=self.balance,
                committed=self.exposure.committed(),
            )
            self.decision_log.record(
                {
                    "event": "EVALUATION",
                    "game_key": view.game_key,
                    "league": view.league,
                    "stat": view.stat,
                    "side": view.side,
                    "rung": ev.rung,
                    "bid": ev.bid,
                    "contracts": ev.contracts,
                    "cost": ev.cost,
                    "outcome": ev.outcome,
                    "reason": ev.reason,
                    "headroom": ev.headroom,
                    "committed_after": ev.committed_after,
                    "ts": now_f,
                }
            )
            if ev.outcome != "QUOTED":
                return ev

            start_utc = getattr(leg, "start_utc", None)
            if isinstance(start_utc, datetime):
                commence = start_utc if start_utc.tzinfo is not None else start_utc.replace(tzinfo=timezone.utc)
            else:
                commence = datetime.fromtimestamp(now_f + candidate.mins_to_start * 60.0, tz=timezone.utc)
            game_day = commence.astimezone(_ET).date().isoformat()

            intent_id = (
                f"{candidate.game_key}|{candidate.league}|{candidate.stat}|"
                f"{candidate.player_norm}|{candidate.line}|{view.side}|{game_day}"
            )
            ack = self.venue.place_resting(
                leg,
                intent_id=intent_id,
                side=view.side,
                price=ev.bid,
                contracts_units=ev.contracts,
            )

            status = str((ack or {}).get("status") or "ERROR")
            if status == "OPEN" and (ack or {}).get("order_id"):
                order_id = str(ack["order_id"])
                self.exposure.add_open(order_id, ev.cost)
                if sharp_at_post is not None:
                    sharp_side = dict(sharp_at_post)
                elif book_fair is not None:
                    sharp_side = {
                        b: (float(book_fair[b]) if view.side == "yes" else 1.0 - float(book_fair[b]))
                        for b in maker.SHARP_BOOKS
                        if b in book_fair
                    }
                else:
                    sharp_side = {}

                self._open_meta[order_id] = {
                    "order_id": order_id,
                    "leg_key": lk,
                    "game_key": candidate.game_key,
                    "game_day": game_day,
                    "sport": candidate.league.lower(),
                    "league": candidate.league,
                    "stat": candidate.stat,
                    "player_norm": candidate.player_norm,
                    "line": candidate.line,
                    "side": view.side,
                    "rung": ev.rung,
                    "client_id": ack["client_id"],
                    "intent_id": intent_id,
                    "bid": ev.bid,
                    "contracts_units": ev.contracts,
                    "filled_units": 0.0,
                    "p_fill": None,
                    "p_cons_at_post": view.p_cons_side,
                    "p_cons_side_at_post": view.p_cons_side,
                    "commence": commence,
                    "sharp_at_post": sharp_side,
                    "t_post": now_f,
                    "terminal": False,
                    "settled": False,
                }
                self.decision_log.record(
                    {
                        "event": "QUOTED",
                        "order_id": order_id,
                        "client_id": ack["client_id"],
                        "game_key": candidate.game_key,
                        "league": candidate.league,
                        "stat": candidate.stat,
                        "side": view.side,
                        "rung": ev.rung,
                        "bid": ev.bid,
                        "contracts": ev.contracts,
                        "cost": ev.cost,
                        "outcome": "QUOTED",
                        "reason": "",
                        "headroom": ev.headroom,
                        "committed_after": self.exposure.committed(),
                        "ts": now_f,
                    }
                )
                return ev

            place_event = f"PLACE_{status}"
            self.decision_log.record(
                {
                    "event": place_event,
                    "game_key": candidate.game_key,
                    "league": candidate.league,
                    "stat": candidate.stat,
                    "side": view.side,
                    "rung": ev.rung,
                    "outcome": place_event,
                    "reason": place_event,
                    "error": (ack or {}).get("error"),
                    "committed_after": self.exposure.committed(),
                    "ts": now_f,
                }
            )
            return replace(
                ev,
                outcome="SKIPPED",
                reason=place_event,
                committed_after=self.exposure.committed(),
            )

    def poll_open_orders(
        self,
        states: dict | None = None,
        *,
        now: float | datetime | None = None,
    ) -> list[str]:
        with self._lock:
            now_val = self._clock() if now is None else now
            now_f = _ts_float(now_val)
            now_dt = _ts_dt(now_val)

            _enabled, stopped, readable = self.flag_source.flags()
            if not readable:
                self.cancel_all_open("FLAG_UNREADABLE")
                return []
            if stopped:
                self.cancel_all_open("STOP_FLAG")
                return []

            state_map = states or {}
            cancelled: list[str] = []
            for order_id, meta in list(self._open_meta.items()):
                if meta.get("terminal"):
                    continue
                rq = maker.RestingQuote(
                    game_key=meta["game_key"],
                    league=meta["league"],
                    stat=meta["stat"],
                    side=meta["side"],
                    bid_price=float(meta["bid"]),
                    t_post=_ts_dt(meta["t_post"]),
                    commence=_ts_dt(meta["commence"]),
                    p_cons_side_at_post=float(meta["p_cons_side_at_post"]),
                    sharp_at_post=meta.get("sharp_at_post") or {},
                )
                st = state_map.get(order_id)
                if st is not None:
                    p_cons_side_now = float(st.get("p_cons_side_now", meta["p_cons_side_at_post"]))
                    sharp_now = st.get("sharp_now") or {}
                    n_books_now = int(st.get("n_books_now", maker.MIN_BOOKS))
                    snapshot_age_sec_now = float(st.get("snapshot_age_sec_now", 0.0))
                    status_changed = frozenset(st.get("status_changed_game_keys") or ())
                else:
                    # Absence from states must not fabricate a stale trigger; TTL and T-5 still apply.
                    p_cons_side_now = float(meta["p_cons_side_at_post"])
                    sharp_now = {}
                    n_books_now = maker.MIN_BOOKS
                    snapshot_age_sec_now = 0.0
                    status_changed = frozenset()

                reasons = maker.cancel_reasons(
                    rq,
                    now=now_dt,
                    p_cons_side_now=p_cons_side_now,
                    sharp_now=sharp_now,
                    n_books_now=n_books_now,
                    snapshot_age_sec_now=snapshot_age_sec_now,
                    status_changed_game_keys=status_changed,
                )
                if reasons:
                    try:
                        self.venue.cancel(order_id)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("cancel failed for %s: %r", order_id, exc)
                    self.exposure.release(order_id)
                    meta["terminal"] = True
                    meta["cancel_reasons"] = list(reasons)
                    self.decision_log.record(
                        {
                            "event": "CANCELLED",
                            "order_id": order_id,
                            "reasons": list(reasons),
                            "outcome": "CANCELLED",
                            "reason": ",".join(reasons),
                            "committed_after": self.exposure.committed(),
                            "ts": now_f,
                        }
                    )
                    cancelled.append(order_id)
            return cancelled

    def cancel_all_open(self, reason: str) -> int:
        with self._lock:
            now_f = _ts_float(self._clock())
            count = 0
            for order_id, meta in list(self._open_meta.items()):
                if meta.get("terminal"):
                    continue
                try:
                    self.venue.cancel(order_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("cancel_all_open failed for %s: %r", order_id, exc)
                self.exposure.release(order_id)
                meta["terminal"] = True
                meta["cancel_reasons"] = [reason]
                count += 1
            self.decision_log.record(
                {
                    "event": "CANCEL_ALL",
                    "outcome": "CANCEL_ALL",
                    "reason": reason,
                    "count": count,
                    "committed_after": self.exposure.committed(),
                    "ts": now_f,
                }
            )
            return count

    def sync_fills(self) -> list[str]:
        with self._lock:
            filled_orders: list[str] = []
            for order_id, meta in list(self._open_meta.items()):
                if meta.get("settled"):
                    continue
                cid = meta.get("client_id")
                if not cid:
                    continue
                fills = self.venue.fills_for(cid)
                if not fills:
                    continue
                total_qty = sum(int(f.get("qty", 0)) for f in fills)
                filled_units_total = total_qty / UNIT
                has_cost = any(f.get("cost") is not None for f in fills)
                if filled_units_total > 0 and has_cost:
                    total_cost = sum(float(f.get("cost", 0.0)) for f in fills)
                    p_fill = round(total_cost / filled_units_total, 6)
                else:
                    p_fill = meta.get("p_fill")

                prev_units = float(meta.get("filled_units") or 0.0)
                if filled_units_total > prev_units + 1e-9:
                    delta_units = filled_units_total - prev_units
                    delta_cost = delta_units * float(meta["bid"])
                    self.exposure.apply_fill(order_id, delta_cost)
                    meta["filled_units"] = filled_units_total
                    meta["p_fill"] = p_fill
                    if filled_units_total >= float(meta["contracts_units"]) - 1e-9:
                        meta["terminal"] = True
                    try:
                        self.balance = float(self.balance_provider())
                    except Exception:  # noqa: BLE001
                        log.warning("balance refresh after fill on %s failed", order_id, exc_info=True)
                    self.decision_log.record(
                        {
                            "event": "FILLED",
                            "order_id": order_id,
                            "outcome": "FILLED",
                            "reason": "",
                            "filled_units": filled_units_total,
                            "p_fill": p_fill,
                            "balance": self.balance,
                            "committed_after": self.exposure.committed(),
                            "ts": _ts_float(self._clock()),
                        }
                    )
                    filled_orders.append(order_id)
            return filled_orders

    def sweep_settlements(self) -> list[str]:
        with self._lock:
            resolver = self.resolver
            if resolver is None and self.cfg.boxscores_csv:
                resolver = NHLSogResolver(self.cfg.boxscores_csv)
            settled_ids: list[str] = []
            for order_id, meta in list(self._open_meta.items()):
                if meta.get("settled"):
                    continue
                entry = self.exposure.orders.get(order_id)
                if entry is None or float(entry.get("filled", 0.0)) <= 0.0:
                    continue
                p_fill = meta.get("p_fill")
                fill_input = {
                    "order_id": order_id,
                    "sport": meta["sport"],
                    "stat": meta["stat"],
                    "side": meta["side"],
                    "p_fill": float(p_fill if p_fill is not None else meta["bid"]),
                    "fill_qty_units": float(meta["filled_units"]),
                    "p_cons_at_post": meta["p_cons_at_post"],
                    "p_cons_close": None,
                    "game_key": meta["game_key"],
                    "player_norm": meta["player_norm"],
                    "line": meta["line"],
                    "game_day": meta["game_day"],
                }
                rec = settle_fill(
                    fill_input,
                    resolver=resolver,
                    venue="novig",
                    lane_id="MAK-03",
                    rung_id=meta["rung"],
                    mode="LIVE",
                )
                if rec["settlement_status"] in _SETTLED_STATUSES:
                    self.exposure.settle(order_id)
                    meta["settled"] = True
                    try:
                        self.balance = float(self.balance_provider())
                    except Exception:  # noqa: BLE001
                        log.warning("balance refresh after settlement of %s failed", order_id, exc_info=True)
                    sp = rec.get("settlement_price")
                    won = True if sp == 1.0 else (False if sp == 0.0 else None)
                    self.decision_log.record(
                        {
                            "event": "SETTLED",
                            "order_id": order_id,
                            "outcome": "SETTLED",
                            "reason": "",
                            "settlement_status": rec["settlement_status"],
                            "won": won,
                            "net_realized_pnl_usd": rec["net_realized_pnl_usd"],
                            "balance": self.balance,
                            "committed_after": self.exposure.committed(),
                            "ts": _ts_float(self._clock()),
                        }
                    )
                    settled_ids.append(order_id)
                else:
                    if order_id not in self._pending_settlement_logged:
                        self._pending_settlement_logged.add(order_id)
                        self.decision_log.record(
                            {
                                "event": "SETTLEMENT_PENDING",
                                "order_id": order_id,
                                "outcome": "SETTLEMENT_PENDING",
                                "reason": rec.get("unreconciled_reason") or rec["settlement_status"],
                                "settlement_status": rec["settlement_status"],
                                "committed_after": self.exposure.committed(),
                                "ts": _ts_float(self._clock()),
                            }
                        )
            return settled_ids


# ------------------------------------------------------------------ client factories
def build_prod_client(
    key_id: str | None,
    private_key_pem: bytes | None,
    env: Mapping[str, str] | None = None,
) -> NovigClient:
    """Only supported factory for production NovigClient in the MAK-03 live-maker path."""
    environ = os.environ if env is None else env
    if environ.get(ENABLE_ENV) != "1":
        raise NovigProductionNotEnabled(f"production live maker disabled: set {ENABLE_ENV}=1")
    return NovigClient(PROD_ENV, key_id, private_key_pem)


def build_readonly_client(
    key_id: str | None,
    private_key_pem: bytes | None,
) -> NovigClient:
    """Read-only production client (signed GETs only, e.g., account balance)."""
    return NovigClient(PROD_READONLY_ENV, key_id, private_key_pem)
