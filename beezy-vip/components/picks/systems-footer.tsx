import Link from 'next/link'
import { getSystemsBySport, kindOfSystem, PICK_SYSTEMS, systemPill, type PickKind, type PickSystem } from '@/lib/pick-systems'
import { liveAccuracy, SPORT_MODEL_SPECS, type PropModelSpec } from '@/lib/prop-model-specs'
import type { SystemToday } from '@/lib/board-rows'
import { SPORT_PILLARS, type ProjectionSport, type SportSlug } from '@/lib/sports'
import type { SystemStats } from '@/lib/types'

const TH = { textAlign: 'left', padding: '9px 12px', fontSize: '10px', fontWeight: 600, letterSpacing: '0.06em', color: 'var(--fog)', background: '#11131a', borderBottom: '1px solid #1e2230', whiteSpace: 'nowrap', position: 'sticky', top: 0 } as const
const TD = { padding: '10px 12px', borderBottom: '1px solid #151822', whiteSpace: 'nowrap' } as const
const R = { textAlign: 'right' } as const

const num = (v: unknown) => { const n = parseFloat(String(v ?? '')); return Number.isFinite(n) ? n : null }
const fix = (x: number | null | undefined, d: number) => (x == null ? '--' : x.toFixed(d))
/** MAE spans 0.3 (goals) to 66 (pass yards): keep three significant-ish digits at either scale. */
const maeText = (x: number) => x.toFixed(x >= 10 ? 1 : x >= 1 ? 2 : 3)

/** Out-of-sample numbers a study produced for this system's stat, or null (rendered "--": nothing is filled in). */
function specFor(sport: SportSlug, s: PickSystem): PropModelSpec | null {
  if (sport === 'mlb' || !s.statKey) return null
  return SPORT_MODEL_SPECS[sport as ProjectionSport].models.find(m => m.key === s.statKey) ?? null
}

/**
 * The systems behind a sport page, as its footer: the same table for every sport, one row per registered system
 * (lib/pick-systems.ts), each linking to /picks/[sport]/[system]. Tracked sports (MLB) show the settled record; projection
 * sports show the out-of-sample accuracy their studies produced. `kind` narrows to player or game systems (MLB's two cards).
 */
