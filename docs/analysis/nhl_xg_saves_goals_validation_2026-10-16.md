# NHL xG + goalie saves + skater goals / assists / points -- build and validation (2026-10-16)

Branch `feat/nhl-xg-goalie-props`. Research code only: nothing here is deployed, scheduled, written to GCS or the `bets` table, or
posted to Discord, and no ParlayAPI credit was spent. Numbers below come from local copies of the production NHL masters, shots and
BettingPros history (read-only pulls), reproduced by the commands in section 7.

## Verdict up front

**No promotable edge. There IS detectable information beyond the market on goals, assists and saves; it does not pay at the best
available prices, and the repo rule (CLV, not ROI, is the go/no-go) cannot be applied because this history has no closing-line series.**

| Market | Info beyond the market (blend c, t) | In every season? | Bet the model-vs-market gap >= 5pp, best book price | Does xG help (xg vs no-xG model)? | Verdict |
|---|---|---|---|---|---|
| goalie saves | +0.69 (6.9), n=7,348 | c > 0 in 4 of 4 seasons; log-loss WORSE than the market in 2025 | +6.7% pooled but -1.0% in 2025 (2023-24 and 2024-25 flattered by a market-wide Under bias) | No (no-xG model is as good: c +0.75) | UNDETERMINED: regime-dependent, needs CLV |
| anytime goal | +0.38 (5.9), n=52,578 | 3 of 3 | -0.0% (n=3,975) | Slightly (c +0.38 vs +0.34, ROI +1.1pp) | information yes, not monetizable: NO_EDGE on price |
| assists | +0.34 (7.0), n=68,798 | 5 of 5 | -0.5% (n=16,344) | Slightly (c +0.34 vs +0.26, ROI +1.0pp) | information yes, not monetizable: NO_EDGE on price |
| points | +0.19 (4.0), model log-loss worse than market | 2025: +0.11 (1.3) | -1.6% (n=17,053) | Yes, modestly (c +0.19 vs +0.10, ROI +1.8pp) | NO_EDGE |

