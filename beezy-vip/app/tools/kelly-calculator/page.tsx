'use client'

import { useState } from 'react'
import { kellyStake, formatOdds, americanToImpliedProb, formatProb } from '@/lib/odds'

const B = '1px solid #000'
const B_INNER = '1px solid #1f1f24'
const inputStyle: React.CSSProperties = { width: '100%', background: '#111114', border: B_INNER, color: '#f5f5f7', fontFamily: 'var(--font-mono, monospace)', fontSize: '13px', padding: '10px 14px', outline: 'none', borderRadius: 0 }
const labelStyle: React.CSSProperties = { fontFamily: 'Arial, Helvetica, sans-serif', fontSize: '9px', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#888890', display: 'block', marginBottom: '6px' }

export default function KellyCalculatorPage() {
  const [bankroll, setBankroll] = useState('1000')
  const [odds, setOdds]         = useState('')
  const [prob, setProb]         = useState('')

  const b = parseFloat(bankroll), o = parseFloat(odds), p = parseFloat(prob) / 100
  const valid = !isNaN(b) && b > 0 && !isNaN(o) && !isNaN(p) && p > 0 && p < 1
  const result   = valid ? kellyStake(b, o, p) : null
  const impliedP = !isNaN(o) ? americanToImpliedProb(o) : null

  return (
    <div style={{ maxWidth: '680px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '24px' }}>
        <p className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: '#888890', marginBottom: '6px' }}>Tools</p>
        <h1 className="dell-display" style={{ fontSize: '20px', color: '#f5f5f7', marginBottom: '6px' }}>Kelly Criterion Calculator</h1>
        <p className="times" style={{ fontSize: '13px', color: '#888890' }}>Calculate optimal bet size based on your edge. Enter bankroll, line, and estimated win probability.</p>
      </div>

      <div style={{ border: B, padding: '20px', marginBottom: '16px' }}>
        <div style={{ marginBottom: '16px' }}>
          <label style={labelStyle}>Bankroll ($)</label>
          <input type="number" value={bankroll} onChange={e => setBankroll(e.target.value)} placeholder="1000" style={inputStyle} />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          <div>
            <label style={labelStyle}>Line (American)</label>
            <input type="number" value={odds} onChange={e => setOdds(e.target.value)} placeholder="-110" style={inputStyle} />
            {impliedP && <p className="mono" style={{ fontSize: '11px', color: '#888890', marginTop: '4px' }}>Implied: {formatProb(impliedP)}</p>}
          </div>
          <div>
            <label style={labelStyle}>Your Win Prob (%)</label>
            <input type="number" value={prob} onChange={e => setProb(e.target.value)} placeholder="55.0" min="0" max="100" step="0.1" style={inputStyle} />
            {impliedP && !isNaN(p) && p > 0 && (
              <p className="mono" style={{ fontSize: '11px', marginTop: '4px', color: p > impliedP ? '#b3bd95' : '#d77a7a' }}>
                Edge: {p > impliedP ? '+' : ''}{((p - impliedP) * 100).toFixed(1)}%
              </p>
            )}
          </div>
        </div>
      </div>

      {result && (
        <div style={{ border: B, overflow: 'hidden' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: B_INNER }}>
            <div style={{ padding: '20px', borderRight: B_INNER }}>
              <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: '#888890', marginBottom: '8px' }}>Full Kelly</div>
              <div className="mono" style={{ fontSize: '28px', fontWeight: 700, color: '#b3bd95' }}>{result.fullKelly}%</div>
              <div className="mono" style={{ fontSize: '13px', color: '#b3bd95', marginTop: '4px' }}>${result.stakeFullDollars.toFixed(2)}</div>
            </div>
            <div style={{ padding: '20px' }}>
              <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: '#888890', marginBottom: '8px' }}>Half Kelly</div>
              <div className="mono" style={{ fontSize: '28px', fontWeight: 700, color: '#f5f5f7' }}>{result.halfKelly}%</div>
              <div className="mono" style={{ fontSize: '13px', color: '#888890', marginTop: '4px' }}>${result.stakeHalfDollars.toFixed(2)}</div>
            </div>
          </div>
          <div style={{ padding: '14px 20px' }}>
            <p className="times" style={{ fontSize: '12px', color: '#888890', lineHeight: 1.6 }}>Half Kelly is recommended for most bettors. Full Kelly maximizes long-run growth but requires accurate probability estimates.</p>
          </div>
        </div>
      )}
    </div>
  )
}
