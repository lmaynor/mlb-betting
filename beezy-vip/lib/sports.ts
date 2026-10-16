/**
 * Single source of truth for sport pillars shown in the nav, the +EV terminal
 * and the /sports/[sport] hubs. Statuses describe what is actually running
 * (see CONTEXT.md s0/s19 and docs/designs/multisport_ev_sweep.md), not
 * what is planned.
 */
export type SportSlug = 'mlb' | 'nfl' | 'nba' | 'nhl'

export type SportStatus = 'live' | 'shadow' | 'data_only' | 'planned'

export interface SportMarket {
  key: string
  label: string
  kind: 'prop' | 'game'
  status: string
  /** registry key in lib/pick-systems.ts, when the market has a /picks/mlb/[slug] page */
  system?: string
}

export interface SportPillar {
  slug: SportSlug
  name: string
  shortName: string
  seasonBadge: string
  status: SportStatus
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
    seasonBadge: '6 PROP PROJECTIONS + +EV SCANNER',
    status: 'live',
    accent: '#f59e0b',
    accentBg: 'rgba(245, 158, 11, 0.12)',
    description:
      'A daily board of player-prop projections beside live sportsbook, Kalshi and Novig prices, plus a scanner for prices that beat the consensus. Projections and scanner signals are not yet part of the public results.',
    primarySource: 'nflverse game logs + ParlayAPI books + Kalshi + Novig',
    markets: [
      { key: 'rec_yds', label: 'Receiving Yards', kind: 'prop', status: 'Active' },
      { key: 'rush_yds', label: 'Rushing Yards', kind: 'prop', status: 'Active' },
      { key: 'pass_yds', label: 'Passing Yards', kind: 'prop', status: 'Active' },
      { key: 'receptions', label: 'Player Receptions O/U', kind: 'prop', status: 'Active' },
      { key: 'pass_tds', label: 'Passing Touchdowns', kind: 'prop', status: 'Active' },
      { key: 'anytime_td', label: 'Anytime Touchdown', kind: 'prop', status: 'Active' },
    ],
  },
  nba: {
    slug: 'nba',
    name: 'National Basketball Association',
    shortName: 'NBA',
    seasonBadge: '5 PROP PROJECTIONS + +EV SCANNER',
    status: 'live',
    accent: '#818cf8',
    accentBg: 'rgba(129, 140, 248, 0.12)',
    description:
      'A daily board of player-prop projections beside live sportsbook prices, plus a scanner for prices that beat the consensus. Projections and scanner signals are not yet part of the public results.',
    primarySource: 'SportsBlaze box scores + ParlayAPI books',
    markets: [
      { key: 'pts', label: 'Player Points O/U', kind: 'prop', status: 'Active' },
      { key: 'reb', label: 'Player Rebounds O/U', kind: 'prop', status: 'Active' },
      { key: 'ast', label: 'Player Assists O/U', kind: 'prop', status: 'Active' },
      { key: 'threes', label: 'Player 3-Pointers O/U', kind: 'prop', status: 'Active' },
      { key: 'pra', label: 'Points + Rebounds + Assists', kind: 'prop', status: 'Active' },
    ],
  },
  nhl: {
    slug: 'nhl',
    name: 'National Hockey League',
    shortName: 'NHL',
    seasonBadge: '5 PROP PROJECTIONS + +EV SCANNER',
    status: 'live',
    accent: '#38bdf8',
    accentBg: 'rgba(56, 189, 248, 0.12)',
    description:
      'A daily board of player-prop projections beside live sportsbook prices, plus a scanner for prices that beat the consensus of the other books. Projections and scanner signals are not yet part of the public results.',
    primarySource: 'NHL API game logs + ParlayAPI books',
    markets: [
      { key: 'sog', label: 'Shots on Goal O/U', kind: 'prop', status: 'Active' },
      { key: 'goals', label: 'Player Goals O/U', kind: 'prop', status: 'Active' },
      { key: 'points', label: 'Player Points O/U', kind: 'prop', status: 'Active' },
      { key: 'assists', label: 'Player Assists O/U', kind: 'prop', status: 'Active' },
      { key: 'saves', label: 'Goalie Saves O/U', kind: 'prop', status: 'Active' },
    ],
  },
}

export const SPORT_LIST: SportPillar[] = Object.values(SPORT_PILLARS)

const PROP_STAT_LABEL: Record<string, string> = {
  rec_yds: 'Rec Yds', rush_yds: 'Rush Yds', pass_yds: 'Pass Yds', receptions: 'Receptions', pass_tds: 'Pass TDs',
  anytime_td: 'Anytime TD',
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
  return s in SPORT_PILLARS
}

export const STATUS_LABEL: Record<SportStatus, string> = {
  live: 'LIVE',
  shadow: 'PREVIEW',
  data_only: 'COMING SOON',
  planned: 'COMING SOON',
}
