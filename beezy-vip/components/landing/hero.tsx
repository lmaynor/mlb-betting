import Link from 'next/link'
import { apiGetAnalyticsDeepDive } from '@/lib/betting-api'
import { HeroPerfCard } from '@/components/landing/hero-perf-card'
import { PLANS } from '@/lib/plans'

export async function Hero() {
  const deep = await apiGetAnalyticsDeepDive('EV').catch(() => null)
  const h = deep?.ev_headline ?? null

  return (
    <section className="reveal mt-7">
      <div className="hero-grid overflow-hidden rounded-xl border border-separator-border bg-background-primary-default shadow-card">

        <div className="hero-left flex flex-col justify-center border-r border-separator-border p-9">
          <span className="mono mb-4 self-start rounded-full border border-[rgba(87,242,135,0.35)] px-2.5 py-1 text-micro font-semibold tracking-head text-signal">
            NFL &middot; NBA &middot; NHL &middot; MLB
          </span>

          <h1 className="ui-display hero-h1 mb-3.5 text-[40px] leading-[1.06] tracking-[-0.025em]">
            Stop betting into vig. <span className="text-signal">Strike market dislocations.</span>
          </h1>

          <p className="mb-5 max-w-[56ch] text-body-regular text-text-secondary">
            A 15-minute +EV scanner compares sportsbooks, Kalshi and Novig against de-vigged consensus, alongside
            walk-forward prop models for NFL, NBA, NHL and MLB. Every signal is graded overnight on a public ledger.
          </p>

          <div className="mb-3.5 flex flex-wrap gap-2.5">
            <Link href="/ev" className="btn btn-primary">Open Odds Terminal &rarr;</Link>
            <Link href="/picks" className="btn btn-ghost">Today&apos;s picks</Link>
          </div>

          <span className="mono text-mini text-text-secondary">
            VERIFIED PUBLIC LEDGER &middot; 1/4 KELLY SIZING
            {h ? ` \u00b7 ${h.graded.toLocaleString()} graded` : ''} &middot; From ${PLANS.single.price}/mo
          </span>
        </div>

        <HeroPerfCard headline={h} curve={deep?.bankroll_curve ?? []} />
      </div>
    </section>
  )
}
