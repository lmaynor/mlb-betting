import Link          from 'next/link'
import { apiGetStats } from '@/lib/betting-api'
import { SystemBadge }     from '@/components/ui/primitives'
import type { Metadata }   from 'next'

export const metadata: Metadata = {
  title:       'MLB Picks Today — All Systems',
  description: 'Today\'s MLB picks from all Beezy.VIP machine learning systems: NRFI, HR, F5, K, and OUTS.',
}

export const revalidate = 60

const SYSTEMS = [
  { key: 'NRFI', slug: 'nrfi', desc: 'No Run First Inning', color: '#00FF87' },
  { key: 'HR',   slug: 'hr',   desc: 'Home Run Props',      color: '#FFB800' },
  { key: 'F5',   slug: 'f5',   desc: 'First 5 Innings',     color: '#00B4FF' },
  { key: 'K',    slug: 'k',    desc: 'Strikeout Props',      color: '#BF5FFF' },
  { key: 'OUTS', slug: 'outs', desc: 'Outs Props',           color: '#FF6B35' },
]

export default async function MLBPicksPage() {
  const stats = await apiGetStats().then(s => s.bySystem).catch(() => [])

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      <div className="mb-8">
        <p className="mono text-xs text-accent uppercase tracking-widest mb-2">Picks</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">MLB Picks</h1>
        <p className="mono text-sm text-muted">5 systems · DraftKings · Paper mode</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {SYSTEMS.map(s => {
          const stat = stats.find(x => x.system === s.key)
          const roi  = stat ? parseFloat(String(stat.roi)) : null
          const wr   = stat ? parseFloat(String(stat.win_rate)) : null
          const bets = stat ? parseInt(String(stat.total_bets)) : 0

          return (
            <Link
              key={s.key}
              href={`/picks/mlb/${s.slug}`}
              className="group block p-5 bg-[var(--surface)] border border-[var(--border)] hover:border-[var(--border-bright)] transition-colors"
            >
              <div className="flex items-start justify-between mb-4">
                <SystemBadge system={s.key} />
                {roi !== null && (
                  <span
                    className="mono text-xs font-semibold"
                    style={{ color: s.color }}
                  >
                    {roi > 0 ? '+' : ''}{roi.toFixed(1)}% ROI
                  </span>
                )}
              </div>

              <h2 className="font-bold text-sm mb-4 group-hover:text-accent transition-colors">
                {s.desc}
              </h2>

              <div className="grid grid-cols-3 gap-2 pt-4 border-t border-[var(--border)]">
                <div>
                  <p className="mono text-xs text-muted">Win Rate</p>
                  <p className="mono text-sm font-semibold">{wr !== null ? `${wr.toFixed(1)}%` : '—'}</p>
                </div>
                <div>
                  <p className="mono text-xs text-muted">Bets</p>
                  <p className="mono text-sm font-semibold">{bets}</p>
                </div>
                <div>
                  <p className="mono text-xs text-muted">Gate</p>
                  <div className="mt-1.5">
                    <div className="h-1 bg-[var(--border)] w-full">
                      <div
                        className="h-1"
                        style={{
                          width:      `${Math.min(100, (bets / 200) * 100)}%`,
                          background: s.color,
                        }}
                      />
                    </div>
                  </div>
                </div>
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
