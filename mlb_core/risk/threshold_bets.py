"""
mlb_core.risk.threshold_bets -- shared scoring for one-sided "N+" threshold
sub-markets (2026-08-19): BATTER_TB_2PLUS/3PLUS, BATTER_HITS_2PLUS/3PLUS,
K_2PLUS/3PLUS, OUTS_2PLUS/3PLUS.

These are structurally identical to HR's existing "at least 1 HR" yes/no
market -- a single quoted price, no complementary "under N" side to pair
against for a two-way devig -- so the scoring math mirrors run_hr.py's
pattern exactly: devig_unilateral() with an empirical per-(market,book) vig
lookup (mlb.analysis.book_vig.get_vig), falling back to a flat default vig
while a given market has no settled history yet to fit against.

Kept in one place because four systems (K/OUTS/BATTER_TB/BATTER_HITS) need
the identical kelly-stake/exposure-cap/edge-gate math for their own N=2/N=3
thresholds -- copying this out four times risked the kind of small
kelly/cap drift finding B3.3-adjacent bugs come from.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Long-shot props are known to carry heavier vig than standard O/U lines --
# start conservative (higher than HR's 7%) until book_vig.py's weekly refit
# has real settled history for these brand-new market keys to fit against.
DEFAULT_THRESHOLD_VIG_PCT = 0.10


def _get_vig(vig_market_key: str, bookmaker: str | None) -> float:
    try:
        from mlb.analysis.book_vig import get_vig
        return get_vig(vig_market_key, bookmaker or "", default=DEFAULT_THRESHOLD_VIG_PCT)
    except Exception:  # noqa: BLE001 -- vig lookup is best-effort, same as run_hr.py
        return DEFAULT_THRESHOLD_VIG_PCT


def score_threshold_bet(
    *,
    model_prob_raw: float,
    alt_odds_info: dict,
    vig_market_key: str,
    game_pk: int,
    bankroll: float,
    prefetched_stakes: dict,
    pending_stakes: dict,
    cfg: dict,
    gate_suppressed: bool,
) -> tuple[dict | None, float]:
    """Score one 'N+' threshold bet. Returns (bet_row_or_None, new_bankroll).

    `alt_odds_info` is one entry from sgo.py's extract_*_alt_line_odds()
    output: {odds, line, away_team, home_team, event_id, bookmaker}.
    `model_prob_raw` is the model's own P(X >= N) for this threshold
    (already computed by each system's own count simulator/CDF -- this
    function does no modeling, only the market-side scoring math).

    Mutates `pending_stakes` in place on a triggered bet, matching the
    existing per-game exposure-cap accumulation pattern in run_k.py/
    run_batter_tb.py/run_batter_hits.py (each maintains its own dict per
    system, so a threshold bet on the same player/game as the main line
    shares that system's per-game cap by design -- see CONTEXT.md's
    exposure cap contract; not given a separate allowance).
    """
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct
    from mlb_core.odds.utils import devig_unilateral
    from mlb_core.odds import sgo
    from mlb_core.risk.exposure import apply_cap

    odds = alt_odds_info.get("odds")
    if odds is None:
        return None, bankroll

    vig = _get_vig(vig_market_key, alt_odds_info.get("bookmaker"))
    market_prob = american_to_implied_prob(odds)
    fair_prob = devig_unilateral(market_prob, vig_pct=vig)
    model_prob = min(max(model_prob_raw, 0.001), 0.999)
    edge = model_prob - fair_prob

    bankroll, cap = apply_cap(bankroll, game_pk, prefetched_stakes, pending_stakes,
                              cap_units=cfg.get("cap_units", 2.0))
    stake = min(kelly_stake(
        model_prob, odds, bankroll=bankroll,
        fraction=cfg["kelly_fraction"], min_pct=cfg["min_kelly_pct"], max_pct=cfg["max_kelly_pct"],
    ), cap)
    kelly_triggered = (
        edge >= cfg["min_edge"] and stake > 0 and not gate_suppressed
        and not sgo.is_live_event(alt_odds_info.get("commence_time"))
    )
    if kelly_triggered and stake > 0:
        pending_stakes[game_pk] = pending_stakes.get(game_pk, 0.0) + stake

    row = {
        "line":            alt_odds_info.get("line"),
        "model_prob":      round(model_prob, 4),
        "market_prob":     round(fair_prob, 4),
        "edge":            round(edge, 4),
        "kelly_pct":       round(kpct(model_prob, odds, cfg["kelly_fraction"]), 4),
        "odds":            odds,
        "stake":           round(stake, 4) if kelly_triggered else 0.0,
        "kelly_triggered": kelly_triggered,
        "bookmaker":       alt_odds_info.get("bookmaker"),
        "away_team":       alt_odds_info.get("away_team"),
        "home_team":       alt_odds_info.get("home_team"),
    }
    return row, bankroll
