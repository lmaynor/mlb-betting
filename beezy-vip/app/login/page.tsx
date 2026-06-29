export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'Log In -- Beezy.FYI' }

const B = '1px solid var(--basalt)'

export default function LoginPage() {
  return (
    <div style={{ minHeight: '70vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '64px 16px' }}>
      <div style={{ width: '100%', maxWidth: '380px' }}>
        <div style={{ textAlign: 'center', marginBottom: '28px' }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '9px', marginBottom: '14px' }}>
            <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--signal)', boxShadow: '0 0 8px var(--signal)' }} />
            <span className="dell-display" style={{ fontSize: '17px', fontWeight: 800, color: 'var(--chalk)' }}>BEEZY<span style={{ color: 'var(--signal)' }}>.FYI</span></span>
          </div>
          <h1 className="dell-display" style={{ fontSize: '26px', color: 'var(--chalk)' }}>Sign in</h1>
        </div>
        <div style={{ border: B, borderRadius: 'var(--radius-xl)', background: 'var(--graphite)', boxShadow: 'var(--shadow-md)', padding: '32px', textAlign: 'center' }}>
          <p className="times" style={{ fontSize: '14px', color: 'var(--silver)', lineHeight: 1.6, marginBottom: '20px' }}>
            Paid access launches after the first system clears 200 bets.
            Join Discord for free picks now.
          </p>
          <a href="https://discord.gg/beezy" target="_blank" rel="noopener noreferrer"
            className="btn btn-primary" style={{ width: '100%' }}>
            Join Discord (free)
          </a>
        </div>
      </div>
    </div>
  )
}
