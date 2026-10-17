/**
 * Single source of truth for sport pillars shown in the nav, the +EV terminal and the /picks/[sport] pages. Statuses
 * describe what is actually running (see CONTEXT.md s0/s19 and docs/designs/multisport_ev_sweep.md), not what is
 * planned. Adding a sport is a registry change (docs/ADDING_A_NEW_SPORT.md): an entry here, its systems in
 * lib/pick-systems.ts and their badge tokens in lib/tokens.ts -- no new page or component.
 */
/** Every sport slug, in ribbon order. SportSlug and the SPORT_PILLARS record are derived from it, so a slug added here
 * fails to compile until it has a pillar -- and tests/sport-onboarding.test.ts fails until its systems are wired. */
export const SPORT_SLUGS = ['mlb', 'nfl', 'nhl', 'nba'] as const
export type SportSlug = (typeof SPORT_SLUGS)[number]

export type SportStatus = 'live' | 'shadow' | 'data_only' | 'planned'

/** A sport whose board is a projection feed (everything but MLB's tracked card today). */
export type ProjectionSport = Exclude<SportSlug, 'mlb'>

export interface SportMarket {
  key: string
  label: string
  kind: 'prop' | 'game'
  status: string
  /** registry key in lib/pick-systems.ts: the market's /picks/[sport]/[slug] page */
  system?: string
}

export interface SportPillar {
  slug: SportSlug
  name: string
  shortName: string
  seasonBadge: string
  status: SportStatus
  /**
   * Where the sport's picks board comes from. `tracked`: model picks and +EV alerts logged and settled in the bets table
   * (MLB). `projection`: the daily projection board and live market prices (ev/daily_projections.py), unvalidated.
   */
  board: 'tracked' | 'projection'
  accent: string
  accentBg: string
  description: string
  primarySource: string
  markets: SportMarket[]
}

