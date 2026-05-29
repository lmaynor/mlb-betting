'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useUser, UserButton, SignInButton } from '@clerk/nextjs'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'

const B = '0.5px solid #1f1f24'
const DISCORD_URL = 'https://discord.gg/HfMYCmbmE'
const X_URL = 'https://x.com/beezy_fyi'
const NAV_LINKS = [
  { label: 'Picks',       href: '/picks' },
  { label: 'Daily Card',  href: '/cheat-sheet' },
  { label: 'Tools',       href: '/tools' },
  { label: 'Models',      href: '/models' },
  { label: 'Results',     href: '/results' },
  { label: 'Pricing',     href: '/pricing' },
  { label: 'Learn',       href: '/learn' },
]

export function Nav() {
  const pathname  = usePathname()
  const { isSignedIn } = useUser()

  return (
    <nav style={{ position: 'sticky', top: 0, zIndex: 50, width: '100%', maxWidth: '100vw', borderBottom: B, background: '#111114', overflow: 'hidden' }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: '52px' }}>

        {/* Logo */}
        <Link href="/" style={{ textDecoration: 'none' }}>
          <span className="mono" style={{ fontSize: '15px', fontWeight: 600, color: '#f5f5f7' }}>
            BEEZY<span style={{ color: '#10b981' }}>.FYI</span>
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

        {/* Desktop: Discord + auth */}
        <div className="nav-desktop" style={{ gap: '12px' }}>
          <a href={X_URL} target="_blank" rel="noopener noreferrer" className="mono" aria-label="Follow Beezy.FYI on X"
            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '29px', height: '29px', border: '0.5px solid #2a2a31', color: '#f5f5f7', textDecoration: 'none', borderRadius: 'var(--radius-sm)' }}>
            <XMark size={14} color="currentColor" />
          </a>
          <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" className="mono"
            style={{ display: 'inline-flex', alignItems: 'center', gap: '7px', fontSize: '11px', fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', padding: '6px 12px', background: '#5865f2', color: '#fff', textDecoration: 'none', borderRadius: 'var(--radius-sm)' }}>
            <DiscordMark size={15} color="currentColor" />
            Discord
          </a>
          {isSignedIn ? (
            <>
              <Link href="/dashboard" className="mono" style={{ fontSize: '11px', letterSpacing: '0.06em', textTransform: 'uppercase', textDecoration: 'none', color: pathname.startsWith('/dashboard') ? '#10b981' : '#71717a' }}>
                Dashboard
              </Link>
              <UserButton  />
            </>
          ) : (
            <SignInButton mode="modal">
              <button className="mono" style={{ fontSize: '11px', letterSpacing: '0.06em', textTransform: 'uppercase', background: 'none', border: '0.5px solid #2a2a31', padding: '5px 12px', color: '#71717a', cursor: 'pointer' }}>
                Sign In
              </button>
            </SignInButton>
          )}
        </div>

        {/* Mobile: auth + Discord. BottomNav handles routing. */}
        <div className="mobile-only" style={{ alignItems: 'center', gap: '8px' }}>
          {isSignedIn ? (
            <UserButton  />
          ) : (
            <SignInButton mode="modal">
              <button className="mono" style={{ fontSize: '10px', letterSpacing: '0.04em', textTransform: 'uppercase', background: 'none', border: '0.5px solid #2a2a31', padding: '4px 8px', color: '#71717a', cursor: 'pointer' }}>
                Sign In
              </button>
            </SignInButton>
          )}
          <a href={X_URL} target="_blank" rel="noopener noreferrer" aria-label="Follow Beezy.FYI on X"
            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '27px', height: '27px', border: '0.5px solid #2a2a31', color: '#f5f5f7', textDecoration: 'none', borderRadius: 'var(--radius-sm)' }}>
            <XMark size={13} color="currentColor" />
          </a>
          <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" className="mono"
            style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', fontSize: '10px', fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', padding: '5px 9px', background: '#5865f2', color: '#fff', textDecoration: 'none', borderRadius: 'var(--radius-sm)' }}>
            <DiscordMark size={13} color="currentColor" />
            Discord
          </a>
        </div>
      </div>
    </nav>
  )
}
