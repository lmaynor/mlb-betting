/**
 * MLB projection board: one row per player x count market, built from today's scored predictions. The projection is the
 * model's own expected value for the stat (`proj_mean`, the same number the over/under probability is priced from);
 * the side, probability and price are those of the side the model prefers. HR and the game markets are probability
 * models with no expected count, so they are not on this board.
 */
import { beezyscore } from '@/lib/beezy-score'
import type { Bet } from '@/lib/types'

export const PROJECTION_MARKETS: Record<string, { label: string; unit: string }> = {
  K: { label: 'Strikeouts', unit: 'Ks' },
  OUTS: { label: 'Outs', unit: 'outs' },
  BATTER_TB: { label: 'Total bases', unit: 'TB' },
  BATTER_HITS: { label: 'Hits', unit: 'hits' },
  SB: { label: 'Stolen bases', unit: 'SB' },
  PITCHER_ER: { label: 'Earned runs', unit: 'ER' },
}

export interface MlbProjectionRow {
  key: string
  id: number
  system: string
  marketLabel: string
  player: string
  matchup: string
  side: 'OVER' | 'UNDER'
  line: number
  proj: number
  /** probability of the listed side, 0-1 */
  pModel: number | null
  odds: number | null
  book: string | null
  /** fraction */
  edge: number | null
  score: number
  /** the pick cleared the edge and Kelly gates and is tracked at a stake */
  isPick: boolean
}

/** "K_OVER_5.5" -> OVER 5.5. The one-sided 2+/3+ thresholds ("K_2PLUS_2.0") are alt lines, not a main line: null. */
export function parseSideLine(betType: string | null | undefined): { side: 'OVER' | 'UNDER'; line: number } | null {
  const m = /_(OVER|UNDER)_(\d+(?:\.\d+)?)$/.exec(betType ?? '')
  return m ? { side: m[1] as 'OVER' | 'UNDER', line: Number(m[2]) } : null
}

const asFraction = (p: number | null | undefined) => (p == null ? null : p <= 1.5 ? p : p / 100)
const edgeFraction = (e: number | null | undefined) => (e == null ? null : Math.abs(e) < 2 ? e : e / 100)

export function toProjectionRow(bet: Bet): MlbProjectionRow | null {
  const market = PROJECTION_MARKETS[bet.system]
  const sl = parseSideLine(bet.bet_type)
  const proj = bet.proj_mean
  if (!market || !sl || !bet.player || proj == null || !Number.isFinite(proj)) return null
  return {
    key: `${bet.system}|${bet.game_pk}|${bet.player}`,
    id: bet.id,
    system: bet.system,
    marketLabel: market.label,
    player: bet.player,
    matchup: bet.away_team && bet.home_team ? `${bet.away_team} @ ${bet.home_team}` : '',
    side: sl.side,
    line: sl.line,
    proj,
    pModel: asFraction(bet.model_prob),
    odds: bet.odds,
    book: bet.book,
    edge: edgeFraction(bet.edge),
    score: bet.kelly_triggered || (edgeFraction(bet.edge) ?? 0) > 0 ? beezyscore(bet) : 0,
    isPick: !!bet.kelly_triggered,
  }
}

/**
 * Today's predictions -> board rows. One row per player and market: a tracked pick wins over an unstaked prediction,
 * then the higher score. Pooled +EV rows and one-sided thresholds are left out.
 */
export function buildMlbProjections(bets: Bet[]): MlbProjectionRow[] {
  const best = new Map<string, MlbProjectionRow>()
  for (const b of bets) {
    if (b.system === 'EV') continue
    const row = toProjectionRow(b)
    if (!row) continue
    const cur = best.get(row.key)
    if (!cur || Number(row.isPick) - Number(cur.isPick) > 0 || (row.isPick === cur.isPick && row.score > cur.score)) best.set(row.key, row)
  }
  return [...best.values()].sort((a, b) => b.score - a.score || (b.edge ?? -1) - (a.edge ?? -1) || a.player.localeCompare(b.player))
}
