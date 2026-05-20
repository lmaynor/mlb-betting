/**
 * Shared TypeScript interfaces matching the production `bets` table schema.
 * These are the canonical type definitions -- import from here not from lib/db.
 * Column names must match what Cloud Run public API returns exactly.
 */

export interface Bet {
  id:              number
  system:          string
  game_date:       string
  game_pk:         number
  bet_type:        string
  player:          string | null
  away_team:       string | null
  home_team:       string | null
  odds:            number
  stake:           number
  model_prob:      number
  market_prob:     number
  edge:            number | null
  kelly_pct:       number | null
  kelly_triggered: boolean
  result:          'win' | 'loss' | 'push' | 'void' | null
  profit:          number | null
  paper:           boolean | null
  book:            string | null
  notes:           string | null
  created_at:      string
}

export interface SystemStats {
  system:     string
  total_bets: number
  wins:       number
  losses:     number
  pushes:     number
  win_rate:   number
  roi:        number
  total_pnl:  number
  avg_edge:   number
}
