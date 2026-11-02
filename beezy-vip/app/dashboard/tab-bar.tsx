'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cx } from '@/utils/cx'

const TABS = [
  { label: "Today's Picks", href: '/dashboard/picks' },
  { label: 'Settings',      href: '/dashboard/settings' },
]

/** Client component so the active tab can follow the URL (the old layout read request headers Next never sets). */
export function DashboardTabBar() {
  const pathname = usePathname()
  return (
    <nav aria-label="Dashboard" className="mb-5 flex gap-1 border-b border-separator-border">
      {TABS.map(t => {
        const active = pathname === t.href || pathname.startsWith(t.href + '/')
        return (
          <Link key={t.href} href={t.href} aria-current={active ? 'page' : undefined}
            className={cx('-mb-px border-b-2 px-3.5 py-2.5 text-body-2-semibold no-underline transition-colors',
              active ? 'border-b-[var(--signal)] text-text-primary' : 'border-b-transparent text-text-secondary hover:text-text-primary')}>
            {t.label}
          </Link>
        )
      })}
    </nav>
  )
}
