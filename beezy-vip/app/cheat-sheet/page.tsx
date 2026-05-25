export const dynamic = 'force-dynamic'

import fs   from 'fs'
import path from 'path'
import type { Metadata } from 'next'
import { apiGetTodayPicks } from '@/lib/betting-api'
import type { Bet } from '@/lib/types'
import { CheatSheetClient, type EnrichedBet } from './cheat-sheet-client'

export const metadata: Metadata = {
  title: 'Cheat Sheet — Beezy.VIP',
  description: "Today's MLB picks — top 5 edge-ranked, filterable by game / pitcher / player props.",
  openGraph: {
    title: "MLB Picks Cheat Sheet — Beezy.VIP",
    description: 'Top picks by edge. NRFI · HR · F5 · K · OUTS.',
    images: [{ url: '/api/og?title=MLB+Cheat+Sheet', width: 1200, height: 630 }],
  },
}

// ── Helpers (server-only) ────────────────────────────────────────────────────

const TEAM_SLUG: Record<string, string> = {
  ARI:'ari', ATL:'atl', BAL:'bal', BOS:'bos', CHC:'chc', CIN:'cin', CLE:'cle',
  COL:'col', CWS:'cws', DET:'det', HOU:'hou', KC:'kc',  LAA:'laa', LAD:'lad',
  MIA:'mia', MIL:'mil', MIN:'min', NYM:'nym', NYY:'nyy', OAK:'oak', PHI:'phi',
  PIT:'pit', SD:'sd',  SEA:'sea', SF:'sf',  STL:'stl', TB:'tb',  TEX:'tex',
  TOR:'tor', WSH:'wsh',
}

// eslint-disable-next-line @typescript-eslint/no-require-imports
const PLAYER_MAP: Record<string, number> = (() => {
  try { return require('@/public/headshots/player_map.json') }
  catch { return {} }
})()

const PUBLIC_DIR = path.join(process.cwd(), 'public')

function logoUrl(abbrev: string | null): string | null {
  const slug = TEAM_SLUG[(abbrev ?? '').toUpperCase()] ?? null
  return slug ? `/logos/${slug}.png` : null
}

function playerSlug(name: string): string {
  // Preserve accented chars; convert hyphens→_ BEFORE stripping so Ha-Seong→ha_seong not haseong
  return name.toLowerCase().replace(/\s+/g, '_').replace(/-/g, '_').replace(/[^\wÀ-ɏ]/g, '')
}

function playerSlugNormalized(name: string): string {
  // NFD-normalize then strip diacritics: Vásquez → vasquez. Used as fallback.
  return name.normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '')
}

function headshotUrl(name: string | null): string | null {
  if (!name) return null
  const key      = playerSlug(name)
  const keyAscii = playerSlugNormalized(name)

  // Prefer local bg-removed PNG; try accented key then ascii-normalized key
  for (const k of [key, keyAscii]) {
    const localPath = path.join(PUBLIC_DIR, 'headshots', `${k}.png`)
    if (fs.existsSync(localPath)) return `/headshots/${k}.png`
  }

  // Fall back to MLB CDN via player_map
  const id = PLAYER_MAP[key] ?? PLAYER_MAP[keyAscii]
  if (!id) return null
  return `https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/${id}/headshot/67/current`
}

function dateLabel(): string {
  return new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric',
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

// ── Page ─────────────────────────────────────────────────────────────────────

export default async function CheatSheetPage() {
  const raw   = await apiGetTodayPicks().catch(() => [] as Bet[])
  const picks = raw
    .sort((a, b) => ((b.edge ?? 0) - (a.edge ?? 0)))
    .map(enrich)

  return <CheatSheetClient picks={picks} today={dateLabel()} />
}
