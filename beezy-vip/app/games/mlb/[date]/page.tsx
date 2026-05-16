export const dynamic = 'force-dynamic'

import { query }        from '@/lib/db'
import { SystemBadge }  from '@/components/ui/primitives'
import { formatOdds }   from '@/lib/odds'
import { notFound }     from 'next/navigation'
import type { Metadata, ResolvingMetadata } from 'next'

type Props = { params: { date: string } }

export const revalidate = 1800

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const date    = params.date
  const display = formatDate(date)
  return {
    title:       `MLB Picks ${display} — Beezy.VIP Model Scores`,
    robots:      { index: false, follow: false },
    description: `Machine learning model scores for every MLB game on ${display}. NRFI probability, K projections, and F5 analysis.`,
  }
}

function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr + 'T12:00:00Z').toLocaleDateString('en-US', {
      month: 'long', day: 'numeric', year: 'numeric',
    })
  } catch {
    return dateStr
  }
}

function isValidDate(d: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(d)
}

export default async function DailyGamesPage({ params }: Props) {
  const { date } = params

  if (!isValidDate(date)) notFound()

  const picks = await query<{
    system:       string
    game_pk:      number
    bet_type:     string
    line:         number
    model_prob:   number
    implied_prob: number
    result:       string | null
    pnl:          number | null
  }>(`
    SELECT system, game_pk, bet_type, line, model_prob, implied_prob, result, pnl
    FROM bets
    WHERE game_date = $1 AND kelly_triggered = true
    ORDER BY system, game_pk
  `, [date]).catch(() => [])

  const display = formatDate(date)

  // Group by game_pk
  type PickRow = { system: string; game_pk: number; bet_type: string; line: number; model_prob: number; implied_prob: number; result: string | null; pnl: number | null }
  const byGame = picks.reduce<Record<number, PickRow[]>>((acc, p) => {
    if (!acc[p.game_pk]) acc[p.game_pk] = []
    acc[p.game_pk].push(p)
    return acc
  }, {})

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      <div className="mb-8">
        <p className="mono text-xs text-accent uppercase tracking-widest mb-2">MLB · Daily Games</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">
          MLB Picks {display}
        </h1>
        <p className="mono text-xs text-muted">
          Beezy.VIP model scores · {picks.length} qualified picks · Paper mode
        </p>
      </div>

      {picks.length === 0 ? (
        <div className="border border-[var(--border)] p-12 text-center">
          <p className="mono text-xs text-muted">No qualified picks for {display}.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {Object.entries(byGame).map(([gamePk, gamePicks]) => (
            <div key={gamePk} className="border border-[var(--border)]">
              <div className="bg-[var(--surface)] border-b border-[var(--border)] px-5 py-3">
                <p className="mono text-xs text-muted">Game {gamePk}</p>
              </div>
              <div className="overflow-x-auto">
                <div
                  className="grid min-w-[600px] border-b border-[var(--border)] bg-[var(--surface)]/50"
                  style={{ gridTemplateColumns: '0.8fr 1fr 0.8fr 0.8fr 0.8fr 0.8fr 0.7fr' }}
                >
                  {['System', 'Pick', 'Line', 'Model Prob', 'Implied', 'Edge', 'Result'].map(h => (
                    <div key={h} className="px-4 py-2 mono text-xs uppercase tracking-widest text-muted">{h}</div>
                  ))}
                </div>
                {gamePicks.map((p, i) => {
                  const edge = ((p.model_prob - p.implied_prob) * 100).toFixed(1)
                  return (
                    <div
                      key={i}
                      className="grid min-w-[600px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
                      style={{ gridTemplateColumns: '0.8fr 1fr 0.8fr 0.8fr 0.8fr 0.8fr 0.7fr' }}
                    >
                      <div className="px-4 py-3"><SystemBadge system={p.system} /></div>
                      <div className="px-4 py-3 mono text-xs text-text">{p.bet_type}</div>
                      <div className="px-4 py-3 mono text-xs text-text">{formatOdds(p.line)}</div>
                      <div className="px-4 py-3 mono text-xs font-semibold text-accent">
                        {(p.model_prob * 100).toFixed(1)}%
                      </div>
                      <div className="px-4 py-3 mono text-xs text-muted">
                        {(p.implied_prob * 100).toFixed(1)}%
                      </div>
                      <div className="px-4 py-3 mono text-xs font-semibold text-accent">
                        +{edge}%
                      </div>
                      <div className="px-4 py-3 mono text-xs">
                        {p.result ? (
                          <span className={p.result === 'W' ? 'text-win font-semibold' : p.result === 'L' ? 'text-loss font-semibold' : 'text-muted'}>
                            {p.result}
                          </span>
                        ) : (
                          <span className="text-muted">Pending</span>
                        )}
                      </div>
                    </div>
                  )
                })}
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
            '@type':    'ItemList',
            name:       `MLB Picks ${display}`,
            url:        `https://beezy.vip/games/mlb/${date}`,
            numberOfItems: picks.length,
          }),
        }}
      />
    </div>
  )
}
