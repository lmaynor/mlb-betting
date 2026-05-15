import { notFound }       from 'next/navigation'
import { getModelSpec, MODEL_SPECS } from '@/lib/model-specs'
import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import { PicksTable }                from '@/components/picks/picks-table'
import { SystemBadge, StatCard }     from '@/components/ui/primitives'
import { ogUrl }                     from '@/lib/og'
import Link                          from 'next/link'
import type { Metadata }             from 'next'

type Props = { params: { slug: string } }

export const revalidate = 3600

export async function generateStaticParams() {
  return MODEL_SPECS.map(m => ({ slug: m.slug }))
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const spec = getModelSpec(params.slug)
  if (!spec) return { title: 'Model Not Found' }
  return {
    title:       `${spec.name} Model — Methodology & Results`,
    description: `${spec.name} XGBoost model methodology, feature list, OOS AUC, and full settled bet history. Beezy.VIP ${spec.system} system.`,
    openGraph: {
      images: [{ url: ogUrl({ title: spec.name, sub: `${spec.system} System · OOS AUC ${spec.oosAUC}`, stat1: `Version:${spec.version}`, stat2: `Features:${spec.features.length}`, stat3: `Data:${spec.dataRange}` }), width: 1200, height: 630 }],
    },
  }
}

const IMPORTANCE_COLOR: Record<string, string> = {
  High:   'text-win   border-win/30   bg-win/5',
  Medium: 'text-[#FFB800] border-[#FFB800]/30 bg-[#FFB800]/5',
  Low:    'text-muted border-muted/20 bg-muted/5',
}

