"""
ev.kalshi_rfq_pricer -- fair-value book + RFQ quote pricer for the Kalshi RFQ market maker.

Pure and offline: no network, no clock reads except through `now` arguments, so every gate is unit-testable.

Two classes:
  InMemoryFairBook   {kalshi ticker -> LegFairValue}. Loaded from rows shaped like ev.sweep_nfl_props rows
                     (the leave-Kalshi-out, DFS-excluded sportsbook consensus) or any feed that emits the same
                     keys. Swapped atomically so the WebSocket loop never sees a half-built book.
  KalshiRFQPricer    RFQ -> QuoteDecision. Single-leg RFQs get a two-sided quote; k>=2 combo RFQs get the LAY
                     treatment: yes_bid = 0 (never buy a retail parlay) and only a padded no_bid.

Everything is FAIL-CLOSED: any leg that is unmapped, thin, dispersed, stale, hot (lineup/injury flag) or whose
`side` we do not understand rejects the whole RFQ. All defaults are UNVALIDATED starting points to be calibrated
on the shadow tape (scripts/kalshi_rfq_daemon.py --mode record|shadow) -- none is a measured result.

Conventions that differ from the pasted spec (deliberate):
  * game_key follows the repo's ev.kalshi_map convention '{sport}:{YYYY-MM-DD}:{AWAY}@{HOME}'.
  * a combo leg carries its own `side` ('yes'|'no'); a 'no' leg contributes 1 - p_yes to the joint.
  * the model blend uses mlb_core.risk.calibration.market_blend_prob (validated, c=0 -> pure market for every
    NFL stat and every stat without a walk-forward fit), not a flat 85/15; the >0.15 winner's-curse guard stays.
  * the collateral-ROI test is NET of a fee reserve (the quote-creator fee schedule is not documented, so the
    taker formula is used as a conservative stand-in).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from statistics import NormalDist
from typing import Callable

import numpy as np

from mlb.execution.kalshi.fees import taker_fee
from mlb_core.risk.calibration import market_blend_prob

# ---- reject codes -------------------------------------------------------------------------------------
REJ_UNMAPPED_TICKER = "REJ_UNMAPPED_TICKER"
REJ_LOW_BOOKS = "REJ_LOW_BOOKS"
REJ_HIGH_DISPERSION = "REJ_HIGH_DISPERSION"
REJ_STALE_CONSENSUS = "REJ_STALE_CONSENSUS"
REJ_HOT_LINEUP_SCRATCH = "REJ_HOT_LINEUP_SCRATCH"
REJ_COMBO_LEG_UNVALIDATED = "REJ_COMBO_LEG_UNVALIDATED"
REJ_SGP_DISABLED = "REJ_SGP_DISABLED"
REJ_SGP_SAME_PLAYER = "REJ_SGP_SAME_PLAYER"
REJ_COMBO_NOT_LONGSHOT = "REJ_COMBO_NOT_LONGSHOT"
REJ_LOW_ROI = "REJ_LOW_ROI"
REJ_NO_EDGE = "REJ_NO_EDGE"
REJ_SINGLE_LEG_OFF = "REJ_SINGLE_LEG_OFF"

# Same-game pairwise correlation of the two events happening, both in the "more / yes" direction. UNVALIDATED
# starting values from the spec; the repo's own empirical study (research/sgp-empirical-rho) found real but small
# prop correlations, so these are generous (= conservative for a lay-only maker). Negative entries are clamped to
# 0 at use (we must never underprice positive correlation, and ignoring a negative one only overprices the lay).
SGP_CORR_TABLE = {
    frozenset(("pass_yds", "rec_yds")): 0.38,
    frozenset(("rush_yds", "pass_yds")): -0.15,
    frozenset(("k_over", "nrfi_yes")): 0.22,
}


@dataclass(frozen=True)
class PricerConfig:
    # Leg gates were widened 2026-10-26 (3 / 0.06 / 600 s -> 2 / 0.08 / 1500 s): the feeds refresh every ~10-15 min, so a 600 s
    # staleness limit rejected live legs part of every cycle. Still UNVALIDATED -- the tape's per-leg status shows what binds.
    min_books: int = 2
    max_disp: float = 0.08
    max_staleness_sec: float = 1500.0
    base_half_spread: float = 0.020
    size_addon_cap: float = 0.03
    size_addon_per_500: float = 0.005
    inv_gamma: float = 0.02
    max_ticker_contracts: float = 500.0
    tick: float = 0.01
    quote_single_leg: bool = False          # single-leg props are -EV to make passively (market_making_2026-10-12)
    allow_same_game_combos: bool = False
    max_legs: int = 16
    # A combo may carry up to this many legs we cannot price (unmapped / thin / dispersed / stale). Such a leg is DROPPED from
    # the joint, which is a sound UPPER bound on P(YES) (a leg's probability is <= 1), so the lay quote only gets more
    # conservative. 0 = strict (any unpriced leg rejects the combo). A HOT (scratch/injury) leg always rejects the combo.
    max_unpriced_legs: int = 0
    longshot_max_p: float = 0.35
    per_leg_edge: float = 0.015
    min_combo_edge: float = 0.035
    # The spec's edge is k * per_leg_edge, which is LINEAR in legs: a 14-leg combo would demand 21c, i.e. a NO bid of ~0.78 against
    # a fair ~0.998, which nobody accepts -- the lay could never fill by construction. Cap it (UNVALIDATED; tape will say).
    max_combo_edge: float = 0.06
    leg_safety_pad: float = 0.008
    min_collateral_roi: float = 0.030
    sgp_pair_buffer: float = 0.025
    sgp_default_rho: float = 0.15
    sgp_mc_draws: int = 100_000
    model_guard: float = 0.15               # |p_model - p_cons| above this => trust the market (winner's curse)
    fee_mult: float = 1.0                   # reserve; the quote-creator fee schedule is not documented


@dataclass(frozen=True)
class LegFairValue:
    ticker: str
    sport: str                      # 'nfl' | 'mlb' | 'nba' | 'nhl'
    game_key: str                   # '{sport}:{YYYY-MM-DD}:{AWAY}@{HOME}' (ev.kalshi_map.game_key) or str(game_pk)
    market_class: str               # 'game_ml' | 'game_total' | 'game_spread' | 'nrfi' | 'player_prop'
    player_norm: str | None         # None for game lines
    stat: str
    strike: float
    p_cons_yes: float               # leave-Kalshi-out de-vigged consensus P(YES)
    n_books: int
    disp: float                     # MAD (or max-min) dispersion of per-book fair P
    p_model_yes: float | None       # calibrated model probability when one exists
    pooled: bool                    # consensus came from the adjacent-half-point pooled fallback
    updated_ts: float               # epoch seconds
    is_hot_scratch: bool = False
    p_true_yes: float | None = None  # blended truth; defaults to blend(p_cons, p_model) in __post_init__

    def __post_init__(self):
        if self.p_true_yes is None:
            object.__setattr__(self, "p_true_yes", blend_true_prob(self.p_cons_yes, self.p_model_yes,
                                                                   self.sport, self.stat))


def blend_true_prob(p_cons: float, p_model: float | None, sport: str, stat: str, guard: float = 0.15) -> float:
    """p_cons unless a validated blend weight exists for (sport, stat) AND the model does not diverge by more
    than `guard` (a model that far from 4+ books is the winner's-curse signature: trust the market)."""
    if p_model is None or not math.isfinite(float(p_model)) or abs(float(p_model) - p_cons) > guard:
        return float(p_cons)
    p = market_blend_prob(p_cons, p_model, sport, stat)
    return float(p_cons) if p != p else float(p)


def _num(x, default=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _ts(x, default: float) -> float:
    if x is None:
        return default
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return default


def leg_from_row(row: dict, now: float | None = None) -> LegFairValue | None:
    """One feed row -> LegFairValue, or None when it lacks a ticker / a usable probability.

    Accepts an ev.sweep_nfl_props row (YES == the book's OVER, so `p_over` is P(YES)) or the generic keys
    {ticker, sport, game_key, market_class, player_norm, stat, strike, p_yes, n_books, disp, p_model_yes, pooled,
    updated_ts}."""
    now = time.time() if now is None else now
    ticker = row.get("ticker")
    p = _num(row.get("p_yes", row.get("p_over")))
    if not ticker or p is None or not (0.0 < p < 1.0):
        return None
    gk = str(row.get("game_key") or "")
    sport = str(row.get("sport") or (gk.split(":", 1)[0] if ":" in gk else "")).lower()
    pooled = bool(row.get("pooled")) or row.get("consensus_src") == "pooled"
    return LegFairValue(
        ticker=str(ticker), sport=sport, game_key=gk, market_class=str(row.get("market_class") or "player_prop"),
        player_norm=row.get("player_norm") or None, stat=str(row.get("stat") or ""),
        strike=_num(row.get("strike"), 0.0), p_cons_yes=p, n_books=int(row.get("n_books") or 0),
        disp=_num(row.get("disp"), 1.0), p_model_yes=_num(row.get("p_model_yes")), pooled=pooled,
        updated_ts=_ts(row.get("updated_ts", row.get("ts")), now))


class InMemoryFairBook:
    """ticker -> LegFairValue with an atomic swap and a per-game hot-scratch flag (the last-look trigger)."""

    def __init__(self, hot_ttl_sec: float = 300.0, clock: Callable[[], float] = time.time):
        self._legs: dict[str, LegFairValue] = {}
        self._hot: dict[str, float] = {}            # game_key -> hot-until epoch
        self._prop_indices: dict = {}               # sport -> ev.kalshi_rfq_props.PropFairIndex (ticker-resolved player props)
        self._dyn: dict[str, tuple] = {}            # per-attach cache: ticker -> (leg | None, why); misses are cached too
        self.hot_ttl_sec, self._clock = hot_ttl_sec, clock

    def __len__(self) -> int:
        return len(self._legs)

    def replace_all(self, legs) -> int:
        new = {leg.ticker: leg for leg in legs}
        self._legs = new                             # single reference swap: readers see old or new, never partial
        return len(new)

    def load_rows(self, rows, now: float | None = None) -> int:
        now = self._clock() if now is None else now
        return self.replace_all([lf for lf in (leg_from_row(r, now) for r in rows) if lf is not None])

    def attach_prop_indices(self, indices: dict) -> None:
        """Swap in the per-sport prop indices (and drop the resolution cache, which is only valid for one set of indices)."""
        self._dyn = {}
        self._prop_indices = dict(indices or {})

    def _resolve_prop(self, ticker: str):
        return self._resolve_prop_why(ticker)[0]

    def _resolve_prop_why(self, ticker: str) -> tuple:
        if ticker in self._dyn:
            return self._dyn[ticker]
        out: tuple = (None, "not_a_prop_ticker")
        if self._prop_indices:
            from ev.kalshi_props import ParsedPropTicker, parse_prop_ticker
            parsed = parse_prop_ticker(ticker)
            if not isinstance(parsed, ParsedPropTicker):
                out = (None, "not_a_prop_ticker")
            elif parsed.sport not in self._prop_indices:
                out = (None, "no_sport_index")
            else:
                out = self._prop_indices[parsed.sport].resolve_why(parsed)
        self._dyn[ticker] = out
        return out

    def why_unmapped(self, ticker: str) -> str | None:
        """Why a ticker has no fair value ('no_game', 'no_player', 'no_rung', 'not_a_prop_ticker', ...), for the tape."""
        return None if ticker in self._legs else self._resolve_prop_why(ticker)[1]

    def upsert(self, leg: LegFairValue) -> None:
        self._legs[leg.ticker] = leg

    def mark_hot(self, game_key: str, now: float | None = None, ttl: float | None = None) -> None:
        now = self._clock() if now is None else now
        self._hot[game_key] = now + (self.hot_ttl_sec if ttl is None else ttl)

    def is_hot(self, game_key: str, now: float | None = None) -> bool:
        now = self._clock() if now is None else now
        return self._hot.get(game_key, 0.0) > now

    def get(self, ticker: str, now: float | None = None) -> LegFairValue | None:
        leg = self._legs.get(ticker)
        if leg is None:
            leg = self._resolve_prop(ticker)          # direct feed rows win; otherwise resolve a player prop from its ticker
        if leg is None:
            return None
        return replace(leg, is_hot_scratch=True) if self.is_hot(leg.game_key, now) else leg


# ---- RFQ payload ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class RFQLeg:
    ticker: str
    side: str                       # 'yes' | 'no' (lowercased)
    event_ticker: str | None = None


@dataclass(frozen=True)
class RFQ:
    rfq_id: str
    market_ticker: str
    legs: tuple                     # () for a single-market RFQ, else RFQLeg per mve_selected_legs
    contracts: float | None
    target_cost: float | None
    collection: str | None = None

    @property
    def is_combo(self) -> bool:
        return bool(self.legs)


def parse_rfq(p: dict) -> RFQ:
    """REST rfq object or WS rfq_created `msg` -> RFQ. Combo = non-empty mve_selected_legs (alias: legs)."""
    raw_legs = p.get("mve_selected_legs") or p.get("legs") or []
    legs = tuple(RFQLeg(str(l.get("market_ticker") or l.get("ticker") or ""), str(l.get("side") or "").lower(),
                        l.get("event_ticker")) for l in raw_legs)
    return RFQ(rfq_id=str(p.get("id") or p.get("rfq_id") or ""), market_ticker=str(p.get("market_ticker") or ""),
               legs=legs, contracts=_num(p.get("contracts_fp", p.get("contracts"))),
               target_cost=_num(p.get("target_cost_dollars")), collection=p.get("mve_collection_ticker"))


# ---- decision ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class QuoteDecision:
    action: str                     # 'QUOTE' | 'REJECT'
    reason: str                     # 'PLAN' for a quote, else a REJ_* code
    rfq_id: str = ""
    kind: str = ""                  # 'single' | 'combo'
    yes_bid: float = 0.0
    no_bid: float = 0.0
    p_yes_true: float | None = None     # joint P(YES) used for EV (single: p_skewed-free blended truth)
    p_yes_cons: float | None = None     # padded joint used to set the price (combo)
    edge: float = 0.0
    ev_per_contract: float | None = None
    roi_on_collateral: float | None = None
    contracts: float | None = None
    collateral: float = 0.0
    legs: tuple = ()                    # per-leg snapshot dicts (last-look baseline)
    notes: dict = field(default_factory=dict)

    @property
    def quoted(self) -> bool:
        return self.action == "QUOTE"

    @property
    def yes_bid_fp(self) -> str:
        return f"{self.yes_bid:.4f}"

    @property
    def no_bid_fp(self) -> str:
        return f"{self.no_bid:.4f}"


def floor_to_tick(x: float, tick: float = 0.01) -> float:
    """Round DOWN to the grid (a lower bid is the safe direction), guarded against float noise."""
    return round(math.floor(x / tick + 1e-9) * tick, 4)


def _reject(rfq_id: str, kind: str, reason: str, **notes) -> QuoteDecision:
    return QuoteDecision("REJECT", reason, rfq_id=rfq_id, kind=kind, notes=notes)


def _pair_rho(a: LegFairValue, da: int, b: LegFairValue, db: int, cfg: PricerConfig) -> float:
    base = SGP_CORR_TABLE.get(frozenset((a.stat, b.stat)), cfg.sgp_default_rho)
    return max(0.0, base * da * db)


def _copula_joint(ps: list[float], R: np.ndarray, draws: int) -> float:
    """P(all events) under a Gaussian copula with correlation R (deterministic: fixed seed, common draws)."""
    m = len(ps)
    w, v = np.linalg.eigh(R)
    w = np.clip(w, 1e-6, None)                                   # force PSD, then renormalise to unit diagonal
    R2 = (v * w) @ v.T
    d = np.sqrt(np.diag(R2))
    R2 = R2 / np.outer(d, d)
    L = np.linalg.cholesky(R2)
    z = np.random.default_rng(20261025).standard_normal((draws, m)) @ L.T
    nd = NormalDist()
    thr = np.array([nd.inv_cdf(min(max(p, 1e-6), 1 - 1e-6)) for p in ps])
    p_mc = float(np.mean(np.all(z <= thr, axis=1)))
    return min(min(ps), max(p_mc, float(np.prod(ps))))           # Frechet upper bound; never below independence


class KalshiRFQPricer:
    def __init__(self, book: InMemoryFairBook, cfg: PricerConfig | None = None,
                 inventory_fn: Callable[[str], float] | None = None):
        self.book, self.cfg = book, cfg or PricerConfig()
        self.inventory_fn = inventory_fn or (lambda ticker: 0.0)

    # ---- gates ----
    def leg_gate(self, leg: LegFairValue | None, now: float) -> str | None:
        cfg = self.cfg
        if leg is None:
            return REJ_UNMAPPED_TICKER
        if leg.n_books < cfg.min_books:
            return REJ_LOW_BOOKS
        if leg.disp > cfg.max_disp:
            return REJ_HIGH_DISPERSION
        if now - leg.updated_ts > cfg.max_staleness_sec:
            return REJ_STALE_CONSENSUS
        if leg.is_hot_scratch:
            return REJ_HOT_LINEUP_SCRATCH
        return None

    def price(self, rfq: RFQ | dict, now: float | None = None) -> QuoteDecision:
        now = time.time() if now is None else now
        if isinstance(rfq, dict):
            rfq = parse_rfq(rfq)
        return self._price_combo(rfq, now) if rfq.is_combo else self._price_single(rfq, now)

    # ---- single leg ----
    def _price_single(self, rfq: RFQ, now: float) -> QuoteDecision:
        cfg = self.cfg
        if not cfg.quote_single_leg:
            return _reject(rfq.rfq_id, "single", REJ_SINGLE_LEG_OFF)
        leg = self.book.get(rfq.market_ticker, now)
        bad = self.leg_gate(leg, now)
        if bad:
            return _reject(rfq.rfq_id, "single", bad, ticker=rfq.market_ticker)
        contracts = rfq.contracts or 0.0
        addon = min(cfg.size_addon_cap, cfg.size_addon_per_500 * (contracts / 500.0))
        req_edge = cfg.base_half_spread + addon
        net = self.inventory_fn(leg.ticker)
        skew = cfg.inv_gamma * max(-1.0, min(1.0, net / cfg.max_ticker_contracts))
        p_sk = leg.p_true_yes - skew
        yes_bid = max(0.0, min(0.99, floor_to_tick(p_sk - req_edge, cfg.tick)))
        no_bid = max(0.0, min(0.99, floor_to_tick((1.0 - p_sk) - req_edge, cfg.tick)))
        if yes_bid + no_bid > 1.0 - 2 * cfg.base_half_spread + 1e-9:
            no_bid = max(0.0, floor_to_tick(1.0 - 2 * cfg.base_half_spread - yes_bid, cfg.tick))
        if yes_bid <= 0.0 and no_bid <= 0.0:
            return _reject(rfq.rfq_id, "single", REJ_NO_EDGE, p=leg.p_true_yes)
        snap = (_snap(leg, "yes", leg.p_true_yes),)
        return QuoteDecision("QUOTE", "PLAN", rfq.rfq_id, "single", yes_bid, no_bid, p_yes_true=leg.p_true_yes,
                             edge=req_edge, contracts=contracts or None,
                             collateral=(contracts or 0.0) * max(yes_bid, no_bid), legs=snap,
                             notes={"skew": round(skew, 5), "size_addon": round(addon, 5)})

    # ---- combo ----
    def _price_combo(self, rfq: RFQ, now: float) -> QuoteDecision:
        cfg = self.cfg
        k = len(rfq.legs)
        if k < 2 or k > cfg.max_legs:
            return _reject(rfq.rfq_id, "combo", REJ_COMBO_LEG_UNVALIDATED, why=f"{k} legs")
        if len({l.ticker for l in rfq.legs}) != k:
            return _reject(rfq.rfq_id, "combo", REJ_COMBO_LEG_UNVALIDATED, why="duplicate leg ticker")
        resolved: list[tuple[LegFairValue, str, float]] = []
        status: list[dict] = []                    # one entry per leg, ALWAYS recorded: shows which gate binds, and how many
        hot = bad_side = False
        for l in rfq.legs:
            fv = self.book.get(l.ticker, now)
            bad = self.leg_gate(fv, now)
            if bad is None and l.side not in ("yes", "no"):
                bad, bad_side = f"unknown side {l.side!r}", True
            status.append({"ticker": l.ticker, "status": bad or "OK"})
            if bad == REJ_UNMAPPED_TICKER:
                status[-1]["why"] = self.book.why_unmapped(l.ticker)
            hot = hot or bad == REJ_HOT_LINEUP_SCRATCH
            if bad is None:
                resolved.append((fv, l.side, fv.p_true_yes if l.side == "yes" else 1.0 - fv.p_true_yes))
        n_unpriced = k - len(resolved)
        notes = {"legs_status": status, "n_priced": len(resolved), "n_unpriced": n_unpriced}
        if hot or bad_side or not resolved or n_unpriced > cfg.max_unpriced_legs:
            return _reject(rfq.rfq_id, "combo", REJ_COMBO_LEG_UNVALIDATED, **notes)

        groups: dict[str, list[tuple[LegFairValue, str, float]]] = {}
        for item in resolved:
            groups.setdefault(item[0].game_key, []).append(item)
        sgp = any(len(g) > 1 for g in groups.values())
        if sgp and not cfg.allow_same_game_combos:
            return _reject(rfq.rfq_id, "combo", REJ_SGP_DISABLED, **notes)
        for g in groups.values():
            players = [fv.player_norm for fv, _, _ in g if fv.player_norm]
            if len(players) != len(set(players)):
                return _reject(rfq.rfq_id, "combo", REJ_SGP_SAME_PLAYER, **notes)

        p_true = self._joint(groups, pad=0.0)
        p_cons = self._joint(groups, pad=cfg.leg_safety_pad)
        if p_cons > cfg.longshot_max_p:
            return _reject(rfq.rfq_id, "combo", REJ_COMBO_NOT_LONGSHOT, p_cons=round(p_cons, 4), **notes)
        edge = min(cfg.max_combo_edge, max(cfg.min_combo_edge, k * cfg.per_leg_edge))
        no_bid = min(0.99, floor_to_tick((1.0 - p_cons) - edge, cfg.tick))
        if no_bid <= 0.0:
            return _reject(rfq.rfq_id, "combo", REJ_NO_EDGE, p_cons=round(p_cons, 4), **notes)
        ev = (1.0 - p_true) - no_bid
        fee = taker_fee(no_bid, 100, cfg.fee_mult) / 100.0
        roi = (ev - fee) / no_bid
        snaps = tuple(_snap(fv, side, p) for fv, side, p in resolved)
        notes.update(k=k, same_game=sgp, fee_reserve=round(fee, 5), bounded=n_unpriced > 0)
        if roi < cfg.min_collateral_roi:
            return QuoteDecision("REJECT", REJ_LOW_ROI, rfq.rfq_id, "combo", 0.0, no_bid, p_true, p_cons, edge, ev, roi,
                                 legs=snaps, notes=notes)
        contracts = rfq.contracts
        if contracts is None and rfq.target_cost:
            contracts = rfq.target_cost / max(1.0 - no_bid, cfg.tick)   # requester pays (1 - no_bid) per YES contract
        return QuoteDecision("QUOTE", "PLAN", rfq.rfq_id, "combo", 0.0, no_bid, p_true, p_cons, edge, ev, roi,
                             contracts, (contracts or 0.0) * no_bid, snaps, notes)

    def _joint(self, groups: dict, pad: float) -> float:
        """P(every leg wins). Independent across games; Gaussian copula + per-pair buffer inside a game."""
        cfg, total = self.cfg, 1.0
        for g in groups.values():
            ps = [min(0.99, p + pad) for _, _, p in g]
            if len(g) == 1:
                total *= ps[0]
                continue
            m = len(g)
            dirs = [1 if side == "yes" else -1 for _, side, _ in g]
            R = np.eye(m)
            for i in range(m):
                for j in range(i + 1, m):
                    R[i, j] = R[j, i] = _pair_rho(g[i][0], dirs[i], g[j][0], dirs[j], cfg)
            pairs = m * (m - 1) // 2
            pj = _copula_joint(ps, R, cfg.sgp_mc_draws)
            total *= min(min(ps), pj + cfg.sgp_pair_buffer * pairs)
        return total


def _snap(fv: LegFairValue, side: str, p_leg: float) -> dict:
    """Per-leg baseline stored with the quote; the last-look shield compares against it."""
    return {"ticker": fv.ticker, "game_key": fv.game_key, "player_norm": fv.player_norm, "side": side,
            "p_leg": round(p_leg, 6), "p_true_yes": round(fv.p_true_yes, 6), "n_books": fv.n_books}


def load_feed_rows(source: str) -> tuple[list[dict], float | None]:
    """Read a fair-value feed -> (rows, published_epoch | None). `source` is a local path or a storage key
    (e.g. 'EVSweep/nfl_props/latest.json'). The payload is either a list of rows or {"ts": ..., "rows": [...]}
    (the sweep's latest.json). The published `ts` becomes every row's updated_ts so staleness reflects the pull,
    not the time this process read the file."""
    import os
    if os.path.exists(source):
        with open(source, "rb") as fh:
            raw = fh.read()
    else:
        from mlb_core import storage
        raw = storage.read_bytes(source)
    import json
    data = json.loads(raw)
    if isinstance(data, list):
        return data, None
    return list(data.get("rows") or []), (_ts(data.get("ts"), 0.0) or None)
