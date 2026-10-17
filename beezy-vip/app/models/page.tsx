export const dynamic = 'force-dynamic'

import { apiGetStats } from '@/lib/betting-api'
import Link from 'next/link'
import type { Metadata } from 'next'
import { getModelSpec } from '@/lib/model-specs'
import { getSystemsBySport } from '@/lib/pick-systems'
import { liveAccuracy } from '@/lib/prop-model-specs'
import { SPORT_PILLARS, STATUS_LABEL, type ProjectionSport, type SportSlug } from '@/lib/sports'
import { SYSTEM_PILL } from '@/lib/tokens'

export const metadata: Metadata = {
  title: 'Models: Methodology & Transparency',
  description: 'How beezy.fyi builds its models in MLB, NFL, NHL and NBA: validation, accuracy, features and what each system has and has not proven.',
}

const B = '1px solid var(--basalt)'
const B_INNER = '1px solid var(--basalt)'

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
    <details id={sport} open={open} style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--carbon)', marginBottom: '14px', scrollMarginTop: '140px' }}>
      <summary className="sport-summary" style={{ cursor: 'pointer', listStyle: 'none', padding: '14px 18px', display: 'flex', alignItems: 'baseline', gap: '10px 14px', flexWrap: 'wrap' }}>
        <span className="sport-chev" aria-hidden="true" style={{ fontSize: '11px', color: 'var(--fog)', display: 'inline-block', transition: 'transform 120ms ease' }}>&#9656;</span>
        <span style={{ fontSize: '16px', fontWeight: 700, color: pillar.accent }}>{pillar.shortName}</span>
        <span className="mono" style={{ fontSize: '12px', color: 'var(--silver)' }}>{n} systems</span>
        <span className="dell-heading" style={{ fontSize: '10px', color: pillar.accent, border: `1px solid ${pillar.accent}66`, borderRadius: '6px', padding: '2px 8px' }}>{STATUS_LABEL[pillar.status]}</span>
        <span style={{ fontSize: '12px', color: 'var(--fog)', flex: '1 1 320px', minWidth: 0 }}>{pillar.name}</span>
      </summary>
      <div style={{ padding: '4px 18px 18px' }}>{children}</div>
      <style>{SECTION_CSS}</style>
    </details>
  )
}

