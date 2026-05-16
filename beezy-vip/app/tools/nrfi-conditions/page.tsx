export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { LiveDot }       from '@/components/ui/primitives'

export const metadata: Metadata = {
  robots: { index: false, follow: false },
  title:       'NRFI Conditions Today — MLB First Inning Data',
  description: "Today's MLB slate with starter ERA, umpire K-rate, wind, and park factors for NRFI betting. Updated daily.",
}

export const revalidate = 1800  // 30 min

// GCS fetch helper — conditions data is public
async function fetchNRFIFeatures() {
  const bucket = process.env.GCS_BUCKET_NAME
  if (!bucket) return null

  try {
    const res = await fetch(
      `https://storage.googleapis.com/${bucket}/NRFI_Pro_System/data/pitcher_start_features.csv`,
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

// Seed data shown when GCS unavailable
const SEED_GAMES = [
  { away: 'NYY', home: 'BOS', awayStarter: 'Gerrit Cole',    homeStarter: 'Brayan Bello',   umpire: 'CB Bucknor', umpKRate: '18.2%', temp: '58°F', wind: '8mph IN',  parkFactor: '1.02', beezyProb: null },
  { away: 'LAD', home: 'SF',  awayStarter: 'Tyler Glasnow',  homeStarter: 'Logan Webb',     umpire: 'Dan Bellino', umpKRate: '22.4%', temp: '62°F', wind: '12mph OUT', parkFactor: '0.94', beezyProb: null },
  { away: 'HOU', home: 'TEX', awayStarter: 'Framber Valdez', homeStarter: 'Nathan Eovaldi', umpire: 'Angel Hernandez', umpKRate: '15.1%', temp: '79°F', wind: '5mph L-R', parkFactor: '1.08', beezyProb: null },
  { away: 'ATL', home: 'PHI', awayStarter: 'Spencer Strider', homeStarter: 'Zack Wheeler', umpire: 'Mark Wegner',  umpKRate: '24.1%', temp: '67°F', wind: '9mph IN',  parkFactor: '0.97', beezyProb: null },
  { away: 'CHC', home: 'MIL', awayStarter: 'Justin Steele',  homeStarter: 'Freddy Peralta', umpire: 'Joe West',   umpKRate: '16.8%', temp: '55°F', wind: '15mph OUT', parkFactor: '0.96', beezyProb: null },
]

export default async function NRFIConditionsPage() {
  const features = await fetchNRFIFeatures()
  const games    = features ?? SEED_GAMES
  const isLive   = features !== null

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-3">
          <p className="mono text-xs text-accent uppercase tracking-widest">Tools</p>
          {isLive && <LiveDot label="Live Data" />}
        </div>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">
          NRFI Conditions Today
        </h1>
        <p className="text-muted text-sm">
          Starter matchups, umpire tendencies, weather, and park factors for today&apos;s slate.
          Model probability visible to Pro members.
        </p>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-4 mb-6 mono text-xs text-muted">
        <span><span className="text-win">■</span> Favorable for NRFI</span>
        <span><span className="text-loss">■</span> Unfavorable for NRFI</span>
        <span><span className="text-muted">■</span> Neutral</span>
      </div>

      <div className="border border-[var(--border)] overflow-x-auto">
        {/* Header */}
        <div className="grid min-w-[900px] border-b border-[var(--border)] bg-[var(--surface)]"
          style={{ gridTemplateColumns: '1.5fr 1.5fr 1fr 0.8fr 0.8fr 0.8fr 0.8fr' }}
        >
          {['Away Starter', 'Home Starter', 'Umpire', 'Ump K%', 'Temp', 'Wind', 'Beezy Prob'].map(h => (
            <div key={h} className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
          ))}
        </div>

        {games.map((g: Record<string, string | null>, i: number) => {
          const windStr   = String(g.wind ?? '')
          const windOut   = windStr.toLowerCase().includes('out')
          const windIn    = windStr.toLowerCase().includes('in')
          const umpKNum   = parseFloat(String(g.umpKRate ?? '0'))
          const umpColor  = umpKNum > 20 ? 'text-win' : umpKNum < 16 ? 'text-loss' : 'text-text'

          return (
            <div
              key={i}
              className="grid min-w-[900px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
              style={{ gridTemplateColumns: '1.5fr 1.5fr 1fr 0.8fr 0.8fr 0.8fr 0.8fr' }}
            >
              <div className="px-4 py-3">
                <p className="text-xs font-semibold">{String(g.awayStarter ?? g.away_starter ?? '—')}</p>
                <p className="mono text-xs text-muted">{String(g.away ?? '')}</p>
              </div>
              <div className="px-4 py-3">
                <p className="text-xs font-semibold">{String(g.homeStarter ?? g.home_starter ?? '—')}</p>
                <p className="mono text-xs text-muted">{String(g.home ?? '')}</p>
              </div>
              <div className="px-4 py-3 mono text-xs text-text">{String(g.umpire ?? '—')}</div>
              <div className={`px-4 py-3 mono text-xs font-semibold ${umpColor}`}>
                {String(g.umpKRate ?? g.ump_k_rate ?? '—')}
              </div>
              <div className="px-4 py-3 mono text-xs text-text">{String(g.temp ?? '—')}</div>
              <div className={`px-4 py-3 mono text-xs font-semibold ${windOut ? 'text-loss' : windIn ? 'text-win' : 'text-text'}`}>
                {windStr || '—'}
              </div>
              {/* Beezy prob — gated */}
              <div className="px-4 py-3">
                {g.beezyProb ? (
                  <span className="mono text-sm font-semibold text-accent">{String(g.beezyProb)}</span>
                ) : (
                  <span className="mono text-xs text-muted border border-[var(--border)] px-2 py-0.5">
                    Pro
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div className="mt-6 flex flex-wrap gap-6 mono text-xs text-muted">
        <span>Ump K% = umpire called-strike rate vs. league avg</span>
        <span>Wind OUT = blowing out to CF (unfavorable)</span>
        <span>Updated after 12:00 UTC model build</span>
      </div>

      {/* Upsell */}
      <div className="mt-8 border border-accent/20 bg-accent/5 p-5 flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-semibold mb-1">See Beezy model probabilities</p>
          <p className="mono text-xs text-muted">
            Pro members see model NRFI probability for every game, updated daily.
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
