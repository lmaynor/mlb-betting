'use client'

import { useId, useState } from 'react'
import Link from 'next/link'
import { SystemSparkline } from './system-sparkline'
import { SYSTEM_PILL } from '@/lib/tokens'
import type { SparklinePoint } from '@/lib/betting-api'

export interface SystemCard {
  system: string
  name: string
  desc: string
  href: string
  tint: string
  win_rate: number
  roi: number
  total_bets: number
  sparkline: SparklinePoint[] | null
}

function Chevron({ open, color }: { open: boolean; color: string }) {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 12 12"
      aria-hidden="true"
      style={{
        transition: 'transform .28s cubic-bezier(.22,1,.36,1)',
        transform: open ? 'rotate(180deg)' : 'rotate(0deg)',
        flexShrink: 0,
      }}
    >
      <path d="M2 4.5 L6 8.5 L10 4.5" fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function SystemTile({ s }: { s: SystemCard }) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const roiPos = s.roi >= 0
  const gateCleared = s.total_bets >= 200
  const pill = SYSTEM_PILL[s.system] ?? { bg: 'var(--slate)', color: s.tint, border: '1px solid var(--iron)' }

  return (
    <div
      className="card-hover"
      style={{ display: 'flex', flexDirection: 'column', background: 'var(--graphite)', border: '1px solid var(--basalt)', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}
    >
      {/* Top accent line in the system hue */}
      <div style={{ height: '3px', background: s.tint, opacity: 0.85 }} />

      {/* Header -- click to expand */}
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-controls={panelId}
        style={{
          background: 'transparent',
          padding: '14px 16px 0',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '8px',
          cursor: 'pointer',
          width: '100%',
          font: 'inherit',
          textAlign: 'left',
          color: 'inherit',
          border: 'none',
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
          <span className="dell-heading" style={{ fontSize: '9.5px', letterSpacing: '0.05em', padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border }}>
            {s.system}
          </span>
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span className="mono" style={{ fontSize: '12px', fontWeight: 600, color: roiPos ? 'var(--signal)' : 'var(--loss)', flexShrink: 0 }}>
            {roiPos ? '+' : ''}{s.roi.toFixed(1)}%
          </span>
          <Chevron open={open} color="var(--fog)" />
        </span>
      </button>

      {/* Compact body: name + at-a-glance ROI sparkline */}
      <div style={{ padding: '10px 16px 6px' }}>
        <div className="dell-display" style={{ fontSize: '17px', color: 'var(--chalk)', letterSpacing: '-0.01em' }}>
          {s.name}
        </div>
        {s.sparkline && <SystemSparkline data={s.sparkline} color={s.tint} positive={s.roi >= 0} />}
      </div>

      {/* Expandable detail */}
      <div id={panelId} className="sys-expand" data-open={open}>
        <div className="sys-expand-inner">
          <div style={{ padding: '4px 16px 16px' }}>
            <div className="times" style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.55, marginBottom: '14px' }}>
              {s.desc}
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3,1fr)',
                gap: '10px',
                paddingTop: '12px',
                borderTop: '1px solid var(--basalt)',
                marginBottom: '14px',
              }}
            >
              {[
                ['Win rate', s.win_rate.toFixed(1) + '%', 'var(--chalk)'] as const,
                ['Bets', String(s.total_bets), 'var(--chalk)'] as const,
                ['Gate', gateCleared ? 'CLEARED' : s.total_bets + '/200', gateCleared ? 'var(--signal)' : 'var(--fog)'] as const,
              ].map(([label, val, color]) => (
                <div key={label}>
                  <div className="dell-heading" style={{ fontSize: '8.5px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '4px' }}>{label}</div>
                  <div className="mono" style={{ fontSize: '12px', fontWeight: 600, color }}>{val}</div>
                </div>
              ))}
            </div>
            <Link
              href={s.href}
              className="times"
              style={{ fontSize: '13px', fontWeight: 600, color: 'var(--link)', textDecoration: 'none' }}
            >
              View picks &rarr;
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}

export function ModelsGridClient({ systems }: { systems: SystemCard[] }) {
  return (
    <div className="systems-grid">
      {systems.map(s => (
        <SystemTile key={s.system} s={s} />
      ))}
    </div>
  )
}
