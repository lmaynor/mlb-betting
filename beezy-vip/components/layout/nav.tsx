'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useUser, UserButton, SignInButton } from '@clerk/nextjs'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'

const DISCORD_URL = 'https://discord.gg/HfMYCmbmE'
const X_URL = 'https://x.com/beezy_fyi'
const NAV_LINKS = [
  { label: 'The Edge',   href: '/edge' },
  { label: 'Picks',      href: '/picks' },
  { label: 'Daily Card', href: '/cheat-sheet' },
  { label: 'Tools',      href: '/tools' },
  { label: 'Models',     href: '/models' },
  { label: 'Results',    href: '/results' },
  { label: 'Pricing',    href: '/pricing' },
  { label: 'Learn',      href: '/learn' },
]

const iconBox: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  width: '34px', height: '34px', border: '1px solid var(--basalt)',
  borderRadius: 'var(--radius)', color: 'var(--silver)', textDecoration: 'none',
  background: 'var(--graphite)', transition: 'border-color var(--dur) var(--ease-out), color var(--dur) var(--ease-out)',
}

const discordBtn: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '7px',
  fontFamily: 'var(--font-text), sans-serif', fontWeight: 600, fontSize: '13px',
  padding: '8px 14px', borderRadius: 'var(--radius)', textDecoration: 'none',
  background: 'color-mix(in oklab, #5865f2 22%, var(--carbon))',
  color: '#c6ccff', border: '1px solid color-mix(in oklab, #5865f2 50%, var(--carbon))',
}

export function Nav() {
  const pathname  = usePathname()
  const { isSignedIn } = useUser()

  return (
    <nav style={{
      position: 'sticky', top: 0, zIndex: 50, width: '100%', maxWidth: '100vw',
      background: 'color-mix(in oklab, var(--graphite) 82%, transparent)',
      backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)',
      borderBottom: '1px solid var(--basalt)', overflow: 'hidden',
    }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 24px', display: 'flex', alignItems: 'center', height: '60px', gap: '24px' }}>

        {/* Logo */}
        <Link href="/" style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
          <span style={{ width: '9px', height: '9px', borderRadius: '50%', background: 'var(--signal)', boxShadow: '0 0 10px var(--signal)', flexShrink: 0 }} />
          <span className="dell-display" style={{ fontSize: '19px', fontWeight: 800, color: 'var(--chalk)', letterSpacing: '-0.01em', lineHeight: 1 }}>
            BEEZY<span style={{ color: 'var(--signal)' }}>.FYI</span>
          </span>
        </Link>

        {/* Desktop nav links */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '2px', flex: 1 }} className="nav-desktop">
          {NAV_LINKS.map((l) => {
            const active = pathname === l.href || (l.href !== '/' && pathname.startsWith(l.href))
            return (
              <Link
                key={l.href}
                href={l.href}
                style={{
                  fontSize: '13px', fontWeight: 500, letterSpacing: '0.005em',
                  textDecoration: 'none',
                  color: active ? 'var(--signal)' : 'var(--silver)',
                  padding: '7px 11px', borderRadius: 'var(--radius)',
                  background: active ? 'var(--win-wash)' : 'transparent',
                  transition: 'color var(--dur) var(--ease-out), background var(--dur) var(--ease-out)',
                }}
              >
                {l.label}
              </Link>
            )
          })}
        </div>

        {/* Desktop right: social + auth + Discord */}
        <div className="nav-desktop" style={{ gap: '10px', alignItems: 'center', flexShrink: 0 }}>
          <a href={X_URL} target="_blank" rel="noopener noreferrer" aria-label="Follow Beezy.FYI on X" style={iconBox}>
            <XMark size={14} color="currentColor" />
          </a>

          {isSignedIn ? (
            <>
              <Link
                href="/dashboard"
                style={{
                  fontSize: '13px', fontWeight: 600, textDecoration: 'none',
                  color: pathname.startsWith('/dashboard') ? 'var(--signal)' : 'var(--ash)',
                  padding: '8px 14px', border: '1px solid var(--basalt)', borderRadius: 'var(--radius)',
                }}
              >
                Dashboard
              </Link>
              <UserButton />
            </>
          ) : (
            <SignInButton mode="modal">
              <button
                style={{
                  fontFamily: 'var(--font-text), sans-serif', fontSize: '13px', fontWeight: 600,
                  background: 'transparent', border: '1px solid var(--basalt)', borderRadius: 'var(--radius)',
                  padding: '8px 14px', color: 'var(--ash)', cursor: 'pointer', whiteSpace: 'nowrap',
                }}
              >
                Sign In
              </button>
            </SignInButton>
          )}

          <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" style={discordBtn}>
            <DiscordMark size={15} color="currentColor" />
            <span>Discord</span>
          </a>
        </div>

        {/* Mobile: auth + social. BottomNav handles routing. */}
        <div className="mobile-only" style={{ alignItems: 'center', gap: '8px', marginLeft: 'auto' }}>
          {isSignedIn ? (
            <UserButton />
          ) : (
            <SignInButton mode="modal">
              <button
                style={{
                  fontFamily: 'var(--font-text), sans-serif', fontSize: '13px', fontWeight: 600,
                  background: 'transparent', border: '1px solid var(--basalt)', borderRadius: 'var(--radius)',
                  padding: '7px 12px', color: 'var(--ash)', cursor: 'pointer', whiteSpace: 'nowrap',
                }}
              >
                Sign In
              </button>
            </SignInButton>
          )}
          <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" aria-label="Join Discord" style={{ ...discordBtn, padding: '7px 11px' }}>
            <DiscordMark size={15} color="currentColor" />
          </a>
        </div>
      </div>
    </nav>
  )
}
