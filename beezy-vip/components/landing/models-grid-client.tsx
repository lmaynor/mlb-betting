'use client'

import { useId, useState } from 'react'
import Link from 'next/link'
import { SystemSparkline } from './system-sparkline'
import type { SparklinePoint } from '@/lib/betting-api'

const B = '1px solid #000'

export interface SystemCard {
  system: string
  name: string
  desc: string
  href: string
  tint: string
  tintBg: string
  border: string
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
      <path d="M2 4.5 L6 8.5 L10 4.5" fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="square" />
    </svg>
  )
}

function SystemTile({ s, cellBorder }: { s: SystemCard; cellBorder: React.CSSProperties }) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const roiPos = s.roi >= 0
  const gateCleared = s.total_bets >= 200

  return (
    <div style={{ ...cellBorder, display: 'flex', flexDirection: 'column', background: s.tintBg }}>
      {/* Title bar -- click to expand */}
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-controls={panelId}
        className="card-hover"
        style={{
          background: '#0a0a0c',
          borderBottom: B,
          padding: '7px 12px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '8px',
          cursor: 'pointer',
          width: '100%',
          font: 'inherit',
          textAlign: 'left',
          color: 'inherit',
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
          <Chevron open={open} color="#888890" />
          <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.06em', color: s.tint }}>
            {s.system}
          </span>
        </span>
        <span className="mono" style={{ fontSize: '11px', fontWeight: 600, color: roiPos ? '#b3bd95' : '#d77a7a', flexShrink: 0 }}>
          {roiPos ? '+' : ''}{s.roi.toFixed(1)}%
        </span>
      </button>

      {/* Always-visible compact body: name + at-a-glance ROI sparkline */}
      <div style={{ padding: '12px 12px 4px' }}>
        <div className="dell-heading" style={{ fontSize: '11px', color: '#f5f5f7', letterSpacing: '0.02em' }}>
          {s.name}
        </div>
        {s.sparkline && <SystemSparkline data={s.sparkline} color={s.tint} positive={s.roi >= 0} />}
      </div>

      {/* Expandable detail: description + stats + link */}
      <div id={panelId} className="sys-expand" data-open={open}>
        <div className="sys-expand-inner">
          <div style={{ padding: '4px 12px 14px' }}>
            <div className="times" style={{ fontSize: '12px', color: '#a1a1aa', lineHeight: 1.5, marginBottom: '12px' }}>
              {s.desc}
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3,1fr)',
                gap: '8px',
                paddingTop: '10px',
                borderTop: '1px solid rgba(255,255,255,0.08)',
                marginBottom: '12px',
              }}
            >
              {[
                ['WR', s.win_rate.toFixed(1) + '%', s.tint] as const,
                ['Bets', String(s.total_bets), s.tint] as const,
                ['Gate', gateCleared ? 'CLEARED' : s.total_bets + '/200', gateCleared ? '#b3bd95' : '#888890'] as const,
              ].map(([label, val, color]) => (
                <div key={label}>
                  <div className="dell-heading" style={{ fontSize: '8px', letterSpacing: '0.1em', color: '#888890' }}>{label}</div>
                  <div className="mono" style={{ fontSize: '11px', fontWeight: 600, color }}>{val}</div>
                </div>
              ))}
            </div>
            <Link
              href={s.href}
              className="dell-heading"
              style={{ fontSize: '9px', letterSpacing: '0.08em', color: '#9999ff', textDecoration: 'none' }}
            >
              VIEW PICKS &rarr;
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}

export function ModelsGridClient({ systems }: { systems: SystemCard[] }) {
  const columns = 3
  const rows = Math.ceil(systems.length / columns)

  return (
    <div className="systems-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', border: B }}>
      {systems.map((s, i) => {
        const col = i % columns
        const row = Math.floor(i / columns)
        const cellBorder: React.CSSProperties = {
          borderRight: col < columns - 1 ? B : undefined,
          borderBottom: row < rows - 1 ? B : undefined,
        }
        return <SystemTile key={s.system} s={s} cellBorder={cellBorder} />
      })}
    </div>
  )
}
