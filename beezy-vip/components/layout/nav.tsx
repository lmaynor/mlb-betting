'use client'

import Link from 'next/link'
import { useState } from 'react'
import { usePathname } from 'next/navigation'
import { Menu, X } from 'lucide-react'

const B = '0.5px solid #1f1f24'
const NAV_LINKS = [
  { label: 'Picks',   href: '/picks' },
  { label: 'Tools',   href: '/tools' },
  { label: 'Models',  href: '/models' },
  { label: 'Results', href: '/results' },
  { label: 'Learn',   href: '/learn' },
]

export function Nav() {
  const [open, setOpen] = useState(false)
  const pathname = usePathname()

  return (
    <>
      {/* Paper mode banner — top padding accounts for notch/Dynamic Island */}
      <div style={{ background: '#1c1207', borderBottom: '0.5px solid #3d2e0f', padding: 'max(6px, env(safe-area-inset-top)) 16px 6px', textAlign: 'center' }}>
        <span className="mono banner-text" style={{ fontSize: '10px', letterSpacing: '0.06em', textTransform: 'uppercase', color: '#f59e0b' }}>
          <span className="banner-full">Paper mode &middot; All results are simulated &middot; Not financial advice</span>
          <span className="banner-short">Paper mode &middot; Not financial advice</span>
        </span>
      </div>

      <nav style={{ position: 'sticky', top: 0, zIndex: 50, width: '100%', maxWidth: '100vw', borderBottom: B, background: '#111114', overflow: 'hidden' }}>
        <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: '52px' }}>

          {/* Logo */}
          <Link href="/" style={{ textDecoration: 'none' }}>
            <span className="mono" style={{ fontSize: '15px', fontWeight: 600, color: '#f5f5f7' }}>
              BEEZY<span style={{ color: '#10b981' }}>.VIP</span>
            </span>
          </Link>

          {/* Desktop nav */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }} className="nav-desktop">
            {NAV_LINKS.map(l => (
              <Link key={l.href} href={l.href} className="mono" style={{ fontSize: '11px', letterSpacing: '0.06em', textTransform: 'uppercase', textDecoration: 'none', color: pathname.startsWith(l.href) ? '#10b981' : '#71717a' }}>
                {l.label}
              </Link>
            ))}
          </div>

          {/* Desktop CTA */}
          <div className="nav-desktop">
            <a href="https://discord.gg/beezy" target="_blank" rel="noopener noreferrer" className="mono"
              style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', padding: '6px 14px', background: '#10b981', color: '#0a0a0c', textDecoration: 'none' }}>
              Join Discord
            </a>
          </div>

          {/* Mobile toggle */}
          <button onClick={() => setOpen(!open)} className="nav-mobile" style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#71717a', alignItems: 'center' }}>
            {open ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>

        {/* Mobile menu */}
        {open && (
          <div style={{ borderTop: B, background: '#111114' }} className="nav-mobile">
            {NAV_LINKS.map(l => (
              <Link key={l.href} href={l.href} onClick={() => setOpen(false)} className="mono"
                style={{ display: 'block', fontSize: '12px', letterSpacing: '0.06em', textTransform: 'uppercase', padding: '14px 20px', borderBottom: B, textDecoration: 'none', color: pathname.startsWith(l.href) ? '#10b981' : '#71717a' }}>
                {l.label}
              </Link>
            ))}
            <div style={{ padding: '14px 20px' }}>
              <a href="https://discord.gg/beezy" target="_blank" rel="noopener noreferrer" className="mono"
                style={{ display: 'block', textAlign: 'center', fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '10px', background: '#10b981', color: '#0a0a0c', textDecoration: 'none' }}>
                Join Discord
              </a>
            </div>
          </div>
        )}
      </nav>
    </>
  )
}
