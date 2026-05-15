import { apiGetStats } from '@/lib/betting-api'
import { SystemBadge }     from '@/components/ui/primitives'
import Link                from 'next/link'
import type { Metadata }   from 'next'

export const metadata: Metadata = {
  title:       'Models — Methodology & Transparency',
  description: 'How Beezy.VIP builds XGBoost models for MLB betting. Data sources, training methodology, walk-forward CV, and version history.',
}

export const revalidate = 3600

const MODEL_DETAIL = {
  NRFI: {
    version:     'v17',
    description: 'Predicts whether the first inning will be scoreless. Primary features: starting pitcher ERA/FIP/K%, umpire called-strike%, park factor, wind speed/direction, game-time temperature.',
    dataRange:   '2021–2025',
    ooaAUC:      0.618,
    href:        '/models/nrfi',
  },
  HR: {
    version:     'v6',
    description: 'Predicts batter HR probability per PA. Primary features: barrel rate, hard-hit%, launch angle, pitcher HR/9, fly ball%, park HR factor, platoon split.',
    dataRange:   '2021–2025',
    ooaAUC:      0.603,
    href:        '/models/hr',
  },
  F5: {
    version:     'v4',
    description: 'Predicts F5 winner and total. Primary features: starter SIERA, last-5 ERA, opponent wOBA, umpire run-scoring rate, weather.',
    dataRange:   '2022–2025',
    ooaAUC:      0.594,
    href:        '/models/f5',
  },
  K: {
    version:     'v9',
    description: 'Predicts starter strikeout total. Primary features: SwStr%, Zone%, ChaseStr%, opponent K%, L5 avg Ks, home/away split, opponent lineup K%.',
    dataRange:   '2021–2025',
    ooaAUC:      0.631,
    href:        '/models/k',
  },
  OUTS: {
    version:     'v3',
    description: 'Predicts outs recorded by a starter. Primary features: pitch efficiency, innings-per-start trend, bullpen availability, opponent OBP, game leverage.',
    dataRange:   '2022–2025',
    ooaAUC:      0.587,
    href:        '/models/outs',
  },
}

export default async function ModelsPage() {
  const stats = await apiGetStats().then(s => s.bySystem).catch(() => [])

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      <div className="mb-12">
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">Models</h1>
        <p className="mono text-sm text-muted">
          Full methodology. Bettors are skeptical — we show our work.
        </p>
      </div>

      {/* Approach overview */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-12">
        {[
          { label: 'Model Type',   value: 'XGBoost',         sub: 'Gradient boosted trees' },
          { label: 'Data Source',  value: '946k+ rows',      sub: 'Statcast pitch-level data' },
          { label: 'Validation',   value: 'Walk-forward CV', sub: 'No future data leakage' },
        ].map(item => (
          <div key={item.label} className="p-5 bg-[var(--surface)] border border-[var(--border)]">
            <p className="mono text-xs uppercase tracking-widest text-muted mb-2">{item.label}</p>
            <p className="font-bold text-sm">{item.value}</p>
            <p className="mono text-xs text-muted mt-1">{item.sub}</p>
          </div>
        ))}
      </div>

      {/* Training methodology */}
      <div className="border border-[var(--border)] p-6 mb-12">
        <h2 className="text-sm font-extrabold uppercase tracking-wide mb-4">Training Methodology</h2>
        <div className="space-y-3 text-sm text-muted leading-relaxed">
          <p>
            Each model uses walk-forward cross-validation: trained on seasons N through N-1,
            validated on season N. This mirrors live deployment — the model never sees future data.
          </p>
          <p>
            Kelly criterion gates betting: a bet only qualifies if model probability minus
            implied probability exceeds a system-specific edge threshold and Kelly stake is positive.
          </p>
          <p>
            Models enter production after clearing a 200-bet out-of-sample gate. Currently
            in paper mode — results are simulated, not real money.
          </p>
        </div>
      </div>

      {/* Per-model cards */}
      <h2 className="text-sm font-extrabold uppercase tracking-wide mb-6">Active Systems</h2>
      <div className="space-y-4">
        {Object.entries(MODEL_DETAIL).map(([system, detail]) => {
          const stat = stats.find(s => s.system === system)
          return (
            <div key={system} className="border border-[var(--border)] bg-[var(--surface)] p-6">
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center gap-3">
                  <SystemBadge system={system} />
                  <span className="mono text-xs text-muted">{detail.version}</span>
                </div>
                <Link
                  href={detail.href}
                  className="mono text-xs uppercase tracking-widest text-muted hover:text-accent transition-colors"
                >
                  Full Detail →
                </Link>
              </div>

              <p className="text-sm text-muted leading-relaxed mb-4">{detail.description}</p>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-4 border-t border-[var(--border)]">
                <div>
                  <p className="mono text-xs text-muted uppercase tracking-widest mb-1">OOS AUC</p>
                  <p className="mono text-sm font-semibold text-accent">{detail.ooaAUC.toFixed(3)}</p>
                </div>
                <div>
                  <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Training Data</p>
                  <p className="mono text-sm font-semibold">{detail.dataRange}</p>
                </div>
                {stat && (
                  <>
                    <div>
                      <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Live Win Rate</p>
                      <p className="mono text-sm font-semibold">{parseFloat(String(stat.win_rate)).toFixed(1)}%</p>
                    </div>
                    <div>
                      <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Live ROI</p>
                      <p className={`mono text-sm font-semibold ${parseFloat(String(stat.roi)) > 0 ? 'text-win' : 'text-loss'}`}>
                        {parseFloat(String(stat.roi)) > 0 ? '+' : ''}{parseFloat(String(stat.roi)).toFixed(1)}%
                      </p>
                    </div>
                  </>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Data sources */}
      <div className="border border-[var(--border)] p-6 mt-12">
        <h2 className="text-sm font-extrabold uppercase tracking-wide mb-4">Data Sources</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {[
            { name: 'Statcast',        desc: '946k+ pitch rows · Exit velo, launch angle, spin rate, zones' },
            { name: 'MLB Stats API',   desc: 'Game results · Nightly settlement · Lineup data' },
            { name: 'Open-Meteo',      desc: 'Weather · Temperature, wind speed/direction, precipitation' },
            { name: 'Umpire Scorecards', desc: 'K-rate, called-strike%, run-scoring tendency' },
            { name: 'SGO Odds',        desc: 'Opening and closing lines · Implied probabilities' },
          ].map(src => (
            <div key={src.name} className="flex gap-3">
              <span className="text-accent mt-0.5">+</span>
              <div>
                <p className="text-sm font-semibold">{src.name}</p>
                <p className="mono text-xs text-muted">{src.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
