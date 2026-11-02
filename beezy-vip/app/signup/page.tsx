export const dynamic = 'force-dynamic'

import { apiGetStats } from '@/lib/betting-api'
import { SYSTEM_COLOR } from '@/lib/tokens'
import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'Join beezy.fyi' }

import { PRE_LAUNCH } from '@/lib/launch'
import { DISCORD_URL } from '@/lib/links'

const SYSTEMS = [
  { system: 'OUTS', color: SYSTEM_COLOR.OUTS },
  { system: 'K',    color: SYSTEM_COLOR.K },
  { system: 'HR',   color: SYSTEM_COLOR.HR },
  { system: 'F5',   color: SYSTEM_COLOR.F5 },
  { system: 'NRFI', color: SYSTEM_COLOR.NRFI },
]

export default async function SignupPage() {
  // Pull live bet counts from API
  const bySystem = await apiGetStats().then(s => s.bySystem).catch(() => [])

  if (PRE_LAUNCH) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center px-4 py-16">
        <div className="w-full max-w-[440px] text-center">
          <p className="ui-heading mb-4 tracking-[0.12em] text-text-secondary">Pre-Launch</p>
          <h1 className="ui-display mb-4 text-[30px]">
            Join the waitlist
          </h1>
          <p className="times mx-auto mb-7 max-w-[360px] text-headline-regular text-text-secondary">
            Models are in paper mode. Join Discord to get picks now &mdash; free, no account needed.
            Paid access opens once the first system clears a 200-bet validation gate.
          </p>
          <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer"
            className="btn btn-primary mb-2.5 w-full">
            Join Discord (free picks now)
          </a>
          <p className="mono mb-10 text-micro text-text-secondary">
            Paid access launches when systems clear the 200-bet gate.
          </p>

          {/* Live gate progress */}
          <div className="rounded-2xl border border-separator-border bg-background-primary-default p-5.5 text-left shadow-md">
            <p className="ui-heading mb-4.5 tracking-label text-text-secondary">
              200-Bet Gate Progress
            </p>
            {SYSTEMS.map(s => {
              const stat = bySystem.find(b => b.system === s.system)
              const count = stat ? parseInt(String(stat.total_bets)) : 0
              const pct   = Math.min(100, (count / 200) * 100)
              return (
                <div key={s.system} className="mb-3.5">
                  <div className="mb-1.5 flex justify-between">
                    <span className="mono text-mini font-semibold" style={{ color: s.color }}>{s.system}</span>
                    <span className="mono text-micro text-text-secondary">{count >= 200 ? `200+ (${count.toLocaleString()})` : `${count}/200`}</span>
                  </div>
                  <div className="h-[7px] overflow-hidden rounded-full bg-chart-track">
                    <div className="h-[7px] rounded-full transition-[width] duration-[400ms]" style={{ background: s.color, width: `${pct}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    )
  }

  // Post-launch: Clerk SignUp goes here
  return (
    <div className="flex min-h-[70vh] items-center justify-center px-4 py-16">
      <div className="w-full max-w-[360px] text-center">
        <h1 className="ui-display mb-6 text-[28px]">Create Account</h1>
        {/* <SignUp /> -- uncomment when PRE_LAUNCH = false */}
      </div>
    </div>
  )
}
