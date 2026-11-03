'use client'

import { useState } from 'react'
import { ToolHeader } from '@/components/ui/tool-header'
import { americanToImpliedProb, removeVig, probToAmerican, formatProb, formatOdds } from '@/lib/odds'

const B = '1px solid var(--color-separator-border)'
const inputStyle: React.CSSProperties = { width: '100%', background: 'var(--slate)', border: B, color: 'var(--color-text-primary)', fontFamily: 'var(--font-mono, monospace)', fontSize: '13px', padding: '10px 14px', outline: 'none', borderRadius: 'var(--radius)' }
const labelStyle: React.CSSProperties = { fontFamily: 'var(--font-mono, monospace)', fontSize: '10px', color: 'var(--color-text-secondary)', display: 'block', marginBottom: '6px' }

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
    <div className="max-w-[680px] mx-auto py-6 px-5">
      <ToolHeader title="Odds & Vig Calculator" hint="Example values shown; edit them" current="/tools/odds-calculator" />

      <div className="border border-separator-border rounded-control bg-background-primary-default p-6 mb-4">
        <div className="grid grid-cols-2 gap-4">
          <div><label htmlFor="odds1" style={labelStyle}>Side 1 (American)</label><input id="odds1" type="number" value={odds1} onChange={e => setOdds1(e.target.value)} placeholder="-110" style={inputStyle} /></div>
          <div><label htmlFor="odds2" style={labelStyle}>Side 2 (American)</label><input id="odds2" type="number" value={odds2} onChange={e => setOdds2(e.target.value)} placeholder="-110" style={inputStyle} /></div>
        </div>
      </div>

      {result && (
        <div className="border border-separator-border rounded-control bg-background-primary-default overflow-hidden mb-4">
          <div className="grid grid-cols-[repeat(3,1fr)] bg-background-secondary-default" style={{ borderBottom: B }}>
            <div /><div className="mono py-[9px] px-3 text-micro text-text-secondary">Side 1</div>
            <div className="mono py-[9px] px-3 text-micro text-text-secondary">Side 2</div>
          </div>
          {[
            { label: 'Input Odds',   v1: formatOdds(o1),                          v2: formatOdds(o2) },
            { label: 'Implied Prob', v1: formatProb(result.p1),                   v2: formatProb(result.p2) },
            { label: 'Fair Prob',    v1: formatProb(result.fair1),                v2: formatProb(result.fair2) },
            { label: 'Fair Odds',    v1: formatOdds(probToAmerican(result.fair1)),v2: formatOdds(probToAmerican(result.fair2)) },
          ].map((row, i) => (
            <div className="grid grid-cols-[repeat(3,1fr)]" key={row.label} style={{ borderBottom: i < 3 ? B : undefined }}>
              <div className="mono py-2.5 px-3 text-mini text-text-secondary">{row.label}</div>
              <div className="mono py-2.5 px-3 text-compact font-semibold text-text-primary">{row.v1}</div>
              <div className="mono py-2.5 px-3 text-compact font-semibold text-text-primary">{row.v2}</div>
            </div>
          ))}
          <div className="grid grid-cols-3 bg-background-primary-default">
            <div className="mono py-2.5 px-3 text-mini text-text-secondary">Vig</div>
            <div className="mono py-[10px] px-3 text-compact font-semibold text-loss" style={{ gridColumn: 'span 2' }}>{result.vig.toFixed(2)}%</div>
          </div>
        </div>
      )}

      <div className="border border-separator-border rounded-control bg-background-primary-default p-5">
        <div className="mono text-micro text-text-secondary mb-2.5">How it works</div>
        <p className="times text-compact text-text-secondary leading-[1.6] mb-2">Books build a margin (vig) into their lines. Removing the vig reveals the true probability each side wins.</p>
        <p className="times text-compact text-text-secondary leading-[1.6]">If your model assigns a higher probability than the fair probability, you have an edge. Use the <a href="/tools/kelly-calculator" className="text-link underline">Kelly Calculator</a> to size your stake.</p>
      </div>
    </div>
  )
}
