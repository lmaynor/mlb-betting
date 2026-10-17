/**
 * Specification + validation facts for the NFL / NHL / NBA prop models, for the /sports/[sport] Models tab.
 *
 * Every number below is copied from the repo's own 2026-10-14 studies (docs/analysis/nba_props_evaluation_2026-10-14.md,
 * docs/analysis/nhl_sog_validation_2026-10-14.md, handoffs/handoff_2026-10-14_nfl_prop_systems_shadow.md). A metric a
 * study did not produce is null and renders as "--": nothing here is estimated or filled in.
 *
 * Two different things are described and kept apart:
 *  - `research`: the trained model (XGBoost + a fitted distribution) evaluated in those studies. Its artifacts are local
 *    only and are NOT what the live board runs.
 *  - LIVE_ENGINE: the empirical estimator that produces the daily board (ev/daily_projections.py, engine
 *    "empirical_ewm_v1"). It has not been validated against prices.
 */
import type { SportSlug } from '@/lib/sports'

export interface ModelValidation {
  /** evaluation sample, as the study states it */
  sample: string
  /** out-of-sample MAE of the model, and of the best naive baseline it was compared with */
  mae: number | null
  baselineMae: number | null
  crps: number | null
  baselineCrps: number | null
  brier: number | null
  ece: number | null
}

export interface PropModelSpec {
  key: string
  label: string
  /** true when a trained model exists for this stat (NHL has one: shots on goal) */
  trained: boolean
  distribution: string
  validation: ModelValidation | null
  /**
   * Blend coefficient c from the model-vs-market study (docs/analysis/bettingpros_model_vs_market_2026-10-15.md):
   * logit(p) = logit(market) + c * (logit(model) - logit(market)); c > 0 with a large t means the TRAINED model adds
   * information the market lacks. Null where the study did not cover the stat. It is about the research model, not the
   * live empirical engine.
   */
  blend?: { c: number; t: number } | null
  verdict: string
}

export interface SportModelSpec {
  sport: Exclude<SportSlug, 'mlb'>
  models: PropModelSpec[]
  features: string[]
  /** what the studies concluded about prices, in one sentence */
  marketVerdict: string
  sources: string
}

// c (t) per stat from the 2026-10-15 BettingPros study; only the stats that study reviewed are here.
const BLEND: Record<string, Record<string, { c: number; t: number }>> = {
  nfl: { pass_yds: { c: 0.43, t: 2.01 }, rush_yds: { c: 0.03, t: 0.27 }, rec_yds: { c: 0.02, t: 0.2 }, receptions: { c: 0.1, t: 1.15 } },
  nhl: { sog: { c: 0.3, t: 4.9 } },
  nba: { points: { c: 0.07, t: 2.99 }, rebounds: { c: 0.19, t: 7.71 }, assists: { c: 0.18, t: 7.65 }, threes: { c: 0.28, t: 8.32 }, pra: { c: 0.05, t: 2.52 } },
}
const withBlend = (sport: string, models: PropModelSpec[]): PropModelSpec[] =>
  models.map(m => ({ ...m, blend: BLEND[sport]?.[m.key] ?? null }))

const none = (sample: string): ModelValidation => ({ sample, mae: null, baselineMae: null, crps: null, baselineCrps: null, brier: null, ece: null })

export const LIVE_ENGINE = {
  name: 'Empirical projection (empirical_ewm_v1)',
  summary:
    'The daily board runs an empirical estimator: exponentially weighted game-log average, blended with a usage-adjusted rate, scaled by an opponent-allowed factor, with a negative-binomial or normal spread fitted from the league\'s own game-to-game variance. It does not model injuries, lineups, minutes, weather or teammate absences, and it has not been validated against prices.',
}

const NBA_SAMPLE = '4 rolling-origin test seasons (2022-2025), 186,447 played player-games in the master'

