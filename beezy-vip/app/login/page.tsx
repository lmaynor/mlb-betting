export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'Log In -- Beezy.VIP' }

const B = '0.5px solid #1f1f24'

export default function LoginPage() {
  return (
    <div style={{ minHeight: '70vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '64px 16px' }}>
      <div style={{ width: '100%', maxWidth: '360px' }}>
        <div style={{ textAlign: 'center', marginBottom: '32px' }}>
          <p className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#10b981', marginBottom: '8px' }}>Beezy.VIP</p>
          <h1 style={{ fontSize: '18px', fontWeight: 600, color: '#f5f5f7', letterSpacing: '-0.01em' }}>Sign In</h1>
        </div>
        <div style={{ border: B, padding: '32px', textAlign: 'center' }}>
          <p className="mono" style={{ fontSize: '11px', color: '#71717a', marginBottom: '16px' }}>
            Paid access launches after the first system clears 200 bets.
            Join Discord for free picks now.
          </p>
          <a href="https://discord.gg/beezy" target="_blank" rel="noopener noreferrer"
            className="mono" style={{
              display: 'block', padding: '10px 16px',
              background: '#10b981', color: '#0a0a0c',
              fontSize: '11px', fontWeight: 600,
              letterSpacing: '0.06em', textTransform: 'uppercase',
              textDecoration: 'none', textAlign: 'center',
            }}>
            Join Discord (Free)
          </a>
        </div>
      </div>
    </div>
  )
}
