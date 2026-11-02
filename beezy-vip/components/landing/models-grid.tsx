import { apiGetStats, apiGetSparklineBySystem } from '@/lib/betting-api'
import Link from 'next/link'
import { SYSTEM_COLOR } from '@/lib/tokens'
import { SPORT_LIST } from '@/lib/sports'
import { ModelsGridClient, type SystemCard } from './models-grid-client'
import { SectionHeader } from './section-header'

const META: Record<string, { name: string; desc: string; href: string }> = {
  NRFI:        { name: 'No Run First Inning',   desc: 'Scoreless first innings.', href: '/picks/mlb/nrfi' },
  HR:          { name: 'Home Run Props',         desc: 'Launch angle, exit velocity and barrel rate vs. pitcher HR/9.', href: '/picks/mlb/hr' },
  F5:          { name: 'First 5 Innings',        desc: 'F5 moneylines.', href: '/picks/mlb/f5' },
  K:           { name: 'Strikeout Props',         desc: 'Starter strikeout over/unders.', href: '/picks/mlb/k' },
  OUTS:        { name: 'Pitcher Outs Props',     desc: 'Negative-binomial outs projection.', href: '/picks/mlb/outs' },
  GAME:        { name: 'Full Game',              desc: 'Full-game moneyline. Log-only until 200 bets.', href: '/picks/mlb/game' },
  F3:          { name: 'First 3 Innings',         desc: 'Early-game starter window.', href: '/picks/mlb/f3' },
  F1H:         { name: 'First Half',              desc: 'Innings 1-4 moneyline. Log-only.', href: '/picks/mlb/f1h' },
  F7:          { name: 'First 7 Innings',         desc: 'Late starter and bridge-relief pricing.', href: '/picks/mlb/f7' },
  BATTER_K:    { name: 'Batter Strikeouts',       desc: 'Batter K props.', href: '/picks/mlb/batter-k' },
  BATTER_TB:   { name: 'Total Bases',            desc: 'Total-base props.', href: '/picks/mlb/batter-tb' },
  BATTER_HITS: { name: 'Hits Props',             desc: 'Hits props.', href: '/picks/mlb/batter-hits' },
  SB:          { name: 'Stolen Base Props',      desc: 'Stolen-base props.', href: '/picks/mlb/sb' },
  PITCHER_ER:  { name: 'Pitcher ER Props',       desc: 'Earned-runs props.', href: '/picks/mlb/pitcher-er' },
  '1I':        { name: 'First Inning Moneyline', desc: 'Three-way first-inning pricing.', href: '/picks/mlb/1i' },
}

export async function ModelsGrid() {
  let systems: SystemCard[] = []
  try {
    const stats = (await apiGetStats()).bySystem.map(s => ({
      system: s.system, win_rate: parseFloat(String(s.win_rate)),
      roi: s.roi == null ? null : parseFloat(String(s.roi)), total_bets: parseInt(String(s.total_bets)),
    }))
    const sl = await Promise.allSettled(stats.map(s => apiGetSparklineBySystem(s.system, 30)))
    systems = stats.filter(s => META[s.system])
      .sort((a, b) => {
        const ag = a.total_bets >= 200 ? 1 : 0, bg = b.total_bets >= 200 ? 1 : 0
        return ag !== bg ? bg - ag : (b.roi ?? -Infinity) - (a.roi ?? -Infinity)
      })
      .map(s => {
        const i = stats.indexOf(s), r = sl[i]
        return { system: s.system, ...META[s.system], tint: SYSTEM_COLOR[s.system] ?? 'var(--color-neutral-300)', win_rate: s.win_rate, roi: s.roi, total_bets: s.total_bets, sparkline: r.status === 'fulfilled' ? r.value : null }
      })
  } catch { /* API unavailable: league cards still render */ }

  return (
    <section className="pt-8">
      <SectionHeader title="Coverage by league & system" href="/picks" link="Explore all models &rarr;" />
      <div className="models-grid grid-cols-2 gap-2.5">
        {SPORT_LIST.map(p => (
          <Link key={p.slug} href={`/picks/${p.slug}`} className="block rounded-xl border border-separator-border bg-background-primary-default p-3.5 no-underline shadow-card" style={{ borderLeft: `3px solid ${p.accent}` }}>
            <div className="mb-1.5 flex justify-between gap-2">
              <span className="text-body-2-bold" style={{ color: p.accent }}>{p.shortName}</span>
              <span className="mono text-micro text-text-secondary">{p.seasonBadge}</span>
            </div>
            <div className="text-caption-1-regular text-text-secondary">
              {p.markets.filter(m => m.kind === 'prop' || m.kind === 'game').slice(0, 6).map(m => m.label.replace(/ O\/U| \(Yes\/No\)/, '')).join(' \u00b7 ')}
            </div>
          </Link>
        ))}
      </div>
      {systems.length > 0 && (
        <details className="mt-2.5 rounded-xl border border-separator-border bg-background-primary-default px-3.5 py-2.5 shadow-card">
          <summary className="cursor-pointer text-caption-1-semibold text-text-secondary">MLB system records ({systems.length})</summary>
          <div className="mt-3"><ModelsGridClient systems={systems} /></div>
        </details>
      )}
    </section>
  )
}
