export const dynamic = 'force-dynamic'

import { apiGetStats } from '@/lib/betting-api'
import Link from 'next/link'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Models - Methodology & Transparency',
  description: 'How Beezy.FYI builds MLB betting models. Walk-forward CV, Kelly gating, 200-bet gate, and pipeline systems.',
}

const B = '0.5px solid #1f1f24'

const PILL: Record<string, { bg: string; color: string; border: string }> = {
  NRFI: { bg: '#052016', color: '#10b981', border: '0.5px solid #0f6e56' },
  HR: { bg: '#1c1207', color: '#f59e0b', border: '0.5px solid #854f0b' },
  F5: { bg: '#040e1c', color: '#3b82f6', border: '0.5px solid #185fa5' },
  K: { bg: '#0e0718', color: '#a78bfa', border: '0.5px solid #534ab7' },
  OUTS: { bg: '#1a0d05', color: '#fb923c', border: '0.5px solid #9a3412' },
  GAME: { bg: '#09111f', color: '#60a5fa', border: '0.5px solid #1d4ed8' },
  F3: { bg: '#101827', color: '#38bdf8', border: '0.5px solid #0e7490' },
  F1H: { bg: '#101827', color: '#38bdf8', border: '0.5px solid #0e7490' },
  F7: { bg: '#101827', color: '#38bdf8', border: '0.5px solid #0e7490' },
  BATTER_K: { bg: '#130d1c', color: '#c084fc', border: '0.5px solid #6b21a8' },
  BATTER_TB: { bg: '#1c1207', color: '#fbbf24', border: '0.5px solid #a16207' },
  BATTER_HITS: { bg: '#141006', color: '#fde047', border: '0.5px solid #a16207' },
  PITCHER_ER: { bg: '#1a0d05', color: '#fb7185', border: '0.5px solid #be123c' },
}

const ACTIVE_MODELS = [
  { system: 'NRFI', version: 'v17', desc: 'Predicts whether the first inning will be scoreless using starter quality, umpire zone, park, and weather context.', range: '2021-2025', metric: '0.577', metricLabel: 'OOS AUC', href: '/models/nrfi' },
  { system: 'HR', version: 'v6', desc: 'Prices batter HR probability with barrel rate, launch angle, pitcher HR risk, park factor, and platoon split.', range: '2021-2025', metric: '0.630', metricLabel: 'OOS AUC', href: '/models/hr' },
  { system: 'F5', version: 'v5', desc: 'Models first-five moneyline outcomes from starter SIERA, recent form, opponent wOBA, umpire, and weather.', range: '2022-2025', metric: '0.553', metricLabel: 'OOS AUC', href: '/models/f5' },
  { system: 'K', version: 'v1', desc: 'Projects starter strikeout totals with swinging strikes, zone/chase rates, opponent K%, and recent K workload.', range: '2021-2025', metric: '1.807', metricLabel: 'OOS MAE', href: '/models/k' },
  { system: 'OUTS', version: 'v1', desc: 'Projects starter outs recorded from pitch efficiency, innings trends, bullpen context, and opponent OBP.', range: '2021-2025', metric: 'Proxy', metricLabel: 'IP model', href: '/models/outs' },
]

const PIPELINE_MODELS = [
  { system: 'GAME', label: 'Full Game', note: 'Moneyline and totals layer that extends F5 context into full-game pricing.' },
  { system: 'F3', label: 'First 3', note: 'Early-game starter window for openers, short leashes, and lineup-top exposure.' },
  { system: 'F1H', label: 'First Half', note: 'Hybrid innings window for stronger starter splits before bullpen noise dominates.' },
  { system: 'F7', label: 'First 7', note: 'Late starter and bridge-relief pricing before full bullpen exposure.' },
  { system: 'BATTER_K', label: 'Batter K', note: 'Batter strikeout props using pitcher shape, zone, chase, and batter whiff profile.' },
  { system: 'BATTER_TB', label: 'Total Bases', note: 'Batter total-base props with contact quality, matchup, lineup slot, and park.' },
  { system: 'BATTER_HITS', label: 'Hits', note: 'Hit props using contact rate, expected average, platoon split, and park/run context.' },
  { system: 'PITCHER_ER', label: 'Pitcher ER', note: 'Earned-runs props using starter quality, opponent run creation, weather, and leash.' },
]

