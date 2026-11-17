'use client'
/** The standings: one real table. Ranked by bankroll (balance plus stakes still at risk). Shows totals and the settled record,
 *  never a pick. An eliminated player stays on the board, dimmed. */
import { fmtMoney } from '@/lib/contest/odds'
import type { LeaderRow } from '@/lib/contest/types'

const TH: React.CSSProperties = { fontSize: 10, fontWeight: 600, color: '#7d8596', background: '#11131a', padding: '9px 10px', borderBottom: '1px solid #1e2230', position: 'sticky', top: 0, textAlign: 'left', whiteSpace: 'nowrap' }
const TD: React.CSSProperties = { fontSize: 13, color: '#f3f4f6', padding: '10px 10px', borderBottom: '1px solid #151822' }

export function Leaderboard({ rows, start }: { rows: LeaderRow[]; start: number }) {
  return (
    <div style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: 8, overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0 }} aria-label="Leaderboard">
        <thead><tr>
          <th style={{ ...TH, width: 44 }}>#</th><th style={TH}>Player</th>
          <th style={{ ...TH, textAlign: 'right' }}>Bankroll</th><th style={{ ...TH, textAlign: 'right' }}>vs start</th>
          <th className="hidden sm:table-cell" style={{ ...TH, textAlign: 'right' }}>At risk</th><th style={{ ...TH, textAlign: 'right' }}>W-L-P</th>
        </tr></thead>
        <tbody>
          {rows.map(r => {
            const out = r.status === 'eliminated'
            const delta = r.bankrollCents - start
            return (
              <tr key={r.name} className="contest-row" data-mine={r.mine ? 'true' : undefined} style={{ opacity: out ? 0.5 : 1, background: r.mine ? '#13171f' : undefined }}>
                <td className="mono" style={{ ...TD, color: '#7d8596', borderLeft: `2px solid ${r.mine ? 'var(--signal)' : 'transparent'}` }}>{r.rank}</td>
                <td style={TD}>{r.name}{out && <span className="mono" style={{ marginLeft: 8, fontSize: 11, color: '#ef4444' }}>OUT</span>}</td>
                <td className="mono" style={{ ...TD, textAlign: 'right', fontWeight: 600 }}>{fmtMoney(r.bankrollCents)}</td>
                <td className="mono" style={{ ...TD, textAlign: 'right', color: delta >= 0 ? '#57f287' : '#ef4444' }}>{fmtMoney(delta, { sign: true })}</td>
                <td className="mono hidden sm:table-cell" style={{ ...TD, textAlign: 'right', color: '#9ca3af' }}>{r.openRiskCents ? fmtMoney(r.openRiskCents) : '--'}</td>
                <td className="mono" style={{ ...TD, textAlign: 'right', color: '#9ca3af' }}>{r.won}-{r.lost}-{r.pushed}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
