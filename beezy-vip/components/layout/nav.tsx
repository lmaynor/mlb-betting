'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useUser, UserButton, SignInButton } from '@clerk/nextjs'
import { BrandWordmark } from '@/components/ui/brand-mark'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'
import { LIVE_SPORTS } from '@/lib/sports'
import { NAV, navActive, sportActive, sportHref } from '@/lib/ia'

import { DISCORD_URL, X_URL } from '@/lib/links'
import { PRE_LAUNCH } from '@/lib/launch'
import { InstallAppButton } from '@/components/layout/install-app-button'


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

const SIGN_IN_STYLE: React.CSSProperties = { fontFamily: 'var(--font-text), sans-serif', fontSize: '13px', fontWeight: 600, background: 'transparent', border: '1px solid #1e2230', borderRadius: 'var(--radius-sm)', padding: '7px 12px', color: 'var(--ash)', cursor: 'pointer', whiteSpace: 'nowrap' }

/** `authDisabled` (lib/auth-mode.ts) renders the nav with no Clerk hooks/components, so it works without a ClerkProvider. */
export function Nav({ authDisabled = false }: { authDisabled?: boolean }) {
  if (authDisabled) {
    return (
      <NavShell isSignedIn={false} userButton={null}
        signIn={<Link href="/login" style={{ ...SIGN_IN_STYLE, textDecoration: 'none' }}>Sign In</Link>} />
    )
  }
  return <ClerkNav />
}

function ClerkNav() {
  const { isSignedIn } = useUser()
  return (
    <NavShell
      isSignedIn={!!isSignedIn}
      userButton={<UserButton />}
      signIn={
        PRE_LAUNCH ? (
          <Link href="/login" style={{ ...SIGN_IN_STYLE, textDecoration: 'none' }}>Sign In</Link>
        ) : (
          <SignInButton mode="modal"><button style={SIGN_IN_STYLE}>Sign In</button></SignInButton>
        )
      }
    />
  )
}

function NavShell({ isSignedIn, userButton, signIn }: {
  isSignedIn: boolean; userButton: React.ReactNode; signIn: React.ReactNode
}) {
  const pathname  = usePathname()

  return (
    <header style={{ width: '100%', maxWidth: '100vw' }}>
      {/* TIER 1 -- product bar */}
      <nav style={{ background: '#090a0f', borderBottom: '1px solid #1e2230', overflow: 'hidden' }}>
        <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '0 24px', display: 'flex', alignItems: 'center', height: '56px', gap: '20px' }}>

          <Link href="/" aria-label="beezy.fyi home" style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', flexShrink: 0 }}>
            <BrandWordmark size="md" showDomain />
          </Link>

          <div style={{ display: 'flex', alignItems: 'center', gap: '2px', flex: 1, justifyContent: 'center' }} className="nav-desktop">
            {NAV.map((l) => {
              const active = navActive(pathname, l)
              return (
                <Link
                  key={l.href}
                  href={l.href}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '7px',
                    fontSize: '13px', fontWeight: 500, textDecoration: 'none',
                    color: active ? 'var(--signal)' : 'var(--silver)',
                    padding: '7px 11px', borderRadius: 'var(--radius-sm)',
                    background: active ? 'var(--brand-dim)' : 'transparent',
                    transition: 'color var(--dur) var(--ease-out), background var(--dur) var(--ease-out)',
                  }}
                >
                  {l.label}
                </Link>
              )
            })}
          </div>

          <div className="nav-desktop" style={{ gap: '10px', alignItems: 'center', flexShrink: 0, marginLeft: 'auto' }}>
            <InstallAppButton />
            <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" aria-label="Join Discord" style={iconBox}>
              <DiscordMark size={15} color="currentColor" />
            </a>
            <a href={X_URL} target="_blank" rel="noopener noreferrer" aria-label="Follow Beezy.FYI on X" style={iconBox} className="nav-xl">
              <XMark size={14} color="currentColor" />
            </a>
            {isSignedIn ? (
              <>
                <Link
                  href="/dashboard"
                  style={{
                    fontSize: '13px', fontWeight: 600, textDecoration: 'none',
                    color: pathname.startsWith('/dashboard') ? 'var(--signal)' : 'var(--ash)',
                    padding: '7px 12px', border: '1px solid #1e2230', borderRadius: 'var(--radius-sm)',
                  }}
                >
                  Dashboard
                </Link>
                {userButton}
              </>
            ) : (
              <>
                {signIn}
                <Link href="/pricing" style={{ fontSize: '12px', fontWeight: 700, letterSpacing: '0.02em', textDecoration: 'none', background: '#57F287', color: '#090a0f', borderRadius: '6px', padding: '8px 12px', whiteSpace: 'nowrap' }}>
                  {PRE_LAUNCH ? 'JOIN WAITLIST' : 'UPGRADE TO PRO'}
                </Link>
              </>
            )}
          </div>

          {/* Mobile: auth + Discord + Install. BottomNav handles routing. */}
          <div className="mobile-only" style={{ alignItems: 'center', gap: '8px', marginLeft: 'auto' }}>
            <InstallAppButton compact />
            {isSignedIn ? (
              userButton
            ) : (
              signIn
            )}
            <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" aria-label="Join Discord" style={{ ...discordBtn, padding: '7px 11px' }}>
              <DiscordMark size={15} color="currentColor" />
            </a>
          </div>
        </div>
      </nav>

      {/* TIER 2 -- multi-sport context bar */}
      <div style={{ background: '#11131a', borderBottom: '1px solid #1e2230', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
        <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '0 24px', display: 'flex', alignItems: 'center', height: '40px', gap: '4px', whiteSpace: 'nowrap' }}>
          <SportLink href={sportHref('all')} label="ALL SPORTS" active={sportActive(pathname, 'all')} />
          {LIVE_SPORTS.map(p => (
            <SportLink
              key={p.slug}
              href={sportHref(p.slug)}
              label={p.shortName}
              active={sportActive(pathname, p.slug)}
              accent={p.accent}
            />
          ))}
        </div>
      </div>
    </header>
  )
}

function SportLink({ href, label, active, accent }: { href: string; label: string; active: boolean; accent?: string }) {
  const color = active ? (accent ?? 'var(--chalk)') : 'var(--silver)'
  return (
    <Link
      href={href}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: '7px', textDecoration: 'none',
        fontSize: '11.5px', fontWeight: 600, letterSpacing: '0.05em', color,
        padding: '5px 10px', borderRadius: 'var(--radius-sm)',
        background: active ? 'var(--surface-elevated)' : 'transparent',
        border: `1px solid ${active ? '#2e3446' : 'transparent'}`,
      }}
    >
      {label}
    </Link>
  )
}
