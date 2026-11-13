'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useUser, UserButton, SignInButton } from '@clerk/nextjs'
import { BrandWordmark } from '@/components/ui/brand-mark'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'
import { NAV, SECTIONS, navActive } from '@/lib/ia'

import { DISCORD_URL, X_URL } from '@/lib/links'
import { cx } from '@/utils/cx'
import { PRE_LAUNCH } from '@/lib/launch'
import { InstallAppButton } from '@/components/layout/install-app-button'


// A menu row: full width, 48px tall, icon then label, a hairline between rows. Install is a button with its own modal, so it takes the same look as a style.
const MENU_ROW = 'flex h-12 w-full items-center gap-3 border-b border-separator-border px-4 text-[15px] text-text-primary no-underline hover:bg-background-primary-hover'
const ROW_STYLE: React.CSSProperties = {
  height: 48, width: '100%', padding: '0 16px', gap: 14, borderRadius: 0, border: 0, borderBottom: '1px solid var(--color-separator-border)',
  background: 'transparent', fontSize: 15, fontWeight: 400, justifyContent: 'flex-start',
}

const SIGN_IN = 'button-press-motion cursor-pointer whitespace-nowrap rounded-lg border border-separator-border bg-transparent px-3 py-[7px] text-body-2-semibold text-text-primary no-underline hover:bg-background-primary-hover'

/** `authDisabled` (lib/auth-mode.ts) renders the nav with no Clerk hooks/components, so it works without a ClerkProvider. */
export function Nav({ authDisabled = false }: { authDisabled?: boolean }) {
  if (authDisabled) {
    return (
      <NavShell isSignedIn={false} userButton={null}
        signIn={<Link href="/login" className={SIGN_IN}>Sign In</Link>} />
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
          <Link href="/login" className={SIGN_IN}>Sign In</Link>
        ) : (
          <SignInButton mode="modal"><button className={SIGN_IN}>Sign In</button></SignInButton>
        )
      }
    />
  )
}

