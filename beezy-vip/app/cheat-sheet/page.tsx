export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { LockedNotice } from '@/components/picks/locked-notice'
import { limitBets } from '@/lib/access-bets'
import { getViewer } from '@/lib/auth'
import { apiGetTodayPicks, apiGetRecentSettled } from '@/lib/betting-api'
import type { Bet } from '@/lib/types'
import { beezyscore } from '@/lib/beezy-score'
import { CheatSheetClient, type EnrichedBet } from './cheat-sheet-client'
import { formatCentralDate, siteDateKey, addDaysToDateKey } from '@/lib/dates'
import { teamLogoUrl, type AssetSport } from '@/lib/sport-assets'
import { headshotUrlByName } from '@/lib/sport-assets.server'

export const metadata: Metadata = {
  title: 'Daily Card',
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

function logoUrl(abbrev: string | null): string | null {
  const slug = TEAM_SLUG[(abbrev ?? '').toUpperCase()] ?? null
  return slug ? `/logos/${slug}.png` : null
}

function dateLabel(): string {
  return formatCentralDate(new Date(), {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  }).toUpperCase()
}

// NHL/NBA rows (bets.sport, added 2026-10-14) resolve logos/headshots through lib/sport-assets; everything else is MLB
// and takes the unchanged path below. The API defaults to sport=mlb, so today every row is MLB -- this is dormant until
// NHL/NBA picks are requested.
function assetSport(bet: Bet): AssetSport | null {
  const s = (bet as Bet & { sport?: string }).sport
  return s === 'nhl' || s === 'nba' ? s : null
}

function enrich(bet: Bet): EnrichedBet {
  const sp = assetSport(bet)
  if (sp) {
    return {
      ...bet,
      headshotUrl: headshotUrlByName(sp, bet.player),
      awayLogoUrl: teamLogoUrl(sp, bet.away_team),
      homeLogoUrl: teamLogoUrl(sp, bet.home_team),
    }
  }
  return {
    ...bet,
    headshotUrl: headshotUrlByName('mlb', bet.player),
    awayLogoUrl: logoUrl(bet.away_team),
    homeLogoUrl: logoUrl(bet.home_team),
  }
}

export default async function CheatSheetPage() {
  // Today's card is the paid surface (lib/access.ts); the cut happens here so withheld picks are not in the page payload.
  const [rawAll, viewer] = await Promise.all([apiGetTodayPicks().catch(() => [] as Bet[]), getViewer()])
  const { bets: raw, locked } = limitBets(rawAll, viewer, siteDateKey())
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
    <>
      <CheatSheetClient picks={picks} today={dateLabel()} yesterdayPicks={yesterdayPicks} />
      <div style={{ maxWidth: '420px', margin: '0 auto', padding: '0 16px 24px' }}>
        <LockedNotice locked={locked} shown={picks.length} what="picks" plan="The full card is on the MLB Pass or All-Access Pro." />
      </div>
    </>
  )
}
