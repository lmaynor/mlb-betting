import type { ProjectionSport } from '@/lib/sports'
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
  /** the model's expected value for the player stat, in the market's unit (Ks, outs, TB, hits, SB, ER); null for HR and game markets */
  proj_mean?:      number | null
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
/** One book's quote on the same player / stat / line (line shopping). over/under are American odds; fair is the
 * de-vigged P(over) as a 0-1 fraction at that book's own two-sided quote. */
export interface EvBookQuote {
  book:  string
  over:  number | null
  under: number | null
  fair:  number | null
}

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
  all_books?:    EvBookQuote[]    // every book quoting this line (optional; drives the Compare books drawer)
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
  /** live rows withheld by the access policy (lib/access-feeds.ts); absent when nothing was cut */
  locked?: number
}

// -- Daily projection board (GET /api/public/projections; ev/daily_projections.py) ----------------------------
/** One player x stat. `has_line: false` rows are rotation projections with no market yet (line/side fields are null). */
export interface SportProjectionRow {
  sport: ProjectionSport
  stat: string
  stat_label: string
  player: string
  player_norm: string
  team: string | null
  opp: string | null
  game_key: string
  commence: string | null
  mins_to_start: number | null
  has_line: boolean
  line: number | null
  consensus_line: number | null
  /** the side the model prefers vs the market ('over' | 'under'); null when there is no model probability */
  side: 'over' | 'under' | null
  proj_mean: number | null
  p_over_model: number | null
  /** market-anchored posterior (display only, unvalidated prior; ev/daily_projections.py blend_p_over) */
  p_over_blend?: number | null
  p_model_side: number | null
  p_mkt_side: number | null
  l5_hit_rate: number | null
  l10_hit_rate: number | null
  szn_rate: number | null
  szn_avg: number | null
  avg_l10: number | null
  usage_label: string
  n_games: number
  best_over_odds: number | null
  best_over_book: string | null
  best_under_odds: number | null
  best_under_book: string | null
  p_over_market: number | null
  n_books: number | null
  model_edge: number | null
  market_ev: number | null
  confluence: boolean
  edge_capped: boolean
  beezy_score: number
  score_basis: 'confluence' | 'market' | 'model' | 'capped' | 'none'
  validated: false
  engine: string
  /** resolved server-side by name (NHL / NBA only); null when unknown */
  headshot_id?: number | null
}

export interface SportProjections {
  sport: ProjectionSport
  engine: string | null
  validated: false
  status: 'live' | 'awaiting_lines' | 'idle' | 'unavailable'
  stale: boolean
  as_of: string | null
  age_minutes: number | null
  lines_age_minutes: number | null
  gamelog_latest: string | null
  stale_gamelog: boolean | null
  stats: string[]
  count: number
  rows: SportProjectionRow[]
  summary: Record<string, unknown>
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

/** /edge cockpit: one game of the player's log against the prop line. `hit` = cleared the line (val > line), whichever
 * side the flagged quote is on; null when the line is unknown. */
export interface CockpitGame {
  date:  string | null
  opp:   string | null
  val:   number | null
  usage: number | null
  hit:   boolean | null
}

/** Last-10 game log vs the prop line (mlb_core/data/cockpit_form.py). Rates are 0-1 fractions of games that cleared the line. */
export interface CockpitForm {
  line:        number | null
  n_games:     number
  games:       CockpitGame[]
  l5_rate:     number | null
  l10_rate:    number | null
  szn_rate:    number | null
  szn_avg:     number | null
  szn_games:   number
  usage_label: string | null     // 'Targets' | 'Carries' | 'Pass attempts' | 'TOI (min)' | 'Shots faced' | 'Minutes'
  avg_val:     number | null
  avg_usage:   number | null
}

/** One prop candidate in the cockpit (mlb/runners/public_api.py::get_multisport_cockpit). */
export interface CockpitRow {
  id:           string
  sport:        'mlb' | 'nfl' | 'nhl' | 'nba'
  source:       'model' | 'ev_alert' | 'sink_a' | 'kalshi'
  shadow:       boolean          // sweep signal: evidence only, no stake sizing or Beezy score
  player:       string | null
  player_id:    number | string | null
  team:         string | null
  matchup:      string | null
  commence:     string | null
  mins_to_kick: number | null
  stat:         string | null
  side:         'OVER' | 'UNDER' | string
  line:         number | null
  book:         string | null
  american:     number | null
  decimal:      number | null
  ev:           number | null    // fraction at the quote's own price
  p_fair:       number | null    // consensus fair probability of the flagged side
  n_books:      number | null
  flag:         boolean          // passed the credibility gates
  reason:       string | null
  all_books:    EvBookQuote[] | null
  recentForm:   CockpitForm | null
  // MLB model rows only
  system?:      string
  bet_type?:    string
  model_prob?:  number | null
  spray?:       { x: number; y: number; hit: boolean; ev?: number | null }[] | null
  ev_la?:       { ev: number; la: number; hit: boolean }[] | null
}

export interface CockpitSportMeta {
  status:      string            // 'live' | 'idle' | 'shadow_live' | 'unavailable'
  count:       number
  as_of:       string | null
  age_minutes: number | null
  stale:       boolean
  validated:   boolean
}

export interface CockpitFeed {
  as_of:  string
  sport:  string
  min_ev: number
  rows:   CockpitRow[]
  sports: Partial<Record<'mlb' | 'nfl' | 'nhl' | 'nba', CockpitSportMeta>>
  /** rows withheld by the access policy (lib/access-feeds.ts); absent when nothing was cut */
  locked?: number
}
