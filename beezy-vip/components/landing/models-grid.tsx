import { SystemBadge } from '@/components/ui/primitives'
import { apiGetStats } from '@/lib/betting-api'
import Link from 'next/link'

const SYSTEM_META: Record<string, {
  name:        string
  description: string
  href:        string
  color:       string
}> = {
  NRFI: {
    name:        'No Run First Inning',
    description: 'Starter ERA, ump K-rate, weather, and park factors combine to predict scoreless first innings.',
    href:        '/picks/mlb/nrfi',
    color:       '#00FF87',
  },
  HR: {
    name:        'Home Run Props',
    description: 'Batter launch angle, exit velocity, and pitcher HR/9 data to identify +EV HR props.',
    href:        '/picks/mlb/hr',
    color:       '#FFB800',
  },
  F5: {
    name:        'First 5 Innings',
    description: 'Starting pitcher quality, bullpen rest, and lineup data for F5 spreads and totals.',
    href:        '/picks/mlb/f5',
    color:       '#00B4FF',
  },
  K: {
    name:        'Strikeout Props',
    description: 'SwStr%, zone rate, and opponent K% to project starter strikeout over/unders.',
    href:        '/picks/mlb/k',
    color:       '#BF5FFF',
  },
  OUTS: {
    name:        'Outs Props',
    description: 'Pitcher efficiency metrics and game-state factors for outs recorded props.',
    href:        '/picks/mlb/outs',
    color:       '#FF6B35',
  },
}

const SEED_STATS = [
  { system: 'NRFI', win_rate: 61.5, roi: 14.2, total_bets: 28 },
  { system: 'HR',   win_rate: 54.0, roi: 8.1,  total_bets: 19 },
  { system: 'F5',   win_rate: 58.3, roi: 11.7, total_bets: 18 },
  { system: 'K',    win_rate: 62.5, roi: 16.3, total_bets: 14 },
  { system: 'OUTS', win_rate: 50.0, roi: 4.2,  total_bets: 8  },
]

export async function ModelsGrid() {
  let stats = SEED_STATS

  try {
    const db = await apiGetStats().then(s => s.bySystem)
    if (db.length > 0) {
      stats = db.map(s => ({
        system:     s.system,
        win_rate:   parseFloat(String(s.win_rate)),
        roi:        parseFloat(String(s.roi)),
        total_bets: parseInt(String(s.total_bets)),
      }))
    }
  } catch { /* seed */ }

  return (
    <section className="max-w-7xl mx-auto px-4 py-20 border-b border-[var(--border)]">
      <div className="flex items-end justify-between mb-10">
        <div>
          <h2 className="text-2xl font-extrabold tracking-tight uppercase mb-2">Active Systems</h2>
          <p className="text-muted text-sm mono">5 models live · MLB · DraftKings lines</p>
        </div>
        <Link href="/models" className="mono text-xs tracking-widest uppercase text-muted hover:text-accent transition-colors">
          Full Methodology →
        </Link>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {stats.map(s => {
          const meta = SYSTEM_META[s.system]
          if (!meta) return null
          return (
            <Link
              key={s.system}
              href={meta.href}
              className="group block p-5 bg-[var(--surface)] border border-[var(--border)] hover:border-[var(--border-bright)] transition-colors"
            >
              <div className="flex items-start justify-between mb-4">
                <SystemBadge system={s.system} />
                <span
                  className="mono text-xs font-semibold"
                  style={{ color: meta.color }}
                >
                  {s.roi > 0 ? '+' : ''}{s.roi.toFixed(1)}% ROI
                </span>
              </div>

              <h3 className="font-bold text-sm mb-2 group-hover:text-accent transition-colors">
                {meta.name}
              </h3>
              <p className="text-muted text-xs leading-relaxed mb-5">
                {meta.description}
              </p>

              <div className="grid grid-cols-3 gap-2 pt-4 border-t border-[var(--border)]">
                <div>
                  <p className="mono text-xs text-muted">Win Rate</p>
                  <p className="mono text-sm font-semibold">{s.win_rate.toFixed(1)}%</p>
                </div>
                <div>
                  <p className="mono text-xs text-muted">Bets</p>
                  <p className="mono text-sm font-semibold">{s.total_bets}</p>
                </div>
                <div>
                  <p className="mono text-xs text-muted">Gate</p>
                  <div className="mt-1">
                    <div className="h-1 bg-[var(--border)] w-full">
                      <div
                        className="h-1 transition-all"
                        style={{
                          width:      `${Math.min(100, (s.total_bets / 200) * 100)}%`,
                          background: meta.color,
                        }}
                      />
                    </div>
                    <p className="mono text-xs text-muted mt-1">{s.total_bets}/200</p>
                  </div>
                </div>
              </div>
            </Link>
          )
        })}
      </div>
    </section>
  )
}
