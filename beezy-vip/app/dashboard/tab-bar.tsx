'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const TABS = [
  { label: "Today's Picks", href: '/dashboard/picks' },
  { label: 'Settings',      href: '/dashboard/settings' },
]

/** Client component so the active tab can follow the URL (the old layout read request headers Next never sets). */
export function DashboardTabBar() {
  const pathname = usePathname()
  return (
    <nav aria-label="Dashboard" style={{ display: 'flex', gap: '4px', borderBottom: '1px solid #1e2230', marginBottom: '20px' }}>
      {TABS.map(t => {
        const active = pathname === t.href || pathname.startsWith(t.href + '/')
        return (
          <Link key={t.href} href={t.href} aria-current={active ? 'page' : undefined}
            style={{ fontSize: '13px', fontWeight: 600, padding: '10px 14px', textDecoration: 'none', borderBottom: `2px solid ${active ? 'var(--signal)' : 'transparent'}`, color: active ? 'var(--chalk)' : 'var(--silver)', marginBottom: '-1px' }}>
            {t.label}
          </Link>
        )
      })}
    </nav>
  )
}