export const SPORT_MODEL_SPECS: Record<Exclude<SportSlug, 'mlb'>, SportModelSpec> = {
  nfl: {
    sport: 'nfl',
    models: withBlend('nfl', [
      { key: 'pass_yds', label: 'Passing Yards', trained: true, distribution: 'XGBoost (Tweedie) + Hurdle-Gamma / Normal, chosen by out-of-sample log score', validation: none('Walk-forward 2021-2025'), verdict: 'Beats trailing-mean baselines' },
      { key: 'rush_yds', label: 'Rushing Yards', trained: true, distribution: 'XGBoost (Tweedie) + Hurdle-Gamma / Normal, chosen by out-of-sample log score', validation: none('Walk-forward 2021-2025'), verdict: 'Beats trailing-mean baselines' },
      { key: 'rec_yds', label: 'Receiving Yards', trained: true, distribution: 'XGBoost (Tweedie) + Hurdle-Gamma / Normal, chosen by out-of-sample log score', validation: none('Walk-forward 2021-2025'), verdict: 'Beats trailing-mean baselines' },
      { key: 'receptions', label: 'Receptions', trained: true, distribution: 'XGBoost (Poisson) + NegBin', validation: none('Walk-forward 2021-2025'), verdict: 'Beats trailing-mean baselines' },
      { key: 'pass_tds', label: 'Passing Touchdowns', trained: true, distribution: 'XGBoost (Poisson) + NegBin', validation: none('Walk-forward 2021-2025'), verdict: 'Beats trailing-mean baselines' },
      { key: 'anytime_td', label: 'Anytime Touchdown', trained: true, distribution: 'XGBoost (Poisson) + NegBin', validation: none('Walk-forward 2021-2025'), verdict: 'Beats trailing-mean baselines' },
      { key: 'pass_completions', label: 'Pass Completions', trained: true, distribution: 'XGBoost (Poisson) + NegBin (opt-in stat)', validation: null, verdict: 'Added 2026-10-14; not yet validated' },
      { key: 'pass_attempts', label: 'Pass Attempts', trained: true, distribution: 'XGBoost (Poisson) + NegBin (opt-in stat)', validation: null, verdict: 'Added 2026-10-14; not yet validated' },
      { key: 'rush_attempts', label: 'Rushing Attempts', trained: true, distribution: 'XGBoost (Poisson) + NegBin (opt-in stat)', validation: null, verdict: 'Added 2026-10-14; not yet validated' },
      { key: 'interceptions', label: 'Interceptions Thrown', trained: false, distribution: 'Empirical projection, NegBin spread', validation: null, verdict: 'No trained model; unvalidated' },
    ]),
    features: [
      'Trailing L3 / L5 / season and EWM averages',
      'Closing-line game environment (spread and total)',
      'Opponent-allowed rates',
      'Red-zone usage from play-by-play',
      'Injury report status',
      'Missing-teammate usage redistribution (experimental, off by default)',
    ],
    marketVerdict:
      'No edge shown. On the banked weeks (main line, 3+ books) the model-minus-market Brier gap was -0.005 +/- 0.013 (n=83), indistinguishable from zero.',
    sources: 'Walk-forward and banked-market study, 2026-10-14 (NFL prop systems handoff).',
  },
  nhl: {
    sport: 'nhl',
    models: withBlend('nhl', [
      {
        key: 'sog', label: 'Shots on Goal', trained: true, distribution: 'XGBoost (Poisson) + NegBin (alpha 0.06 - 0.10)',
        validation: { sample: 'Jan 2026 holdout; 7,007 props with 3+ books vs the market', mae: 1.01, baselineMae: 1.03, crps: null, baselineCrps: null, brier: 0.247, ece: null },
        verdict: 'NO_EDGE vs market (consensus Brier 0.2443)',
      },
      { key: 'goals', label: 'Goals', trained: false, distribution: 'Empirical projection, NegBin spread', validation: null, verdict: 'No trained model; unvalidated' },
      { key: 'points', label: 'Points', trained: false, distribution: 'Empirical projection, NegBin spread', validation: null, verdict: 'No trained model; unvalidated' },
      { key: 'assists', label: 'Assists', trained: false, distribution: 'Empirical projection, NegBin spread', validation: null, verdict: 'No trained model; unvalidated' },
      { key: 'saves', label: 'Goalie Saves', trained: false, distribution: 'Empirical projection, normal spread', validation: null, verdict: 'No trained model; unvalidated' },
      { key: 'pp_points', label: 'Power Play Points', trained: false, distribution: 'Not projected', validation: null, verdict: 'Data gap: the box-score master has no power-play assists' },
      { key: 'blocked_shots', label: 'Blocked Shots', trained: false, distribution: 'Empirical projection, NegBin spread', validation: null, verdict: 'No trained model; unvalidated' },
    ]),
    features: [
      'Rolling SOG, TOI, shot attempts and power-play attempts per player',
      'Own-team shots for and opponent shots allowed',
      'Home / away, rest days, position, league shot environment',
      'Missing-teammate shot redistribution (experimental, off by default)',
    ],
    marketVerdict:
      'NO_EDGE on 63 days of real lines: the model\'s log loss was 0.6872 vs 0.6816 for the market consensus, and bets on model disagreement lost -3.6% at the best price.',
    sources: 'NHL shots-on-goal validation, 2026-10-14.',
  },
  nba: {
    sport: 'nba',
    models: withBlend('nba', [
      { key: 'points', label: 'Points', trained: true, distribution: 'XGBoost + NegBin, PIT-recalibrated', validation: { sample: NBA_SAMPLE, mae: 4.547, baselineMae: 4.634, crps: 3.179, baselineCrps: 3.267, brier: 0.2424, ece: 0.0093 }, verdict: '1.9% lower MAE; no market edge claimable' },
      { key: 'rebounds', label: 'Rebounds', trained: true, distribution: 'XGBoost + NegBin, PIT-recalibrated', validation: { sample: NBA_SAMPLE, mae: 1.882, baselineMae: 1.912, crps: 1.294, baselineCrps: 1.329, brier: 0.2397, ece: 0.004 }, verdict: '1.6% lower MAE; no market edge claimable' },
      { key: 'assists', label: 'Assists', trained: true, distribution: 'XGBoost + NegBin, PIT-recalibrated', validation: { sample: NBA_SAMPLE, mae: 1.319, baselineMae: 1.339, crps: 0.907, baselineCrps: 0.931, brier: 0.2374, ece: 0.0048 }, verdict: '1.5% lower MAE; no market edge claimable' },
      { key: 'threes', label: '3-Pointers', trained: true, distribution: 'XGBoost + NegBin, PIT-recalibrated', validation: { sample: NBA_SAMPLE, mae: 0.871, baselineMae: 0.875, crps: 0.582, baselineCrps: 0.592, brier: 0.2089, ece: 0.0074 }, verdict: '0.5% lower MAE; no market edge claimable' },
      { key: 'pra', label: 'Points + Rebounds + Assists', trained: true, distribution: 'XGBoost + NegBin, PIT-recalibrated', validation: { sample: NBA_SAMPLE, mae: 5.981, baselineMae: 6.146, crps: 4.184, baselineCrps: 4.344, brier: 0.2392, ece: 0.0072 }, verdict: '2.7% lower MAE; no market edge claimable' },
      { key: 'pr', label: 'Points + Rebounds', trained: false, distribution: 'Empirical projection, normal spread', validation: null, verdict: 'No trained model; unvalidated' },
      { key: 'pa', label: 'Points + Assists', trained: false, distribution: 'Empirical projection, normal spread', validation: null, verdict: 'No trained model; unvalidated' },
      { key: 'ra', label: 'Rebounds + Assists', trained: false, distribution: 'Empirical projection, normal spread', validation: null, verdict: 'No trained model; unvalidated' },
      { key: 'steals', label: 'Steals', trained: false, distribution: 'Empirical projection, NegBin spread', validation: null, verdict: 'No trained model; unvalidated' },
      { key: 'blocks', label: 'Blocks', trained: false, distribution: 'Empirical projection, NegBin spread', validation: null, verdict: 'No trained model; unvalidated' },
    ]),
    features: [
      'Rolling L5 / L10 averages and EWM (half-life 8) of each stat and of minutes',
      'Opponent-allowed rates',
      'Rest days and back-to-back flags',
      'Missing-teammate minutes and usage redistribution (experimental, off by default)',
    ],
    marketVerdict:
      'No edge claimable. Accuracy gains over a rolling mean are 0.5 - 2.7% of MAE, the kind of gap a book\'s own projection already contains. Minutes and availability are the main driver the model does not have.',
    sources: 'NBA props evaluation, 2026-10-14 (CRPS: 2025-26 season; Brier / ECE: proxy lines, PIT-recalibrated).',
  },
}
