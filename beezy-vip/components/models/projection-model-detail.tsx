import Link from 'next/link'
import { SystemBadge } from '@/components/ui/primitives'
import { slateRows } from '@/lib/board-days'
import { systemTodayCounts } from '@/lib/board-rows'
import { apiGetSportProjections } from '@/lib/betting-api'
import type { PickSystem } from '@/lib/pick-systems'
import { liveAccuracy, LIVE_ENGINE, SPORT_MODEL_SPECS, type PropModelSpec } from '@/lib/prop-model-specs'
import { SPORT_PILLARS, type ProjectionSport } from '@/lib/sports'
import { SYSTEM_COLOR } from '@/lib/tokens'

const B = '1px solid var(--color-separator-border)'
const fmt = (x: number | null | undefined, d: number) => (x == null ? '--' : x.toFixed(d))
const maeDigits = (x: number) => (x >= 10 ? 1 : 3)

function StatBlock({ label, value, sub, accent, last }: { label: string; value: string; sub?: string; accent?: boolean; last?: boolean }) {
  return (
    <div style={{ padding: '18px', borderRight: last ? undefined : B }}>
      <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'var(--color-text-secondary)', marginBottom: '6px' }}>{label}</div>
      <div className="mono" style={{ fontSize: '20px', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: accent ? 'var(--signal)' : 'var(--color-text-primary)' }}>{value}</div>
      {sub && <div className="times text-mini text-text-secondary">{sub}</div>}
    </div>
  )
}

/** Two bars on one scale (lower is better): the model against the baseline it was tested against. */
function VersusBar({ label, model, baseline, color }: { label: string; model: number; baseline: number; color: string }) {
  const max = Math.max(model, baseline) || 1
  const row = (name: string, v: number, c: string) => (
    <div style={{ display: 'grid', gridTemplateColumns: '64px 1fr 56px', alignItems: 'center', gap: '8px', marginBottom: '5px' }}>
      <span className="mono text-micro text-text-secondary">{name}</span>
      <div style={{ height: '6px', borderRadius: 'var(--radius-pill)', background: 'var(--slate)', overflow: 'hidden' }}>
        <div style={{ height: '6px', borderRadius: 'var(--radius-pill)', background: c, width: `${Math.round((v / max) * 100)}%` }} />
      </div>
      <span className="mono text-micro text-neutral-300 text-right">{v.toFixed(v >= 10 ? 1 : 3)}</span>
    </div>
  )
  return (
    <div className="mb-3.5">
      <div className="mono text-micro text-text-primary mb-1.5">{label}</div>
      {row('model', model, color)}
      {row('baseline', baseline, 'var(--color-border-button-default)')}
    </div>
  )
}

/**
 * The model page for a projection system (NFL / NHL / NBA), laid out like the MLB model page: header, headline numbers,
 * configuration, evidence, feature list, status and where to find today's rows. Every number comes from a study or from the
 * live-accuracy file (lib/prop-model-specs.ts); a figure no study produced prints "--". Unlike MLB there is no settled bet
 * history: these systems are not logged to the public ledger, and the page says so.
 */
