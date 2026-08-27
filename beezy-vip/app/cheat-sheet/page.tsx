export const dynamic = 'force-dynamic'

import fs from 'fs'
import path from 'path'
import type { Metadata } from 'next'
import { apiGetTodayPicks, apiGetRecentSettled } from '@/lib/betting-api'
import type { Bet } from '@/lib/types'
import { beezyscore } from '@/lib/beezy-score'
import { CheatSheetClient, type EnrichedBet } from './cheat-sheet-client'
import { formatCentralDate, siteDateKey, addDaysToDateKey } from '@/lib/dates'
import playerMap from '@/public/headshots/player_map.json'

export const metadata: Metadata = {
  title: 'Daily Card - Beezy.FYI',
  description: "Today's MLB Daily Card - top Beezy Score picks, filterable by game, pitcher, and player props.",
  openGraph: {
    title: 'MLB Daily Card - Beezy.FYI',
    description: 'Top picks by Beezy Score with model edge on every play.',
    images: ['/api/og/picks-card'],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'MLB Daily Card - Beezy.FYI',
    description: 'Top picks by Beezy Score with model edge on every play.',
    images: ['/api/og/picks-card'],
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
  return name.toLowerCase()
    .replace(/\s+/g, '_')
    .replace(/-/g, '_')
    .replace(/[^\p{L}\p{N}_]/gu, '')
}

function playerSlugNormalized(name: string): string {
  return name.normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/\s+/g, '_')
    .replace(/[^a-z0-9_]/g, '')
}

function headshotUrl(name: string | null): string | null {
  if (!name) return null
  const key = playerSlug(name)
  const keyAscii = playerSlugNormalized(name)

  for (const k of [key, keyAscii]) {
    const localPath = path.join(PUBLIC_DIR, 'headshots', `${k}.png`)
    if (fs.existsSync(localPath)) return `/headshots/${k}.png`
  }

  const id = PLAYER_MAP[key] ?? PLAYER_MAP[keyAscii]
  if (!id) return null
  return `https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/${id}/headshot/67/current`
}

function dateLabel(): string {
  return formatCentralDate(new Date(), {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  }).toUpperCase()
}

function enrich(bet: Bet): EnrichedBet {
  return {
    ...bet,
    headshotUrl: headshotUrl(bet.player),
    awayLogoUrl: logoUrl(bet.away_team),
    homeLogoUrl: logoUrl(bet.home_team),
  }
}

export default async function CheatSheetPage() {
  const raw = await apiGetTodayPicks().catch(() => [] as Bet[])
  const picks = raw
    // The Daily Card showcases model picks -- exclude pooled +EV alerts
    // (system="EV", a cross-market soft-line/Kalshi scanner, not a model
    // prediction). They'd otherwise show up here with a book suffix baked
    // into bet_type and no Beezy Score inputs of their own.
    .filter(b => b.system !== 'EV')
    .map(enrich)
    .sort((a, b) => beezyscore(b) - beezyscore(a))

  // Yesterday's settled picks for empty-state proof
  const yesterday = addDaysToDateKey(siteDateKey(), -1)
  const settledRaw = await apiGetRecentSettled(40).catch(() => [] as Bet[])
  const yesterdayPicks = settledRaw
    .filter(b => b.game_date === yesterday && b.system !== 'EV')
    .map(enrich)
    .sort((a, b) => beezyscore(b) - beezyscore(a))

  return (
    <CheatSheetClient picks={picks} today={dateLabel()} yesterdayPicks={yesterdayPicks} />
  )
}