export const SPORT_PILLARS: Record<SportSlug, SportPillar> = {
  mlb: {
    slug: 'mlb',
    name: 'Major League Baseball',
    shortName: 'MLB',
    seasonBadge: '10 MODELS + +EV SCANNER',
    status: 'live',
    board: 'tracked',
    accent: '#57F287',
    accentBg: 'rgba(87, 242, 135, 0.10)',
    description:
      'XGBoost and negative-binomial models scored daily across game and prop markets, plus a 15-minute soft-line +EV scanner. Everything is tracked in paper mode and settled publicly; no system graduates before its 200-bet gate.',
    primarySource: 'Statcast + ParlayAPI + Kalshi',
    markets: [
      { key: 'NRFI', label: '1st Inning O/U (NRFI)', kind: 'game', status: 'Paper', system: 'NRFI' },
      { key: 'HR', label: 'Batter Home Runs (Yes/No)', kind: 'prop', status: 'Paper', system: 'HR' },
      { key: 'K', label: 'Pitcher Strikeouts O/U', kind: 'prop', status: 'Paper', system: 'K' },
      { key: 'OUTS', label: 'Pitcher Outs Recorded', kind: 'prop', status: 'Paper', system: 'OUTS' },
      { key: 'F5', label: 'First 5 Innings ML', kind: 'game', status: 'Paper', system: 'F5' },
      { key: 'GAME', label: 'Full Game Moneyline', kind: 'game', status: 'Log-only', system: 'GAME' },
      { key: 'BATTER_TB', label: 'Batter Total Bases O/U', kind: 'prop', status: 'Paper', system: 'BATTER_TB' },
      { key: 'BATTER_HITS', label: 'Batter Hits O/U', kind: 'prop', status: 'Log-only', system: 'BATTER_HITS' },
      { key: 'SB', label: 'Stolen Bases O/U', kind: 'prop', status: 'Log-only', system: 'SB' },
      { key: 'PITCHER_ER', label: 'Earned Runs O/U', kind: 'prop', status: 'Log-only', system: 'PITCHER_ER' },
    ],
  },
  nfl: {
    slug: 'nfl',
    name: 'National Football League',
    shortName: 'NFL',
    seasonBadge: '10 PROP PROJECTIONS + +EV SCANNER',
    status: 'live',
    board: 'projection',
    accent: '#f59e0b',
    accentBg: 'rgba(245, 158, 11, 0.12)',
    description:
      'A daily board of player-prop projections beside live sportsbook, Kalshi and Novig prices, plus a scanner for prices that beat the consensus. Projections and scanner signals are not yet part of the public results.',
    primarySource: 'nflverse game logs + ParlayAPI books + Kalshi + Novig',
    markets: [
      { key: 'pass_yds', label: 'Passing Yards', kind: 'prop', status: 'Active', system: 'NFL_PASS_YDS' },
      { key: 'pass_tds', label: 'Passing Touchdowns', kind: 'prop', status: 'Active', system: 'NFL_PASS_TDS' },
      { key: 'pass_completions', label: 'Pass Completions', kind: 'prop', status: 'Active', system: 'NFL_PASS_COMP' },
      { key: 'pass_attempts', label: 'Pass Attempts', kind: 'prop', status: 'Active', system: 'NFL_PASS_ATT' },
      { key: 'interceptions', label: 'Interceptions Thrown', kind: 'prop', status: 'Active', system: 'NFL_INT' },
      { key: 'rush_yds', label: 'Rushing Yards', kind: 'prop', status: 'Active', system: 'NFL_RUSH_YDS' },
      { key: 'rush_attempts', label: 'Rushing Attempts', kind: 'prop', status: 'Active', system: 'NFL_RUSH_ATT' },
      { key: 'rec_yds', label: 'Receiving Yards', kind: 'prop', status: 'Active', system: 'NFL_REC_YDS' },
      { key: 'receptions', label: 'Player Receptions O/U', kind: 'prop', status: 'Active', system: 'NFL_RECEPTIONS' },
      { key: 'anytime_td', label: 'Anytime Touchdown', kind: 'prop', status: 'Active', system: 'NFL_ANYTIME_TD' },
    ],
  },
  nba: {
    slug: 'nba',
    name: 'National Basketball Association',
    shortName: 'NBA',
    seasonBadge: '10 PROP PROJECTIONS + +EV SCANNER',
    status: 'live',
    board: 'projection',
    accent: '#818cf8',
    accentBg: 'rgba(129, 140, 248, 0.12)',
    description:
      'A daily board of player-prop projections beside live sportsbook prices, plus a scanner for prices that beat the consensus. Projections and scanner signals are not yet part of the public results.',
    primarySource: 'SportsBlaze box scores + ParlayAPI books',
    markets: [
      { key: 'points', label: 'Player Points O/U', kind: 'prop', status: 'Active', system: 'NBA_PTS' },
      { key: 'rebounds', label: 'Player Rebounds O/U', kind: 'prop', status: 'Active', system: 'NBA_REB' },
      { key: 'assists', label: 'Player Assists O/U', kind: 'prop', status: 'Active', system: 'NBA_AST' },
      { key: 'threes', label: 'Player 3-Pointers O/U', kind: 'prop', status: 'Active', system: 'NBA_3PM' },
      { key: 'pra', label: 'Points + Rebounds + Assists', kind: 'prop', status: 'Active', system: 'NBA_PRA' },
      { key: 'pr', label: 'Points + Rebounds', kind: 'prop', status: 'Active', system: 'NBA_PR' },
      { key: 'pa', label: 'Points + Assists', kind: 'prop', status: 'Active', system: 'NBA_PA' },
      { key: 'ra', label: 'Rebounds + Assists', kind: 'prop', status: 'Active', system: 'NBA_RA' },
      { key: 'steals', label: 'Player Steals O/U', kind: 'prop', status: 'Active', system: 'NBA_STL' },
      { key: 'blocks', label: 'Player Blocks O/U', kind: 'prop', status: 'Active', system: 'NBA_BLK' },
    ],
  },
  nhl: {
    slug: 'nhl',
    name: 'National Hockey League',
    shortName: 'NHL',
    seasonBadge: '6 PROP PROJECTIONS + +EV SCANNER',
    status: 'live',
    board: 'projection',
    accent: '#38bdf8',
    accentBg: 'rgba(56, 189, 248, 0.12)',
    description:
      'A daily board of player-prop projections beside live sportsbook prices, plus a scanner for prices that beat the consensus of the other books. Projections and scanner signals are not yet part of the public results.',
    primarySource: 'NHL API game logs + ParlayAPI books',
    markets: [
      { key: 'sog', label: 'Shots on Goal O/U', kind: 'prop', status: 'Active', system: 'NHL_SOG' },
      { key: 'goals', label: 'Player Goals O/U', kind: 'prop', status: 'Active', system: 'NHL_GOALS' },
      { key: 'assists', label: 'Player Assists O/U', kind: 'prop', status: 'Active', system: 'NHL_ASSISTS' },
      { key: 'points', label: 'Player Points O/U', kind: 'prop', status: 'Active', system: 'NHL_POINTS' },
      // no projection: the skater master has no power-play assists (see lib/pick-systems.ts NHL_PP_POINTS)
      { key: 'pp_points', label: 'Power Play Points O/U', kind: 'prop', status: 'Data gap', system: 'NHL_PP_POINTS' },
      { key: 'saves', label: 'Goalie Saves O/U', kind: 'prop', status: 'Active', system: 'NHL_SAVES' },
      { key: 'blocked_shots', label: 'Blocked Shots O/U', kind: 'prop', status: 'Active', system: 'NHL_BLOCKS' },
    ],
  },
}