function NavShell({ isSignedIn, userButton, signIn }: {
  isSignedIn: boolean; userButton: React.ReactNode; signIn: React.ReactNode
}) {
  const pathname  = usePathname()
  const [menuOpen, setMenuOpen] = useState(false)
  const headerRef = useRef<HTMLElement>(null)

  // The menu closes on navigation, Escape, and a tap anywhere outside the header.
  useEffect(() => { setMenuOpen(false) }, [pathname])
  useEffect(() => {
    const open = () => setMenuOpen(true)
    window.addEventListener('beezy:open-menu', open)
    return () => window.removeEventListener('beezy:open-menu', open)
  }, [])
  useEffect(() => {
    if (!menuOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuOpen(false) }
    const onDown = (e: MouseEvent | TouchEvent) => { if (headerRef.current && !headerRef.current.contains(e.target as Node)) setMenuOpen(false) }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('touchstart', onDown)
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onDown); document.removeEventListener('touchstart', onDown) }
  }, [menuOpen])

  return (
    <header ref={headerRef} className="relative w-full max-w-[100vw]">
      {/* TIER 1 -- product bar */}
      <nav className="overflow-hidden border-b border-separator-border bg-background-secondary-default">
        <div className="mx-auto flex h-12 md:h-14 max-w-[1280px] items-center gap-5 px-6">

          <Link href="/" aria-label="beezy.fyi home" className="flex shrink-0 items-center no-underline">
            <BrandWordmark size="bar" showDomain />
          </Link>

          <div className="nav-desktop flex flex-1 items-center justify-center gap-0.5">
            {NAV.map((l) => {
              const active = navActive(pathname, l)
              return (
                <Link
                  key={l.href}
                  href={l.href}
                  className={cx('inline-flex items-center gap-[7px] rounded-md px-[11px] py-[7px] text-body-2-medium no-underline transition-colors',
                    active ? 'bg-[var(--brand-dim)] text-signal' : 'bg-transparent text-text-secondary hover:text-text-primary')}
                >
                  {l.label}
                </Link>
              )
            })}
          </div>

          <div className="nav-desktop ml-auto shrink-0 items-center gap-2.5">
            {isSignedIn ? (
              <>
                <Link
                  href="/dashboard"
                  className={cx('rounded-md border border-separator-border px-3 py-[7px] text-body-2-semibold no-underline', pathname.startsWith('/dashboard') ? 'text-signal' : 'text-text-primary')}
                >
                  Dashboard
                </Link>
                {userButton}
              </>
            ) : (
              <>
                {signIn}
                <Link href="/pricing" className="whitespace-nowrap rounded-md bg-signal px-3 py-2 text-caption-1-bold text-background-secondary-default no-underline hover:bg-[#7bf5a4]">
                  {PRE_LAUNCH ? 'Join waitlist' : 'Upgrade to Pro'}
                </Link>
              </>
            )}
          </div>

          {/* Mobile: the account button (when signed in) and a menu for Install, Discord, X and sign in. BottomNav handles routing. */}
          <div className="mobile-only nav-mobile-actions ml-auto items-center gap-2">
            {isSignedIn && userButton}
            <button type="button" aria-label={menuOpen ? 'Close menu' : 'Open menu'} aria-expanded={menuOpen} aria-controls="nav-menu" data-testid="nav-burger"
              onClick={() => setMenuOpen(o => !o)} className="button-press-motion inline-flex cursor-pointer items-center justify-center rounded-2lg border border-separator-border bg-transparent text-text-primary hover:border-border-button-hover" style={{ width: 44, height: 44 }}>
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                {menuOpen ? <path d="M4 4l10 10M14 4L4 14" /> : <path d="M3 5h12M3 9h12M3 13h12" />}
              </svg>
            </button>
          </div>
        </div>
      </nav>

      {menuOpen && (
        <>
          {/* Scrim: dims the page under the header and closes the menu on a tap. */}
          <div aria-hidden onClick={() => setMenuOpen(false)} className="mobile-only absolute inset-x-0 top-full z-[59] bg-black/60" style={{ height: '100dvh' }} />
          <div id="nav-menu" data-testid="nav-menu" className="mobile-only absolute inset-x-0 top-full z-[60] max-h-[calc(100dvh-48px)] flex-col overflow-y-auto border-b border-separator-border bg-background-secondary-default shadow-card">
            <Link href="/learn" className={MENU_ROW}>Learn</Link>
            {NAV.filter(n => n.label !== 'Learn').map(n => (
              <Link key={n.href} href={n.href} className={MENU_ROW}>{n.label}</Link>
            ))}
            {SECTIONS.map(s => (
              <div key={s.id}>
                <div className="ui-heading px-4 pb-1 pt-3 text-micro text-text-secondary">{s.label}</div>
                {s.tabs.map(t => (
                  <Link key={t.href} href={t.href} className={MENU_ROW}>{t.label}</Link>
                ))}
              </div>
            ))}
            <InstallAppButton style={ROW_STYLE} />
            <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" className={MENU_ROW}>
              <span className="inline-flex w-[12px] flex-none justify-center"><DiscordMark size={14} color="currentColor" /></span> Discord
            </a>
            <a href={X_URL} target="_blank" rel="noopener noreferrer" className={MENU_ROW}>
              <span className="inline-flex w-[12px] flex-none justify-center"><XMark size={14} color="currentColor" /></span> Follow on X
            </a>
            <div className="grid gap-2 px-4 py-3 [&>a]:flex [&>a]:min-h-[44px] [&>a]:items-center [&>a]:justify-center [&>button]:min-h-[44px] [&>button]:w-full">
              {isSignedIn ? (
                <Link href="/dashboard" className="rounded-lg border border-separator-border text-body-2-semibold text-text-primary no-underline">Dashboard</Link>
              ) : (
                <>
                  {signIn}
                  <Link href="/pricing" className="rounded-lg bg-signal text-caption-1-bold text-background-secondary-default no-underline">
                    {PRE_LAUNCH ? 'Join waitlist' : 'Upgrade to Pro'}
                  </Link>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </header>
  )
}
