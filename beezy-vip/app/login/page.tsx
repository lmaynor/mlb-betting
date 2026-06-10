export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'Log In -- Beezy.FYI' }

const B = '1px solid #1f1f24'

export default function LoginPage() {
  return (
    <div style={{ minHeight: '70vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '64px 16px' }}>
      <div style={{ width: '100%', maxWidth: '360px' }}>
        <div style={{ textAlign: 'center', marginBottom: '32px' }}>
          <p className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: '#888890', marginBottom: '8px' }}>Beezy.FYI</p>
          <h1 className="dell-display" style={{ fontSize: '18px', color: '#f5f5f7' }}>Sign In</h1>
        </div>
        <div style={{ border: B, padding: '32px', textAlign: 'center' }}>
          <p className="times" style={{ fontSize: '11px', color: '#888890', marginBottom: '16px' }}>
            Paid access launches after the first system clears 200 bets.
            Join Discord for free picks now.
          </p>
          <a href="https://discord.gg/beezy" target="_blank" rel="noopener noreferrer"
            className="mono" style={{
              display: 'block', padding: '10px 16px',
              background: '#fcc20f', color: '#0a0a0c',
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
