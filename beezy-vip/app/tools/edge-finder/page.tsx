'use client'

import { useState } from 'react'
import { americanToImpliedProb, formatProb, formatOdds, probToAmerican } from '@/lib/odds'

const B = '1px solid var(--basalt)'
const inputStyle: React.CSSProperties = { width: '100%', background: 'var(--slate)', border: B, color: 'var(--ash)', fontFamily: 'var(--font-mono, monospace)', fontSize: '13px', padding: '10px 14px', outline: 'none', borderRadius: 'var(--radius)' }
const labelStyle: React.CSSProperties = { fontFamily: 'var(--font-mono, monospace)', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)', display: 'block', marginBottom: '6px' }

export default function EdgeFinderPage() {
  const [bookOdds, setBookOdds]   = useState('')
  const [modelProb, setModelProb] = useState('')

  const o = parseFloat(bookOdds)
  const p = parseFloat(modelProb) / 100
  const valid = !isNaN(o) && !isNaN(p) && p > 0 && p < 1
  const impliedP = !isNaN(o) ? americanToImpliedProb(o) : null
  const edge = valid && impliedP ? ((p - impliedP) * 100) : null

  return (
    <div style={{ maxWidth: '680px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '24px' }}>
        <p className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '6px' }}>Tools</p>
        <h1 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)', marginBottom: '6px' }}>Edge Finder</h1>
        <p className="times" style={{ fontSize: '13px', color: 'var(--fog)' }}>Enter the book line and your model probability. See your edge instantly.</p>
      </div>
      <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', padding: '24px', marginBottom: '16px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          <div>
            <label style={labelStyle}>Book Line (American)</label>
            <input type="number" value={bookOdds} onChange={e => setBookOdds(e.target.value)} placeholder="-115" style={inputStyle} />
            {impliedP && <p className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '4px' }}>Implied: {formatProb(impliedP)}</p>}
          </div>
          <div>
            <label style={labelStyle}>Model Win Prob (%)</label>
            <input type="number" value={modelProb} onChange={e => setModelProb(e.target.value)} placeholder="58.0" min="0" max="100" step="0.1" style={inputStyle} />
          </div>
        </div>
      </div>
      {edge !== null && (
        <div style={{ border: B, padding: '24px', textAlign: 'center' }}>
          <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '8px' }}>Edge</div>
          <div className="mono" style={{ fontSize: '40px', fontWeight: 700, color: edge > 0 ? 'var(--signal)' : 'var(--loss)' }}>
            {edge > 0 ? '+' : ''}{edge.toFixed(1)}%
          </div>
          <div className="times" style={{ fontSize: '12px', color: 'var(--fog)', marginTop: '8px' }}>
            {edge > 4 ? 'Qualifies for Kelly sizing' : edge > 0 ? 'Positive edge — below typical threshold' : 'No edge — do not bet'}
          </div>
        </div>
      )}
    </div>
  )
}
