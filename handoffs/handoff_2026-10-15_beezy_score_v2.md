# Handoff -- 2026-10-15 -- Beezy Score v2

Branch `feat/beezy-score-v2` (stacked on `fix/ui-consistency-sweep`, PR #61). Frontend only.

## Why
v1 (`beezy-vip/lib/beezy-score.ts`): edge x3 (cap 45) + probability gap x2 (cap 25) + 15 kelly bonus + 15/10/5 price bonus.
For every +EV alert `kelly_triggered` is true and most prices beat -120, so **30 of the points were constant**; the probability
gap is almost the same number as edge (double counted); the price bonus rewarded chalk; a 4% longshot with a 17% edge scored 100.
Result on 7,259 graded alerts: 75% LEAN / 16.5% STRONG / 8% WATCH, and realised ROI by tier was not monotonic (WATCH +10.4% > LEAN +4.8%).

## v2
`score = edge + consensus + hit`, with consensus and hit scaled by `min(edge/2%, 1)` (no edge => 0):
- edge: 4 points per 1% of edge, cap 60
- consensus: `20 * clamp((books - 3) / 9, 0, 1)`; model bets (no books count) get a neutral 10
- hit probability: `20 * clamp((p_win - 0.15) / 0.35, 0, 1)` (longshots penalised)
Tiers: STRONG >= 70, LEAN 45-69, WATCH < 45 (`TIER_CUTS`). Tier labels are now short everywhere (`STRONG`/`LEAN`/`WATCH`; the long
"LEAN PLAY" forms are gone). Hover a score for its parts (`explainScore`); `ScoreLegend` on /picks and /ev publishes the formula.

## Evidence (in-sample, `scripts/beezy_score_backtest.py`)
| tier | n | share | flat ROI | hit |
|---|---|---|---|---|
| STRONG >= 70 | 918 | 13% | +25.8% (+-3.8) | 56.0% |
| LEAN 45-69 | 3,890 | 54% | +6.4% (+-1.9) | 51.9% |
| WATCH < 45 | 2,451 | 34% | +3.5% (+-2.7) | 45.3% |
By decile v2 is monotone at the top (80s +36.6%, 90s +44.9%) and noisy in the middle. Cut points were chosen on the same rows, so expect
them to shrink out of sample; the legend says so. By consensus size ROI rises 5.7% (4-5 books) -> 7.7% (6-8) -> 9.8% (9+).

## Not validated / follow-ups
- Model bets (K, OUTS, HR, ...): no books count, tiers not tested against their outcomes. HR realises -16% ROI; check where HR scores land.
- Re-run the script monthly; if STRONG ROI decays toward LEAN, drop the tiers' claims.
- Rojas Under 0.5 Hits (+7.6%, 5 books, 45.2% win) scored 59 LEAN under v1, 52 LEAN under v2.
