'use client'

import { useState } from 'react'
import Link from 'next/link'
import dynamic from 'next/dynamic'
import { PRE_LAUNCH } from '@/lib/launch'
import { PLANS, ANNUAL_PER_MONTH, ANNUAL_SAVINGS_PCT } from '@/lib/plans'
import { DISCORD_URL } from '@/lib/links'
import { SITE_CONFIG } from '@/lib/site-config'
import { SPORT_PILLARS } from '@/lib/sports'
import { cx } from '@/utils/cx'
import { LEAGUES, type League } from '@/lib/tiers'

const CheckoutButton = dynamic(
  () => import('@/components/ui/checkout-button').then(m => m.CheckoutButton),
  { ssr: false }
)

const CARD = 'rounded-xl border border-separator-border bg-background-primary-default shadow-card'

export { PLANS, ANNUAL_PER_MONTH, ANNUAL_SAVINGS_PCT }

const LEAGUE_LABEL = Object.fromEntries(LEAGUES.map(l => [l, SPORT_PILLARS[l].shortName])) as Record<League, string>
// Existing display order first; a league added to lib/sports.ts is appended, so it appears in the picker without an edit here.
const PICKER_ORDER: League[] = ['nfl', 'nba', 'nhl', 'mlb']
const PICKER: League[] = [...PICKER_ORDER.filter(l => LEAGUES.includes(l)), ...LEAGUES.filter(l => !PICKER_ORDER.includes(l))]

const ENGINES = [
  { n: '1', title: 'Market Consensus Engine', body: 'Scans 14+ books, Kalshi and Novig every 15 minutes for off-market prices.' },
  { n: '2', title: 'ML Projection Engine', body: 'Projects player minutes, snaps and TOI, teammate-injury redistribution, and full probability distributions.' },
  { n: '3', title: 'Confluence Scoring', body: 'Plays where a soft book is off-market and the projection agrees earn the top STRONG (70+) Beezy Scores.' },
]

function Check() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" style={{ flexShrink: 0, marginTop: '3px' }} aria-hidden>
      <path d="M3.5 8.5 L6.5 11.5 L12.5 4.5" stroke="#57f287" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function Features({ items }: { items: string[] }) {
  return (
    <ul className="m-0 mb-5 flex-1 list-none p-0">
      {items.map(f => (
        <li key={f} className="mb-2.5 flex gap-[9px] text-body-regular text-text-secondary">
          <Check /><span>{f}</span>
        </li>
      ))}
    </ul>
  )
}

function Plan({ name, price, period, note, featured, children }: {
  name: string; price: string; period: string; note?: string; featured?: boolean; children: React.ReactNode
}) {
  return (
    <div className={cx(CARD, 'flex flex-col px-[18px] pb-5 pt-[18px]', featured && 'border-[rgba(87,242,135,0.55)]')}>
      <div className={cx('dell-heading mb-2.5 text-[11px] tracking-[0.06em]', featured ? 'text-[var(--signal)]' : 'text-text-secondary')}>{name.toUpperCase()}</div>
      <div className="mb-1 flex items-baseline gap-[5px]">
        <span className="mono text-[34px] font-bold leading-none text-text-primary">{price}</span>
        <span className="text-body-2-regular text-text-secondary">{period}</span>
      </div>
      <div className="mb-3.5 min-h-[18px] text-caption-1-regular text-text-secondary">{note ?? ''}</div>
      {children}
    </div>
  )
}

function Cta({ tier, sport, label, featured }: { tier: 'single_sport' | 'pro_monthly' | 'pro_annual'; sport?: League; label: string; featured?: boolean }) {
  if (PRE_LAUNCH) {
    return <a href={DISCORD_URL} className={cx('w-full', featured ? 'btn btn-primary' : 'btn btn-ghost')}>Join waitlist</a>
  }
  return <CheckoutButton tier={tier} sport={sport} label={label} featured={featured} />
}

