'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const TABS = [
  { label: 'Player picks', href: '/picks/mlb' },
  { label: 'Systems', href: '/picks/mlb/systems' },
]

/** MLB's own view switch: today's card vs the per-system record. Per-system pages (/picks/mlb/hr) count as Systems. */
export function MlbTabs() {
  const pathname = usePathname()
  const active = (href: string) => (href === '/picks/mlb' ? pathname === '/picks/mlb' : pathname.startsWith('/picks/mlb/'))
  return (
    <nav aria-label="MLB views" style={{ borderBottom: '1px solid #1e2230', background: '#090a0f' }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 24px', display: 'flex', gap: '2px' }}>
        {TABS.map(t => (
          <Link key={t.href} href={t.href} aria-current={active(t.href) ? 'page' : undefined}
            style={{ fontSize: '12.5px', fontWeight: 600, padding: '10px 12px', textDecoration: 'none', color: active(t.href) ? 'var(--chalk)' : 'var(--fog)',
              borderBottom: `2px solid ${active(t.href) ? 'var(--signal)' : 'transparent'}`, marginBottom: '-1px' }}>
            {t.label}
          </Link>
        ))}
      </div>
    </nav>
  )
}
