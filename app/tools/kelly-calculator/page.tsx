'use client'

import { useState } from 'react'
import { kellyStake, formatOdds, americanToImpliedProb, formatProb } from '@/lib/odds'

export default function KellyCalculatorPage() {
  const [bankroll, setBankroll] = useState('1000')
  const [odds,     setOdds]     = useState('')
  const [prob,     setProb]     = useState('')

  const b = parseFloat(bankroll)
  const o = parseFloat(odds)
  const p = parseFloat(prob) / 100  // user enters as percentage

  const valid = !isNaN(b) && b > 0 && !isNaN(o) && !isNaN(p) && p > 0 && p < 1

  const result    = valid ? kellyStake(b, o, p) : null
  const impliedP  = !isNaN(o) ? americanToImpliedProb(o) : null

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
          Kelly Criterion Calculator
        </h1>
        <p className="text-muted text-sm">
          Calculates optimal bet size based on your edge. Enter your bankroll, the line,
          and your estimated win probability.
        </p>
      </div>

      <div className="border border-[var(--border)] p-6 mb-6 space-y-4">
        <div>
          <label className="mono text-xs uppercase tracking-widest text-muted block mb-2">
            Bankroll ($)
          </label>
          <input
            type="number"
            value={bankroll}
            onChange={e => setBankroll(e.target.value)}
            placeholder="1000"
            className={inputClass}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="mono text-xs uppercase tracking-widest text-muted block mb-2">
              Line (American)
            </label>
            <input
              type="number"
              value={odds}
              onChange={e => setOdds(e.target.value)}
              placeholder="-110"
              className={inputClass}
            />
            {impliedP && (
              <p className="mono text-xs text-muted mt-1">
                Implied: {formatProb(impliedP)}
              </p>
            )}
          </div>

          <div>
            <label className="mono text-xs uppercase tracking-widest text-muted block mb-2">
              Your Win Prob (%)
            </label>
            <input
              type="number"
              value={prob}
              onChange={e => setProb(e.target.value)}
              placeholder="55.0"
              min="0"
              max="100"
              step="0.1"
              className={inputClass}
            />
            {impliedP && !isNaN(p) && p > 0 && (
              <p className={`mono text-xs mt-1 ${p > impliedP ? 'text-win' : 'text-loss'}`}>
                Edge: {p > impliedP ? '+' : ''}{((p - impliedP) * 100).toFixed(1)}%
              </p>
            )}
          </div>
        </div>
      </div>

      {result && (
        <div className="border border-[var(--border)]">
          <div className="grid grid-cols-2 border-b border-[var(--border)] bg-[var(--surface)]">
            <div className="px-5 py-4">
              <p className="mono text-xs text-muted uppercase tracking-widest mb-2">Full Kelly</p>
              <p className="mono text-3xl font-extrabold text-accent">{result.fullKelly}%</p>
              <p className="mono text-sm text-win mt-1">${result.stakeFullDollars.toFixed(2)}</p>
            </div>
            <div className="px-5 py-4 border-l border-[var(--border)]">
              <p className="mono text-xs text-muted uppercase tracking-widest mb-2">Half Kelly</p>
              <p className="mono text-3xl font-extrabold text-text">{result.halfKelly}%</p>
              <p className="mono text-sm text-muted mt-1">${result.stakeHalfDollars.toFixed(2)}</p>
            </div>
          </div>

          <div className="px-5 py-4">
            <p className="mono text-xs text-muted leading-relaxed">
              Half Kelly is recommended for most bettors. Full Kelly maximizes long-run growth
              but requires an accurate probability estimate and tolerance for variance.
            </p>
          </div>
        </div>
      )}

      {result && result.fullKelly === 0 && (
        <div className="border border-loss/30 bg-loss/5 px-5 py-4 mt-4">
          <p className="mono text-xs text-loss">
            No edge detected. Kelly criterion returns 0% — do not bet.
          </p>
        </div>
      )}
    </div>
  )
}
