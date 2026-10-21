'use client'

import { Fragment, useState } from 'react'
import { americanText, lineText, pctText, READY_AFTER_GAMES, signedPts, tipText, type NbaGame, type NbaGamesBoard, type NbaLadderPoint } from '@/lib/nba-games'
import { SURFACE_CARD, TABLE_TD, TABLE_TH } from '@/lib/tokens'

const TH = { ...TABLE_TH, textAlign: 'left', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
const TD = { ...TABLE_TD, verticalAlign: 'middle', whiteSpace: 'nowrap' } as const
const NUM = { ...TD, textAlign: 'right' } as const
const HEADS: { label: string; tip?: string; right?: boolean }[] = [
  { label: 'GAME' }, { label: 'TIP (CT)' },
  { label: 'SPREAD MKT', right: true, tip: 'Market consensus spread, home team handicap.' },
  { label: 'OURS', right: true, tip: 'Our fair spread for the home team.' },
  { label: 'GAP', right: true, tip: 'Our expected home margin minus the market margin, in points. Positive: our model likes the home team more.' },
  { label: 'TOTAL MKT', right: true }, { label: 'OURS', right: true },
  { label: 'GAP', right: true, tip: 'Our total minus the market total, in points.' },
  { label: 'HOME WIN MKT', right: true, tip: 'Market consensus win probability for the home team, vig removed.' },
  { label: 'OURS', right: true },
  { label: 'BOOKS', right: true, tip: 'Books quoting the moneyline.' },
  { label: 'RATINGS', tip: `Ratings are last season's until both teams have played ${READY_AFTER_GAMES} games. Until then the gaps say little.` },
]

function Ladder({ title, points, kind }: { title: string; points: NbaLadderPoint[]; kind: 'spread' | 'total' }) {
  if (!points.length) return null
  return (
    <div style={{ flex: '1 1 320px', minWidth: 0 }}>
      <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>{title}</div>
      <div className="mono" style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', fontSize: '12px', color: 'var(--silver)' }}>
        {points.map(p => (
          <span key={p.line} title="Our probability and the fair (no-vig) price at this line">
            {kind === 'spread' ? lineText(p.line) : p.line} &middot; {pctText(p.p)} &middot; {americanText(p.fair_american)}
          </span>
        ))}
      </div>
    </div>
  )
}

function GameRow({ g, open, toggle }: { g: NbaGame; open: boolean; toggle: () => void }) {
  const dim = g.reliable ? 'var(--chalk)' : 'var(--fog)'
  return (
    <Fragment>
      <tr onClick={toggle} style={{ cursor: 'pointer' }} aria-expanded={open}>
        <td style={{ ...TD, color: 'var(--chalk)', fontWeight: 600 }}>{g.away} @ {g.home}</td>
        <td style={{ ...TD, color: 'var(--fog)' }}>{tipText(g.start)}</td>
        <td className="mono" style={NUM}>{g.home} {lineText(g.market.spread_home)}</td>
        <td className="mono" style={NUM}>{g.home} {lineText(g.model.spread_home)}</td>
        <td className="mono" style={{ ...NUM, color: dim }}>{signedPts(g.diff.margin_pts)}</td>
        <td className="mono" style={NUM}>{g.market.total ?? '--'}</td>
        <td className="mono" style={NUM}>{g.model.total}</td>
        <td className="mono" style={{ ...NUM, color: dim }}>{signedPts(g.diff.total_pts)}</td>
        <td className="mono" style={NUM}>{pctText(g.market.home_win_p)}</td>
        <td className="mono" style={NUM}>{pctText(g.model.home_win_p)}</td>
        <td className="mono" style={{ ...NUM, color: 'var(--fog)' }}>{g.n_books}</td>
        <td style={{ ...TD, color: g.reliable ? 'var(--silver)' : 'var(--warn)', fontSize: '12px' }}
          title={g.games_min != null ? `fewest games played by either team this season: ${g.games_min}` : undefined}>
          {g.reliable ? 'Ready' : 'Warming up'}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={HEADS.length} style={{ ...TD, whiteSpace: 'normal', background: '#0d0f15' }}>
            <div style={{ display: 'flex', gap: '16px 32px', flexWrap: 'wrap', padding: '4px 0' }}>
              <Ladder title={`FAIR ${g.home} SPREAD LADDER: line, our cover probability, fair price`} points={g.ladders.spread_home} kind="spread" />
              <Ladder title="FAIR TOTAL LADDER: over line, our over probability, fair price" points={g.ladders.total} kind="total" />
            </div>
            <div style={{ fontSize: '11.5px', color: 'var(--fog)', marginTop: '8px', lineHeight: 1.5 }}>
              These are our fair prices at alternate lines. No book price is shown: we do not have alternate-line prices to compare them to.
              {g.market.pinnacle_home_win_p != null && <> Pinnacle has {g.home} at {pctText(g.market.pinnacle_home_win_p)}.</>}
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  )
}

/**
 * The NBA games table: market consensus beside our model line, one real table, detail as a colSpan sub-row. Nothing here is a
 * recommendation: the payload has no edge or EV, and the footnote states how the model did against the market.
 */
export function NbaGamesTable({ board }: { board: NbaGamesBoard }) {
  const [openKey, setOpenKey] = useState<string | null>(null)
  if (board.games.length === 0) {
    return (
      <div style={{ ...SURFACE_CARD, padding: '28px 20px', textAlign: 'center' }}>
        <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>Nothing to show</div>
        <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '62ch', margin: '0 auto', lineHeight: 1.55 }}>
          {board.status === 'unavailable'
            ? 'The games board has not been produced yet.'
            : 'No upcoming games have market lines from at least two books yet. The board fills in once lines are posted.'}
        </div>
      </div>
    )
  }
  return (
    <div style={{ ...SURFACE_CARD, overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '980px', fontSize: '13px' }}>
        <thead>
          <tr>{HEADS.map((h, i) => <th key={`${h.label}${i}`} className="dell-heading" title={h.tip} style={{ ...TH, textAlign: h.right ? 'right' : 'left' }}>{h.label}</th>)}</tr>
        </thead>
        <tbody>
          {board.games.map(g => (
            <GameRow key={g.game_key} g={g} open={openKey === g.game_key} toggle={() => setOpenKey(k => (k === g.game_key ? null : g.game_key))} />
          ))}
        </tbody>
      </table>
    </div>
  )
}