export function PricingSection() {
  const [league, setLeague] = useState<League>('nfl')
  const lc = league
  return (
    <section className="mx-auto max-w-[1080px] px-6 pb-14 pt-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="text-title-2-bold text-text-primary">Pricing</h1>
        <span className="text-body-2-regular text-text-secondary">
          {PRE_LAUNCH ? 'Pre-launch: join the waitlist. Prices lock at launch.' : 'Cancel anytime in 1 click.'}
        </span>
      </div>

      <div className={cx(CARD, 'mb-5 flex flex-wrap items-center justify-between gap-4 border-l-[3px] border-l-[var(--signal)] px-[18px] py-3.5')}>
        <div>
          <div className="flex items-center gap-2">
            <span className="dell-heading text-[11px] tracking-[0.08em] text-[var(--signal)]">EXPLORER TIER</span>
            <span className="mono text-body-2-bold text-text-primary">$0 / Free Forever</span>
          </div>
          <div className="mt-[3px] text-body-2-regular text-text-secondary">
            Daily Top 10 cross-sport picks, full public results ledger, odds & vig calculator, and Kelly sizing. No account or credit card required.
          </div>
        </div>
        <Link href="/picks" className="button-press-motion whitespace-nowrap rounded-2lg border border-border-button-default bg-background-secondary-default px-3.5 py-[7px] text-caption-1-semibold text-text-primary no-underline hover:bg-background-primary-hover">
          Browse free picks &rarr;
        </Link>
      </div>

      <div className="pricing-grid">
        <Plan name="Single-Sport Pass" price={`$${PLANS.single.price}`} period={PLANS.single.period} note="One league of your choice">
          <div role="radiogroup" aria-label="League" className="mb-4 flex flex-wrap gap-1.5">
            {PICKER.map(l => (
              <button key={l} role="radio" aria-checked={league === l} onClick={() => setLeague(l)}
                className={cx('button-press-motion cursor-pointer rounded-lg border px-3 py-[5px] text-caption-1-semibold',
                  league === l
                    ? 'border-[var(--signal)] bg-[var(--signal)] text-[#090a0f]'
                    : 'border-separator-border bg-background-secondary-default text-text-secondary hover:bg-background-primary-hover')}>
                {LEAGUE_LABEL[l]}
              </button>
            ))}
          </div>
          <Features items={[
            `Choose 1 league (${LEAGUES.map(l => LEAGUE_LABEL[l]).join(', ')})`,
            `Full daily model projections and Beezy Scores (0-100) for ${LEAGUE_LABEL[league]}`,
            `L5 / L10 game-log and opportunity research cockpit (/edge) for ${LEAGUE_LABEL[league]}`,
            `${LEAGUE_LABEL[league]} Discord role and channel access (#${lc}-card)`,
          ]} />
          <Cta tier="single_sport" sport={league} label={`Get ${LEAGUE_LABEL[league]} Pass`} />
        </Plan>

        <Plan name="All-Access Pro Monthly" price={`$${PLANS.monthly.price}`} period={PLANS.monthly.period} note="Most complete plan, billed monthly" featured>
          <Features items={[
            'All 4 leagues (NFL, NBA, NHL, MLB): model projections and daily cards',
            '15-minute +EV Terminal (/ev) across 14+ sportsbooks, Kalshi and Novig',
            'Multi-book line-shopping drawer and dynamic 1/4-Kelly bankroll sizing',
            'Full VIP Discord access (#ev-sportsbooks, #ev-exchanges, all 4 sport cards)',
          ]} />
          <Cta tier="pro_monthly" label="Get All-Access Pro" featured />
        </Plan>

        <Plan name="All-Access Pro Annual" price={`$${PLANS.annual.price}`} period={PLANS.annual.period} note={`$${ANNUAL_PER_MONTH.toFixed(2)}/mo, save ${ANNUAL_SAVINGS_PCT}%`}>
          <Features items={[
            'Everything in All-Access Pro across the entire 12-month sports calendar',
            'Full CSV and closing-line value (CLV) data exports',
            'Priority access to exchange execution tools when they are released',
          ]} />
          <Cta tier="pro_annual" label="Get Annual Pro" />
        </Plan>
      </div>

      <div className={cx(CARD, 'mt-5')}>
        <div className="dell-heading border-b border-separator-border px-4 py-3 text-[11px] text-text-secondary">HOW THE DUAL-ENGINE TERMINAL WORKS</div>
        <div className="pricing-grid" style={{ gap: 0 }}>
          {ENGINES.map((e, i) => (
            <div key={e.n} className={cx('px-4 py-3.5', i > 0 && 'border-l border-separator-border')}>
              <div className="mb-[5px] text-body-2-semibold text-text-primary">{e.n}. {e.title}</div>
              <div className="text-body-2-regular text-text-secondary">{e.body}</div>
            </div>
          ))}
        </div>
      </div>

      {PRE_LAUNCH && (
        <p className="mt-3.5 text-caption-1-regular text-text-secondary">
          Models enter paid mode after clearing the 200-bet gate. Projections and signals are in paper mode and not yet part of the public results.
        </p>
      )}
      <p className="mt-2 text-caption-1-regular text-text-secondary">
        Cancel anytime in 1 click from your account settings. All tools and projections are for informational purposes only; beezy.fyi is not a sportsbook.
        Questions: <a href={`mailto:${SITE_CONFIG.supportEmail}`} className="text-[var(--link)]">{SITE_CONFIG.supportEmail}</a> or the <a href={DISCORD_URL} className="text-[var(--link)]">Discord</a>.
      </p>
    </section>
  )
}
