'use client'

import { clsx } from 'clsx'

// ── Live dot ────────────────────────────────────────────────
export function LiveDot({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="live-dot" />
      {label && (
        <span className="mono text-xs tracking-widest text-muted uppercase">
          {label}
        </span>
      )}
    </span>
  )
}

// ── System badge ─────────────────────────────────────────────
const SYSTEM_COLORS: Record<string, string> = {
  NRFI:  'text-[#00FF87] border-[#00FF87]/30 bg-[#00FF87]/5',
  HR:    'text-[#FFB800] border-[#FFB800]/30 bg-[#FFB800]/5',
  F5:    'text-[#00B4FF] border-[#00B4FF]/30 bg-[#00B4FF]/5',
  K:     'text-[#BF5FFF] border-[#BF5FFF]/30 bg-[#BF5FFF]/5',
  OUTS:  'text-[#FF6B35] border-[#FF6B35]/30 bg-[#FF6B35]/5',
}

export function SystemBadge({ system }: { system: string }) {
  const colors = SYSTEM_COLORS[system] ?? 'text-muted border-white/10 bg-white/5'
  return (
    <span className={clsx('mono text-xs font-600 px-2 py-0.5 border tracking-widest', colors)}>
      {system}
    </span>
  )
}

// ── Stat card ─────────────────────────────────────────────────
export function StatCard({
  label,
  value,
  sub,
  accent = false,
}: {
  label: string
  value: string
  sub?: string
  accent?: boolean
}) {
  return (
    <div className="flex flex-col gap-1 p-5 border border-[var(--border)] bg-[var(--surface)]">
      <span className="text-xs tracking-widest uppercase text-muted">{label}</span>
      <span
        className={clsx(
          'mono text-3xl font-semibold leading-none',
          accent ? 'text-accent' : 'text-text'
        )}
      >
        {value}
      </span>
      {sub && <span className="mono text-xs text-muted">{sub}</span>}
    </div>
  )
}

// ── Result pill ───────────────────────────────────────────────
export function ResultPill({ result }: { result: string | null }) {
  if (!result) return <span className="mono text-xs text-muted">PENDING</span>
  const isWin  = result === 'win'
  const isPush = result === 'push'
  const isVoid = result === 'void'
  // Display label: capitalize for UI readability
  const label  = result.toUpperCase()
  return (
    <span
      className={clsx(
        'mono text-xs font-semibold px-2 py-0.5 border',
        isWin  && 'text-win  border-win/30  bg-win/5',
        isPush && 'text-muted border-muted/30 bg-muted/5',
        isVoid && 'text-muted border-muted/30 bg-muted/5',
        !isWin && !isPush && !isVoid && 'text-loss border-loss/30 bg-loss/5'
      )}
    >
      {label}
    </span>
  )
}

// ── PnL display ───────────────────────────────────────────────
export function PnL({ value }: { value: number | null }) {
  if (value === null) return <span className="mono text-xs text-muted">—</span>
  const pos = value >= 0
  return (
    <span className={clsx('mono text-sm font-semibold', pos ? 'text-win' : 'text-loss')}>
      {pos ? '+' : ''}
      {value.toFixed(2)}u
    </span>
  )
}

// ── Section header ────────────────────────────────────────────
export function SectionHeader({
  label,
  sub,
}: {
  label: string
  sub?: string
}) {
  return (
    <div className="flex flex-col gap-1 mb-8">
      <h2 className="text-2xl font-extrabold tracking-tight uppercase">{label}</h2>
      {sub && <p className="text-muted text-sm">{sub}</p>}
    </div>
  )
}

// ── Button ────────────────────────────────────────────────────
export function Button({
  children,
  variant = 'primary',
  href,
  onClick,
  className,
}: {
  children: React.ReactNode
  variant?: 'primary' | 'ghost' | 'accent'
  href?: string
  onClick?: () => void
  className?: string
}) {
  const base = 'inline-flex items-center justify-center mono text-sm font-semibold tracking-widest uppercase px-6 py-3 border transition-all duration-150 cursor-pointer'
  const variants = {
    primary: 'bg-accent text-bg border-accent hover:bg-accent/90',
    ghost:   'bg-transparent text-text border-[var(--border-bright)] hover:border-accent hover:text-accent',
    accent:  'bg-accent/10 text-accent border-accent/40 hover:bg-accent/20',
  }
  const cls = clsx(base, variants[variant], className)

  if (href) {
    return <a href={href} className={cls}>{children}</a>
  }
  return <button onClick={onClick} className={cls}>{children}</button>
}

// ── Divider ───────────────────────────────────────────────────
export function Divider() {
  return <div className="w-full h-px bg-[var(--border)]" />
}
