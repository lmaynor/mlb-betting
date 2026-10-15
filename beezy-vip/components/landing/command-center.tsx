import { apiGetEvFeed, apiGetTodayPicks } from '@/lib/betting-api'
import { siteDateKey } from '@/lib/dates'
import type { Bet, EvMlbRow } from '@/lib/types'
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

export async function CommandCenter() {
  const [picks, feed] = await Promise.all([
    apiGetTodayPicks().catch(() => [] as Bet[]),
    apiGetEvFeed({ sport: 'mlb', minEv: 0.03, limit: 40 }).catch(() => null),
  ])
  // Only today's open alerts: an earlier date means the game is over and just awaiting grading.
  const ev = (feed?.mlb.rows ?? []).filter(r => r.result == null && r.game_date >= siteDateKey()).map(evToBet)
  return <CommandCenterClient bets={[...picks.filter(p => p.result == null), ...ev]} />
}
