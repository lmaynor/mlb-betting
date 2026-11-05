# NHL skater time-on-ice splits (EV / PP / PK, PP1 vs PP2): builder, validation, and a walk-forward accuracy check (2026-11-05)

Code: `nhl/data/toi_splits.py` (builder + validation), `nhl/props/toi_feat.py` (prior-only rolling features),
`nhl/props/eval_toi_splits.py` (paired walk-forward scorer). Raw numbers: `nhl_toi_splits_2026-11-05_results.json`.
Gotchas: `docs/solutions/integration-issues/nhl-shiftcharts-and-official-toi-split.md`.

Status: code + tests only. LOCAL output, nothing written to GCS (`--write-gcs` exists and was not run), no job, no scheduler, serving
engine untouched. Every output is UNVALIDATED for betting purposes; the check below is ACCURACY only (no price, no edge).

## 1. What the repo already had

The boxscore has ONE total `toi` per skater and `powerPlayGoals`; `nhl/data/powerplay.py` derives power-play POINTS from play-by-play
goals. Power-play TOI, PP1/PP2 deployment and even-strength vs power-play splits were listed as "still not available" in
`docs/solutions/integration-issues/nhl-boxscore-has-no-power-play-points.md`. No new masters existed under `NHL/` for them. Nothing here
duplicates that work: it adds TIME by game state and the unit guess.

## 2. The builder

Per game: one `api.nhle.com/stats/rest/en/shiftcharts` request (typeCode 517 rows are shifts) plus the play-by-play, which is already
mirrored at `NHL/raw/pbp/<id>.json.gz` (`--pbp-from-gcs`; rosterSpots tell goalies from skaters, situationCode gives the strength at
each event). Per second of game clock: skaters on the ice per team from the shifts, a rolling median (5 s) over line-change flicker,
effective skaters = skaters minus one when no goalie is on the ice (the same pulled-goalie rule as `powerplay.goal_is_power_play`),
then own > opp = power play, own < opp = penalty kill, equal = even strength (`toi_ev`; `toi_ev5` is the strict 5v5 subset). Two
cleanups were needed and both come from real games: exact duplicate shift rows are merged (they invent a sixth skater), and every
stretch between two play-by-play events that record the SAME strength is pinned to it (a too-many-men sixth skater is on the ice
before the call; the ledger does not count it).

Output per (game, player): `toi_total toi_ev toi_ev5 toi_pp toi_pk n_shifts team_pp_s team_pk_s pp_share pp_unit_guess`.
`pp_share` = his power-play seconds / the seconds his team was on the power play; `pp_unit_guess` = 1 if the share is >= 0.5, 2 if it is
smaller (and he had >= 10 s), else 0. Resumable (a game already in the output is skipped, 404s are recorded not retried), raw responses
cached under the gitignored `data/nhl_toi_cache/`, default delay 1.0 s, 429s honour Retry-After (the existing `NhlClient`).

## 3. Validation

Two real samples: a DEV sample (37 games, 8 dates 2021-12 to 2025-02, 1,332 skater-games; the cleanups above were found on it) and a
HELD-OUT sample (27 games drawn with a fixed seed after the rules were frozen: 20 random 2021-26 games, 4 shootout games, 3 playoff
overtime games; 972 skater-games). 64 shift-chart requests in total.

| check | dev (37 games) | held-out (27 games) |
|---|---|---|
| split sum (ev+pp+pk) == box score TOI, exact | 99.55% of rows (6 rows off, max 60 s, in 3 games) | 100.0% |
| rows more than 5 s off the box TOI | 0.45% | 0.0% |
| shift-count strength == play-by-play strength at event times (before pinning) | 99.63% exact counts, 99.69% same PP/EV/PK direction | 99.80% / 99.93% |
| vs the NHL's published EV time: within 5 s / mean abs error | 99.55% / 0.16 s | 100% / 0.01 s |
| vs the NHL's published PP time: within 5 s / mean abs error | 100% / 0.012 s | 100% / 0.005 s |
| vs the NHL's published PK time: within 5 s / mean abs error | 100% / 0.012 s | 100% / 0.004 s |

