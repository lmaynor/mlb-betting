// Client for the mlb-betting Cloud Run public API.
// Replaces direct Cloud SQL access for all bets-table queries.
// The Cloud Run service handles auth and Cloud SQL via its own binding.

import type { Bet, SystemStats, CLVDataPoint, TodaySlate } from '@/lib/types'
import { siteDateKey } from '@/lib/dates'

function normalizeApiUrl(value: string) {
  const trimmed = value.trim().replace(/\/+$/, '')
  if (!trimmed) return ''
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`
}

const API_URL = normalizeApiUrl(process.env.BETTING_API_URL ?? '')
const API_KEY = process.env.BETTING_API_KEY ?? ''

async function apiFetch<T>(path: string, cacheSecs = 60): Promise<T> {
  // In browser (client components), use the Next.js proxy route to avoid
  // exposing BETTING_API_URL and BETTING_API_KEY to the client bundle.
  const isBrowser = typeof window !== 'undefined'
  const url = isBrowser
    ? (path.includes('/stats/') ? '/api/stats' : path.replace('/api/public/', '/api/'))
    : `${API_URL}${path}`
  if (!isBrowser && !API_URL) throw new Error('BETTING_API_URL is not set')
  const headers: Record<string, string> = isBrowser
    ? {}
    : { 'X-API-Key': API_KEY }
  const res = await fetch(url, {
    headers,
    next: isBrowser ? undefined : { revalidate: cacheSecs },
  })
  if (!res.ok) {
    throw new Error(`Betting API error ${res.status} on ${url}`)
  }
  return res.json() as Promise<T>
}

// -- API response shapes (must match what Cloud Run returns) --

export interface PicksResponse {
  picks: Bet[]
  count: number
}

export interface StatsResponse {
  overall:  {
    total_bets: string
    win_rate:   string
    roi:        string
    avg_edge:   string
  }
  bySystem: SystemStats[]
}

// -- Public fetchers --

export async function apiGetTodayPicks(): Promise<Bet[]> {
  const data = await apiFetch<PicksResponse>(`/api/public/picks?date=${siteDateKey()}`, 60)
  return data.picks
}

export async function apiGetRecentSettled(limit = 20): Promise<Bet[]> {
  const data = await apiFetch<PicksResponse>(`/api/public/picks/recent?limit=${limit}`, 120)
  return data.picks
}

export interface SparklinePoint {
  date:      string
  daily_pnl: number
  cum_pnl:   number
}

export async function apiGetSparkline(days = 30): Promise<SparklinePoint[]> {
  const data = await apiFetch<{ sparkline: SparklinePoint[] }>(
    `/api/public/stats/sparkline?days=${days}`, 300
  )
  return data.sparkline ?? []
}

export async function apiGetSparklineBySystem(system: string, days = 30): Promise<SparklinePoint[]> {
  const data = await apiFetch<{ sparkline: SparklinePoint[] }>(
    `/api/public/stats/sparkline?days=${days}&system=${system}`, 300
  )
  return data.sparkline ?? []
}

export async function apiGetStats(): Promise<StatsResponse> {
  return apiFetch<StatsResponse>('/api/public/stats/summary', 300)
}

export async function apiGetPicks(params: {
  system?:  string
  date?:    string
  status?:  string
  limit?:   number
  offset?:  number
  book?:    string
}): Promise<Bet[]> {
  const sp = new URLSearchParams()
  if (params.system) sp.set('system',  params.system)
  if (params.date)   sp.set('date',    params.date)
  if (params.status) sp.set('status',  params.status)
  if (params.limit)  sp.set('limit',   String(params.limit))
  if (params.offset) sp.set('offset',  String(params.offset))
  if (params.book)   sp.set('book',    params.book)
  const qs = sp.toString() ? `?${sp.toString()}` : ''
  const data = await apiFetch<PicksResponse>(`/api/public/picks${qs}`, 60)
  return data.picks
}

// Server-side only (called from server components, not client)
export async function apiGetCLVData(days = 90, systems?: string): Promise<CLVDataPoint[]> {
  if (!API_URL) throw new Error('BETTING_API_URL is not set')
  const sp = new URLSearchParams({ days: String(days) })
  if (systems) sp.set('systems', systems)
  const res = await fetch(`${API_URL}/api/public/stats/clv?${sp.toString()}`, {
    headers: { 'X-API-Key': API_KEY },
    next: { revalidate: 300 },
  })
  if (!res.ok) throw new Error(`CLV API ${res.status}`)
  const json = await res.json()
  return (json.data ?? []) as CLVDataPoint[]
}

export async function apiGetTodaySlate(): Promise<TodaySlate> {
  if (!API_URL) throw new Error('BETTING_API_URL is not set')
  const res = await fetch(`${API_URL}/api/public/slate/today`, {
    headers: { 'X-API-Key': API_KEY },
    next: { revalidate: 300 },
  })
  if (!res.ok) throw new Error(`Slate API ${res.status}`)
  return res.json() as Promise<TodaySlate>
}

// -- The Edge dashboard enrichment (weather / recent form / spray) ------------
// Lineup/active status for a player pick. confirmed = in today's posted lineup;
// il = on the injured list; out = both lineups posted and he's in neither;
// expected = projected but his lineup not posted yet; unknown = no data yet.
// The cockpit never hides picks — it badges them.
export type PlayerStatus = 'confirmed' | 'expected' | 'out' | 'il' | 'unknown'

// A season stat line. realized = traditional (AVG/HR/OBP, ERA/K/IP);
// expected = Statcast quality (xwOBA/xBA/xSLG, xERA). Values are pre-formatted
// strings (or numbers) keyed by short label; order preserved for display.
export interface SeasonStats {
  realized?: { label: string; value: string }[]
  expected?: { label: string; value: string }[]
}

export interface PlayerEnrich {
  position?: string | null            // "SS", "SP", ...
  status?: PlayerStatus
  season?: SeasonStats
  weather?: { temp_f: number | null; wind_mph: number | null; wind_dir: string | null }
  recent_form?: { stat: string; line: number | null; games: { date: string; value: number; over: boolean | null }[] }
  spray?: { x: number; y: number; hit: boolean; ev?: number | null }[]
  ev_la?: { ev: number; la: number; hit: boolean }[]
  velo?: { pitch: string; mph: number; n: number }[]
  release?: { x: number; z: number; pitch: string }[]
  zone?: Record<string, number>
}
export interface EdgeEnrich { date: string; players: Record<string, PlayerEnrich> }

// Server-only (called from the /edge server component). Fail-soft: returns empty
// players on any error so the dashboard always renders.
export async function apiGetEdgeEnrich(date: string): Promise<EdgeEnrich> {
  if (!API_URL) return { date, players: {} }
  try {
    const res = await fetch(`${API_URL}/api/public/edge-enrich?date=${date}`, {
      headers: { 'X-API-Key': API_KEY },
      next: { revalidate: 300 },
    })
    if (!res.ok) return { date, players: {} }
    return res.json() as Promise<EdgeEnrich>
  } catch {
    return { date, players: {} }
  }
}

// -- live +EV outlier alerts (fast_alert_loop, 15-min cadence) -----------------
export interface EdgeAlert {
  market: string | null
  game_pk: number | null
  player_id: number | null
  selection: string | null
  line: number | null
  book: string | null
  american: number | null
  ev: number | null            // fraction, e.g. 0.062 = +6.2%
  anchored: boolean            // confirmed vs the Pinnacle sharp anchor
  snapshot_ts: string
}

// Server-only. Fail-soft: empty list on any error so /edge always renders.
export async function apiGetTodayAlerts(date: string): Promise<EdgeAlert[]> {
  if (!API_URL) return []
  try {
    const res = await fetch(`${API_URL}/api/public/alerts/today?date=${date}`, {
      headers: { 'X-API-Key': API_KEY },
      next: { revalidate: 120 },
    })
    if (!res.ok) return []
    const data = await res.json() as { alerts: EdgeAlert[] }
    return data.alerts ?? []
  } catch {
    return []
  }
}

// Client-side lazy fetch of one player's enrichment (used when a pick is
// selected in the cockpit). Goes through the /api/edge-enrich proxy so keys
// stay server-side. `normName` must match the producer's normalized key.
export async function apiGetPlayerEnrich(date: string, normName: string): Promise<PlayerEnrich | null> {
  try {
    const res = await fetch(`/api/edge-enrich?date=${encodeURIComponent(date)}&player=${encodeURIComponent(normName)}`, { cache: 'no-store' })
    if (!res.ok) return null
    const data = (await res.json()) as EdgeEnrich
    return data.players?.[normName] ?? Object.values(data.players ?? {})[0] ?? null
  } catch {
    return null
  }
}
