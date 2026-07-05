export const dynamic = 'force-dynamic'

import fs from 'fs'
import path from 'path'
import type { Metadata } from 'next'
import { apiGetTodayPicks, apiGetTodaySlate, apiGetEdgeEnrich, apiGetTodayAlerts, type EdgeAlert } from '@/lib/betting-api'
import type { Bet, TodaySlate, SlateGame } from '@/lib/types'
import { formatCentralDate, siteDateKey } from '@/lib/dates'
import { EdgeClient, type EdgePick } from './edge-client'
import playerMap from '@/public/headshots/player_map.json'

export const metadata: Metadata = {
  title: 'The Edge - Beezy.FYI',
  description: "Today's MLB picks with our model probability vs the market line -- see the edge behind every play. Updated hourly.",
  openGraph: {
    title: 'The Edge - Beezy.FYI',
    description: 'Model probability vs market line, the edge on every pick. Updated hourly.',
  },
}

const TEAM_SLUG: Record<string, string> = {
  ARI: 'ari', ATL: 'atl', BAL: 'bal', BOS: 'bos', CHC: 'chc', CIN: 'cin', CLE: 'cle',
  COL: 'col', CWS: 'cws', DET: 'det', HOU: 'hou', KC: 'kc', LAA: 'laa', LAD: 'lad',
  MIA: 'mia', MIL: 'mil', MIN: 'min', NYM: 'nym', NYY: 'nyy', OAK: 'oak', PHI: 'phi',
  PIT: 'pit', SD: 'sd', SEA: 'sea', SF: 'sf', STL: 'stl', TB: 'tb', TEX: 'tex',
  TOR: 'tor', WSH: 'wsh',
}

const PLAYER_MAP = playerMap as Record<string, number>
const PUBLIC_DIR = path.join(process.cwd(), 'public')

function logoUrl(abbrev: string | null): string | null {
  const slug = TEAM_SLUG[(abbrev ?? '').toUpperCase()] ?? null
  return slug ? `/logos/${slug}.png` : null
}

function playerSlug(name: string): string {
  return name.toLowerCase().replace(/\s+/g, '_').replace(/-/g, '_').replace(/[^\p{L}\p{N}_]/gu, '')
}
function playerSlugAscii(name: string): string {
  return name.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '')
}
function headshotUrl(name: string | null): string | null {
  if (!name) return null
  const key = playerSlug(name)
  const keyAscii = playerSlugAscii(name)
  for (const k of [key, keyAscii]) {
    if (fs.existsSync(path.join(PUBLIC_DIR, 'headshots', `${k}.png`))) return `/headshots/${k}.png`
  }
  const id = PLAYER_MAP[key] ?? PLAYER_MAP[keyAscii]
  if (!id) return null
  return `https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/${id}/headshot/67/current`
}

// edge stored as fraction (0.08) on some rows, already-pct on others -- normalize to pct
function edgePct(e: number | null): number | null {
  if (e == null) return null
  return Math.abs(e) < 2 ? e * 100 : e
}
// probabilities are 0-1 in the bets table; normalize defensively to pct
function toPct(p: number | null | undefined): number | null {
  if (p == null) return null
  return p <= 1.5 ? p * 100 : p
}

// match runner's _norm_name: NFD-strip diacritics, lowercase, "First Last"
function normName(name: string | null): string {
  if (!name) return ''
  return name.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim()
}

export default async function EdgePage() {
  const date = siteDateKey()
  const [raw, slate, enrichData, alerts] = await Promise.all([
    apiGetTodayPicks().catch(() => [] as Bet[]),
    apiGetTodaySlate().catch(() => ({ games: [] } as unknown as TodaySlate)),
    apiGetEdgeEnrich(date),
    apiGetTodayAlerts(date),
  ])

  const gameMap = new Map<number, SlateGame>((slate.games ?? []).map(g => [g.game_pk, g]))
  const players = enrichData.players ?? {}

  // live +EV outlier alerts, matched to picks: player props by (game_pk, MLBAM
  // id via the headshot player_map); game markets by game_pk. Best EV wins.
  const GAME_ALERT_MARKETS = new Set(['nrfi_ou', 'game_ml', 'f5_ml'])
  function alertFor(bet: Bet): EdgeAlert | null {
    const pid = bet.player ? (PLAYER_MAP[playerSlug(bet.player)] ?? PLAYER_MAP[playerSlugAscii(bet.player)]) : null
    let best: EdgeAlert | null = null
    for (const a of alerts) {
      if (a.game_pk !== bet.game_pk || a.ev == null) continue
      const match = pid != null ? a.player_id === pid
        : (a.player_id == null && GAME_ALERT_MARKETS.has(a.market ?? ''))
      if (match && (best == null || a.ev > (best.ev ?? 0))) best = a
    }
    return best
  }

  function enrich(bet: Bet): EdgePick {
    const g = gameMap.get(bet.game_pk)
    const en = players[normName(bet.player)]
    return {
      ...bet,
      headshotUrl: headshotUrl(bet.player),
      alert: alertFor(bet),
      awayLogoUrl: logoUrl(bet.away_team),
      homeLogoUrl: logoUrl(bet.home_team),
      modelProbPct: toPct(bet.model_prob),
      marketProbPct: toPct(bet.market_prob),
      edgePctValue: edgePct(bet.edge),
      position: en?.position ?? null,
      status: en?.status ?? 'unknown',
      season: en?.season ?? null,
      matchup: g ? {
        awayTeam: g.away_team, awayPitcher: g.away_pitcher,
        homeTeam: g.home_team, homePitcher: g.home_pitcher,
        startTime: g.start_time,
      } : null,
      weather: en?.weather ?? null,
      recentForm: en?.recent_form ?? null,
      spray: en?.spray ?? null,
      evLa: en?.ev_la ?? null,
      velo: en?.velo ?? null,
      release: en?.release ?? null,
      zone: en?.zone ?? null,
    }
  }

  const picks = raw
    .map(enrich)
    .filter(p => p.edgePctValue != null)
    .sort((a, b) => (b.edgePctValue ?? 0) - (a.edgePctValue ?? 0))

  const updated = formatCentralDate(new Date(), {
    weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })

  return <EdgeClient picks={picks} updated={updated} dateKey={date} />
}
