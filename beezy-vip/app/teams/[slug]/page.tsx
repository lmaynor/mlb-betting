export const dynamic = 'force-dynamic'

import { query }       from '@/lib/db'
import { SystemBadge } from '@/components/ui/primitives'
import { formatOdds }  from '@/lib/odds'
import { notFound }    from 'next/navigation'
import type { Metadata } from 'next'

type Props = { params: { slug: string } }

export const revalidate = 3600

const TEAM_MAP: Record<string, { name: string; abbr: string }> = {
  'new-york-yankees':    { name: 'New York Yankees',    abbr: 'NYY' },
  'boston-red-sox':      { name: 'Boston Red Sox',      abbr: 'BOS' },
  'los-angeles-dodgers': { name: 'Los Angeles Dodgers', abbr: 'LAD' },
  'houston-astros':      { name: 'Houston Astros',      abbr: 'HOU' },
  'atlanta-braves':      { name: 'Atlanta Braves',      abbr: 'ATL' },
  'new-york-mets':       { name: 'New York Mets',       abbr: 'NYM' },
  'philadelphia-phillies':{ name: 'Philadelphia Phillies', abbr: 'PHI' },
  'chicago-cubs':        { name: 'Chicago Cubs',        abbr: 'CHC' },
  'san-francisco-giants':{ name: 'San Francisco Giants',abbr: 'SF'  },
  'seattle-mariners':    { name: 'Seattle Mariners',    abbr: 'SEA' },
  'san-diego-padres':    { name: 'San Diego Padres',    abbr: 'SD'  },
  'texas-rangers':       { name: 'Texas Rangers',       abbr: 'TEX' },
}

export async function generateStaticParams() {
  return Object.keys(TEAM_MAP).map(slug => ({ slug }))
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const team = TEAM_MAP[params.slug]
  const name = team?.name ?? params.slug.replace(/-/g, ' ')
  return {
    title:       `${name} Betting Model History — Beezy.VIP`,
    robots:      { index: false, follow: false },
    description: `Beezy.VIP model performance when betting on or against the ${name}. NRFI, HR, F5, K, and OUTS system history.`,
  }
}

export default async function TeamPage({ params }: Props) {
  const team = TEAM_MAP[params.slug]
  if (!team) notFound()

  // Bets where this team was involved — match on bet_type containing the abbreviation
  const bets = await query<{
    system:       string
    game_date:    string
    game_pk:      number
    bet_type:     string
    line:         number
    model_prob:   number
    implied_prob: number
    result:       string | null
    pnl:          number | null
  }>(`
    SELECT system, game_date, game_pk, bet_type, line, model_prob, implied_prob, result, pnl
    FROM bets
    WHERE UPPER(bet_type) LIKE UPPER($1)
       OR game_pk IN (
         SELECT DISTINCT game_pk FROM bets
         WHERE UPPER(bet_type) LIKE UPPER($1)
       )
    ORDER BY game_date DESC
    LIMIT 60
  `, [`%${team.abbr}%`]).catch(() => [])

  const settled  = bets.filter(b => b.result !== null)
  const wins     = settled.filter(b => b.result === 'W').length
  const losses   = settled.filter(b => b.result === 'L').length
  const totalPnl = settled.reduce((s, b) => s + (b.pnl ?? 0), 0)
  const winRate  = wins + losses > 0 ? (wins / (wins + losses) * 100).toFixed(1) : '—'

  // Per-system breakdown
  const bySystem = settled.reduce<Record<string, { wins: number; losses: number; pnl: number }>>((acc, b) => {
    if (!acc[b.system]) acc[b.system] = { wins: 0, losses: 0, pnl: 0 }
    if (b.result === 'W') acc[b.system].wins++
    if (b.result === 'L') acc[b.system].losses++
    acc[b.system].pnl += b.pnl ?? 0
    return acc
  }, {})

  return (
    <div className="max-w-4xl mx-auto px-4 py-12">
      <div className="mb-8">
        <p className="mono text-xs text-accent uppercase tracking-widest mb-2">Teams · MLB</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">
          {team.name}
        </h1>
        <p className="mono text-xs text-muted">
          Beezy.VIP model history for {team.abbr} games
        </p>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-4 border border-[var(--border)] mb-8">
        <div className="p-4 border-r border-[var(--border)]">
          <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Model Bets</p>
          <p className="mono text-2xl font-extrabold">{bets.length}</p>
        </div>
        <div className="p-4 border-r border-[var(--border)]">
          <p className="mono text-xs text-muted uppercase tracking-widest mb-1">Record</p>
          <p className="mono text-2xl font-extrabold">
            <span className="text-win">{wins}</span>
            <span className="text-muted">-</span>
            <span className="text-loss">{losses}</span>
          </p>
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

      {/* Per-system */}
      {Object.keys(bySystem).length > 0 && (
        <div className="border border-[var(--border)] mb-8">
          <div className="grid grid-cols-4 border-b border-[var(--border)] bg-[var(--surface)]">
            {['System', 'W-L', 'Win Rate', 'P&L'].map(h => (
              <div key={h} className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
            ))}
          </div>
          {Object.entries(bySystem).map(([system, s]) => {
            const wr = s.wins + s.losses > 0 ? (s.wins / (s.wins + s.losses) * 100).toFixed(1) : '—'
            return (
              <div key={system} className="grid grid-cols-4 border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors">
                <div className="px-4 py-3"><SystemBadge system={system} /></div>
                <div className="px-4 py-3 mono text-sm font-semibold">
                  <span className="text-win">{s.wins}</span>
                  <span className="text-muted">-</span>
                  <span className="text-loss">{s.losses}</span>
                </div>
                <div className="px-4 py-3 mono text-xs text-text">{wr}%</div>
                <div className={`px-4 py-3 mono text-sm font-semibold ${s.pnl >= 0 ? 'text-win' : 'text-loss'}`}>
                  {s.pnl >= 0 ? '+' : ''}{s.pnl.toFixed(2)}u
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Bet history */}
      {bets.length === 0 ? (
        <div className="border border-[var(--border)] p-12 text-center">
          <p className="mono text-xs text-muted">No model history found for {team.name}.</p>
        </div>
      ) : (
        <div className="border border-[var(--border)] overflow-x-auto">
          <div
            className="grid min-w-[650px] border-b border-[var(--border)] bg-[var(--surface)]"
            style={{ gridTemplateColumns: '0.7fr 0.7fr 1.2fr 0.7fr 0.7fr 0.7fr' }}
          >
            {['Date', 'System', 'Pick', 'Line', 'Result', 'P&L'].map(h => (
              <div key={h} className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
            ))}
          </div>
          {bets.map((b, i) => (
            <div
              key={i}
              className="grid min-w-[650px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
              style={{ gridTemplateColumns: '0.7fr 0.7fr 1.2fr 0.7fr 0.7fr 0.7fr' }}
            >
              <div className="px-4 py-3 mono text-xs text-muted">
                {new Date(b.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
              </div>
              <div className="px-4 py-3"><SystemBadge system={b.system} /></div>
              <div className="px-4 py-3 mono text-xs text-text">{b.bet_type}</div>
              <div className="px-4 py-3 mono text-xs text-text">{formatOdds(b.line)}</div>
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
          ))}
        </div>
      )}

      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            '@context':  'https://schema.org',
            '@type':     'SportsOrganization',
            name:        team.name,
            sport:       'Baseball',
            url:         `https://beezy.vip/teams/${params.slug}`,
          }),
        }}
      />
    </div>
  )
}
