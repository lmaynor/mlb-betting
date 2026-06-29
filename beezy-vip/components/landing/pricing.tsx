'use client'

const PRE_LAUNCH = true
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
    features: ['1 system picks daily', 'Full results history', 'All free tools', 'Discord access'],
  },
  {
    name: 'Pro',
    price: '$79',
    period: '/mo',
    tier: 'pro' as const,
    featured: true,
    features: ['All 5 system picks', 'Kelly stake sizing', 'Model probabilities', 'Dashboard access', 'CSV export', 'Edge finder (full)'],
  },
  {
    name: 'Season',
    price: '$499',
    period: '/season',
    tier: 'season' as const,
    featured: false,
    features: ['Everything in Pro', 'Full 2026 MLB season', 'Best per-month value', 'Priority Discord role'],
  },
]

export function PricingSection() {
  return (
    <section style={{ maxWidth: '1000px', margin: '0 auto', padding: '56px 24px' }}>

      <div style={{ marginBottom: '32px', textAlign: 'center' }}>
        <h1 className="dell-display" style={{ fontSize: '32px', color: 'var(--chalk)', marginBottom: '10px' }}>Pricing</h1>
        <p className="times" style={{ fontSize: '15px', color: 'var(--fog)' }}>
          {PRE_LAUNCH ? 'Pre-launch — join the waitlist. Prices lock at launch.' : 'All plans include a 7-day money-back guarantee.'}
        </p>
      </div>

      <div className="pricing-grid">
        {TIERS.map((t) => (
          <div
            key={t.name}
            style={{
              display: 'flex', flexDirection: 'column',
              borderRadius: 'var(--radius-lg)',
              border: t.featured ? '1px solid var(--win-border)' : '1px solid var(--basalt)',
              background: t.featured ? 'linear-gradient(180deg, var(--win-wash), var(--graphite))' : 'var(--graphite)',
              boxShadow: t.featured ? 'var(--glow-signal), var(--shadow-md)' : 'var(--shadow-sm)',
              overflow: 'hidden',
            }}
          >
            <div style={{ padding: '20px 20px 0', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span className="dell-heading" style={{ fontSize: '12px', letterSpacing: '0.08em', color: t.featured ? 'var(--signal)' : 'var(--silver)' }}>{t.name.toUpperCase()}</span>
              {t.featured && (
                <span
                  className="dell-heading"
                  style={{ fontSize: '8.5px', letterSpacing: '0.08em', padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: 'color-mix(in oklab, var(--signal) 18%, var(--carbon))', color: 'var(--signal)', border: '1px solid var(--win-border)' }}
                >
                  MOST POPULAR
                </span>
              )}
            </div>

            <div style={{ padding: '16px 20px 24px', flex: 1, display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: '5px', marginBottom: '22px' }}>
                <span className="dell-display" style={{ fontSize: '40px', color: 'var(--chalk)', lineHeight: 1 }}>{t.price}</span>
                <span className="times" style={{ fontSize: '14px', color: 'var(--fog)', marginBottom: '6px' }}>{t.period}</span>
              </div>
              <ul style={{ listStyle: 'none', marginBottom: '24px', flex: 1 }}>
                {t.features.map(f => (
                  <li key={f} style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '11px' }}>
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" style={{ flexShrink: 0 }} aria-hidden>
                      <path d="M3.5 8.5 L6.5 11.5 L12.5 4.5" stroke="#71d083" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    <span className="times" style={{ fontSize: '14px', color: 'var(--silver)' }}>{f}</span>
                  </li>
                ))}
              </ul>
              {PRE_LAUNCH ? (
                <a
                  href={DISCORD_URL}
                  className={t.featured ? 'btn btn-primary' : 'btn btn-ghost'}
                  style={{ width: '100%' }}
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
      <p className="times" style={{ textAlign: 'center', fontSize: '13px', color: 'var(--fog)', marginTop: '20px' }}>
        {PRE_LAUNCH ? 'Models enter paid mode after clearing 200-bet gate. Currently in paper mode.' : 'Cancel anytime.'}
      </p>
    </section>
  )
}
