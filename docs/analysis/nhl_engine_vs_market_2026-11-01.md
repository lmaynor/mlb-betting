# NHL serving engine vs the market, plus the two wiring experiments (2026-11-01)

Harness: `scripts/nhl_engine_vs_market.py` (new). It calls the production functions (`project_nhl`, `GameLogs.context`) as of
each historical BettingPros consensus prop's game date (everything cut to games strictly before it), prices the market's own
line with the engine's distribution, and scores it with the PR #70 machinery (`nhl.props.bp_eval`: blend coefficient c,
log loss vs the market, disagreement ROI at the best price, per season). The old engine is scored on the SAME props. Props:
BettingPros history 2022-10 -> 2026-10, reference books only, de-vigged median, close-ish snapshots, integer lines dropped, saves
on goalie starts only. There is no closing-line series, so none of this can reach "promote".

## 1. New engine vs the market

| Market | n props | log loss new / old / market | new - old (paired, se) | blend c (t), new | ROI5 at best price (n) |
|---|---|---|---|---|---|
| saves | 7,287 | 0.6919 / 0.7059 / 0.6920 | -0.0140 (0.0024) | +0.48 (4.5) | +1.5% (2,575) |
| goals | 52,481 | 0.4186 / 0.4294 / 0.4113 | -0.0108 (0.0005) | +0.03 (1.0) | -4.1% (18,454) |
| assists | 68,763 | 0.6352 / 0.6383 / 0.6239 | -0.0031 (0.0002) | +0.06 (2.7) | -3.8% |
| points | 65,944 | 0.6801 / 0.6822 / 0.6709 | -0.0021 (0.0002) | +0.03 (0.9) | -4.0% |

- The new engine is clearly better than the old one on every market (the Part-1 accuracy result holds against market lines too).
- **It does not beat the market, and on the skater markets it adds no information beyond it** (c <= 0.06; 2025 assists / points c is
  slightly negative). Its log loss is 0.007-0.011 worse than the market's on goals / assists / points.
- **Saves is the exception, and it is regime-dependent**: c = -0.23 (2023), +0.84 (2024, t 4.8), +0.19 (2025, t 1.0); ROI5 -7.0% /
  +8.9% / -1.3%. Same pattern PR #70 found for its trained model. Pooled log loss ties the market (0.6919 vs 0.6920). UNDETERMINED.
- For reference, PR #70's TRAINED models measured c = +0.69 saves, +0.38 goals, +0.34 assists, +0.19 points on a different
  population. The gap is the cost of the board running an empirical estimator instead of a trained model (the trained artifacts are
  local-only, never in the image). If market value is the goal, serving a trained model is the lever, not tuning this one.

## 2. Opposing starter (`nhl.game.goalie_model`) on skater scoring: NOT shipped

`GameLogs.goalie_tracker()` builds the tracker in global date order; `opp_goalie_gsax()` returns the expected opposing starter's
shrunk GSAx/60 (recent-starts share, back-to-back backup inference) centred on the mean over teams; `H.goalie_factor` turns it into
a scoring multiplier (switch `H.GOALIE_BETA`, default 0). On goals, beta = 1.0 made the market-line log loss WORSE: +0.00071 per
prop (se 0.00014, z 5.0), +0.00075 in 2025 (z 5.0). The team-level opponent factor already carries most of the goalie signal and
this term double counts it with a noisier estimate. Left off. Not tested: a confirmed-starter feed (none exists here), saves.

## 3. Per-shooter xG in the goals link: NOT shipped to the live path

The prior of the shooting-% shrinkage is scaled by the shooter's own xG per shot on goal (`H.XG_PRIOR`, default off). Ceiling test with
the walk-forward xG tables (`nhl.xg.build`, the best case: out-of-season models): +0.00018 log loss per prop (se 0.00006, z 2.8),
the right sign in all three seasons (2024 +0.00086, 2025 +0.00011, 2026 +0.00056), and blend c barely moves (0.030 -> 0.038). That
is real but ~2% of the 0.007 gap to the market. A live version would need an xG model artifact or a distance/angle lookup in the image
plus a shots read per request, and could only recover less than this ceiling. Not worth the plumbing; the switch and tests stay.

## What this changes

Nothing on the board: both switches are off, so served numbers are identical to PR #149. What it adds is the market harness for
any future engine change, the evidence above, and two tested, default-off hooks. Next lever, if wanted: make a trained model
servable (artifact storage + loader), then re-run this harness.
