'use client'

import { LineChart, Line, ReferenceLine, ResponsiveContainer, Tooltip } from 'recharts'
import type { SparklinePoint } from '@/lib/betting-api'

const B = '1px solid var(--color-separator-border)'

function CustomTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload as SparklinePoint
  const pos = d.cum_pnl >= 0
  return (
    <div className="bg-background-primary-default py-[6px] px-[10px]" style={{ border: B }}>
      <div className="mono text-micro text-text-secondary mb-0.5">{d.date}</div>
      <div className="mono text-mini font-semibold" style={{ color: pos ? 'var(--signal)' : 'var(--loss)' }}>
        {pos ? '+' : ''}{(d.cum_pnl / 10).toFixed(2)}u
      </div>
    </div>
  )
}

export function HeroSparkline({ data }: { data: SparklinePoint[] }) {
  if (!data || data.length < 2) return null

  const final    = data[data.length - 1].cum_pnl
  const lineColor = final >= 0 ? '#57f287' : '#ef4444'
  const settled  = data.reduce((s, d) => s + (d.daily_pnl !== 0 ? 1 : 0), 0)

  return (
    <div className="pt-3 px-[18px] pb-[14px]" style={{ borderTop: B, gridColumn: '1 / -1' }}>
      <div className="flex justify-between items-center mb-2">
        <span className="ui-heading text-micro tracking-label text-text-secondary">
          30D cumulative &middot; paper &middot; {settled} settled
        </span>
        <span className="mono text-micro font-semibold" style={{ color: lineColor }}>
          {final >= 0 ? '+' : ''}{(final / 10).toFixed(2)}u
        </span>
      </div>
      <ResponsiveContainer width="100%" height={48}>
        <LineChart data={data} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
          <ReferenceLine y={0} stroke="#2e3446" strokeDasharray="2 2" />
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
