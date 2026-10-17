import type { SportSlug } from '@/lib/sports'
import { resolveEvUnderlying, SYSTEM_PILL } from '@/lib/tokens'
import type { Bet } from '@/lib/types'

/** Player picks (a named player's stat) and game picks (a team or game outcome) are separate products and separate access. */
export type PickKind = 'player' | 'game'

const GAME_SYSTEMS = new Set(['NRFI', '1I', '1IOU', 'F3', 'F5', 'F1H', 'F7', 'GAME'])

export function kindOfSystem(system: string): PickKind {
  return GAME_SYSTEMS.has(system.toUpperCase()) ? 'game' : 'player'
}

/** An EV row is classified by the market it actually alerted on, not by its pooled `EV` tag. */
export function betKind(bet: Bet): PickKind {
  return kindOfSystem(bet.system === 'EV' ? resolveEvUnderlying(bet).system : bet.system)
}

export type PickSystem = {
  key: string
  slug: string
  name: string
  shortName: string
  description: string
  learnSlug?: string
  sport: SportSlug
  /** prop = a player's stat, game = a team or game outcome */
  market: 'prop' | 'game'
  /** projection / props-sweep stat key (ev/daily_projections.py PROJECT_STATS); absent for MLB's tracked systems */
  statKey?: string
  /** the distribution / model family behind the number, in one line */
  family: string
  /** what the system looks at */
  features: string[]
  /** registered so its URL keeps working, but not listed in the footer, chips or counts (no live market) */
  shelved?: boolean
  /** set when the market cannot be projected yet: the reason, shown on the system page instead of a fake projection */
  gap?: string
}

type Seed = Omit<PickSystem, 'sport' | 'market' | 'family' | 'features'> & Partial<Pick<PickSystem, 'family' | 'features' | 'market'>>

const mlb = (family: string, features: string[], seed: Seed): PickSystem => ({
  market: kindOfSystem(seed.key) === 'game' ? 'game' : 'prop', family, features, ...seed, sport: 'mlb',
})

