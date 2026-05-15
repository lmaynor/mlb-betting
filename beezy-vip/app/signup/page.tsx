import type { Metadata } from 'next'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = { title: 'Join Beezy.VIP' }

// PRE_LAUNCH = true until Stripe is enabled
const PRE_LAUNCH = true

export default function SignupPage() {
  if (PRE_LAUNCH) {
    return (
      <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
        <div className="w-full max-w-md text-center">
          <p className="mono text-xs text-accent uppercase tracking-widest mb-4">Pre-Launch</p>
          <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-4">
            Join the Waitlist
          </h1>
          <p className="text-muted text-sm leading-relaxed mb-8">
            Models are in paper mode. Join the Discord to get picks now — free, no account needed.
            We&apos;ll open paid access once the first system clears 200 bets.
          </p>
          <div className="space-y-3">
            <a
              href="https://discord.gg/beezy"
              target="_blank"
              rel="noopener noreferrer"
              className="block w-full mono text-sm uppercase tracking-widest py-4 bg-accent text-bg font-bold hover:bg-accent/90 transition-colors text-center"
            >
              Join Discord (Free Picks Now)
            </a>
            <p className="mono text-xs text-muted">
              Paid access launches when systems clear the 200-bet gate.
            </p>
          </div>

          {/* Progress */}
          <div className="mt-12 border border-[var(--border)] p-5 text-left">
            <p className="mono text-xs uppercase tracking-widest text-muted mb-4">200-Bet Gate Progress</p>
            {[
              { system: 'NRFI', bets: 28 },
              { system: 'K',    bets: 14 },
              { system: 'HR',   bets: 19 },
              { system: 'F5',   bets: 18 },
              { system: 'OUTS', bets: 8  },
            ].map(s => (
              <div key={s.system} className="mb-3">
                <div className="flex justify-between mb-1">
                  <span className="mono text-xs text-muted">{s.system}</span>
                  <span className="mono text-xs text-muted">{s.bets}/200</span>
                </div>
                <div className="h-1 bg-[var(--border)]">
                  <div
                    className="h-1 bg-accent transition-all"
                    style={{ width: `${(s.bets / 200) * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  // Post-launch: show Clerk SignUp
  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-xl font-extrabold uppercase tracking-tight">Create Account</h1>
        </div>
        {/* <SignUp /> — uncomment when PRE_LAUNCH = false */}
      </div>
    </div>
  )
}
