'use client'

import { LineChart, Line, ReferenceLine, ResponsiveContainer, Tooltip } from 'recharts'
import type { SparklinePoint } from '@/lib/betting-api'

const B = '0.5px solid #1f1f24'

function CustomTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload as SparklinePoint
  const pos = d.cum_pnl >= 0
  return (
    <div style={{ background: '#111114', border: B, padding: '6px 10px' }}>
      <div className="mono" style={{ fontSize: '9px', color: '#71717a', marginBottom: '2px' }}>{d.date}</div>
      <div className="mono" style={{ fontSize: '11px', fontWeight: 600, color: pos ? '#10b981' : '#ef4444' }}>
        {pos ? '+' : ''}{(d.cum_pnl / 10).toFixed(2)}u
      </div>
    </div>
  )
}

export function HeroSparkline({ data }: { data: SparklinePoint[] }) {
  if (!data || data.length < 2) return null

  const final   = data[data.length - 1].cum_pnl
  const lineColor = final >= 0 ? '#10b981' : '#ef4444'
  const settled = data.reduce((s, d) => s + (d.daily_pnl !== 0 ? 1 : 0), 0)

  return (
    <div style={{ borderTop: B, padding: '12px 18px 14px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>
          30D cumulative &middot; paper &middot; {settled} settled
        </span>
        <span className="mono" style={{ fontSize: '10px', fontWeight: 600, color: lineColor }}>
          {final >= 0 ? '+' : ''}{(final / 10).toFixed(2)}u
        </span>
      </div>
      <ResponsiveContainer width="100%" height={48}>
        <LineChart data={data} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
          <ReferenceLine y={0} stroke="#2a2a31" strokeDasharray="2 2" />
          <Tooltip content={<CustomTooltip />} />
          <Line
            type="monotone"
            dataKey="cum_pnl"
            stroke={lineColor}
            strokeWidth={1.5}
            dot={false}
            activeDot={{ r: 3, fill: lineColor, strokeWidth: 0 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