// ---- MLB: the tracked model systems (every pick is logged and settled in the bets table) ----
const MLB_SYSTEMS: PickSystem[] = [
  mlb('XGBoost ensemble + isotonic calibration', ['Starter form and pitch mix', 'Umpire zone', 'Park and weather', 'Top-of-order quality'], {
    key: 'NRFI', slug: 'nrfi', name: 'No Run First Inning', shortName: 'NRFI',
    description: 'Starter form, umpire zone, park, and weather signals for scoreless first innings.', learnSlug: 'what-is-nrfi-betting',
  }),
  mlb('Derived from the NRFI half-inning probabilities', ['NRFI home and away half-inning probabilities'], {
    key: '1I', slug: '1i', name: 'First Inning Moneyline', shortName: '1I', shelved: true,
    description: 'Three-way first-inning sides: home, away, or draw before the game settles in.',
  }),
  mlb('Shelved: no dedicated model', ['Starter window', 'Lineup-top exposure'], {
    key: 'F3', slug: 'f3', name: 'First 3 Innings', shortName: 'F3', shelved: true,
    description: 'Early-game starter window for openers, short leashes, and lineup-top exposure.',
  }),
  mlb('XGBoost binary + isotonic calibration', ['Starter SIERA and recent form', 'Opponent wOBA', 'Bullpen quality', 'Umpire run context'], {
    key: 'F5', slug: 'f5', name: 'First 5 Innings', shortName: 'F5',
    description: 'Starting pitcher SIERA, recent form, opponent wOBA, and umpire run context.', learnSlug: 'f5-betting-mlb',
  }),
  mlb('F5 probability with a window scalar (proxy)', ['F5 model output', 'Starter splits'], {
    key: 'F1H', slug: 'f1h', name: 'First Half', shortName: 'F1H', shelved: true,
    description: 'Hybrid innings window for stronger starter splits before bullpen noise dominates.',
  }),
  mlb('Shelved: no dedicated model', ['Starter and bridge-relief pricing'], {
    key: 'F7', slug: 'f7', name: 'First 7 Innings', shortName: 'F7', shelved: true,
    description: 'Late starter and bridge-relief pricing before full bullpen exposure.',
  }),
  mlb('XGBoost binary:logistic + isotonic calibration', ['Starter rolling xwOBA, whiff and hard-hit', 'Bullpen L14 and fatigue', 'Offense L20', 'Park and weather'], {
    key: 'GAME', slug: 'game', name: 'Full Game', shortName: 'Game',
    description: 'Full-game moneyline model with starter, bullpen, park, weather, and offense context.',
  }),
  mlb('XGBoost binary + isotonic calibration', ['Barrels and exit velocity', 'Launch angle', 'Pitcher HR vulnerability', 'Park and platoon'], {
    key: 'HR', slug: 'hr', name: 'Home Run Props', shortName: 'HR',
    description: 'Barrels, exit velocity, launch angle, pitcher HR vulnerability, park, and platoon fit.', learnSlug: 'mlb-home-run-props',
  }),
  mlb('XGBoost count:poisson + NegBin CDF', ['Contact quality and XBH rate', 'Matchup and platoon', 'Lineup slot', 'Park'], {
    key: 'BATTER_TB', slug: 'batter-tb', name: 'Batter Total Bases', shortName: 'Total Bases',
    description: 'Contact quality, matchup, lineup slot, and park context for batter total-base props.',
  }),
  mlb('XGBoost count:poisson + NegBin CDF', ['BABIP and contact profile', 'Platoon', 'Pitcher hits allowed'], {
    key: 'BATTER_HITS', slug: 'batter-hits', name: 'Batter Hits', shortName: 'Hits',
    description: 'BABIP, contact profile, platoon context, and pitcher hits-allowed shape.',
  }),
  mlb('XGBoost count:poisson + NegBin CDF', ['On-base skill and sprint speed', 'Lineup slot', 'Pitcher hold rate and pickoffs', 'Catcher pop time and arm'], {
    key: 'SB', slug: 'sb', name: 'Stolen Base Props', shortName: 'SB',
    description: 'On-base skill, base-running speed, lineup slot, opposing pitcher hold rate, and catcher arm strength/pop time for stolen-base props.',
  }),
  mlb('Shelved: no paired book coverage', ['Batter whiff and contact', 'Opposing pitcher strikeout profile'], {
    key: 'BATTER_K', slug: 'batter-k', name: 'Batter Strikeouts', shortName: 'Batter K', shelved: true,
    description: 'Batter whiff and contact tendencies against opposing pitcher strikeout profile.',
  }),
  mlb('XGBoost count:poisson + NegBin Monte Carlo', ['SwStr%, zone and chase', 'Opponent lineup K rate', 'Umpire tendency', 'Recent strikeouts'], {
    key: 'K', slug: 'k', name: 'Pitcher Strikeouts', shortName: 'K',
    description: 'SwStr%, zone, chase, opponent lineup K rate, umpire tendency, and recent strikeouts.', learnSlug: 'mlb-strikeout-props-guide',
  }),
  mlb('XGBoost count:poisson + NegBin', ['Pitch efficiency', 'Innings-per-start trend', 'Bullpen availability', 'Opponent OBP'], {
    key: 'OUTS', slug: 'outs', name: 'Pitcher Outs', shortName: 'Outs',
    description: 'Pitch efficiency, innings-per-start trend, bullpen availability, and opponent OBP.',
  }),
  mlb('Gamma proxy anchored to the K model', ['K model expected value', 'Opponent run creation', 'Weather and leash'], {
    key: 'PITCHER_ER', slug: 'pitcher-er', name: 'Pitcher Earned Runs', shortName: 'Pitcher ER',
    description: 'Starter quality, opponent run creation, weather, and leash context for earned-runs props.',
  }),
]

// ---- NFL / NHL / NBA: projection systems. The live board runs the empirical estimator (ev/daily_projections.py,
// engine empirical_ewm_v1), NOT a trained model; every one is unvalidated and none is tracked in the public results. ----
const LIVE_FEATURES = (usage: string, extra: string[] = []) => [
  'Exponentially weighted game-log average', `Usage-adjusted rate (${usage})`, 'Opponent-allowed factor, shrunk and clipped', ...extra,
]

const prop = (sport: SportSlug, seed: Seed & { statKey: string; family: string; usage: string; extra?: string[] }): PickSystem => {
  const { usage, extra, ...rest } = seed
  return { market: 'prop', features: LIVE_FEATURES(usage, extra), ...rest, sport }
}

