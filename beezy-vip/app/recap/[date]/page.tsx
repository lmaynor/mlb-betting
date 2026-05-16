export const dynamic = 'force-dynamic'

import { query }       from '@/lib/db'
import { SystemBadge } from '@/components/ui/primitives'
import { ogUrl }       from '@/lib/og'
import { notFound }    from 'next/navigation'
import type { Metadata } from 'next'

type Props = { params: { date: string } }

export const revalidate = 3600

function formatDate(d: string): string {
  try {
    return new Date(d + 'T12:00:00Z').toLocaleDateString('en-US', {
      weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
    })
  } catch { return d }
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const display = formatDate(params.date)
  return {
    title:       `MLB Betting Recap ${display}`,
    robots:      { index: false, follow: false },
    description: `Beezy.VIP model results for ${display}. All systems: picks, results, and P&L breakdown.`,
    openGraph: {
      images: [{ url: ogUrl({ title: `Recap ${params.date}`, sub: 'Daily Results · Beezy.VIP' }), width: 1200, height: 630 }],
    },
  }
}

export default async function RecapPage({ params }: Props) {
  const { date } = params

  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) notFound()

  const bets = await query<{
    system:       string
    bet_type:     string
    game_pk:      number
    line:         number
    model_prob:   number
    result:       string | null
    pnl:          number | null
  }>(`
    SELECT system, bet_type, game_pk, line, model_prob, result, pnl
    FROM bets
    WHERE game_date = $1 AND result IS NOT NULL
    ORDER BY system, result
  `, [date]).catch(() => [])

  const display  = formatDate(date)
  const wins     = bets.filter(b => b.result === 'W').length
  const losses   = bets.filter(b => b.result === 'L').length
  const totalPnl = bets.reduce((s, b) => s + (b.pnl ?? 0), 0)

  // Group by system for summary
  const bySystem = bets.reduce<Record<string, { wins: number; losses: number; pnl: number }>>((acc, b) => {
    if (!acc[b.system]) acc[b.system] = { wins: 0, losses: 0, pnl: 0 }
    if (b.result === 'W') acc[b.system].wins++
    if (b.result === 'L') acc[b.system].losses++
    acc[b.system].pnl += b.pnl ?? 0
    return acc
  }, {})

  // Auto-generated summary sentence
  const summaryLine = bets.length === 0
    ? 'No qualifying picks posted for this date.'
    : `Beezy.VIP went ${wins}-${losses} on ${bets.length} picks, finishing the day ${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)} units.`

  return (
    <div className="max-w-4xl mx-auto px-4 py-12">
      <div className="mb-8">
        <p className="mono text-xs text-accent uppercase tracking-widest mb-2">Daily Recap</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-3">
          MLB Betting Recap — {display}
        </h1>
        <p className="text-muted text-sm leading-relaxed">{summaryLine}</p>
      </div>

      {/* Day summary */}
      <div className="grid grid-cols-3 border border-[var(--border)] mb-8">
        <div className="p-4 border-r border-[var(--border)]">
          <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Record</p>
          <p className="mono text-2xl font-extrabold">
            <span className="text-win">{wins}</span>
            <span className="text-muted">-</span>
            <span className="text-loss">{losses}</span>
          </p>
        </div>
        <div className="p-4 border-r border-[var(--border)]">
          <p className="mono text-xs text-muted uppercase tracking-widest mb-1">P&L</p>
          <p className={`mono text-2xl font-extrabold ${totalPnl >= 0 ? 'text-win' : 'text-loss'}`}>
            {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}u
          </p>
        </div>
        <div className="p-4">
          <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Systems</p>
          <p className="mono text-2xl font-extrabold">{Object.keys(bySystem).length}</p>
        </div>
      </div>

      {/* System breakdown */}
      {Object.keys(bySystem).length > 0 && (
        <div className="border border-[var(--border)] mb-8">
          <div className="grid grid-cols-4 border-b border-[var(--border)] bg-[var(--surface)]">
            {['System', 'W-L', 'P&L', ''].map(h => (
              <div key={h} className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
            ))}
          </div>
          {Object.entries(bySystem).map(([system, s]) => (
            <div key={system} className="grid grid-cols-4 border-b border-[var(--border)]">
              <div className="px-4 py-3"><SystemBadge system={system} /></div>
              <div className="px-4 py-3 mono text-sm font-semibold">
                <span className="text-win">{s.wins}</span>
                <span className="text-muted">-</span>
                <span className="text-loss">{s.losses}</span>
              </div>
              <div className={`px-4 py-3 mono text-sm font-semibold ${s.pnl >= 0 ? 'text-win' : 'text-loss'}`}>
                {s.pnl >= 0 ? '+' : ''}{s.pnl.toFixed(2)}u
              </div>
              <div className="px-4 py-3" />
            </div>
          ))}
        </div>
      )}

      {/* Full pick list */}
      {bets.length > 0 && (
        <div className="border border-[var(--border)] overflow-x-auto">
          <div className="grid grid-cols-5 min-w-[500px] border-b border-[var(--border)] bg-[var(--surface)]">
            {['System', 'Pick', 'Line', 'Result', 'P&L'].map(h => (
              <div key={h} className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
            ))}
          </div>
          {bets.map((b, i) => (
            <div key={i} className="grid grid-cols-5 min-w-[500px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors">
              <div className="px-4 py-3"><SystemBadge system={b.system} /></div>
              <div className="px-4 py-3 mono text-xs text-text">{b.bet_type}</div>
              <div className="px-4 py-3 mono text-xs text-text">{b.line > 0 ? '+' : ''}{b.line}</div>
              <div className="px-4 py-3 mono text-xs font-semibold">
                <span className={b.result === 'W' ? 'text-win' : 'text-loss'}>{b.result}</span>
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

      {/* Disclaimer */}
      <p className="mono text-xs text-muted mt-8">
        All figures are paper-mode results. Past performance is not indicative of future results.
      </p>

      {/* Structured data */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            '@context':   'https://schema.org',
            '@type':      'Article',
            headline:     `MLB Betting Recap ${display}`,
            datePublished: date,
            url:          `https://beezy.vip/recap/${date}`,
            publisher: {
              '@type': 'Organization',
              name:    'Beezy.VIP',
              url:     'https://beezy.vip',
            },
          }),
        }}
      />
    </div>
  )
}
