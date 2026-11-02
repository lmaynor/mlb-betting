'use client'

import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, YAxis } from 'recharts'
import type { BankrollPoint, EvHeadline } from '@/lib/types'
import { fmtMoney } from '@/lib/ev-math'

export function HeroPerfCard({ headline, curve }: { headline: EvHeadline | null; curve: BankrollPoint[] }) {
  const pts = curve.slice(-45).map(p => ({ date: p.date, bank: Math.round((headline?.bankroll ?? 1000) + p.ev_kelly_cum) }))
  const roi = headline?.kelly_roi_pct
  const positive = (roi ?? 0) >= 0
  const tone = positive ? '#57f287' : '#ef4444'
  return (
    <div className="flex flex-col gap-4 bg-background-secondary-default px-[22px] py-6">
      <div className="flex items-start justify-between gap-2.5">
        <div>
          <div className="dell-heading mb-2 text-text-secondary">PUBLIC LEDGER GROWTH (1/4 KELLY)</div>
          <div className="mono text-[30px] font-bold leading-none text-text-primary">
            {headline ? fmtMoney(headline.kelly_ending_bankroll) : '--'}
          </div>
          <div className="mt-1.5 text-caption-1-regular text-text-secondary">from {headline ? fmtMoney(headline.bankroll) : '$1,000'} starting bankroll{headline?.flat_roi_pct != null ? ` (${headline.flat_roi_pct > 0 ? '+' : ''}${headline.flat_roi_pct.toFixed(1)}% flat-stake ROI)` : ''}</div>
        </div>
        {roi != null && (
          <span className="mono whitespace-nowrap rounded-md border px-[9px] py-[5px] text-caption-1-bold" style={{ color: tone, background: positive ? 'var(--brand-dim)' : 'var(--loss-wash)', borderColor: positive ? 'var(--brand-border)' : 'var(--loss-border)' }}>
            {roi > 0 ? '+' : ''}{roi.toFixed(1)}% KELLY ROI
          </span>
        )}
      </div>

      {pts.length > 2 && (
        <div className="h-[84px]" aria-label="Cumulative bankroll, last 45 days with results">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={pts} margin={{ top: 4, right: 2, left: 2, bottom: 2 }}>
              <defs><linearGradient id="hpc" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={tone} stopOpacity={0.12} /><stop offset="100%" stopColor={tone} stopOpacity={0.12} /></linearGradient></defs>
              <YAxis hide domain={['dataMin', 'dataMax']} />
              <ReferenceLine y={headline?.bankroll ?? 1000} stroke="#2e3446" strokeDasharray="2 2" />
              <Tooltip contentStyle={{ background: '#171a24', border: '1px solid var(--color-separator-border)', borderRadius: 8, fontSize: 12 }} formatter={(v) => fmtMoney(Number(v))} labelStyle={{ color: '#7d8596' }} />
              <Area type="monotone" dataKey="bank" stroke={tone} strokeWidth={1.75} fill="url(#hpc)" dot={false} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

    </div>
  )
}
