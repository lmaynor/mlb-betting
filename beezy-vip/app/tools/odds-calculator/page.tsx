'use client'

import { useState } from 'react'
import { ToolHeader } from '@/components/ui/tool-header'
import { americanToImpliedProb, removeVig, probToAmerican, formatProb, formatOdds } from '@/lib/odds'

const B = '1px solid var(--color-separator-border)'
const inputStyle: React.CSSProperties = { width: '100%', background: 'var(--slate)', border: B, color: 'var(--color-text-primary)', fontFamily: 'var(--font-mono, monospace)', fontSize: '13px', padding: '10px 14px', outline: 'none', borderRadius: 'var(--radius)' }
const labelStyle: React.CSSProperties = { fontFamily: 'var(--font-mono, monospace)', fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--color-text-secondary)', display: 'block', marginBottom: '6px' }

export default function OddsCalculatorPage() {
  const [odds1, setOdds1] = useState('-110')
  const [odds2, setOdds2] = useState('-110')
  const o1 = parseFloat(odds1), o2 = parseFloat(odds2)
  const valid = !isNaN(o1) && !isNaN(o2)
  let result = null
  if (valid) {
    const p1 = americanToImpliedProb(o1), p2 = americanToImpliedProb(o2)
    const { fair1, fair2, vig } = removeVig(p1, p2)
    result = { p1, p2, fair1, fair2, vig }
  }

  return (
    <div className="max-w-[680px] mx-auto py-[24px] px-[20px]">
      <ToolHeader title="Odds & Vig Calculator" hint="Example values shown; edit them" current="/tools/odds-calculator" />

      <div className="border border-[color:var(--color-separator-border)] rounded-[8px] bg-[var(--color-background-primary-default)] p-[24px] mb-[16px]">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          <div><label htmlFor="odds1" style={labelStyle}>Side 1 (American)</label><input id="odds1" type="number" value={odds1} onChange={e => setOdds1(e.target.value)} placeholder="-110" style={inputStyle} /></div>
          <div><label htmlFor="odds2" style={labelStyle}>Side 2 (American)</label><input id="odds2" type="number" value={odds2} onChange={e => setOdds2(e.target.value)} placeholder="-110" style={inputStyle} /></div>
        </div>
      </div>

      {result && (
        <div className="border border-[color:var(--color-separator-border)] rounded-[8px] bg-[var(--color-background-primary-default)] overflow-hidden mb-[16px]">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', background: 'var(--color-background-secondary-default)', borderBottom: B }}>
            <div /><div className="mono py-[9px] px-[12px] text-[10px] tracking-[0.1em] uppercase text-text-secondary">Side 1</div>
            <div className="mono py-[9px] px-[12px] text-[10px] tracking-[0.1em] uppercase text-text-secondary">Side 2</div>
          </div>
          {[
            { label: 'Input Odds',   v1: formatOdds(o1),                          v2: formatOdds(o2) },
            { label: 'Implied Prob', v1: formatProb(result.p1),                   v2: formatProb(result.p2) },
            { label: 'Fair Prob',    v1: formatProb(result.fair1),                v2: formatProb(result.fair2) },
            { label: 'Fair Odds',    v1: formatOdds(probToAmerican(result.fair1)),v2: formatOdds(probToAmerican(result.fair2)) },
          ].map((row, i) => (
            <div key={row.label} style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', borderBottom: i < 3 ? B : undefined }}>
              <div className="mono py-[10px] px-[12px] text-[11px] text-text-secondary">{row.label}</div>
              <div className="mono py-[10px] px-[12px] text-[12px] font-semibold text-text-primary">{row.v1}</div>
              <div className="mono py-[10px] px-[12px] text-[12px] font-semibold text-text-primary">{row.v2}</div>
            </div>
          ))}
          <div className="grid grid-cols-3 bg-[var(--color-background-primary-default)]">
            <div className="mono py-[10px] px-[12px] text-[11px] text-text-secondary">Vig</div>
            <div className="mono" style={{ padding: '10px 12px', fontSize: '12px', fontWeight: 600, color: 'var(--loss)', gridColumn: 'span 2' }}>{result.vig.toFixed(2)}%</div>
          </div>
        </div>
      )}

      <div className="border border-[color:var(--color-separator-border)] rounded-[8px] bg-[var(--color-background-primary-default)] p-[20px]">
        <div className="mono text-[10px] tracking-[0.1em] uppercase text-text-secondary mb-[10px]">How it works</div>
        <p className="times text-[12px] text-text-secondary leading-[1.6] mb-[8px]">Books build a margin (vig) into their lines. Removing the vig reveals the true probability each side wins.</p>
        <p className="times text-[12px] text-text-secondary leading-[1.6]">If your model assigns a higher probability than the fair probability, you have an edge. Use the <a href="/tools/kelly-calculator" className="text-[var(--link)] underline">Kelly Calculator</a> to size your stake.</p>
      </div>
    </div>
  )
}
