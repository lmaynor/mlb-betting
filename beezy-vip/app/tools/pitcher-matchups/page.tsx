export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { LiveDot }       from '@/components/ui/primitives'

export const metadata: Metadata = {
  robots: { index: false, follow: false },
  title:       'MLB Pitcher Strikeout Props Today — Matchup Dashboard',
  description: "Today's MLB starters with SwStr%, zone rate, and opponent K%. Beezy K projection for Pro members.",
}

export const revalidate = 1800

async function fetchPitcherFeatures() {
  const bucket = process.env.GCS_BUCKET_NAME
  if (!bucket) return null

  try {
    const res = await fetch(
      `https://storage.googleapis.com/${bucket}/K_Pro_System/data/model_features.csv`,
      { next: { revalidate: 1800 } }
    )
    if (!res.ok) return null
    const text = await res.text()
    return parseCSV(text)
  } catch {
    return null
  }
}

function parseCSV(text: string): Record<string, string>[] {
  const lines = text.trim().split('\n')
  if (lines.length < 2) return []
  const headers = lines[0].split(',').map(h => h.trim())
  return lines.slice(1).map(line => {
    const values = line.split(',')
    return Object.fromEntries(headers.map((h, i) => [h, (values[i] ?? '').trim()]))
  })
}

const SEED_PITCHERS = [
  { pitcher: 'Zack Wheeler',    team: 'PHI', opp: 'ATL', line: 'O7.5 -115', swStr: '13.4%', zone: '48.2%', oppK: '23.1%', l5AvgK: 7.8, beezyProj: null },
  { pitcher: 'Spencer Strider', team: 'ATL', opp: 'PHI', line: 'O8.5 -120', swStr: '17.2%', zone: '44.1%', oppK: '21.8%', l5AvgK: 9.2, beezyProj: null },
  { pitcher: 'Gerrit Cole',     team: 'NYY', opp: 'BOS', line: 'O7.5 -110', swStr: '15.1%', zone: '46.8%', oppK: '22.4%', l5AvgK: 8.1, beezyProj: null },
  { pitcher: 'Logan Webb',      team: 'SF',  opp: 'LAD', line: 'O5.5 -125', swStr: '9.8%',  zone: '52.3%', oppK: '19.7%', l5AvgK: 5.9, beezyProj: null },
  { pitcher: 'Framber Valdez',  team: 'HOU', opp: 'TEX', line: 'O6.5 -110', swStr: '11.2%', zone: '50.1%', oppK: '20.3%', l5AvgK: 6.8, beezyProj: null },
  { pitcher: 'Tyler Glasnow',   team: 'LAD', opp: 'SF',  line: 'O8.5 -115', swStr: '16.8%', zone: '42.9%', oppK: '24.1%', l5AvgK: 9.0, beezyProj: null },
]

function swStrColor(val: string): string {
  const n = parseFloat(val)
  if (n >= 14) return 'text-win'
  if (n <= 10) return 'text-loss'
  return 'text-text'
}

export default async function PitcherMatchupsPage() {
  const features  = await fetchPitcherFeatures()
  const pitchers  = features ?? SEED_PITCHERS
  const isLive    = features !== null

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-3">
          <p className="mono text-xs text-accent uppercase tracking-widest">Tools</p>
          {isLive && <LiveDot label="Live Data" />}
        </div>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">
          Pitcher Matchup Dashboard
        </h1>
        <p className="text-muted text-sm">
          Today&apos;s starters with key strikeout model features. K projection for Pro members.
        </p>
      </div>

      <div className="border border-[var(--border)] overflow-x-auto">
        <div
          className="grid min-w-[860px] border-b border-[var(--border)] bg-[var(--surface)]"
          style={{ gridTemplateColumns: '1.6fr 0.6fr 0.6fr 1fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr' }}
        >
          {['Pitcher', 'Team', 'Opp', 'Line', 'SwStr%', 'Zone%', 'Opp K%', 'L5 Avg K', 'Beezy K Proj'].map(h => (
            <div key={h} className="px-3 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
          ))}
        </div>

        {pitchers.map((p: Record<string, string | number | null>, i: number) => {
          const sw   = String(p.swStr ?? p.sw_str ?? p.swstr ?? '—')
          const zone = String(p.zone  ?? p.zone_pct ?? '—')
          const oppK = String(p.oppK  ?? p.opp_k   ?? '—')
          const l5   = p.l5AvgK ?? p.l5_avg_k ?? '—'
          const line = String(p.line ?? '—')

          return (
            <div
              key={i}
              className="grid min-w-[860px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
              style={{ gridTemplateColumns: '1.6fr 0.6fr 0.6fr 1fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr' }}
            >
              <div className="px-3 py-3 text-xs font-semibold">{String(p.pitcher ?? '—')}</div>
              <div className="px-3 py-3 mono text-xs text-muted">{String(p.team ?? '—')}</div>
              <div className="px-3 py-3 mono text-xs text-muted">{String(p.opp ?? '—')}</div>
              <div className="px-3 py-3 mono text-xs text-text">{line}</div>
              <div className={`px-3 py-3 mono text-xs font-semibold ${swStrColor(sw)}`}>{sw}</div>
              <div className="px-3 py-3 mono text-xs text-text">{zone}</div>
              <div className="px-3 py-3 mono text-xs text-text">{oppK}</div>
              <div className="px-3 py-3 mono text-xs font-semibold text-text">{String(l5)}</div>
              <div className="px-3 py-3">
                {p.beezyProj ? (
                  <span className="mono text-sm font-semibold text-accent">{String(p.beezyProj)}</span>
                ) : (
                  <span className="mono text-xs text-muted border border-[var(--border)] px-2 py-0.5">Pro</span>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div className="mt-4 mono text-xs text-muted space-y-1">
        <p>SwStr% = swinging strike rate · higher is better for K props</p>
        <p>Zone% = % of pitches in strike zone · lower often correlates with higher K rate</p>
        <p>L5 Avg K = average strikeouts over last 5 starts</p>
      </div>

      <div className="mt-8 border border-accent/20 bg-accent/5 p-5 flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-semibold mb-1">See Beezy K projections</p>
          <p className="mono text-xs text-muted">
            Pro members get model-projected strikeout totals for every starter, updated daily.
          </p>
        </div>
        <a
          href="/signup"
          className="shrink-0 mono text-xs uppercase tracking-widest px-4 py-2.5 bg-accent text-bg font-semibold hover:bg-accent/90 transition-colors"
        >
          Get Pro Access
        </a>
      </div>
    </div>
  )
}
