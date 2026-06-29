export const dynamic = 'force-dynamic'
import type { Metadata } from 'next'
export const metadata: Metadata = { title: 'Personal Bet Tracker', robots: { index: false, follow: false } }
const B = '1px solid var(--basalt)'
export default function BetTrackerPage() {
  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', padding: '40px 24px' }}>
      <div style={{ marginBottom: '24px' }}>
        <p className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'var(--fog)', marginBottom: '8px' }}>Tools · Pro</p>
        <h1 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)', marginBottom: '8px' }}>Personal bet tracker</h1>
        <p className="times" style={{ fontSize: '15px', color: 'var(--fog)' }}>Log your own bets, track ROI, and compare your performance against the Beezy model. Pro tier only.</p>
      </div>
      <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', padding: '48px', textAlign: 'center' }}>
        <p className="mono" style={{ fontSize: '13px', color: 'var(--fog)', marginBottom: '20px' }}>Available to Pro subscribers after launch.</p>
        <a href="https://discord.gg/beezy" className="btn btn-primary">Join waitlist</a>
      </div>
    </div>
  )
}
