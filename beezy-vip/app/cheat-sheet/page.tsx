export const dynamic = 'force-dynamic'

import fs from 'fs'
import path from 'path'
import type { Metadata } from 'next'
import { apiGetTodayPicks } from '@/lib/betting-api'
import type { Bet } from '@/lib/types'
import { beezyscore } from '@/lib/beezy-score'
import { CheatSheetClient, type EnrichedBet } from './cheat-sheet-client'
import { SlateStrip } from '@/components/today/slate-strip'
import playerMap from '@/public/headshots/player_map.json'

export const metadata: Metadata = {
  title: 'Daily Card - Beezy.VIP',
  description: "Today's MLB Daily Card - top Beezy Score picks, filterable by game, pitcher, and player props.",
  openGraph: {
    title: 'MLB Daily Card - Beezy.VIP',
    description: 'Top picks by Beezy Score. NRFI / HR / F5 / K / OUTS.',
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
  return new Date().toLocaleDateString('en-US', {
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
    .map(enrich)
    .sort((a, b) => beezyscore(b) - beezyscore(a))

  return (
    <>
      <SlateStrip />
      <CheatSheetClient picks={picks} today={dateLabel()} />
    </>
  )
}
