'use client'

import { useState } from 'react'
import { americanToImpliedProb, removeVig, probToAmerican, formatProb, formatOdds } from '@/lib/odds'

const B = '1px solid var(--basalt)'
const inputStyle: React.CSSProperties = { width: '100%', background: 'var(--slate)', border: B, color: 'var(--ash)', fontFamily: 'var(--font-mono, monospace)', fontSize: '13px', padding: '10px 14px', outline: 'none', borderRadius: 'var(--radius)' }
const labelStyle: React.CSSProperties = { fontFamily: 'var(--font-mono, monospace)', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)', display: 'block', marginBottom: '6px' }

export default function OddsCalculatorPage() {
  const [odds1, setOdds1] = useState('')
  const [odds2, setOdds2] = useState('')
  const o1 = parseFloat(odds1), o2 = parseFloat(odds2)
  const valid = !isNaN(o1) && !isNaN(o2)
  let result = null
  if (valid) {
    const p1 = americanToImpliedProb(o1), p2 = americanToImpliedProb(o2)
    const { fair1, fair2, vig } = removeVig(p1, p2)
    result = { p1, p2, fair1, fair2, vig }
  }

  return (
    <div style={{ maxWidth: '680px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '24px' }}>
        <p className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '6px' }}>Tools</p>
        <h1 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)', marginBottom: '6px' }}>Odds &amp; Vig Calculator</h1>
        <p className="times" style={{ fontSize: '13px', color: 'var(--fog)' }}>Enter American odds for both sides. Get implied probability, vig, and fair value odds.</p>
      </div>

      <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', padding: '24px', marginBottom: '16px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          <div><label style={labelStyle}>Side 1 (American)</label><input type="number" value={odds1} onChange={e => setOdds1(e.target.value)} placeholder="-115" style={inputStyle} /></div>
          <div><label style={labelStyle}>Side 2 (American)</label><input type="number" value={odds2} onChange={e => setOdds2(e.target.value)} placeholder="+105" style={inputStyle} /></div>
        </div>
      </div>

      {result && (
        <div style={{ border: B, borderRadius: 'var(--radius-lg)', overflow: 'hidden', marginBottom: '16px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', background: 'var(--obsidian)', borderBottom: B }}>
            <div /><div className="mono" style={{ padding: '9px 12px', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)' }}>Side 1</div>
            <div className="mono" style={{ padding: '9px 12px', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)' }}>Side 2</div>
          </div>
          {[
            { label: 'Input Odds',   v1: formatOdds(o1),                          v2: formatOdds(o2) },
            { label: 'Implied Prob', v1: formatProb(result.p1),                   v2: formatProb(result.p2) },
            { label: 'Fair Prob',    v1: formatProb(result.fair1),                v2: formatProb(result.fair2) },
            { label: 'Fair Odds',    v1: formatOdds(probToAmerican(result.fair1)),v2: formatOdds(probToAmerican(result.fair2)) },
          ].map((row, i) => (
            <div key={row.label} style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', borderBottom: i < 3 ? B : undefined }}>
              <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: 'var(--fog)' }}>{row.label}</div>
              <div className="mono" style={{ padding: '10px 12px', fontSize: '12px', fontWeight: 600, color: 'var(--ash)' }}>{row.v1}</div>
              <div className="mono" style={{ padding: '10px 12px', fontSize: '12px', fontWeight: 600, color: 'var(--ash)' }}>{row.v2}</div>
            </div>
          ))}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', background: 'var(--graphite)' }}>
            <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: 'var(--fog)' }}>Vig</div>
            <div className="mono" style={{ padding: '10px 12px', fontSize: '12px', fontWeight: 600, color: 'var(--loss)', gridColumn: 'span 2' }}>{result.vig.toFixed(2)}%</div>
          </div>
        </div>
      )}

      <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', padding: '20px' }}>
        <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)', marginBottom: '10px' }}>How it works</div>
        <p className="times" style={{ fontSize: '12px', color: 'var(--fog)', lineHeight: 1.6, marginBottom: '8px' }}>Books build a margin (vig) into their lines. Removing the vig reveals the true probability each side wins.</p>
        <p className="times" style={{ fontSize: '12px', color: 'var(--fog)', lineHeight: 1.6 }}>If your model assigns a higher probability than the fair probability, you have an edge. Use the <a href="/tools/kelly-calculator" style={{ color: 'var(--link)', textDecoration: 'underline' }}>Kelly Calculator</a> to size your stake.</p>
      </div>
    </div>
  )
}