How to read it. A positive blend coefficient c means a logistic blend leans on the model beyond the market; it is a calibration /
information statement, not profit. Betting the disagreements at the BEST book price (an optimistic, line-shopped fill) is break-even
for the three skater markets and regime-dependent for saves. The "vs-ref" column in section 3 compares each bet with betting BOTH
sides of the same props at the same prices (what the market's hold costs with no skill); the model's side selection beats that
reference in all 17 stat-season rows (10 of the 17 with t >= 2), so the model is not noise, but a +4% gap to a -5% hold is still a ~0% bet. Where the goals
signal comes from: the consensus overprices low-probability scorers (props the market prices ~12% convert ~10%), the model is
calibrated there (10.5% vs 10.6%), and the matching Under is priced around -1000, where the hold swallows the gap
(blind Under at best price: -3.2%, t = -16).

What this does NOT show: that no edge exists. These are close-ish BettingPros snapshots (one per day), the model sees no lineups,
line combinations, power-play units or injuries that the market prices at the close, and a +3% edge needs thousands of independent
bets to resolve. It also does not validate the three archives' claims: the qrob-style structural saves projection is clearly worse than
the market here (log-loss 0.7166 vs 0.6920, c = +0.08, t = 1.7); its 65% target rests on a two-day real-line sample.


## 0. Request, sources, and what we may use

Request: use six downloaded archives plus MoneyPuck / Natural Stat Trick / Evolving Hockey to build hockey prop models and the data
infrastructure behind them. Two findings reshaped that before any code:

1. **None of the three sites can feed a commercial product as things stand** (details and quotes:
   `docs/solutions/conventions/nhl-data-source-licensing.md`). MoneyPuck's page: data is "free to use for non-commercial purposes and
   by journalists for ad-hoc use", other purposes need to inquire. Evolving Hockey's terms bar bots/spiders, limit data to personal
   use, and bar commercial use. Natural Stat Trick answers automated fetches with HTTP 403 and its terms were not retrievable. beezy.fyi
   has paid tiers built, so we did not ingest any of them.
2. **We do not need them for the core of it.** `nhl/` already holds seven seasons of official-API box scores, play-by-play and shot
   events (9,977 games, 1,151,495 shot attempts with x/y, shot type, strength, empty net). MoneyPuck's headline product is an xG
   model on exactly that kind of data, so `nhl/xg/` fits our own.

| Archive | What it is | Used? |
|---|---|---|
| `nhl-xG-model` (MIT) | XGBoost xG on MoneyPuck shot CSVs; shooter / goalie cumulative % inside the model; random 80/20 split; headline = aggregate goal error | Idea only. We keep xG context-only and model shooter / goalie talent separately, split by time, judge by log-loss + calibration. Its data is MoneyPuck's, so none was used. |
| `qrob-nhl-goalie-model` | Saves = expected shots against x time-in-net share x shrunk save%; SOG and game-winner sub-models; DK / PrizePicks line logging; 2-day real-line record | Structural idea reimplemented as a non-ML benchmark (`xgfeat.analytic_saves_mu`) and as feature `analytic_exp_sa`; pull-risk features. Its PrizePicks / Odds-API scrapers were not run. |
| `the-quant-rink` | Static site: per-game / per-player JSON (xG, RAPM, GAR, WAR, model P(home), ESPN BET lines) | Not ingested (the author's derived product, provenance and licence unclear). Confirms what a richer pipeline needs: shift data for RAPM / GAR. |
| `nhl-performance-model` | Notebook: season-level fantasy points regression on 4 seasons of CSVs | Not applicable to game-level props. |
| `nhl-fantasy-predictor` | Flask + React app over the NHL API, SQLite | Not applicable (we already ingest the same API). |
| `hockey_dfs_optimizer` | R: Evolving Hockey scraper -> MySQL -> DK lineup LP | Not used. The scraper is what Evolving Hockey's terms bar. |

All six were scanned first: no embedded secrets, no pickle / `eval` / `exec` / shell calls, hosts limited to the NHL API, PrizePicks,
The Odds API, rotogrinders and GitHub. They were unpacked into a scratch directory and never executed.

## 1. Data infrastructure

Already in production (nothing here changed it): `NHL/{games,team_boxscores,skater_boxscores,goalie_boxscores}_master.csv`,
`NHL/shots/shots_{2019..2026}.csv`, raw box + play-by-play cache (9,977 games), `Odds/bettingpros_nhl/{saves,goals,points,assists,
shots,blocked-shots}/` (2022-10 -> 2026-10, ~1,100 days, two-sided prices from ~14 books), and the banked ParlayAPI snapshots.

Added by this branch (all under `nhl/`, additive, local output only):

| Piece | What it does |
|---|---|
| `nhl/xg/features.py` | shot rows -> geometry, shot type, strength, previous-attempt context (rebound flag, time / distance since the last attempt); empty-net, shootout and preseason excluded |
| `nhl/xg/model.py` | XGBoost `binary:logistic` for two populations (unblocked attempts; on target); time-ordered fitting; `walk_forward` = every shot scored by a model trained only on EARLIER seasons |
| `nhl/xg/aggregate.py`, `build.py` | per skater-game (ixG, attempts, PP / EV split), goalie-game (xGA, goals saved above expected), team-game (xGF / xGA, both sides) tables |
| `nhl/xg/evaluate.py` | temporal-holdout report |
| `nhl/props/xgfeat.py` | leakage-safe rolling features (every window `shift(1)` first) for goalie saves and skater goals / assists / points; no market-derived input |
| `nhl/props/countmodel.py` | generic count:poisson + NegBin walk-forward model (the SOG `train.py` is untouched) |
| `nhl/props/bp_eval.py`, `eval_xg.py` | fail-closed name resolution, BettingPros consensus join with coverage bookkeeping, model-vs-market scoring per season |
| tests | 15 new (`tests/test_nhl_xg*.py`): populations, previous-attempt context, walk-forward leak guard (corrupting a season's own labels leaves its xG unchanged), aggregate identities, feature leakage, coverage balance, ROI maths |

Bugs found and fixed on the way (details: `docs/solutions/integration-issues/bettingpros-nhl-pregame-flag-and-team-codes.md`):
the strict `pregame` flag discarded 46% of saves consensus rows (the TRUE closing quotes; a 10-minute grace window takes coverage
from 52% to 98%), and BettingPros' `LA/NJ/SJ/TB` did not match the API's `LAK/NJD/SJS/TBL` (the earlier SOG hook silently lost
those clubs' games; `history.TEAM_FIX` fixed).

Not built, deliberately: a nightly xG job / GCS tables (the evidence below does not yet justify a deploy), a live scorer, any bets-table
write. Gaps a richer pipeline would need: shift charts (on-ice data -> RAPM / on-ice CF%, xGF%), faceoff / hit / turnover events from
the raw play-by-play (`NHL/raw/pbp/` is already cached) for rush and off-turnover flags, a starting-goalie confirmation feed, and
banked opening AND closing prop lines (BettingPros' `open` rows are copies of the book's current quote -- checked).

## 2. The xG model

Fit on seasons 2019-2023, tested on 2024-2026 (the early-stopping slice is the last training season; no random splitting).

### xG population `attempt`  train 2019-2023 (n=545,753)  test [2024, 2025, 2026] (n=245,976, goal rate 0.0672)

| model | log-loss | Brier | AUC | sum p | goals |
|---|---|---|---|---|---|
| XGBoost xG | 0.21848 | 0.05871 | 0.7546 | 17008 | 16518 |
| dist+angle logistic | 0.22700 | 0.06048 | 0.7197 | 16563 | 16518 |
| constant | 0.24621 | 0.06264 | 0.5000 | 16512 | 16518 |

Team-game goals (n=5,798): MAE xG 1.324 vs constant 1.336; corr xG 0.212 vs constant 0.072

| p bin | n | mean p | goal rate | gap |
|---|---|---|---|---|
| (-0.001, 0.02] | 68,383 | 0.0109 | 0.0108 | -0.0001 |
| (0.02, 0.04] | 39,689 | 0.0290 | 0.0316 | +0.0026 |
| (0.04, 0.06] | 31,783 | 0.0499 | 0.0496 | -0.0003 |
| (0.06, 0.08] | 25,753 | 0.0694 | 0.0734 | +0.0040 |
| (0.08, 0.12] | 35,463 | 0.0984 | 0.0996 | +0.0012 |
| (0.12, 0.2] | 32,770 | 0.1537 | 0.1365 | -0.0172 |
| (0.2, 0.35] | 10,854 | 0.2449 | 0.2298 | -0.0151 |
| (0.35, 0.6] | 1,252 | 0.4263 | 0.4321 | +0.0058 |
| (0.6, 1.0] | 29 | 0.6239 | 0.6897 | +0.0657 |

By test season (sum p vs goals): 2024: 7980/7867, 2025: 8380/8025, 2026: 647/626
Top gain: rebound 198.96, dist_ft 172.41, lateral_ft 134.42, st_snap 78.45, pp 52.86, st_tip-in 51.54, skaters_against 44.38, st_wrap-around 41.02

### xG population `sog`  train 2019-2023 (n=392,259)  test [2024, 2025, 2026] (n=161,301, goal rate 0.1024)

| model | log-loss | Brier | AUC | sum p | goals |
|---|---|---|---|---|---|
| XGBoost xG | 0.29152 | 0.08464 | 0.7500 | 15559 | 16518 |
| dist+angle logistic | 0.30214 | 0.08743 | 0.7220 | 15090 | 16518 |
| constant | 0.33080 | 0.09200 | 0.5000 | 15065 | 16518 |

Team-game goals (n=5,798): MAE xG 1.267 vs constant 1.292; corr xG 0.290 vs constant 0.176

| p bin | n | mean p | goal rate | gap |
|---|---|---|---|---|
| (-0.001, 0.02] | 29,948 | 0.0113 | 0.0116 | +0.0003 |
| (0.02, 0.04] | 25,922 | 0.0290 | 0.0333 | +0.0044 |
| (0.04, 0.06] | 16,102 | 0.0496 | 0.0583 | +0.0087 |
| (0.06, 0.08] | 13,446 | 0.0697 | 0.0770 | +0.0072 |
| (0.08, 0.12] | 20,674 | 0.0990 | 0.1161 | +0.0171 |
| (0.12, 0.2] | 35,667 | 0.1575 | 0.1632 | +0.0057 |
| (0.2, 0.35] | 17,735 | 0.2423 | 0.2409 | -0.0013 |
| (0.35, 0.6] | 1,722 | 0.4170 | 0.4518 | +0.0348 |
| (0.6, 1.0] | 85 | 0.6311 | 0.7176 | +0.0865 |

By test season (sum p vs goals): 2024: 7382/7867, 2025: 7588/8025, 2026: 589/626
Top gain: dist_ft 206.21, lateral_ft 156.0, st_snap 58.07, skaters_against 52.52, rebound 50.98, st_wrap-around 48.81, st_backhand 44.94, sk_diff 44.3

Reading it: on the held-out seasons the model beats a distance+angle logistic on log-loss (attempt 0.2185 vs 0.2270, on target 0.2915 vs 0.3021) and AUC (0.755 vs 0.720), and is well calibrated by bin. Two honest limits: (1) a level shift from era drift -- the attempt model over-predicts total goals by 3.0% and the on-target model under-predicts by 5.8% on 2024-26 (on-target and finishing rates both moved after the training window), so every downstream feature is used RELATIVE to a rolling league baseline, never as an absolute; (2) no pre-shot movement or turnover context (the shots table has no faceoff / hit / giveaway events), which is what separates MoneyPuck's / Evolving Hockey's models from this one. The nhl-xG-model archive's headline (3.0% aggregate goal error on a random 20% split) is not comparable: aggregate error ignores ranking and calibration.

Persistence of what xG measures (season t vs t+1, regular season 2021+; pairs of players above a volume floor):

| Quantity | Year-over-year corr |
|---|---|
| skater ixG per hour | 0.95 |
| skater ixG per attempt (shot quality) | 0.93 |
| skater goals per attempt | 0.61 |
| skater finishing, (goals - ixG) per attempt | 0.22 |
| goalie raw save % (>= 600 shots) | 0.22 |
| goalie goals saved above expected per shot (>= 600 shots) | 0.15 |

Shot volume and shot quality are close to a skater's fixed trait; finishing above expectation is weak; and on THIS xG, a goalie's
expected-goals adjustment is LESS persistent than his plain save %. That is the setup for the ablations below.

## 3. Saves, goals, assists, points vs the historical market

### Method
- **Lines:** `Odds/bettingpros_nhl/{saves,goals,points,assists}` 2022-10 -> 2026-10 (184k / 1.47M / 1.33M / 1.37M quote rows). Consensus =
  `mlb_core.odds.bettingpros_loader.consensus_from_long` (reference books only: no DFS apps, no Kalshi, no BettingPros "consensus" / "open"
  rows; two-sided only; de-vigged median across >= 3 books at the modal line). Quotes last updated up to 10 minutes after the scheduled start
  are kept (`bp_eval.apply_slack`; see the solutions note); integer lines are dropped (pushes).
- **Models:** XGBoost `count:poisson` + a NegBin dispersion fit on a time-ordered validation slice, one model per stat, retrained every 2
  months (saves) / 4 months (skaters) on `date < period start`, so every scored game comes from a model that never saw its date. Saves:
  starters only (the market only exists for the expected starter); 34 prior-only features (goalie workload / form / save %, GSAx, shot
  quality faced; own-team defence and opposing-offence shot rates and xG, PP exposure, rest, league baseline). Skaters: 36 features (TOI,
  per-60 rates of ixG / attempts / SOG / goals / assists / points over 20-120 games, finishing ratio, PP involvement, team and opponent xG
  form, the OPPOSING STARTER's save % / GSAx). Hyper-parameters are the SOG model's, untuned. **No market-derived feature.**
- **Variants on the same props:** `xg` (all features), `noxg` (same model without any xG-derived input: ablation), `baseline` (non-ML:
  saves = qrob-style structural projection; skaters = L120 per-60 rate x L10 TOI).
- **Scoring:** model P(over) at the market's own line vs the consensus fair P(over): log-loss, blend regression
  `y ~ a + b*logit(mkt) + c*(logit(model) - logit(mkt))` with game-clustered SEs, and flat-stake ROI on the side the model disagrees with
  (>= 5pp; 3pp / 8pp and Novig-price versions are in the JSON). Every table is PER SEASON (a pooled number hid a regime change, below).
  Voids (player did not play) are dropped, not graded; coverage is balanced to the row (matched + dropped = consensus rows).
- Pre-registered headline per stat: the blend coefficient c on the full period. Everything else is descriptive; across 4 stats x 3
  variants x up to 5 seasons x 3 gaps this is dozens of looks, so single-cell t-statistics below ~3 should be read as noise.

### Results

#### saves

Market coverage: 7,352 of 7,520 consensus rows scored (98%); dropped: postgame_quote=14, unresolved=29, ambiguous=2, void=123. Props scored by ALL variants: 7,348.

| variant | rows | NegBin log-lik | MAE | mean mu | mean actual |
|---|---|---|---|---|---|
| xg | 11,398 | -3.4336 | 5.538 | 25.720 | 25.586 |
| noxg | 11,398 | -3.4335 | 5.537 | 25.741 | 25.586 |
| baseline | 11,394 | -3.4738 | 5.757 | 25.139 | 25.586 |

`xg` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 7,348 | 0.475 | 0.501 | 0.458 | 0.6886 / 0.6920 | 0.69 (6.9) | +6.7% (3,671) | +11.6% (7.5) | +0.3% (0.3) |
| 2023 | 1,981 | 0.452 | 0.501 | 0.446 | 0.6891 / 0.6921 | 0.32 (1.3) | +6.0% (1,158) | +11.7% (4.3) | +3.6% (1.7) |
| 2024 | 2,409 | 0.453 | 0.500 | 0.439 | 0.6803 / 0.6909 | 0.88 (5.4) | +11.3% (1,478) | +16.5% (6.9) | +4.0% (2.1) |
| 2025 | 2,727 | 0.515 | 0.502 | 0.484 | 0.6957 / 0.6928 | 0.37 (1.9) | -1.0% (893) | +2.7% (0.9) | -6.4% (-3.6) |
| 2026 | 191 | 0.398 | 0.499 | 0.445 | 0.6792 / 0.6913 | 0.35 (0.7) | +15.9% (117) | +18.9% (2.2) | +16.9% (2.7) |

`noxg` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 7,348 | 0.475 | 0.501 | 0.459 | 0.6879 / 0.6920 | 0.75 (7.3) | +7.2% (3,529) | +12.1% (7.7) | +0.3% (0.3) |
| 2023 | 1,981 | 0.452 | 0.501 | 0.447 | 0.6886 / 0.6921 | 0.37 (1.5) | +7.2% (1,098) | +12.9% (4.6) | +3.6% (1.7) |
| 2024 | 2,409 | 0.453 | 0.500 | 0.445 | 0.6786 / 0.6909 | 1.02 (6.1) | +12.2% (1,403) | +17.5% (7.2) | +4.0% (2.1) |
| 2025 | 2,727 | 0.515 | 0.502 | 0.482 | 0.6961 / 0.6928 | 0.35 (1.8) | -1.6% (884) | +2.0% (0.6) | -6.4% (-3.6) |
| 2026 | 191 | 0.398 | 0.499 | 0.440 | 0.6748 / 0.6913 | 0.50 (1.0) | +16.2% (118) | +19.3% (2.3) | +16.9% (2.7) |

`baseline` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 7,348 | 0.475 | 0.501 | 0.433 | 0.7166 / 0.6920 | 0.08 (1.7) | +0.4% (4,760) | +5.3% (3.9) | +0.3% (0.3) |
| 2023 | 1,981 | 0.452 | 0.501 | 0.410 | 0.7201 / 0.6921 | -0.06 (-0.6) | +1.0% (1,350) | +6.6% (2.6) | +3.6% (1.7) |
| 2024 | 2,409 | 0.453 | 0.500 | 0.428 | 0.6987 / 0.6909 | 0.27 (3.3) | +7.1% (1,610) | +12.3% (5.3) | +4.0% (2.1) |
| 2025 | 2,727 | 0.515 | 0.502 | 0.456 | 0.7294 / 0.6928 | -0.06 (-0.8) | -7.7% (1,627) | -3.6% (-1.5) | -6.4% (-3.6) |
| 2026 | 191 | 0.398 | 0.499 | 0.418 | 0.6863 / 0.6913 | 0.28 (1.2) | +15.3% (136) | +18.3% (2.3) | +16.9% (2.7) |

#### goals

Market coverage: 52,783 of 54,945 consensus rows scored (96%); dropped: postgame_quote=76, unresolved=49, ambiguous=21, void=2016. Props scored by ALL variants: 52,578.

| variant | rows | NegBin log-lik | MAE | mean mu | mean actual |
|---|---|---|---|---|---|
| xg | 205,118 | -0.4484 | 0.269 | 0.174 | 0.171 |
| noxg | 205,118 | -0.4485 | 0.268 | 0.173 | 0.171 |
| baseline | 203,754 | -0.4686 | 0.269 | 0.175 | 0.171 |

`xg` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 52,578 | 0.163 | 0.180 | 0.162 | 0.4098 / 0.4111 | 0.38 (5.9) | -0.0% (3,975) | +4.5% (2.3) | -3.2% (-16.4) |
| 2024 | 2,867 | 0.275 | 0.281 | 0.258 | 0.5766 / 0.5767 | 0.66 (2.4) | -1.3% (521) | +4.2% (0.9) | -5.7% (-5.3) |
| 2025 | 46,369 | 0.157 | 0.174 | 0.157 | 0.4007 / 0.4020 | 0.37 (5.2) | +0.0% (3,057) | +3.9% (1.7) | -3.2% (-15.5) |
| 2026 | 3,342 | 0.153 | 0.177 | 0.159 | 0.3932 / 0.3958 | 0.36 (1.6) | +1.4% (397) | +9.1% (1.5) | -1.3% (-2.1) |

`noxg` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 52,578 | 0.163 | 0.180 | 0.161 | 0.4100 / 0.4111 | 0.34 (5.0) | -1.1% (3,794) | +4.5% (2.4) | -3.2% (-16.4) |
| 2024 | 2,867 | 0.275 | 0.281 | 0.254 | 0.5772 / 0.5767 | 0.68 (2.4) | -2.8% (552) | +0.9% (0.2) | -5.7% (-5.3) |
| 2025 | 46,369 | 0.157 | 0.174 | 0.155 | 0.4008 / 0.4020 | 0.32 (4.4) | -0.6% (2,811) | +4.9% (2.3) | -3.2% (-15.5) |
| 2026 | 3,342 | 0.153 | 0.177 | 0.156 | 0.3939 / 0.3958 | 0.20 (0.9) | -1.9% (431) | +6.7% (1.1) | -1.3% (-2.1) |

`baseline` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 52,578 | 0.163 | 0.180 | 0.171 | 0.4195 / 0.4111 | 0.11 (3.7) | -3.9% (10,161) | +4.2% (3.2) | -3.2% (-16.4) |
| 2024 | 2,867 | 0.275 | 0.281 | 0.272 | 0.5813 / 0.5767 | -0.06 (-0.3) | -11.1% (547) | -4.1% (-1.0) | -5.7% (-5.3) |
| 2025 | 46,369 | 0.157 | 0.174 | 0.165 | 0.4104 / 0.4020 | 0.11 (3.5) | -3.1% (8,760) | +4.5% (3.1) | -3.2% (-15.5) |
| 2026 | 3,342 | 0.153 | 0.177 | 0.172 | 0.4072 / 0.3958 | 0.12 (1.4) | -7.5% (854) | +6.0% (1.4) | -1.3% (-2.1) |

#### points

Market coverage: 66,054 of 67,469 consensus rows scored (98%); dropped: postgame_quote=94, unresolved=187, ambiguous=225, void=909. Props scored by ALL variants: 65,974.

| variant | rows | NegBin log-lik | MAE | mean mu | mean actual |
|---|---|---|---|---|---|
| xg | 205,118 | -0.8332 | 0.525 | 0.467 | 0.459 |
| noxg | 205,118 | -0.8336 | 0.525 | 0.466 | 0.459 |
| baseline | 203,754 | -0.8514 | 0.525 | 0.466 | 0.460 |

`xg` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 65,974 | 0.469 | 0.476 | 0.468 | 0.6735 / 0.6709 | 0.19 (4.0) | -1.6% (17,053) | +3.2% (3.8) | -3.8% (-7.6) |
| 2022 | 2,820 | 0.516 | 0.511 | 0.515 | 0.6795 / 0.6755 | 0.07 (0.4) | +6.7% (848) | +8.4% (2.1) | -6.6% (-2.8) |
| 2023 | 18,569 | 0.474 | 0.491 | 0.484 | 0.6758 / 0.6741 | 0.24 (2.6) | -2.9% (4,629) | +3.4% (2.2) | -2.6% (-2.6) |
| 2024 | 21,243 | 0.471 | 0.475 | 0.462 | 0.6743 / 0.6723 | 0.27 (3.1) | -0.2% (5,309) | +4.2% (2.8) | -4.5% (-5.1) |
| 2025 | 21,386 | 0.460 | 0.463 | 0.456 | 0.6716 / 0.6680 | 0.11 (1.3) | -3.4% (5,610) | +1.2% (0.8) | -4.1% (-4.7) |
| 2026 | 1,956 | 0.427 | 0.443 | 0.434 | 0.6554 / 0.6509 | 0.04 (0.1) | +1.0% (657) | +3.2% (0.7) | -1.2% (-0.4) |

`noxg` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 65,974 | 0.469 | 0.476 | 0.466 | 0.6742 / 0.6709 | 0.10 (2.1) | -3.4% (16,789) | +1.4% (1.6) | -3.8% (-7.6) |
| 2022 | 2,820 | 0.516 | 0.511 | 0.514 | 0.6805 / 0.6755 | -0.04 (-0.2) | +2.5% (819) | +3.6% (0.8) | -6.6% (-2.8) |
| 2023 | 18,569 | 0.474 | 0.491 | 0.483 | 0.6762 / 0.6741 | 0.20 (2.1) | -3.2% (4,809) | +3.1% (2.0) | -2.6% (-2.6) |
| 2024 | 21,243 | 0.471 | 0.475 | 0.462 | 0.6751 / 0.6723 | 0.16 (1.9) | -2.5% (5,204) | +1.8% (1.2) | -4.5% (-5.1) |
| 2025 | 21,386 | 0.460 | 0.463 | 0.453 | 0.6723 / 0.6680 | -0.01 (-0.1) | -5.4% (5,317) | -0.7% (-0.5) | -4.1% (-4.7) |
| 2026 | 1,956 | 0.427 | 0.443 | 0.429 | 0.6570 / 0.6509 | -0.13 (-0.6) | -2.2% (640) | +0.0% (0.0) | -1.2% (-0.4) |

`baseline` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 65,974 | 0.469 | 0.476 | 0.470 | 0.6767 / 0.6709 | -0.08 (-1.7) | -6.2% (19,638) | -1.2% (-1.6) | -3.8% (-7.6) |
| 2022 | 2,820 | 0.516 | 0.511 | 0.491 | 0.6830 / 0.6755 | -0.12 (-0.6) | -6.0% (1,013) | -3.3% (-1.0) | -6.6% (-2.8) |
| 2023 | 18,569 | 0.474 | 0.491 | 0.483 | 0.6785 / 0.6741 | 0.01 (0.2) | -4.5% (5,732) | +1.5% (1.1) | -2.6% (-2.6) |
| 2024 | 21,243 | 0.471 | 0.475 | 0.473 | 0.6795 / 0.6723 | -0.27 (-3.4) | -11.0% (5,871) | -5.7% (-4.2) | -4.5% (-5.1) |
| 2025 | 21,386 | 0.460 | 0.463 | 0.455 | 0.6729 / 0.6680 | 0.03 (0.4) | -3.4% (6,305) | +0.9% (0.6) | -4.1% (-4.7) |
| 2026 | 1,956 | 0.427 | 0.443 | 0.441 | 0.6619 / 0.6509 | -0.24 (-1.0) | -6.2% (717) | -2.4% (-0.6) | -1.2% (-0.4) |

#### assists

Market coverage: 68,898 of 70,385 consensus rows scored (98%); dropped: postgame_quote=103, unresolved=189, ambiguous=244, void=951. Props scored by ALL variants: 68,798.

| variant | rows | NegBin log-lik | MAE | mean mu | mean actual |
|---|---|---|---|---|---|
| xg | 205,118 | -0.6395 | 0.402 | 0.293 | 0.288 |
| noxg | 205,118 | -0.6398 | 0.402 | 0.293 | 0.288 |
| baseline | 203,754 | -0.6587 | 0.401 | 0.292 | 0.289 |

`xg` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 68,798 | 0.342 | 0.359 | 0.341 | 0.6245 / 0.6238 | 0.34 (7.0) | -0.5% (16,344) | +5.9% (7.2) | -3.0% (-8.9) |
| 2022 | 2,716 | 0.374 | 0.387 | 0.379 | 0.6467 / 0.6438 | 0.20 (1.0) | -1.8% (753) | +6.2% (1.7) | -3.7% (-2.3) |
| 2023 | 19,078 | 0.351 | 0.372 | 0.357 | 0.6300 / 0.6303 | 0.46 (4.9) | -0.6% (4,525) | +6.4% (4.2) | -2.4% (-3.7) |
| 2024 | 21,330 | 0.339 | 0.355 | 0.334 | 0.6240 / 0.6238 | 0.41 (4.3) | +0.6% (4,844) | +7.1% (4.7) | -3.3% (-5.6) |
| 2025 | 23,765 | 0.336 | 0.350 | 0.332 | 0.6214 / 0.6196 | 0.20 (2.5) | -1.8% (5,682) | +3.8% (2.7) | -3.1% (-5.5) |
| 2026 | 1,909 | 0.309 | 0.332 | 0.314 | 0.5821 / 0.5844 | 0.51 (2.0) | +7.1% (540) | +12.6% (2.5) | -1.7% (-0.9) |

`noxg` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 68,798 | 0.342 | 0.359 | 0.340 | 0.6250 / 0.6238 | 0.26 (5.4) | -1.5% (16,055) | +4.7% (5.6) | -3.0% (-8.9) |
| 2022 | 2,716 | 0.374 | 0.387 | 0.380 | 0.6469 / 0.6438 | 0.17 (0.8) | -4.4% (730) | +2.6% (0.7) | -3.7% (-2.3) |
| 2023 | 19,078 | 0.351 | 0.372 | 0.356 | 0.6303 / 0.6303 | 0.41 (4.5) | -1.1% (4,616) | +5.9% (3.9) | -2.4% (-3.7) |
| 2024 | 21,330 | 0.339 | 0.355 | 0.334 | 0.6247 / 0.6238 | 0.29 (3.0) | -0.0% (4,655) | +6.2% (4.1) | -3.3% (-5.6) |
| 2025 | 23,765 | 0.336 | 0.350 | 0.331 | 0.6218 / 0.6196 | 0.13 (1.6) | -2.9% (5,495) | +2.6% (1.8) | -3.1% (-5.5) |
| 2026 | 1,909 | 0.309 | 0.332 | 0.311 | 0.5841 / 0.5844 | 0.30 (1.2) | -0.5% (559) | +5.2% (1.0) | -1.7% (-0.9) |

`baseline` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH sides of the same props at the same prices):

| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 68,798 | 0.342 | 0.359 | 0.341 | 0.6273 / 0.6238 | 0.14 (3.9) | -2.5% (22,616) | +3.6% (5.2) | -3.0% (-8.9) |
| 2022 | 2,716 | 0.374 | 0.387 | 0.358 | 0.6504 / 0.6438 | -0.01 (-0.1) | -5.3% (1,154) | +2.0% (0.7) | -3.7% (-2.3) |
| 2023 | 19,078 | 0.351 | 0.372 | 0.354 | 0.6315 / 0.6303 | 0.31 (4.3) | +0.2% (6,597) | +7.5% (6.0) | -2.4% (-3.7) |
| 2024 | 21,330 | 0.339 | 0.355 | 0.341 | 0.6285 / 0.6238 | 0.01 (0.2) | -4.7% (6,410) | +0.7% (0.5) | -3.3% (-5.6) |
| 2025 | 23,765 | 0.336 | 0.350 | 0.330 | 0.6232 / 0.6196 | 0.16 (2.4) | -2.6% (7,780) | +2.8% (2.3) | -3.1% (-5.5) |
| 2026 | 1,909 | 0.309 | 0.332 | 0.319 | 0.5897 / 0.5844 | 0.06 (0.3) | -1.3% (675) | +5.6% (1.4) | -1.7% (-0.9) |


### What the saves tables say (read this before trusting the +6.7%)
- Saves per goalie appearance fell every season (25.7 in 2022-23, 25.0, 23.4, 22.9, and 20.3 in the first weeks of 2026-27) as team shot
  volume fell from ~31 to ~27-28 per game. (Season labels in the tables are the START year: `2023` = 2023-24, `2024` = 2024-25, `2025` = 2025-26.)
  Consensus lines LAGGED that fall in the 2023-24 and 2024-25 seasons (realized over-rate 45.2% / 45.3% against a
  de-vigged 50.1% / 50.0%; blind Under at best price +3.6% (t 1.7) and +4.0% (t 2.1)) and had caught up by 2025-26 (over 51.5%, blind Under
  -6.4%, t = -3.6). The model, which tracks recent volume through its team and league features, beat those lagged lines; in 2025 it
  did not (log-loss 0.6957 vs 0.6928, ROI5 -1.0%). A league-only model (rolling league saves mean, nothing else) does NOT reproduce
  the signal (blend c = -0.00, t = -0.04 pooled), so the gain is more than "knows the trend", but the pooled +6.7% is mostly those
  two seasons' regime. It is not evidence of a durable edge.
- **xG adds nothing for saves** (no-xG model: log-loss 0.6879 vs 0.6886, c +0.75 vs +0.69). Consistent with the persistence table in
  section 2: goalie goals-saved-above-expected is the least persistent quantity we measured (0.15, below raw save % at 0.22), so an
  xG-based goalie adjustment is mostly noise on this xG.
- For the skater stats xG helps by a small, same-sign margin in all three (blend c, ROI5): consistent with shot quality being highly
  persistent, but the three stats share the same players and games, so this is one observation, not three.

## 4. Caveats and limits (what the numbers cannot say)
1. **No CLV.** One snapshot per (market, day); BettingPros' `open` rows are copies of each book's current quote (checked). The only way
   to a PROMOTE_CANDIDATE verdict is forward-banked opens AND closes (the paused `odds-bank-*` jobs) scored with
   `mlb_core.risk.clv.clv_verdict`.
2. **Best-price fills are optimistic** (line-shopped across 4-8 books, stale quotes included); the Novig-price rows in the JSON are
   lower or equal.
3. **Missing information the market has:** confirmed lineups and line combinations, power-play unit assignments, injuries / late
   scratches, goalie fatigue beyond rest days. The skater models use actual TOI history only. The opposing starter IS used (public
   before puck drop; the saves market itself only exists for the expected starter); a live scorer needs a confirmation feed.
4. **Untuned models, one retrain schedule, one xG vintage.** The xG model has an era-drift level shift (+3.0% / -5.8% on 2024-26 goals);
   features are relative to rolling league baselines for that reason.
5. **Selection of props:** consensus rows need >= 3 two-sided real books, which favours popular players; goals / points / assists
   coverage thins before 2024 (2022-23 are small).
6. **Not independent bets:** props within a game share outcomes; every t-statistic is clustered by game (1,772 to 4,539 games per stat,
   not 50-70k props).

## 5. What was NOT done
No job, scheduler, GCS write, `bets`-table write, Discord post, registry entry, or ParlayAPI spend; no live scorer; no
hyper-parameter tuning; no on-ice (shift) data; no raw-play-by-play xG context features; MoneyPuck / NST / Evolving Hockey data were
not read. `nhl/xg/build.py` deliberately has no `--gcs` flag.

## 6. Recommended next steps (decisions for the user)
1. **Resume NHL odds banking for saves / goals / assists** (the `odds-bank-*` schedulers are paused by an earlier decision): it is the only
   route to CLV, and CLV is the repo's gate. Cost = ParlayAPI credits from the shared pool.
2. **Do not build a deploy for xG yet.** Its measured value is: strong, persistent shot-quality signal; no help on saves; a small
   consistent gain on skater markets. A nightly job (writing `NHL/xg/*.parquet`) is a day of work if you want the tables for the
   site's projection board, and `empirical_ewm_v1` could consume `ixg_p60` / `opp_g_*` features; say so and it is a separate PR.
3. **MoneyPuck:** if you want their xG / on-ice data in a paid product, ask them in writing; until then nothing here depends on it.
4. **If saves is the priority**, the highest-value inputs are a starting-goalie confirmation feed and per-season recalibration of the
   level (the regime effect above), not more xG.
5. **Richer xG** (rush / turnover / faceoff-win context, shift-based on-ice players -> RAPM) is possible from `NHL/raw/pbp/` plus the NHL
   shift-chart endpoint; the earlier evidence says expect modest gains.

## 7. Reproduce
```bash
# inputs (read-only; use gcloud storage, NOT gsutil -m)
gcloud storage cp "gs://<bucket>/NHL/*.csv" <dir>/NHL/ ; gcloud storage cp "gs://<bucket>/NHL/shots/*.csv" <dir>/NHL/shots/
for m in saves goals points assists; do gcloud storage cp "gs://<bucket>/Odds/bettingpros_nhl/$m/*.parquet" <dir>/bp/$m/; done
PYTHONPATH=. python -m nhl.xg.evaluate --data-dir <dir> --json xg_eval.json                  # section 2 tables (~25 s)
PYTHONPATH=. python -m nhl.xg.build --data-dir <dir> --out-dir <xg dir>                       # walk-forward xG + game tables (~3 min)
PYTHONPATH=. python -m nhl.props.eval_xg --data-dir <dir> --xg-dir <xg dir> --out-dir <eval dir> \
    --json report.json --markdown report.md                                                   # section 3 (~25 min first run, cached after)
python -m pytest tests/test_nhl_xg.py tests/test_nhl_xg_features.py
```
Use the repo `.venv` (Python 3.14, pandas 3.0, xgboost 3.4). Set `NHL_NTHREAD` to limit XGBoost threads (default 6).
