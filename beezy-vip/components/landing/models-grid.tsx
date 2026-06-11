import { apiGetStats, apiGetSparklineBySystem } from '@/lib/betting-api'
import Link from 'next/link'
import { ModelsGridClient, type SystemCard } from './models-grid-client'

const B = '1px solid #000'
const B_INNER = '1px solid #1f1f24'

// Dell 1996 catalog tint mapping: each system gets its own ribbon color
const META: Record<string, {
  name: string
  desc: string
  href: string
  tint: string   // foreground tint color
  tintBg: string // dark ribbon body fill
  border: string
}> = {
  NRFI:        { name: 'No Run First Inning',   desc: 'Starter ERA, ump K-rate, weather, and park factors combine to predict scoreless first innings.', href: '/picks/mlb/nrfi',        tint: '#b3bd95', tintBg: '#1a2218', border: '1px solid #8e9e78' },
  HR:          { name: 'Home Run Props',         desc: 'Launch angle, exit velocity, and barrel rate vs. pitcher HR/9.',                                href: '/picks/mlb/hr',          tint: '#d77a7a', tintBg: '#2a1818', border: '1px solid #b05050' },
  F5:          { name: 'First 5 Innings',        desc: 'Starting pitcher quality, bullpen rest, and lineup data for F5 moneylines.',                    href: '/picks/mlb/f5',          tint: '#9ab6c8', tintBg: '#131e24', border: '1px solid #6a8fa0' },
  K:           { name: 'Strikeout Props',         desc: 'SwStr%, zone rate, and opponent K% to project starter strikeout over/unders.',                 href: '/picks/mlb/k',           tint: '#8c9ae0', tintBg: '#0f1024', border: '1px solid #5c6bbc' },
  OUTS:        { name: 'Pitcher Outs Props',     desc: 'IP projection via Normal model for DraftKings pitcher outs markets.',                           href: '/picks/mlb/outs',        tint: '#e6915d', tintBg: '#2a1a0f', border: '1px solid #c06830' },
  GAME:        { name: 'Full Game',              desc: 'Moneyline and totals layer extending F5 context into full-game pricing.',                       href: '/picks/mlb/game',        tint: '#8e8a25', tintBg: '#1c1c0a', border: '1px solid #6a6615' },
  F3:          { name: 'First 3 Innings',         desc: 'Early-game starter window for openers, short leashes, and lineup-top exposure.',               href: '/picks/mlb/f3',          tint: '#a5b8c0', tintBg: '#131a1e', border: '1px solid #7a9aa5' },
  F1H:         { name: 'First Half',              desc: 'Hybrid innings window before bullpen noise dominates the projection.',                         href: '/picks/mlb/f1h',         tint: '#9ab6c8', tintBg: '#131e24', border: '1px solid #6a8fa0' },
  F7:          { name: 'First 7 Innings',         desc: 'Late starter and bridge-relief pricing before full bullpen exposure.',                         href: '/picks/mlb/f7',          tint: '#8c9ae0', tintBg: '#0f1024', border: '1px solid #5c6bbc' },
  BATTER_K:    { name: 'Batter Strikeouts',       desc: 'Pitcher shape, zone, chase, and batter whiff profile for batter K props.',                     href: '/picks/mlb/batter-k',    tint: '#8c9ae0', tintBg: '#0f1024', border: '1px solid #5c6bbc' },
  BATTER_TB:   { name: 'Total Bases',            desc: 'Contact quality, matchup, lineup slot, and park context for total-base props.',                href: '/picks/mlb/batter-tb',   tint: '#c0d4a7', tintBg: '#141e0f', border: '1px solid #8aaa6c' },
  BATTER_HITS: { name: 'Hits Props',             desc: 'Contact rate, expected average, platoon split, and park context for hits props.',              href: '/picks/mlb/batter-hits', tint: '#a5b8c0', tintBg: '#131a1e', border: '1px solid #7a9aa5' },
  PITCHER_ER:  { name: 'Pitcher ER Props',       desc: 'Starter quality, opponent run creation, weather, and leash for earned-runs props.',            href: '/picks/mlb/pitcher-er',  tint: '#d77a7a', tintBg: '#2a1818', border: '1px solid #b05050' },
  '1I':        { name: 'First Inning Moneyline', desc: 'Three-way first-inning pricing for home, away, or draw outcomes.',                             href: '/picks/mlb/1i',          tint: '#9ab6c8', tintBg: '#131e24', border: '1px solid #6a8fa0' },
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
    <section style={{ padding: '40px 20px 32px', borderBottom: B_INNER }}>
      <SectionHeader />
      <div style={{ border: B, padding: '40px', textAlign: 'center' }}>
        <p className="times" style={{ fontSize: '13px', color: '#888890' }}>Loading systems&hellip;</p>
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
        tint: meta.tint,
        tintBg: meta.tintBg,
        border: meta.border,
        win_rate: s.win_rate,
        roi: s.roi,
        total_bets: s.total_bets,
        sparkline: sparklines[s.system] ?? null,
      }
    })

  return (
    <section style={{ padding: '40px 20px 32px', borderBottom: B_INNER }}>
      <SectionHeader />
      <p style={{ fontSize: '12px', color: 'var(--muted)', marginTop: '-8px', marginBottom: '14px' }}>
        Every model we run, with its live paper record. Tap a system for the method and stats.
      </p>
      <ModelsGridClient systems={systems} />
    </section>
  )
}

function SectionHeader() {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: '14px', gap: '12px' }}>
      <span className="dell-display" style={{ fontSize: '14px', color: 'var(--text)' }}>Active Systems</span>
      <Link
        href="/models"
        style={{ fontSize: '11px', fontFamily: 'Arial, Helvetica, sans-serif', fontWeight: 700, color: '#9999ff', textDecoration: 'underline' }}
      >
        Full methodology &rarr;
      </Link>
    </div>
  )
}
