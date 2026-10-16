export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { limitRows } from '@/lib/access'
import { getViewer } from '@/lib/auth'
import { apiGetRecentSettled } from '@/lib/betting-api'
import { addDaysToDateKey, formatCentralDate, siteDateKey } from '@/lib/dates'
import { betKind } from '@/lib/pick-systems'
import { teamLogoUrl, type AssetSport } from '@/lib/sport-assets'
import { headshotUrlByName } from '@/lib/sport-assets.server'
import { TOP_N, topFromBets, type TopPick } from '@/lib/top-picks'
import { loadTopPlayerPicks } from '@/lib/top-picks-data'
import type { Bet } from '@/lib/types'
import { CheatSheetClient, type CardPick } from './cheat-sheet-client'

export const metadata: Metadata = {
  title: 'Daily Card',
  description: "Today's Daily Card - the top 10 player picks across MLB, NFL, NHL and NBA, ranked by Beezy Score, ready to share.",
  openGraph: {
    title: 'Daily Card - Beezy.FYI',
    description: 'Top player picks by Beezy Score with the edge on every play.',
    images: ['/api/og/picks-card'],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Daily Card - Beezy.FYI',
    description: 'Top player picks by Beezy Score with the edge on every play.',
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
  return formatCentralDate(new Date(), { weekday: 'long', month: 'long', day: 'numeric' }).toUpperCase()
}

// bets.sport is mlb unless a row says otherwise (lib/sport-assets handles nhl / nba logos)
function assetSport(bet: Bet): AssetSport | null {
  const s = (bet as Bet & { sport?: string }).sport
  return s === 'nhl' || s === 'nba' ? s : null
}

/** Headshot for a player pick (NFL has no headshot source) and team logos for a game pick. */
function enrich(p: TopPick): CardPick {
  const isPlayer = p.sport !== 'mlb' || !!p.bet?.player
  const headshotUrl = isPlayer && p.sport !== 'nfl' ? headshotUrlByName(p.sport, p.who) : null
  const bet = p.bet
  const sp = bet ? assetSport(bet) : null
  const logo = (team: string | null) => (sp ? teamLogoUrl(sp, team) : logoUrl(team))
  return { ...p, headshotUrl, awayLogoUrl: bet ? logo(bet.away_team) : null, homeLogoUrl: bet ? logo(bet.home_team) : null }
}

export default async function CheatSheetPage() {
  const date = siteDateKey()
  const [{ top, mlbToday }, viewer, settledRaw] = await Promise.all([
    loadTopPlayerPicks(date),
    getViewer(),
    apiGetRecentSettled(60).catch(() => [] as Bet[]),
  ])

  // The Top 10 player picks are the free tier. Game picks are the paid surface (lib/access.ts), cut here so withheld picks
  // are not in the page payload.
  const games = limitRows(topFromBets(mlbToday, TOP_N, 'game'), viewer, 'game_picks', 'mlb', p => p.score)

  // Yesterday's settled MLB player picks, for the empty-state proof
  const yesterday = addDaysToDateKey(date, -1)
  const yesterdayPicks = topFromBets(
    settledRaw.filter(b => b.game_date === yesterday && betKind(b) === 'player' && b.system !== 'EV'),
    3,
  )

  return (
    <CheatSheetClient
      players={top.map(enrich)}
      games={games.rows.map(enrich)}
      gamesLocked={games.locked}
      today={dateLabel()}
      yesterday={yesterdayPicks.map(enrich)}
    />
  )
}
