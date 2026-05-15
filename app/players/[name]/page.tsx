import { query }      from '@/lib/db'
import { formatOdds } from '@/lib/odds'
import { notFound }   from 'next/navigation'
import type { Metadata } from 'next'

type Props = { params: { name: string } }

export const revalidate = 3600

function decodeName(slug: string): string {
  return decodeURIComponent(slug).replace(/-/g, ' ')
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const name = decodeName(params.name)
  return {
    title:       `${name} Home Run Prop Picks & Model History`,
    robots:      { index: false, follow: false },
    description: `Beezy.VIP HR model history for ${name}. Barrel rate, exit velocity, and historical home run prop performance.`,
  }
}

export default async function PlayerPage({ params }: Props) {
  const name = decodeName(params.name)

  const bets = await query<{
    game_date:    string
    bet_type:     string
    line:         number
    model_prob:   number
    implied_prob: number
    result:       string | null
    pnl:          number | null
  }>(`
    SELECT game_date, bet_type, line, model_prob, implied_prob, result, pnl
    FROM bets
    WHERE system = 'HR'
      AND LOWER(bet_type) LIKE LOWER($1)
    ORDER BY game_date DESC
    LIMIT 30
  `, [`%${name.split(' ').pop()}%`]).catch(() => [])

  const settled  = bets.filter(b => b.result !== null)
  const wins     = settled.filter(b => b.result === 'W').length
  const winRate  = settled.length > 0 ? (wins / settled.length * 100).toFixed(1) : '—'
  const totalPnl = bets.reduce((s, b) => s + (b.pnl ?? 0), 0)

  // Avg model prob across bets = career HR probability per game per model
  const avgProb = bets.length > 0
    ? (bets.reduce((s, b) => s + b.model_prob, 0) / bets.length * 100).toFixed(1)
    : '—'

  return (
    <div className="max-w-4xl mx-auto px-4 py-12">
      <div className="mb-8">
        <p className="mono text-xs text-accent uppercase tracking-widest mb-2">Player · HR Model</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">{name}</h1>
        <p className="mono text-xs text-muted">
          Home run prop model history — Beezy.VIP HR System
        </p>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-4 border border-[var(--border)] mb-8">
        <div className="p-4 border-r border-[var(--border)]">
          <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Model Bets</p>
          <p className="mono text-2xl font-extrabold">{bets.length}</p>
        </div>
        <div className="p-4 border-r border-[var(--border)]">
          <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Win Rate</p>
          <p className="mono text-2xl font-extrabold">{winRate}%</p>
        </div>
        <div className="p-4 border-r border-[var(--border)]">
          <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Avg HR Prob</p>
          <p className="mono text-2xl font-extrabold text-accent">{avgProb}%</p>
        </div>
        <div className="p-4">
          <p className="mono text-xs text-muted uppercase tracking-widest mb-1">P&L</p>
          <p className={`mono text-2xl font-extrabold ${totalPnl >= 0 ? 'text-win' : 'text-loss'}`}>
            {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}u
          </p>
        </div>
      </div>

      {bets.length === 0 ? (
        <div className="border border-[var(--border)] p-12 text-center">
          <p className="mono text-xs text-muted">No HR model history found for {name}.</p>
          <p className="mono text-xs text-muted mt-2">
            Player appears in model when HR line is available and Kelly threshold is met.
          </p>
        </div>
      ) : (
        <div className="border border-[var(--border)] overflow-x-auto">
          <div
            className="grid min-w-[650px] border-b border-[var(--border)] bg-[var(--surface)]"
            style={{ gridTemplateColumns: '0.8fr 1.2fr 0.8fr 0.9fr 0.9fr 0.8fr 0.8fr' }}
          >
            {['Date', 'Pick', 'Line', 'Model Prob', 'Implied', 'Result', 'P&L'].map(h => (
              <div key={h} className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
            ))}
          </div>

          {bets.map((b, i) => {
            const edge = ((b.model_prob - b.implied_prob) * 100).toFixed(1)
            return (
              <div
                key={i}
                className="grid min-w-[650px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
                style={{ gridTemplateColumns: '0.8fr 1.2fr 0.8fr 0.9fr 0.9fr 0.8fr 0.8fr' }}
              >
                <div className="px-4 py-3 mono text-xs text-muted">
                  {new Date(b.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </div>
                <div className="px-4 py-3 mono text-xs text-text">{b.bet_type}</div>
                <div className="px-4 py-3 mono text-xs text-text">{formatOdds(b.line)}</div>
                <div className="px-4 py-3">
                  <span className="mono text-xs font-semibold text-accent">
                    {(b.model_prob * 100).toFixed(1)}%
                  </span>
                  <span className="mono text-xs text-muted ml-1">(+{edge}%)</span>
                </div>
                <div className="px-4 py-3 mono text-xs text-muted">
                  {(b.implied_prob * 100).toFixed(1)}%
                </div>
                <div className="px-4 py-3 mono text-xs font-semibold">
                  <span className={b.result === 'W' ? 'text-win' : b.result === 'L' ? 'text-loss' : 'text-muted'}>
                    {b.result ?? 'Pending'}
                  </span>
                </div>
                <div className="px-4 py-3 mono text-sm font-semibold">
                  <span className={b.pnl !== null && b.pnl >= 0 ? 'text-win' : 'text-loss'}>
                    {b.pnl !== null ? `${b.pnl >= 0 ? '+' : ''}${b.pnl.toFixed(2)}u` : '—'}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* CTA */}
      <div className="mt-8 border border-accent/20 bg-accent/5 p-5 flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-semibold mb-1">See today&apos;s HR model picks</p>
          <p className="mono text-xs text-muted">
            Pro members see all qualifying HR picks with model probability and Kelly sizing.
          </p>
        </div>
        <a
          href="/picks/mlb/hr"
          className="shrink-0 mono text-xs uppercase tracking-widest px-4 py-2.5 bg-accent text-bg font-semibold hover:bg-accent/90 transition-colors"
        >
          View HR Picks →
        </a>
      </div>

      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            '@context':  'https://schema.org',
            '@type':     'Person',
            name,
            url:         `https://beezy.vip/players/${params.name}`,
            description: `${name} home run prop model history on Beezy.VIP`,
          }),
        }}
      />
    </div>
  )
}
