'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Home, Trophy, Wrench, Zap, ListChecks } from 'lucide-react'
import { cx } from '@/utils/cx'

// Five questions, five tabs (lib/ia.ts). Home is also the logo in the top bar.
const TABS = [
  { label: 'Home',    href: '/',        Icon: Home,       prefixes: [] as string[] },
  { label: 'Odds',    href: '/ev',      Icon: Zap,        prefixes: ['/ev', '/edge'] },
  { label: 'Picks',   href: '/picks',   Icon: ListChecks, prefixes: ['/picks', '/cheat-sheet', '/sports'] },
  { label: 'Results', href: '/results', Icon: Trophy,     prefixes: ['/results', '/analytics', '/models'] },
  { label: 'Tools',   href: '/tools',   Icon: Wrench,     prefixes: ['/tools'] },
]

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
            className="flex flex-1 flex-col items-center justify-center gap-1 py-2 no-underline"
            style={{ color }}
          >
            <span className={cx('inline-flex h-[26px] w-9 items-center justify-center rounded-full transition-colors', active ? 'bg-[var(--win-wash)]' : 'bg-transparent')}>
              <tab.Icon size={18} strokeWidth={active ? 2.3 : 1.9} color={color} />
            </span>
            <span className="ui-heading tracking-[0.03em]">
              {tab.label}
            </span>
          </Link>
        )
      })}
    </nav>
  )
}
