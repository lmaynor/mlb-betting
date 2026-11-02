export const dynamic = 'force-dynamic'

import { apiGetStats } from '@/lib/betting-api'
import Link from 'next/link'
import type { Metadata } from 'next'
import { getModelSpec } from '@/lib/model-specs'
import { getSystemsBySport } from '@/lib/pick-systems'
import { liveAccuracy } from '@/lib/prop-model-specs'
import { SPORT_PILLARS, STATUS_LABEL, type ProjectionSport, type SportSlug } from '@/lib/sports'
import { SYSTEM_PILL } from '@/lib/tokens'
import { asOfLabel } from '@/lib/seo'
import { cx } from '@/utils/cx'

export const metadata: Metadata = {
  title: 'Models: Methodology & Transparency',
  description: 'How beezy.fyi builds its models in MLB, NFL, NHL and NBA: validation, accuracy, features and what each system has and has not proven.',
}

const CARD = 'rounded-xl border border-separator-border bg-background-primary-default'
const LBL = 'dell-heading tracking-[0.1em] text-text-secondary'

const PILL = SYSTEM_PILL

const ACTIVE_MODELS = [
  { system: 'NRFI', version: 'v17', desc: 'Predicts whether the first inning will be scoreless using starter quality, umpire zone, park, and weather context.', range: '2021-2025', metric: '0.577', metricLabel: 'OOS AUC', href: '/models/nrfi' },
  { system: 'HR', version: 'v6', desc: 'Prices batter HR probability with barrel rate, launch angle, pitcher HR risk, park factor, and platoon split.', range: '2021-2025', metric: '0.630', metricLabel: 'OOS AUC', href: '/models/hr' },
  { system: 'F5', version: 'v5', desc: 'Models first-five moneyline outcomes from starter SIERA, recent form, opponent wOBA, umpire, and weather.', range: '2022-2025', metric: '0.553', metricLabel: 'OOS AUC', href: '/models/f5' },
  { system: 'K', version: 'v1', desc: 'Projects starter strikeout totals with swinging strikes, zone/chase rates, opponent K%, and recent K workload.', range: '2021-2025', metric: '1.807', metricLabel: 'OOS MAE', href: '/models/k' },
  { system: 'OUTS', version: 'v1', desc: 'Projects starter outs recorded with a negative-binomial count model from pitch efficiency, innings trends, bullpen context, and opponent OBP.', range: '2021-2025', metric: 'NegBin', metricLabel: 'Count model', href: '/models/outs' },
]

const PIPELINE_MODELS = [
  { system: 'GAME', label: 'Full Game', note: 'Full-game moneyline from starter, bullpen fatigue and lineup features. Log-only until it reaches 200 bets.' },
  { system: 'F3', label: 'First 3', note: 'Early-game starter window for openers, short leashes, and lineup-top exposure.' },
  { system: 'F1H', label: 'First Half', note: 'First-half (innings 1-4) moneyline built from the F5 model. Log-only.' },
  { system: 'F7', label: 'First 7', note: 'Late starter and bridge-relief pricing before full bullpen exposure.' },
  { system: 'BATTER_K', label: 'Batter K', note: 'Batter strikeout props using pitcher shape, zone, chase, and batter whiff profile.' },
  { system: 'BATTER_TB', label: 'Total Bases', note: 'Batter total-base props with contact quality, matchup, lineup slot, and park.' },
  { system: 'BATTER_HITS', label: 'Hits', note: 'Hit props using contact rate, expected average, platoon split, and park/run context.' },
  { system: 'PITCHER_ER', label: 'Pitcher ER', note: 'Earned-runs props using starter quality, opponent run creation, weather, and leash.' },
  { system: 'SB', label: 'Stolen Bases', note: 'Stolen-base props using on-base rate, sprint speed, and attempt history vs. opposing pitcher hold and catcher pop time.' },
]

/** One sport's expandable block: a header row (sport, system count, status) that opens onto its models. MLB opens by default. */
const SECTION_CSS = '.sport-summary::-webkit-details-marker{display:none}details[open]>.sport-summary .sport-chev{transform:rotate(90deg)}'

