'use client'

import { useState } from 'react'
import dynamic from 'next/dynamic'
import { PRE_LAUNCH } from '@/lib/launch'
import { DISCORD_URL } from '@/lib/links'
import { SITE_CONFIG } from '@/lib/site-config'
import { SPORT_PILLARS } from '@/lib/sports'
import { SURFACE_CARD } from '@/lib/tokens'
import { LEAGUES, type League } from '@/lib/tiers'

const CheckoutButton = dynamic(
  () => import('@/components/ui/checkout-button').then(m => m.CheckoutButton),
  { ssr: false }
)

const SURFACE = SURFACE_CARD

/** Plan copy lives in one object so the tests can pin prices and the annual-savings arithmetic. */
export const PLANS = {
  single: { price: 19, period: '/mo' },
  monthly: { price: 39, period: '/mo' },
  annual: { price: 299, period: '/yr' },
} as const
export const ANNUAL_PER_MONTH = PLANS.annual.price / 12                                   // 24.92
export const ANNUAL_SAVINGS_PCT = Math.round((1 - PLANS.annual.price / (PLANS.monthly.price * 12)) * 100)   // 36

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
    <ul style={{ listStyle: 'none', margin: '0 0 20px', padding: 0, flex: 1 }}>
      {items.map(f => (
        <li key={f} style={{ display: 'flex', gap: '9px', marginBottom: '10px', fontSize: '13.5px', color: 'var(--silver)', lineHeight: 1.45 }}>
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
    <div style={{ ...SURFACE, display: 'flex', flexDirection: 'column', padding: '18px 18px 20px', borderColor: featured ? 'rgba(87,242,135,0.55)' : '#1e2230' }}>
      <div className="dell-heading" style={{ fontSize: '11px', letterSpacing: '0.06em', color: featured ? 'var(--signal)' : 'var(--silver)', marginBottom: '10px' }}>{name.toUpperCase()}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '5px', marginBottom: '4px' }}>
        <span className="mono" style={{ fontSize: '34px', fontWeight: 700, color: 'var(--chalk)', lineHeight: 1 }}>{price}</span>
        <span style={{ fontSize: '13px', color: 'var(--fog)' }}>{period}</span>
      </div>
      <div style={{ minHeight: '18px', fontSize: '12px', color: 'var(--fog)', marginBottom: '14px' }}>{note ?? ''}</div>
      {children}
    </div>
  )
}

function Cta({ tier, sport, label, featured }: { tier: 'single_sport' | 'pro_monthly' | 'pro_annual'; sport?: League; label: string; featured?: boolean }) {
  if (PRE_LAUNCH) {
    return <a href={DISCORD_URL} className={featured ? 'btn btn-primary' : 'btn btn-ghost'} style={{ width: '100%' }}>Join waitlist</a>
  }
  return <CheckoutButton tier={tier} sport={sport} label={label} featured={featured} />
}

export function PricingSection() {
  const [league, setLeague] = useState<League>('nfl')
  const lc = league
  return (
    <section style={{ maxWidth: '1080px', margin: '0 auto', padding: '24px 24px 56px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '16px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0, letterSpacing: '-0.01em' }}>Pricing</h1>
        <span style={{ fontSize: '12.5px', color: 'var(--fog)' }}>
          {PRE_LAUNCH ? 'Pre-launch: join the waitlist. Prices lock at launch.' : 'Cancel anytime in 1 click.'}
        </span>
      </div>

      <div className="pricing-grid">
        <Plan name="Single-Sport Pass" price={`$${PLANS.single.price}`} period={PLANS.single.period} note="One league of your choice">
          <div role="radiogroup" aria-label="League" style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '16px' }}>
            {PICKER.map(l => (
              <button key={l} role="radio" aria-checked={league === l} onClick={() => setLeague(l)}
                style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '12px', fontWeight: 600, padding: '5px 12px', borderRadius: '6px', cursor: 'pointer',
                  color: league === l ? '#090a0f' : 'var(--silver)', background: league === l ? 'var(--signal)' : 'var(--surface-elevated)',
                  border: `1px solid ${league === l ? 'var(--signal)' : '#1e2230'}`, transition: 'background 120ms ease, border-color 120ms ease' }}>
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

      <div style={{ ...SURFACE, marginTop: '20px' }}>
        <div className="dell-heading" style={{ fontSize: '11px', color: 'var(--silver)', padding: '12px 16px', borderBottom: '1px solid #1e2230' }}>HOW THE DUAL-ENGINE TERMINAL WORKS</div>
        <div className="pricing-grid" style={{ gap: 0 }}>
          {ENGINES.map((e, i) => (
            <div key={e.n} style={{ padding: '14px 16px', borderLeft: i === 0 ? 'none' : '1px solid #1e2230' }}>
              <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--chalk)', marginBottom: '5px' }}>{e.n}. {e.title}</div>
              <div style={{ fontSize: '12.5px', color: 'var(--fog)', lineHeight: 1.5 }}>{e.body}</div>
            </div>
          ))}
        </div>
      </div>

      {PRE_LAUNCH && (
        <p style={{ fontSize: '12px', color: 'var(--fog)', margin: '14px 0 0' }}>
          Models enter paid mode after clearing the 200-bet gate. Projections and signals are in paper mode and not yet part of the public results.
        </p>
      )}
      <p style={{ fontSize: '12px', color: 'var(--fog)', margin: '8px 0 0', lineHeight: 1.55 }}>
        Cancel anytime in 1 click from your account settings. All tools and projections are for informational purposes only; beezy.fyi is not a sportsbook.
        Questions: <a href={`mailto:${SITE_CONFIG.supportEmail}`} style={{ color: 'var(--link)' }}>{SITE_CONFIG.supportEmail}</a> or the <a href={DISCORD_URL} style={{ color: 'var(--link)' }}>Discord</a>.
      </p>
    </section>
  )
}
