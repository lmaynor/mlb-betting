export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { LockedNotice } from '@/components/picks/locked-notice'
import { MlbProjectionsBoard } from '@/components/picks/mlb-projections-board'
import { MlbTabs } from '@/components/picks/mlb-tabs'
import { SystemsFooter } from '@/components/picks/systems-footer'
import { limitRows } from '@/lib/access'
import { getViewer } from '@/lib/auth'
import { apiGetStats, apiGetTodayAllPredictions } from '@/lib/betting-api'
import { formatDateKey, siteDateKey } from '@/lib/dates'
import { buildMlbProjections } from '@/lib/mlb-projections'
import type { Bet } from '@/lib/types'

export const metadata: Metadata = {
  title: 'MLB Projections',
  description: "The model's projection for every MLB player prop on today's slate: strikeouts, outs, total bases, hits, stolen bases and earned runs, beside the line and the best price.",
}

export default async function MlbProjectionsPage() {
  const date = siteDateKey()
  const [bets, stats, viewer] = await Promise.all([
    apiGetTodayAllPredictions().catch(() => [] as Bet[]),
    apiGetStats().then(r => r.bySystem).catch(() => []),
    getViewer(),
  ])
  // The MLB player board is the paid surface (lib/access.ts), cut here so withheld rows are never in the payload.
  const all = buildMlbProjections(bets)
  const board = limitRows(all, viewer, 'player_board', 'mlb', r => r.score)

  return (
    <div>
      <MlbTabs active="projections" />
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '14px' }}>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>MLB projections</h1>
          <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>
            {formatDateKey(date, { weekday: 'short', month: 'short', day: 'numeric' })} &middot; player props, the model&rsquo;s expected value beside the line
          </span>
        </div>
        <MlbProjectionsBoard rows={board.rows} />
        <LockedNotice locked={board.locked} shown={board.rows.length} what="MLB projections" plan="The full board is on the MLB Pass or All-Access Pro." />
        <SystemsFooter kind="player" stats={stats} />
      </div>
    </div>
  )
}
