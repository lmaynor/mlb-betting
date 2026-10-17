import { Suspense } from 'react'
import { DateBar } from '@/components/picks/date-bar'
import { FilterBar } from '@/components/picks/filter-bar'
import { LockedNotice } from '@/components/picks/locked-notice'
import { PicksTable } from '@/components/picks/picks-table'
import { ScoreLegend } from '@/components/ui/score-legend'
import { limitRows } from '@/lib/access'
import { getViewer } from '@/lib/auth'
import { SportTabs } from '@/components/picks/sport-tabs'
import { SystemsFooter } from '@/components/picks/systems-footer'
import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import { addDaysToDateKey, formatDateKey, siteDateKey } from '@/lib/dates'
import { collapseBooks } from '@/lib/dedupe'
import { beezyscore } from '@/lib/beezy-score'
import { betKind, type PickKind } from '@/lib/pick-systems'
import type { SportSlug } from '@/lib/sports'
import { matchesBook, matchesQuery, matchesTier, parsePickFilters } from '@/lib/picks-filters'
import type { Bet } from '@/lib/types'

const COPY: Record<PickKind, { h1: string; sub: string; what: string; plan: string }> = {
  player: {
    h1: 'MLB player picks',
    sub: 'Model picks + +EV alerts, one row per prop, ranked by Beezy Score',
    what: 'MLB player picks',
    plan: 'The full card is on the MLB Pass or All-Access Pro.',
  },
  game: {
    h1: 'Game picks',
    sub: 'MLB NRFI, first-5, first-half and full-game sides, ranked by Beezy Score',
    what: 'MLB game picks',
    plan: 'Game picks are on the MLB Pass or All-Access Pro.',
  },
}

async function PicksContent({ kind, searchParams }: { kind: PickKind; searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams

  const explicitDate = sp.date && /^\d{4}-\d{2}-\d{2}$/.test(sp.date) ? sp.date : undefined
  const today = siteDateKey()
  const date = explicitDate ?? today
  // The API knows pending|settled|won|lost; the chips say WIN/LOSS.
  const STATUS_MAP: Record<string, string> = { win: 'won', loss: 'lost', pending: 'pending' }
  const status = sp.status && sp.status !== 'ALL' ? STATUS_MAP[sp.status.toLowerCase()] : undefined
  const f = parsePickFilters(sp)
  const { market, book, sort, dir } = f

  // Tier, "my books" and the text search are cut here after the fetch (the API filters on system, date, status and one book).
  const keep = (p: Bet) => matchesTier(beezyscore(p), f.tier) && matchesBook(p.book, { book: undefined, books: f.books })
    && matchesQuery(`${p.player ?? ''} ${p.away_team ?? ''} ${p.home_team ?? ''}`, f.q)
  const load = (d: string) => getPicks({ system: market, date: d, status, book, limit: 200 })
    .then(rows => rows.filter(p => betKind(p) === kind && keep(p)))
    .catch(() => [] as Bet[])

  let picks = await load(date)

  // Overnight gap: the site date rolls at midnight CT but today's card only publishes after the ~11:00 CT morning run.
  // An empty "today" with no explicit date falls back to the latest card instead of a blank page, clearly labeled.
  let shownDate = date
  if (!explicitDate && picks.length === 0) {
    const yesterday = addDaysToDateKey(date, -1)
    const prior = await load(yesterday)
    if (prior.length > 0) {
      picks = prior
      shownDate = yesterday
    }
  }

  // Settled days are public so the record stays checkable; only today's (and later) card is the paid surface.
  const viewer = await getViewer()
  const rows = book ? picks : collapseBooks(picks)
  const limited = shownDate >= today
    ? limitRows(rows, viewer, kind === 'player' ? 'player_board' : 'game_picks', 'mlb', beezyscore)
    : { rows, locked: 0, full: true }

  return (
    <>
      {shownDate !== date && (
        <div style={{ marginBottom: '16px', padding: '10px 14px', border: '1px solid var(--iron)', borderRadius: 'var(--radius)', background: 'var(--obsidian)' }}>
          <p className="mono" style={{ fontSize: '11px', color: 'var(--warn)', margin: 0 }}>
            Showing {formatDateKey(shownDate, { weekday: 'short', month: 'short', day: 'numeric' })}. Today&rsquo;s model card publishes after the ~11:00 AM CT run; +EV alerts post through the day.
          </p>
        </div>
      )}
      {/* keyed on the query so the table's local sort/page state resets whenever a filter changes */}
      {limited.rows.length > 0 && <PicksTable key={[date, market, status, book, f.books.join(','), f.tier, f.q, sort, dir].join('|')} bets={limited.rows} sort={sort} dir={dir} />}
      <LockedNotice locked={limited.locked} shown={limited.rows.length} what={COPY[kind].what} plan={COPY[kind].plan} />
    </>
  )
}

/** MLB's tracked card, split by what is being picked: a player's stat (`player`) or a game outcome (`game`). */
export async function PicksCard({ sport = 'mlb', kind, searchParams }: { sport?: SportSlug; kind: PickKind; searchParams: Promise<Record<string, string>> }) {
  const copy = COPY[kind]
  const stats = await apiGetStats().then(r => r.bySystem).catch(() => [])
  return (
    <div>
      {kind === 'player' && <SportTabs sport={sport} active="picks" />}
      <DateBar />
      <FilterBar sport={sport} kind={kind} />
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '32px 24px' }}>
        <div style={{ marginBottom: '14px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>{copy.h1}</h1>
          <span style={{ display: 'flex', gap: '16px', alignItems: 'baseline', flexWrap: 'wrap' }}>
            <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>{copy.sub}</span>
            <a href={kind === 'player' ? '/picks/games' : '/picks/mlb'} style={{ fontSize: '12.5px', color: 'var(--link)' }}>{kind === 'player' ? 'Game picks' : 'Player picks'} &rarr;</a>
            <a href="/ev" style={{ fontSize: '12.5px', color: 'var(--link)' }}>+EV Terminal &rarr;</a>
          </span>
        </div>
        <Suspense fallback={
          <div style={{ padding: '48px', textAlign: 'center', border: '1px solid var(--basalt)', borderRadius: 'var(--radius-lg)', background: 'var(--graphite)' }}>
            <p className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>Loading picks...</p>
          </div>
        }>
          <PicksContent kind={kind} searchParams={searchParams} />
        </Suspense>
        <ScoreLegend />
        <SystemsFooter sport={sport} kind={kind === 'game' ? 'game' : undefined} stats={stats} />
      </div>
    </div>
  )
}
