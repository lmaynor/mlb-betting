import Link from 'next/link'
import { liveAccuracy, LIVE_ENGINE, SPORT_MODEL_SPECS, type ModelValidation, type PropModelSpec } from '@/lib/prop-model-specs'
import type { PickSystem } from '@/lib/pick-systems'
import { SPORT_PILLARS, type ProjectionSport } from '@/lib/sports'

const CARD = { background: 'var(--color-background-primary-default)', border: '1px solid var(--color-separator-border)', borderRadius: '8px', padding: '14px 16px', display: 'grid', gap: '12px', fontSize: '13px', color: 'var(--color-neutral-300)', lineHeight: 1.55 } as const
const fmt = (x: number | null | undefined, d: number) => (x == null ? '--' : x.toFixed(d))
const pair = (a: number | null, b: number | null, d: number) => (a == null ? '--' : `${a.toFixed(d)} vs ${fmt(b, d)}`)

function Stat({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div title={title}>
      <div className="ui-heading text-text-secondary">{label}</div>
      <div className="mono text-cozy text-text-primary mt-0.5">{value}</div>
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
          <div className="ui-heading text-micro mb-[6px]" style={{ color: SPORT_PILLARS.mlb.accent }}>How it works</div>
          <div><strong className="text-text-primary">Model.</strong> {system.family}.</div>
        </div>
        <div>
          <div className="ui-heading text-text-secondary mb-1.5">Key features</div>
          <ul className="m-0 pl-4.5">{system.features.map(f => <li key={f}>{f}</li>)}</ul>
        </div>
        {system.learnSlug && <div><Link className="text-link" href={`/learn/${system.learnSlug}`}>Read the guide to this market &rarr;</Link></div>}
      </div>
    )
  }
  const spec = SPORT_MODEL_SPECS[sport as ProjectionSport]
  const model: PropModelSpec | undefined = spec.models.find(m => m.key === system.statKey)
  const v: ModelValidation | null = model?.validation ?? null
  const pillar = SPORT_PILLARS[sport]
  const live = liveAccuracy(sport, system.statKey)
  return (
    <div style={CARD}>
      {system.gap && (
        <div role="note" className="border border-[color:rgba(245,158,11,0.35)] bg-[rgba(245,158,11,0.06)] rounded-control py-2.5 px-3">
          <strong className="text-gold">Data gap.</strong> {system.gap}
        </div>
      )}
      <div>
        <div className="ui-heading text-micro mb-[6px]" style={{ color: pillar.accent }}>Distribution</div>
        <div><strong className="text-text-primary">Live board.</strong> {system.family}.{system.gap ? '' : ` ${LIVE_ENGINE.summary}`}</div>
        {model && <div className="mt-1.5"><strong className="text-text-primary">Research model.</strong> {model.trained ? model.distribution : 'None trained for this stat.'}</div>}
      </div>
      <div>
        <div className="ui-heading text-micro mb-[6px]" style={{ color: pillar.accent }}>What the live board looks at</div>
        <ul className="m-0 pl-4.5">{system.features.map(f => <li key={f}>{f}</li>)}</ul>
      </div>
      {live && (
        <div>
          <div className="ui-heading text-micro mb-2" style={{ color: pillar.accent }}>Live board accuracy</div>
          <div className="flex gap-y-2.5 gap-x-7 flex-wrap">
            <Stat label="Sample" value={live.sample} />
            <Stat label="PLAYER-GAMES" value={live.n.toLocaleString('en-US')} />
            <Stat label="MAE (ENGINE VS L5)" value={pair(live.mae, live.baselineMae, live.mae >= 10 ? 1 : 3)} title="Mean absolute error of the projection on past games, using only earlier games; L5 is the player's average over the last five. Lower is better." />
            <Stat label="MAE change" value={`${live.dMae <= 0 ? '' : '+'}${((live.dMae / live.baselineMae) * 100).toFixed(1)}% (se ${((live.dMaeSe / live.baselineMae) * 100).toFixed(1)}%)`} title="Paired over the same games, as a share of the L5 error; negative means the engine is more accurate." />
          </div>
          <div className="mt-2 text-text-secondary text-[12px]">{live.method}. Accuracy of the projection only; it says nothing about beating a price.</div>
        </div>
      )}
      <div>
        <div className="ui-heading text-micro mb-2" style={{ color: pillar.accent }}>Research model validation</div>
        <div className="flex gap-y-2.5 gap-x-7 flex-wrap">
          <Stat label="Sample" value={v?.sample ?? '--'} />
          <Stat label="MAE (MODEL VS BASELINE)" value={v ? pair(v.mae, v.baselineMae, 3) : '--'} title="Lower is better; the baseline is the best trailing-average forecaster in the study" />
          <Stat label="CRPS (MODEL VS BASELINE)" value={v ? pair(v.crps, v.baselineCrps, 3) : '--'} />
          <Stat label="Brier" value={fmt(v?.brier, 4)} />
          <Stat label="ECE" value={fmt(v?.ece, 4)} />
          <Stat label="BLEND c (t)" value={model?.blend ? `${model.blend.c >= 0 ? '+' : ''}${model.blend.c.toFixed(2)} (${model.blend.t >= 0 ? '+' : ''}${model.blend.t.toFixed(1)})` : '--'} title="c > 0 with a large t: the trained research model adds information beyond the market. It does not say the information is tradable." />
        </div>
        <div className="mt-2"><strong className="text-text-primary">Result.</strong> {model?.verdict ?? 'No study covers this stat.'}</div>
      </div>
      <div><strong className="text-text-primary">Against prices.</strong> {model?.trained ? spec.marketVerdict : 'No study has compared this stat with prices.'}</div>
      <div><strong className="text-text-primary">Record.</strong> No graded history is published for {pillar.shortName} projections: they are not logged to the public ledger, so there is no win rate or ROI to show.</div>
      <div className="text-text-secondary text-[12px]">Source: {spec.sources} MAE and CRPS: lower is better. The live board does not run the research model.</div>
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
        <div className="ui-heading text-micro mb-[6px]" style={{ color: pillar.accent }}>KEY FEATURES (RESEARCH MODEL)</div>
        <ul className="m-0 pl-4.5">{spec.features.map(f => <li key={f}>{f}</li>)}</ul>
      </div>
      <div><strong className="text-text-primary">Against prices.</strong> {spec.marketVerdict}</div>
      <div><strong className="text-text-primary">What the daily board runs.</strong> {LIVE_ENGINE.summary}</div>
      <div className="text-text-secondary text-[12px]">Source: {spec.sources} Baselines are the best trailing-average forecaster in each study.</div>
    </div>
  )
}
