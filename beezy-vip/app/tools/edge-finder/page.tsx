'use client'

import { useState } from 'react'
import { americanToImpliedProb, formatProb, formatOdds, probToAmerican } from '@/lib/odds'

export default function EdgeFinderPage() {
  const [odds, setOdds] = useState('')

  const o        = parseFloat(odds)
  const valid    = !isNaN(o)
  const implied  = valid ? americanToImpliedProb(o) : null

  return (
    <div className="max-w-2xl mx-auto px-4 py-12">
      <div className="mb-8">
        <p className="mono text-xs text-accent uppercase tracking-widest mb-2">Tools</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">Edge Finder</h1>
        <p className="text-muted text-sm">
          Enter the line you see at your book. Get implied probability instantly.
          Pro members also see the Beezy model number and edge percentage.
        </p>
      </div>

      <div className="border border-[var(--border)] p-6 mb-6">
        <label className="mono text-xs uppercase tracking-widest text-muted block mb-2">
          Your Line (American Odds)
        </label>
        <input
          type="number"
          value={odds}
          onChange={e => setOdds(e.target.value)}
          placeholder="-115"
          className="w-full bg-[var(--surface)] border border-[var(--border)] text-text mono text-sm px-4 py-3 focus:outline-none focus:border-accent placeholder:text-muted transition-colors"
        />
      </div>

      {implied !== null && (
        <div className="border border-[var(--border)] mb-6">
          <div className="grid grid-cols-2 border-b border-[var(--border)]">
            <div className="px-5 py-4 border-r border-[var(--border)]">
              <p className="mono text-xs text-muted uppercase tracking-widest mb-2">Implied Probability</p>
              <p className="mono text-3xl font-extrabold text-text">{formatProb(implied)}</p>
              <p className="mono text-xs text-muted mt-1">With vig</p>
            </div>
            <div className="px-5 py-4">
              <p className="mono text-xs text-muted uppercase tracking-widest mb-2">Your Odds</p>
              <p className="mono text-3xl font-extrabold text-text">{formatOdds(o)}</p>
              <p className="mono text-xs text-muted mt-1">American</p>
            </div>
          </div>

          {/* Beezy model prob — gated */}
          <div className="px-5 py-4 bg-[var(--surface)]">
            <div className="flex items-center justify-between">
              <div>
                <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Beezy Model Probability</p>
                <div className="flex items-center gap-3">
                  <span
                    className="mono text-2xl font-extrabold text-muted blur-sm select-none"
                    aria-hidden
                  >
                    {formatProb(implied + 0.05)}
                  </span>
                  <span className="mono text-xs border border-[var(--border)] px-2 py-0.5 text-muted">
                    Pro only
                  </span>
                </div>
              </div>
              <a
                href="/signup"
                className="mono text-xs uppercase tracking-widest px-4 py-2.5 bg-accent text-bg font-semibold hover:bg-accent/90 transition-colors"
              >
                Unlock
              </a>
            </div>
          </div>
        </div>
      )}

      {/* How to use */}
      <div className="border border-[var(--border)] p-5 space-y-3">
        <p className="mono text-xs text-muted uppercase tracking-widest">How to Use</p>
        <p className="text-xs text-muted leading-relaxed">
          Enter the line you see at DraftKings or your book of choice. The tool converts
          it to implied probability — what the market says the true win probability is.
        </p>
        <p className="text-xs text-muted leading-relaxed">
          If Beezy&apos;s model probability is higher than the implied probability, that&apos;s positive
          expected value. Use the{' '}
          <a href="/tools/kelly-calculator" className="text-accent hover:underline">Kelly Calculator</a>{' '}
          to size your stake.
        </p>
      </div>
    </div>
  )
}
