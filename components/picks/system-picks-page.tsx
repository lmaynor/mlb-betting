import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import { PicksTable }                from '@/components/picks/picks-table'
import { StatCard, SystemBadge }     from '@/components/ui/primitives'
import Link                          from 'next/link'

const SYSTEM_META: Record<string, {
  name:        string
  description: string
  color:       string
  slug:        string
  learnSlug?:  string
}> = {
  NRFI: {
    name:        'No Run First Inning',
    description: 'XGBoost model combining starter ERA/FIP/K%, umpire called-strike rate, park factor, and weather to predict scoreless first innings.',
    color:       '#00FF87',
    slug:        'nrfi',
    learnSlug:   'what-is-nrfi',
  },
  HR: {
    name:        'Home Run Props',
    description: 'Barrel rate, exit velocity, launch angle, pitcher HR/9, park HR factor, and platoon splits identify +EV home run props.',
    color:       '#FFB800',
    slug:        'hr',
    learnSlug:   'home-run-props',
  },
  F5: {
    name:        'First 5 Innings',
    description: 'Starting pitcher SIERA, L5 ERA, opponent wOBA, and umpire run-scoring tendency for F5 spread and total bets.',
    color:       '#00B4FF',
    slug:        'f5',
    learnSlug:   'f5-betting',
  },
  K: {
    name:        'Strikeout Props',
    description: 'SwStr%, Zone%, ChaseStr%, opponent K% per lineup, and L5 average strikeouts project starter K totals.',
    color:       '#BF5FFF',
    slug:        'k',
    learnSlug:   'mlb-strikeout-props',
  },
  OUTS: {
    name:        'Outs Props',
    description: 'Pitch efficiency, innings-per-start trend, bullpen availability, and opponent OBP for outs recorded props.',
    color:       '#FF6B35',
    slug:        'outs',
  },
}

export async function SystemPicksPage({ system }: { system: string }) {
  const meta = SYSTEM_META[system]

  const [picks, allStats] = await Promise.all([
    getPicks({ system, date: 'today', limit: 50 }).catch(() => []),
    apiGetStats().then(s => s.bySystem).catch(() => []),
  ])

  const stats     = allStats.find(s => s.system === system)
  const winRate   = stats ? parseFloat(String(stats.win_rate)).toFixed(1) : '—'
  const roi       = stats ? parseFloat(String(stats.roi)) : 0
  const totalBets = stats ? parseInt(String(stats.total_bets)) : 0
  const avgEdge   = stats ? parseFloat(String(stats.avg_edge)).toFixed(1) : '—'

  // Recent history for this system
  const history = await getPicks({ system, status: 'settled', limit: 20 }).catch(() => [])

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-3">
          <Link href="/picks/mlb" className="mono text-xs text-muted hover:text-accent transition-colors">
            MLB Picks
          </Link>
          <span className="text-muted mono text-xs">·</span>
          <SystemBadge system={system} />
        </div>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">
          {meta.name}
        </h1>
        <p className="text-muted text-sm max-w-xl leading-relaxed">{meta.description}</p>
        {meta.learnSlug && (
          <Link
            href={`/learn/${meta.learnSlug}`}
            className="mono text-xs text-muted hover:text-accent transition-colors mt-2 inline-block"
          >
            Learn about {system} betting →
          </Link>
        )}
      </div>

      {/* System stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 border border-[var(--border)] mb-8">
        <StatCard
          label="ROI"
          value={`${roi > 0 ? '+' : ''}${roi.toFixed(1)}%`}
          sub="All settled bets"
          accent={roi > 0}
        />
        <StatCard label="Win Rate"   value={`${winRate}%`}       sub="W / (W+L)" />
        <StatCard label="Total Bets" value={String(totalBets)}   sub="Paper mode" />
        <StatCard label="Avg Edge"   value={`+${avgEdge}%`}      sub="Model vs implied" />
      </div>

      {/* 200-bet gate progress */}
      <div className="border border-[var(--border)] p-4 mb-8 flex items-center gap-6">
        <div className="flex-1">
          <div className="flex justify-between mb-1">
            <span className="mono text-xs text-muted uppercase tracking-widest">200-Bet Gate</span>
            <span className="mono text-xs text-muted">{totalBets}/200</span>
          </div>
          <div className="h-1 bg-[var(--border)]">
            <div
              className="h-1 transition-all"
              style={{
                width:      `${Math.min(100, (totalBets / 200) * 100)}%`,
                background: meta.color,
              }}
            />
          </div>
        </div>
        <div className="shrink-0">
          {totalBets >= 200 ? (
            <span className="mono text-xs text-win font-semibold border border-win/30 bg-win/5 px-2 py-1">
              GATE CLEARED
            </span>
          ) : (
            <span className="mono text-xs text-muted border border-[var(--border)] px-2 py-1">
              Paper Mode
            </span>
          )}
        </div>
      </div>

      {/* Today's picks */}
      <div className="mb-10">
        <h2 className="text-sm font-extrabold uppercase tracking-wide mb-4">
          Today&apos;s {system} Picks
        </h2>
        {picks.length === 0 ? (
          <div className="border border-[var(--border)] p-10 text-center">
            <p className="mono text-xs text-muted">No qualifying {system} picks today.</p>
            <p className="mono text-xs text-muted mt-1">
              Model runs at 13:00 UTC after lineups post.
            </p>
          </div>
        ) : (
          <PicksTable picks={picks} />
        )}
      </div>

      {/* Recent history */}
      {history.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-extrabold uppercase tracking-wide">Recent Results</h2>
            <Link
              href={`/results?system=${system}`}
              className="mono text-xs text-muted hover:text-accent transition-colors uppercase tracking-widest"
            >
              Full History →
            </Link>
          </div>
          <PicksTable picks={history} />
        </div>
      )}
    </div>
  )
}
