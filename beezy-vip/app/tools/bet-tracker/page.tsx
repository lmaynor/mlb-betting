export const dynamic = 'force-dynamic'
import type { Metadata } from 'next'
export const metadata: Metadata = { title: 'Personal Bet Tracker', robots: { index: false, follow: false } }
const B = '1px solid var(--basalt)'
export default function BetTrackerPage() {
  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '24px' }}>
        <p className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '6px' }}>Tools · Pro</p>
        <h1 className="dell-display" style={{ fontSize: '20px', color: 'var(--ash)', marginBottom: '6px' }}>Personal Bet Tracker</h1>
        <p className="times" style={{ fontSize: '13px', color: 'var(--fog)' }}>Log your own bets, track ROI, and compare your performance against the Beezy model. Pro tier only.</p>
      </div>
      <div style={{ border: B, padding: '40px', textAlign: 'center' }}>
        <p className="mono" style={{ fontSize: '12px', color: 'var(--fog)', marginBottom: '16px' }}>Available to Pro subscribers after launch.</p>
        <a href="https://discord.gg/beezy" className="mono" style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '8px 20px', background: 'var(--signal)', color: 'var(--carbon)', textDecoration: 'none' }}>Join Waitlist</a>
      </div>
    </div>
  )
}
