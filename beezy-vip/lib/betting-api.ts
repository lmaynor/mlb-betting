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
