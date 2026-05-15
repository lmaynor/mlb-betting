// Client for the mlb-betting Cloud Run public API.
// Replaces direct Cloud SQL access for all bets-table queries.
// The Cloud Run service handles auth and Cloud SQL via its own binding.

import type { Bet, SystemStats } from '@/lib/db'

const API_URL  = process.env.BETTING_API_URL  ?? ''
const API_KEY  = process.env.BETTING_API_KEY  ?? ''

async function apiFetch<T>(path: string, cacheSecs = 60): Promise<T> {
  if (!API_URL) throw new Error('BETTING_API_URL is not set -- set this env var in Vercel')
  const res = await fetch(`${API_URL}${path}`, {
    headers: { 'X-API-Key': API_KEY },
    next: { revalidate: cacheSecs },
  })
  if (!res.ok) {
    throw new Error(`Betting API error ${res.status} on ${path}`)
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
  const data = await apiFetch<PicksResponse>('/api/public/picks/today', 60)
  return data.picks
}

export async function apiGetRecentSettled(limit = 20): Promise<Bet[]> {
  const data = await apiFetch<PicksResponse>(`/api/public/picks/recent?limit=${limit}`, 120)
  return data.picks
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
}): Promise<Bet[]> {
  const sp = new URLSearchParams()
  if (params.system) sp.set('system',  params.system)
  if (params.date)   sp.set('date',    params.date)
  if (params.status) sp.set('status',  params.status)
  if (params.limit)  sp.set('limit',   String(params.limit))
  if (params.offset) sp.set('offset',  String(params.offset))
  const qs = sp.toString() ? `?${sp.toString()}` : ''
  const data = await apiFetch<PicksResponse>(`/api/public/picks${qs}`, 60)
  return data.picks
}
