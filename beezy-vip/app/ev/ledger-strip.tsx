import Link from 'next/link'
import type { EvHeadline } from '@/lib/types'
import { fmtMoney, fmtPct } from '@/lib/ev-math'
import { SURFACE_CARD } from '@/lib/tokens'

const HAIR = '#1e2230'

function Metric({ label, value, tone, sub }: { label: string; value: React.ReactNode; tone?: string; sub?: React.ReactNode }) {
  return (
    <div style={{ padding: '10px 18px', borderRight: `1px solid ${HAIR}`, display: 'grid', gap: '3px', alignContent: 'center', minWidth: 0 }}>
      <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>{label}</span>
      <span className="mono" style={{ fontSize: '16px', fontWeight: 700, color: tone ?? 'var(--chalk)', lineHeight: 1.1, whiteSpace: 'nowrap' }}>{value}</span>
      {sub != null && <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)', whiteSpace: 'nowrap' }}>{sub}</span>}
    </div>
  )
}

/** The MLB +EV paper track record (the ledger the old +EV Terminal carried) as one strip above the filters. */
export function LedgerStrip({ h }: { h: EvHeadline | null }) {
  if (!h) return null
  const tone = (x: number | null) => ((x ?? 0) >= 0 ? 'var(--signal)' : 'var(--loss)')
  const roi = (x: number | null) => (x == null ? '--' : `${x > 0 ? '+' : ''}${x.toFixed(1)}%`)
  const decided = h.wins + h.losses
  const winPct = decided > 0 ? (h.wins / decided) * 100 : null
  return (
    <section aria-label="Paper track record" style={{ ...SURFACE_CARD, overflow: 'hidden', marginBottom: '10px' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap' }}>
        <Metric label="+EV RECORD (MLB)" value={`${h.wins.toLocaleString()}-${h.losses.toLocaleString()}${h.pushes ? `-${h.pushes}` : ''}`}
          sub={`${winPct != null ? `${winPct.toFixed(1)}% win \u00b7 ` : ''}${h.graded.toLocaleString()} graded`} />
        <Metric label="FLAT STAKES" value={fmtMoney(h.flat_pnl_dollars, true)} tone={tone(h.flat_roi_pct)} sub={`${roi(h.flat_roi_pct)} ROI`} />
        <Metric label="1/4 KELLY" value={fmtMoney(h.kelly_ending_bankroll)} tone={tone(h.kelly_roi_pct)} sub={`from ${fmtMoney(h.bankroll)} \u00b7 ${roi(h.kelly_roi_pct)} ROI`} />
        <details style={{ marginLeft: 'auto', padding: '10px 18px', alignSelf: 'center', flex: '1 1 260px', minWidth: 0 }}>
          <summary style={{ fontSize: '11.5px', color: 'var(--silver)', cursor: 'pointer', textAlign: 'right' }}>Methodology &amp; audit notes</summary>
          <p className="times" style={{ margin: '10px 0 0', fontSize: '12px', color: 'var(--silver)', lineHeight: 1.6 }}>
            Paper results only: no slippage, limits or line movement, and the same prop flagged at several books counts several
            times ({h.unique_props_graded.toLocaleString()} unique props behind {h.graded.toLocaleString()} graded alerts). Voids are excluded
            from ROI; Kelly is {fmtPct(h.kelly_cap, 0)} per-bet capped and non-compounding. Rows on this screen are unvalidated market
            signals and are not part of this record. Per-book results on the{' '}
            <Link href="/analytics?tab=bankroll" style={{ color: 'var(--link)' }}>analytics page</Link>.
          </p>
        </details>
      </div>
    </section>
  )
}
