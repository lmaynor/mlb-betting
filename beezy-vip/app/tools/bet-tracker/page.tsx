export const dynamic = 'force-dynamic'
import type { Metadata } from 'next'
export const metadata: Metadata = { title: 'Personal Bet Tracker', robots: { index: false, follow: false } }
const B = '0.5px solid #1f1f24'
export default function BetTrackerPage() {
  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '24px' }}>
        <p className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#a78bfa', marginBottom: '6px' }}>Tools · Pro</p>
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '6px' }}>Personal Bet Tracker</h1>
        <p style={{ fontSize: '13px', color: '#71717a' }}>Log your own bets, track ROI, and compare your performance against the Beezy model. Pro tier only.</p>
      </div>
      <div style={{ border: B, padding: '40px', textAlign: 'center' }}>
        <p className="mono" style={{ fontSize: '12px', color: '#71717a', marginBottom: '16px' }}>Available to Pro subscribers after launch.</p>
        <a href="https://discord.gg/beezy" className="mono" style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '8px 20px', background: '#10b981', color: '#0a0a0c', textDecoration: 'none' }}>Join Waitlist</a>
      </div>
    </div>
  )
}
