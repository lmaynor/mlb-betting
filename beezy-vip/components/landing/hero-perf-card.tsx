'use client'

import Link from 'next/link'
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, YAxis } from 'recharts'
import type { BankrollPoint, EvHeadline, EvMlbRow } from '@/lib/types'
import { bookDisplay, pickLabel } from '@/lib/tokens'
import { fmtMoney } from '@/lib/ev-math'
import { formatOdds } from '@/lib/odds'
import type { Bet } from '@/lib/types'

export function HeroPerfCard({ headline, curve, signals }: { headline: EvHeadline | null; curve: BankrollPoint[]; signals: EvMlbRow[] }) {
  const pts = curve.slice(-45).map(p => ({ date: p.date, bank: Math.round((headline?.bankroll ?? 1000) + p.ev_kelly_cum) }))
  const roi = headline?.kelly_roi_pct
  const positive = (roi ?? 0) >= 0
  const tone = positive ? '#57f287' : '#ef4444'
  return (
    <div style={{ padding: '24px 22px', display: 'flex', flexDirection: 'column', gap: '16px', background: 'var(--obsidian)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '10px' }}>
        <div>
          <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '8px' }}>+EV ALERTS &middot; 1/4 KELLY &middot; PAPER</div>
          <div className="mono" style={{ fontSize: '30px', fontWeight: 700, color: 'var(--chalk)', lineHeight: 1 }}>
            {headline ? fmtMoney(headline.kelly_ending_bankroll) : '--'}
          </div>
          <div style={{ fontSize: '11.5px', color: 'var(--fog)', marginTop: '6px' }}>from {headline ? fmtMoney(headline.bankroll) : '$1,000'}, {headline ? headline.graded.toLocaleString() : '--'} graded alerts</div>
        </div>
        {roi != null && (
          <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: tone, background: positive ? 'var(--brand-dim)' : 'var(--loss-wash)', border: `1px solid ${positive ? 'var(--brand-border)' : 'var(--loss-border)'}`, borderRadius: '6px', padding: '5px 9px', whiteSpace: 'nowrap' }}>
            {roi > 0 ? '+' : ''}{roi.toFixed(1)}% KELLY ROI
          </span>
        )}
      </div>

      {pts.length > 2 && (
        <div style={{ height: 84 }} aria-label="Cumulative bankroll, last 45 days with results">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={pts} margin={{ top: 4, right: 2, left: 2, bottom: 2 }}>
              <defs><linearGradient id="hpc" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={tone} stopOpacity={0.35} /><stop offset="100%" stopColor={tone} stopOpacity={0} /></linearGradient></defs>
              <YAxis hide domain={['dataMin', 'dataMax']} />
              <ReferenceLine y={headline?.bankroll ?? 1000} stroke="#2e3446" strokeDasharray="2 2" />
              <Tooltip contentStyle={{ background: '#171a24', border: '1px solid #1e2230', borderRadius: 8, fontSize: 12 }} formatter={(v) => fmtMoney(Number(v))} labelStyle={{ color: '#7d8596' }} />
              <Area type="monotone" dataKey="bank" stroke={tone} strokeWidth={1.75} fill="url(#hpc)" dot={false} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      <div>
        <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '8px' }}>LIVE SIGNALS</div>
        {signals.length === 0 ? (
          <div style={{ fontSize: '12.5px', color: 'var(--fog)' }}>No open alerts above +3% right now.</div>
        ) : (
          <div style={{ display: 'grid', gap: '8px' }}>
            {signals.map(r => {
              const b = bookDisplay(r.book)
              const bet = { ...r, system: 'EV', game_pk: r.game_pk ?? 0, model_prob: r.model_prob ?? 0, market_prob: r.market_prob ?? 0, kelly_triggered: true, paper: true } as unknown as Bet
              return (
                <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: '8px', justifyContent: 'space-between', border: '1px solid #1e2230', background: '#11131a', borderRadius: '8px', padding: '8px 10px' }}>
                  <span style={{ fontSize: '12.5px', color: 'var(--chalk)', fontWeight: 600, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pickLabel(bet)}</span>
                  <span style={{ display: 'flex', gap: '8px', alignItems: 'center', flexShrink: 0 }}>
                    <span style={{ fontSize: '10.5px', fontWeight: 700, color: b.color }}>{b.short}</span>
                    <span className="mono" style={{ fontSize: '12px', color: 'var(--ash)' }}>{formatOdds(r.odds)}</span>
                    <span className="mono" style={{ fontSize: '11.5px', fontWeight: 700, color: 'var(--signal)' }}>+{((r.edge ?? 0) * 100).toFixed(1)}%</span>
                  </span>
                </div>
              )
            })}
          </div>
        )}
        <Link href="/ev" style={{ display: 'inline-block', marginTop: '10px', fontSize: '12.5px', color: 'var(--link)' }}>Open the terminal &rarr;</Link>
      </div>
    </div>
  )
}
