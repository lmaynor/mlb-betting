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
  navBadge: string
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
    seasonBadge: 'MODELS + +EV SCANNER',
    navBadge: '10 Models',
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
    seasonBadge: 'SHADOW SWEEP',
    navBadge: 'Props Sweep',
    status: 'shadow',
    accent: '#f59e0b',
    accentBg: 'rgba(245, 158, 11, 0.12)',
    description:
      'Player-prop consensus from sportsbooks (ParlayAPI) joined to Kalshi and Novig order books. Evidence collection only: no orders are placed, and these signals are not validated.',
    primarySource: 'ParlayAPI books + Kalshi + Novig',
    markets: [
      { key: 'rec_yds', label: 'Receiving Yards Ladder', kind: 'prop', status: 'Shadow' },
      { key: 'rush_yds', label: 'Rushing Yards Ladder', kind: 'prop', status: 'Shadow' },
      { key: 'pass_yds', label: 'Passing Yards Ladder', kind: 'prop', status: 'Shadow' },
      { key: 'receptions', label: 'Player Receptions O/U', kind: 'prop', status: 'Shadow' },
      { key: 'pass_tds', label: 'Passing Touchdowns', kind: 'prop', status: 'Shadow' },
    ],
  },
  nba: {
    slug: 'nba',
    name: 'National Basketball Association',
    shortName: 'NBA',
    seasonBadge: 'DATA ONLY',
    navBadge: 'Data Only',
    status: 'data_only',
    accent: '#818cf8',
    accentBg: 'rgba(129, 140, 248, 0.12)',
    description:
      'Box-score history is ingested nightly (315k+ player rows across seven seasons). There is no odds feed, model or betting runner yet.',
    primarySource: 'SportsBlaze box scores',
    markets: [
      { key: 'pts', label: 'Player Points O/U', kind: 'prop', status: 'Planned' },
      { key: 'reb', label: 'Player Rebounds O/U', kind: 'prop', status: 'Planned' },
      { key: 'ast', label: 'Player Assists O/U', kind: 'prop', status: 'Planned' },
      { key: 'pra', label: 'Points + Rebounds + Assists', kind: 'prop', status: 'Planned' },
    ],
  },
  nhl: {
    slug: 'nhl',
    name: 'National Hockey League',
    shortName: 'NHL',
    seasonBadge: 'PLANNED',
    navBadge: 'Planned',
    status: 'planned',
    accent: '#38bdf8',
    accentBg: 'rgba(56, 189, 248, 0.12)',
    description:
      'Market adapters for Kalshi hockey contracts exist, but no sweep is running and nothing is tracked yet.',
    primarySource: 'ParlayAPI + Kalshi (adapter only)',
    markets: [
      { key: 'goals', label: 'Anytime Goal Scorer', kind: 'prop', status: 'Planned' },
      { key: 'sog', label: 'Shots on Goal O/U', kind: 'prop', status: 'Planned' },
      { key: 'game_ml', label: 'Full Game Moneyline', kind: 'game', status: 'Planned' },
    ],
  },
}

export const SPORT_LIST: SportPillar[] = Object.values(SPORT_PILLARS)

export function isSportSlug(s: string): s is SportSlug {
  return s in SPORT_PILLARS
}

export const STATUS_LABEL: Record<SportStatus, string> = {
  live: 'LIVE',
  shadow: 'SHADOW',
  data_only: 'DATA ONLY',
  planned: 'PLANNED',
}
