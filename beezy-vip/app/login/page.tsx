export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { SignIn } from '@clerk/nextjs'
import { AUTH_DISABLED } from '@/lib/auth-mode'
import { PRE_LAUNCH } from '@/lib/launch'
import { DISCORD_URL } from '@/lib/links'

export const metadata: Metadata = { title: 'Sign in' }

const B = '1px solid var(--color-separator-border)'

export default function LoginPage() {
  return (
    <div style={{ minHeight: '60vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '48px 16px' }}>
      <div style={{ width: '100%', maxWidth: '400px', display: 'grid', gap: '16px', justifyItems: 'center' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--color-text-primary)', margin: 0 }}>Sign in</h1>
        {/* The Clerk form only exists when Clerk is on (never under AUTH_DISABLED, which has no ClerkProvider). */}
        {!AUTH_DISABLED && <SignIn routing="hash" signUpUrl="/signup" />}
        <div style={{ width: '100%', border: B, borderRadius: '8px', background: 'var(--color-background-primary-default)', padding: '16px 18px', textAlign: 'center' }}>
          <p style={{ fontSize: '13px', color: 'var(--color-neutral-300)', lineHeight: 1.55, margin: '0 0 12px' }}>
            {PRE_LAUNCH ? 'Paid plans are not open yet. Picks, +EV alerts and results are free to read; join the Discord for updates.' : 'Need an account? Create one on the sign-up page.'}
          </p>
          <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" className="btn btn-primary" style={{ width: '100%' }}>
            Join the Discord
          </a>
        </div>
      </div>
    </div>
  )
}
