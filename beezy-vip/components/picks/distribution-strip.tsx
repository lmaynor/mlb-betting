import React from 'react'

export interface DistributionStripProps {
  mean: number | null
  p10?: number | null
  p90?: number | null
  line?: number | null
  side?: 'over' | 'under' | null
}

export const fmtStrip = (v: number): string =>
  Number.isInteger(v) ? String(v) : Math.abs(v) >= 10 ? v.toFixed(1) : String(Math.round(v * 100) / 100)

/**
 * Visual representation of the model's 80% range (10th to 90th percentile)
 * against the market line and the model expected value (mean).
 */
export function DistributionStrip({ mean, p10, p90, line, side: _side }: DistributionStripProps) {
  if (mean == null || p10 == null || p90 == null) {
    return null
  }

  const lo0 = Math.min(p10, line ?? p10)
  const hi0 = Math.max(p90, line ?? p90)
  const span = hi0 - lo0 || Math.max(Math.abs(mean) * 0.2, 1)
  const lo = lo0 - span * 0.08
  const hi = hi0 + span * 0.08
  const toPct = (v: number) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100))

  const p10Pct = toPct(p10)
  const p90Pct = toPct(p90)
  const meanPct = toPct(mean)
  const linePct = line != null ? toPct(line) : null

  const label = `Model expects ${fmtStrip(mean)}, 10th to 90th percentile ${fmtStrip(p10)} to ${fmtStrip(p90)}${
    line != null ? `; the line is ${fmtStrip(line)}` : ''
  }`

  return (
    <div role="img" aria-label={label} style={{ width: '100%', userSelect: 'none' }}>
      <div style={{ position: 'relative', width: '100%', height: 16, display: 'flex', alignItems: 'center', marginTop: 12 }}>
        {/* Projection value label above the mean dot */}
        <span
          style={{
            position: 'absolute',
            left: `${meanPct}%`,
            top: -13,
            transform: 'translateX(-50%)',
            whiteSpace: 'nowrap',
            fontSize: 10,
            fontFamily: 'monospace',
            color: '#f3f4f6',
          }}
        >
          Proj {fmtStrip(mean)}
        </span>
        {/* Hairline track */}
        <div
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            height: 4,
            background: '#1e2230',
            borderRadius: 2,
          }}
        />
        {/* Highlighted P10-P90 segment */}
        <div
          style={{
            position: 'absolute',
            left: `${p10Pct}%`,
            width: `${Math.max(0, p90Pct - p10Pct)}%`,
            height: 4,
            background: '#2e384d',
            borderRadius: 2,
          }}
        />
        {/* Amber tick for line */}
        {linePct != null && (
          <div
            style={{
              position: 'absolute',
              left: `${linePct}%`,
              width: 3,
              height: 12,
              background: '#f59e0b',
              transform: 'translateX(-50%)',
              borderRadius: 1.5,
              zIndex: 1,
            }}
          />
        )}
        {/* White dot for mean */}
        <div
          style={{
            position: 'absolute',
            left: `${meanPct}%`,
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: '#f3f4f6',
            transform: 'translateX(-50%)',
            zIndex: 2,
          }}
        />
      </div>

      {/* Labels under the track */}
      <div
        style={{
          position: 'relative',
          marginTop: 4,
          fontSize: 10,
          fontFamily: 'monospace',
          color: '#7d8596',
          display: 'flex',
          justifyContent: 'space-between',
        }}
      >
        <span>P10: {fmtStrip(p10)}</span>
        {line != null && linePct != null && (
          <span
            style={{
              position: 'absolute',
              left: `${linePct}%`,
              transform: 'translateX(-50%)',
              whiteSpace: 'nowrap',
            }}
          >
            Line: {fmtStrip(line)}
          </span>
        )}
        <span>P90: {fmtStrip(p90)}</span>
      </div>

      {/* Caption line */}
      <div style={{ fontSize: 10, color: '#7d8596', marginTop: 4 }}>
        Model 80% range (10th to 90th percentile)
      </div>
    </div>
  )
}
