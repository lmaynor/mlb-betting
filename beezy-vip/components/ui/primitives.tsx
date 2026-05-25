'use client'

const PILL: Record<string, { bg: string; color: string; border: string }> = {
  NRFI: { bg: '#052016', color: '#10b981', border: '0.5px solid #0f6e56' },
  HR:   { bg: '#1c1207', color: '#f59e0b', border: '0.5px solid #854f0b' },
  F5:   { bg: '#040e1c', color: '#3b82f6', border: '0.5px solid #185fa5' },
  K:    { bg: '#0e0718', color: '#a78bfa', border: '0.5px solid #534ab7' },
  OUTS: { bg: '#1a0d05', color: '#fb923c', border: '0.5px solid #9a3412' },
}

export function LiveDot({ label }: { label?: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
      <span className="live-dot" />
      {label && <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>{label}</span>}
    </span>
  )
}

export function SystemBadge({ system }: { system: string }) {
  const p = PILL[system] ?? { bg: '#1f1f24', color: '#a1a1aa', border: '0.5px solid #2a2a31' }
  return (
    <span className="mono" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.06em', padding: '3px 7px', background: p.bg, color: p.color, border: p.border, display: 'inline-block', borderRadius: 'var(--radius-sm)' }}>
      {system}
    </span>
  )
}

export function StatCard({ label, value, sub, accent = false }: { label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', padding: '20px', border: '0.5px solid #1f1f24', background: '#111114', borderRadius: 'var(--radius)', boxShadow: 'var(--shadow-card)' }}>
      <span style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>{label}</span>
      <span className="mono" style={{ fontSize: '28px', fontWeight: 600, lineHeight: 1, color: accent ? '#10b981' : '#f5f5f7' }}>{value}</span>
      {sub && <span className="mono" style={{ fontSize: '11px', color: '#71717a' }}>{sub}</span>}
    </div>
  )
}

export function ResultPill({ result }: { result: string | null }) {
  if (!result || result === 'pending') return <span className="mono" style={{ fontSize: '9px', color: '#71717a' }}>PENDING</span>
  const isWin  = result === 'win'
  const isPush = result === 'push'
  const isVoid = result === 'void'
  const style = isWin
    ? { background: '#052016', color: '#10b981', border: '0.5px solid #0f6e56' }
    : isPush || isVoid
    ? { background: '#1f1f24', color: '#71717a', border: '0.5px solid #2a2a31' }
    : { background: '#200808', color: '#ef4444', border: '0.5px solid #a32d2d' }
  return (
    <span className="mono" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.06em', padding: '3px 7px', display: 'inline-block', ...style }}>
      {result.toUpperCase()}
    </span>
  )
}

export function PnL({ value }: { value: number | null }) {
  if (value === null) return <span className="mono" style={{ fontSize: '12px', color: '#71717a' }}>—</span>
  const pos = value >= 0
  return (
    <span className="mono" style={{ fontSize: '12px', fontWeight: 600, color: pos ? '#10b981' : '#ef4444' }}>
      {pos ? '+' : ''}{(value / 10).toFixed(2)}u
    </span>
  )
}

export function SectionHeader({ label, sub }: { label: string; sub?: string }) {
  return (
    <div style={{ marginBottom: '24px' }}>
      <h2 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', letterSpacing: '-0.01em' }}>{label}</h2>
      {sub && <p style={{ fontSize: '13px', color: '#71717a', marginTop: '4px' }}>{sub}</p>}
    </div>
  )
}

export function Button({ children, variant = 'primary', href, onClick }: { children: React.ReactNode; variant?: 'primary' | 'ghost' | 'accent'; href?: string; onClick?: () => void }) {
  const styles: Record<string, React.CSSProperties> = {
    primary: { background: '#10b981', color: '#0a0a0c', border: 'none' },
    ghost:   { background: 'transparent', color: '#f5f5f7', border: '0.5px solid #2a2a31' },
    accent:  { background: 'rgba(16,185,129,0.1)', color: '#10b981', border: '0.5px solid rgba(16,185,129,0.4)' },
  }
  const base: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--font-mono, monospace)', fontSize: '12px', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '10px 20px', cursor: 'pointer', textDecoration: 'none', ...styles[variant] }
  if (href) return <a href={href} style={base}>{children}</a>
  return <button onClick={onClick} style={base}>{children}</button>
}

export function Divider() {
  return <div style={{ width: '100%', height: '0.5px', background: '#1f1f24' }} />
}