export async function ProjectionModelDetail({ system }: { system: PickSystem }) {
  const sport = system.sport as ProjectionSport
  const pillar = SPORT_PILLARS[sport]
  const spec = SPORT_MODEL_SPECS[sport]
  const model: PropModelSpec | undefined = spec.models.find(m => m.key === system.statKey)
  const v = model?.validation ?? null
  const live = liveAccuracy(sport, system.statKey)
  const color = SYSTEM_COLOR[system.key] ?? pillar.accent
  const today = await apiGetSportProjections(sport, { limit: 1200 }).then(p => systemTodayCounts(slateRows(p.rows))[system.key] ?? null).catch(() => null)
  const gap = Boolean(system.gap)

  return (
    <div className="max-w-page mx-auto py-10 px-6">
      <div className="flex items-center gap-2 mb-6 flex-wrap">
        <Link href="/models" className="times" style={{ fontSize: '13px', color: 'var(--link)', textDecoration: 'none' }}>Models</Link>
        <span className="mono text-mini text-text-secondary">&middot;</span>
        <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: pillar.accent }}>{pillar.shortName}</span>
        <span className="mono text-mini text-text-secondary">&middot;</span>
        <SystemBadge system={system.key} />
      </div>

      <div className="mb-7">
        <div className="flex items-center gap-3 mb-2.5 flex-wrap">
          <h1 className="ui-display text-[30px] text-text-primary">{pillar.shortName} {system.name}</h1>
          <span className="mono" style={{ fontSize: '11px', color: 'var(--color-neutral-300)', border: B, borderRadius: 'var(--radius-pill)', padding: '3px 10px' }}>empirical_ewm_v2</span>
          <span className="ui-heading" style={{ fontSize: '10px', color, border: `1px solid ${color}66`, borderRadius: '6px', padding: '2px 8px' }}>{gap ? 'DATA GAP' : 'UNVALIDATED'}</span>
        </div>
        <p className="text-cozy text-neutral-300 leading-[1.65] max-w-[640px] mb-2.5">{system.description}</p>
        <Link href={`/picks/${sport}/${system.slug}`} className="times" style={{ fontSize: '13px', color: 'var(--link)', textDecoration: 'none' }}>
          Today&apos;s {system.name.toLowerCase()} board &rarr;
        </Link>
      </div>

      {gap && (
        <div role="note" className="border border-[color:rgba(245,158,11,0.35)] bg-[rgba(245,158,11,0.06)] rounded-[var(--radius-lg)] py-3.5 px-4.5 mb-7 text-cozy text-neutral-300 leading-[1.6]">
          <strong className="text-gold">DATA GAP.</strong> {system.gap}
        </div>
      )}

      {!gap && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', border: B, borderRadius: 'var(--radius-lg)', background: 'var(--color-background-primary-default)', overflow: 'hidden', marginBottom: '28px' }}>
          <StatBlock label="Live MAE" value={live ? live.mae.toFixed(maeDigits(live.mae)) : '--'} sub={live ? `last-5 average ${live.baselineMae.toFixed(maeDigits(live.baselineMae))}` : 'no live accuracy file'} />
          <StatBlock label="Vs last-5 average" value={live ? `${live.dMae <= 0 ? '' : '+'}${((live.dMae / live.baselineMae) * 100).toFixed(1)}%` : '--'} accent={live != null && live.dMae < 0} sub={live ? `se ${((live.dMaeSe / live.baselineMae) * 100).toFixed(1)}%, lower is better` : undefined} />
          <StatBlock label="Player-games" value={live ? live.n.toLocaleString('en-US') : '--'} sub="live accuracy sample" />
          <StatBlock label="Rows today" value={today ? String(today.rows) : '--'} sub={today ? `${today.priced} priced, ${today.confluence} model + market` : 'board unavailable'} />
          <StatBlock label="Research model" value={model?.trained ? 'Trained' : 'None'} sub={model?.blend ? `blend c ${model.blend.c >= 0 ? '+' : ''}${model.blend.c.toFixed(2)} (t ${model.blend.t.toFixed(1)})` : model?.trained ? 'not yet validated' : 'live engine only'} last />
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 mb-7">
        <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--color-background-primary-default)', padding: '20px' }}>
          <div className="ui-display text-[16px] text-text-primary mb-4">Model configuration</div>
          {[
            { label: 'Live engine', value: gap ? 'Not projected' : LIVE_ENGINE.name },
            { label: 'Live distribution', value: system.family },
            { label: 'Research model', value: model ? (model.trained ? model.distribution : 'None trained') : 'No study' },
            { label: 'Usage driver', value: system.features[1]?.replace(/^Usage-adjusted rate \((.*)\)$/, '$1') ?? '--' },
            { label: 'Validation', value: v?.sample ?? 'Live accuracy on past games, earlier games only' },
            { label: 'Public record', value: 'Not tracked in the results' },
          ].map((row, i, arr) => (
            <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', padding: '9px 0', borderBottom: i < arr.length - 1 ? B : undefined }}>
              <span className="mono text-mini text-text-secondary shrink-0">{row.label}</span>
              <span className="mono text-mini font-semibold text-text-primary text-right">{row.value}</span>
            </div>
          ))}
        </div>

        <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--color-background-primary-default)', padding: '20px' }}>
          <div className="flex items-baseline gap-2 mb-4">
            <span style={{ fontSize: '12px', fontWeight: 600, textTransform: 'uppercase' as const, letterSpacing: '0.06em', color: 'var(--color-text-primary)' }}>Accuracy</span>
            <span className="mono text-micro text-text-secondary">model vs baseline, shorter bar is better</span>
          </div>
          {live && <VersusBar label="Live engine MAE vs last-5 average" model={live.mae} baseline={live.baselineMae} color={color} />}
          {v && v.mae != null && v.baselineMae != null && <VersusBar label="Research model MAE vs trailing-mean baseline" model={v.mae} baseline={v.baselineMae} color={color} />}
          {v && v.crps != null && v.baselineCrps != null && <VersusBar label="Research model CRPS vs baseline" model={v.crps} baseline={v.baselineCrps} color={color} />}
          {!live && !(v && v.mae != null) && <p className="mono text-mini text-text-secondary">{gap ? 'Nothing to measure: the stat is not projected.' : 'No accuracy study covers this stat yet.'}</p>}
          {v && (v.brier != null || v.ece != null) && (
            <p className="mono text-micro text-text-secondary mt-1">Research model calibration: Brier {fmt(v.brier, 4)}, ECE {fmt(v.ece, 4)}.</p>
          )}
          <p className="mono text-micro text-text-secondary mt-2.5 leading-[1.55]">
            {live ? `${live.method}. ` : ''}Accuracy of the projection only; it says nothing about beating a price.
          </p>
        </div>
      </div>

      <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--color-background-primary-default)', overflow: 'hidden', marginBottom: '28px' }}>
        <div style={{ padding: '14px 18px', borderBottom: B, background: 'var(--color-background-secondary-default)' }}>
          <div style={{ fontSize: '12px', fontWeight: 600, textTransform: 'uppercase' as const, letterSpacing: '0.06em', color: 'var(--color-text-primary)' }}>Feature List</div>
          <div className="mono text-micro text-text-secondary mt-[3px]">{system.features.length} live-board inputs &middot; {spec.features.length} research-model inputs</div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', background: 'var(--color-background-secondary-default)', borderBottom: B }}>
          {['Used by', 'Feature'].map(h => <div key={h} className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'var(--color-text-secondary)', padding: '9px 14px' }}>{h}</div>)}
        </div>
        {[...system.features.map(f => ({ f, used: 'Live board' })), ...(gap ? [] : spec.features.map(f => ({ f, used: 'Research model' })))].map(({ f, used }, i, arr) => (
          <div key={`${used}-${f}`} style={{ display: 'grid', gridTemplateColumns: '120px 1fr', borderBottom: i < arr.length - 1 ? B : undefined, alignItems: 'center' }}>
            <div className="py-2.5 px-3.5">
              <span className="mono" style={{ fontSize: '10px', fontWeight: 600, padding: '3px 8px', borderRadius: 'var(--radius-pill)', color: used === 'Live board' ? 'var(--signal)' : 'var(--color-text-secondary)', border: `1px solid ${used === 'Live board' ? 'var(--win-border)' : 'var(--color-border-button-default)'}`, background: used === 'Live board' ? 'var(--win-wash)' : 'transparent' }}>{used}</span>
            </div>
            <div className="text-[11.5px] text-neutral-300 py-2.5 px-3.5">{f}</div>
          </div>
        ))}
      </div>

      <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--color-background-primary-default)', padding: '18px 20px', marginBottom: '28px', display: 'grid', gap: '10px', fontSize: '12.5px', color: 'var(--color-neutral-300)', lineHeight: 1.6 }}>
        <div><strong className="text-text-primary">What the live board runs.</strong> {gap ? 'Nothing: this stat is not projected.' : LIVE_ENGINE.summary}</div>
        {model && <div><strong className="text-text-primary">Research model result.</strong> {model.verdict}</div>}
        <div><strong className="text-text-primary">Against prices.</strong> {model?.trained ? spec.marketVerdict : 'No study has compared this stat with prices.'}</div>
        <div><strong className="text-text-primary">Record.</strong> No graded history is published for {pillar.shortName} projections: they are not logged to the public ledger, so there is no win rate or ROI to show. Paper mode.</div>
        <div className="text-text-secondary text-[11.5px]">Source: {spec.sources} Baselines are the best trailing-average forecaster in each study. The live board does not run the research model.</div>
      </div>

      <div className="flex justify-between items-center flex-wrap gap-2">
        <Link href={`/picks/${sport}/systems`} className="times" style={{ fontSize: '13px', color: 'var(--link)', textDecoration: 'none' }}>&larr; All {pillar.shortName} systems</Link>
        <Link href={`/edge/projections?league=${sport}`} className="times" style={{ fontSize: '13px', color: 'var(--link)', textDecoration: 'none' }}>Every {pillar.shortName} projection in Prop Research &rarr;</Link>
      </div>
    </div>
  )
}
