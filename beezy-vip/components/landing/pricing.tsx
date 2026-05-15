'use client'

// PRE_LAUNCH controls whether CTAs go to Discord or Stripe checkout
const PRE_LAUNCH = true

const TIERS = [
  {
    name:     'Starter',
    price:    '$29',
    period:   '/mo',
    tier:     'starter' as const,
    featured: false,
    features: [
      '1 system picks daily',
      'Full results history',
      'All free tools',
      'Discord access',
    ],
  },
  {
    name:     'Pro',
    price:    '$79',
    period:   '/mo',
    tier:     'pro' as const,
    featured: true,
    features: [
      'All 5 system picks',
      'Kelly stake sizing',
      'Model probabilities',
      'Dashboard access',
      'CSV export',
      'Edge finder (full)',
    ],
  },
  {
    name:     'Season',
    price:    '$499',
    period:   '/season',
    tier:     'season' as const,
    featured: false,
    features: [
      'Everything in Pro',
      'Full 2026 MLB season',
      'Best per-month value',
      'Priority Discord role',
    ],
  },
]

// Lazy import so CheckoutButton JS only loads post-launch
import dynamic from 'next/dynamic'
const CheckoutButton = dynamic(
  () => import('@/components/ui/checkout-button').then(m => m.CheckoutButton),
  { ssr: false }
)

export function PricingSection() {
  return (
    <section className="max-w-7xl mx-auto px-4 py-20 border-b border-[var(--border)]">
      <div className="text-center mb-12">
        <h2 className="text-2xl font-extrabold tracking-tight uppercase mb-3">Pricing</h2>
        <p className="text-muted text-sm mono">
          {PRE_LAUNCH
            ? 'Pre-launch · Join the waitlist · Prices lock at launch'
            : 'All plans include a 7-day money-back guarantee'}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-4xl mx-auto">
        {TIERS.map(t => (
          <div
            key={t.name}
            className={`
              relative p-6 border flex flex-col
              ${t.featured
                ? 'border-accent bg-accent/5'
                : 'border-[var(--border)] bg-[var(--surface)]'}
            `}
          >
            {t.featured && (
              <div className="absolute -top-px left-0 right-0 h-px bg-accent" />
            )}
            {t.featured && (
              <span className="mono text-xs tracking-widest uppercase text-accent mb-4">
                Most Popular
              </span>
            )}

            <p className="mono text-xs tracking-widest uppercase text-muted mb-2">{t.name}</p>
            <div className="flex items-end gap-1 mb-6">
              <span className="text-4xl font-extrabold text-text">{t.price}</span>
              <span className="mono text-sm text-muted mb-1">{t.period}</span>
            </div>

            <ul className="space-y-2 mb-8 flex-1">
              {t.features.map(f => (
                <li key={f} className="flex items-center gap-2 text-xs text-muted">
                  <span className="text-accent">+</span>
                  {f}
                </li>
              ))}
            </ul>

            {PRE_LAUNCH ? (
              <a
                href="https://discord.gg/beezy"
                className={`
                  block w-full text-center mono text-xs tracking-widest uppercase py-3
                  font-semibold transition-colors
                  ${t.featured
                    ? 'bg-accent text-bg hover:bg-accent/90'
                    : 'border border-[var(--border-bright)] text-text hover:border-accent hover:text-accent'}
                `}
              >
                Join Waitlist
              </a>
            ) : (
              <CheckoutButton
                tier={t.tier}
                label={`Get ${t.name}`}
                featured={t.featured}
              />
            )}
          </div>
        ))}
      </div>

      <p className="text-center mono text-xs text-muted mt-6">
        {PRE_LAUNCH
          ? 'Models enter paid mode after clearing 200-bet gate. Currently in paper mode.'
          : 'Cancel anytime. Bets are paper mode until each system clears 200 bets.'}
      </p>
    </section>
  )
}
