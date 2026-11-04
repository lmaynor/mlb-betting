'use client'

import { ToolHeader } from '@/components/ui/tool-header'
import { useState } from 'react'
import { americanToImpliedProb, removeVig, formatProb } from '@/lib/odds'

const B = '1px solid var(--color-separator-border)'
const inputStyle: React.CSSProperties = { width: '100%', background: 'var(--slate)', border: B, color: 'var(--color-text-primary)', fontFamily: 'var(--font-mono, monospace)', fontSize: '14px', padding: '11px 14px', outline: 'none', borderRadius: 'var(--radius)' }
const labelStyle: React.CSSProperties = { fontFamily: 'var(--font-text), sans-serif', fontSize: '10px', fontWeight: 600, color: 'var(--color-text-secondary)', display: 'block', marginBottom: '8px' }

function americanToDecimal(odds: number): number {
  return odds > 0 ? 1 + odds / 100 : 1 + 100 / Math.abs(odds)
}

export default function BoostEvPage() {
  // boosted price you are offered
  const [boosted, setBoosted] = useState('150')
  // fair value: devig the market's two sides (the normal price of your side + the other side)
  const [sideOdds, setSideOdds]   = useState('110')
  const [otherOdds, setOtherOdds] = useState('-130')

  const bo = parseFloat(boosted), so = parseFloat(sideOdds), oo = parseFloat(otherOdds)
  const validFair = !isNaN(so) && so !== 0 && !isNaN(oo) && oo !== 0
  const validBoost = !isNaN(bo) && bo !== 0

  let fairProb: number | null = null
  if (validFair) {
    fairProb = removeVig(americanToImpliedProb(so), americanToImpliedProb(oo)).fair1
  }

  let ev: number | null = null
  let kellyPct: number | null = null
  if (fairProb != null && validBoost) {
    const dec = americanToDecimal(bo)
    ev = fairProb * dec - 1
    const b = dec - 1
    kellyPct = b > 0 ? Math.max(0, (fairProb * (b + 1) - 1) / b) : 0
  }

  return (
    <div className="max-w-[680px] mx-auto p-6">
      <ToolHeader title="Boost EV Calculator" hint="Example values shown; edit them" current="/tools/boost-ev" />

      <div className="border border-separator-border rounded-control bg-background-primary-default p-6 mb-4">
        <div className="mb-4">
          <label htmlFor="boosted" style={labelStyle}>Boosted line you&apos;re offered (American)</label>
          <input id="boosted" type="number" value={boosted} onChange={e => setBoosted(e.target.value)} placeholder="+150" style={inputStyle} />
          {validBoost && <p className="mono text-mini text-text-secondary mt-1">Implied: {formatProb(americanToImpliedProb(bo))}</p>}
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="sideOdds" style={labelStyle}>Normal price, your side</label>
            <input id="sideOdds" type="number" value={sideOdds} onChange={e => setSideOdds(e.target.value)} placeholder="+110" style={inputStyle} />
          </div>
          <div>
            <label htmlFor="otherOdds" style={labelStyle}>Normal price, other side</label>
            <input id="otherOdds" type="number" value={otherOdds} onChange={e => setOtherOdds(e.target.value)} placeholder="-130" style={inputStyle} />
          </div>
        </div>
        {fairProb != null && (
          <p className="mono text-mini text-text-secondary mt-2">
            Devigged fair win probability: <span className="text-text-primary">{formatProb(fairProb)}</span>
          </p>
        )}
      </div>

      {ev != null && kellyPct != null ? (
        <div className="border border-separator-border rounded-control bg-background-primary-default overflow-hidden">
          <div className="grid grid-cols-2" style={{ borderBottom: B }}>
            <div className="p-5" style={{ borderRight: B }}>
              <div className="ui-heading text-micro text-text-secondary mb-2">Expected value</div>
              <div className="mono text-[28px] font-semibold" style={{ color: ev > 0 ? 'var(--signal)' : 'var(--loss)' }}>
                {ev > 0 ? '+' : ''}{(ev * 100).toFixed(1)}%
              </div>
              <div className="mono text-cozy text-text-secondary mt-1">
                {ev > 0 ? '+' : ''}{(ev * 100).toFixed(1)}&cent; per $1 staked
              </div>
            </div>
            <div className="p-5">
              <div className="ui-heading text-micro text-text-secondary mb-2">Kelly fraction</div>
              <div className="mono text-[28px] font-semibold text-text-primary">{(kellyPct * 100).toFixed(1)}%</div>
              <div className="mono text-cozy text-text-secondary mt-1">of bankroll (full Kelly; bet a quarter of this)</div>
            </div>
          </div>
          <div className="py-3.5 px-5">
            <p className="times text-compact text-text-secondary leading-[1.5]">
              {ev > 0
                ? 'Positive EV: the boosted price pays more than the fair probability implies. Max out the boost limit if the Kelly fraction supports it.'
                : 'Negative EV: even boosted, this price is below fair value. Skip it.'}
            </p>
          </div>
        </div>
      ) : (
        <div className="border border-separator-border rounded-control bg-background-primary-default p-6 text-center text-text-secondary text-cozy">
          Enter the boosted price and two-way market lines above to compute Expected Value and Kelly sizing.
        </div>
      )}
    </div>
  )
}