The mismatch rate, honestly: **6 of 2,304 rows (0.26%) and 3 of 64 games (4.7%)** have a shift-chart total that disagrees with the
box TOI (all 3 are in the dev sample; the published split agrees with the box there, so the chart is the odd one out). The three parts
always sum to the shift total by construction; the real test is the chart vs the box, and vs the NHL's independent published split.
Caveat: 64 games is a small sample, shootout / OT / playoff coverage is thin (4 shootout games, 3 playoff-OT games, 6 regular-season OT games), and the pinning rule trusts the play-by-play
strength, which would be wrong in a game whose situationCodes are wrong (none seen).

PP1 vs PP2 is a GUESS and cannot be validated against ground truth (the league publishes no unit labels). The structural check: among
team-games with >= 90 s on the power play, exactly 5 players are labelled PP1 in 78% (dev, 69 team-games) / 82% (held-out, 51), and
4-6 in 93% / 88%. A real first unit is 3F + 2D, so most games look like one; short power plays and 5-on-3s blur it.

## 4. Walk-forward accuracy: do the features help? (accuracy only)

Setup. `nhl.props.countmodel.walk_forward` (XGBoost count:poisson + NegBin dispersion, refit every 4 months, every row scored by a model
that never saw its date), test period 2022-10-01 to 2026-11-03 = 210,122 skater-games (2022-23 .. 2025-26 plus 8,855 rows of 2026-27).
`base` = the 36 `nhl.props.xgfeat.SKATER_FEATURES` (the current research set, incl. xG-based rates and `toi_l10`); `toi` = base + 6
prior-only features (`toi_ev_l10 toi_pp_l10 toi_pk_l10 pp_frac_l20 pp_share_l10 pp1_share_l10`; coverage of the test rows 99.3-99.8%).
Same rows, same schedule, paired. Standard errors cluster by game. `control` = the base features refit with another xgboost seed: what a
change carrying NO information scores. The TOI inputs for this run are the NHL's own published per-game split for all 9,403 games
(`--official-from`, 140 date-window requests), NOT the shift-derived table: the validation above shows the two agree to the second on
the validated rows (and the cheap table's team-PP-time estimate has corr 0.9997 with the exact one, with identical `pp_unit_guess` on all
1,332 dev rows), and a shift backfill of 9,403 games was not the sample the owner asked for.

| stat | log score base -> toi | gain per row (SE) | z | MAE base -> toi | seasons gain > 0 | noise floor (control gain) |
|---|---|---|---|---|---|---|
| goals | -0.4487 -> -0.4487 | +0.00004 (0.00004) | 1.18 | 0.2689 -> 0.2694 (+0.16%) | 3/5 | -0.00006 |
| assists | -0.6397 -> -0.6393 | +0.00041 (0.00009) | 4.67 | 0.4021 -> 0.4016 (-0.11%) | 5/5 | -0.00001 |
| points | -0.8335 -> -0.8328 | +0.00063 (0.00010) | 6.29 | 0.5250 -> 0.5245 (-0.09%) | 5/5 | +0.00004 |
| sog | -1.5640 -> -1.5639 | +0.00006 (0.00006) | 1.15 | 1.0463 -> 1.0463 (0.00%) | 4/5 | -0.00007 |

