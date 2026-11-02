import Link from 'next/link'
import { PLANS } from '@/lib/plans'
import { PRE_LAUNCH } from '@/lib/launch'
import { cx } from '@/utils/cx'
import { SectionHeader } from './section-header'

const TIERS = [
  { name: 'Explorer', price: '$0', period: 'free', body: 'Daily Top 10 cross-sport picks, the public results ledger and betting calculators.' },
  { name: 'Single-Sport Pass', price: `$${PLANS.single.price}`, period: PLANS.single.period, body: 'Full projections, picks and the prop research cockpit for one league: NFL, NBA, NHL or MLB.' },
  { name: 'All-Access Pro', price: `$${PLANS.monthly.price}`, period: `${PLANS.monthly.period} or $${PLANS.annual.price}${PLANS.annual.period}`, body: 'Live Odds Terminal with +EV, arbitrage and alt longshots across all four sports.', featured: true },
]

export function PricingSummary() {
  return (
    <section className="pt-8">
      <SectionHeader title="Pricing" href="/pricing" link={PRE_LAUNCH ? 'Join the waitlist \u2192' : 'Compare plans \u2192'} />
      <div className="pricing-grid gap-[10px]">
        {TIERS.map(t => (
          <Link key={t.name} href="/pricing" className={cx('block rounded-xl border bg-background-primary-default p-3.5 no-underline shadow-card', t.featured ? 'border-[rgba(87,242,135,0.55)]' : 'border-separator-border')}>
            <div className={cx('mono mb-1.5 text-[10px] tracking-[0.06em]', t.featured ? 'text-[var(--signal)]' : 'text-text-secondary')}>{t.name.toUpperCase()}</div>
            <div className="mb-1.5 flex items-baseline gap-1.5">
              <span className="mono text-[24px] font-bold text-text-primary">{t.price}</span>
              <span className="text-caption-1-regular text-text-secondary">{t.period}</span>
            </div>
            <div className="text-caption-1-regular text-text-secondary">{t.body}</div>
          </Link>
        ))}
      </div>
      {PRE_LAUNCH && <p className="mt-2 text-caption-2-regular text-text-secondary">Pre-launch: paid plans open after the 200-bet gate. Prices lock at launch.</p>}
    </section>
  )
}