export function SystemsFooter({ sport, kind, stats = [], today = {} }: { sport: SportSlug; kind?: PickKind; stats?: SystemStats[]; today?: Record<string, SystemToday> }) {
  const pillar = SPORT_PILLARS[sport]
  const tracked = pillar.board === 'tracked'
  const status = new Map(pillar.markets.map(m => [m.system ?? m.key, m.status]))
  const listed = getSystemsBySport(sport)
  const shelvedWithBets = tracked ? PICK_SYSTEMS.filter(s => s.sport === sport && s.shelved && (num(stats.find(x => x.system === s.key)?.total_bets) ?? 0) > 0) : []
  const rows = [...listed, ...shelvedWithBets].filter(s => !kind || kindOfSystem(s.key) === kind)
  const heading = kind ? (kind === 'player' ? 'Player systems' : 'Game systems') : `${pillar.shortName} systems`

  return (
    <section id="systems" aria-labelledby="systems-h" style={{ marginTop: '36px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <h2 id="systems-h" style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>{heading}</h2>
        <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>
          {rows.length} systems &middot; {tracked ? 'best available book \u00b7 paper mode' : 'unvalidated projections \u00b7 not in the public results'}
        </span>
      </div>
      <div style={{ overflowX: 'auto', border: '1px solid #1e2230', borderRadius: '8px', background: '#11131a' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: tracked ? '1040px' : '1240px' }}>
          <thead>
            <tr>
              <th style={TH}>SYSTEM</th>
              <th style={TH}>MARKET</th>
              <th style={TH}>TYPE</th>
              <th style={TH}>DISTRIBUTION</th>
              <th style={TH}>STATUS</th>
              {tracked ? (
                <>
                  <th style={{ ...TH, ...R }}>BETS</th>
                  <th style={{ ...TH, ...R }}>HIT %</th>
                  <th style={{ ...TH, ...R }}>ROI</th>
                  <th style={{ ...TH, ...R }}>SAMPLE</th>
                </>
              ) : (
                <>
                  <th style={{ ...TH, ...R }} title="Projections on the board right now, and how many of them have a posted line">TODAY (ROWS / PRICED)</th>
                  <th style={{ ...TH, ...R }} title="Mean absolute error of the mean the live board uses on past games, against the trailing-5 average (lower is better). Accuracy of the projection, not edge against a price.">LIVE MAE (ENGINE / L5)</th>
                  <th style={{ ...TH, ...R }} title="Out-of-sample mean absolute error of the trained research model, and of the trailing-average baseline">RESEARCH MAE (MODEL / BASE)</th>
                  <th style={{ ...TH, ...R }} title="Brier score against proxy or real lines in the study">BRIER</th>
                  <th style={{ ...TH, ...R }} title="Blend coefficient c vs the market and its t-statistic: > 0 means the research model adds information">BLEND c (t)</th>
                </>
              )}
              <th style={TH}></th>
            </tr>
          </thead>
          <tbody>
            {rows.map(s => {
              const stat = stats.find(x => x.system === s.key)
              const bets = num(stat?.total_bets) ?? 0
              const roi = num(stat?.roi)
              const wr = num(stat?.win_rate)
              const pill = systemPill(s.key)
              const spec = specFor(sport, s)
              // a feed was loaded (today has entries) but this system has no row in it: that is 0 rows, not unknown
              const t = today[s.key] ?? (Object.keys(today).length > 0 ? { rows: 0, priced: 0, confluence: 0 } : undefined)
              const live = sport === 'mlb' ? null : liveAccuracy(sport, s.statKey)
              const v = spec?.validation
              const href = `/picks/${sport}/${s.slug}`
              return (
                <tr key={s.key} className="sys-row">
                  <td style={TD}>
                    <Link href={href} style={{ textDecoration: 'none' }}>
                      <span className="dell-heading" style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.05em', padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border }}>{s.shortName}</span>
                    </Link>
                  </td>
                  <td style={{ ...TD, fontSize: '13.5px', fontWeight: 600 }}>
                    <Link href={href} style={{ color: 'var(--chalk)', textDecoration: 'none' }}>{s.name}</Link>
                  </td>
                  <td style={{ ...TD, fontSize: '12px', color: 'var(--silver)' }}>{s.market === 'game' ? 'Game' : 'Prop'}</td>
                  <td style={{ ...TD, fontSize: '12px', color: 'var(--silver)' }}>{s.family}</td>
                  <td style={{ ...TD, fontSize: '12px', color: s.gap ? '#f59e0b' : 'var(--silver)' }}>{status.get(s.key) ?? '--'}</td>
                  {tracked ? (
                    <>
                      <td className="mono" style={{ ...TD, ...R, fontSize: '13px', color: 'var(--chalk)' }}>{bets}</td>
                      <td className="mono" style={{ ...TD, ...R, fontSize: '13px', color: 'var(--chalk)' }}>{wr !== null ? `${wr.toFixed(1)}%` : '--'}</td>
                      <td className="mono" style={{ ...TD, ...R, fontSize: '13px', fontWeight: 600, color: roi == null ? 'var(--fog)' : roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>
                        {roi == null ? '--' : `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%`}
                      </td>
                      <td className="mono" style={{ ...TD, ...R, fontSize: '12px', color: 'var(--fog)' }}>{bets >= 200 ? '200+ bets' : `${bets}/200 bets`}</td>
                    </>
                  ) : (
                    <>
                      <td className="mono" style={{ ...TD, ...R, fontSize: '12.5px', color: 'var(--silver)' }}>
                        {t ? <span>{t.rows} <span style={{ color: 'var(--fog)' }}>/</span> <span style={{ color: t.priced > 0 ? 'var(--chalk)' : 'var(--fog)' }}>{t.priced}</span></span> : '--'}
                      </td>
                      <td className="mono" title={live ? `${live.sample}; ${live.n.toLocaleString('en-US')} player-games. ${live.method}. Engine minus L5 MAE ${live.dMae.toFixed(3)} (se ${live.dMaeSe.toFixed(3)}).` : 'No replay covers this stat yet.'}
                        style={{ ...TD, ...R, fontSize: '12.5px', color: live && live.dMae < 0 ? 'var(--signal)' : 'var(--silver)' }}>
                        {live ? `${maeText(live.mae)} / ${maeText(live.baselineMae)}` : '--'}
                      </td>
                      <td className="mono" style={{ ...TD, ...R, fontSize: '12.5px', color: 'var(--silver)' }}>{v?.mae != null ? `${fix(v.mae, 3)} / ${fix(v.baselineMae, 3)}` : '--'}</td>
                      <td className="mono" style={{ ...TD, ...R, fontSize: '12.5px', color: 'var(--silver)' }}>{fix(v?.brier, 4)}</td>
                      <td className="mono" style={{ ...TD, ...R, fontSize: '12.5px', color: 'var(--silver)' }}>
                        {spec?.blend ? `${spec.blend.c >= 0 ? '+' : ''}${spec.blend.c.toFixed(2)} (${spec.blend.t >= 0 ? '+' : ''}${spec.blend.t.toFixed(1)})` : '--'}
                      </td>
                    </>
                  )}
                  <td style={{ ...TD, fontSize: '12px' }}>
                    <Link href={href} style={{ color: 'var(--link)', textDecoration: 'none' }}>View System -&gt;</Link>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: '11.5px', color: 'var(--fog)', margin: '10px 2px 0', lineHeight: 1.55 }}>
        {tracked ? (
          <>Log-only systems record every pick but are not counted as a live card until they clear 200 settled bets. Every result is public on the <Link href="/results" style={{ color: 'var(--link)' }}>Results</Link> ledger.</>
        ) : (
          <>The board runs an empirical estimator. LIVE MAE is that estimator&rsquo;s accuracy on past games against the trailing-5 average; RESEARCH MAE, Brier and blend c belong to the trained research models, which the board does not run. No projection is tracked in the public results yet, and none of these figures is edge against a price. <span className="mono">--</span> means no study produced a number.</>
        )}
      </p>
    </section>
  )
}
