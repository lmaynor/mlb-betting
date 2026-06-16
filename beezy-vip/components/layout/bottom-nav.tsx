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
        zIndex: 50,
        justifyContent: 'space-around',
        alignItems: 'stretch',
        height: '56px',
        paddingBottom: 'env(safe-area-inset-bottom)',
        background: '#000',
        borderTop: '1px solid #000',
      }}
    >
      {TABS.map(tab => {
        const active = pathname.startsWith(tab.href)
        const color = active ? '#fcc20f' : '#888890'
        return (
          <Link
            key={tab.href}
            href={tab.href}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '3px',
              textDecoration: 'none',
              flex: 1,
              padding: '8px 0',
              color,
              borderTop: active ? '2px solid #fcc20f' : '2px solid transparent',
              background: active ? '#111' : 'transparent',
            }}
          >
            <tab.Icon size={17} strokeWidth={active ? 2.4 : 1.9} color={color} />
            <span
              className="dell-heading"
              style={{ fontSize: '10px', letterSpacing: '0.06em' }}
            >
              {tab.label}
            </span>
          </Link>
        )
      })}
    </nav>
  )
}
