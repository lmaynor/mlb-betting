import { apiGetStats, apiGetSparklineBySystem } from '@/lib/betting-api'
import Link from 'next/link'
import { SystemSparkline } from './system-sparkline'

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
    <section style={{ padding: '24px 20px', borderBottom: B }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
        <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--muted)' }}>Active Systems</span>
        <Link href="/models" style={{ fontSize: '11px', fontFamily: 'Arial, Helvetica, sans-serif', fontWeight: 700, color: '#9999ff', textDecoration: 'underline' }}>Full methodology &rarr;</Link>
      </div>
      <div style={{ border: B, padding: '40px', textAlign: 'center' }}>
        <p className="times" style={{ fontSize: '13px', color: '#888890' }}>Loading systems&hellip;</p>
      </div>
    </section>
  )

  const columns = 3
  const rows = Math.ceil(stats.length / columns)

  return (
    <section style={{ padding: '24px 20px', borderBottom: B_INNER }}>
      {/* Section eyebrow -- Dell display block */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
        <span className="dell-display" style={{ fontSize: '14px', color: 'var(--text)' }}>Active Systems</span>
        <Link
          href="/models"
          style={{ fontSize: '11px', fontFamily: 'Arial, Helvetica, sans-serif', fontWeight: 700, color: '#9999ff', textDecoration: 'underline' }}
        >
          Full methodology &rarr;
        </Link>
      </div>

      <div className="systems-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', border: B }}>
        {stats.map((s, i) => {
          const col = i % columns
          const row = Math.floor(i / columns)
          const cellBorder: React.CSSProperties = {
            borderRight:  col < columns - 1 ? B : undefined,
            borderBottom: row < rows - 1 ? B : undefined,
          }

          const meta = META[s.system]
          if (!meta) return null
          const roiPos = s.roi >= 0

          return (
            // Dell ribbon card: title bar + tinted body
            <Link
              key={s.system}
              href={meta.href}
              className="card-hover"
              style={{ ...cellBorder, display: 'block', textDecoration: 'none' }}
            >
              {/* Ribbon title bar -- white/light header with system label */}
              <div style={{ background: '#0a0a0c', borderBottom: B, padding: '6px 12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span
                  className="dell-heading"
                  style={{ fontSize: '10px', letterSpacing: '0.06em', color: meta.tint }}
                >
                  {s.system}
                </span>
                <span
                  className="mono"
                  style={{ fontSize: '11px', fontWeight: 600, color: roiPos ? '#b3bd95' : '#d77a7a' }}
                >
                  {roiPos ? '+' : ''}{s.roi.toFixed(1)}%
                </span>
              </div>

              {/* Ribbon body -- tinted fill */}
              <div style={{ padding: '14px 12px', background: meta.tintBg }}>
                <div
                  className="dell-heading"
                  style={{ fontSize: '11px', color: '#f5f5f7', marginBottom: '6px', letterSpacing: '0.02em' }}
                >
                  {meta.name}
                </div>
                <div
                  className="times"
                  style={{ fontSize: '12px', color: '#a1a1aa', lineHeight: 1.5, marginBottom: '12px' }}
                >
                  {meta.desc}
                </div>
                <div
                  style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '8px', paddingTop: '10px', borderTop: '1px solid rgba(255,255,255,0.08)' }}
                >
                  {[['WR', s.win_rate.toFixed(1)+'%'], ['Bets', String(s.total_bets)], ['Gate', s.total_bets+'/200']].map(([label, val]) => (
                    <div key={label}>
                      <div className="dell-heading" style={{ fontSize: '8px', letterSpacing: '0.1em', color: '#888890' }}>{label}</div>
                      <div className="mono" style={{ fontSize: '11px', fontWeight: 600, color: label === 'Gate' ? '#888890' : meta.tint }}>{val}</div>
                    </div>
                  ))}
                </div>
                {sparklines[s.system] && (
                  <SystemSparkline data={sparklines[s.system]} color={meta.tint} />
                )}
              </div>
            </Link>
          )
        })}
      </div>
    </section>
  )
}
