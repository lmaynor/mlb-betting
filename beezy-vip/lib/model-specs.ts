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
  /** the model family when it is not the default XGBoost classifier/regressor (e.g. a proxy anchored to another model) */
  algorithm?:  string
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
    learnSlug:   'what-is-nrfi-betting',
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
    learnSlug:   'mlb-home-run-props',
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
    learnSlug:   'f5-betting-mlb',
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
    learnSlug:   'mlb-strikeout-props-guide',
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
  {
    system:      'GAME',
    slug:        'game',
    name:        'Full Game',
    version:     'v1',
    color:       '#e3b261',
    description: 'Predicts the probability the home team wins the full game. Binary logistic XGBoost with isotonic calibration; its edge over the F5 model is dedicated bullpen features (xwOBA allowed, K%, BB%, whiff, hard-hit and recent fatigue) that a first-five window never sees. Log-only until it clears the 200-bet gate.',
    dataRange:   '2023\u20132025',
    algorithm:   'XGBoost binary:logistic + isotonic calibration',
    oosAUC:      0.5565,
    edgeThreshold: 0.04,
    kellyFraction: 0.25,
    features: [
      { name: 'Starter xwOBA (L3)',        description: 'Both starters\' expected wOBA allowed over their last 3 starts', importance: 'High' },
      { name: 'Starter Whiff % (L3)',      description: 'Swinging-strike tendency of each starter, last 3 starts', importance: 'Medium' },
      { name: 'Starter Hard-Hit % (L3)',   description: 'Hard-hit rate allowed by each starter, last 3 starts', importance: 'Medium' },
      { name: 'Bullpen xwOBA (L14)',       description: 'Expected wOBA allowed by each bullpen over 14 days', importance: 'High' },
      { name: 'Bullpen K% / BB% (L14)',    description: 'Strikeout and walk rates of each bullpen over 14 days', importance: 'Medium' },
      { name: 'Bullpen Whiff / Hard-Hit',  description: 'Swing-and-miss and contact quality allowed by each bullpen', importance: 'Medium' },
      { name: 'Bullpen Fatigue (IP L7)',   description: 'Relief innings thrown in the last 7 days: a tired pen is a weaker pen', importance: 'High' },
      { name: 'Offense wOBA (L20)',        description: 'Each lineup\'s wOBA over its last 20 games', importance: 'High' },
      { name: 'Offense Hard-Hit % (L20)',  description: 'Each lineup\'s hard-hit rate over its last 20 games', importance: 'Medium' },
      { name: 'Park and Weather',          description: 'Ballpark run factor, temperature, wind and roof state', importance: 'Medium' },
    ],
  },
  {
    system:      'BATTER_TB',
    slug:        'batter-tb',
    name:        'Batter Total Bases',
    version:     'v1',
    color:       '#a9d166',
    // No published out-of-sample metric for this system yet, so the detail page shows an honest "pending" state.
    description: 'Predicts expected total bases for a batter in a game. A count regression (XGBoost count:poisson) gives the expected value, and a negative-binomial distribution turns it into the probability of clearing a line. Built from extra-base and contact quality, platoon splits, lineup slot, the opposing pitcher\'s damage profile and the park. Needs a confirmed lineup to score.',
    dataRange:   '2021\u20132025',
    oosAUC:      0,
    algorithm:   'XGBoost count:poisson + negative binomial',
    metricStatus: 'pending',
    edgeThreshold: 0.04,
    kellyFraction: 0.25,
    features: [
      { name: 'TB per Game (L20 / L50)',   description: 'Rolling total bases per game: the direct recent-rate signal', importance: 'High' },
      { name: 'TB Rate (L20 / season)',    description: 'Total bases per plate appearance, recent and season', importance: 'High' },
      { name: 'XBH Rate (L20 / L50)',      description: 'Extra-base hit rate: doubles, triples and homers drive total bases', importance: 'High' },
      { name: 'SLG on Contact',            description: 'Slugging on batted balls, last 20 and 50 games', importance: 'Medium' },
      { name: 'Barrel / Hard-Hit Rate',    description: 'Quality of contact over the last 20 games', importance: 'High' },
      { name: 'Line-Drive / Fly-Ball Rate', description: 'Batted-ball mix, which shapes how much contact turns into bases', importance: 'Medium' },
      { name: 'PA per Game, Batting Slot', description: 'Expected plate-appearance volume from lineup position', importance: 'High' },
      { name: 'TB vs Handedness',          description: 'Career and season total bases against the opposing pitcher\'s arm', importance: 'Medium' },
      { name: 'Pitcher Damage Allowed',    description: 'Opposing pitcher TB/9, XBH rate, hard-hit and barrel rate allowed', importance: 'High' },
      { name: 'Park Factor (TB)',          description: 'Ballpark total-base factor', importance: 'Medium' },
      { name: 'Weather and Roof',          description: 'Temperature and dome flag', importance: 'Low' },
      { name: 'Pitch Clock Regime',        description: 'Post-2023 rules era indicator', importance: 'Low' },
    ],
  },
  {
    system:      'BATTER_HITS',
    slug:        'batter-hits',
    name:        'Batter Hits',
    version:     'v1',
    color:       '#4fc7bd',
    // Log-only and no published out-of-sample metric yet: shown as pending rather than as a number.
    description: 'Predicts expected hits for a batter in a game. A dedicated count regression (XGBoost count:poisson) with a negative-binomial distribution for the over/under probability, built from contact rate, BABIP, batted-ball mix, platoon splits and how many balls in play the opposing pitcher allows. Log-only until it clears the 200-bet gate.',
    dataRange:   '2021\u20132025',
    oosAUC:      0,
    algorithm:   'XGBoost count:poisson + negative binomial',
    metricStatus: 'pending',
    edgeThreshold: 0.04,
    kellyFraction: 0.25,
    features: [
      { name: 'Hits per Game (L20 / L50)', description: 'Rolling hits per game: the direct recent-rate signal', importance: 'High' },
      { name: 'Hit Rate (L20 / season)',   description: 'Hits per plate appearance, recent and season', importance: 'High' },
      { name: 'BABIP (L20 / L50)',         description: 'Batting average on balls in play: contact quality on balls in play', importance: 'High' },
      { name: 'Contact % / Chase %',       description: 'Fewer whiffs and fewer chases mean more balls in play', importance: 'Medium' },
      { name: 'Line-Drive / Ground-Ball Rate', description: 'Batted-ball mix; line drives carry the highest BABIP', importance: 'Medium' },
      { name: 'Hard-Hit % (L20)',          description: 'Share of hard-hit balls', importance: 'Medium' },
      { name: 'PA per Game, Batting Slot', description: 'Expected plate-appearance volume from lineup position', importance: 'High' },
      { name: 'Hits vs Handedness',        description: 'Career and season hit rate against the opposing pitcher\'s arm', importance: 'Medium' },
      { name: 'Pitcher BABIP / H9 Allowed', description: 'How many hits the opposing starter allows on balls in play', importance: 'High' },
      { name: 'Pitcher GB% and K%',        description: 'Ground-ball pitchers allow more hits; strikeout pitchers allow fewer balls in play', importance: 'Medium' },
      { name: 'Park Factor (hits)',        description: 'Ballpark hit factor', importance: 'Medium' },
      { name: 'Weather and Roof',          description: 'Temperature and dome flag', importance: 'Low' },
    ],
  },
  {
    system:      'PITCHER_ER',
    slug:        'pitcher-er',
    name:        'Pitcher Earned Runs',
    version:     'v1',
    color:       '#ef7f6e',
    // Not separately trained: a Gamma proxy anchored to the K model's expected value and innings. No metric of its own.
    description: 'Prices a starter\'s earned runs from the K model\'s expected strikeouts and innings. It is a proxy, not a separately trained model: a Gamma distribution anchored to the K model\'s lambda and average innings, adjusted for opponent run creation, weather and the starter\'s leash. Log-only, and its own track record decides whether it is kept.',
    dataRange:   '2021\u20132025 (via the K model)',
    oosAUC:      0,
    algorithm:   'Gamma proxy anchored to the K model (not separately trained)',
    metricStatus: 'proxy',
    edgeThreshold: 0.04,
    kellyFraction: 0.25,
    features: [
      { name: 'K Model Expected Value',    description: 'The strikeout model\'s lambda and innings estimate: the anchor for the whole distribution', importance: 'High' },
      { name: 'Expected Innings',          description: 'Average innings per start: more innings, more exposure to runs', importance: 'High' },
      { name: 'Opponent Run Creation',     description: 'The opposing lineup\'s run-scoring strength', importance: 'High' },
      { name: 'Park Factor (runs)',        description: 'Ballpark run-scoring factor', importance: 'Medium' },
      { name: 'Weather',                   description: 'Temperature and wind at game time', importance: 'Low' },
      { name: 'Leash',                     description: 'How quickly the manager pulls the starter', importance: 'Medium' },
    ],
  },
]

export function getModelSpec(slug: string): ModelSpec | undefined {
  return MODEL_SPECS.find(m => m.slug === slug)
}
