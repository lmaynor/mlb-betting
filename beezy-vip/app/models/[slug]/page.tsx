import { notFound }                          from 'next/navigation'
import { getModelSpec, MODEL_SPECS }          from '@/lib/model-specs'
import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import { PicksTable }                         from '@/components/picks/picks-table'
import { SystemBadge }                        from '@/components/ui/primitives'
import Link                                   from 'next/link'
import type { Metadata }                      from 'next'

export const dynamic = 'force-dynamic'

type Props = { params: Promise<{ slug: string }> }

const B = '1px solid var(--basalt)'

export async function generateStaticParams() {
  return MODEL_SPECS.map(m => ({ slug: m.slug }))
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params
  const spec = getModelSpec(slug)
  if (!spec) return { title: 'Model Not Found' }
  return {
    title:       `${spec.name} Model -- Methodology & Results`,
    description: `${spec.name} XGBoost model methodology, feature list, OOS metric, and full settled bet history. Beezy.FYI ${spec.system} system.`,
  }
}

const IMPORTANCE: Record<string, { color: string; border: string; bg: string }> = {
  High:   { color: 'var(--signal)', border: 'var(--win-border)', bg: 'var(--win-wash)' },
  Medium: { color: 'var(--warn)', border: 'var(--warn-border)', bg: 'var(--warn-wash)' },
  Low:    { color: 'var(--fog)', border: 'var(--iron)', bg: 'transparent' },
}

function StatBlock({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <div style={{ padding: '18px', borderRight: B }}>
      <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'var(--fog)', marginBottom: '6px' }}>{label}</div>
      <div className="mono" style={{ fontSize: '20px', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: accent ? 'var(--signal)' : 'var(--ash)' }}>{value}</div>
      {sub && <div className="times" style={{ fontSize: '11px', color: 'var(--fog)' }}>{sub}</div>}
    </div>
  )
}