/** NFL / NHL / NBA: every system with its engine and live accuracy against the last-5 average, linking to its model page. */
function ProjectionSportTable({ sport }: { sport: ProjectionSport }) {
  const systems = getSystemsBySport(sport)
  const TH = { textAlign: 'left', padding: '9px 12px', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)', background: 'var(--obsidian)', borderBottom: B_INNER, whiteSpace: 'nowrap' } as const
  const TD = { padding: '12px', verticalAlign: 'top', borderBottom: B } as const
  return (
    <>
      <p style={{ fontSize: '12px', color: 'var(--silver)', lineHeight: 1.6, margin: '8px 0 14px', maxWidth: '90ch' }}>{SPORT_PILLARS[sport].description}</p>
      <div style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '760px' }}>
          <thead>
            <tr>{['System', 'What it prices', 'Engine', 'Live MAE vs last 5', 'Detail'].map(h => <th key={h} className="mono" style={TH}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {systems.map(sys => {
              const pill = PILL[sys.key] ?? PILL.ALL
              const live = liveAccuracy(sport, sys.statKey)
              return (
                <tr key={sys.key}>
                  <td style={TD}>
                    <span className="mono" style={{ fontSize: '9px', fontWeight: 700, padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border, display: 'inline-block', whiteSpace: 'nowrap' }}>{sys.shortName}</span>
                  </td>
                  <td style={{ ...TD, fontSize: '12px', color: 'var(--silver)', lineHeight: 1.55 }}>
                    <div style={{ fontWeight: 600, color: 'var(--ash)', marginBottom: '2px' }}>{sys.name}</div>
                    {sys.gap ? <span style={{ color: '#f59e0b' }}>Data gap: not projected yet.</span> : sys.description}
                  </td>
                  <td className="mono" style={{ ...TD, fontSize: '11px', color: 'var(--silver)' }}>{sys.family}</td>
                  <td className="mono" style={{ ...TD, fontSize: '11px', color: live && live.dMae < 0 ? 'var(--signal)' : 'var(--silver)', whiteSpace: 'nowrap' }}>
                    {live ? `${live.mae.toFixed(live.mae >= 10 ? 1 : 3)} vs ${live.baselineMae.toFixed(live.baselineMae >= 10 ? 1 : 3)}` : '--'}
                  </td>
                  <td style={TD}><Link href={`/models/${sport}/${sys.slug}`} className="times" style={{ fontSize: '13px', color: 'var(--link)', textDecoration: 'none', fontWeight: 600, whiteSpace: 'nowrap' }}>Detail &rarr;</Link></td>
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
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 24px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', marginBottom: '12px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>Models</h1>
        <Link href="/results" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--link)', textDecoration: 'none' }}>View results &rarr;</Link>
      </div>

      <div className="mono" style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 22px', alignItems: 'center', minHeight: '38px', padding: '8px 14px', background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', fontSize: '12px', color: 'var(--silver)', marginBottom: '22px' }}>
        <span><span style={{ color: 'var(--fog)' }}>MLB</span> XGBoost + count-model sims</span>
        <span><span style={{ color: 'var(--fog)' }}>NFL / NHL / NBA</span> empirical projections</span>
        <span><span style={{ color: 'var(--fog)' }}>VALIDATION</span> walk-forward, no future data</span>
        <span><span style={{ color: 'var(--fog)' }}>SAMPLE</span> 200 bets per MLB system, paper</span>
      </div>

      <section style={{ padding: '20px', border: B, borderRadius: 'var(--radius-lg)', marginBottom: '24px', background: 'var(--graphite)' }}>
        <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--silver)', marginBottom: '12px' }}>Training Methodology</div>
        {[
          'Trained models use walk-forward validation: trained on prior seasons and validated on the next season so the model never sees future data.',
          'An MLB pick has to clear model probability, implied probability, edge threshold, and Kelly-positive checks before it reaches the card. NFL, NHL and NBA run projection systems that are shown beside the market but not tracked in the public results.',
          'Every system is paper-only. MLB bets settle publicly; reaching 200 bets means the sample is large enough to judge, not that the system is profitable, and live ROI is shown next to each model.',
        ].map((p, i) => (
          <p key={i} style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.7, marginBottom: i < 2 ? '10px' : 0 }}>{p}</p>
        ))}
      </section>

      <SportSection sport="mlb" open={!sp.sport || sp.sport === 'mlb'}>
      <section style={{ margin: '14px 0 24px' }}>
        <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '10px' }}>Active systems</div>
        <div className="models-table-desktop" style={{ border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', overflow: 'hidden' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr 88px 88px 72px', background: 'var(--obsidian)', borderBottom: B_INNER }}>
            {['System', 'What it prices', 'Metric', 'Training', 'Detail'].map(h => (
              <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)', padding: '9px 12px' }}>{h}</div>
            ))}
          </div>
          {ACTIVE_MODELS.map((model, i) => {
            const stat = stats.find(s => s.system === model.system)
            const pill = PILL[model.system]
            const roi = stat && stat.roi != null ? parseFloat(String(stat.roi)) : null
            return (
              <div key={model.system} style={{ display: 'grid', gridTemplateColumns: '110px 1fr 88px 88px 72px', borderBottom: i < ACTIVE_MODELS.length - 1 ? B : undefined, alignItems: 'center' }}>
                <div style={{ padding: '12px' }}>
                  <span className="mono" style={{ fontSize: '9px', fontWeight: 700, padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border, display: 'inline-block', marginBottom: '4px' }}>{model.system}</span>
                  <div className="mono" style={{ fontSize: '9px', color: 'var(--fog)' }}>{model.version}</div>
                </div>
                <div style={{ padding: '12px', fontSize: '12px', color: 'var(--silver)', lineHeight: 1.55 }}>{model.desc}</div>
                <div style={{ padding: '12px' }}>
                  <div className="mono" style={{ fontSize: '15px', fontWeight: 700, color: 'var(--ash)' }}>{model.metric}</div>
                  <div className="mono" style={{ fontSize: '8px', color: 'var(--fog)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{model.metricLabel}</div>
                  {roi !== null && <div className="mono" style={{ fontSize: '10px', color: roi >= 0 ? 'var(--signal)' : 'var(--loss)', marginTop: '2px', fontWeight: 700 }}>{roi >= 0 ? '+' : ''}{roi.toFixed(1)}% live ROI</div>}
                </div>
                <div className="mono" style={{ padding: '12px', fontSize: '11px', color: 'var(--silver)' }}>{model.range}</div>
                <div style={{ padding: '12px' }}>
                  <Link href={model.href} className="times" style={{ fontSize: '13px', color: 'var(--link)', textDecoration: 'none', fontWeight: 600 }}>Detail &rarr;</Link>
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
              <Link key={model.system} href={model.href} style={{ display: 'block', textDecoration: 'none', border: B, borderRadius: 'var(--radius)', background: 'var(--graphite)', padding: '13px', marginBottom: '10px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', marginBottom: '9px' }}>
                  <span className="mono" style={{ fontSize: '10px', fontWeight: 800, padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border }}>{model.system}</span>
                  <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>{model.metricLabel}: <strong style={{ color: 'var(--ash)' }}>{model.metric}</strong></span>
                </div>
                <p style={{ fontSize: '12px', color: 'var(--silver)', lineHeight: 1.55, marginBottom: '10px' }}>{model.desc}</p>
                <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', fontSize: '10px', color: 'var(--fog)' }}>
                  <span>{model.version} / {model.range}</span>
                  {roi !== null && <span style={{ color: roi >= 0 ? 'var(--signal)' : 'var(--loss)', fontWeight: 800 }}>{roi >= 0 ? '+' : ''}{roi.toFixed(1)}% ROI</span>}
                </div>
              </Link>
            )
          })}
        </div>
      </section>

      <section style={{ marginBottom: '24px' }}>
        <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '10px' }}>Pipeline models</div>
        <div className="pipeline-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1px', border: B, borderRadius: 'var(--radius-lg)', overflow: 'hidden', background: 'var(--basalt)' }}>
          {PIPELINE_MODELS.map(model => {
            const pill = PILL[model.system]
            const slug = getModelSpec(model.system.toLowerCase().replace(/_/g, '-'))?.slug
            const body = (
              <>
                <span className="mono" style={{ display: 'inline-block', fontSize: '9px', fontWeight: 800, padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border, marginBottom: '9px' }}>{model.system}</span>
                <div style={{ fontSize: '13px', fontWeight: 700, color: 'var(--ash)', marginBottom: '5px' }}>{model.label}</div>
                <p style={{ fontSize: '11px', color: 'var(--fog)', lineHeight: 1.5 }}>{model.note}</p>
                {slug && <div className="times" style={{ fontSize: '12px', color: 'var(--link)', fontWeight: 600, marginTop: '8px' }}>Detail &rarr;</div>}
              </>
            )
            return slug
              ? <Link key={model.system} href={`/models/${slug}`} style={{ background: 'var(--graphite)', padding: '14px', display: 'block', textDecoration: 'none' }}>{body}</Link>
              : <div key={model.system} style={{ background: 'var(--graphite)', padding: '14px' }}>{body}</div>
          })}
        </div>
      </section>
      </SportSection>

      {(['nfl', 'nhl', 'nba'] as const).map(sport => (
        <SportSection key={sport} sport={sport} open={sp.sport === sport}>
          <ProjectionSportTable sport={sport} />
        </SportSection>
      ))}

      <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', margin: '24px 0 10px' }}>Data sources</div>
      <div className="sources-grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', border: B, borderRadius: 'var(--radius-lg)', background: 'var(--graphite)', overflow: 'hidden' }}>
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
          <div key={src.name} style={{ padding: '14px', borderRight: i % 4 !== 3 ? B : undefined, borderBottom: i < 4 ? B : undefined }}>
            <div className="mono" style={{ fontSize: '11px', fontWeight: 700, color: 'var(--ash)', marginBottom: '4px' }}>{src.name}</div>
            <div style={{ fontSize: '11px', color: 'var(--fog)', lineHeight: 1.5 }}>{src.desc}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
