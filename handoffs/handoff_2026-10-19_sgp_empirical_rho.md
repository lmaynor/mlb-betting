# Handoff -- 2026-10-19 -- SGP / Gaussian-copula correlation research (MLB)

Branch `research/sgp-empirical-rho` (pushed, no PR). Research only: nothing deployed, nothing wired into any runner, alert or DB.
Files: `scripts/sgp_empirical_rho.py`, `docs/analysis/sgp_empirical_rho_2026-10-19.md` (results table + findings + caveats).

## Where this came from
Two write-ups pasted into the session (not stored verbatim; the ideas are below):
1. **Reverse-engineer DraftKings' SGP pricing.** De-vig each leg, map to normal thresholds `z = Phi^-1(p)`, joint `P(A and B) = Phi2(zA, zB; rho)`.
   Pull all four O/U combinations of a 2-leg SGP, de-vig that 4-cell partition (power method), solve for DK's implied rho with `brentq`, then price
   N-leg parlays from a pairwise correlation matrix (nearest-PSD fix). Claims DK uses archetype correlations, an extra penalty on negatively
   correlated legs, and fatter tails on alt lines. These claims about DK's engine are **unverified speculation**.
2. **Use historical prop lines instead.** Join closing lines with box scores, fit empirical rho by bivariate-probit MLE, bucket by game script
   (favourite/total/bring-back), test Student-t / Gumbel / Clayton tails, validate with a season split and log-loss vs independence. Key hurdle: DK
   SGP hold is ~15-18%, so a rho edge must be large, or you need low-hold boosted SGPs.

We built (2) for MLB. We did NOT build (1): we have no DK SGP quotes.

## Result (Over/Over, closing consensus, fit 2024-25, test 2026)
| Pair | rho (95% CI, game-clustered) |
|---|---|
| starter outs x game total | -0.29 (-0.34, -0.23) |
| starter Ks x game total | -0.16 (-0.20, -0.12) |
| batter hits x game total | +0.24 (+0.20, +0.28) |
| batter total bases x game total | +0.20 (+0.16, +0.23) |
| starter Ks x outs (same pitcher) | +0.32 (+0.27, +0.37) |

Real and correctly signed, but small in payout terms: empirical joint vs independent product is ~1.10x (hits x total), ~1.08x (K x outs, TB x total),
~0.90x (K x total), ~0.61x (outs x total). Below a ~15% hold. No regime structure clear enough to claim (intervals overlap).
The copula overshoots the realized joint on the 2026 holdout for hits x total and K x outs, so a flat 2024-25 rho is not stable.

## Do not trust / known gaps
- hits x total bases (same batter): events are nested, rho pins at the 0.9 grid bound, the "lift" is meaningless. Needs an exact conditional model.
- k_ou x bhits_ou mixes teammates and opponents (no batter team in the feature tables) -> uninformative.
- game_total settled closing legs: 3,454 games; outs_ou mostly 2026, so outs pairs are train-light.
- Over/Over only, main line only, half-point lines only. No Under cells, no alt-line tail dependence, no Student-t/Gumbel fit.

## Next steps if we come back
1. **Get DK SGP quotes** (the only way to turn a real rho into an edge claim). Check whether ParlayAPI or BettingPros expose SGP prices; if not,
   hand-sample a few dozen 2-leg quotes from the DK site. Compare DK-implied rho (method 1) to the empirical rho here, net of hold.
   Careful: the 4-cell trick needs all four combinations to be quoted; DK often blocks some.
2. Add batter team to the pair frame (join lineup/team) so teammate vs opponent pairs (QB-style stacks: batter x batter same team, pitcher x opposing
   batter) can be separated.
3. Re-fit by season with an expanding window to see whether rho drifts (2026 holdout overshoot hints it does).
4. Only then consider a pricing module (N-leg copula from a correlation matrix). Do not wire alerts to it before step 1.

## How to re-run (the slow part is I/O, not compute)
`mlb.analysis.odds_history.read_history` reads one GCS partition at a time and took >15 min per market without finishing. `gsutil -m rsync` also
stalled. What worked: a threaded `google.cloud.storage` download of `Odds/history/market={k_ou,outs_ou,bhits_ou,btb_ou,game_total}/` (32 threads,
~14.7k files, 76 s) into a local dir, then:

    PYTHONPATH=. MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data .venv/bin/python scripts/sgp_empirical_rho.py \
      --local-history <dir containing market=*/> --boot 100 --out docs/analysis/sgp_empirical_rho_<date>.md

`--selftest` checks the vectorized bivariate CDF against scipy and recovers a simulated rho. The system python has no scipy; use `.venv`.
