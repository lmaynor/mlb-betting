'use client'

const PRE_LAUNCH = true
const B = '1px solid #000'
const DISCORD_URL = 'https://discord.gg/HfMYCmbmE'

import dynamic from 'next/dynamic'
const CheckoutButton = dynamic(
  () => import('@/components/ui/checkout-button').then(m => m.CheckoutButton),
  { ssr: false }
)

const TIERS = [
  {
    name: 'Starter',
    price: '$29',
    period: '/mo',
    tier: 'starter' as const,
    featured: false,
    tintBg: '#131e24',
    tint: '#9ab6c8',
    features: ['1 system picks daily', 'Full results history', 'All free tools', 'Discord access'],
  },
  {
    name: 'Pro',
    price: '$79',
    period: '/mo',
    tier: 'pro' as const,
    featured: true,
    tintBg: '#1a2218',
    tint: '#b3bd95',
    features: ['All 5 system picks', 'Kelly stake sizing', 'Model probabilities', 'Dashboard access', 'CSV export', 'Edge finder (full)'],
  },
  {
    name: 'Season',
    price: '$499',
    period: '/season',
    tier: 'season' as const,
    featured: false,
    tintBg: '#2a1a0f',
    tint: '#e6915d',
    features: ['Everything in Pro', 'Full 2026 MLB season', 'Best per-month value', 'Priority Discord role'],
  },
]

export function PricingSection() {
  return (
    <section style={{ maxWidth: '900px', margin: '0 auto', padding: '48px 20px', borderBottom: '1px solid #1f1f24' }}>

      {/* Section eyebrow */}
      <div style={{ marginBottom: '24px', textAlign: 'center' }}>
        <h1 className="dell-display" style={{ fontSize: '22px', color: '#f5f5f7', marginBottom: '8px' }}>Pricing</h1>
        <p className="times" style={{ fontSize: '13px', color: '#888890' }}>
          {PRE_LAUNCH ? 'Pre-launch — Join the waitlist. Prices lock at launch.' : 'All plans include a 7-day money-back guarantee'}
        </p>
      </div>

      <div className="pricing-grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', gap: '0', border: B, overflow: 'hidden' }}>
        {TIERS.map((t, i) => (
          <div
            key={t.name}
            style={{ display: 'flex', flexDirection: 'column', borderRight: i < 2 ? B : undefined }}
          >
            {/* Ribbon title bar */}
            <div style={{ background: '#0a0a0c', borderBottom: B, padding: '7px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span className="dell-heading" style={{ fontSize: '11px', letterSpacing: '0.06em', color: t.tint }}>{t.name.toUpperCase()}</span>
              {t.featured && (
                <span
                  className="dell-heading"
                  style={{ fontSize: '8px', letterSpacing: '0.08em', padding: '2px 6px', background: '#fcc20f', color: '#000', border: '1px solid #000' }}
                >
                  MOST POPULAR
                </span>
              )}
            </div>

            {/* Ribbon body */}
            <div style={{ padding: '20px 16px', background: t.tintBg, flex: 1, display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: '4px', marginBottom: '20px' }}>
                <span className="dell-display" style={{ fontSize: '32px', color: t.tint, lineHeight: 1 }}>{t.price}</span>
                <span className="times" style={{ fontSize: '13px', color: '#888890', marginBottom: '4px' }}>{t.period}</span>
              </div>
              <ul style={{ listStyle: 'none', marginBottom: '24px', flex: 1 }}>
                {t.features.map(f => (
                  <li key={f} style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                    <span className="dell-heading" style={{ fontSize: '10px', color: t.tint, flexShrink: 0 }}>+</span>
                    <span className="times" style={{ fontSize: '13px', color: '#a1a1aa' }}>{f}</span>
                  </li>
                ))}
              </ul>
              {PRE_LAUNCH ? (
                <a
                  href={DISCORD_URL}
                  style={{
                    display: 'block',
                    textAlign: 'center',
                    fontFamily: 'Arial, Helvetica, sans-serif',
                    fontSize: '11px',
                    letterSpacing: '0.06em',
                    textTransform: 'uppercase',
                    fontWeight: 700,
                    padding: '10px',
                    textDecoration: 'none',
                    background: t.featured ? '#fcc20f' : 'transparent',
                    color: t.featured ? '#000' : '#f5f5f7',
                    border: t.featured ? '1px solid #000' : '1px solid #333',
                  }}
                >
                  Join waitlist
                </a>
              ) : (
                <CheckoutButton tier={t.tier} label={`Get ${t.name}`} featured={t.featured} />
              )}
            </div>
          </div>
        ))}
      </div>
      <p className="times" style={{ textAlign: 'center', fontSize: '12px', color: '#888890', marginTop: '16px' }}>
        {PRE_LAUNCH ? 'Models enter paid mode after clearing 200-bet gate. Currently in paper mode.' : 'Cancel anytime.'}
      </p>
    </section>
  )
}
