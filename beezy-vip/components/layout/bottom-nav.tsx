'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { BarChart3, Calculator, ClipboardList, ListChecks, Trophy } from 'lucide-react'

const TABS = [
  { label: 'Card', href: '/cheat-sheet', Icon: ClipboardList },
  { label: 'Picks', href: '/picks', Icon: ListChecks },
  { label: 'Results', href: '/results', Icon: Trophy },
  { label: 'Tools', href: '/tools', Icon: Calculator },
  { label: 'Models', href: '/models', Icon: BarChart3 },
]

export function BottomNav() {
  const pathname = usePathname()

  return (
    <nav className="mobile-only" style={{
      position: 'fixed',
      bottom: 0,
      left: 0,
      right: 0,
      zIndex: 50,
      justifyContent: 'space-around',
      alignItems: 'center',
      height: '56px',
      paddingBottom: 'env(safe-area-inset-bottom)',
      background: 'rgba(10,10,12,.92)',
      backdropFilter: 'blur(20px)',
      borderTop: '0.5px solid rgba(255,255,255,.06)',
    }}>
      {TABS.map(tab => {
        const active = pathname.startsWith(tab.href)
        const color = active ? '#10b981' : '#71717a'
        return (
          <Link key={tab.href} href={tab.href} style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '3px',
            textDecoration: 'none',
            flex: 1,
            padding: '8px 0',
            color,
          }}>
            <tab.Icon size={17} strokeWidth={active ? 2.4 : 1.9} color={color} />
            <span style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '9px',
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
            }}>
              {tab.label}
            </span>
          </Link>
        )
      })}
    </nav>
  )
}
