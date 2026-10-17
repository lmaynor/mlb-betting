import Link from 'next/link'
import { LIVE_ENGINE, SPORT_MODEL_SPECS, type ModelValidation, type PropModelSpec } from '@/lib/prop-model-specs'
import type { PickSystem } from '@/lib/pick-systems'
import { SPORT_PILLARS, type ProjectionSport } from '@/lib/sports'

const CARD = { background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', padding: '14px 16px', display: 'grid', gap: '12px', fontSize: '12.5px', color: 'var(--silver)', lineHeight: 1.55 } as const
const fmt = (x: number | null | undefined, d: number) => (x == null ? '--' : x.toFixed(d))
const pair = (a: number | null, b: number | null, d: number) => (a == null ? '--' : `${a.toFixed(d)} vs ${fmt(b, d)}`)

function Stat({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div title={title}>
      <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>{label}</div>
      <div className="mono" style={{ fontSize: '13px', color: 'var(--chalk)', marginTop: '2px' }}>{value}</div>
    </div>
  )
}

/**
 * The methodology card on a system page: what the system looks at, the distribution behind its number, how it validated
 * and what the studies concluded about prices. Every figure is copied from a study (lib/prop-model-specs.ts); a metric no
 * study produced prints "--". The record section says outright that projection systems are not tracked.
 */
export function SystemMethodology({ system }: { system: PickSystem }) {
  const sport = system.sport
  if (sport === 'mlb') {
    return (
      <div style={CARD}>
        <div>
          <div className="dell-heading" style={{ fontSize: '10px', color: SPORT_PILLARS.mlb.accent, marginBottom: '6px' }}>HOW IT WORKS</div>
          <div><strong style={{ color: 'var(--chalk)' }}>Model.</strong> {system.family}.</div>
        </div>
        <div>
          <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>KEY FEATURES</div>
          <ul style={{ margin: 0, paddingLeft: '18px' }}>{system.features.map(f => <li key={f}>{f}</li>)}</ul>
        </div>
        {system.learnSlug && <div><Link href={`/learn/${system.learnSlug}`} style={{ color: 'var(--link)' }}>Read the guide to this market &rarr;</Link></div>}
      </div>
    )
  }
  const spec = SPORT_MODEL_SPECS[sport as ProjectionSport]
  const model: PropModelSpec | undefined = spec.models.find(m => m.key === system.statKey)
  const v: ModelValidation | null = model?.validation ?? null
  const pillar = SPORT_PILLARS[sport]
  return (
    <div style={CARD}>
      {system.gap && (
        <div role="note" style={{ border: '1px solid rgba(245,158,11,0.35)', background: 'rgba(245,158,11,0.06)', borderRadius: '8px', padding: '10px 12px' }}>
          <strong style={{ color: '#f59e0b' }}>DATA GAP.</strong> {system.gap}
        </div>
      )}
      <div>
        <div className="dell-heading" style={{ fontSize: '10px', color: pillar.accent, marginBottom: '6px' }}>DISTRIBUTION</div>
        <div><strong style={{ color: 'var(--chalk)' }}>Live board.</strong> {system.family}.{system.gap ? '' : ` ${LIVE_ENGINE.summary}`}</div>
        {model && <div style={{ marginTop: '6px' }}><strong style={{ color: 'var(--chalk)' }}>Research model.</strong> {model.trained ? model.distribution : 'None trained for this stat.'}</div>}
      </div>
      <div>
        <div className="dell-heading" style={{ fontSize: '10px', color: pillar.accent, marginBottom: '6px' }}>WHAT THE LIVE BOARD LOOKS AT</div>
        <ul style={{ margin: 0, paddingLeft: '18px' }}>{system.features.map(f => <li key={f}>{f}</li>)}</ul>
      </div>
      <div>
        <div className="dell-heading" style={{ fontSize: '10px', color: pillar.accent, marginBottom: '8px' }}>VALIDATION</div>
        <div style={{ display: 'flex', gap: '10px 28px', flexWrap: 'wrap' }}>
          <Stat label="SAMPLE" value={v?.sample ?? '--'} />
          <Stat label="MAE (MODEL VS BASELINE)" value={v ? pair(v.mae, v.baselineMae, 3) : '--'} title="Lower is better; the baseline is the best trailing-average forecaster in the study" />
          <Stat label="CRPS (MODEL VS BASELINE)" value={v ? pair(v.crps, v.baselineCrps, 3) : '--'} />
          <Stat label="BRIER" value={fmt(v?.brier, 4)} />
          <Stat label="ECE" value={fmt(v?.ece, 4)} />
          <Stat label="BLEND c (t)" value={model?.blend ? `${model.blend.c >= 0 ? '+' : ''}${model.blend.c.toFixed(2)} (${model.blend.t >= 0 ? '+' : ''}${model.blend.t.toFixed(1)})` : '--'} title="c > 0 with a large t: the trained research model adds information beyond the market. It does not say the information is tradable." />
        </div>
        <div style={{ marginTop: '8px' }}><strong style={{ color: 'var(--chalk)' }}>Result.</strong> {model?.verdict ?? 'No study covers this stat.'}</div>
      </div>
      <div><strong style={{ color: 'var(--chalk)' }}>Against prices.</strong> {model?.trained ? spec.marketVerdict : 'No study has compared this stat with prices.'}</div>
      <div><strong style={{ color: 'var(--chalk)' }}>Record.</strong> No graded history is published for {pillar.shortName} projections: they are not logged to the public ledger, so there is no win rate or ROI to show.</div>
      <div style={{ color: 'var(--fog)', fontSize: '11.5px' }}>Source: {spec.sources} MAE and CRPS: lower is better. The live board does not run the research model.</div>
    </div>
  )
}

/** The sport-level evidence note under a projection sport's systems footer: what the studies concluded about prices. */
export function SportEvidenceNote({ sport }: { sport: ProjectionSport }) {
  const spec = SPORT_MODEL_SPECS[sport]
  const pillar = SPORT_PILLARS[sport]
  return (
    <div style={{ ...CARD, marginTop: '16px' }}>
      <div>
        <div className="dell-heading" style={{ fontSize: '10px', color: pillar.accent, marginBottom: '6px' }}>KEY FEATURES (RESEARCH MODEL)</div>
        <ul style={{ margin: 0, paddingLeft: '18px' }}>{spec.features.map(f => <li key={f}>{f}</li>)}</ul>
      </div>
      <div><strong style={{ color: 'var(--chalk)' }}>Against prices.</strong> {spec.marketVerdict}</div>
      <div><strong style={{ color: 'var(--chalk)' }}>What the daily board runs.</strong> {LIVE_ENGINE.summary}</div>
      <div style={{ color: 'var(--fog)', fontSize: '11.5px' }}>Source: {spec.sources} Baselines are the best trailing-average forecaster in each study.</div>
    </div>
  )
}
