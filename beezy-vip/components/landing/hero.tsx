import Link from 'next/link'
import { apiGetAnalyticsDeepDive, apiGetEvFeed, apiGetStats } from '@/lib/betting-api'
import { HeroPerfCard } from '@/components/landing/hero-perf-card'

const B = '1px solid var(--basalt)'

export async function Hero() {
  const [deep, feed, stats] = await Promise.all([
    apiGetAnalyticsDeepDive('EV').catch(() => null),
    apiGetEvFeed({ sport: 'mlb', minEv: 0.03, limit: 12 }).catch(() => null),
    apiGetStats().then(s => s.overall).catch(() => null),
  ])
  const signals = (feed?.mlb.rows ?? []).filter(r => r.result == null).slice(0, 3)

  return (
    <section className="reveal" style={{ marginTop: '28px' }}>
      <div className="hero-grid" style={{ border: B, borderRadius: 'var(--radius-xl)', overflow: 'hidden', background: 'var(--graphite)', boxShadow: 'var(--shadow-card)' }}>

        <div
          className="hero-left"
          style={{ padding: '52px 44px', borderRight: B, display: 'flex', flexDirection: 'column', justifyContent: 'center', background: 'linear-gradient(180deg, color-mix(in oklab, var(--signal) 5%, var(--graphite)), var(--graphite))' }}
        >
          <span className="dell-sticker" style={{ alignSelf: 'flex-start', marginBottom: '20px' }}>
            Model picks + live +EV scanner
          </span>

          <h1 className="dell-display hero-h1" style={{ fontSize: '46px', lineHeight: 1.04, color: 'var(--chalk)', marginBottom: '18px', letterSpacing: '-0.025em' }}>
            Stop betting into vig. <span style={{ color: 'var(--signal)' }}>Strike market dislocations.</span>
          </h1>

          <p className="times" style={{ fontSize: '16.5px', color: 'var(--silver)', marginBottom: '28px', maxWidth: '52ch' }}>
            Two engines, one public ledger. A 15-minute scanner flags sportsbook prices that lag the de-vigged consensus, with an
            NFL prop sweep against Kalshi and Novig running in shadow. Alongside it, calibrated MLB models grade every game and
            prop into a single 0&ndash;100 Beezy Score.
          </p>

          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '18px' }}>
            <Link href="/ev" className="btn btn-primary">Launch +EV Terminal &rarr;</Link>
            <Link href="/sports/mlb" className="btn btn-ghost">Explore Sport Hubs</Link>
          </div>

          <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>
            {stats ? `${Number(stats.total_bets).toLocaleString()} SETTLED BETS` : 'PUBLIC LEDGER'} &middot; PAPER MODE &middot; EVERY RESULT PUBLIC
          </span>
        </div>

        <HeroPerfCard headline={deep?.ev_headline ?? null} curve={deep?.bankroll_curve ?? []} signals={signals} />
      </div>
    </section>
  )
}
