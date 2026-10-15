import type { Bet } from './types'
import { type ScoreTier, TIER_COLOR, TIER_LABEL } from './tokens'

export type { ScoreTier }
export { TIER_COLOR, TIER_LABEL }

/**
 * Beezy Score v2: a 0-100 strength score for one flagged price. It is NOT a win probability.
 *
 *   edge          up to 60 pts   4 pts per 1% of edge at the flagged price (full at 15%)
 *   consensus     up to 20 pts   how many books back the fair price (3 books = 0, 12+ = 20)
 *   hit prob      up to 20 pts   realistic chance the bet wins (15% = 0, 50%+ = 20): longshots are penalised
 *
 * The last two are scaled by min(edge / 2%, 1), so a price with no edge scores 0 however many books agree
 * (v1 handed every +EV alert 30 free points: a constant Kelly bonus and a chalk-price bonus).
 * Model bets carry no books count, so they get a neutral 10 consensus points.
 *
 * Evidence (docs: handoffs/handoff_2026-10-15_beezy_score_v2.md): on 7,259 graded +EV alerts, v2 tiers realised flat
 * ROI of +25.8% (STRONG, n=918) / +6.4% (LEAN, n=3,890) / +3.5% (WATCH, n=2,451), monotonic. v1 was not (WATCH
 * beat LEAN). The cut points were chosen on that same sample, so expect them to shrink out of sample. They are
 * unvalidated for model bets.
 */
export const SCORE_WEIGHTS = { edge: 60, consensus: 20, hit: 20 } as const
export const TIER_CUTS = { strong: 70, lean: 45 } as const

// edge is stored as a FRACTION (0.08) on some rows and already-PERCENT (8) on others; probs are usually 0-1 but
// occasionally pct. Normalize both before scoring.
function edgeAsPct(e: number | null | undefined): number {
  if (e == null) return 0
  return Math.abs(e) < 2 ? e * 100 : e
}
function probAsFraction(p: number | null | undefined): number {
  if (p == null) return 0
  return p <= 1.5 ? p : p / 100
}
const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x))

/** Reference-book count behind a flagged price. EV rows carry it in notes as "(N books)"; model bets do not. */
export function booksBehind(bet: Pick<Bet, 'notes'> & { n_books?: number | null }): number | null {
  if (bet.n_books != null) return bet.n_books
  const m = /\((\d+) books\)/.exec(bet.notes ?? '')
  return m ? Number(m[1]) : null
}

export interface ScoreParts { edge: number; consensus: number; hit: number; total: number }

export function scoreParts(bet: Bet): ScoreParts {
  const edgePct = Math.max(edgeAsPct(bet.edge), 0)
  const edge = Math.min(edgePct * 4, SCORE_WEIGHTS.edge)
  const books = booksBehind(bet)
  const consensusRaw = books == null ? SCORE_WEIGHTS.consensus / 2 : SCORE_WEIGHTS.consensus * clamp((books - 3) / 9, 0, 1)
  const hitRaw = SCORE_WEIGHTS.hit * clamp((probAsFraction(bet.model_prob) - 0.15) / 0.35, 0, 1)
  const scale = Math.min(edgePct / 2, 1)
  const consensus = consensusRaw * scale
  const hit = hitRaw * scale
  return { edge, consensus, hit, total: Math.round(clamp(edge + consensus + hit, 0, 100)) }
}

export function beezyscore(bet: Bet): number {
  return scoreParts(bet).total
}

export function scoreTier(score: number): ScoreTier {
  if (score >= TIER_CUTS.strong) return 'strong'
  if (score >= TIER_CUTS.lean) return 'lean'
  return 'watch'
}

/** One-line breakdown for a tooltip: "52 = edge 30 + consensus 4 + hit prob 17". */
export function explainScore(bet: Bet): string {
  const p = scoreParts(bet)
  return `Beezy Score ${p.total} = edge ${Math.round(p.edge)} + consensus ${Math.round(p.consensus)} + hit probability ${Math.round(p.hit)}`
}

export const TIER_META: Record<ScoreTier, { label: string; color: string; bg: string; border: string; glow: string }> = {
  strong: {
    label: 'STRONG',
    color: '#57F287',
    bg: 'rgba(87, 242, 135, 0.10)',
    border: 'rgba(87, 242, 135, 0.35)',
    glow: 'none',
  },
  lean: {
    label: 'LEAN',
    color: '#facc15',
    bg: 'rgba(250, 204, 21, 0.10)',
    border: 'rgba(250, 204, 21, 0.30)',
    glow: 'none',
  },
  watch: {
    label: 'WATCH',
    color: '#94a3b8',
    bg: 'rgba(148, 163, 184, 0.08)',
    border: '#1e2230',
    glow: 'none',
  },
}
