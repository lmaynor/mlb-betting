export const dynamic = 'force-dynamic'
import type { Metadata } from 'next'
export const metadata: Metadata = { title: 'Pitcher Matchup Dashboard', robots: { index: false, follow: false } }
const B = '0.5px solid #1f1f24'
export default function PitcherMatchupsPage() {
  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '24px' }}>
        <p className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#10b981', marginBottom: '6px' }}>Tools · Partial Access</p>
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '6px' }}>Pitcher Matchup Dashboard</h1>
        <p style={{ fontSize: '13px', color: '#71717a' }}>Today&apos;s starters with SwStr%, zone rate, and opponent K%. Beezy strikeout projection for Pro members.</p>
      </div>
      <div style={{ border: B, padding: '40px', textAlign: 'center' }}>
        <p className="mono" style={{ fontSize: '12px', color: '#71717a', marginBottom: '16px' }}>Full dashboard available to Pro members.</p>
        <a href="https://discord.gg/beezy" className="mono" style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '8px 20px', background: '#10b981', color: '#0a0a0c', textDecoration: 'none' }}>Join Discord</a>
      </div>
    </div>
  )
}
