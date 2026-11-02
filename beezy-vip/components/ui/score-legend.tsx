import { SCORE_WEIGHTS, TIER_CUTS } from '@/lib/beezy-score'

/** The published definition of the Beezy Score, shown on every page that ranks by it. Collapsed by default. */
export function ScoreLegend() {
  return (
    <details style={{ marginTop: '14px', border: '1px solid var(--color-separator-border)', borderRadius: '8px', background: 'var(--color-background-primary-default)', padding: '10px 14px' }}>
      <summary style={{ cursor: 'pointer', fontSize: '12.5px', fontWeight: 600, color: 'var(--color-neutral-300)' }}>How the Beezy Score works</summary>
      <div className="text-[12.5px] text-[var(--color-neutral-300)] leading-[1.65] mt-[10px] grid gap-[8px]">
        <p className="m-0">
          A 0-100 strength score for one flagged price. It is not a win probability. Hover a score to see its parts.
        </p>
        <ul className="m-0 pl-[18px]">
          <li><strong>Edge, up to {SCORE_WEIGHTS.edge}:</strong> 4 points per 1% of edge at the flagged price.</li>
          <li><strong>Consensus, up to {SCORE_WEIGHTS.consensus}:</strong> how many other books back the fair price (3 books is 0, 12 or more is the max). Model bets have no books count and get a neutral 10.</li>
          <li><strong>Hit probability, up to {SCORE_WEIGHTS.hit}:</strong> longshots are penalised (15% to win is 0, 50% or better is the max).</li>
        </ul>
        <p className="m-0">
          Consensus and hit probability only count when there is an edge, so a price with no edge scores 0.
          STRONG is {TIER_CUTS.strong}+, LEAN is {TIER_CUTS.lean}-{TIER_CUTS.strong - 1}, WATCH is under {TIER_CUTS.lean}.
        </p>
        <p className="mono m-0 text-[11.5px] text-text-secondary">
          On 7,259 graded +EV alerts the tiers realised +25.8% (STRONG), +6.4% (LEAN) and +3.5% (WATCH) flat-stake ROI, paper, each book counted separately.
          The cut points were set on that same sample, so expect them to shrink going forward, and they are not yet validated for model bets.
        </p>
      </div>
    </details>
  )
}
