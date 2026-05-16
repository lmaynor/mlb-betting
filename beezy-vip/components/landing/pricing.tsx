'use client'

const PRE_LAUNCH = true
const B = '0.5px solid #1f1f24'

import dynamic from 'next/dynamic'
const CheckoutButton = dynamic(
  () => import('@/components/ui/checkout-button').then(m => m.CheckoutButton),
  { ssr: false }
)

const TIERS = [
  { name: 'Starter', price: '$29', period: '/mo',     tier: 'starter' as const, featured: false, features: ['1 system picks daily', 'Full results history', 'All free tools', 'Discord access'] },
  { name: 'Pro',     price: '$79', period: '/mo',     tier: 'pro'     as const, featured: true,  features: ['All 5 system picks', 'Kelly stake sizing', 'Model probabilities', 'Dashboard access', 'CSV export', 'Edge finder (full)'] },
  { name: 'Season',  price: '$499',period: '/season', tier: 'season'  as const, featured: false, features: ['Everything in Pro', 'Full 2026 MLB season', 'Best per-month value', 'Priority Discord role'] },
]

export function PricingSection() {
  return (
    <section style={{ maxWidth: '900px', margin: '0 auto', padding: '48px 20px', borderBottom: B }}>
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <h2 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '6px' }}>Pricing</h2>
        <p className="mono" style={{ fontSize: '13px', color: '#71717a' }}>
          {PRE_LAUNCH ? 'Pre-launch · Join the waitlist · Prices lock at launch' : 'All plans include a 7-day money-back guarantee'}
        </p>
      </div>

      <div className="pricing-grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', gap: '0', border: B }}>
        {TIERS.map((t, i) => (
          <div key={t.name} style={{ padding: '24px', display: 'flex', flexDirection: 'column', borderRight: i < 2 ? B : undefined, background: t.featured ? 'rgba(16,185,129,0.04)' : '#0a0a0c', borderTop: t.featured ? '2px solid #10b981' : undefined }}>
            {t.featured && (
              <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#10b981', marginBottom: '10px' }}>Most Popular</div>
            )}
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '8px' }}>{t.name}</div>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: '4px', marginBottom: '20px' }}>
              <span style={{ fontSize: '36px', fontWeight: 700, color: '#f5f5f7', lineHeight: 1 }}>{t.price}</span>
              <span className="mono" style={{ fontSize: '13px', color: '#71717a', marginBottom: '4px' }}>{t.period}</span>
            </div>
            <ul style={{ listStyle: 'none', marginBottom: '24px', flex: 1 }}>
              {t.features.map(f => (
                <li key={f} style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: '#a1a1aa', marginBottom: '8px' }}>
                  <span style={{ color: '#10b981' }}>+</span>{f}
                </li>
              ))}
            </ul>
            {PRE_LAUNCH ? (
              <a href="https://discord.gg/beezy" className="mono" style={{ display: 'block', textAlign: 'center', fontSize: '11px', letterSpacing: '0.06em', textTransform: 'uppercase', padding: '10px', fontWeight: 600, textDecoration: 'none', background: t.featured ? '#10b981' : 'transparent', color: t.featured ? '#0a0a0c' : '#f5f5f7', border: t.featured ? 'none' : B }}>
                Join Waitlist
              </a>
            ) : (
              <CheckoutButton tier={t.tier} label={`Get ${t.name}`} featured={t.featured} />
            )}
          </div>
        ))}
      </div>
      <p className="mono" style={{ textAlign: 'center', fontSize: '11px', color: '#71717a', marginTop: '16px' }}>
        {PRE_LAUNCH ? 'Models enter paid mode after clearing 200-bet gate. Currently in paper mode.' : 'Cancel anytime.'}
      </p>
    </section>
  )
}
