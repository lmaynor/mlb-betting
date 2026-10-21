export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { NbaGamesTable } from '@/components/picks/nba-games-board'
import { SportTabs } from '@/components/picks/sport-tabs'
import { apiGetNbaGames } from '@/lib/betting-api'
import type { NbaGamesBoard } from '@/lib/nba-games'
import { SPORT_PILLARS, STATUS_LABEL } from '@/lib/sports'

type Props = { params: Promise<{ sport: string }> }

export const metadata: Metadata = {
  title: 'NBA Games',
  description: 'NBA spread, total and win probability: our model line beside the market consensus. Informational and unvalidated.',
}

const EMPTY: NbaGamesBoard = {
  sport: 'nba', engine: null, validated: false, status: 'unavailable', stale: true, as_of: null, lines_as_of: null, age_minutes: null,
  lines_age_minutes: null, count: 0, reliable_count: 0, label: null, accuracy: null, games: [],
}

/** /picks/nba/games -- only NBA has a games board; every other sport 404s here. */
export default async function SportGamesRoute({ params }: Props) {
  const { sport } = await params
  if (sport !== 'nba') notFound()
  const pillar = SPORT_PILLARS.nba
  const board = await apiGetNbaGames().catch(() => EMPTY)
  const acc = board.accuracy
  const stale = board.stale && board.status !== 'unavailable'
  return (
    <div>
      <SportTabs sport="nba" active="games" />
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
        <div style={{ marginBottom: '14px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px', flexWrap: 'wrap' }}>
            <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>NBA games</h1>
            <span className="dell-heading" title="Our model line beside the market. It has not beaten the market in testing." style={{ fontSize: '10px', color: pillar.accent, border: `1px solid ${pillar.accent}66`, borderRadius: '6px', padding: '2px 8px' }}>{STATUS_LABEL.shadow}</span>
          </div>
          <span className="mono" style={{ fontSize: '12px', color: stale ? 'var(--warn)' : 'var(--fog)' }}>
            {board.count} {board.count === 1 ? 'game' : 'games'}
            {board.lines_age_minutes != null && <> &middot; lines {Math.round(board.lines_age_minutes)} min old{stale ? ' (stale)' : ''}</>}
          </span>
        </div>
        {acc && (
          <div className="mono" style={{ ...{ background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px' }, padding: '9px 14px', marginBottom: '14px', fontSize: '12px', color: 'var(--silver)', display: 'flex', gap: '6px 18px', flexWrap: 'wrap' }}>
            <span>{acc.games.toLocaleString('en-US')} games, {acc.seasons}</span>
            <span>spread error: market {acc.spread_rmse.market} pts, model {acc.spread_rmse.model}</span>
            <span>total error: market {acc.total_rmse.market}, model {acc.total_rmse.model}</span>
            <span>win log-loss: market {acc.moneyline_logloss.market}, model {acc.moneyline_logloss.model}</span>
          </div>
        )}
        <NbaGamesTable board={board} />
        <p style={{ fontSize: '11.5px', color: 'var(--fog)', margin: '14px 0 0', maxWidth: '90ch', lineHeight: 1.55 }}>
          {board.label ?? 'Our model line beside the market. Informational, not a recommendation.'} {acc?.verdict}
          {' '}A lower error is better. Rows marked Warming up use last season&apos;s ratings. Unvalidated.
        </p>
      </div>
    </div>
  )
}
