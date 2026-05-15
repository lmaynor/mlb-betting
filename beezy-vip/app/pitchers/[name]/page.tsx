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
    title:       `${name} Strikeout Prop Picks & Model History`,
    robots:      { index: false, follow: false },
    description: `Beezy.VIP K model history for ${name}. Projected strikeouts, SwStr%, and historical model performance.`,
  }
}

export default async function PitcherPage({ params }: Props) {
  const name = decodeName(params.name)

  // Fetch K system bets where pitcher name is in bet_type
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
    WHERE system = 'K'
      AND LOWER(bet_type) LIKE LOWER($1)
    ORDER BY game_date DESC
    LIMIT 30
  `, [`%${name.split(' ').pop()}%`]).catch(() => [])

  const wins     = bets.filter(b => b.result === 'W').length
  const settled  = bets.filter(b => b.result !== null)
  const winRate  = settled.length > 0 ? (wins / settled.length * 100).toFixed(1) : '—'
  const totalPnl = bets.reduce((s, b) => s + (b.pnl ?? 0), 0)

  return (
    <div className="max-w-4xl mx-auto px-4 py-12">
      <div className="mb-8">
        <p className="mono text-xs text-accent uppercase tracking-widest mb-2">Pitcher · K Model</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">
          {name}
        </h1>
        <p className="mono text-xs text-muted">
          Strikeout prop model history — Beezy.VIP K System
        </p>
      </div>

      {/* Career summary */}
      <div className="grid grid-cols-3 border border-[var(--border)] mb-8">
        <div className="p-4 border-r border-[var(--border)]">
          <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Model Bets</p>
          <p className="mono text-2xl font-extrabold">{bets.length}</p>
        </div>
        <div className="p-4 border-r border-[var(--border)]">
          <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Win Rate</p>
          <p className="mono text-2xl font-extrabold">{winRate}%</p>
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
          <p className="mono text-xs text-muted">No K model history found for {name}.</p>
        </div>
      ) : (
        <div className="border border-[var(--border)] overflow-x-auto">
          <div className="grid grid-cols-6 min-w-[600px] border-b border-[var(--border)] bg-[var(--surface)]">
            {['Date', 'Pick', 'Line', 'Model Prob', 'Result', 'P&L'].map(h => (
              <div key={h} className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
            ))}
          </div>
          {bets.map((b, i) => (
            <div key={i} className="grid grid-cols-6 min-w-[600px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors">
              <div className="px-4 py-3 mono text-xs text-muted">
                {new Date(b.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
              </div>
              <div className="px-4 py-3 mono text-xs text-text">{b.bet_type}</div>
              <div className="px-4 py-3 mono text-xs text-text">{formatOdds(b.line)}</div>
              <div className="px-4 py-3 mono text-xs font-semibold text-accent">
                {(b.model_prob * 100).toFixed(1)}%
              </div>
              <div className="px-4 py-3 mono text-xs">
                <span className={b.result === 'W' ? 'text-win font-semibold' : b.result === 'L' ? 'text-loss font-semibold' : 'text-muted'}>
                  {b.result ?? 'Pending'}
                </span>
              </div>
              <div className="px-4 py-3 mono text-sm font-semibold">
                <span className={b.pnl !== null && b.pnl >= 0 ? 'text-win' : 'text-loss'}>
                  {b.pnl !== null ? `${b.pnl >= 0 ? '+' : ''}${b.pnl.toFixed(2)}u` : '—'}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Structured data */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            '@context': 'https://schema.org',
            '@type':    'Person',
            name,
            url:        `https://beezy.vip/pitchers/${params.name}`,
            description: `${name} strikeout prop model history on Beezy.VIP`,
          }),
        }}
      />
    </div>
  )
}
