export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
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
          <Link className={PILL_CLASS} href={`/ev/projections?league=${league}`} aria-current={view === 'props' ? 'page' : undefined} style={pill(view === 'props')}>Player props</Link>
          <Link className={PILL_CLASS} href="/ev/projections?view=games" aria-current={view === 'games' ? 'page' : undefined} style={pill(view === 'games')}>Game lines</Link>
        </nav>
        {view === 'props' ? (
          <nav aria-label="League" className={GROUP_CLASS}>
            {LIVE_SPORTS.map(p => (
              <Link className={PILL_CLASS} key={p.slug} href={`/ev/projections?league=${p.slug}`} aria-current={p.slug === league ? 'page' : undefined} style={pill(p.slug === league)}>{p.shortName}</Link>
            ))}
          </nav>
        ) : (
          <nav aria-label="Game line sport" className={GROUP_CLASS}>
            {GAME_SPORTS.map(t => (
              <Link className={PILL_CLASS} key={t.key} href={t.key === 'all' ? '/ev/projections?view=games' : `/ev/projections?view=games&sport=${t.key}`} aria-current={gameSport === t.key ? 'page' : undefined} style={pill(gameSport === t.key)}>{t.label}</Link>
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
          <GameLinesClient key={gameSport} feeds={{ nba: cut(nba), nhl: cut(nhl) }} sport={gameSport} />
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
  const rows = applyPickFilters(board.rows, f)
  const games = withCounts(board.games, board.rows)
  const filtered = activeFilterCount(f) > 0
  const today = siteDateKey()

  return (
    <main>
      {header(`${rows.length} ${rows.length === 1 ? 'projection' : 'projections'}${filtered ? ' after filters' : ''}`)}
      <AlignedBar>
        <DayToggle today={today} yesterday={board.yesterday} days={board.days} selected={board.date} pinDates={league !== 'mlb'} />
      </AlignedBar>
      <GamesStrip games={games} selected={f.game} today={today} total={board.rows.length} />
      <FilterBar sport={league} kind={league === 'mlb' ? 'player' : undefined} />
      <div className="max-w-page mx-auto p-6">
        <SportProjectionsBoard sport={league} rows={rows} status={board.status} statusWarn={board.statusWarn} visuals
          emptyBody={board.rows.length === 0 ? board.emptyBody : 'No rows match these filters. Clear a filter, or pick another game.'} />
        <LockedNotice locked={board.locked} shown={rows.length} what={`${pillar.shortName} projections`} plan={`The full board is on the ${pillar.shortName} Pass or All-Access Pro.`} />
        <EdgeEvLegend />
        <p className="text-[12px] text-text-secondary leading-[1.55] max-w-[90ch]" style={{ margin: '14px 2px 0' }}>
          MODEL is the projected value for the stat (the lambda behind the price) and the probability it goes over the line. RANGE draws that value against the line inside the model&rsquo;s own
          10th-90th percentile band. Rows with no line yet are projections only; nothing here is a recommendation, and {league === 'mlb'
            ? 'only the highest-edge MLB plays are tracked in the public results'
            : `${pillar.shortName} projections are unvalidated`}. The plays that clear the official gate are on the <Link className="text-link" href="/cheat-sheet">Daily Card</Link>.
        </p>
      </div>
    </main>
  )
}
