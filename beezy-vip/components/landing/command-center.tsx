import { apiGetEvFeed, apiGetTodayPicks } from '@/lib/betting-api'
import { siteDateKey } from '@/lib/dates'
import type { Bet, EvBookOutlierRow, EvMlbRow } from '@/lib/types'
import { CommandCenterClient } from './command-center-client'

function evToBet(r: EvMlbRow): Bet {
  return {
    id: -r.id, system: 'EV', game_date: r.game_date, game_pk: r.game_pk ?? 0, bet_type: r.bet_type,
    player: r.player, away_team: r.away_team, home_team: r.home_team, odds: r.odds, stake: r.stake,
    model_prob: r.model_prob ?? 0, market_prob: r.market_prob ?? 0, edge: r.edge, kelly_pct: r.kelly_pct,
    kelly_triggered: true, result: r.result, profit: r.profit, paper: true, book: r.book, notes: r.notes,
    created_at: r.created_at,
  }
}

function outlierToBet(r: EvBookOutlierRow, idx: number): Bet {
  return {
    id: -(idx + 1000),
    sport: r.sport ?? null,
    system: 'EV',
    game_date: r.commence ? r.commence.split('T')[0] : siteDateKey(),
    game_pk: 0,
    bet_type: `${r.stat ?? 'prop'} ${r.side} ${r.point ?? ''}`.trim(),
    player: r.player,
    away_team: r.away_team ?? null,
    home_team: r.home_team ?? null,
    odds: r.american,
    stake: 10,
    model_prob: r.p_fair ?? 0,
    market_prob: 0,
    edge: r.ev,
    kelly_pct: 0.02,
    kelly_triggered: true,
    result: null,
    profit: null,
    paper: true,
    book: r.book,
    notes: null,
    created_at: new Date().toISOString(),
  }
}

export async function CommandCenter() {
  const [picks, feed] = await Promise.all([
    apiGetTodayPicks('all').catch(() => [] as Bet[]),
    apiGetEvFeed({ sport: 'all', minEv: 0.03, limit: 40 }).catch(() => null),
  ])
  const evMlb = (feed?.mlb?.rows ?? []).filter(r => r.result == null && r.game_date >= siteDateKey()).map(evToBet)
  const evNfl = (feed?.nfl?.book_outliers ?? []).map(outlierToBet)
  const evNba = (feed?.nba?.rows ?? []).map(outlierToBet)
  const evNhl = (feed?.nhl?.rows ?? []).map(outlierToBet)
  return <CommandCenterClient bets={[...picks.filter(p => p.result == null), ...evMlb, ...evNfl, ...evNba, ...evNhl]} />
}
