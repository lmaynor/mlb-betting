"""
ev.kalshi_resting_replay -- tape-delayed resting simulator (Argon Prong 2, pre-registered 2026-11-11).

Question it answers: if we had posted a resting post-only NO bid on already-created MVE combo
tickers, starting 10 minutes after combo creation (skipping the RFQ creation print, whose fills
grade -40.58c/contract), would the subsequent public tape trades that cross our bid have made
money net of the exact KXMVE maker fee?

Design (Argon verbatim, ~/workspace/beezy/profitability/argon-resting-seat-consult-VERBATIM-2026-11-11.md):
  * Universe: fully-priced combos only (unpriced_legs == 0 under the pricer's fair construction,
    ev.kalshi_rfq_replay MEASURE pass), Oct 16 - Nov 1, 2026 close_time window.
  * Tier 1: SGP-anchored + cross-game combos, 2-4 legs, every leg >= 4 books; cross-game only
    when the priced joint p_yes <= 0.35.
  * Tier 2: 2-3 leg combos anchored by an NHL saves leg whose trained model beats the >= 4-book
    consensus median by >= 3.0pp (directional, in the leg's own side direction).
  * k >= 5 legs only if every leg is exact-priced (not pooled/curve-filled) with joint
    p_yes <= 0.15.
  * Unmeasured same-player pairs are excluded: SAME_PLAYER_ALLOWED_PAIRS is empty, so ANY
    same-player pair inside one game rejects the combo from the resting universe.
  * Bid: P_no_bid = floor_to_cent((1.0 - P_cons_yes) - Edge_class - Fee_reserve), where
    P_cons_yes is the pricer's padded joint (0.008/leg leg_safety_pad, ev.kalshi_rfq_pricer)
    and Fee_reserve = Fee_entry at the bid (the formula is implicit in P_no; solved by
    fixed-point iteration, then floored to the cent).
  * Edge_class: 4.0c SGP different-player / 4.5c approved same-player (unreachable while the
    allowlist is empty) / 5.0c cross-game / 3.5c saves-anchored.
  * Fee: Fee_entry(P) = 0.035 * P * (1 - P) (KXMVE maker coefficient, fee_mult = 2).
    Grades are reported under BOTH treatments: (a) one-way settlement fee (1 x Fee_entry at
    the fill price), (b) full roundtrip (2 x Fee_entry).
  * Fill: every public tape trade at t >= creation + 600s whose yes_price >= 1.00 - P_no_bid
    (a trade exactly at the cross price counts) is a fill of ONE contract at our bid. (Per-
    contract grading unit: the tape print's count_fp is the print size, not our fill size.)
  * Metrics: day-clustered mean net fill P&L under both fee treatments with a day-cluster
    bootstrap 95% CI (same convention as the Candidate 1 replay grader: pooled mean, days
    resampled with replacement, 4000 reps, seed 20261111), fill count n, and the pickoff
    ratio = realized hit rate on crossed orders minus priced P_true_yes, in percentage points.
  * Verdict: PASS = one-way >= +3.50c AND roundtrip >= +2.50c AND 95% lower bound >= +0.50c
    (applied to both treatments) AND n >= 50 AND pickoff <= +2.0pp.
    KILL = mean <= 0.0c net under either treatment OR either 95% lower bound < -2.0c.
    Otherwise NEITHER.

CONFIG BLOCK -- mirrors PR-2's resting engine config (branch feat/kalshi-resting-engine,
built in parallel; values copied from Argon's spec, NOT imported from that branch). If PR-2's
engine config changes a value, change it here too; the two blocks must stay identical.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass

# ---- resting engine config (mirrors PR-2; source: Argon resting-seat consult 2026-11-11) ----
FEE_COEFF = 0.035                      # KXMVE maker fee coefficient (fee_mult = 2)
EDGE_SGP_DIFF_PLAYER = 0.040           # 4.0c
EDGE_SGP_SAME_PLAYER_APPROVED = 0.045  # 4.5c (unreachable: allowlist empty)
EDGE_CROSS_GAME = 0.050                # 5.0c
EDGE_SAVES_ANCHORED = 0.035            # 3.5c
POST_DELAY_SEC = 600.0                 # post 10 minutes after creation; skip the creation print
MIN_LEG_BOOKS = 4                      # every leg >= 4 books
CROSS_GAME_MAX_P_YES = 0.35            # cross-game Tier-1 admission cap on the priced joint
LONG_K_MIN_LEGS = 5                    # k >= 5 path ...
LONG_K_MAX_P_YES = 0.15                # ... only with every leg exact-priced and joint <= 0.15
SAVES_MODEL_EDGE_MIN = 0.030           # Tier-2 saves leg: model beats >= 4-book median by >= 3.0pp
SAME_PLAYER_ALLOWED_PAIRS = frozenset()  # empty per Argon Prong 2 task spec: all same-player pairs excluded

# ---- pre-registered Prong 2 thresholds ----
PASS_ONEWAY_C = 3.50
PASS_ROUNDTRIP_C = 2.50
PASS_LOWER_BOUND_C = 0.50
PASS_MIN_FILLS = 50
PASS_MAX_PICKOFF_PP = 2.0
KILL_LOWER_BOUND_C = -2.0

CLASS_SGP = "sgp_diff_player"
CLASS_SGP_SAME_PLAYER = "sgp_same_player_approved"
CLASS_CROSS_GAME = "cross_game"
CLASS_SAVES = "saves_anchored"

EDGE_BY_CLASS = {CLASS_SGP: EDGE_SGP_DIFF_PLAYER, CLASS_SGP_SAME_PLAYER: EDGE_SGP_SAME_PLAYER_APPROVED,
                 CLASS_CROSS_GAME: EDGE_CROSS_GAME, CLASS_SAVES: EDGE_SAVES_ANCHORED}


def fee_entry(p_no: float) -> float:
    """Exact KXMVE maker fee per contract for one side at price p_no: 0.035 * P * (1 - P)."""
    if p_no <= 0.0 or p_no >= 1.0:
        return 0.0
    return FEE_COEFF * p_no * (1.0 - p_no)


def floor_to_cent(x: float) -> float:
    return round(math.floor(x / 0.01 + 1e-9) * 0.01, 4)


def resting_no_bid(p_cons_yes: float, edge_class: float) -> float:
    """P_no_bid = floor_to_cent((1 - P_cons_yes) - Edge_class - Fee_reserve(P_no_bid)).

    Fee_reserve is Fee_entry evaluated at the bid itself, so solve by fixed-point iteration
    from the fee-free price, flooring to the cent at each step (a lower bid is the safe side)."""
    b = (1.0 - p_cons_yes) - edge_class
    for _ in range(10):
        nb = floor_to_cent((1.0 - p_cons_yes) - edge_class - fee_entry(b))
        if nb == floor_to_cent(b):
            b = nb
            break
        b = nb
    return min(0.99, max(0.0, floor_to_cent(b)))


def saves_model_edge(leg: dict) -> float | None:
    """Directional trained-model edge of an NHL saves leg vs its consensus, in the leg's side
    direction: (p_model_yes - p_cons_yes) for a YES leg, negated for a NO leg. None when the
    leg is not an NHL saves leg or carries no model probability."""
    if leg.get("sport") != "nhl" or leg.get("stat") != "saves":
        return None
    pm, pc = leg.get("p_model_yes"), leg.get("p_cons_yes")
    if pm is None or pc is None:
        return None
    d = float(pm) - float(pc)
    return d if str(leg.get("side", "yes")).lower() == "yes" else -d


def has_same_player_pair(legs: list[dict]) -> bool:
    """True when two legs in the same game share a non-None player (a same-player pair)."""
    seen: dict = {}
    for leg in legs:
        player = leg.get("player")
        if player is None:
            continue
        key = (leg.get("game_key"), player)
        if key in seen:
            return True
        seen[key] = leg
    return False


@dataclass(frozen=True)
class Admission:
    admitted: bool
    tier: str | None          # 'tier1' | 'tier2' | 'long_k'
    edge_class: str | None
    reason: str


def admit_combo(*, n_legs: int, n_unpriced: int, p_cons_yes: float | None, legs: list[dict]) -> Admission:
    """Tier-1 / Tier-2 / long-k admission for one fully-priced combo. `legs` are per-leg dicts
    with at least {sport, stat, game_key, player, side, n_books, pooled, p_cons_yes, p_model_yes}
    for every PRICED leg (the fair-book view at creation, as in ev.kalshi_rfq_replay.leg_records)."""
    if p_cons_yes is None or n_unpriced != 0:
        return Admission(False, None, None, "REJ_NOT_FULLY_PRICED")
    if len(legs) != n_legs:
        return Admission(False, None, None, "REJ_LEG_DETAIL_INCOMPLETE")
    if any(int(leg.get("n_books") or 0) < MIN_LEG_BOOKS for leg in legs):
        return Admission(False, None, None, "REJ_LEG_BOOKS_LT_4")
    if has_same_player_pair(legs):
        # SAME_PLAYER_ALLOWED_PAIRS is empty: no same-player pair is approved for resting.
        return Admission(False, None, None, "REJ_SGP_SAME_PLAYER_UNMEASURED")
    same_game = len({leg.get("game_key") for leg in legs}) < n_legs
    _se = [e for e in (saves_model_edge(leg) for leg in legs) if e is not None]
    saves_edge = max(_se) if _se else -1.0
    if n_legs >= LONG_K_MIN_LEGS:
        if any(bool(leg.get("pooled")) for leg in legs):
            return Admission(False, None, None, "REJ_LONG_K_NOT_EXACT")
        if p_cons_yes > LONG_K_MAX_P_YES:
            return Admission(False, None, None, "REJ_LONG_K_P_GT_0_15")
        cls = CLASS_SGP if same_game else CLASS_CROSS_GAME
        return Admission(True, "long_k", cls, "PLAN")
    if n_legs < 2 or n_legs > 4:
        return Admission(False, None, None, "REJ_LEG_COUNT")
    if n_legs <= 3 and saves_edge >= SAVES_MODEL_EDGE_MIN:
        return Admission(True, "tier2", CLASS_SAVES, "PLAN")
    if same_game:
        return Admission(True, "tier1", CLASS_SGP, "PLAN")
    if p_cons_yes > CROSS_GAME_MAX_P_YES:
        return Admission(False, None, None, "REJ_CROSS_GAME_P_GT_0_35")
    return Admission(True, "tier1", CLASS_CROSS_GAME, "PLAN")


def crosses(yes_price: float, p_no_bid: float) -> bool:
    """A public YES print crosses our resting NO bid when yes_price >= 1.00 - P_no_bid.
    A trade exactly at the cross price counts."""
    return yes_price >= (1.0 - p_no_bid) - 1e-9


def fill_pnl(p_no_bid: float, yes_won: bool, *, roundtrip: bool) -> float:
    """Net P&L per contract of a NO position filled at p_no_bid, held to settlement.
    One-way treatment deducts 1 x Fee_entry; roundtrip deducts 2 x Fee_entry."""
    fee = fee_entry(p_no_bid) * (2.0 if roundtrip else 1.0)
    return (0.0 if yes_won else 1.0) - p_no_bid - fee


def grade_combo_fills(*, created_epoch: float, p_no_bid: float, p_true_yes: float | None,
                      yes_won: bool, trades: list[dict]) -> list[dict]:
    """One fill record per crossing public tape trade at/after creation + POST_DELAY_SEC.
    Trades inside the 10-minute window (including the creation print) never fill."""
    out = []
    for tr in trades:
        t = float(tr["ts"])
        if t < created_epoch + POST_DELAY_SEC:
            continue
        y = float(tr["yes_price"])
        if not crosses(y, p_no_bid):
            continue
        out.append({"ts": t, "yes_price": y,
                    "pnl_oneway": fill_pnl(p_no_bid, yes_won, roundtrip=False),
                    "pnl_roundtrip": fill_pnl(p_no_bid, yes_won, roundtrip=True),
                    "yes_won": bool(yes_won), "p_true_yes": p_true_yes})
    return out


def day_cluster_bootstrap(xs_by_day: dict, reps: int = 4000, seed: int = 20261111) -> dict:
    """Pooled mean with a day-cluster bootstrap 95% CI (Candidate 1 replay convention)."""
    days = sorted(d for d, v in xs_by_day.items() if v)
    pooled = [x for d in days for x in xs_by_day[d]]
    if not pooled:
        return {"n": 0, "days": 0, "mean_c": None, "day_weighted_c": None, "ci95_c": None}
    mean = sum(pooled) / len(pooled)
    daily = {d: sum(xs_by_day[d]) / len(xs_by_day[d]) for d in days}
    dw = sum(daily.values()) / len(daily)
    rng = random.Random(seed)
    boots = []
    for _ in range(reps):
        num = den = 0.0
        for _ in days:
            d = rng.choice(days)
            v = xs_by_day[d]
            num += sum(v)
            den += len(v)
        if den:
            boots.append(num / den)
    boots.sort()
    lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]
    return {"n": len(pooled), "days": len(days), "mean_c": round(mean * 100, 2),
            "day_weighted_c": round(dw * 100, 2), "ci95_c": [round(lo * 100, 2), round(hi * 100, 2)]}


def pickoff_pp(fills: list[dict]) -> float | None:
    """Realized hit rate on crossed orders minus priced P_true_yes, in percentage points.
    Fills without a priced p_true are excluded (admitted combos always carry one)."""
    rows = [f for f in fills if f.get("p_true_yes") is not None]
    if not rows:
        return None
    return round(sum((1.0 if f["yes_won"] else 0.0) - float(f["p_true_yes"]) for f in rows) / len(rows) * 100, 2)


def verdict(oneway: dict, roundtrip: dict, pickoff: float | None) -> str:
    """PASS / KILL / NEITHER from the graded stats, exactly per the pre-registered mapping."""
    n = oneway.get("n") or 0
    mo, mr = oneway.get("mean_c"), roundtrip.get("mean_c")
    if mo is None or mr is None or n == 0:
        return "NEITHER"
    lo_o = oneway["ci95_c"][0]
    lo_r = roundtrip["ci95_c"][0]
    if mo <= 0.0 or mr <= 0.0 or lo_o < KILL_LOWER_BOUND_C or lo_r < KILL_LOWER_BOUND_C:
        return "KILL"
    if (mo >= PASS_ONEWAY_C and mr >= PASS_ROUNDTRIP_C and lo_o >= PASS_LOWER_BOUND_C
            and lo_r >= PASS_LOWER_BOUND_C and n >= PASS_MIN_FILLS
            and pickoff is not None and pickoff <= PASS_MAX_PICKOFF_PP):
        return "PASS"
    return "NEITHER"


def grade_fills(fills_by_day: dict) -> dict:
    """fills_by_day: {day: [fill records]} -> the full Prong 2 graded output."""
    one, rt = defaultdict(list), defaultdict(list)
    all_fills = []
    for d, rows in fills_by_day.items():
        for f in rows:
            one[d].append(f["pnl_oneway"])
            rt[d].append(f["pnl_roundtrip"])
            all_fills.append(f)
    g_one = day_cluster_bootstrap(one)
    g_rt = day_cluster_bootstrap(rt)
    pk = pickoff_pp(all_fills)
    return {"n_fills": len(all_fills), "oneway": g_one, "roundtrip": g_rt,
            "pickoff_pp": pk, "verdict": verdict(g_one, g_rt, pk)}