function SportSection({ sport, open, children }: { sport: SportSlug; open: boolean; children: React.ReactNode }) {
  const pillar = SPORT_PILLARS[sport]
  const n = getSystemsBySport(sport).length
  return (
    <details id={sport} open={open} className="mb-3.5 scroll-mt-[140px] rounded-xl border border-separator-border bg-background-full">
      <summary className="sport-summary flex cursor-pointer list-none flex-wrap items-baseline gap-x-3.5 gap-y-2.5 px-[18px] py-3.5">
        <span className="sport-chev inline-block text-[11px] text-text-secondary transition-transform duration-[120ms]" aria-hidden="true">&#9656;</span>
        <span className="text-headline-bold" style={{ color: pillar.accent }}>{pillar.shortName}</span>
        <span className="mono text-caption-1-regular text-text-secondary">{n} systems</span>
        <span className="dell-heading rounded-md border px-2 py-0.5" style={{ color: pillar.accent, borderColor: `${pillar.accent}66` }}>{STATUS_LABEL[pillar.status]}</span>
        <span className="min-w-0 flex-[1_1_320px] text-caption-1-regular text-text-secondary">{pillar.name}</span>
      </summary>
      <div className="px-[18px] pb-[18px] pt-1">{children}</div>
      <style>{SECTION_CSS}</style>
    </details>
  )
}

const TH = 'mono whitespace-nowrap border-b border-separator-border bg-background-secondary-default px-3 py-[9px] text-left text-[10px] uppercase tracking-[0.1em] text-text-secondary'
const TD = 'border-b border-border-table p-3 align-top'

