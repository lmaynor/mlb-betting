export const dynamic = 'force-dynamic'

import { apiGetStats } from '@/lib/betting-api'
import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'Join Beezy.FYI' }

const PRE_LAUNCH = true
const B = '1px solid #1f1f24'

const SYSTEMS = [
  { system: 'OUTS', color: '#e6915d' },
  { system: 'K',    color: '#8c9ae0' },
  { system: 'HR',   color: '#e6915d' },
  { system: 'F5',   color: '#9ab6c8' },
  { system: 'NRFI', color: '#b3bd95' },
]

export default async function SignupPage() {
  // Pull live bet counts from API
  const bySystem = await apiGetStats().then(s => s.bySystem).catch(() => [])

  if (PRE_LAUNCH) {
    return (
      <div style={{ minHeight: '70vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '64px 16px' }}>
        <div style={{ width: '100%', maxWidth: '440px', textAlign: 'center' }}>
          <p className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: '#888890', marginBottom: '16px' }}>Pre-Launch</p>
          <h1 className="dell-display" style={{ fontSize: '22px', color: '#f5f5f7', marginBottom: '16px' }}>
            Join the Waitlist
          </h1>
          <p style={{ fontSize: '13px', color: '#a1a1aa', lineHeight: 1.65, marginBottom: '28px', maxWidth: '360px', margin: '0 auto 28px' }}>
            Models are in paper mode. Join Discord to get picks now &mdash; free, no account needed.
            Paid access opens once the first system clears a 200-bet validation gate.
          </p>
          <a href="https://discord.gg/beezy" target="_blank" rel="noopener noreferrer"
            className="mono" style={{
              display: 'block', padding: '12px 16px', marginBottom: '8px',
              background: '#fcc20f', color: '#0a0a0c',
              fontSize: '11px', fontWeight: 600,
              letterSpacing: '0.06em', textTransform: 'uppercase',
              textDecoration: 'none',
            }}>
            Join Discord (Free Picks Now)
          </a>
          <p className="mono" style={{ fontSize: '10px', color: '#888890', marginBottom: '40px' }}>
            Paid access launches when systems clear the 200-bet gate.
          </p>

          {/* Live gate progress */}
          <div style={{ border: B, padding: '20px', textAlign: 'left' }}>
            <p className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: '#888890', marginBottom: '16px' }}>
              200-Bet Gate Progress
            </p>
            {SYSTEMS.map(s => {
              const stat = bySystem.find(b => b.system === s.system)
              const count = stat ? parseInt(String(stat.total_bets)) : 0
              const pct   = Math.min(100, (count / 200) * 100)
              return (
                <div key={s.system} style={{ marginBottom: '12px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '5px' }}>
                    <span className="mono" style={{ fontSize: '10px', color: '#a1a1aa' }}>{s.system}</span>
                    <span className="mono" style={{ fontSize: '10px', color: '#888890' }}>{count}/200</span>
                  </div>
                  <div style={{ height: '2px', background: '#1f1f24' }}>
                    <div style={{ height: '2px', background: s.color, width: `${pct}%`, transition: 'width 0.3s' }} />
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
        <h1 style={{ fontSize: '18px', fontWeight: 600, color: '#f5f5f7', marginBottom: '24px' }}>Create Account</h1>
        {/* <SignUp /> -- uncomment when PRE_LAUNCH = false */}
      </div>
    </div>
  )
}
