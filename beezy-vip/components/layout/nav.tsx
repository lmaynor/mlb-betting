'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const B = '0.5px solid #1f1f24'
const NAV_LINKS = [
  { label: 'Picks',       href: '/picks' },
  { label: 'Cheat Sheet', href: '/cheat-sheet' },
  { label: 'Tools',       href: '/tools' },
  { label: 'Models',      href: '/models' },
  { label: 'Results',     href: '/results' },
  { label: 'Learn',       href: '/learn' },
]

export function Nav() {
  const pathname = usePathname()

  return (
    <nav style={{ position: 'sticky', top: 0, zIndex: 50, width: '100%', maxWidth: '100vw', borderBottom: B, background: '#111114', overflow: 'hidden' }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: '52px' }}>

        {/* Logo */}
        <Link href="/" style={{ textDecoration: 'none' }}>
          <span className="mono" style={{ fontSize: '15px', fontWeight: 600, color: '#f5f5f7' }}>
            BEEZY<span style={{ color: '#10b981' }}>.VIP</span>
          </span>
        </Link>

        {/* Desktop nav links */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }} className="nav-desktop">
          {NAV_LINKS.map(l => (
            <Link key={l.href} href={l.href} className="mono" style={{ fontSize: '11px', letterSpacing: '0.06em', textTransform: 'uppercase', textDecoration: 'none', color: pathname.startsWith(l.href) ? '#10b981' : '#71717a' }}>
              {l.label}
            </Link>
          ))}
        </div>

        {/* Desktop CTA */}
        <div className="nav-desktop">
          <a href="https://discord.gg/HfMYCmbmE" target="_blank" rel="noopener noreferrer" className="mono"
            style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', padding: '6px 14px', background: '#10b981', color: '#0a0a0c', textDecoration: 'none' }}>
            Join Discord
          </a>
        </div>

        {/* Mobile: Discord link only — BottomNav handles all navigation */}
        <div className="nav-mobile">
          <a href="https://discord.gg/HfMYCmbmE" target="_blank" rel="noopener noreferrer" className="mono"
            style={{ fontSize: '10px', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', padding: '5px 10px', background: '#10b981', color: '#0a0a0c', textDecoration: 'none' }}>
            Discord
          </a>
        </div>
      </div>
    </nav>
  )
}
