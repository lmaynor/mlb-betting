export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { apiGetTodayAllPredictions, apiGetTodaySlate, apiGetEdgeEnrich, apiGetTodayAlerts, apiGetEdgeCockpit, type EdgeAlert } from '@/lib/betting-api'
import type { Bet, CockpitFeed, TodaySlate, SlateGame } from '@/lib/types'
import { formatCentralDate, siteDateKey } from '@/lib/dates'
import { EdgeClient, type EdgePick } from './edge-client'
import { headshotUrlByName, playerIdByName } from '@/lib/sport-assets.server'

export const metadata: Metadata = {
  title: 'Prop Research',
  description: 'Multi-sport prop research cockpit: MLB, NFL, NHL and NBA lines with last-10 game logs, volume trends and every book price side by side.',
  openGraph: {
    title: 'Prop Research',
    description: 'Prop research across MLB, NFL, NHL and NBA: price, form and volume for every flagged line.',
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
  const [raw, slate, enrichData, alerts, cockpitRaw] = await Promise.all([
    // The model board is model predictions only; pooled +EV rows (system EV) belong to the +EV Terminal.
    apiGetTodayAllPredictions().then(r => r.filter(b => b.system !== 'EV')).catch(() => [] as Bet[]),
    apiGetTodaySlate().catch(() => ({ games: [] } as unknown as TodaySlate)),
    apiGetEdgeEnrich(date),
    apiGetTodayAlerts(date),
    // fail-soft: until the backend serves /edge/cockpit the page falls back to the MLB model board
    apiGetEdgeCockpit({ limit: 60 }).catch(() => null),
  ])

  // MLB headshots resolve by name against the roster map, which is server-only: attach ids here (the client keeps them across refreshes)
  const cockpit: CockpitFeed | null = cockpitRaw
    ? { ...cockpitRaw, rows: cockpitRaw.rows.map(r => (r.sport === 'mlb' && r.player_id == null && r.player ? { ...r, player_id: playerIdByName('mlb', r.player) } : r)) }
    : null

  const gameMap = new Map<number, SlateGame>((slate.games ?? []).map(g => [g.game_pk, g]))
  const players = enrichData.players ?? {}

  // live +EV outlier alerts, matched to picks: player props by (game_pk, MLBAM
  // id via the headshot player_map); game markets by game_pk. Best EV wins.
  const GAME_ALERT_MARKETS = new Set(['nrfi_ou', 'game_ml', 'f5_ml'])
  function alertFor(bet: Bet): EdgeAlert | null {
    const pid = bet.player ? playerIdByName('mlb', bet.player) : null
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
      headshotUrl: headshotUrlByName('mlb', bet.player),
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

  return <EdgeClient picks={picks} updated={updated} dateKey={date} cockpit={cockpit} />
}
