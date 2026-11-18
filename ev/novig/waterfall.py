"""ev/novig/waterfall.py -- Module 7.2: Deterministic 4-State Novig Waterfall.

Pure decision function evaluated for every (game_key, player_norm, stat, line, side).
Import NO venue clients and makes NO order placement or network calls.

States:
  STATE 1: TAKER -- Execute immediately at 0% pre-game fee when L1 ask >= $25 and edge >= 2.5c.
  STATE 2: MAKER_WIDE_STINK_BID -- Post wide resting bid ($5 notional) at queue position Q=0
           when yes_spread >= 0.08 and lineup/news gate clears.
  STATE 3: WITNESS_SINK_A -- Sharp reference only when yes_spread < 0.08 (tight market).
  STATE 4: ABSTAIN -- Pull quotes when consensus/model quality conditions fail or in injury blackout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

WaterfallState = Literal["TAKER", "MAKER_WIDE_STINK_BID", "WITNESS_SINK_A", "ABSTAIN"]


@dataclass(frozen=True)
class WaterfallDecision:
    """Decision output from the 4-State Waterfall."""
    state: WaterfallState
    reason_code: str
    p_fair_floor: float
    p_bid: float | None = None
    notional: float = 0.0
    cancel_resting: bool = False
    rationale: str = ""


def floor_to_005_tick(price: float) -> float:
    """Floor a probability price to the nearest Novig half-cent (0.005) tick."""
    if math.isnan(price) or math.isinf(price):
        return price
    return round(math.floor(round(price / 0.005, 9)) * 0.005, 3)


def evaluate_waterfall(
    *,
    p_cons: float,
    p_blend: float,
    p_model: float,
    p_novig_ask: float,
    p_novig_bid: float,
    n_books: int,
    disp: float,
    fee_charged: str = "WHEN_LIVE",
    l1_ask_dollars: float = 0.0,
    is_injury_blackout: bool = False,
    minutes_to_start: float = 60.0,
    lineup_confirmed: bool = False,
    stat_kind: str = "count",  # "count" | "yardage" | "saves"
    teammate_questionable: bool = False,
    injury_delta_alert: bool = False,
    per_bet_cap: float = 10.0,
    per_player_cap: float = 15.0,
    per_game_cap: float = 30.0,
    current_player_exposure: float = 0.0,
    current_game_exposure: float = 0.0,
) -> WaterfallDecision:
    """Evaluate the 4-State Waterfall for a single Novig proposition.

    Decision flow:
      1. STATE 4 (ABSTAIN):
         Evaluated first. Fires if n_books < 4, disp > 0.06, |P_model - P_cons| > 0.15,
         or inside 20m post-injury news blackout.
      2. STATE 1 (TAKER):
         fee_charged == 'WHEN_LIVE', L1_ask_dollars >= 25.0, and P_fair_floor - P_novig_ask >= 0.025.
         cancel_resting=True carries as data.
      3. STATE 2 (MAKER_WIDE_STINK_BID):
         State 1 did not fire, yes_spread >= 0.08, |P_model - P_cons| <= 0.15.
         Dynamic delta offset applied. If P_bid < P_novig_bid, State 2 is returned with
         p_bid=None and reason_code='MAKER_BEHIND_BOOK_BID' (Q=0 rule).
         At T-5m or on injury_delta_alert, carries cancel_resting=True.
      4. STATE 3 (WITNESS_SINK_A):
         yes_spread < 0.08 (tight book). Midpoint feeds consensus at 2.0x weight.
    """
    p_fair_floor = min(p_cons, p_blend)

    # --------------------------------------------------------------------------
    # 1. STATE 4: ABSTAIN
    # --------------------------------------------------------------------------
    if n_books < 4:
        return WaterfallDecision(
            state="ABSTAIN",
            reason_code="ABSTAIN_N_BOOKS_LT_4",
            p_fair_floor=p_fair_floor,
            p_bid=None,
            notional=0.0,
            cancel_resting=True,
            rationale=f"Insufficient books backing consensus (n_books={n_books} < 4)",
        )

    if disp > 0.06:
        return WaterfallDecision(
            state="ABSTAIN",
            reason_code="ABSTAIN_DISP_GT_0_06",
            p_fair_floor=p_fair_floor,
            p_bid=None,
            notional=0.0,
            cancel_resting=True,
            rationale=f"Excessive book dispersion (disp={disp:.4f} > 0.06)",
        )

    if abs(p_model - p_cons) > 0.15:
        return WaterfallDecision(
            state="ABSTAIN",
            reason_code="ABSTAIN_MODEL_CONS_DIVERGENCE",
            p_fair_floor=p_fair_floor,
            p_bid=None,
            notional=0.0,
            cancel_resting=True,
            rationale=f"Model-consensus divergence > 15pp (|{p_model:.3f} - {p_cons:.3f}| = {abs(p_model - p_cons):.3f})",
        )

    if is_injury_blackout:
        return WaterfallDecision(
            state="ABSTAIN",
            reason_code="ABSTAIN_INJURY_BLACKOUT",
            p_fair_floor=p_fair_floor,
            p_bid=None,
            notional=0.0,
            cancel_resting=True,
            rationale="Inside 20-minute post-injury news repricing blackout window",
        )

    # --------------------------------------------------------------------------
    # 2. STATE 1: TAKER
    # --------------------------------------------------------------------------
    taker_edge = p_fair_floor - p_novig_ask
    if fee_charged == "WHEN_LIVE" and l1_ask_dollars >= 25.0 and taker_edge >= 0.025:
        notional = min(l1_ask_dollars, per_bet_cap)
        return WaterfallDecision(
            state="TAKER",
            reason_code="TAKER_EDGE_CLEARED",
            p_fair_floor=p_fair_floor,
            p_bid=None,
            notional=notional,
            cancel_resting=True,
            rationale=f"Taker edge {taker_edge:.3f} >= 0.025 at $0 fee with ${l1_ask_dollars:.1f} depth",
        )

    # --------------------------------------------------------------------------
    # 3. STATE 2: MAKER_WIDE_STINK_BID
    # --------------------------------------------------------------------------
    yes_spread = p_novig_ask - p_novig_bid
    if yes_spread >= 0.08:
        # Dynamic half-spread offset delta
        is_yardage_or_saves = str(stat_kind).lower() in ("yardage", "saves", "sog", "rec_yds", "rush_yds", "pass_yds")
        delta_0 = 0.035 if is_yardage_or_saves else 0.025
        delta_time = 0.015 if minutes_to_start > 180.0 else 0.0
        delta_news = 0.020 if teammate_questionable else 0.0
        delta = max(0.035, delta_0 + 0.50 * disp + delta_time + delta_news)

        p_raw_bid = p_fair_floor - delta
        floored_bid = floor_to_005_tick(p_raw_bid)
        p_bid_candidate = round(min(floored_bid, p_novig_bid + 0.010), 4)

        # Hard-cancel triggers
        if minutes_to_start < 5.0:
            return WaterfallDecision(
                state="MAKER_WIDE_STINK_BID",
                reason_code="MAKER_T_MINUS_5M_CANCEL",
                p_fair_floor=p_fair_floor,
                p_bid=None,
                notional=0.0,
                cancel_resting=True,
                rationale=f"Hard-cancel resting bid inside T-5m window (minutes_to_start={minutes_to_start:.1f})",
            )

        if injury_delta_alert:
            return WaterfallDecision(
                state="MAKER_WIDE_STINK_BID",
                reason_code="MAKER_INJURY_DELTA_CANCEL",
                p_fair_floor=p_fair_floor,
                p_bid=None,
                notional=0.0,
                cancel_resting=True,
                rationale="Hard-cancel resting bid within <200ms of lineup/injury delta",
            )

        # Liability caps check ($5.00 notional)
        maker_notional = 5.0
        if current_player_exposure + maker_notional > per_player_cap:
            return WaterfallDecision(
                state="MAKER_WIDE_STINK_BID",
                reason_code="LIABILITY_CAP_PLAYER",
                p_fair_floor=p_fair_floor,
                p_bid=None,
                notional=0.0,
                cancel_resting=False,
                rationale=f"Player liability cap exceeded (${current_player_exposure + maker_notional:.2f} > ${per_player_cap:.2f})",
            )

        if current_game_exposure + maker_notional > per_game_cap:
            return WaterfallDecision(
                state="MAKER_WIDE_STINK_BID",
                reason_code="LIABILITY_CAP_GAME",
                p_fair_floor=p_fair_floor,
                p_bid=None,
                notional=0.0,
                cancel_resting=False,
                rationale=f"Game liability cap exceeded (${current_game_exposure + maker_notional:.2f} > ${per_game_cap:.2f})",
            )

        # Queue position Q = 0 rule: only emit if P_bid >= P_novig_bid
        if p_bid_candidate >= p_novig_bid:
            return WaterfallDecision(
                state="MAKER_WIDE_STINK_BID",
                reason_code="MAKER_WIDE_STINK_BID_POSTED",
                p_fair_floor=p_fair_floor,
                p_bid=p_bid_candidate,
                notional=maker_notional,
                cancel_resting=False,
                rationale=f"Posted wide stink bid {p_bid_candidate:.3f} at Q=0 (spread={yes_spread:.3f}, delta={delta:.3f})",
            )
        else:
            return WaterfallDecision(
                state="MAKER_WIDE_STINK_BID",
                reason_code="MAKER_BEHIND_BOOK_BID",
                p_fair_floor=p_fair_floor,
                p_bid=None,
                notional=0.0,
                cancel_resting=False,
                rationale=f"Calculated bid {p_bid_candidate:.3f} is behind book bid {p_novig_bid:.3f} (violates Q=0)",
            )

    # --------------------------------------------------------------------------
    # 4. STATE 3: WITNESS_SINK_A
    # --------------------------------------------------------------------------
    return WaterfallDecision(
        state="WITNESS_SINK_A",
        reason_code="WITNESS_SINK_A_TIGHT_SPREAD",
        p_fair_floor=p_fair_floor,
        p_bid=None,
        notional=0.0,
        cancel_resting=True,
        rationale=f"Tight Novig spread ({yes_spread:.3f} < 0.08); de-vigged Novig midpoint feeds consensus at 2.0x weight",
    )
