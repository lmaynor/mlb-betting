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

// CLV Scatter tool
export interface CLVDataPoint {
  system:         string
  game_date:      string
  bet_type:       string
  player:         string | null
  away_team:      string | null
  home_team:      string | null
  model_edge_pct: number   // edge * 100, e.g. 8.2 for 8.2% edge
  clv_pct:        number   // (entry_fair - closing_fair) / closing_fair * 100
  result:         'win' | 'loss' | 'push'
  opening_odds:   number
  closing_odds:   number
}

// Slate Command Center
export interface SlatePick {
  game_pk:         number
  system:          string
  bet_type:        string
  model_prob_pct:  number
  market_prob_pct: number
  edge_pct:        number
  odds:            number
  result:          string | null
  notes:           string | null
  player:          string | null
  away_team:       string | null
  home_team:       string | null
}

export interface SlateGame {
  game_pk:      number
  away_team:    string
  home_team:    string
  start_time:   string | null
  away_pitcher: string | null
  home_pitcher: string | null
  picks:        SlatePick[]
}

export interface TodaySlate {
  games:        SlateGame[]
  run_date:     string
  as_of:        string
  total_picks:  number
  total_games:  number
}