/** NFL / NHL / NBA: every system with its engine and live accuracy against the last-5 average, linking to its model page. */
function ProjectionSportTable({ sport }: { sport: ProjectionSport }) {
  const systems = getSystemsBySport(sport)
  return (
    <>
      <p className="mb-3.5 mt-2 max-w-[90ch] text-caption-1-regular text-text-secondary">{SPORT_PILLARS[sport].description}</p>
      <div className={cx(CARD, 'overflow-x-auto')}>
        <table className="w-full min-w-[760px] border-collapse">
          <thead>
            <tr>{['System', 'What it prices', 'Engine', 'Live MAE vs last 5', 'Detail'].map(h => <th key={h} className={TH}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {systems.map(sys => {
              const pill = PILL[sys.key] ?? PILL.ALL
              const live = liveAccuracy(sport, sys.statKey)
              return (
                <tr key={sys.key} className="transition-colors hover:bg-background-primary-hover">
                  <td className={TD}>
                    <span className="mono inline-block whitespace-nowrap rounded-full px-2 py-[3px] text-[10px] font-bold" style={{ background: pill.bg, color: pill.color, border: pill.border }}>{sys.shortName}</span>
                  </td>
                  <td className={cx(TD, 'text-caption-1-regular text-text-secondary')}>
                    <div className="mb-0.5 font-semibold text-text-primary">{sys.name}</div>
                    {sys.gap ? <span className="text-[#f59e0b]">Data gap: not projected yet.</span> : sys.description}
                  </td>
                  <td className={cx('mono text-[11px] text-text-secondary', TD)}>{sys.family}</td>
                  <td className={cx('mono whitespace-nowrap text-[11px]', TD)} style={{ color: live && live.dMae < 0 ? 'var(--signal)' : undefined }}>
                    {live ? `${live.mae.toFixed(live.mae >= 10 ? 1 : 3)} vs ${live.baselineMae.toFixed(live.baselineMae >= 10 ? 1 : 3)}` : '--'}
                  </td>
                  <td className={TD}><Link href={`/models/${sport}/${sys.slug}`} className="times whitespace-nowrap text-body-2-semibold text-[var(--link)] no-underline hover:underline">Detail &rarr;</Link></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

export default async function ModelsPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams
  const stats = await apiGetStats().then(s => s.bySystem).catch(() => [])

  return (
    <div className="mx-auto max-w-[1200px] px-6 py-10">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-title-2-bold text-text-primary">Models</h1>
        <Link href="/results" className="text-body-2-semibold text-[var(--link)] no-underline hover:underline">View results &rarr;</Link>
      </div>

      <div className={cx(CARD, 'mono mb-[22px] flex min-h-[38px] flex-wrap items-center gap-x-[22px] gap-y-1.5 px-3.5 py-2 text-caption-1-regular text-text-secondary shadow-card')}>
        <span><span className="text-text-tertiary">MLB</span> XGBoost + count-model sims</span>
        <span><span className="text-text-tertiary">NFL / NHL / NBA</span> empirical projections</span>
        <span><span className="text-text-tertiary">VALIDATION</span> walk-forward, no future data</span>
        <span><span className="text-text-tertiary">SAMPLE</span> 200 bets per MLB system, paper</span>
        {stats.length > 0 && <span><span className="text-text-tertiary">RECORDS AS OF</span> {asOfLabel()}</span>}
      </div>

      <section className={cx(CARD, 'mb-6 p-5')}>
        <div className="dell-heading mb-3 tracking-[0.1em] text-text-secondary">Training Methodology</div>
        {[
          'Trained models use walk-forward validation: trained on prior seasons and validated on the next season so the model never sees future data.',
          'An MLB pick has to clear model probability, implied probability, edge threshold, and Kelly-positive checks before it reaches the card. NFL, NHL and NBA run projection systems that are shown beside the market but not tracked in the public results.',
          'Every system is paper-only. MLB bets settle publicly; reaching 200 bets means the sample is large enough to judge, not that the system is profitable, and live ROI is shown next to each model.',
        ].map((p, i) => (
          <p key={i} className={cx('text-body-2-regular text-text-secondary', i < 2 && 'mb-2.5')}>{p}</p>
        ))}
      </section>

      <SportSection sport="mlb" open={!sp.sport || sp.sport === 'mlb'}>
      <section className="mb-6 mt-3.5">
        <div className={cx(LBL, 'mb-2.5')}>Active systems</div>
        <div className={cx(CARD, 'models-table-desktop overflow-hidden')}>
          <div className="grid border-b border-separator-border bg-background-secondary-default" style={{ gridTemplateColumns: '110px 1fr 88px 88px 72px' }}>
            {['System', 'What it prices', 'Metric', 'Training', 'Detail'].map(h => (
              <div key={h} className="mono px-3 py-[9px] text-[10px] uppercase tracking-[0.1em] text-text-secondary">{h}</div>
            ))}
          </div>
          {ACTIVE_MODELS.map((model, i) => {
            const stat = stats.find(s => s.system === model.system)
            const pill = PILL[model.system]
            const roi = stat && stat.roi != null ? parseFloat(String(stat.roi)) : null
            return (
              <div key={model.system} className={cx('grid items-center transition-colors hover:bg-background-primary-hover', i < ACTIVE_MODELS.length - 1 && 'border-b border-border-table')} style={{ gridTemplateColumns: '110px 1fr 88px 88px 72px' }}>
                <div className="p-3">
                  <span className="mono mb-1 inline-block rounded-full px-2 py-[3px] text-[10px] font-bold" style={{ background: pill.bg, color: pill.color, border: pill.border }}>{model.system}</span>
                  <div className="mono text-[10px] text-text-secondary">{model.version}</div>
                </div>
                <div className="p-3 text-caption-1-regular text-text-secondary">{model.desc}</div>
                <div className="p-3">
                  <div className="mono text-headline-bold text-text-primary">{model.metric}</div>
                  <div className="mono text-[10px] uppercase tracking-[0.06em] text-text-secondary">{model.metricLabel}</div>
                  {roi !== null && <div className="mono mt-0.5 text-[10px] font-bold" style={{ color: roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{roi >= 0 ? '+' : ''}{roi.toFixed(1)}% live ROI</div>}
                </div>
                <div className="mono p-3 text-[11px] text-text-secondary">{model.range}</div>
                <div className="p-3">
                  <Link href={model.href} className="times text-body-2-semibold text-[var(--link)] no-underline hover:underline">Detail &rarr;</Link>
                </div>
              </div>
            )
          })}
        </div>

        <div className="models-cards-mobile">
          {ACTIVE_MODELS.map(model => {
            const stat = stats.find(s => s.system === model.system)
            const pill = PILL[model.system]
            const roi = stat && stat.roi != null ? parseFloat(String(stat.roi)) : null
            return (
              <Link key={model.system} href={model.href} className="mb-2.5 block rounded-2lg border border-separator-border bg-background-primary-default p-[13px] no-underline">
                <div className="mb-[9px] flex justify-between gap-3">
                  <span className="mono rounded-full px-2 py-[3px] text-[10px] font-extrabold" style={{ background: pill.bg, color: pill.color, border: pill.border }}>{model.system}</span>
                  <span className="mono text-[10px] text-text-secondary">{model.metricLabel}: <strong className="text-text-primary">{model.metric}</strong></span>
                </div>
                <p className="mb-2.5 text-caption-1-regular text-text-secondary">{model.desc}</p>
                <div className="mono flex justify-between gap-2.5 text-[10px] text-text-secondary">
                  <span>{model.version} / {model.range}</span>
                  {roi !== null && <span className="font-extrabold" style={{ color: roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{roi >= 0 ? '+' : ''}{roi.toFixed(1)}% ROI</span>}
                </div>
              </Link>
            )
          })}
        </div>
      </section>

      <section className="mb-6">
        <div className={cx(LBL, 'mb-2.5')}>Pipeline models</div>
        <div className="pipeline-grid grid gap-px overflow-hidden rounded-xl border border-separator-border bg-separator-border" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
          {PIPELINE_MODELS.map(model => {
            const pill = PILL[model.system]
            const slug = getModelSpec(model.system.toLowerCase().replace(/_/g, '-'))?.slug
            const body = (
              <>
                <span className="mono mb-[9px] inline-block rounded-full px-2 py-[3px] text-[10px] font-extrabold" style={{ background: pill.bg, color: pill.color, border: pill.border }}>{model.system}</span>
                <div className="mb-[5px] text-body-2-bold text-text-primary">{model.label}</div>
                <p className="text-caption-2-regular text-text-secondary">{model.note}</p>
                {slug && <div className="times mt-2 text-caption-1-semibold text-[var(--link)]">Detail &rarr;</div>}
              </>
            )
            return slug
              ? <Link key={model.system} href={`/models/${slug}`} className="block bg-background-primary-default p-3.5 no-underline transition-colors hover:bg-background-primary-hover">{body}</Link>
              : <div key={model.system} className="bg-background-primary-default p-3.5">{body}</div>
          })}
        </div>
      </section>
      </SportSection>

      {(['nfl', 'nhl', 'nba'] as const).map(sport => (
        <SportSection key={sport} sport={sport} open={sp.sport === sport}>
          <ProjectionSportTable sport={sport} />
        </SportSection>
      ))}

      <div className={cx(LBL, 'mb-2.5 mt-6')}>Data sources</div>
      <div className={cx(CARD, 'sources-grid overflow-hidden')} style={{ gridTemplateColumns: 'repeat(4,1fr)' }}>
        {[
          { name: 'Statcast', desc: 'Pitch rows, launch angle, exit velocity, spin, and contact quality.' },
          { name: 'MLB Stats API', desc: 'Game results, innings, lineups, starters, and settlement.' },
          { name: 'Open-Meteo', desc: 'Historical and forecast weather by stadium.' },
          { name: 'Umpire Scorecards', desc: 'Zone accuracy, K boost, and run impact by umpire.' },
          { name: 'nflverse', desc: 'NFL play-by-play, player game logs, rosters and injury reports.' },
          { name: 'NHL official API', desc: 'Skater and goalie box scores, schedules and rosters.' },
          { name: 'ESPN / hoopR', desc: 'NBA player game logs and the official pre-game availability report.' },
          { name: 'ParlayAPI + BettingPros', desc: 'Sportsbook and exchange prices behind every line, consensus and +EV flag.' },
        ].map((src, i) => (
          <div key={src.name} className={cx('p-3.5', i % 4 !== 3 && 'border-r border-separator-border', i < 4 && 'border-b border-separator-border')}>
            <div className="mono mb-1 text-[11px] font-bold text-text-primary">{src.name}</div>
            <div className="text-caption-2-regular text-text-secondary">{src.desc}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
