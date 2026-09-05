"""
mlb_core.risk.ou_bets -- shared scoring for two-way over/under bets.

Sibling to mlb_core.risk.threshold_bets (2026-08-19's extraction for the
one-sided "N+" sub-markets): this one holds the analogous shared math for
the *main* two-way O/U line -- devig_two_way -> pick side -> calibrate ->
edge-cap -> apply_cap -> kelly_stake -> kelly_triggered -- that was
independently copy-pasted across K's own two sub-markets (K + OUTS, both
in run_k.py) and BATTER_TB/BATTER_HITS's own single O/U line. Extracted
2026-09-04 as part of the broader code-audit dedup pass.

Calibration (mlb_core.risk.calibration.apply) corrects the live
overconfidence /edge-analysis surfaced (largest apparent edges are mostly
model error, not real edge) BEFORE edge is computed; the interim EDGE_CAP
skips a bet whose POST-calibration edge still looks implausibly large
(residual overconfidence), but only once was_calibrated=True so it never
acts on a raw uncalibrated edge. See CONTEXT.md s11 "Prediction calibration
+ edge cap" for the full rationale -- not repeated at each call site.

Deliberately NOT covering everything upstream of devig_two_way: each
system's own p_over/p_under computation (Monte Carlo rung, NegBin CDF,
Gamma CDF, ...), its own market_prob total>0 pre-check (some call sites use
a bare `if total and total > 0:` with no `continue` -- skipping this
market falls through to other code later in the same loop iteration;
others use an explicit `if not total: continue` -- skipping this market
also skips any later code in that same iteration, e.g. a threshold
sub-market loop after it). That pre-check's own control-flow shape is a
real, pre-existing difference between call sites, not incidental
copy-paste -- left untouched at each call site rather than folded in here.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def score_ou_bet(
    *,
    p_over: float,
    p_under: float,
    over_odds,
    under_odds,
    system: str,
    game_pk: int,
    bankroll: float,
    prefetched_stakes: dict,
    pending_stakes: dict,
    cfg: dict,
    gate_suppressed: bool,
    is_live: bool,
    log_only: bool = False,
    cap_units_default: float = 2.0,
    round_stake: bool = True,
    devig_method: str = "shin",
) -> tuple[dict | None, float]:
    """Score one two-way O/U bet. Returns (bet_row_or_None, new_bankroll).

    `p_over`/`p_under` are the model's own already-computed probabilities for
    this line (this function does no modeling, only the market-side scoring
    math -- mirrors score_threshold_bet()'s own division of responsibility).

    Returns (None, bankroll) if the devig produces NaN fair probabilities --
    every call site had an identical `if pd.isna(fair_over) or
    pd.isna(fair_under): continue` guard at this exact point; the caller
    should `continue` its own loop on a None result, exactly as before
    extraction.

    `log_only` ANDs an extra static suppression flag into kelly_triggered
    (e.g. BATTER_TB/BATTER_HITS's own module-level LOG_ONLY) -- K/OUTS have
    no such flag and simply don't pass it, so it defaults to False and is a
    no-op for them.

    `round_stake` matches each call site's own pre-extraction behavior: K/
    OUTS (and score_threshold_bet) round a triggered stake to 4dp;
    BATTER_TB/BATTER_HITS store the raw (unrounded) stake. Preserved as a
    parameter rather than silently unified.

    Mutates `pending_stakes` in place on a triggered bet, matching
    score_threshold_bet()'s identical per-game exposure-cap accumulation
    pattern.
    """
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct
    from mlb_core.odds.utils import devig_two_way
    from mlb_core.risk.exposure import apply_cap
    from mlb_core.risk.calibration import apply as _cal_apply, EDGE_CAP as _EDGE_CAP

    mkt_over  = american_to_implied_prob(over_odds)
    mkt_under = american_to_implied_prob(under_odds)
    fair_over, fair_under = devig_two_way(mkt_over, mkt_under, method=devig_method)
    if pd.isna(fair_over) or pd.isna(fair_under):
        return None, bankroll

    edge_over  = p_over  - fair_over
    edge_under = p_under - fair_under
    if edge_over >= edge_under:
        side, edge, fair, odds, model_prob = "OVER", edge_over, fair_over, over_odds, p_over
    else:
        side, edge, fair, odds, model_prob = "UNDER", edge_under, fair_under, under_odds, p_under

    model_prob, _cal = _cal_apply(system, model_prob)
    edge = model_prob - fair
    edge_capped = _cal and edge > _EDGE_CAP

    bankroll, cap = apply_cap(bankroll, game_pk, prefetched_stakes, pending_stakes,
                              cap_units=cfg.get("cap_units", cap_units_default))
    stake = min(kelly_stake(
        model_prob, odds, bankroll=bankroll,
        fraction=cfg["kelly_fraction"], min_pct=cfg["min_kelly_pct"], max_pct=cfg["max_kelly_pct"],
    ), cap)
    kelly_triggered = (
        edge >= cfg["min_edge"] and stake > 0 and not log_only
        and not gate_suppressed and not edge_capped and not is_live
    )
    if kelly_triggered and stake > 0:
        pending_stakes[game_pk] = pending_stakes.get(game_pk, 0.0) + stake

    row = {
        "side":            side,
        "model_prob":      round(model_prob, 4),
        "market_prob":     round(fair, 4),
        "edge":            round(edge, 4),
        "kelly_pct":       round(kpct(model_prob, odds, cfg["kelly_fraction"]), 4),
        "odds":            odds,
        "stake":           (round(stake, 4) if round_stake else stake) if kelly_triggered else 0.0,
        "kelly_triggered": kelly_triggered,
    }
    return row, bankroll
