export const dynamic = 'force-dynamic'

import { apiGetStats } from '@/lib/betting-api'
import { SYSTEM_COLOR } from '@/lib/tokens'
import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'Join Beezy.FYI' }

const PRE_LAUNCH = true
const B = '1px solid var(--basalt)'

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
      <div style={{ minHeight: '70vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '64px 16px' }}>
        <div style={{ width: '100%', maxWidth: '440px', textAlign: 'center' }}>
          <p className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'var(--fog)', marginBottom: '16px' }}>Pre-Launch</p>
          <h1 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)', marginBottom: '16px' }}>
            Join the waitlist
          </h1>
          <p className="times" style={{ fontSize: '15px', color: 'var(--silver)', lineHeight: 1.6, maxWidth: '360px', margin: '0 auto 28px' }}>
            Models are in paper mode. Join Discord to get picks now &mdash; free, no account needed.
            Paid access opens once the first system clears a 200-bet validation gate.
          </p>
          <a href="https://discord.gg/beezy" target="_blank" rel="noopener noreferrer"
            className="btn btn-primary" style={{ width: '100%', marginBottom: '10px' }}>
            Join Discord (free picks now)
          </a>
          <p className="mono" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '40px' }}>
            Paid access launches when systems clear the 200-bet gate.
          </p>

          {/* Live gate progress */}
          <div style={{ border: B, borderRadius: 'var(--radius-xl)', background: 'var(--graphite)', boxShadow: 'var(--shadow-md)', padding: '22px', textAlign: 'left' }}>
            <p className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '18px' }}>
              200-Bet Gate Progress
            </p>
            {SYSTEMS.map(s => {
              const stat = bySystem.find(b => b.system === s.system)
              const count = stat ? parseInt(String(stat.total_bets)) : 0
              const pct   = Math.min(100, (count / 200) * 100)
              return (
                <div key={s.system} style={{ marginBottom: '14px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                    <span className="mono" style={{ fontSize: '11px', fontWeight: 600, color: s.color }}>{s.system}</span>
                    <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>{count}/200</span>
                  </div>
                  <div style={{ height: '7px', borderRadius: 'var(--radius-pill)', background: 'var(--slate)', overflow: 'hidden' }}>
                    <div style={{ height: '7px', borderRadius: 'var(--radius-pill)', background: s.color, width: `${pct}%`, transition: 'width 0.4s var(--ease-out)' }} />
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
    <div style={{ minHeight: '70vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '64px 16px' }}>
      <div style={{ width: '100%', maxWidth: '360px', textAlign: 'center' }}>
        <h1 className="dell-display" style={{ fontSize: '28px', color: 'var(--chalk)', marginBottom: '24px' }}>Create Account</h1>
        {/* <SignUp /> -- uncomment when PRE_LAUNCH = false */}
      </div>
    </div>
  )
}
