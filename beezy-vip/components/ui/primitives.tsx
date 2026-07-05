'use client'

import { beezyscore, scoreTier, TIER_COLOR, TIER_LABEL } from '@/lib/beezy-score'
import { SYSTEM_PILL, systemLabel } from '@/lib/tokens'
import type { Bet } from '@/lib/types'

const FALLBACK_PILL = { bg: 'color-mix(in oklab, #c9c6cf 10%, #04040b)', color: '#c9c6cf', border: '1px solid var(--iron)' }

export function LiveDot({ label }: { label?: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
      <span className="live-dot" />
      {label && (
        <span
          className="dell-heading"
          style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'var(--fog)' }}
        >
          {label}
        </span>
      )}
    </span>
  )
}

export function SystemBadge({ system }: { system: string }) {
  const p = SYSTEM_PILL[system] ?? FALLBACK_PILL
  const label = systemLabel(system, true)
  return (
    <span
      className="dell-heading"
      style={{ fontSize: '9.5px', letterSpacing: '0.05em', padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: p.bg, color: p.color, border: p.border, display: 'inline-block' }}
    >
      {label}
    </span>
  )
}

export function StatCard({ label, value, sub, accent = false }: { label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', padding: '20px', border: '1px solid var(--basalt)', borderRadius: 'var(--radius-lg)', background: 'var(--graphite)' }}>
      <span
        className="dell-heading"
        style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '2px' }}
      >
        {label}
      </span>
      <span
        className="mono"
        style={{ fontSize: '28px', fontWeight: 600, lineHeight: 1, color: accent ? 'var(--signal)' : 'var(--chalk)' }}
      >
        {value}
      </span>
      {sub && <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>{sub}</span>}
    </div>
  )
}

export function ResultPill({ result }: { result: string | null }) {
  if (!result || result === 'pending') {
    return <span className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.06em', color: 'var(--fog)' }}>PENDING</span>
  }
  const isWin  = result === 'win'
  const isPush = result === 'push'
  const isVoid = result === 'void'
  const style = isWin
    ? { background: 'var(--win-wash)', color: 'var(--signal)', border: '1px solid var(--win-border)' }
    : isPush || isVoid
    ? { background: 'var(--slate)', color: 'var(--fog)', border: '1px solid var(--iron)' }
    : { background: 'var(--loss-wash)', color: 'var(--loss)', border: '1px solid var(--loss-border)' }
  return (
    <span
      className="dell-heading"
      style={{ fontSize: '9px', letterSpacing: '0.06em', padding: '3px 8px', borderRadius: 'var(--radius-pill)', display: 'inline-block', ...style }}
    >
      {result.toUpperCase()}
    </span>
  )
}

export function PnL({ value }: { value: number | null }) {
  if (value === null) return <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>&mdash;</span>
  const pos = value >= 0
  return (
    <span className="mono" style={{ fontSize: '12px', fontWeight: 600, color: pos ? 'var(--signal)' : 'var(--loss)' }}>
      {pos ? '+' : ''}{(value / 10).toFixed(2)}u
    </span>
  )
}

export function SectionHeader({ label, sub }: { label: string; sub?: string }) {
  return (
    <div style={{ marginBottom: '24px' }}>
      <h2 className="dell-display" style={{ fontSize: '24px', color: 'var(--chalk)' }}>{label}</h2>
      {sub && <p className="times" style={{ fontSize: '14px', color: 'var(--fog)', marginTop: '7px', lineHeight: 1.5 }}>{sub}</p>}
    </div>
  )
}

export function Button({ children, variant = 'primary', href, onClick }: { children: React.ReactNode; variant?: 'primary' | 'ghost' | 'accent'; href?: string; onClick?: () => void }) {
  const cls = variant === 'ghost' ? 'btn btn-ghost' : variant === 'accent' ? 'btn btn-secondary' : 'btn btn-primary'
  if (href) return <a href={href} className={cls}>{children}</a>
  return <button onClick={onClick} className={cls}>{children}</button>
}

export function Divider() {
  return <div style={{ width: '100%', height: '1px', background: 'var(--basalt)' }} />
}

export function ScoreBadge({ bet }: { bet: Bet }) {
  const score = beezyscore(bet)
  const tier  = scoreTier(score)
  const color = TIER_COLOR[tier]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px' }}>
      <span className="mono" style={{ fontSize: '22px', fontWeight: 800, color, lineHeight: 1 }}>
        {score}
      </span>
      <span
        className="dell-heading"
        style={{ fontSize: '8px', letterSpacing: '0.08em', padding: '2px 6px', borderRadius: 'var(--radius-pill)', border: `1px solid ${color}`, background: `color-mix(in oklab, ${color} 14%, var(--carbon))`, color }}
      >
        {TIER_LABEL[tier]}
      </span>
    </div>
  )
}
