import Link from 'next/link'
import { apiGetAnalyticsDeepDive, apiGetEvFeed } from '@/lib/betting-api'
import { HeroPerfCard } from '@/components/landing/hero-perf-card'
import { propKey } from '@/lib/dedupe'
import { siteDateKey } from '@/lib/dates'
import type { Bet } from '@/lib/types'

const B = '1px solid var(--basalt)'

export async function Hero() {
  const [deep, feed] = await Promise.all([
    apiGetAnalyticsDeepDive('EV').catch(() => null),
    apiGetEvFeed({ sport: 'mlb', minEv: 0.03, limit: 12 }).catch(() => null),
  ])
  // One signal per prop, not one per book that flagged it.
  const seen = new Set<string>()
  const signals = (feed?.mlb.rows ?? []).filter(r => r.result == null && r.game_date >= siteDateKey()).filter(r => {
    const k = propKey({ ...r, system: 'EV', game_pk: r.game_pk ?? 0 } as unknown as Bet)
    if (seen.has(k)) return false
    seen.add(k)
    return true
  }).slice(0, 3)

  return (
    <section className="reveal" style={{ marginTop: '28px' }}>
      <div className="hero-grid" style={{ border: B, borderRadius: 'var(--radius-xl)', overflow: 'hidden', background: 'var(--graphite)', boxShadow: 'var(--shadow-card)' }}>

        <div
          className="hero-left"
          style={{ padding: '52px 44px', borderRight: B, display: 'flex', flexDirection: 'column', justifyContent: 'center', background: 'linear-gradient(180deg, color-mix(in oklab, var(--signal) 5%, var(--graphite)), var(--graphite))' }}
        >
          <span style={{ alignSelf: 'flex-start', marginBottom: '20px', fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#57F287', background: 'rgba(87, 242, 135, 0.10)', border: '1px solid rgba(87, 242, 135, 0.30)', borderRadius: '999px', padding: '5px 12px' }}>
            MULTI-SPORT +EV SCANNER &amp; QUANTITATIVE MODELS
          </span>

          <h1 className="dell-display hero-h1" style={{ fontSize: '46px', lineHeight: 1.04, color: 'var(--chalk)', marginBottom: '18px', letterSpacing: '-0.025em' }}>
            Stop betting into vig. <span style={{ color: 'var(--signal)' }}>Strike market dislocations.</span>
          </h1>

          <p className="times" style={{ fontSize: '16.5px', color: 'var(--silver)', marginBottom: '28px', maxWidth: '52ch' }}>
            Two engines, one public ledger. Our 15-minute market scanner flags player props and lines across sportsbooks,
            Kalshi, and Novig that lag the de-vigged consensus, while MLB models grade games and props into a single
            0&ndash;100 Beezy Score. Everything is paper-tracked.
          </p>

          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '18px' }}>
            <Link href="/ev" className="btn btn-primary">Launch +EV Terminal &rarr;</Link>
            <Link href="/sports/mlb" className="btn btn-ghost">Browse sports</Link>
          </div>

          <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>
            PAPER MODE &middot; NO REAL MONEY STAKED &middot; EVERY RESULT PUBLIC
          </span>
        </div>

        <HeroPerfCard headline={deep?.ev_headline ?? null} curve={deep?.bankroll_curve ?? []} signals={signals} />
      </div>
    </section>
  )
}
