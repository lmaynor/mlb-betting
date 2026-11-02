'use client'

import { useId, useState } from 'react'
import Link from 'next/link'
import { SystemSparkline } from './system-sparkline'
import { SYSTEM_PILL } from '@/lib/tokens'
import type { SparklinePoint } from '@/lib/betting-api'
import { cx } from '@/utils/cx'

export interface SystemCard {
  system: string
  name: string
  desc: string
  href: string
  tint: string
  win_rate: number
  roi: number | null
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
  const roiPos = (s.roi ?? 0) >= 0
  const gateCleared = s.total_bets >= 200
  const pill = SYSTEM_PILL[s.system] ?? { bg: 'var(--slate)', color: s.tint, border: '1px solid var(--color-border-button-default)' }

  return (
    <div className="card-hover flex flex-col overflow-hidden rounded-xl border border-separator-border bg-background-primary-default shadow-card">
      {/* Top accent line in the system hue */}
      <div className="h-[3px] opacity-85" style={{ background: s.tint }} />

      {/* Header -- click to expand */}
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-controls={panelId}
        className="flex w-full cursor-pointer items-center justify-between gap-2 border-none bg-transparent px-4 pb-0 pt-3.5 text-left [font:inherit] [color:inherit]"
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className="ui-heading rounded-full px-2 py-[3px] tracking-[0.05em]" style={{ background: pill.bg, color: pill.color, border: pill.border }}>
            {s.system}
          </span>
        </span>
        <span className="flex items-center gap-2">
          <span className="mono shrink-0 text-caption-1-semibold" style={{ color: roiPos ? 'var(--signal)' : 'var(--loss)' }}>
            {s.roi == null ? '--' : `${roiPos ? '+' : ''}${s.roi.toFixed(1)}%`}
          </span>
          <Chevron open={open} color="var(--color-text-secondary)" />
        </span>
      </button>

      {/* Compact body: name + at-a-glance ROI sparkline */}
      <div className="px-4 pb-1.5 pt-2.5">
        <div className="ui-display text-[17px] tracking-[-0.01em]">
          {s.name}
        </div>
        {s.sparkline && <SystemSparkline data={s.sparkline} color={s.tint} positive={roiPos} />}
      </div>

      {/* Expandable detail */}
      <div id={panelId} className="sys-expand" data-open={open}>
        <div className="sys-expand-inner">
          <div className="px-4 pb-4 pt-1">
            <div className="times mb-3.5 text-body-2-regular text-text-secondary">
              {s.desc}
            </div>
            <div className="mb-3.5 grid grid-cols-3 gap-2.5 border-t border-separator-border pt-3">
              {[
                ['Win rate', s.win_rate.toFixed(1) + '%', 'var(--color-text-primary)'] as const,
                ['Bets', String(s.total_bets), 'var(--color-text-primary)'] as const,
                ['Sample', gateCleared ? '200+ bets' : s.total_bets + '/200 bets', gateCleared ? 'var(--color-text-primary)' : 'var(--color-text-secondary)'] as const,
              ].map(([label, val, color]) => (
                <div key={label}>
                  <div className="ui-heading mb-1 tracking-label text-text-secondary">{label}</div>
                  <div className="mono text-caption-1-semibold" style={{ color }}>{val}</div>
                </div>
              ))}
            </div>
            <Link
              href={s.href}
              className="times text-body-2-semibold text-link no-underline hover:underline"
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
