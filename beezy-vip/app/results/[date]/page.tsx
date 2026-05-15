import { apiGetPicks }  from '@/lib/betting-api'
import { PicksTable }   from '@/components/picks/picks-table'
import { StatCard }     from '@/components/ui/primitives'
import { notFound }     from 'next/navigation'
import Link             from 'next/link'
import type { Metadata, ResolvingMetadata } from 'next'
import type { Bet }     from '@/lib/db'

type Props = { params: { date: string } }

export const dynamic = 'force-dynamic'

export const revalidate = 3600

function formatDate(d: string): string {
  try {
    return new Date(d + 'T12:00:00Z').toLocaleDateString('en-US', {
      weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
    })
  } catch { return d }
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return {
    title:       `Results ${formatDate(params.date)}`,
    description: `Beezy.VIP model results for ${formatDate(params.date)}. All systems P&L.`,
  }
}

export default async function ResultsByDatePage({ params }: Props) {
  const { date } = params
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) notFound()

  const picks = await apiGetPicks({ date }).catch(() => [])

  const settled  = picks.filter(p => p.result !== null)
  const wins     = settled.filter(p => p.result === 'win').length
  const losses   = settled.filter(p => p.result === 'loss').length
  const totalPnl = settled.reduce((s, p) => s + (p.profit ?? 0), 0)
  const display  = formatDate(date)

  // Prev/next date navigation
  const d       = new Date(date + 'T12:00:00Z')
  const prev    = new Date(d); prev.setDate(prev.getDate() - 1)
  const next    = new Date(d); next.setDate(next.getDate() + 1)
  const prevStr = prev.toISOString().split('T')[0]
  const nextStr = next.toISOString().split('T')[0]

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      {/* Breadcrumb + date nav */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-2 mono text-xs text-muted">
          <Link href="/results" className="hover:text-accent transition-colors">Results</Link>
          <span>·</span>
          <span className="text-text">{display}</span>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href={`/results/${prevStr}`}
            className="mono text-xs text-muted hover:text-accent transition-colors px-3 py-1.5 border border-[var(--border)] hover:border-accent"
          >
            ← Prev
          </Link>
          <Link
            href={`/results/${nextStr}`}
            className="mono text-xs text-muted hover:text-accent transition-colors px-3 py-1.5 border border-[var(--border)] hover:border-accent"
          >
            Next →
          </Link>
        </div>
      </div>

      <div className="mb-6">
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-1">
          Results · {display}
        </h1>
        <p className="mono text-xs text-muted">
          Paper mode · Past performance is not indicative of future results
        </p>
      </div>

      {/* Day summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 border border-[var(--border)] mb-8">
        <StatCard label="Total Picks"  value={String(picks.length)} />
        <StatCard
          label="Record"
          value={settled.length > 0 ? `${wins}-${losses}` : '—'}
        />
        <StatCard
          label="P&L"
          value={settled.length > 0 ? `${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}u` : '—'}
          accent={totalPnl > 0}
        />
        <StatCard label="Pending" value={String(picks.filter(p => !p.result).length)} />
      </div>

      {picks.length === 0 ? (
        <div className="border border-[var(--border)] p-12 text-center">
          <p className="mono text-xs text-muted">No picks found for {display}.</p>
        </div>
      ) : (
        <PicksTable picks={picks} />
      )}
    </div>
  )
}
