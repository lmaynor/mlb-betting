'use client'

import Link           from 'next/link'
import { useState }   from 'react'
import { usePathname } from 'next/navigation'
import { LiveDot }    from '@/components/ui/primitives'
import { Menu, X }    from 'lucide-react'
import { clsx }       from 'clsx'

const NAV_LINKS = [
  { label: 'Picks',   href: '/picks' },
  { label: 'Tools',   href: '/tools' },
  { label: 'Models',  href: '/models' },
  { label: 'Results', href: '/results' },
  { label: 'Learn',   href: '/learn' },
]

export function Nav() {
  const [open, setOpen] = useState(false)
  const pathname        = usePathname()

  return (
    <>
      {/* Paper mode banner */}
      <div className="w-full text-center py-1.5" style={{ background: '#1c1207', borderBottom: '.5px solid #3d2e0f', padding: '7px 16px' }}>
        <span className="mono text-[10px] tracking-widest uppercase" style={{ color: '#f59e0b' }}>
          Paper mode &middot; All results are simulated &middot; Not financial advice
        </span>
      </div>

      <nav className="sticky top-0 z-50 w-full border-b border-[var(--border)] bg-[var(--bg)]/95 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 flex items-center justify-between h-14">

          {/* Logo */}
          <Link href="/" className="flex items-center gap-3">
            <span className="mono text-base font-semibold tracking-tight" style={{ color: 'var(--text)' }}>
              BEEZY<span style={{ color: 'var(--accent)' }}>.VIP</span>
            </span>
          </Link>

          {/* Desktop nav */}
          <div className="hidden md:flex items-center gap-6">
            {NAV_LINKS.map(l => (
              <Link
                key={l.href}
                href={l.href}
                className={clsx(
                  'mono text-xs tracking-widest uppercase transition-colors',
                  pathname.startsWith(l.href)
                    ? 'text-accent'
                    : 'text-muted hover:text-text'
                )}
              >
                {l.label}
              </Link>
            ))}
          </div>

          {/* CTA */}
          <div className="hidden md:flex items-center gap-3">
            <a
              href="https://discord.gg/beezy"
              target="_blank"
              rel="noopener noreferrer"
              className="mono text-xs tracking-widest uppercase px-4 py-2 font-semibold transition-colors" style={{ background: 'var(--accent)', color: 'var(--bg)' }}
            >
              Join Discord
            </a>
          </div>

          {/* Mobile menu toggle */}
          <button
            className="md:hidden text-muted hover:text-text"
            onClick={() => setOpen(!open)}
          >
            {open ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>

        {/* Mobile menu */}
        {open && (
          <div className="md:hidden border-t border-[var(--border)] bg-[var(--surface)]">
            {NAV_LINKS.map(l => (
              <Link
                key={l.href}
                href={l.href}
                onClick={() => setOpen(false)}
                className={clsx(
                  'block mono text-xs tracking-widest uppercase px-4 py-3 border-b border-[var(--border)] transition-colors',
                  pathname.startsWith(l.href) ? 'text-accent' : 'text-muted hover:text-text'
                )}
              >
                {l.label}
              </Link>
            ))}
            <div className="p-4">
              <a
                href="https://discord.gg/beezy"
                target="_blank"
                rel="noopener noreferrer"
                className="block w-full mono text-xs tracking-widest uppercase text-center px-4 py-3 bg-accent text-bg font-semibold"
              >
                Join Discord
              </a>
            </div>
          </div>
        )}
      </nav>
    </>
  )
}
