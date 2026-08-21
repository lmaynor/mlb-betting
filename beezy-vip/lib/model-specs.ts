export interface ModelSpec {
  system:      string
  slug:        string
  name:        string
  version:     string
  color:       string
  description: string
  dataRange:   string
  oosAUC:      number   // for K: stores MAE. Ignored when metricStatus is set below.
  // Set when there is no real trained-and-evaluated metric to show, so the
  // detail page can render an honest state instead of a raw oosAUC number:
  //   'proxy'   -- not a separately trained model, e.g. OUTS (IP simulation
  //                 derived from K's features)
  //   'pending' -- brand-new system, no settled/backtested metric yet, e.g.
  //                 SB at launch
  // Omit entirely for a normal trained-and-evaluated system.
  metricStatus?: 'proxy' | 'pending'
  features:    Array<{ name: string; description: string; importance: string }>
  edgeThreshold: number
  kellyFraction: number
  learnSlug?:  string
}

export const MODEL_SPECS: ModelSpec[] = [
  {
    system:      'NRFI',
    slug:        'nrfi',
    name:        'No Run First Inning',
    version:     'v17',
    color:       '#00FF87',
    description: 'Predicts whether the first inning will be scoreless. Trained on pitch-level Statcast data from 2021–2025 with walk-forward cross-validation. Primary signal is the combination of both starters\' first-inning tendencies, umpire called-strike tendency, and atmospheric conditions.',
    dataRange:   '2021–2025',
    oosAUC:      0.577,
    edgeThreshold: 0.04,
    kellyFraction: 0.5,
    learnSlug:   'what-is-nrfi',
    features: [
      { name: 'Home Starter ERA (L5)',     description: 'Home starter ERA over last 5 starts', importance: 'High' },
      { name: 'Away Starter ERA (L5)',     description: 'Away starter ERA over last 5 starts', importance: 'High' },
      { name: 'Home Starter K/9 (season)', description: 'Home starter strikeout rate',         importance: 'High' },
      { name: 'Away Starter K/9 (season)', description: 'Away starter strikeout rate',         importance: 'High' },
      { name: 'Umpire Called Strike %',   description: 'Umpire tendency to call strikes — high = fewer runs', importance: 'High' },
      { name: 'Umpire K Rate',            description: 'Umpire strikeout rate vs. league avg', importance: 'Medium' },
      { name: 'Park Factor (runs)',        description: 'Ballpark run-scoring factor',          importance: 'Medium' },
      { name: 'Wind Speed (mph)',          description: 'Wind speed at game time',              importance: 'Medium' },
      { name: 'Wind Direction',           description: 'Blowing in vs. out to CF',             importance: 'Medium' },
      { name: 'Temperature',              description: 'Game-time temperature',                 importance: 'Low' },
      { name: 'Home Starter NRFI Rate',   description: 'Historical NRFI rate for home starter', importance: 'High' },
      { name: 'Away Starter NRFI Rate',   description: 'Historical NRFI rate for away starter', importance: 'High' },
    ],
  },
  {
    system:      'HR',
    slug:        'hr',
    name:        'Home Run Props',
    version:     'v6',
    color:       '#FFB800',
    description: 'Predicts batter HR probability per plate appearance. Combines batter Statcast quality-of-contact metrics with opposing pitcher HR vulnerability and ballpark factors. Platoon splits applied as a multiplicative adjustment.',
    dataRange:   '2021–2025',
    oosAUC:      0.630,
    edgeThreshold: 0.03,
    kellyFraction: 0.33,
    learnSlug:   'home-run-props',
    features: [
      { name: 'Barrel Rate',          description: 'Batted ball barrel rate (ideal launch angle + exit velo combo)', importance: 'High' },
      { name: 'Hard Hit %',           description: 'Batted ball hard-hit rate (exit velo >= 95 mph)',               importance: 'High' },
      { name: 'Launch Angle (avg)',   description: 'Season average launch angle',                                   importance: 'High' },
      { name: 'Pitcher HR/9',         description: 'Opposing pitcher HR allowed per 9 innings',                    importance: 'High' },
      { name: 'Pitcher FB Rate',      description: 'Opposing pitcher fly ball rate — more FB = more HR exposure',  importance: 'Medium' },
      { name: 'Park HR Factor',       description: 'Ballpark HR factor (L/R split available)',                     importance: 'Medium' },
      { name: 'Platoon Split',        description: 'Batter vs. pitcher handedness HR rate adjustment',             importance: 'Medium' },
      { name: 'Pull %',               description: 'Batter pull rate — correlates with power output',              importance: 'Low' },
      { name: 'ISO (season)',         description: 'Isolated power (SLG - BA)',                                    importance: 'Medium' },
      { name: 'Recent Form (L14)',    description: 'HR rate over last 14 days',                                    importance: 'Low' },
    ],
  },
  {
    system:      'F5',
    slug:        'f5',
    name:        'First 5 Innings',
    version:     'v5',
    color:       '#00B4FF',
    description: 'Predicts F5 spread and total outcomes. F5 bets isolate starting pitcher performance and remove bullpen variance. Model focuses on starter quality metrics, recent form, and opponent quality.',
    dataRange:   '2022–2025',
    oosAUC:      0.553,
    edgeThreshold: 0.04,
    kellyFraction: 0.5,
    learnSlug:   'f5-betting',
    features: [
      { name: 'Starter SIERA',        description: 'Skill-Interactive ERA — best predictor of future performance', importance: 'High' },
      { name: 'Starter ERA (L5)',      description: 'ERA over last 5 starts',                                      importance: 'High' },
      { name: 'Opponent wOBA',        description: 'Opponent weighted on-base average (season)',                   importance: 'High' },
      { name: 'Opponent wOBA (L14)',  description: 'Opponent wOBA over last 14 days',                             importance: 'Medium' },
      { name: 'Starter IP/Start',     description: 'Average innings per start — durability proxy',                importance: 'Medium' },
      { name: 'Umpire Run Rate',      description: 'Umpire tendency to allow runs vs. league avg',                importance: 'Medium' },
      { name: 'Park Factor (runs)',   description: 'Ballpark run-scoring factor',                                  importance: 'Low' },
      { name: 'Home/Away Split',      description: 'Starter ERA home vs. away differential',                     importance: 'Low' },
    ],
  },
  {
    system:      'K',
    slug:        'k',
    name:        'Strikeout Props',
    version:     'v1',
    color:       '#BF5FFF',
    // oosAUC field stores MAE for K (Poisson regression -- AUC is not the right metric)
    description: 'Projects starter strikeout totals using pitch movement and command metrics alongside opponent lineup K vulnerability. Poisson regression model; evaluated by MAE not AUC.',
    dataRange:   '2021–2025',
    oosAUC:      1.807,
    edgeThreshold: 0.05,
    kellyFraction: 0.5,
    learnSlug:   'mlb-strikeout-props',
    features: [
      { name: 'Swinging Strike %',       description: 'SwStr% — single best predictor of strikeout rate',       importance: 'High' },
      { name: 'Zone %',                  description: '% of pitches in the strike zone',                        importance: 'High' },
      { name: 'Chase %',                 description: '% of pitches outside zone that batters swing at',        importance: 'High' },
      { name: 'Opponent K % (season)',   description: 'Opposing lineup strikeout rate season-long',             importance: 'High' },
      { name: 'Opponent K % (L14)',      description: 'Opposing lineup K rate over last 14 days',              importance: 'Medium' },
      { name: 'L5 Average Strikeouts',   description: 'Starter avg Ks over last 5 starts',                     importance: 'High' },
      { name: 'Pitches/PA (season)',     description: 'Average pitches per PA — more pitches = more K chances', importance: 'Medium' },
      { name: 'Home/Away K Split',       description: 'Starter K rate home vs. away',                          importance: 'Low' },
      { name: 'Ump K Rate',              description: 'Umpire strikeout tendency vs. league avg',               importance: 'Medium' },
    ],
  },
  {
    system:      'OUTS',
    slug:        'outs',
    name:        'Outs Props',
    version:     'v1',
    color:       '#FF6B35',
    description: 'Projects outs recorded by a starter via Normal IP simulation (proxy model derived from K system features). Not a separately trained model.',
    dataRange:   '2021–2025',
    oosAUC:      0,
    metricStatus: 'proxy',
    edgeThreshold: 0.04,
    kellyFraction: 0.4,
    features: [
      { name: 'Pitch Efficiency',         description: 'Pitches per out (season) — lower = deeper into games',   importance: 'High' },
      { name: 'IP/Start (season)',        description: 'Average innings per start',                              importance: 'High' },
      { name: 'IP/Start (L5)',            description: 'Innings per start over last 5',                         importance: 'High' },
      { name: 'Bullpen Availability',     description: 'Bullpen rest score — tired bullpen = longer starter',   importance: 'Medium' },
      { name: 'Opponent OBP',            description: 'Opponent on-base percentage (season)',                   importance: 'Medium' },
      { name: 'Opponent OBP (L14)',      description: 'Opponent OBP over last 14 days',                        importance: 'Medium' },
      { name: 'Win Probability Context', description: 'Expected leverage — blowouts = shorter starts',         importance: 'Low' },
      { name: 'Home/Away Split',         description: 'IP/Start differential home vs. away',                   importance: 'Low' },
    ],
  },
  {
    system:      'SB',
    slug:        'sb',
    name:        'Stolen Base Props',
    version:     'v1',
    color:       '#d9cf5a',
    // Brand new 2026-08-20, LOG_ONLY -- no settled bets yet, so there is no
    // real backtested metric to show. metricStatus: 'pending' below renders
    // an honest "not yet available" state on the detail page instead of a
    // raw oosAUC number.
    description: 'Predicts expected stolen bases per batter per game. NegBin count regression (XGBoost count:poisson) on on-base opportunity -- reaching via single/walk/HBP specifically, since a double or homer skips the steal chance entirely -- sprint speed, and recent attempt/success rate, adjusted for the opposing pitcher hold profile and lineup slot. The first beezy.fyi model to add opposing catcher defense (pop time and arm strength to second) as a feature, sourced from MLB Stats API boxscores since the Statcast public pitch-level feed carries no SB/CS events at all.',
    dataRange:   '2023–2025',
    oosAUC:      0,
    metricStatus: 'pending',
    edgeThreshold: 0.04,
    kellyFraction: 0.25,
    features: [
      { name: 'On-Base Rate (L20)',      description: 'Reaching base via single/walk/HBP per PA, last 20 games -- the events that actually create a steal opportunity', importance: 'High' },
      { name: 'Sprint Speed',            description: 'Statcast sprint speed (season) -- raw baserunning speed floor', importance: 'High' },
      { name: 'SB Rate (L20)',           description: 'Rolling stolen bases per game, last 20 -- direct recent-rate signal', importance: 'High' },
      { name: 'SB Attempt Rate (L20)',   description: '(SB+CS) attempts per game, last 20 -- how often this runner actually goes', importance: 'High' },
      { name: 'SB Success % (L50)',      description: 'Stolen base success rate over the last 50 games', importance: 'Medium' },
      { name: 'Batting Order Slot',      description: 'Recency-weighted lineup slot -- top-of-order hitters see more basestealing opportunities', importance: 'Medium' },
      { name: 'Pitcher Handedness',      description: 'Opposing pitcher throws left-handed -- a real hold advantage facing first base', importance: 'Medium' },
      { name: 'Pitcher SB Allowed',      description: 'Opposing pitcher stolen bases allowed, season-level', importance: 'Medium' },
      { name: 'Catcher Pop Time',        description: 'Opposing catcher pop time to 2nd on steal attempts -- lower pop time suppresses both attempts and success', importance: 'High' },
      { name: 'Catcher Arm Strength',    description: 'Opposing catcher max-effort arm strength to 2B/3B on steal attempts', importance: 'Medium' },
      { name: 'Pitch Clock Regime',      description: '2023 pitch-clock / bigger-base / disengagement-limit rules -- steal rates rose leaguewide after this change', importance: 'Medium' },
    ],
  },
]

export function getModelSpec(slug: string): ModelSpec | undefined {
  return MODEL_SPECS.find(m => m.slug === slug)
}
