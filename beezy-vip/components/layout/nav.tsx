'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useUser, UserButton, SignInButton } from '@clerk/nextjs'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'
import { SPORT_LIST } from '@/lib/sports'
import { useBankroll } from '@/lib/use-bankroll'
import { fmtMoney } from '@/lib/ev-math'

const DISCORD_URL = 'https://discord.gg/HfMYCmbmE'
const X_URL = 'https://x.com/beezy_fyi'
const NAV_LINKS = [
  { label: 'Home',          href: '/' },
  { label: '+EV Terminal',  href: '/ev', live: true },
  { label: 'The Edge',      href: '/edge' },
  { label: 'Picks',         href: '/picks' },
  { label: 'Analytics',     href: '/analytics' },
  { label: 'Results',       href: '/results' },
  { label: 'Tools',         href: '/tools' },
]

function isActive(pathname: string, href: string) {
  return href === '/' ? pathname === '/' : pathname === href || pathname.startsWith(href + '/')
}

function Emblem() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true" style={{ flexShrink: 0 }}>
      <rect x="0.5" y="0.5" width="27" height="27" rx="7" fill="#11131a" stroke="#1e2230" />
      <path d="M14 4.5 23.5 14 14 23.5 4.5 14Z" fill="none" stroke="#57F287" strokeOpacity="0.35" strokeWidth="1.2" />
      <path d="M9.5 18.5 18.5 9.5" stroke="#57F287" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  )
}

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
      <NavShell isSignedIn={false} bankrollDefault={1000} userButton={null}
        signIn={<Link href="/login" style={{ ...SIGN_IN_STYLE, textDecoration: 'none' }}>Sign In</Link>} />
    )
  }
  return <ClerkNav />
}

function ClerkNav() {
  const { isSignedIn, user } = useUser()
  return (
    <NavShell
      isSignedIn={!!isSignedIn}
      bankrollDefault={(user?.publicMetadata?.bankroll as number | undefined) ?? 1000}
      userButton={<UserButton />}
      signIn={<SignInButton mode="modal"><button style={SIGN_IN_STYLE}>Sign In</button></SignInButton>}
    />
  )
}

function NavShell({ isSignedIn, bankrollDefault, userButton, signIn }: {
  isSignedIn: boolean; bankrollDefault: number; userButton: React.ReactNode; signIn: React.ReactNode
}) {
  const pathname  = usePathname()
  const [bankroll] = useBankroll(bankrollDefault)

  return (
    <header style={{ position: 'sticky', top: 0, zIndex: 50, width: '100%', maxWidth: '100vw' }}>
      {/* TIER 1 -- product bar */}
      <nav style={{ background: '#090a0f', borderBottom: '1px solid #1e2230', overflow: 'hidden' }}>
        <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '0 24px', display: 'flex', alignItems: 'center', height: '56px', gap: '20px' }}>

          <Link href="/" style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
            <Emblem />
            <span className="dell-display" style={{ fontSize: '17px', color: 'var(--chalk)', letterSpacing: '-0.01em', lineHeight: 1 }}>
              BEEZY<span style={{ color: 'var(--signal)' }}>.FYI</span>
            </span>
          </Link>

          <div style={{ display: 'flex', alignItems: 'center', gap: '2px', flex: 1, justifyContent: 'center' }} className="nav-desktop">
            {NAV_LINKS.map((l) => {
              const active = isActive(pathname, l.href)
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
                  {l.live && <span className="live-dot" style={{ width: '6px', height: '6px' }} />}
                  {l.label}
                </Link>
              )
            })}
          </div>

          <div className="nav-desktop" style={{ gap: '10px', alignItems: 'center', flexShrink: 0, marginLeft: 'auto' }}>
            <Link
              href="/tools/kelly-calculator"
              className="mono nav-xl"
              title="Your bankroll (used to size stakes). Click to open the Kelly calculator."
              style={{
                fontSize: '12px', fontWeight: 600, textDecoration: 'none', color: 'var(--ash)',
                border: '1px solid #1e2230', background: '#11131a', borderRadius: 'var(--radius-sm)', padding: '6px 10px',
              }}
            >
              <span style={{ color: 'var(--fog)', marginRight: '6px' }}>BANKROLL</span>{fmtMoney(bankroll)}
            </Link>
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
                  UPGRADE TO PRO
                </Link>
              </>
            )}
          </div>

          {/* Mobile: auth + Discord. BottomNav handles routing. */}
          <div className="mobile-only" style={{ alignItems: 'center', gap: '8px', marginLeft: 'auto' }}>
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
          <SportLink href="/" label="ALL SPORTS" active={pathname === '/'} />
          {SPORT_LIST.map(p => (
            <SportLink
              key={p.slug}
              href={`/sports/${p.slug}`}
              label={p.shortName}
              badge={p.navBadge}
              active={pathname === `/sports/${p.slug}`}
              accent={p.accent}
            />
          ))}
          <span style={{ flex: 1 }} />
          <SportLink href="/cheat-sheet" label="DAILY CARD" active={pathname.startsWith('/cheat-sheet')} />
        </div>
      </div>
    </header>
  )
}

function SportLink({ href, label, badge, active, accent }: { href: string; label: string; badge?: string; active: boolean; accent?: string }) {
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
      {badge && (
        <span style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.02em', color: 'var(--fog)', border: '1px solid #1e2230', borderRadius: '4px', padding: '1px 5px' }}>
          {badge}
        </span>
      )}
    </Link>
  )
}
