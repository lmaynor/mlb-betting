export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { SignIn } from '@clerk/nextjs'
import { AUTH_DISABLED } from '@/lib/auth-mode'
import { PRE_LAUNCH } from '@/lib/launch'
import { DISCORD_URL } from '@/lib/links'

export const metadata: Metadata = { title: 'Sign in' }

export default function LoginPage() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center px-4 py-12">
      <div className="grid w-full max-w-[400px] justify-items-center gap-4">
        <h1 className="text-title-2-bold text-text-primary">Sign in</h1>
        {/* The Clerk form only exists when Clerk is on (never under AUTH_DISABLED, which has no ClerkProvider). */}
        {!AUTH_DISABLED && <SignIn routing="hash" signUpUrl="/signup" />}
        <div className="w-full rounded-xl border border-separator-border bg-background-primary-default px-4.5 py-4 text-center shadow-card">
          <p className="mb-3 text-body-2-regular text-text-secondary">
            {PRE_LAUNCH ? 'Paid plans are not open yet. Picks, +EV alerts and results are free to read; join the Discord for updates.' : 'Need an account? Create one on the sign-up page.'}
          </p>
          <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" className="btn btn-primary w-full">
            Join the Discord
          </a>
        </div>
      </div>
    </div>
  )
}
