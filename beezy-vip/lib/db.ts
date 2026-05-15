import { Pool } from 'pg'

// Singleton pool -- reused across hot reloads in dev
const globalPool = global as typeof global & { _pgPool?: Pool }

export function getPool(): Pool {
  if (!globalPool._pgPool) {
    globalPool._pgPool = new Pool({
      connectionString: process.env.DATABASE_URL,
      ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: false } : false,
      max: 10,
      idleTimeoutMillis: 30_000,
    })
  }
  return globalPool._pgPool
}

export async function query<T = Record<string, unknown>>(
  sql: string,
  params?: unknown[]
): Promise<T[]> {
  const pool = getPool()
  const result = await pool.query(sql, params)
  return result.rows as T[]
}

// -- Bet type --
// Column names match the production bets table in mlb-betting.
// result values: 'win' | 'loss' | 'push' | 'void' | null (pending)
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
  notes:           string | null
  created_at:      string
}

// -- Aggregate stats --
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

// -- Common queries --

export async function getTodayPicks(): Promise<Bet[]> {
  return query<Bet>(`
    SELECT * FROM bets
    WHERE game_date = CURRENT_DATE
      AND kelly_triggered = true
    ORDER BY system, created_at DESC
  `)
}

export async function getRecentSettled(limit = 20): Promise<Bet[]> {
  return query<Bet>(`
    SELECT * FROM bets
    WHERE result IS NOT NULL
    ORDER BY game_date DESC, created_at DESC
    LIMIT $1
  `, [limit])
}

export async function getSummaryStats(): Promise<SystemStats[]> {
  return query<SystemStats>(`
    SELECT
      system,
      COUNT(*)                                               AS total_bets,
      COUNT(*) FILTER (WHERE result = 'win')                 AS wins,
      COUNT(*) FILTER (WHERE result = 'loss')                AS losses,
      COUNT(*) FILTER (WHERE result = 'push')                AS pushes,
      ROUND(
        COUNT(*) FILTER (WHERE result = 'win')::numeric /
        NULLIF(COUNT(*) FILTER (WHERE result IN ('win','loss')), 0) * 100, 1
      )                                                      AS win_rate,
      ROUND(
        SUM(profit) / NULLIF(SUM(stake), 0) * 100, 2
      )                                                      AS roi,
      ROUND(SUM(profit), 2)                                  AS total_pnl,
      ROUND(AVG(model_prob - market_prob) * 100, 2)          AS avg_edge
    FROM bets
    WHERE result IS NOT NULL
      AND result != 'void'
    GROUP BY system
    ORDER BY roi DESC
  `)
}

export async function getOverallStats() {
  const rows = await query<{
    total_bets: string
    win_rate:   string
    roi:        string
    avg_edge:   string
  }>(`
    SELECT
      COUNT(*)                                               AS total_bets,
      ROUND(
        COUNT(*) FILTER (WHERE result = 'win')::numeric /
        NULLIF(COUNT(*) FILTER (WHERE result IN ('win','loss')), 0) * 100, 1
      )                                                      AS win_rate,
      ROUND(SUM(profit) / NULLIF(SUM(stake), 0) * 100, 2)   AS roi,
      ROUND(AVG(model_prob - market_prob) * 100, 2)          AS avg_edge
    FROM bets
    WHERE result IS NOT NULL
      AND result != 'void'
  `)
  return rows[0] ?? { total_bets: '0', win_rate: '0', roi: '0', avg_edge: '0' }
}

export async function getPicks(params: {
  system?:  string
  date?:    string
  status?:  string
  limit?:   number
  offset?:  number
}): Promise<Bet[]> {
  const conditions: string[] = []
  const values:     unknown[] = []
  let   idx = 1

  if (params.system) { conditions.push(`system = $${idx++}`); values.push(params.system) }

  if (params.date === 'today') {
    conditions.push(`game_date = CURRENT_DATE`)
  } else if (params.date === 'yesterday') {
    conditions.push(`game_date = CURRENT_DATE - INTERVAL '1 day'`)
  } else if (params.date === 'last7') {
    conditions.push(`game_date >= CURRENT_DATE - INTERVAL '7 days'`)
  } else if (params.date) {
    conditions.push(`game_date = $${idx++}`)
    values.push(params.date)
  }

  if (params.status === 'pending')  conditions.push(`result IS NULL`)
  if (params.status === 'settled')  conditions.push(`result IS NOT NULL`)
  if (params.status === 'won')      conditions.push(`result = 'win'`)
  if (params.status === 'lost')     conditions.push(`result = 'loss'`)

  const where  = conditions.length ? `WHERE ${conditions.join(' AND ')}` : ''
  const limit  = params.limit  ?? 50
  const offset = params.offset ?? 0

  values.push(limit, offset)

  return query<Bet>(`
    SELECT * FROM bets
    ${where}
    ORDER BY game_date DESC, created_at DESC
    LIMIT $${idx++} OFFSET $${idx++}
  `, values)
}
