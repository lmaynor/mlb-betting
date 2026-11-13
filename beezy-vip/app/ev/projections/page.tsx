export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
import { redirect } from 'next/navigation'
import { AlignedBar } from '@/components/picks/aligned-bar'
import { DayToggle } from '@/components/picks/day-toggle'
import { FilterBar } from '@/components/picks/filter-bar'
import { GamesStrip } from '@/components/picks/games-strip'
import { LockedNotice } from '@/components/picks/locked-notice'
import { SportProjectionsBoard } from '@/components/picks/sport-projections-board'
import { EdgeEvLegend } from '@/components/ui/edge-ev-legend'
import { limitGameLines } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { apiGetGameProjections } from '@/lib/betting-api'
import { withCounts } from '@/lib/board-games'
import { applyPickFilters } from '@/lib/board-rows'
import { siteDateKey } from '@/lib/dates'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import { activeFilterCount, parsePickFilters } from '@/lib/picks-filters'
import { loadSportBoard } from '@/lib/sport-board-data'
import { isSportSlug, LIVE_SPORTS, SPORT_PILLARS, type SportSlug } from '@/lib/sports'
import { GameLinesClient } from '../games/game-lines-client'
import { parseGameSport, type GameSport } from '../games/games-nav'

export const metadata: Metadata = {
  title: 'Projections',
  description: "The model's expected value (lambda) for every player prop and every NBA / NHL game line, drawn against the line and the market. Unvalidated model output.",
}

type View = 'props' | 'games'
type Props = { searchParams: Promise<Record<string, string>> }

const GAME_SPORTS: { key: GameSport; label: string }[] = [{ key: 'all', label: 'NBA + NHL' }, { key: 'nba', label: 'NBA' }, { key: 'nhl', label: 'NHL' }]

const pill = (on: boolean): React.CSSProperties => ({
  borderRadius: '6px',
  color: on ? '#ffffff' : 'var(--color-neutral-300)',
  background: on ? 'var(--color-background-tertiary-default)' : 'transparent',
  border: `1px solid ${on ? 'var(--color-border-button-hover)' : 'transparent'}`,
})
const PILL_CLASS = 'text-compact font-semibold py-[7px] px-[13px] no-underline whitespace-nowrap'
const GROUP_CLASS = 'inline-flex gap-0.5 p-[3px] bg-background-secondary-default border border-separator-border rounded-control'

/**
 * The Scanner's Projections tab: the model's expected value (lambda) for everything the site prices, beside what the market says.
 * PLAYER PROPS is the projection table (every prop on the slate for one league, with lambda drawn against the line and the model's own
 * range); GAME LINES is the NBA / NHL game engines (expected score, margin and total against the books). Both are UNVALIDATED model output
 * with no tested edge, which is why they live in the Scanner beside the market data and not under Picks (the tracked, graded record).
 * `?league=` picks the prop league (the shared filter bar owns `sport`), `?view=games&sport=` the game-line sport.
 */
