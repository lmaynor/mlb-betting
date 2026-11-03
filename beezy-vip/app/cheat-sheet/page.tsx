export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { limitRows } from '@/lib/access'
import { getViewer } from '@/lib/auth'
import { addDaysToDateKey, formatCentralDate, siteDateKey } from '@/lib/dates'
import { buildSheets, SHEET_SIZE } from '@/lib/cheat-sheet-post'
import { teamLogoUrl, type AssetSport } from '@/lib/sport-assets'
import { headshotUrlByName } from '@/lib/sport-assets.server'
import { SPORT_SLUGS, type SportSlug } from '@/lib/sports'
import { recordOf, type TopPick } from '@/lib/top-picks'
import { loadDailyPlaysHistory, loadTopPlayerPicks } from '@/lib/top-picks-data'
import type { Bet } from '@/lib/types'
import { CheatSheetClient, type CardPick } from './cheat-sheet-client'

export const metadata: Metadata = {
  title: 'Daily Card',
  description: "Today's Daily Card: every official play across MLB, NFL, NHL and NBA, no cap and no filler, with the graded record behind it.",
  openGraph: {
    title: 'Daily Card \u2014 beezy.fyi',
    description: 'Every official play with its price and edge, and the graded record behind them.',
    images: ['/api/og/picks-card'],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Daily Card \u2014 beezy.fyi',
    description: 'Every official play with its price and edge, and the graded record behind them.',
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

/** Headshot for a player pick (NFL: nfl.com image token via the team-keyed map) and team logos for a game pick. */
function enrich(p: TopPick): CardPick {
  const isPlayer = p.sport !== 'mlb' || !!p.bet?.player
  const headshotUrl = isPlayer ? headshotUrlByName(p.sport, p.who, p.team) : null
  const bet = p.bet
  const sp = bet ? assetSport(bet) : null
  const logo = (team: string | null) => (sp ? teamLogoUrl(sp, team) : logoUrl(team))
  return { ...p, headshotUrl, awayLogoUrl: bet ? logo(bet.away_team) : null, homeLogoUrl: bet ? logo(bet.home_team) : null }
}

/** Days of record shown on the card; the full range is on Graded History. */
const RECORD_DAYS = 7

export default async function CheatSheetPage({ searchParams }: { searchParams?: Promise<{ ops?: string }> }) {
  const date = siteDateKey()
  const params = searchParams ? await searchParams : undefined
  const recentDates = Array.from({ length: RECORD_DAYS }, (_, i) => addDaysToDateKey(date, -(i + 1)))
  const [{ dailyCard }, viewer, recent] = await Promise.all([
    loadTopPlayerPicks(date),
    getViewer(),
    loadDailyPlaysHistory(recentDates),
  ])
  const canPost = viewer.tier !== 'public' || params?.ops === '1'

  // Yesterday's graded plays (the empty-state proof) and the last week's record: both are the same daily plays Graded History lists.
  const yesterdayPicks = recent.find(d => d.date === addDaysToDateKey(date, -1))?.picks ?? []
  const weekRecord = recordOf(recent.flatMap(d => d.picks))

  // One list per sport from the day's official plays. limitRows keeps the free player-board preview rule when tier gating is on; the
  // overall list is the merge of those cut lists, so a free viewer cannot read the whole card through the "All" chip.
  // The card's sport chips AND the POST TO X sheets both read these exact arrays, so the screenshot and the text cannot disagree.
  const sportPicks: Partial<Record<SportSlug, CardPick[]>> = {}
  let locked = 0
  for (const s of SPORT_SLUGS) {
    const cut = limitRows(dailyCard.filter(p => p.sport === s), viewer, 'player_board', s, p => p.score)
    locked += cut.locked
    if (cut.rows.length) sportPicks[s] = cut.rows.map(enrich)
  }
  const players = Object.values(sportPicks).flat().sort((a, b) => b.score - a.score || (b.edge ?? 0) - (a.edge ?? 0))
  const sheets = buildSheets(
    players.slice(0, SHEET_SIZE),
    Object.fromEntries(Object.entries(sportPicks).map(([s, l]) => [s, (l ?? []).slice(0, SHEET_SIZE)])),
    formatCentralDate(new Date(), { weekday: 'long' }),
  )

  return (
    <CheatSheetClient
      sheets={sheets}
      canPost={canPost}
      sportPicks={sportPicks}
      players={players}
      locked={locked}
      today={dateLabel()}
      yesterday={yesterdayPicks.map(enrich)}
      weekRecord={{ ...weekRecord, days: recent.length }}
    />
  )
}
