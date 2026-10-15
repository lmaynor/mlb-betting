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

// -- +EV terminal feed (GET /api/public/ev/feed) ------------------------------
export interface EvMlbRow {
  sport:       'mlb'
  id:          number
  game_date:   string
  game_pk:     number | null
  player:      string | null
  away_team:   string | null
  home_team:   string | null
  bet_type:    string            // underlying market, book suffix already stripped
  model_prob:  number | null     // consensus fair probability
  market_prob: number | null
  edge:        number | null     // net EV, fraction
  kelly_pct:   number | null
  odds:        number
  stake:       number
  book:        string | null
  result:      'win' | 'loss' | 'push' | 'void' | null
  profit:      number | null
  notes:       string | null
  n_books:     number | null
  anchored:    boolean           // priced against the Pinnacle anchor
  created_at:  string
}

export interface EvNflRow {
  sport:        'nfl'
  venue:        string           // exchange the ask was read from
  ticker:       string | null
  game_key:     string | null
  stat:         string | null    // pass_yds | rush_yds | rec_yds | receptions | pass_tds
  player:       string | null
  team:         string | null
  strike:       number | null
  side:         'OVER' | 'UNDER'
  ask:          number           // dollars per $1 contract
  net_ev:       number           // net of exchange fee, fraction
  fair_prob:    number | null
  n_books:      number | null
  books:        string | null
  disp:         number | null
  spread:       number | null
  fee_mult:     number | null
  commence:     string | null
  mins_to_kick: number
  flag:         boolean
  reason:       string | null
}

/**
 * Sink A: a sportsbook / exchange quote beating the leave-self-out consensus of the OTHER books. The one row shape for
 * NFL `book_outliers` and the NHL/NBA props blocks (mlb/runners/public_api.py::_outlier_row). SHADOW / unvalidated:
 * never render a stake or a Beezy score for these.
 */
export interface EvBookOutlierRow {
  sport?:        'nfl' | 'nhl' | 'nba'
  venue?:        string
  game_key:      string | null
  commence?:     string | null
  mins_to_kick?: number | null
  book:          string
  player:        string
  player_norm?:  string
  player_id?:    number | string | null
  team?:         string | null
  away_team?:    string | null
  home_team?:    string | null
  stat?:         string
  market?:       string
  point?:        number
  line?:         number
  side:          'OVER' | 'UNDER' | string
  american:      number
  decimal:       number
  p_fair:        number | null
  ev:            number           // fraction, at the quote's own price
  n_books:       number           // reference books (leave-self-out)
  disp?:         number | null
  flag?:         boolean          // passed the sweep's credibility gates (EV cap, >= min reference books)
  reason?:       string | null
}

export interface EvNflBlock {
  rows:          EvNflRow[]       // Kalshi order-book rows (empty when Kalshi blocks the cloud egress IP)
  book_outliers?: EvBookOutlierRow[]
  count:         number
  status:        'shadow_live' | 'idle' | 'unavailable'
  as_of:         string | null
  age_minutes:   number | null
  stale:         boolean
  summary:       Record<string, unknown>
  validated:     boolean
}

/** NHL / NBA props sweep block (ev/sweep_props_alerts.py). Same freshness contract as EvNflBlock. */
export interface EvPropsBlock {
  rows:        EvBookOutlierRow[]
  count:       number
  status:      'shadow_live' | 'idle' | 'unavailable'
  as_of:       string | null
  age_minutes: number | null
  stale:       boolean
  summary:     Record<string, unknown>
  validated:   boolean
}

export interface EvFeed {
  sport:  string
  min_ev: number
  as_of:  string
  mlb:    { rows: EvMlbRow[]; count: number }
  nfl:    EvNflBlock | null
  nhl?:   EvPropsBlock | null
  nba?:   EvPropsBlock | null
}

// -- Analytics deep dive (GET /api/public/analytics/deep-dive) -----------------
export interface EdgeBucket {
  bucket: string; n: number; wins: number; losses: number
  hit_rate: number | null; avg_model_prob: number | null; avg_market_prob: number | null
  roi_pct: number | null; mean_clv: number | null
}
export interface BookRow {
  book: string; n: number; wins: number; hit_rate: number | null
  roi_pct: number | null; profit_units: number; mean_clv: number | null; avg_edge: number | null
}
export interface BankrollPoint {
  date: string
  ev_flat_pnl: number; ev_kelly_pnl: number; models_pnl: number
  ev_flat_cum: number; ev_kelly_cum: number; models_cum: number
}
export interface EvHeadline {
  logged: number; graded: number; wins: number; losses: number; pushes: number; voids: number; pending: number
  first_date: string | null; last_date: string | null
  flat_stake_unit: number; flat_roi_pct: number | null; flat_pnl_dollars: number
  bankroll: number; kelly_mult: number; kelly_cap: number
  kelly_roi_pct: number | null; kelly_pnl_dollars: number; kelly_ending_bankroll: number
  unique_props_graded: number; voids_excluded_from_roi: boolean
}
export interface AnalyticsDeepDive {
  edge_buckets: EdgeBucket[]
  book_leaderboard: BookRow[]
  bankroll_curve: BankrollPoint[]
  ev_headline: EvHeadline
  system: string | null
}
