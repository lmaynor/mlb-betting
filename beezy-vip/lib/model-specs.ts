export interface ModelSpec {
  system:      string
  slug:        string
  name:        string
  version:     string
  color:       string
  description: string
  dataRange:   string
  oosAUC:      number   // for K: stores MAE; for OUTS: 0 (proxy)
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
    // oosAUC = 0 signals proxy model — no trained AUC. The detail page shows 'Proxy' when oosAUC === 0.
    description: 'Projects outs recorded by a starter via Normal IP simulation (proxy model derived from K system features). Not a separately trained model.',
    dataRange:   '2021–2025',
    oosAUC:      0,
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
]

export function getModelSpec(slug: string): ModelSpec | undefined {
  return MODEL_SPECS.find(m => m.slug === slug)
}
