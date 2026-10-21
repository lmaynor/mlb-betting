/**
 * The NBA games board: our model's line next to the market consensus (backend: nba/games/board.py, GET /api/public/nba-games).
 * Informational and UNVALIDATED. The payload carries no edge / EV fields on purpose: in the 2021-25 walk-forward the market
 * line was more accurate than the model on moneyline, spread and totals, so nothing here is presented as a bet.
 */
export interface NbaLadderPoint { line: number; p: number | null; fair_american: number | null }

export interface NbaGame {
  game_key: string
  date: string
  /** tip, ISO UTC */
  start: string | null
  home: string
  away: string
  home_name: string | null
  away_name: string | null
  n_books: number
  games_min: number | null
  /** false until both teams have played enough games for the ratings to mean anything */
  reliable: boolean
  market: {
    spread_home: number | null; total: number | null; home_win_p: number | null
    n_books_spread: number; n_books_total: number; pinnacle_home_win_p: number | null
  }
  model: { margin: number; spread_home: number; total: number; home_win_p: number; sigma_margin: number; sigma_total: number }
  diff: { margin_pts: number | null; total_pts: number | null; home_win_pp: number | null }
  ladders: { spread_home: NbaLadderPoint[]; total: NbaLadderPoint[] }
}

export interface NbaGamesBoard {
  sport: 'nba'
  engine: string | null
  validated: false
  status: 'ok' | 'no_games' | 'unavailable'
  stale: boolean
  as_of: string | null
  lines_as_of: string | null
  age_minutes: number | null
  lines_age_minutes: number | null
  count: number
  reliable_count: number
  label: string | null
  accuracy: {
    games: number; seasons: string
    moneyline_logloss: { market: number; model: number }
    spread_rmse: { market: number; model: number }
    total_rmse: { market: number; model: number }
    verdict: string
  } | null
  games: NbaGame[]
}

/** Games that must not be highlighted or counted as a real disagreement. */
export const READY_AFTER_GAMES = 15

export const signedPts = (x: number | null | undefined, d = 1) => (x == null ? '--' : `${x > 0 ? '+' : ''}${x.toFixed(d)}`)
export const lineText = (x: number | null | undefined) => (x == null ? '--' : x > 0 ? `+${x}` : `${x}`)
export const pctText = (x: number | null | undefined) => (x == null ? '--' : `${Math.round(x * 100)}%`)
export const americanText = (x: number | null | undefined) => (x == null ? '--' : x > 0 ? `+${x}` : `${x}`)

/** Tip time on the site clock (Central), e.g. "Tue 7:30 PM". */
export function tipText(iso: string | null): string {
  if (!iso) return '--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--'
  return new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(d)
}
