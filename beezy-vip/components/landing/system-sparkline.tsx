'use client'

import { LineChart, Line, ReferenceLine, ResponsiveContainer } from 'recharts'
import type { SparklinePoint } from '@/lib/betting-api'

export function SystemSparkline({ data, color }: { data: SparklinePoint[]; color: string }) {
  if (!data || data.length < 3) return null
  const final = data[data.length - 1].cum_pnl
  const lineColor = final >= 0 ? color : '#ef4444'
  return (
    <div style={{ paddingTop: '10px', borderTop: '0.5px solid #1f1f24' }}>
      <ResponsiveContainer width="100%" height={32}>
        <LineChart data={data} margin={{ top: 2, right: 4, left: 4, bottom: 2 }}>
          <ReferenceLine y={0} stroke="#2a2a31" strokeDasharray="2 2" />
          <Line
            type="monotone"
            dataKey="cum_pnl"
            stroke={lineColor}
            strokeWidth={1.5}
            dot={false}
            activeDot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
