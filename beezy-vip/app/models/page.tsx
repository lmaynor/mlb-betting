export const dynamic = 'force-dynamic'

import { apiGetStats } from '@/lib/betting-api'
import Link from 'next/link'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Models — Methodology & Transparency',
  description: 'How Beezy.VIP builds XGBoost models for MLB betting. Walk-forward CV, Kelly gating, 200-bet gate.',
}

const B = '0.5px solid #1f1f24'

const PILL: Record<string, { bg: string; color: string; border: string }> = {
  NRFI: { bg: '#052016', color: '#10b981', border: '0.5px solid #0f6e56' },
  HR:   { bg: '#1c1207', color: '#f59e0b', border: '0.5px solid #854f0b' },
  F5:   { bg: '#040e1c', color: '#3b82f6', border: '0.5px solid #185fa5' },
  K:    { bg: '#0e0718', color: '#a78bfa', border: '0.5px solid #534ab7' },
  OUTS: { bg: '#1a0d05', color: '#fb923c', border: '0.5px solid #9a3412' },
}

const MODEL_DETAIL = {
  NRFI: { version: 'v17', desc: 'Predicts whether the first inning will be scoreless. Primary features: starter ERA/FIP/K%, umpire called-strike%, park factor, wind speed/direction, game-time temperature.', range: '2021–2025', auc: 0.618, href: '/models/nrfi' },
  HR:   { version: 'v6',  desc: 'Predicts batter HR probability per PA. Primary features: barrel rate, hard-hit%, launch angle, pitcher HR/9, fly ball%, park HR factor, platoon split.', range: '2021–2025', auc: 0.603, href: '/models/hr' },
  F5:   { version: 'v4',  desc: 'Predicts F5 winner. Primary features: starter SIERA, last-5 ERA, opponent wOBA, umpire run-scoring rate, weather.', range: '2022–2025', auc: 0.594, href: '/models/f5' },
  K:    { version: 'v9',  desc: 'Predicts starter strikeout total. Primary features: SwStr%, Zone%, ChaseStr%, opponent K%, L5 avg Ks, home/away split.', range: '2021–2025', auc: 0.631, href: '/models/k' },
  OUTS: { version: 'v3',  desc: 'Predicts outs recorded by a starter. Primary features: pitch efficiency, innings-per-start trend, bullpen availability, opponent OBP.', range: '2022–2025', auc: 0.587, href: '/models/outs' },
}