const NFL_SYSTEMS: PickSystem[] = [
  prop('nfl', { key: 'NFL_PASS_YDS', slug: 'pass-yds', name: 'Passing Yards', shortName: 'Pass Yds', statKey: 'pass_yds', family: 'Empirical EWM + Normal', usage: 'pass attempts',
    description: 'Quarterback passing yards against the books\' consensus line, from pass volume and the opponent\'s passing defense.' }),
  prop('nfl', { key: 'NFL_PASS_TDS', slug: 'pass-tds', name: 'Passing Touchdowns', shortName: 'Pass TDs', statKey: 'pass_tds', family: 'Empirical EWM + NegBin', usage: 'pass attempts',
    description: 'Quarterback touchdown passes: a low-count market priced from attempts and recent touchdown rate.' }),
  prop('nfl', { key: 'NFL_PASS_COMP', slug: 'pass-comp', name: 'Pass Completions', shortName: 'Completions', statKey: 'pass_completions', family: 'Empirical EWM + Normal', usage: 'pass attempts',
    description: 'Completions per game from attempt volume and completion rate. No dropback or pace data is modeled.' }),
  prop('nfl', { key: 'NFL_PASS_ATT', slug: 'pass-att', name: 'Pass Attempts', shortName: 'Pass Att', statKey: 'pass_attempts', family: 'Empirical EWM + Normal', usage: 'pass attempts',
    description: 'Quarterback pass attempts from recent volume. Game script (spread, total) is not modeled on the live board.' }),
  prop('nfl', { key: 'NFL_INT', slug: 'interceptions', name: 'Interceptions Thrown', shortName: 'INT', statKey: 'interceptions', family: 'Empirical EWM + NegBin', usage: 'pass attempts',
    description: 'Interceptions thrown: a rare-event count market with a wide spread, priced from attempts and recent rate.' }),
  prop('nfl', { key: 'NFL_RUSH_YDS', slug: 'rush-yds', name: 'Rushing Yards', shortName: 'Rush Yds', statKey: 'rush_yds', family: 'Empirical EWM + Normal', usage: 'carries',
    description: 'Rushing yards from carry volume, yards per carry and the opponent\'s run defense.' }),
  prop('nfl', { key: 'NFL_RUSH_ATT', slug: 'rush-att', name: 'Rushing Attempts', shortName: 'Carries', statKey: 'rush_attempts', family: 'Empirical EWM + Normal', usage: 'carries',
    description: 'Carries from recent workload. Snap share and teammate absences are not modeled on the live board.' }),
  prop('nfl', { key: 'NFL_REC_YDS', slug: 'rec-yds', name: 'Receiving Yards', shortName: 'Rec Yds', statKey: 'rec_yds', family: 'Empirical EWM + Normal', usage: 'targets',
    description: 'Receiving yards from target volume, yards per target and the opponent\'s pass defense.' }),
  prop('nfl', { key: 'NFL_RECEPTIONS', slug: 'receptions', name: 'Receptions', shortName: 'Receptions', statKey: 'receptions', family: 'Empirical EWM + NegBin', usage: 'targets',
    description: 'Catches from target volume and catch rate.' }),
  prop('nfl', { key: 'NFL_ANYTIME_TD', slug: 'anytime-td', name: 'Anytime Touchdown', shortName: 'Anytime TD', statKey: 'anytime_td', family: 'Empirical EWM + Poisson (at least one)', usage: 'carries + targets',
    description: 'Probability of at least one rushing or receiving touchdown. Return and defensive touchdowns are not counted; the books\' own scope is unverified.' }),
]

const NHL_SYSTEMS: PickSystem[] = [
  prop('nhl', { key: 'NHL_SOG', slug: 'sog', name: 'Shots on Goal', shortName: 'SOG', statKey: 'sog', family: 'Empirical EWM + NegBin', usage: 'time on ice',
    description: 'Skater shots on goal from time on ice and recent shot rate. A trained research model exists for this stat and found no edge against prices.' }),
  prop('nhl', { key: 'NHL_GOALS', slug: 'goals', name: 'Player Goals', shortName: 'Goals', statKey: 'goals', family: 'Empirical EWM + NegBin', usage: 'time on ice',
    description: 'Goals per game from recent scoring rate and ice time. A rare-event market: most prices are one-sided anytime-scorer quotes.' }),
  prop('nhl', { key: 'NHL_ASSISTS', slug: 'assists', name: 'Player Assists', shortName: 'Assists', statKey: 'assists', family: 'Empirical EWM + NegBin', usage: 'time on ice',
    description: 'Assists per game from recent rate and ice time. Linemate and power-play context are not modeled.' }),
  prop('nhl', { key: 'NHL_POINTS', slug: 'points', name: 'Player Points', shortName: 'Points', statKey: 'points', family: 'Empirical EWM + NegBin', usage: 'time on ice',
    description: 'Goals plus assists per game from recent rate and ice time.' }),
  { key: 'NHL_PP_POINTS', slug: 'pp-points', name: 'Power Play Points', shortName: 'PP Pts', sport: 'nhl', market: 'prop', statKey: 'pp_points',
    family: 'Not projected (data gap)', features: ['Needs power-play goals and assists per game'],
    description: 'Power-play points. Not projected yet: the NHL box-score master carries power-play goals but no power-play assists, so the stat cannot be built from it.',
    gap: 'The NHL skater box-score master has power_play_goals but no power-play assists column, so PP points cannot be reconstructed. Lines are only banked on the opt-in full tier. No projection is shown rather than a made-up one.' },
  prop('nhl', { key: 'NHL_SAVES', slug: 'saves', name: 'Goalie Saves', shortName: 'Saves', statKey: 'saves', family: 'Empirical EWM + Normal', usage: 'shots faced',
    description: 'Goalie saves from recent shots faced and save rate. Not posted until goalies are confirmed, so lines often arrive late in the day.' }),
  prop('nhl', { key: 'NHL_BLOCKS', slug: 'blocks', name: 'Blocked Shots', shortName: 'Blocks', statKey: 'blocked_shots', family: 'Empirical EWM + NegBin', usage: 'time on ice',
    description: 'Skater blocked shots from recent rate and ice time. Opponent shot pace is only captured through the opponent factor.' }),
]