export default async function ProjectionsPage({ searchParams }: Props) {
  const sp = await searchParams
  // Scanner URLs carry no ?tier= or ?result= (lib/ia.ts URL standard): strip them on arrival, keeping every
  // other param, so a shared picks-board link lands on the same filtered board under its canonical URL.
  if ('tier' in sp || 'result' in sp) {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(sp)) if (k !== 'tier' && k !== 'result' && v != null) q.set(k, v)
    const s = q.toString()
    redirect(s ? `/ev/projections?${s}` : '/ev/projections')
  }
  // Pill hrefs preserve the shared filter params (F-J1): only the pill's own keys change.
  const pillHref = (over: Record<string, string | undefined>): string => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(sp)) if (k !== 'tier' && k !== 'result' && v != null) q.set(k, v)
    for (const [k, v] of Object.entries(over)) { if (v == null) q.delete(k); else q.set(k, v) }
    const s = q.toString()
    return s ? `/ev/projections?${s}` : '/ev/projections'
  }
  const view: View = sp.view === 'games' ? 'games' : 'props'
  const league: SportSlug = sp.league && isSportSlug(sp.league) ? sp.league : 'nfl'
  const gameSport = parseGameSport(sp.sport)
  const viewer = await getViewer()

  const header = (count: string) => (
    <header className="max-w-page mx-auto pt-5 px-6 pb-3.5">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h1 className="text-[20px] font-bold text-text-primary m-0 tracking-[-0.01em]">Projections</h1>
        <span className="mono text-compact text-text-secondary">{count}</span>
      </div>
      <div className="flex gap-3 flex-wrap items-center mt-3.5">
        <nav aria-label="Projection type" className={GROUP_CLASS}>
          <Link className={PILL_CLASS} href={pillHref({ view: undefined, sport: undefined, league })} aria-current={view === 'props' ? 'page' : undefined} style={pill(view === 'props')}>Player props</Link>
          <Link className={PILL_CLASS} href={pillHref({ view: 'games', league: undefined })} aria-current={view === 'games' ? 'page' : undefined} style={pill(view === 'games')}>Game lines</Link>
        </nav>
        {view === 'props' ? (
          <nav aria-label="League" className={GROUP_CLASS}>
            {LIVE_SPORTS.map(p => (
              <Link className={PILL_CLASS} key={p.slug} href={pillHref({ league: p.slug, view: undefined, sport: undefined })} aria-current={p.slug === league ? 'page' : undefined} style={pill(p.slug === league)}>{p.shortName}</Link>
            ))}
          </nav>
        ) : (
          <nav aria-label="Game line sport" className={GROUP_CLASS}>
            {GAME_SPORTS.map(t => (
              <Link className={PILL_CLASS} key={t.key} href={pillHref({ view: 'games', sport: t.key === 'all' ? undefined : t.key, league: undefined })} aria-current={gameSport === t.key ? 'page' : undefined} style={pill(gameSport === t.key)}>{t.label}</Link>
            ))}
          </nav>
        )}
      </div>
    </header>
  )

  if (view === 'games') {
    const wants = (s: 'nba' | 'nhl') => gameSport === 'all' || gameSport === s
    const [nba, nhl] = await Promise.all([
      wants('nba') ? apiGetGameProjections('nba').catch(() => null) : Promise.resolve(null),
      wants('nhl') ? apiGetGameProjections('nhl').catch(() => null) : Promise.resolve(null),
    ])
    // Same tier rule as the Odds Terminal (limitGameLines): cut before the payload leaves the server.
    const cut = (feed: GameProjectionsFeed | null): GameProjectionsFeed | null => {
      if (!feed) return null
      const c = limitGameLines(feed, viewer)
      return { ...c.feed, locked: c.locked }
    }
    return (
      <main>
        {header('NBA and NHL game engines')}
        <div className="max-w-page mx-auto px-6 pb-6">
          <GameLinesClient key={gameSport} feeds={{ nba: cut(nba), nhl: cut(nhl) }} sport={gameSport === 'soccer' || gameSport === 'ncaab' ? 'all' : gameSport} />
          <p className="text-[12px] text-text-secondary leading-[1.55] max-w-[90ch]" style={{ margin: '14px 2px 0' }}>
            The bar on each game is the model&rsquo;s expected score. NHL uses its goal rates (lambda for each team); NBA splits the modelled total by the modelled margin.
            Model % is the model&rsquo;s probability for the side; market fair is the books&rsquo; probability with the vig removed. Every price for these games is on{' '}
            <Link className="text-link" href="/ev/games">Game Lines</Link>.
          </p>
        </div>
      </main>
    )
  }

  const pillar = SPORT_PILLARS[league]
  const f = parsePickFilters(sp)
  const board = await loadSportBoard(league, viewer, { day: f.day, date: f.date, view: 'projections', kind: league === 'mlb' ? 'player' : undefined })
  // T-2: league-level stat categories, the day's stat markets for this league as pills over the census
  const statCounts = new Map<string, { label: string; n: number }>()
  for (const r of board.rows) {
    const cur = statCounts.get(r.system)
    if (cur) cur.n++
    else statCounts.set(r.system, { label: r.marketLabel, n: 1 })
  }
  const statPills = [...statCounts.entries()].sort((a, b) => b[1].n - a[1].n)
  const rows = applyPickFilters(board.rows, f)
  const games = withCounts(board.games, board.rows)
  const filtered = activeFilterCount(f) > 0
  const today = siteDateKey()

  return (
    <main>
      {header(`${rows.length} ${rows.length === 1 ? 'projection' : 'projections'}${filtered ? ' after filters' : ''}`)}
      {statPills.length > 1 && (
        <div className="mx-auto flex max-w-page flex-wrap items-center gap-1.5 px-6 pb-3">
          <span className="label mr-1">Stat</span>
          <Link href={pillHref({ market: undefined })} className={PILL_CLASS} style={pill(!f.market)}>All stats</Link>
          {statPills.map(([system, s]) => (
            <Link key={system} href={pillHref({ market: system })} className={PILL_CLASS} style={pill(f.market === system)}>
              {s.label} <span className="opacity-60">{s.n}</span>
            </Link>
          ))}
        </div>
      )}
      {(board.yesterday || board.days.length > 0) && (
        <AlignedBar>
          <DayToggle today={today} yesterday={board.yesterday} days={board.days} selected={board.date} pinDates={league !== 'mlb'} />
        </AlignedBar>
      )}
      <GamesStrip games={games} selected={f.game} today={today} total={board.rows.length} />
      <FilterBar sport={league} kind={league === 'mlb' ? 'player' : undefined} />
      <div className="max-w-page mx-auto p-6">
        <SportProjectionsBoard sport={league} rows={rows} status={board.status} statusWarn={board.statusWarn} visuals
          emptyBody={board.rows.length === 0 ? board.emptyBody : 'No rows match these filters. Clear a filter, or pick another game.'} />
        <LockedNotice locked={board.locked} shown={rows.length} what={`${pillar.shortName} projections`} plan={`The full board is on the ${pillar.shortName} Pass or All-Access Pro.`} />
        <EdgeEvLegend />
        <p className="text-[12px] text-text-secondary leading-[1.55] max-w-[90ch]" style={{ margin: '14px 2px 0' }}>
          Model is the projected value for the stat (the lambda behind the price) and the probability it goes over the line. Range draws that value against the line inside the model&rsquo;s own
          10th-90th percentile band. Rows with no line yet are projections only; {league === 'mlb'
            ? 'only the highest-edge MLB plays are tracked in the public results'
            : `${pillar.shortName} projections are unvalidated`}. The plays that clear the official gate are on the <Link className="text-link" href="/cheat-sheet">Daily Card</Link>.
        </p>
      </div>
    </main>
  )
}
