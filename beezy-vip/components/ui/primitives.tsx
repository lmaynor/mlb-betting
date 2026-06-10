'use client'

import { beezyscore, scoreTier, TIER_COLOR, TIER_LABEL } from '@/lib/beezy-score'
import type { Bet } from '@/lib/types'

// Dell 1996 pill definitions -- mapped from tokens.ts tints
const PILL: Record<string, { bg: string; color: string; border: string }> = {
  NRFI: { bg: '#1a2218', color: '#b3bd95', border: '1px solid #8e9e78' },
  HR:   { bg: '#2a1818', color: '#d77a7a', border: '1px solid #b05050' },
  F5:   { bg: '#131e24', color: '#9ab6c8', border: '1px solid #6a8fa0' },
  K:    { bg: '#0f1024', color: '#8c9ae0', border: '1px solid #5c6bbc' },
  OUTS: { bg: '#2a1a0f', color: '#e6915d', border: '1px solid #c06830' },
}

export function LiveDot({ label }: { label?: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
      <span className="live-dot" />
      {label && (
        <span
          className="dell-heading"
          style={{ fontSize: '10px', letterSpacing: '0.1em', color: '#888890' }}
        >
          {label}
        </span>
      )}
    </span>
  )
}

export function SystemBadge({ system }: { system: string }) {
  const p = PILL[system] ?? { bg: '#1f1f24', color: '#a1a1aa', border: '1px solid #2a2a31' }
  return (
    <span
      className="dell-heading"
      style={{ fontSize: '9px', letterSpacing: '0.06em', padding: '3px 7px', background: p.bg, color: p.color, border: p.border, display: 'inline-block' }}
    >
      {system}
    </span>
  )
}

export function StatCard({ label, value, sub, accent = false }: { label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', padding: '20px', border: '1px solid #000', background: '#111114' }}>
      <span
        className="dell-heading"
        style={{ fontSize: '9px', letterSpacing: '0.1em', color: '#888890', marginBottom: '4px' }}
      >
        {label}
      </span>
      <span
        className="mono"
        style={{ fontSize: '28px', fontWeight: 600, lineHeight: 1, color: accent ? '#b3bd95' : '#f5f5f7' }}
      >
        {value}
      </span>
      {sub && <span className="mono" style={{ fontSize: '11px', color: '#888890' }}>{sub}</span>}
    </div>
  )
}

export function ResultPill({ result }: { result: string | null }) {
  if (!result || result === 'pending') {
    return <span className="dell-heading" style={{ fontSize: '9px', color: '#888890' }}>PENDING</span>
  }
  const isWin  = result === 'win'
  const isPush = result === 'push'
  const isVoid = result === 'void'
  const style = isWin
    ? { background: '#1a2218', color: '#b3bd95', border: '1px solid #8e9e78' }
    : isPush || isVoid
    ? { background: '#1f1f24', color: '#888890', border: '1px solid #2a2a31' }
    : { background: '#2a1818', color: '#d77a7a', border: '1px solid #b05050' }
  return (
    <span
      className="dell-heading"
      style={{ fontSize: '9px', letterSpacing: '0.06em', padding: '3px 7px', display: 'inline-block', ...style }}
    >
      {result.toUpperCase()}
    </span>
  )
}

export function PnL({ value }: { value: number | null }) {
  if (value === null) return <span className="mono" style={{ fontSize: '12px', color: '#888890' }}>—</span>
  const pos = value >= 0
  return (
    <span className="mono" style={{ fontSize: '12px', fontWeight: 600, color: pos ? '#b3bd95' : '#d77a7a' }}>
      {pos ? '+' : ''}{(value / 10).toFixed(2)}u
    </span>
  )
}

export function SectionHeader({ label, sub }: { label: string; sub?: string }) {
  return (
    <div style={{ marginBottom: '24px' }}>
      <h2 className="dell-display" style={{ fontSize: '18px', color: '#f5f5f7' }}>{label}</h2>
      {sub && <p className="times" style={{ fontSize: '13px', color: '#888890', marginTop: '6px', lineHeight: 1.5 }}>{sub}</p>}
    </div>
  )
}

export function Button({ children, variant = 'primary', href, onClick }: { children: React.ReactNode; variant?: 'primary' | 'ghost' | 'accent'; href?: string; onClick?: () => void }) {
  const styles: Record<string, React.CSSProperties> = {
    primary: { background: '#000', color: '#fff', border: '1px solid #000' },
    ghost:   { background: 'transparent', color: '#f5f5f7', border: '1px solid #333' },
    accent:  { background: '#fcc20f', color: '#000', border: '1px solid #000' },
  }
  const base: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontFamily: 'Arial, Helvetica, sans-serif',
    fontSize: '11px',
    fontWeight: 700,
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
    padding: '8px 18px',
    cursor: 'pointer',
    textDecoration: 'none',
    borderRadius: 0,
    ...styles[variant],
  }
  if (href) return <a href={href} style={base}>{children}</a>
  return <button onClick={onClick} style={base}>{children}</button>
}

export function Divider() {
  return <div style={{ width: '100%', height: '1px', background: '#1f1f24' }} />
}

export function ScoreBadge({ bet }: { bet: Bet }) {
  const score = beezyscore(bet)
  const tier  = scoreTier(score)
  const color = TIER_COLOR[tier]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '3px' }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '22px', fontWeight: 800, color, lineHeight: 1 }}>
        {score}
      </span>
      <span
        className="dell-heading"
        style={{ fontSize: '8px', letterSpacing: '0.1em', padding: '2px 6px', border: `1px solid ${color}`, background: `${color}18`, color }}
      >
        {TIER_LABEL[tier]}
      </span>
    </div>
  )
}