export default async function ModelDetailPage({ params }: Props) {
  const spec = getModelSpec(params.slug)
  if (!spec) notFound()

  const [allStats, history] = await Promise.all([
    apiGetStats().then(s => s.bySystem).catch(() => []),
    getPicks({ system: spec.system, status: 'settled', limit: 100 }).catch(() => []),
  ])

  const stat     = allStats.find(s => s.system === spec.system)
  const roi      = stat ? parseFloat(String(stat.roi))      : null
  const winRate  = stat ? parseFloat(String(stat.win_rate)) : null
  const bets     = stat ? parseInt(String(stat.total_bets)) : 0
  const avgEdge  = stat ? parseFloat(String(stat.avg_edge)) : null
  const totalPnl = stat ? parseFloat(String(stat.total_pnl)) : null

  // Build calibration buckets from history (model prob vs actual hit rate)
  const buckets: Record<string, { count: number; wins: number }> = {}
  for (const pick of history) {
    const bucket = `${Math.floor(pick.model_prob * 10) * 10}–${Math.floor(pick.model_prob * 10) * 10 + 10}%`
    if (!buckets[bucket]) buckets[bucket] = { count: 0, wins: 0 }
    buckets[bucket].count++
    if (pick.result === 'win') buckets[bucket].wins++
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 mono text-xs text-muted mb-8">
        <Link href="/models" className="hover:text-accent transition-colors">Models</Link>
        <span>·</span>
        <SystemBadge system={spec.system} />
      </div>

      {/* Header */}
      <div className="mb-10">
        <div className="flex items-center gap-3 mb-3">
          <h1 className="text-2xl font-extrabold uppercase tracking-tight">{spec.name}</h1>
          <span className="mono text-xs text-muted border border-[var(--border)] px-2 py-0.5">
            {spec.version}
          </span>
        </div>
        <p className="text-muted text-sm max-w-2xl leading-relaxed">{spec.description}</p>
        {spec.learnSlug && (
          <Link
            href={`/learn/${spec.learnSlug}`}
            className="mono text-xs text-muted hover:text-accent transition-colors mt-2 inline-block"
          >
            Read the betting guide →
          </Link>
        )}
      </div>

      {/* Performance stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 border border-[var(--border)] mb-10">
        <StatCard label="OOS AUC"   value={spec.oosAUC.toFixed(3)}        sub="Out-of-sample" />
        <StatCard label="Bets"      value={String(bets)}                   sub="Paper mode" />
        <StatCard
          label="Win Rate"
          value={winRate !== null ? `${winRate.toFixed(1)}%` : '—'}
          sub="W / (W+L)"
        />
        <StatCard
          label="ROI"
          value={roi !== null ? `${roi > 0 ? '+' : ''}${roi.toFixed(1)}%` : '—'}
          sub="On settled bets"
          accent={roi !== null && roi > 0}
        />
        <StatCard
          label="Avg Edge"
          value={avgEdge !== null ? `+${avgEdge.toFixed(1)}%` : '—'}
          sub="Model vs implied"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-10">
        {/* Model config */}
        <div className="border border-[var(--border)] p-5">
          <h2 className="text-sm font-extrabold uppercase tracking-wide mb-5">Model Configuration</h2>
          <div className="space-y-3">
            {[
              { label: 'Algorithm',       value: 'XGBoost (gradient boosted trees)' },
              { label: 'Training Data',   value: spec.dataRange },
              { label: 'Validation',      value: 'Walk-forward cross-validation' },
              { label: 'Edge Threshold',  value: `+${(spec.edgeThreshold * 100).toFixed(0)}% minimum` },
              { label: 'Kelly Fraction',  value: `${spec.kellyFraction * 100}% Kelly` },
              { label: 'Bet Gate',        value: `${bets}/200 (${bets >= 200 ? 'cleared' : 'in progress'})` },
            ].map(row => (
              <div key={row.label} className="flex justify-between items-center py-2 border-b border-[var(--border)]">
                <span className="mono text-xs text-muted">{row.label}</span>
                <span className={`mono text-xs font-semibold ${row.label === 'Bet Gate' && bets >= 200 ? 'text-win' : 'text-text'}`}>
                  {row.value}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Calibration table */}
        <div className="border border-[var(--border)] p-5">
          <h2 className="text-sm font-extrabold uppercase tracking-wide mb-5">
            Calibration
            <span className="mono text-xs text-muted font-normal ml-2">
              Model prob vs actual hit rate
            </span>
          </h2>
          {Object.keys(buckets).length === 0 ? (
            <p className="mono text-xs text-muted">
              Calibration data available after more settled bets.
            </p>
          ) : (
            <div className="space-y-2">
              {Object.entries(buckets).sort().map(([bucket, data]) => {
                const hitRate  = data.count > 0 ? data.wins / data.count : 0
                const barWidth = Math.round(hitRate * 100)
                return (
                  <div key={bucket}>
                    <div className="flex justify-between mb-1">
                      <span className="mono text-xs text-muted">{bucket}</span>
                      <span className="mono text-xs text-muted">
                        {(hitRate * 100).toFixed(0)}% ({data.wins}/{data.count})
                      </span>
                    </div>
                    <div className="h-1.5 bg-[var(--border)]">
                      <div
                        className="h-1.5 bg-accent transition-all"
                        style={{ width: `${barWidth}%` }}
                      />
                    </div>
                  </div>
                )
              })}
              <p className="mono text-xs text-muted mt-3">
                Well-calibrated model: bar width should match bucket label.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Feature table */}
      <div className="border border-[var(--border)] mb-10">
        <div className="px-5 py-4 border-b border-[var(--border)] bg-[var(--surface)]">
          <h2 className="text-sm font-extrabold uppercase tracking-wide">Feature List</h2>
          <p className="mono text-xs text-muted mt-1">{spec.features.length} features · XGBoost importance ranking</p>
        </div>
        <div className="grid grid-cols-3 border-b border-[var(--border)] bg-[var(--surface)]">
          {['Feature', 'Description', 'Importance'].map(h => (
            <div key={h} className="px-5 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
          ))}
        </div>
        {spec.features.map((f, i) => (
          <div
            key={i}
            className="grid grid-cols-3 border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
          >
            <div className="px-5 py-3 mono text-xs font-semibold text-text">{f.name}</div>
            <div className="px-5 py-3 text-xs text-muted">{f.description}</div>
            <div className="px-5 py-3">
              <span className={`mono text-xs font-semibold px-2 py-0.5 border ${IMPORTANCE_COLOR[f.importance]}`}>
                {f.importance}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* 200-bet gate progress */}
      <div className="border border-[var(--border)] p-5 mb-10 flex items-center gap-6">
        <div className="flex-1">
          <div className="flex justify-between mb-1">
            <span className="mono text-xs text-muted uppercase tracking-widest">200-Bet Gate Progress</span>
            <span className="mono text-xs text-muted">{bets}/200</span>
          </div>
          <div className="h-1 bg-[var(--border)]">
            <div
              className="h-1 transition-all"
              style={{ width: `${Math.min(100, (bets / 200) * 100)}%`, background: spec.color }}
            />
          </div>
        </div>
        {bets >= 200 ? (
          <span className="mono text-xs text-win border border-win/30 bg-win/5 px-3 py-1.5 font-semibold">
            Gate Cleared — Live
          </span>
        ) : (
          <span className="mono text-xs text-muted border border-[var(--border)] px-3 py-1.5">
            Paper Mode
          </span>
        )}
      </div>

      {/* P&L summary */}
      {totalPnl !== null && (
        <div className="grid grid-cols-2 md:grid-cols-4 border border-[var(--border)] mb-10">
          <StatCard label="Total P&L"   value={`${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}u`} accent={totalPnl > 0} />
          <StatCard label="Avg Edge"    value={avgEdge !== null ? `+${avgEdge.toFixed(1)}%` : '—'} />
          <StatCard label="Win Rate"    value={winRate !== null ? `${winRate.toFixed(1)}%` : '—'} />
          <StatCard label="Sample Size" value={String(bets)} sub="Settled bets" />
        </div>
      )}

      {/* Full bet history */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-extrabold uppercase tracking-wide">Full Settled History</h2>
        <Link
          href={`/picks/mlb/${spec.slug}`}
          className="mono text-xs uppercase tracking-widest text-muted hover:text-accent transition-colors"
        >
          Today&apos;s Picks →
        </Link>
      </div>

      {history.length === 0 ? (
        <div className="border border-[var(--border)] p-12 text-center">
          <p className="mono text-xs text-muted">No settled bets yet.</p>
        </div>
      ) : (
        <PicksTable picks={history} />
      )}

      {/* Structured data */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            '@context':  'https://schema.org',
            '@type':     'TechArticle',
            headline:    `${spec.name} Model — Beezy.VIP`,
            description: spec.description,
            url:         `https://beezy.vip/models/${spec.slug}`,
          }),
        }}
      />
    </div>
  )
}