export default async function ModelsPage() {
  const stats = await apiGetStats().then(s => s.bySystem).catch(() => [])

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '28px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '6px' }}>Models</h1>
        <p className="mono" style={{ fontSize: '13px', color: '#71717a' }}>Full methodology. Bettors are skeptical — we show our work.</p>
      </div>

      {/* Overview strip */}
      <div className="models-strip" style={{ gridTemplateColumns: 'repeat(3,1fr)', border: B, marginBottom: '24px' }}>
        {[
          { label: 'Model type',   value: 'XGBoost',         sub: 'Gradient boosted trees' },
          { label: 'Training data', value: '946k+ rows',     sub: 'Statcast pitch-level data' },
          { label: 'Validation',   value: 'Walk-forward CV', sub: 'No future data leakage' },
        ].map((item, i) => (
          <div key={item.label} style={{ padding: '18px', borderRight: i < 2 ? B : undefined }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '6px' }}>{item.label}</div>
            <div className="mono" style={{ fontSize: '16px', fontWeight: 600, color: '#f5f5f7', marginBottom: '4px' }}>{item.value}</div>
            <div style={{ fontSize: '11px', color: '#71717a' }}>{item.sub}</div>
          </div>
        ))}
      </div>

      {/* Methodology prose */}
      <div style={{ padding: '20px', border: B, marginBottom: '24px' }}>
        <div style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#a1a1aa', marginBottom: '12px' }}>Training methodology</div>
        {[
          'Each model uses walk-forward cross-validation: trained on seasons N through N-1, validated on season N. The model never sees future data.',
          'Kelly criterion gates betting: a bet only qualifies if model probability minus implied probability exceeds the edge threshold and Kelly stake is positive.',
          'Models enter production after clearing a 200-bet out-of-sample gate. Currently in paper mode — results are simulated, not real money.',
        ].map((p, i) => (
          <p key={i} style={{ fontSize: '13px', color: '#a1a1aa', lineHeight: 1.7, marginBottom: i < 2 ? '10px' : 0 }}>{p}</p>
        ))}
      </div>

      {/* Model table */}
      <div style={{ marginBottom: '24px' }}>
        <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '10px' }}>Active systems</div>
        <div style={{ border: B }}>
          <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr 80px 80px 70px', background: '#111114', borderBottom: B }}>
            {['System', 'Description', 'OOS AUC', 'Training', 'Detail'].map(h => (
              <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', padding: '9px 12px' }}>{h}</div>
            ))}
          </div>
          {Object.entries(MODEL_DETAIL).map(([sys, d], i) => {
            const stat = stats.find(s => s.system === sys)
            const pill = PILL[sys] ?? { bg: '#1f1f24', color: '#a1a1aa', border: B }
            const roi  = stat ? parseFloat(String(stat.roi ?? 0)) : null
            return (
              <div key={sys} style={{ display: 'grid', gridTemplateColumns: '110px 1fr 80px 80px 70px', borderBottom: i < 4 ? B : undefined, alignItems: 'center' }}>
                <div style={{ padding: '12px' }}>
                  <span className="mono" style={{ fontSize: '9px', fontWeight: 600, padding: '3px 7px', background: pill.bg, color: pill.color, border: pill.border, display: 'inline-block', marginBottom: '4px' }}>{sys}</span>
                  <div className="mono" style={{ fontSize: '9px', color: '#71717a' }}>{d.version}</div>
                </div>
                <div style={{ padding: '12px' }}>
                  <div style={{ fontSize: '12px', fontWeight: 600, color: '#f5f5f7', marginBottom: '2px' }}>
                    {MODEL_DETAIL[sys as keyof typeof MODEL_DETAIL]?.desc.split('.')[0]}
                  </div>
                  <div style={{ fontSize: '11px', color: '#71717a' }}>{d.desc.split('.').slice(1).join('.').trim()}</div>
                </div>
                <div style={{ padding: '12px' }}>
                  <div className="mono" style={{ fontSize: '16px', fontWeight: 600, color: '#f5f5f7' }}>{d.auc.toFixed(3)}</div>
                  <div className="mono" style={{ fontSize: '9px', color: '#71717a', textTransform: 'uppercase', letterSpacing: '0.06em' }}>OOS AUC</div>
                  {roi !== null && (
                    <div className="mono" style={{ fontSize: '11px', fontWeight: 600, marginTop: '2px', color: roi >= 0 ? '#10b981' : '#ef4444' }}>
                      {roi >= 0 ? '+' : ''}{roi.toFixed(1)}%
                    </div>
                  )}
                </div>
                <div style={{ padding: '12px' }}>
                  <span className="mono" style={{ fontSize: '11px', color: '#a1a1aa' }}>{d.range}</span>
                </div>
                <div style={{ padding: '12px' }}>
                  <Link href={d.href} style={{ fontSize: '11px', color: '#3b82f6', textDecoration: 'none' }}>Detail &rarr;</Link>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Data sources */}
      <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '10px' }}>Data sources</div>
      <div className="sources-grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', border: B }}>
        {[
          { name: 'Statcast',          desc: '946k+ pitch rows. Launch angle, exit velocity, spin rate.' },
          { name: 'MLB Stats API',      desc: 'Game results, inning scoring, lineup data, settlement.' },
          { name: 'Open-Meteo',         desc: 'Historical and forecast weather per stadium.' },
          { name: 'Umpire Scorecards',  desc: 'K-rate boost, run impact, zone accuracy by umpire.' },
        ].map((src, i) => (
          <div key={src.name} style={{ padding: '14px', borderRight: i < 3 ? B : undefined }}>
            <div className="mono" style={{ fontSize: '11px', fontWeight: 600, color: '#f5f5f7', marginBottom: '4px' }}>{src.name}</div>
            <div style={{ fontSize: '11px', color: '#71717a', lineHeight: 1.5 }}>{src.desc}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