Bar (the repo's, `docs/solutions/conventions/nfl-projection-engine-changes-need-walkforward-evidence.md`, Bonferroni over the 4 stats):
paired log-score gain >= 2.50 SE, MAE agrees, gain positive in most test seasons. Per-season tables are in the results JSON.

Reading it:
- **Goals and SOG: no measurable gain.** Gains are inside the seed-noise floor (|control| up to 0.00007).
- **Assists and points: a real but very small gain.** +0.0004 / +0.0006 nats per skater-game (0.06-0.08% of the log score; MAE -0.1%),
  positive in every test season, 6-9x the seed-noise floor, z 4.7 / 6.3. They clear the statistical bar on the research XGBoost models.
  For scale: the hierarchical engine's own gain on the same stats was +0.004 (assists) and +0.006 (points) log score (nhl_hierarchical_engine_2026-11-01.md).
  A plausible mechanism (not tested): trailing PP time and unit share say who will be on the ice for the power play, where assists and points concentrate.
- **Not adopted.** The repo rule is to measure the SERVING code, and the serving engine is the hierarchical empirical-Bayes projection
  (`nhl/props/hierarchical.py`), not these XGBoost models (which are shadow-only: `NHL_TRAINED_PROPS` is OFF, no money has been tied to them).
  A TOI-aware serving change would need its own serving-function replay. The features stay OFF everywhere (nothing outside the tests and
  `eval_toi_splits` imports `toi_feat`), the engine id is unchanged.
- No price was looked at: this says nothing about edge. The earlier market work (nhl_engine_vs_market_2026-11-01.md) found the engine adds no information beyond the
  market on goals/assists/points (blend c <= 0.06); whether a 0.1% MAE gain changes that is untested.

## 5. Cost of a full backfill (follow-up for the owner)

- Shift-derived table (needs PP1/PP2 EXACT team PP time and the per-second timeline): one shiftchart request per game, play-by-play from the
  existing GCS mirror (0 requests). 9,403 regular-season + playoff games in the masters (2019-20 .. 2026-27 to date) = **9,403 requests**;
  measured 1.16 s per game at `--delay 1.0` -> about **3 hours** single-threaded; raw cache about 17 KB gzipped per game = about 160 MB.
  Without `--pbp-from-gcs` add 9,403 play-by-play requests (~6 hours total).
- Official published split: **~150 requests, ~5 minutes** (14-day windows, 1.5 s delay), then ~1 request per game day nightly. It gives
  ev/pp/pk TOI and, with the team-PP estimate in `official_as_splits`, the same `pp_unit_guess` on the dev sample. It does not give the
  per-second state, so no shift-level work (line combos, PP unit membership by name).
- Requests made for this PR: about 230 (64 shift charts, 148 official-report windows/days, a handful of endpoint probes), none in parallel.

## 6. Decisions for the owner

1. Is a +0.0004..0.0006 nats / -0.1% MAE gain on assists and points worth a serving-engine experiment? If yes, the cheap route is the
   official report (150 requests + a nightly day), not 9,403 shift charts; the shift builder is only needed for per-second work.
2. If yes, the next step is an `empirical_ewm`/hierarchical serving replay (not these XGBoost models) with a pre-registered bar.
3. Scheduling a nightly job (and any GCS write via `--write-gcs`) is yours; nothing here is deployed.

## Reproduce

```bash
gcloud storage cp "gs://<bucket>/NHL/*.csv" <dir>/NHL/ ; gcloud storage cp "gs://<bucket>/NHL/shots/*.csv" <dir>/NHL/shots/   # NOT gsutil -m
PYTHONPATH=. python -m nhl.xg.build --data-dir <dir> --out-dir <xg> --no-save-models
PYTHONPATH=. python -m nhl.data.toi_splits --official-from 2019-09-25 --official-to 2026-11-03 --out-dir <off>      # in season chunks, ~150 requests
PYTHONPATH=. python -m nhl.props.eval_toi_splits --data-dir <dir> --xg-dir <xg> --splits <off>/official_toi.csv --out-dir <eval> --control --json r.json --markdown r.md   # ~1 h
PYTHONPATH=. python -m nhl.data.toi_splits --data-dir <dir> --dates 2024-11-20 --max-per-date 5 --pbp-from-gcs --out-dir <sample>   # shift builder sample
PYTHONPATH=. python -m nhl.data.toi_splits --validate --out-dir <sample> --data-dir <dir> --official-csv <off>/official_toi.csv
python -m pytest tests/test_nhl_toi_splits.py tests/test_nhl_toi_feat.py
```
