'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { BarChart3, Calculator, ClipboardList, ListChecks, Trophy, Zap } from 'lucide-react'

const TABS = [
  { label: 'Edge',    href: '/edge',        Icon: Zap },
  { label: 'Card',    href: '/cheat-sheet', Icon: ClipboardList },
  { label: 'Picks',   href: '/picks',       Icon: ListChecks },
  { label: 'Results', href: '/results',     Icon: Trophy },
  { label: 'Tools',   href: '/tools',       Icon: Calculator },
  { label: 'Models',  href: '/models',      Icon: BarChart3 },
]

export function BottomNav() {
  const pathname = usePathname()

  return (
    <nav
      className="mobile-only"
      style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        zIndex: 60,
        justifyContent: 'space-around',
        alignItems: 'stretch',
        height: 'calc(60px + env(safe-area-inset-bottom))',
        paddingBottom: 'env(safe-area-inset-bottom)',
        background: 'color-mix(in oklab, var(--graphite) 92%, transparent)',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
        borderTop: '1px solid var(--basalt)',
      }}
    >
      {TABS.map(tab => {
        const active = pathname === tab.href || (tab.href !== '/' && pathname.startsWith(tab.href))
        const color = active ? 'var(--signal)' : 'var(--fog)'
        return (
          <Link
            key={tab.href}
            href={tab.href}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '4px',
              textDecoration: 'none',
              flex: 1,
              padding: '8px 0',
              color,
            }}
          >
            <span style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: '40px', height: '26px', borderRadius: 'var(--radius-pill)',
              background: active ? 'var(--win-wash)' : 'transparent',
              transition: 'background var(--dur) var(--ease-out)',
            }}>
              <tab.Icon size={18} strokeWidth={active ? 2.3 : 1.9} color={color} />
            </span>
            <span
              className="dell-heading"
              style={{ fontSize: '9.5px', letterSpacing: '0.05em' }}
            >
              {tab.label}
            </span>
          </Link>
        )
      })}
    </nav>
  )
}
