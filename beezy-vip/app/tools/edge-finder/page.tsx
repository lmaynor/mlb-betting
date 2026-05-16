'use client'

import { useState } from 'react'
import { americanToImpliedProb, formatProb, formatOdds, probToAmerican } from '@/lib/odds'

const B = '0.5px solid #1f1f24'
const inputStyle: React.CSSProperties = { width: '100%', background: '#111114', border: B, color: '#f5f5f7', fontFamily: 'var(--font-mono, monospace)', fontSize: '13px', padding: '10px 14px', outline: 'none' }
const labelStyle: React.CSSProperties = { fontFamily: 'var(--font-mono, monospace)', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', display: 'block', marginBottom: '6px' }

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
        <p className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#10b981', marginBottom: '6px' }}>Tools</p>
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '6px' }}>Edge Finder</h1>
        <p style={{ fontSize: '13px', color: '#71717a' }}>Enter the book line and your model probability. See your edge instantly.</p>
      </div>
      <div style={{ border: B, padding: '20px', marginBottom: '16px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          <div>
            <label style={labelStyle}>Book Line (American)</label>
            <input type="number" value={bookOdds} onChange={e => setBookOdds(e.target.value)} placeholder="-115" style={inputStyle} />
            {impliedP && <p className="mono" style={{ fontSize: '11px', color: '#71717a', marginTop: '4px' }}>Implied: {formatProb(impliedP)}</p>}
          </div>
          <div>
            <label style={labelStyle}>Model Win Prob (%)</label>
            <input type="number" value={modelProb} onChange={e => setModelProb(e.target.value)} placeholder="58.0" min="0" max="100" step="0.1" style={inputStyle} />
          </div>
        </div>
      </div>
      {edge !== null && (
        <div style={{ border: B, padding: '24px', textAlign: 'center' }}>
          <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '8px' }}>Edge</div>
          <div className="mono" style={{ fontSize: '40px', fontWeight: 700, color: edge > 0 ? '#10b981' : '#ef4444' }}>
            {edge > 0 ? '+' : ''}{edge.toFixed(1)}%
          </div>
          <div style={{ fontSize: '12px', color: '#71717a', marginTop: '8px' }}>
            {edge > 4 ? 'Qualifies for Kelly sizing' : edge > 0 ? 'Positive edge — below typical threshold' : 'No edge — do not bet'}
          </div>
        </div>
      )}
    </div>
  )
}