const NBA_SYSTEMS: PickSystem[] = [
  prop('nba', { key: 'NBA_PTS', slug: 'pts', name: 'Player Points', shortName: 'Points', statKey: 'points', family: 'Empirical EWM + Normal', usage: 'minutes',
    description: 'Points per game from recent scoring rate and minutes.' }),
  prop('nba', { key: 'NBA_REB', slug: 'reb', name: 'Player Rebounds', shortName: 'Rebounds', statKey: 'rebounds', family: 'Empirical EWM + NegBin', usage: 'minutes',
    description: 'Rebounds per game from recent rate and minutes.' }),
  prop('nba', { key: 'NBA_AST', slug: 'ast', name: 'Player Assists', shortName: 'Assists', statKey: 'assists', family: 'Empirical EWM + NegBin', usage: 'minutes',
    description: 'Assists per game from recent rate and minutes.' }),
  prop('nba', { key: 'NBA_3PM', slug: 'threes', name: 'Three-Pointers Made', shortName: '3PM', statKey: 'threes', family: 'Empirical EWM + NegBin', usage: 'minutes',
    description: 'Made threes per game from recent rate and minutes.' }),
  prop('nba', { key: 'NBA_PRA', slug: 'pra', name: 'Points + Rebounds + Assists', shortName: 'PRA', statKey: 'pra', family: 'Empirical EWM + Normal', usage: 'minutes',
    description: 'The combined PRA total from recent rate and minutes.' }),
  prop('nba', { key: 'NBA_PR', slug: 'pr', name: 'Points + Rebounds', shortName: 'PR', statKey: 'pr', family: 'Empirical EWM + Normal', usage: 'minutes',
    description: 'Points plus rebounds from recent rate and minutes.' }),
  prop('nba', { key: 'NBA_PA', slug: 'pa', name: 'Points + Assists', shortName: 'PA', statKey: 'pa', family: 'Empirical EWM + Normal', usage: 'minutes',
    description: 'Points plus assists from recent rate and minutes.' }),
  prop('nba', { key: 'NBA_RA', slug: 'ra', name: 'Rebounds + Assists', shortName: 'RA', statKey: 'ra', family: 'Empirical EWM + Normal', usage: 'minutes',
    description: 'Rebounds plus assists from recent rate and minutes.' }),
  prop('nba', { key: 'NBA_STL', slug: 'steals', name: 'Player Steals', shortName: 'Steals', statKey: 'steals', family: 'Empirical EWM + NegBin', usage: 'minutes',
    description: 'Steals per game: a low-count market with a wide spread.' }),
  prop('nba', { key: 'NBA_BLK', slug: 'blocks', name: 'Player Blocks', shortName: 'Blocks', statKey: 'blocks', family: 'Empirical EWM + NegBin', usage: 'minutes',
    description: 'Blocks per game: a low-count market with a wide spread.' }),
]

export const PICK_SYSTEMS: PickSystem[] = [...MLB_SYSTEMS, ...NFL_SYSTEMS, ...NHL_SYSTEMS, ...NBA_SYSTEMS]

export const PICK_SYSTEM_BY_KEY: Record<string, PickSystem> = Object.fromEntries(PICK_SYSTEMS.map(system => [system.key, system]))

/** Slugs repeat across sports ('blocks' is both NHL and NBA), so a slug only means something together with its sport. */
export function getSportSystemBySlug(sport: SportSlug, slug: string): PickSystem | undefined {
  const k = slug.toLowerCase()
  return PICK_SYSTEMS.find(s => s.sport === sport && s.slug === k)
}

/** The systems a sport lists (footer, filter chips, counts): shelved ones keep their URL but are not listed. */
export function getSystemsBySport(sport: SportSlug): PickSystem[] {
  return PICK_SYSTEMS.filter(s => s.sport === sport && !s.shelved)
}

/** Registry system for a projection/props stat key ('rec_yds' -> NFL_REC_YDS). */
export function getSystemByStat(sport: SportSlug, stat: string | null | undefined): PickSystem | undefined {
  const k = (stat ?? '').toLowerCase()
  return PICK_SYSTEMS.find(s => s.sport === sport && s.statKey === k)
}

export function getPickSystemByKey(key: string) {
  return PICK_SYSTEM_BY_KEY[key.toUpperCase()]
}

export function systemPill(system: string) {
  return SYSTEM_PILL[system] ?? SYSTEM_PILL.ALL
}
