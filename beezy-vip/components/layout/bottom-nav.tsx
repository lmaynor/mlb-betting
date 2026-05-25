'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

const TABS = [
  { label: 'Today',   href: '/cheat-sheet', icon: '◆' },
  { label: 'Picks',   href: '/picks',       icon: '≡' },
  { label: 'Results', href: '/results',     icon: '▦' },
  { label: 'Tools',   href: '/tools',       icon: '⚙' },
  { label: 'More',    href: '/models',      icon: '···' },
]

export function BottomNav() {
  const pathname = usePathname()
  return (
    <nav className="mobile-only" style={{
      position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 50,
      justifyContent: 'space-around', alignItems: 'center',
      height: '56px',
      paddingBottom: 'env(safe-area-inset-bottom)',
      background: 'rgba(10,10,12,.92)',
      backdropFilter: 'blur(20px)',
      borderTop: '0.5px solid rgba(255,255,255,.06)',
    }}>
      {TABS.map(tab => {
        const active = pathname.startsWith(tab.href)
        return (
          <Link key={tab.href} href={tab.href} style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '3px',
            textDecoration: 'none', flex: 1, padding: '8px 0',
            color: active ? '#10b981' : '#71717a',
          }}>
            <span style={{ fontSize: '16px' }}>{tab.icon}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9px',
                           letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              {tab.label}
            </span>
          </Link>
        )
      })}
    </nav>
  )
}
