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
import { cx } from '@/utils/cx'
import { PRE_LAUNCH } from '@/lib/launch'
import { InstallAppButton } from '@/components/layout/install-app-button'


const ICON_BOX = 'button-press-motion inline-flex size-[34px] items-center justify-center rounded-2lg border border-separator-border bg-background-primary-default text-text-secondary no-underline hover:border-border-button-hover hover:text-text-primary'

const DISCORD_BTN = 'inline-flex items-center gap-[7px] rounded-2lg border px-[11px] py-[7px] text-body-2-semibold no-underline'
const DISCORD_BTN_STYLE: React.CSSProperties = {
  background: 'color-mix(in oklab, #5865f2 22%, var(--color-background-full))',
  color: '#c6ccff', borderColor: 'color-mix(in oklab, #5865f2 50%, var(--color-background-full))',
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

  return (
    <header className="w-full max-w-[100vw]">
      {/* TIER 1 -- product bar */}
      <nav className="overflow-hidden border-b border-separator-border bg-background-secondary-default">
        <div className="mx-auto flex h-14 max-w-[1280px] items-center gap-5 px-6">

          <Link href="/" aria-label="beezy.fyi home" className="flex shrink-0 items-center no-underline">
            <BrandWordmark size="md" showDomain />
          </Link>

          <div className="nav-desktop flex flex-1 items-center justify-center gap-0.5">
            {NAV.map((l) => {
              const active = navActive(pathname, l)
              return (
                <Link
                  key={l.href}
                  href={l.href}
                  className={cx('inline-flex items-center gap-[7px] rounded-md px-[11px] py-[7px] text-body-2-medium no-underline transition-colors',
                    active ? 'bg-[var(--brand-dim)] text-[var(--signal)]' : 'bg-transparent text-text-secondary hover:text-text-primary')}
                >
                  {l.label}
                </Link>
              )
            })}
          </div>

          <div className="nav-desktop ml-auto shrink-0 items-center gap-2.5">
            <InstallAppButton />
            <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" aria-label="Join Discord" className={ICON_BOX}>
              <DiscordMark size={15} color="currentColor" />
            </a>
            <a href={X_URL} target="_blank" rel="noopener noreferrer" aria-label="Follow Beezy.FYI on X" className={cx(ICON_BOX, 'nav-xl')}>
              <XMark size={14} color="currentColor" />
            </a>
            {isSignedIn ? (
              <>
                <Link
                  href="/dashboard"
                  className={cx('rounded-md border border-separator-border px-3 py-[7px] text-body-2-semibold no-underline', pathname.startsWith('/dashboard') ? 'text-[var(--signal)]' : 'text-text-primary')}
                >
                  Dashboard
                </Link>
                {userButton}
              </>
            ) : (
              <>
                {signIn}
                <Link href="/pricing" className="whitespace-nowrap rounded-md bg-[#57F287] px-3 py-2 text-caption-1-bold tracking-[0.02em] text-[var(--color-background-secondary-default)] no-underline hover:bg-[#7bf5a4]">
                  {PRE_LAUNCH ? 'JOIN WAITLIST' : 'UPGRADE TO PRO'}
                </Link>
              </>
            )}
          </div>

          {/* Mobile: auth + Discord + Install. BottomNav handles routing. */}
          <div className="mobile-only nav-mobile-actions ml-auto items-center gap-2">
            <InstallAppButton compact />
            {isSignedIn ? (
              userButton
            ) : (
              signIn
            )}
            <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" aria-label="Join Discord" className={DISCORD_BTN} style={DISCORD_BTN_STYLE}>
              <DiscordMark size={15} color="currentColor" />
            </a>
          </div>
        </div>
      </nav>

      {/* TIER 2 -- multi-sport context bar */}
      <div className="overflow-x-auto border-b border-separator-border bg-background-primary-default">
        <div className="sport-ribbon mx-auto flex h-10 max-w-[1280px] items-center gap-1 whitespace-nowrap px-6">
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
  const color = active ? (accent ?? 'var(--color-text-primary)') : undefined
  return (
    <Link
      href={href}
      className={cx('sport-ribbon-link inline-flex items-center gap-[7px] rounded-md border px-2.5 py-[5px] text-caption-1-semibold tracking-[0.05em] no-underline transition-colors',
        active ? 'border-border-button-default bg-background-tertiary-default' : 'border-transparent bg-transparent text-text-secondary hover:text-text-primary')}
      style={color ? { color } : undefined}
    >
      {label}
    </Link>
  )
}