export default async function ModelDetailPage({ params }: Props) {
  const { slug } = await params
  const spec = getModelSpec(slug)
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
  const gatePct  = Math.min(100, (bets / 200) * 100)

  // Calibration buckets
  const buckets: Record<string, { count: number; wins: number }> = {}
  for (const pick of history) {
    if (pick.model_prob == null) continue
    const bucket = `${Math.floor(pick.model_prob * 10) * 10}-${Math.floor(pick.model_prob * 10) * 10 + 10}%`
    if (!buckets[bucket]) buckets[bucket] = { count: 0, wins: 0 }
    buckets[bucket].count++
    if (pick.result === 'win') buckets[bucket].wins++
  }

  // OOS metric display -- K uses MAE, OUTS is proxy
  const metricLabel = spec.system === 'K' ? 'OOS MAE' : spec.system === 'OUTS' ? 'Model' : 'OOS AUC'
  const metricValue = spec.system === 'K' ? spec.oosAUC.toFixed(3) : spec.system === 'OUTS' ? 'Proxy' : spec.oosAUC.toFixed(3)

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 24px' }}>

      {/* Breadcrumb */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '24px' }}>
        <Link href="/models" className="times" style={{ fontSize: '13px', color: 'var(--link)', textDecoration: 'none' }}>Models</Link>
        <span className="mono" style={{ fontSize: '11px', color: 'var(--iron)' }}>&middot;</span>
        <SystemBadge system={spec.system} />
      </div>

      {/* Header */}
      <div style={{ marginBottom: '28px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '10px' }}>
          <h1 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)' }}>{spec.name}</h1>
          <span className="mono" style={{ fontSize: '11px', color: 'var(--silver)', border: B, borderRadius: 'var(--radius-pill)', padding: '3px 10px' }}>{spec.version}</span>
        </div>
        <p style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.65, maxWidth: '560px', marginBottom: '10px' }}>{spec.description}</p>
        {spec.learnSlug && (
          <Link href={`/learn/${spec.learnSlug}`} className="times" style={{ fontSize: '13px', color: 'var(--link)', textDecoration: 'none' }}>
            Read the betting guide &rarr;
          </Link>
        )}
      </div>

      {/* Performance stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', overflow: 'hidden', marginBottom: '28px' }}>
        <StatBlock label={metricLabel}  value={metricValue}                                                    sub="Out-of-sample" />
        <StatBlock label="Bets"         value={String(bets)}                                                   sub="Paper mode" />
        <StatBlock label="Win Rate"     value={winRate !== null ? `${winRate.toFixed(1)}%` : '--'}             sub="W / (W+L)" />
        <StatBlock label="ROI"          value={roi !== null ? `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%` : '--'} accent={roi !== null && roi > 0} sub="Settled bets" />
        <div style={{ padding: '18px' }}>
          <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '6px' }}>Avg Edge</div>
          <div className="mono" style={{ fontSize: '20px', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: 'var(--signal)' }}>
            {avgEdge !== null ? `+${avgEdge.toFixed(1)}%` : '--'}
          </div>
          <div className="times" style={{ fontSize: '11px', color: 'var(--fog)' }}>Model vs implied</div>
        </div>
      </div>

      {/* Config + Calibration */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '28px' }}>

        {/* Config */}
        <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', padding: '20px' }}>
          <div className="dell-display" style={{ fontSize: '16px', color: 'var(--chalk)', marginBottom: '16px' }}>Model configuration</div>
          {[
            { label: 'Algorithm',      value: 'XGBoost (gradient boosted trees)' },
            { label: 'Training Data',  value: spec.dataRange },
            { label: 'Validation',     value: 'Walk-forward cross-validation' },
            { label: 'Edge Threshold', value: `+${(spec.edgeThreshold * 100).toFixed(0)}% minimum` },
            { label: 'Kelly Fraction', value: `${spec.kellyFraction * 100}% Kelly` },
            { label: 'Bet Gate',       value: `${bets}/200 (${bets >= 200 ? 'cleared' : 'in progress'})` },
          ].map((row, i, arr) => (
            <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', padding: '9px 0', borderBottom: i < arr.length - 1 ? B : undefined }}>
              <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>{row.label}</span>
              <span className="mono" style={{ fontSize: '11px', fontWeight: 600, color: row.label === 'Bet Gate' && bets >= 200 ? 'var(--signal)' : 'var(--ash)' }}>{row.value}</span>
            </div>
          ))}
        </div>

        {/* Calibration */}
        <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', padding: '20px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginBottom: '16px' }}>
            <span style={{ fontSize: '12px', fontWeight: 600, textTransform: 'uppercase' as const, letterSpacing: '0.06em', color: 'var(--ash)' }}>Calibration</span>
            <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>Model prob vs actual hit rate</span>
          </div>
          {Object.keys(buckets).length === 0 ? (
            <p className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>Available after more settled bets.</p>
          ) : (
            <>
              {Object.entries(buckets).sort().map(([bucket, data]) => {
                const hitRate = data.count > 0 ? data.wins / data.count : 0
                return (
                  <div key={bucket} style={{ marginBottom: '10px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                      <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>{bucket}</span>
                      <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>{(hitRate * 100).toFixed(0)}% ({data.wins}/{data.count})</span>
                    </div>
                    <div style={{ height: '6px', borderRadius: 'var(--radius-pill)', background: 'var(--slate)', overflow: 'hidden' }}>
                      <div style={{ height: '6px', borderRadius: 'var(--radius-pill)', background: 'var(--signal)', width: `${Math.round(hitRate * 100)}%` }} />
                    </div>
                  </div>
                )
              })}
              <p className="mono" style={{ fontSize: '10px', color: 'var(--fog)', marginTop: '10px' }}>
                Well-calibrated: bar width should match bucket label.
              </p>
            </>
          )}
        </div>
      </div>

      {/* Feature table */}
      <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', overflow: 'hidden', marginBottom: '28px' }}>
        <div style={{ padding: '14px 18px', borderBottom: B, background: 'var(--obsidian)' }}>
          <div style={{ fontSize: '12px', fontWeight: 600, textTransform: 'uppercase' as const, letterSpacing: '0.06em', color: 'var(--ash)' }}>Feature List</div>
          <div className="mono" style={{ fontSize: '10px', color: 'var(--fog)', marginTop: '3px' }}>{spec.features.length} features &middot; XGBoost importance ranking</div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr 100px', background: 'var(--obsidian)', borderBottom: B }}>
          {['Feature', 'Description', 'Importance'].map(h => (
            <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'var(--fog)', padding: '9px 14px' }}>{h}</div>
          ))}
        </div>
        {spec.features.map((f, i) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 2fr 100px', borderBottom: i < spec.features.length - 1 ? B : undefined, alignItems: 'center' }}>
            <div className="mono" style={{ fontSize: '11px', fontWeight: 600, color: 'var(--ash)', padding: '10px 14px' }}>{f.name}</div>
            <div style={{ fontSize: '11px', color: 'var(--silver)', padding: '10px 14px' }}>{f.description}</div>
            <div style={{ padding: '10px 14px' }}>
              <span className="mono" style={{
                fontSize: '9px', fontWeight: 600, padding: '3px 8px', borderRadius: 'var(--radius-pill)',
                color: IMPORTANCE[f.importance]?.color ?? 'var(--fog)',
                border: `1px solid ${IMPORTANCE[f.importance]?.border ?? 'var(--iron)'}`,
                background: IMPORTANCE[f.importance]?.bg ?? 'transparent',
              }}>{f.importance}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Gate progress */}
      <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', padding: '18px 20px', marginBottom: '28px', display: 'flex', alignItems: 'center', gap: '24px' }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
            <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'var(--fog)' }}>200-Bet Gate Progress</span>
            <span className="mono" style={{ fontSize: '9px', color: 'var(--fog)' }}>{bets}/200</span>
          </div>
          <div style={{ height: '8px', borderRadius: 'var(--radius-pill)', background: 'var(--slate)', overflow: 'hidden' }}>
            <div style={{ height: '8px', borderRadius: 'var(--radius-pill)', background: spec.color, width: `${gatePct}%` }} />
          </div>
        </div>
        <span className="mono" style={{
          fontSize: '9px', letterSpacing: '0.08em', padding: '5px 11px', borderRadius: 'var(--radius-pill)',
          border: bets >= 200 ? '1px solid var(--win-border)' : B,
          color: bets >= 200 ? 'var(--signal)' : 'var(--fog)',
          background: bets >= 200 ? 'var(--win-wash)' : 'var(--slate)',
        }}>
          {bets >= 200 ? 'Gate Cleared' : 'Paper Mode'}
        </span>
      </div>

      {/* P&L summary */}
      {totalPnl !== null && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', overflow: 'hidden', marginBottom: '28px' }}>
          <StatBlock label="Total P&L"   value={`${totalPnl >= 0 ? '+' : ''}${(totalPnl / 10).toFixed(2)}u`} accent={totalPnl > 0} />
          <StatBlock label="Avg Edge"    value={avgEdge !== null ? `+${avgEdge.toFixed(1)}%` : '--'} />
          <StatBlock label="Win Rate"    value={winRate !== null ? `${winRate.toFixed(1)}%` : '--'} />
          <div style={{ padding: '18px' }}>
            <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '6px' }}>Sample Size</div>
            <div className="mono" style={{ fontSize: '20px', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: 'var(--ash)' }}>{bets}</div>
            <div className="times" style={{ fontSize: '11px', color: 'var(--fog)' }}>Settled bets</div>
          </div>
        </div>
      )}

      {/* Full bet history */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
        <h2 className="dell-display" style={{ fontSize: '20px', color: 'var(--chalk)' }}>Full settled history</h2>
        <Link href={`/picks/mlb/${spec.slug}`} className="times" style={{ fontSize: '13px', color: 'var(--link)', textDecoration: 'none' }}>
          Today&apos;s Picks &rarr;
        </Link>
      </div>

      {history.length === 0 ? (
        <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', padding: '48px', textAlign: 'center' as const }}>
          <p className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>No settled bets yet.</p>
        </div>
      ) : (
        <PicksTable bets={history} />
      )}
    </div>
  )
}
