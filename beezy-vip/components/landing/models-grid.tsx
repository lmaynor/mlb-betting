import { apiGetStats, apiGetSparklineBySystem } from '@/lib/betting-api'
import Link from 'next/link'
import { SYSTEM_COLOR } from '@/lib/tokens'
import { ModelsGridClient, type SystemCard } from './models-grid-client'

// Per-system metadata. Identity color comes from the shared SYSTEM_COLOR
// taxonomy in tokens.ts; cards stay neutral (balanced color usage) with the
// system hue carried on the badge + sparkline only.
const META: Record<string, { name: string; desc: string; href: string }> = {
  NRFI:        { name: 'No Run First Inning',   desc: 'Starter ERA, ump K-rate, weather, and park factors combine to predict scoreless first innings.', href: '/picks/mlb/nrfi' },
  HR:          { name: 'Home Run Props',         desc: 'Launch angle, exit velocity, and barrel rate vs. pitcher HR/9.',                                href: '/picks/mlb/hr' },
  F5:          { name: 'First 5 Innings',        desc: 'Starting pitcher quality, bullpen rest, and lineup data for F5 moneylines.',                    href: '/picks/mlb/f5' },
  K:           { name: 'Strikeout Props',         desc: 'SwStr%, zone rate, and opponent K% to project starter strikeout over/unders.',                 href: '/picks/mlb/k' },
  OUTS:        { name: 'Pitcher Outs Props',     desc: 'IP projection via Normal model for DraftKings pitcher outs markets.',                           href: '/picks/mlb/outs' },
  GAME:        { name: 'Full Game',              desc: 'Moneyline and totals layer extending F5 context into full-game pricing.',                       href: '/picks/mlb/game' },
  F3:          { name: 'First 3 Innings',         desc: 'Early-game starter window for openers, short leashes, and lineup-top exposure.',               href: '/picks/mlb/f3' },
  F1H:         { name: 'First Half',              desc: 'Hybrid innings window before bullpen noise dominates the projection.',                         href: '/picks/mlb/f1h' },
  F7:          { name: 'First 7 Innings',         desc: 'Late starter and bridge-relief pricing before full bullpen exposure.',                         href: '/picks/mlb/f7' },
  BATTER_K:    { name: 'Batter Strikeouts',       desc: 'Pitcher shape, zone, chase, and batter whiff profile for batter K props.',                     href: '/picks/mlb/batter-k' },
  BATTER_TB:   { name: 'Total Bases',            desc: 'Contact quality, matchup, lineup slot, and park context for total-base props.',                href: '/picks/mlb/batter-tb' },
  BATTER_HITS: { name: 'Hits Props',             desc: 'Contact rate, expected average, platoon split, and park context for hits props.',              href: '/picks/mlb/batter-hits' },
  PITCHER_ER:  { name: 'Pitcher ER Props',       desc: 'Starter quality, opponent run creation, weather, and leash for earned-runs props.',            href: '/picks/mlb/pitcher-er' },
  '1I':        { name: 'First Inning Moneyline', desc: 'Three-way first-inning pricing for home, away, or draw outcomes.',                             href: '/picks/mlb/1i' },
}

export async function ModelsGrid() {
  let stats: Array<{ system: string; win_rate: number; roi: number; total_bets: number }> = []
  const sparklines: Record<string, Awaited<ReturnType<typeof apiGetSparklineBySystem>>> = {}
  try {
    const db = await apiGetStats().then(s => s.bySystem)
    stats = db.map(s => ({
      system: s.system, win_rate: parseFloat(String(s.win_rate)),
      roi: parseFloat(String(s.roi ?? 0)), total_bets: parseInt(String(s.total_bets)),
    }))
    const sparklineResults = await Promise.allSettled(
      stats.map(s => apiGetSparklineBySystem(s.system, 30))
    )
    sparklineResults.forEach((r, i) => {
      if (r.status === 'fulfilled') sparklines[stats[i].system] = r.value
    })
  } catch { /* API unavailable -- render empty */ }

  if (stats.length === 0) return (
    <section style={{ padding: '56px 0 0' }}>
      <SectionHeader />
      <div style={{ border: '1px solid var(--basalt)', borderRadius: 'var(--radius-lg)', padding: '48px', textAlign: 'center', background: 'var(--graphite)' }}>
        <p className="times" style={{ fontSize: '14px', color: 'var(--fog)' }}>Loading systems&hellip;</p>
      </div>
    </section>
  )

  // Sort gate-cleared systems first, then by ROI -- strongest signal leads,
  // noise (low-volume outliers) sinks to the bottom of the still-complete grid.
  const systems: SystemCard[] = stats
    .filter(s => META[s.system])
    .sort((a, b) => {
      const ag = a.total_bets >= 200 ? 1 : 0
      const bg = b.total_bets >= 200 ? 1 : 0
      if (ag !== bg) return bg - ag
      return b.roi - a.roi
    })
    .map(s => {
      const meta = META[s.system]
      return {
        system: s.system,
        name: meta.name,
        desc: meta.desc,
        href: meta.href,
        tint: SYSTEM_COLOR[s.system] ?? 'var(--silver)',
        win_rate: s.win_rate,
        roi: s.roi,
        total_bets: s.total_bets,
        sparkline: sparklines[s.system] ?? null,
      }
    })

  return (
    <section style={{ padding: '56px 0 0' }}>
      <SectionHeader />
      <ModelsGridClient systems={systems} />
    </section>
  )
}

function SectionHeader() {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: '24px', gap: '16px' }}>
      <div>
        <h2 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)' }}>Active systems</h2>
        <p className="times" style={{ fontSize: '15px', color: 'var(--fog)', marginTop: '8px' }}>
          Every model we run, with its live paper record. Tap a system for the method and stats.
        </p>
      </div>
      <Link
        href="/models"
        className="times"
        style={{ fontSize: '14px', fontWeight: 600, color: 'var(--link)', textDecoration: 'none', whiteSpace: 'nowrap', flexShrink: 0 }}
      >
        Full methodology &rarr;
      </Link>
    </div>
  )
}
