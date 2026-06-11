'use client'

import { useId } from 'react'
import { Area, AreaChart, ReferenceLine, ResponsiveContainer } from 'recharts'
import type { SparklinePoint } from '@/lib/betting-api'

// `color` (system tint) is accepted for API compatibility but the line is
// intentionally green-for-positive / red-for-negative so profitability reads
// at a glance, independent of each system's tint.
export function SystemSparkline({ data, positive }: { data: SparklinePoint[]; color?: string; positive?: boolean }) {
  const rawId = useId()
  if (!data || data.length < 3) return null

  // Prefer the caller's ROI sign so the line color always agrees with the
  // ROI % shown in the card header; fall back to the trajectory's final value.
  const final = data[data.length - 1].cum_pnl
  const isPositive = positive ?? final >= 0
  const tone = isPositive ? '#b3bd95' : '#d77a7a'
  const gid = 'spk' + rawId.replace(/:/g, '')

  return (
    <div style={{ paddingTop: '10px', borderTop: '1px solid #1f1f24' }}>
      <ResponsiveContainer width="100%" height={38}>
        <AreaChart data={data} margin={{ top: 3, right: 4, left: 4, bottom: 2 }}>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={tone} stopOpacity={0.34} />
              <stop offset="100%" stopColor={tone} stopOpacity={0} />
            </linearGradient>
          </defs>
          <ReferenceLine y={0} stroke="#3a3a42" strokeDasharray="2 2" />
          <Area
            type="monotone"
            dataKey="cum_pnl"
            stroke={tone}
            strokeWidth={1.75}
            fill={`url(#${gid})`}
            dot={false}
            activeDot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