export const SPORT_LIST: SportPillar[] = SPORT_SLUGS.map(slug => SPORT_PILLARS[slug])
/** The sports shown in the sport ribbon, the /picks chips and the scanner pills: every pillar whose status is `live`. */
export const LIVE_SPORTS: SportPillar[] = SPORT_LIST.filter(p => p.status === 'live')

const PROP_STAT_LABEL: Record<string, string> = {
  rec_yds: 'Rec Yds', rush_yds: 'Rush Yds', pass_yds: 'Pass Yds', receptions: 'Receptions', pass_tds: 'Pass TDs',
  anytime_td: 'Anytime TD', pass_completions: 'Completions', pass_attempts: 'Pass Attempts', rush_attempts: 'Carries',
  interceptions: 'Interceptions',
  sog: 'Shots on Goal', goals: 'Goals', points: 'Points', assists: 'Assists', saves: 'Saves', pp_points: 'PP Points',
  blocked_shots: 'Blocked Shots', hits: 'Hits', pim: 'Penalty Min',
  rebounds: 'Rebounds', threes: '3-Pointers', pra: 'Pts+Reb+Ast', pr: 'Pts+Reb', pa: 'Pts+Ast', ra: 'Reb+Ast',
  steals: 'Steals', blocks: 'Blocks', turnovers: 'Turnovers', stocks: 'Stl+Blk', minutes: 'Minutes',
  home_runs: 'Home Runs', strikeouts: 'Strikeouts', outs: 'Pitcher Outs', total_bases: 'Total Bases',
  earned_runs: 'Earned Runs', stolen_bases: 'Stolen Bases',
}

/** Display label for a props-sweep `stat` key ('sog' -> 'Shots on Goal'); unknown keys degrade to 'some stat'. */
export function propStatLabel(stat: string | null | undefined): string {
  const k = (stat ?? '').toLowerCase()
  return PROP_STAT_LABEL[k] ?? k.replace(/_/g, ' ')
}

export function isSportSlug(s: string): s is SportSlug {
  return (SPORT_SLUGS as readonly string[]).includes(s)
}

export const STATUS_LABEL: Record<SportStatus, string> = {
  live: 'LIVE',
  shadow: 'PREVIEW',
  data_only: 'COMING SOON',
  planned: 'COMING SOON',
}
