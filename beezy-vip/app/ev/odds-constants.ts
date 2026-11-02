import { type SportSlug } from '@/lib/sports'
import { type OddsScreenMode } from '@/lib/odds-screen'

export type SportFilter = 'all' | SportSlug
export type SortKey = 'ev' | 'time' | 'player'
export type LinesFilter = 'main' | 'all'
export type ArbView = 'live' | 'near'
export type OutlierView = 'cards' | 'table'
export type OutlierOddsBand = 'all' | 'main' | 'plus_money' | 'longshots'

export const HAIR = 'var(--color-separator-border)'
export const GREEN = '#57F287'
export const SKY = '#38bdf8'
export const VIOLET = '#a78bfa'
export const SPORT_KEYS: SportSlug[] = ['nfl', 'nhl', 'nba', 'mlb']
// Sharp-first column order; any book not listed follows by how many lines it quotes.
export const BOOK_ORDER = ['pinnacle', 'novig', 'prophetx', 'kalshi', 'draftkings', 'fanduel', 'betmgm', 'caesars', 'espnbet', 'thescore', 'fanatics', 'hardrock', 'betrivers', 'bet365']
export const SHARP_PRESET = ['pinnacle', 'novig', 'prophetx']
export const MIN_EV = [
  { label: 'All', v: -1 },
  { label: '0%+', v: 0 },
  { label: '1%+', v: 0.01 },
  { label: '2%+', v: 0.02 },
  { label: '3%+', v: 0.03 },
  { label: '5%+', v: 0.05 },
]
export const ODDS_MIN = [
  { label: 'All Alts', v: 0 },
  { label: '+200+', v: 200 },
  { label: '+350+', v: 350 },
  { label: '+500+', v: 500 },
]
export const PAGE = 50            // rows rendered per step; "Show more" adds another PAGE (keeps the DOM small)
export const MIN_REF = 3          // reference books needed behind a fair value on the Standard view
export const LONGSHOT_MIN_REF = 2 // ... on the Alt & Longshots view, where a line is rarely quoted by many books
export const CELL_EDGE = 0.015    // a book cell is tinted when its price beats the row's fair value by this much EV
export const NEAR_ARB_PCT = -1.5
export const MID_COST = [2, 5, 8]                       // the most a missed middle may cost, percent of the outlay
export const MID_GAP = [{ label: 'ANY', v: 0 }, { label: '2+', v: 2 }, { label: '3+', v: 3 }, { label: '5+', v: 5 }]
export const MIDDLE_MIN_REF = 2

export const OUTLIER_GAP_OPTIONS = [
  { label: '+8%+', v: 8 },
  { label: '+15%+', v: 15 },
  { label: '+25%+', v: 25 },
  { label: '+40%+', v: 40 },
]
export const OUTLIER_ODDS_OPTIONS: { label: string; v: OutlierOddsBand }[] = [
  { label: 'All Odds', v: 'all' },
  { label: 'Main Lines (<+200)', v: 'main' },
  { label: 'Plus Money (+100+)', v: 'plus_money' },
  { label: 'Tail (+250+)', v: 'longshots' },
]
export const OUTLIER_MIN_BOOKS = [
  { label: '3+ Books', v: 3 },
  { label: '4+ Books', v: 4 },
  { label: '5+ Books', v: 5 },
]

// The banker rewrites the boards every 30 minutes, so a faster poll only re-downloads identical data.
export const POLL_MS = 3 * 60_000

export const MODES: { key: OddsScreenMode; label: string }[] = [
  { key: 'ev', label: '+EV Scanner' },
  { key: 'outliers', label: 'Market Outliers' },
  { key: 'arb', label: 'Arbitrage' },
  { key: 'middles', label: 'Middles' },
  { key: 'longshots', label: 'Alt & Tail Ladders' },
  { key: 'sgp', label: 'SGP Proxies' },
]

export type BadgeKey = 'ev' | 'outliers' | 'arb' | 'middles' | 'longshots' | 'sgp'
export const BADGE_KEYS: BadgeKey[] = ['ev', 'outliers', 'arb', 'middles', 'longshots', 'sgp']

export const SGP_ARCHETYPE_OPTIONS = [
  { v: 'all', label: 'All Combos' },
  { v: 'stacks', label: 'Teammate Stacks (NHL Linemates / QB+WR / PG+Scorer)' },
  { v: 'milestones', label: 'Milestones (10+ Reb and 10+ Ast)' },
  { v: 'combos', label: 'Stat Combos (P+R / P+A / R+A / G+A)' },
]

export const SGP_MIN_EV_OPTIONS = [
  { v: 0, label: 'All' },
  { v: 2, label: '+2%+' },
  { v: 5, label: '+5%+' },
  { v: 10, label: '+10%+' },
]