export default async function ModelsPage() {
  const stats = await apiGetStats().then(s => s.bySystem).catch(() => [])

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '36px 20px' }}>
      <div style={{ marginBottom: '24px', display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 700, color: '#f5f5f7', marginBottom: '6px' }}>Models</h1>
          <p className="mono" style={{ fontSize: '12px', color: '#71717a', maxWidth: '620px', lineHeight: 1.55 }}>
            Active systems, pipeline markets, and the validation rules behind the card.
          </p>
        </div>
        <Link href="/results" className="mono" style={{ fontSize: '11px', color: '#10b981', textDecoration: 'none', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
          View results
        </Link>
      </div>

      <div className="models-strip" style={{ gridTemplateColumns: 'repeat(3,1fr)', border: B, marginBottom: '22px' }}>
        {[
          { label: 'Core engine', value: 'XGBoost + props sims', sub: 'Market-specific model stack' },
          { label: 'Validation', value: 'Walk-forward CV', sub: 'No future data leakage' },
          { label: 'Launch gate', value: '200 bets', sub: 'Per-system paper validation' },
        ].map((item, i) => (
          <div key={item.label} style={{ padding: '18px', borderRight: i < 2 ? B : undefined }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '6px' }}>{item.label}</div>
            <div className="mono" style={{ fontSize: '16px', fontWeight: 700, color: '#f5f5f7', marginBottom: '4px' }}>{item.value}</div>
            <div style={{ fontSize: '11px', color: '#71717a' }}>{item.sub}</div>
          </div>
        ))}
      </div>

      <section style={{ padding: '18px', border: B, marginBottom: '24px', background: '#0d0d12' }}>
        <div className="mono" style={{ fontSize: '10px', fontWeight: 800, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#a1a1aa', marginBottom: '12px' }}>Training methodology</div>
        {[
          'Models use walk-forward validation: trained on prior seasons and validated on the next season so the model never sees future data.',
          'A pick has to clear model probability, implied probability, edge threshold, and Kelly-positive checks before it reaches the card.',
          'Systems stay in paper mode until they clear the 200-bet gate. Results are tracked transparently before paid access opens.',
        ].map((p, i) => (
          <p key={i} style={{ fontSize: '13px', color: '#a1a1aa', lineHeight: 1.7, marginBottom: i < 2 ? '10px' : 0 }}>{p}</p>
        ))}
      </section>

      <section style={{ marginBottom: '24px' }}>
        <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '10px' }}>Active systems</div>
        <div className="models-table-desktop" style={{ border: B }}>
          <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr 88px 88px 72px', background: '#111114', borderBottom: B }}>
            {['System', 'What it prices', 'Metric', 'Training', 'Detail'].map(h => (
              <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', padding: '9px 12px' }}>{h}</div>
            ))}
          </div>
          {ACTIVE_MODELS.map((model, i) => {
            const stat = stats.find(s => s.system === model.system)
            const pill = PILL[model.system]
            const roi = stat ? parseFloat(String(stat.roi ?? 0)) : null
            return (
              <div key={model.system} style={{ display: 'grid', gridTemplateColumns: '110px 1fr 88px 88px 72px', borderBottom: i < ACTIVE_MODELS.length - 1 ? B : undefined, alignItems: 'center' }}>
                <div style={{ padding: '12px' }}>
                  <span className="mono" style={{ fontSize: '9px', fontWeight: 700, padding: '3px 7px', background: pill.bg, color: pill.color, border: pill.border, display: 'inline-block', marginBottom: '4px' }}>{model.system}</span>
                  <div className="mono" style={{ fontSize: '9px', color: '#71717a' }}>{model.version}</div>
                </div>
                <div style={{ padding: '12px', fontSize: '12px', color: '#a1a1aa', lineHeight: 1.55 }}>{model.desc}</div>
                <div style={{ padding: '12px' }}>
                  <div className="mono" style={{ fontSize: '15px', fontWeight: 700, color: '#f5f5f7' }}>{model.metric}</div>
                  <div className="mono" style={{ fontSize: '8px', color: '#71717a', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{model.metricLabel}</div>
                  {roi !== null && <div className="mono" style={{ fontSize: '10px', color: roi >= 0 ? '#10b981' : '#ef4444', marginTop: '2px', fontWeight: 700 }}>{roi >= 0 ? '+' : ''}{roi.toFixed(1)}%</div>}
                </div>
                <div className="mono" style={{ padding: '12px', fontSize: '11px', color: '#a1a1aa' }}>{model.range}</div>
                <div style={{ padding: '12px' }}>
                  <Link href={model.href} style={{ fontSize: '11px', color: '#3b82f6', textDecoration: 'none' }}>Detail</Link>
                </div>
              </div>
            )
          })}
        </div>

        <div className="models-cards-mobile">
          {ACTIVE_MODELS.map(model => {
            const stat = stats.find(s => s.system === model.system)
            const pill = PILL[model.system]
            const roi = stat ? parseFloat(String(stat.roi ?? 0)) : null
            return (
              <Link key={model.system} href={model.href} style={{ display: 'block', textDecoration: 'none', border: B, borderRadius: 'var(--radius)', background: '#0d0d12', padding: '13px', marginBottom: '10px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', marginBottom: '9px' }}>
                  <span className="mono" style={{ fontSize: '10px', fontWeight: 800, padding: '3px 7px', background: pill.bg, color: pill.color, border: pill.border }}>{model.system}</span>
                  <span className="mono" style={{ fontSize: '10px', color: '#71717a' }}>{model.metricLabel}: <strong style={{ color: '#f5f5f7' }}>{model.metric}</strong></span>
                </div>
                <p style={{ fontSize: '12px', color: '#a1a1aa', lineHeight: 1.55, marginBottom: '10px' }}>{model.desc}</p>
                <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', fontSize: '10px', color: '#71717a' }}>
                  <span>{model.version} / {model.range}</span>
                  {roi !== null && <span style={{ color: roi >= 0 ? '#10b981' : '#ef4444', fontWeight: 800 }}>{roi >= 0 ? '+' : ''}{roi.toFixed(1)}% ROI</span>}
                </div>
              </Link>
            )
          })}
        </div>
      </section>

      <section style={{ marginBottom: '24px' }}>
        <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '10px' }}>Pipeline models</div>
        <div className="pipeline-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1px', border: B, background: '#1f1f24' }}>
          {PIPELINE_MODELS.map(model => {
            const pill = PILL[model.system]
            return (
              <div key={model.system} style={{ background: '#0d0d12', padding: '14px' }}>
                <span className="mono" style={{ display: 'inline-block', fontSize: '9px', fontWeight: 800, padding: '3px 7px', background: pill.bg, color: pill.color, border: pill.border, marginBottom: '9px' }}>{model.system}</span>
                <div style={{ fontSize: '13px', fontWeight: 700, color: '#f5f5f7', marginBottom: '5px' }}>{model.label}</div>
                <p style={{ fontSize: '11px', color: '#71717a', lineHeight: 1.5 }}>{model.note}</p>
              </div>
            )
          })}
        </div>
      </section>

      <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '10px' }}>Data sources</div>
      <div className="sources-grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', border: B }}>
        {[
          { name: 'Statcast', desc: 'Pitch rows, launch angle, exit velocity, spin, and contact quality.' },
          { name: 'MLB Stats API', desc: 'Game results, innings, lineups, starters, and settlement.' },
          { name: 'Open-Meteo', desc: 'Historical and forecast weather by stadium.' },
          { name: 'Umpire Scorecards', desc: 'Zone accuracy, K boost, and run impact by umpire.' },
        ].map((src, i) => (
          <div key={src.name} style={{ padding: '14px', borderRight: i < 3 ? B : undefined }}>
            <div className="mono" style={{ fontSize: '11px', fontWeight: 700, color: '#f5f5f7', marginBottom: '4px' }}>{src.name}</div>
            <div style={{ fontSize: '11px', color: '#71717a', lineHeight: 1.5 }}>{src.desc}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
