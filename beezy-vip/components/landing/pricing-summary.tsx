import Link from 'next/link'
import { PLANS } from '@/lib/plans'
import { PRE_LAUNCH } from '@/lib/launch'
import { SURFACE_CARD } from '@/lib/tokens'
import { SectionHeader } from './section-header'

const TIERS = [
  { name: 'Explorer', price: '$0', period: 'free', body: 'Daily Top 10 cross-sport picks, the public results ledger and betting calculators.' },
  { name: 'Single-Sport Pass', price: `$${PLANS.single.price}`, period: PLANS.single.period, body: 'Full projections, picks and the prop research cockpit for one league: NFL, NBA, NHL or MLB.' },
  { name: 'All-Access Pro', price: `$${PLANS.monthly.price}`, period: `${PLANS.monthly.period} or $${PLANS.annual.price}${PLANS.annual.period}`, body: 'Live Odds Terminal with +EV, arbitrage and alt longshots across all four sports.', featured: true },
]

export function PricingSummary() {
  return (
    <section style={{ paddingTop: '32px' }}>
      <SectionHeader title="Pricing" href="/pricing" link={PRE_LAUNCH ? 'Join the waitlist \u2192' : 'Compare plans \u2192'} />
      <div className="pricing-grid" style={{ gap: '10px' }}>
        {TIERS.map(t => (
          <Link key={t.name} href="/pricing" style={{ ...SURFACE_CARD, padding: '14px', textDecoration: 'none', borderColor: t.featured ? 'rgba(87,242,135,0.55)' : '#1e2230', display: 'block' }}>
            <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.06em', color: t.featured ? 'var(--signal)' : 'var(--silver)', marginBottom: '6px' }}>{t.name.toUpperCase()}</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: '6px', marginBottom: '6px' }}>
              <span className="mono" style={{ fontSize: '24px', fontWeight: 700, color: 'var(--chalk)' }}>{t.price}</span>
              <span style={{ fontSize: '12px', color: '#7d8596' }}>{t.period}</span>
            </div>
            <div style={{ fontSize: '12px', lineHeight: 1.45, color: '#9ca3af' }}>{t.body}</div>
          </Link>
        ))}
      </div>
      {PRE_LAUNCH && <p style={{ fontSize: '11px', color: '#7d8596', margin: '8px 0 0' }}>Pre-launch: paid plans open after the 200-bet gate. Prices lock at launch.</p>}
    </section>
  )
}
