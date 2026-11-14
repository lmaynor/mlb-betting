'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Home, Trophy, Zap, ListChecks, Menu } from 'lucide-react'
import { cx } from '@/utils/cx'

// Five slots (lib/ia.ts, Q4(b)): Home, Scanner, Daily Card, Results, Menu. The Menu slot is not a route: it
// opens the header drawer (Learn plus the full section trees) through a window event the Nav listens for.
const TABS = [
  { label: 'Home',       href: '/',        Icon: Home,       prefixes: [] as string[] },
  { label: 'Scanner',    href: '/ev',      Icon: Zap,        prefixes: ['/ev', '/edge'] },
  { label: 'Daily Card', href: '/picks',   Icon: ListChecks, prefixes: ['/picks', '/cheat-sheet', '/sports'] },
  { label: 'Results',    href: '/results', Icon: Trophy,     prefixes: ['/results', '/analytics', '/models'] },
]

const ITEM_CLASS = 'flex flex-1 flex-col items-center justify-center gap-1 py-2 no-underline'

export function BottomNav() {
  const pathname = usePathname()

  return (
    <nav
      className="mobile-only fixed inset-x-0 bottom-0 z-[60] h-[calc(60px+env(safe-area-inset-bottom))] items-stretch justify-around border-t border-separator-border bg-[color-mix(in_oklab,var(--color-background-primary-default)_92%,transparent)] pb-[env(safe-area-inset-bottom)] backdrop-blur-[12px]"
    >
      {TABS.map(tab => {
        const active = tab.href === '/' ? pathname === '/' : tab.prefixes.some(p => pathname === p || pathname.startsWith(p + '/'))
        const color = active ? 'var(--signal)' : 'var(--color-text-secondary)'
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={ITEM_CLASS}
            style={{ color }}
          >
            <span className={cx('inline-flex h-[26px] w-9 items-center justify-center rounded-full transition-colors', active ? 'bg-[var(--win-wash)]' : 'bg-transparent')}>
              <tab.Icon size={18} strokeWidth={active ? 2.3 : 1.9} color={color} />
            </span>
            <span className="ui-heading">
              {tab.label}
            </span>
          </Link>
        )
      })}
      <button
        type="button"
        className={cx(ITEM_CLASS, 'cursor-pointer')}
        style={{ color: 'var(--color-text-secondary)' }}
        aria-label="Open menu"
        onClick={() => window.dispatchEvent(new CustomEvent('beezy:open-menu'))}
      >
        <span className="inline-flex h-[26px] w-9 items-center justify-center rounded-full bg-transparent">
          <Menu size={18} strokeWidth={1.9} color="var(--color-text-secondary)" />
        </span>
        <span className="ui-heading">Menu</span>
      </button>
    </nav>
  )
}
