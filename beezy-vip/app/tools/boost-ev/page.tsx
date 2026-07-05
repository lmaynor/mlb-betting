'use client'

import { useState } from 'react'
import { americanToImpliedProb, removeVig, formatProb } from '@/lib/odds'

const B = '1px solid var(--basalt)'
const inputStyle: React.CSSProperties = { width: '100%', background: 'var(--slate)', border: B, color: 'var(--ash)', fontFamily: 'var(--font-mono, monospace)', fontSize: '14px', padding: '11px 14px', outline: 'none', borderRadius: 'var(--radius)' }
const labelStyle: React.CSSProperties = { fontFamily: 'var(--font-text), sans-serif', fontSize: '10px', fontWeight: 600, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)', display: 'block', marginBottom: '8px' }

function americanToDecimal(odds: number): number {
  return odds > 0 ? 1 + odds / 100 : 1 + 100 / Math.abs(odds)
}

export default function BoostEvPage() {
  // boosted price you are offered
  const [boosted, setBoosted] = useState('')
  // fair value: devig the market's two sides (the normal price of your side + the other side)
  const [sideOdds, setSideOdds]   = useState('')
  const [otherOdds, setOtherOdds] = useState('')

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
    <div style={{ maxWidth: '680px', margin: '0 auto', padding: '40px 24px' }}>
      <div style={{ marginBottom: '24px' }}>
        <p className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'var(--fog)', marginBottom: '8px' }}>Tools</p>
        <h1 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)', marginBottom: '8px' }}>Boost EV calculator</h1>
        <p className="times" style={{ fontSize: '15px', color: 'var(--fog)' }}>
          Profit boosts and promo odds are the most reliable +EV a bettor gets. Enter the boosted price and the market&apos;s normal two-sided prices — we devig those into a fair probability and show the boost&apos;s true expected value.
        </p>
      </div>

      <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', padding: '24px', marginBottom: '16px' }}>
        <div style={{ marginBottom: '16px' }}>
          <label style={labelStyle}>Boosted line you&apos;re offered (American)</label>
          <input type="number" value={boosted} onChange={e => setBoosted(e.target.value)} placeholder="+150" style={inputStyle} />
          {validBoost && <p className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '4px' }}>Implied: {formatProb(americanToImpliedProb(bo))}</p>}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          <div>
            <label style={labelStyle}>Normal price, your side</label>
            <input type="number" value={sideOdds} onChange={e => setSideOdds(e.target.value)} placeholder="+110" style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Normal price, other side</label>
            <input type="number" value={otherOdds} onChange={e => setOtherOdds(e.target.value)} placeholder="-130" style={inputStyle} />
          </div>
        </div>
        {fairProb != null && (
          <p className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '8px' }}>
            Devigged fair win probability: <span style={{ color: 'var(--ash)' }}>{formatProb(fairProb)}</span>
          </p>
        )}
      </div>

      {ev != null && kellyPct != null && (
        <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', overflow: 'hidden' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: B }}>
            <div style={{ padding: '20px', borderRight: B }}>
              <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '8px' }}>Expected value</div>
              <div className="mono" style={{ fontSize: '28px', fontWeight: 700, color: ev > 0 ? 'var(--signal)' : 'var(--loss)' }}>
                {ev > 0 ? '+' : ''}{(ev * 100).toFixed(1)}%
              </div>
              <div className="mono" style={{ fontSize: '13px', color: 'var(--fog)', marginTop: '4px' }}>
                {ev > 0 ? '+' : ''}{(ev * 100).toFixed(1)}&cent; per $1 staked
              </div>
            </div>
            <div style={{ padding: '20px' }}>
              <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '8px' }}>Kelly fraction</div>
              <div className="mono" style={{ fontSize: '28px', fontWeight: 700, color: 'var(--ash)' }}>{(kellyPct * 100).toFixed(1)}%</div>
              <div className="mono" style={{ fontSize: '13px', color: 'var(--fog)', marginTop: '4px' }}>of bankroll (full Kelly; bet a quarter of this)</div>
            </div>
          </div>
          <div style={{ padding: '14px 20px' }}>
            <p className="times" style={{ fontSize: '12px', color: 'var(--fog)', lineHeight: 1.5 }}>
              {ev > 0
                ? 'Positive EV: the boosted price pays more than the fair probability implies. Max out the boost limit if the Kelly fraction supports it.'
                : 'Negative EV: even boosted, this price is below fair value. Skip it.'}
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
