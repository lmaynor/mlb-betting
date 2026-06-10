'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useUser, UserButton, SignInButton } from '@clerk/nextjs'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'

const BORDER_HARD = '1px solid #000'
const DISCORD_URL = 'https://discord.gg/HfMYCmbmE'
const X_URL = 'https://x.com/beezy_fyi'
const NAV_LINKS = [
  { label: 'Picks',      href: '/picks' },
  { label: 'Daily Card', href: '/cheat-sheet' },
  { label: 'Tools',      href: '/tools' },
  { label: 'Models',     href: '/models' },
  { label: 'Results',    href: '/results' },
  { label: 'Pricing',    href: '/pricing' },
  { label: 'Learn',      href: '/learn' },
]

export function Nav() {
  const pathname  = usePathname()
  const { isSignedIn } = useUser()

  return (
    <nav style={{ position: 'sticky', top: 0, zIndex: 50, width: '100%', maxWidth: '100vw', background: '#000', borderBottom: BORDER_HARD, overflow: 'hidden' }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 20px', display: 'flex', alignItems: 'stretch', height: '56px' }}>

        {/* Logo -- Dell display: Arial Black style */}
        <Link href="/" style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '10px', flex: 1 }}>
          {/* Logo placeholder -- replace with <img> when final mark is ready */}
          <div style={{ width: '28px', height: '28px', border: '1px dashed #444', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#444', fontSize: '9px', fontFamily: 'Arial, Helvetica, sans-serif', letterSpacing: 0 }}>
            ?
          </div>
          <span className="dell-display" style={{ fontSize: '18px', color: '#fff', letterSpacing: '0.02em', lineHeight: 1 }}>
            BEEZY<span style={{ color: '#fcc20f' }}>.FYI</span>
          </span>
        </Link>

        {/* Desktop nav links */}
        <div style={{ display: 'flex', alignItems: 'stretch' }} className="nav-desktop">
          {NAV_LINKS.map(l => {
            const active = pathname.startsWith(l.href)
            return (
              <Link
                key={l.href}
                href={l.href}
                className="dell-heading"
                style={{
                  fontSize: '10px',
                  letterSpacing: '0.08em',
                  textDecoration: 'none',
                  color: active ? '#fcc20f' : '#a1a1aa',
                  display: 'flex',
                  alignItems: 'center',
                  padding: '0 14px',
                  borderRight: '1px solid #1a1a1a',
                  borderBottom: active ? '2px solid #fcc20f' : '2px solid transparent',
                  background: active ? '#111' : 'transparent',
                }}
              >
                {l.label}
              </Link>
            )
          })}
        </div>

        {/* Desktop right: auth + social + Discord sticker */}
        <div className="nav-desktop" style={{ gap: '8px', alignItems: 'center', flex: 1, justifyContent: 'flex-end' }}>
          <a
            href={X_URL}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Follow Beezy.FYI on X"
            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '28px', height: '28px', border: '1px solid #333', color: '#a1a1aa', textDecoration: 'none' }}
          >
            <XMark size={13} color="currentColor" />
          </a>

          {isSignedIn ? (
            <>
              <Link
                href="/dashboard"
                className="dell-heading"
                style={{ fontSize: '10px', letterSpacing: '0.06em', textDecoration: 'none', color: pathname.startsWith('/dashboard') ? '#fcc20f' : '#a1a1aa', padding: '4px 8px', border: '1px solid #333' }}
              >
                Dashboard
              </Link>
              <UserButton />
            </>
          ) : (
            <SignInButton mode="modal">
              <button
                className="dell-heading"
                style={{ fontSize: '10px', letterSpacing: '0.06em', background: 'none', border: '1px solid #333', padding: '5px 10px', color: '#a1a1aa', cursor: 'pointer' }}
              >
                Sign In
              </button>
            </SignInButton>
          )}

          {/* Discord -- yellow sticker (BUY a DELL equivalent) */}
          <a
            href={DISCORD_URL}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: '10px',
              fontFamily: 'Arial, Helvetica, sans-serif',
              fontWeight: 700,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              padding: '6px 12px',
              background: '#fcc20f',
              color: '#000',
              textDecoration: 'none',
              border: '1px solid #000',
              height: '100%',
              alignSelf: 'stretch',
            }}
          >
            <DiscordMark size={13} color="currentColor" />
            <span>Join Discord</span>
          </a>
        </div>

        {/* Mobile: auth + social. BottomNav handles routing. */}
        <div className="mobile-only" style={{ alignItems: 'center', gap: '8px' }}>
          {isSignedIn ? (
            <UserButton />
          ) : (
            <SignInButton mode="modal">
              <button
                className="dell-heading"
                style={{ fontSize: '10px', letterSpacing: '0.04em', background: 'none', border: '1px solid #333', padding: '4px 8px', color: '#a1a1aa', cursor: 'pointer' }}
              >
                Sign In
              </button>
            </SignInButton>
          )}
          <a
            href={X_URL}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Follow Beezy.FYI on X"
            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '27px', height: '27px', border: '1px solid #333', color: '#a1a1aa', textDecoration: 'none' }}
          >
            <XMark size={13} color="currentColor" />
          </a>
          {/* Mobile Discord sticker */}
          <a
            href={DISCORD_URL}
            target="_blank"
            rel="noopener noreferrer"
            style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', fontSize: '10px', fontFamily: 'Arial, Helvetica, sans-serif', fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', padding: '5px 9px', background: '#fcc20f', color: '#000', textDecoration: 'none', border: '1px solid #000' }}
          >
            <DiscordMark size={13} color="currentColor" />
            Discord
          </a>
        </div>
      </div>
    </nav>
  )
}
