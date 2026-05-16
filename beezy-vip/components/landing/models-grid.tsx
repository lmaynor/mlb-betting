import { apiGetStats } from '@/lib/betting-api'
import Link from 'next/link'

const SYSTEM_META: Record<string, {
  name:        string
  description: string
  href:        string
  pillStyle:   string
}> = {
  NRFI: {
    name:        'No Run First Inning',
    description: 'Starter ERA, ump K-rate, weather, and park factors combine to predict scoreless first innings.',
    href:        '/picks/mlb/nrfi',
    pillStyle:   'background:#052016;color:#10b981;border:.5px solid #0f6e56',
  },
  HR: {
    name:        'Home Run Props',
    description: 'Launch angle, exit velocity, and barrel rate vs. pitcher HR/9.',
    href:        '/picks/mlb/hr',
    pillStyle:   'background:#1c1207;color:#f59e0b;border:.5px solid #854f0b',
  },
  F5: {
    name:        'First 5 Innings',
    description: 'Starting pitcher quality, bullpen rest, and lineup data for F5 moneylines.',
    href:        '/picks/mlb/f5',
    pillStyle:   'background:#040e1c;color:#3b82f6;border:.5px solid #185fa5',
  },
  K: {
    name:        'Strikeout Props',
    description: 'SwStr%, zone rate, and opponent K% to project starter strikeout over/unders.',
    href:        '/picks/mlb/k',
    pillStyle:   'background:#0e0718;color:#a78bfa;border:.5px solid #534ab7',
  },
  OUTS: {
    name:        'Pitcher Outs Props',
    description: 'IP projection via Normal model for DraftKings pitcher outs markets.',
    href:        '/picks/mlb/outs',
    pillStyle:   'background:#1a0d05;color:#fb923c;border:.5px solid #9a3412',
  },
}

const SEED_STATS = [
  { system: 'NRFI', win_rate: 61.5, roi:  7.88, total_bets: 25 },
  { system: 'HR',   win_rate: 54.0, roi:  3.76, total_bets: 18 },
  { system: 'F5',   win_rate: 41.9, roi: -7.92, total_bets: 43 },
  { system: 'K',    win_rate: 47.6, roi:-11.80, total_bets: 42 },
  { system: 'OUTS', win_rate: 68.9, roi: 37.57, total_bets: 45 },
]

function pillStyleToObj(s: string): Record<string, string> {
  return Object.fromEntries(
    s.split(';').filter(Boolean).map(p => {
      const [k, v] = p.split(':')
      return [k.trim().replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v.trim()]
    })
  )
}

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
    <section className="px-5 py-8 border-b" style={{ borderColor: 'var(--border)' }}>
      <div className="flex items-center justify-between mb-4">
        <span className="mono text-[10px] tracking-widest uppercase" style={{ color: 'var(--muted)' }}>
          Active systems
        </span>
        <Link href="/models" className="text-xs" style={{ color: 'var(--info)' }}>
          Full methodology &rarr;
        </Link>
      </div>

      {/* 3-col grid — 5 real cards + 1 "coming soon" ghost */}
      <div
        className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 border"
        style={{ borderColor: 'var(--border)' }}
      >
        {stats.map((s, i) => {
          const meta = SYSTEM_META[s.system]
          if (!meta) return null
          const roiPos = s.roi >= 0
          return (
            <Link
              key={s.system}
              href={meta.href}
              className="block p-4 transition-colors group sys-grid-item"
              style={{ background: 'var(--bg)' }}
            >
              <div className="flex items-start justify-between mb-3">
                <span
                  className="mono text-[9px] font-semibold tracking-wider px-1.5 py-0.5 inline-block"
                  style={pillStyleToObj(meta.pillStyle)}
                >
                  {s.system}
                </span>
                <span
                  className="mono text-xs font-semibold"
                  style={{ color: roiPos ? 'var(--win)' : 'var(--loss)' }}
                >
                  {roiPos ? '+' : ''}{s.roi.toFixed(1)}%
                </span>
              </div>
              <h3 className="font-semibold text-xs mb-1 transition-colors" style={{ color: 'var(--text)' }}>
                {meta.name}
              </h3>
              <p className="text-xs leading-relaxed mb-4" style={{ color: 'var(--muted)' }}>
                {meta.description}
              </p>
              <div
                className="pt-3 border-t grid grid-cols-3 gap-1"
                style={{ borderColor: 'var(--border)' }}
              >
                <div>
                  <p className="mono text-[9px] uppercase tracking-wider" style={{ color: 'var(--muted)' }}>WR</p>
                  <p className="mono text-xs font-semibold" style={{ color: 'var(--text)' }}>{s.win_rate.toFixed(1)}%</p>
                </div>
                <div>
                  <p className="mono text-[9px] uppercase tracking-wider" style={{ color: 'var(--muted)' }}>Bets</p>
                  <p className="mono text-xs font-semibold" style={{ color: 'var(--text)' }}>{s.total_bets}</p>
                </div>
                <div>
                  <p className="mono text-[9px] uppercase tracking-wider" style={{ color: 'var(--muted)' }}>Gate</p>
                  <p className="mono text-xs font-semibold" style={{ color: 'var(--muted)' }}>{s.total_bets}/200</p>
                </div>
              </div>
            </Link>
          )
        })}

        {/* Ghost "more coming soon" card — 6th slot */}
        <div
          className="p-4 flex items-center justify-center"
          style={{
            background:   'var(--surface)',
            borderLeft:   `.5px solid var(--border)`,
            borderTop:    stats.length > 3 ? `.5px solid var(--border)` : undefined,
            minHeight:    '140px',
          }}
        >
          <div className="text-center">
            <div className="mono text-[9px] tracking-widest uppercase mb-1" style={{ color: 'var(--border-bright)' }}>
              More coming soon
            </div>
            <div className="text-[10px]" style={{ color: 'var(--border-bright)' }}>
              Batter props &middot; Live models
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
