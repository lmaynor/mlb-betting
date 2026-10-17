/**
 * MLB's tracked bets (picks and +EV alerts) as board rows, so the MLB page is the SAME table, filters and games strip as
 * NFL / NHL / NBA. One row per prop (the same prop flagged at several books collapses to its best-edge row, as the old card
 * did), with the settled result when the day is over.
 */
import { beezyscore } from '@/lib/beezy-score'
import { collapseBooks } from '@/lib/dedupe'
import type { BoardRow } from '@/lib/board-rows'
import { parseSideLine } from '@/lib/mlb-projections'
import { pickLabel, resolveEvUnderlying, teamAbbrev } from '@/lib/tokens'
import type { Bet } from '@/lib/types'

const asFraction = (p: number | null | undefined) => (p == null ? null : p <= 1.5 ? p : p / 100)
const edgeFraction = (e: number | null | undefined) => (e == null ? null : Math.abs(e) < 2 ? e : e / 100)

/** "Aaron Judge Over 1.5 Total Bases" -> "Total Bases": the player has its own column and the side and line have theirs. */
const withoutPlayer = (label: string, player: string | null, hasSideLine: boolean) => {
  const l = player && label.startsWith(player) ? label.slice(player.length).trim() : label
  return hasSideLine ? l.replace(/^(Over|Under)\s+[\d.]+\s+/i, '') : l
}

export function fromBets(bets: Bet[], opts: { collapse?: boolean } = {}): BoardRow[] {
  const rows = opts.collapse === false ? bets.map(b => ({ ...b, bookCount: 1 })) : collapseBooks(bets)
  return rows.map(b => {
    const bt = b.system === 'EV' ? resolveEvUnderlying(b).bet_type : (b.bet_type ?? '')
    const sl = parseSideLine(bt)
    const away = teamAbbrev(b.away_team) || null
    const home = teamAbbrev(b.home_team) || null
    const matchup = away && home ? `${away} @ ${home}` : ''
    const edge = edgeFraction(b.edge)
    return {
      key: String(b.id),
      sport: 'mlb' as const,
      system: b.system,
      marketLabel: withoutPlayer(pickLabel(b), b.player, sl != null),
      player: b.player ?? matchup,
      headshotId: null,
      team: null,
      matchup,
      gameKey: `mlb:${b.game_pk}`,
      away,
      home,
      start: null,
      side: sl ? (sl.side === 'UNDER' ? 'under' as const : 'over' as const) : null,
      line: sl ? sl.line : null,
      hasLine: b.odds != null,
      proj: b.proj_mean ?? null,
      avg10: null,
      avgSzn: null,
      pModel: asFraction(b.model_prob),
      pFair: asFraction(b.market_prob),
      nBooks: b.bookCount > 1 ? b.bookCount : null,
      l5: null,
      l10: null,
      odds: b.odds ?? null,
      book: b.book,
      modelEdge: edge,
      marketEv: null,
      confluence: false,
      score: b.kelly_triggered || (edge ?? 0) > 0 ? beezyscore(b) : 0,
      isPick: !!b.kelly_triggered,
      result: b.result,
    }
  })
}
