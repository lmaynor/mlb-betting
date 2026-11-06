'use client'

import { useState } from 'react'
import { ToolHeader } from '@/components/ui/tool-header'
import { useBankroll } from '@/lib/use-bankroll'
import { kellyStake, americanToImpliedProb, formatProb } from '@/lib/odds'

const B = '1px solid var(--color-separator-border)'
const B_INNER = '1px solid var(--color-separator-border)'
const inputStyle: React.CSSProperties = { width: '100%', background: 'var(--slate)', border: B, color: 'var(--color-text-primary)', fontFamily: 'var(--font-mono, monospace)', fontSize: '14px', padding: '11px 14px', outline: 'none', borderRadius: 'var(--radius)' }
const labelStyle: React.CSSProperties = { fontFamily: 'var(--font-text), sans-serif', fontSize: '10px', fontWeight: 600, color: 'var(--color-text-secondary)', display: 'block', marginBottom: '8px' }

export default function KellyCalculatorPage() {
  // Shared with the +EV terminal: editing it here updates the stake sizing there (stored per browser).
  const [sharedBankroll, setSharedBankroll] = useBankroll(1000)
  const [typed, setTyped] = useState<string | null>(null)
  const bankroll = typed ?? String(sharedBankroll)
  const setBankroll = (v: string) => { setTyped(v); const n = parseFloat(v); if (isFinite(n) && n > 0) setSharedBankroll(n) }
  const [odds, setOdds]         = useState('-110')
  const [prob, setProb]         = useState('55')

  const b = parseFloat(bankroll), o = parseFloat(odds), p = parseFloat(prob) / 100
  const valid = !isNaN(b) && b > 0 && !isNaN(o) && !isNaN(p) && p > 0 && p < 1
  const result   = valid ? kellyStake(b, o, p) : null
  const impliedP = !isNaN(o) ? americanToImpliedProb(o) : null

  return (
    <div className="max-w-[680px] mx-auto p-6">
      <ToolHeader title="Kelly Calculator" hint="Example values shown; edit them" current="/tools/kelly-calculator" />

      <div className="border border-separator-border rounded-control bg-background-primary-default p-6 mb-4">
        <div className="mb-4">
          <label style={labelStyle}>Bankroll ($)</label>
          <input type="number" value={bankroll} onChange={e => setBankroll(e.target.value)} placeholder="1000" style={inputStyle} />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label style={labelStyle}>Line (American)</label>
            <input type="number" value={odds} onChange={e => setOdds(e.target.value)} placeholder="-110" style={inputStyle} />
            {impliedP && <p className="mono text-mini text-text-secondary mt-1">Implied: {formatProb(impliedP)}</p>}
          </div>
          <div>
            <label style={labelStyle}>Your Win Prob (%)</label>
            <input type="number" value={prob} onChange={e => setProb(e.target.value)} placeholder="55.0" min="0" max="100" step="0.1" style={inputStyle} />
            {impliedP && !isNaN(p) && p > 0 && (
              <p className="mono text-mini mt-1" style={{ color: p > impliedP ? 'var(--signal)' : 'var(--loss)' }}>
                Edge: {p > impliedP ? '+' : ''}{((p - impliedP) * 100).toFixed(1)}%
              </p>
            )}
          </div>
        </div>
      </div>

      {result && (
        <div className="border border-separator-border rounded-control bg-background-primary-default overflow-hidden">
          <div className="grid grid-cols-2" style={{ borderBottom: B_INNER }}>
            <div className="p-5" style={{ borderRight: B_INNER }}>
              <div className="ui-heading text-micro text-text-secondary mb-2">Full Kelly</div>
              <div className="mono text-[28px] font-semibold text-signal">{result.fullKelly}%</div>
              <div className="mono text-cozy text-signal mt-1">${result.stakeFullDollars.toFixed(2)}</div>
            </div>
            <div className="p-5">
              <div className="ui-heading text-micro text-text-secondary mb-2">Half Kelly</div>
              <div className="mono text-[28px] font-semibold text-text-primary">{result.halfKelly}%</div>
              <div className="mono text-cozy text-text-secondary mt-1">${result.stakeHalfDollars.toFixed(2)}</div>
            </div>
          </div>
          <div className="py-3.5 px-5">
            <p className="times text-compact text-text-secondary leading-[1.6]">Half Kelly is recommended for most bettors. Full Kelly maximizes long-run growth but requires accurate probability estimates. New to the formula? The <a href="/learn/kelly-criterion-sports-betting" className="text-link underline">Kelly Criterion guide</a> works a full example.</p>
          </div>
        </div>
      )}
    </div>
  )
}
