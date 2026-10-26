import Link from 'next/link'
import { apiGetAnalyticsDeepDive } from '@/lib/betting-api'
import { HeroPerfCard } from '@/components/landing/hero-perf-card'
import { PLANS } from '@/lib/plans'
import { SURFACE_CARD } from '@/lib/tokens'

const B = '1px solid #1e2230'

export async function Hero() {
  const deep = await apiGetAnalyticsDeepDive('EV').catch(() => null)
  const h = deep?.ev_headline ?? null

  return (
    <section className="reveal" style={{ marginTop: '28px' }}>
      <div className="hero-grid" style={{ ...SURFACE_CARD, overflow: 'hidden' }}>

        <div
          className="hero-left"
          style={{ padding: '36px 36px', borderRight: B, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}
        >
          <span className="mono" style={{ alignSelf: 'flex-start', marginBottom: '16px', fontSize: '10px', fontWeight: 600, letterSpacing: '0.06em', color: '#57F287', border: '1px solid rgba(87,242,135,0.35)', borderRadius: '999px', padding: '4px 10px' }}>
            NFL &middot; NBA &middot; NHL &middot; MLB
          </span>

          <h1 className="dell-display hero-h1" style={{ fontSize: '40px', lineHeight: 1.06, color: 'var(--chalk)', marginBottom: '14px', letterSpacing: '-0.025em' }}>
            Stop betting into vig. <span style={{ color: 'var(--signal)' }}>Strike market dislocations.</span>
          </h1>

          <p style={{ fontSize: '14px', lineHeight: 1.55, color: '#9ca3af', marginBottom: '20px', maxWidth: '56ch' }}>
            A 15-minute +EV scanner compares sportsbooks, Kalshi and Novig against de-vigged consensus, alongside
            walk-forward prop models for NFL, NBA, NHL and MLB. Every signal is graded overnight on a public ledger.
          </p>

          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '14px' }}>
            <Link href="/ev" className="btn btn-primary">Open Odds Terminal &rarr;</Link>
            <Link href="/picks" className="btn btn-ghost">Today&apos;s picks</Link>
          </div>

          <span className="mono" style={{ fontSize: '11px', color: '#7d8596' }}>
            VERIFIED PUBLIC LEDGER &middot; 1/4 KELLY SIZING
            {h ? ` \u00b7 ${h.graded.toLocaleString()} graded` : ''} &middot; From ${PLANS.single.price}/mo
          </span>
        </div>

        <HeroPerfCard headline={h} curve={deep?.bankroll_curve ?? []} />
      </div>
    </section>
  )
}
