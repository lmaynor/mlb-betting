'use client'

import { useState } from 'react'
import { americanToImpliedProb, removeVig, probToAmerican, formatProb, formatOdds } from '@/lib/odds'

export default function OddsCalculatorPage() {
  const [odds1, setOdds1] = useState('')
  const [odds2, setOdds2] = useState('')

  const o1 = parseFloat(odds1)
  const o2 = parseFloat(odds2)
  const valid = !isNaN(o1) && !isNaN(o2)

  let result = null
  if (valid) {
    const p1 = americanToImpliedProb(o1)
    const p2 = americanToImpliedProb(o2)
    const { fair1, fair2, vig } = removeVig(p1, p2)
    result = { p1, p2, fair1, fair2, vig }
  }

  const inputClass = `
    w-full bg-[var(--surface)] border border-[var(--border)] text-text
    mono text-sm px-4 py-3 focus:outline-none focus:border-accent
    placeholder:text-muted transition-colors
  `

  return (
    <div className="max-w-2xl mx-auto px-4 py-12">
      <div className="mb-8">
        <p className="mono text-xs text-accent uppercase tracking-widest mb-2">Tools</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">
          Odds & Vig Calculator
        </h1>
        <p className="text-muted text-sm">
          Enter American odds for both sides of a market. Calculates implied probability,
          removes the vig, and shows fair value odds.
        </p>
      </div>

      <div className="border border-[var(--border)] p-6 mb-6">
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div>
            <label className="mono text-xs uppercase tracking-widest text-muted block mb-2">
              Side 1 (American)
            </label>
            <input
              type="number"
              value={odds1}
              onChange={e => setOdds1(e.target.value)}
              placeholder="-115"
              className={inputClass}
            />
          </div>
          <div>
            <label className="mono text-xs uppercase tracking-widest text-muted block mb-2">
              Side 2 (American)
            </label>
            <input
              type="number"
              value={odds2}
              onChange={e => setOdds2(e.target.value)}
              placeholder="+105"
              className={inputClass}
            />
          </div>
        </div>
      </div>

      {result && (
        <div className="border border-[var(--border)] overflow-hidden">
          {/* Header */}
          <div className="grid grid-cols-3 border-b border-[var(--border)] bg-[var(--surface)]">
            <div className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted" />
            <div className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted">Side 1</div>
            <div className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted">Side 2</div>
          </div>

          {[
            { label: 'Input Odds',     v1: formatOdds(o1),                     v2: formatOdds(o2) },
            { label: 'Implied Prob',   v1: formatProb(result.p1),              v2: formatProb(result.p2) },
            { label: 'Fair Prob',      v1: formatProb(result.fair1),           v2: formatProb(result.fair2) },
            { label: 'Fair Odds',      v1: formatOdds(probToAmerican(result.fair1)), v2: formatOdds(probToAmerican(result.fair2)) },
          ].map(row => (
            <div key={row.label} className="grid grid-cols-3 border-b border-[var(--border)]">
              <div className="px-4 py-3 mono text-xs text-muted">{row.label}</div>
              <div className="px-4 py-3 mono text-sm font-semibold text-text">{row.v1}</div>
              <div className="px-4 py-3 mono text-sm font-semibold text-text">{row.v2}</div>
            </div>
          ))}

          <div className="grid grid-cols-3 bg-[var(--surface)]">
            <div className="px-4 py-3 mono text-xs text-muted">Vig</div>
            <div className="px-4 py-3 col-span-2 mono text-sm font-semibold text-loss">
              {result.vig.toFixed(2)}%
            </div>
          </div>
        </div>
      )}

      {!valid && odds1 && odds2 && (
        <p className="mono text-xs text-loss mt-4">Enter valid American odds (e.g. -110, +150)</p>
      )}

      {/* Education */}
      <div className="mt-8 border border-[var(--border)] p-5 space-y-3">
        <p className="mono text-xs text-muted uppercase tracking-widest">How It Works</p>
        <p className="text-xs text-muted leading-relaxed">
          Books build a margin (vig) into their lines. A fair coin flip might be priced at -110/-110
          instead of +100/+100 — the 4.5% vig is how they profit. Removing the vig reveals the
          true probability each side wins.
        </p>
        <p className="text-xs text-muted leading-relaxed">
          If your model assigns a higher probability than the fair probability, you have an edge.
          Use the <a href="/tools/kelly-calculator" className="text-accent hover:underline">Kelly Calculator</a> to
          size your stake.
        </p>
      </div>
    </div>
  )
}
