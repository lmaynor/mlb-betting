import Link from 'next/link'

export type GamesView = 'board' | 'table'
export type GameSport = 'all' | 'nba' | 'nhl'

export const SPORT_TABS: { key: GameSport; label: string }[] = [
  { key: 'all', label: 'All sports' },
  { key: 'nba', label: 'NBA' },
  { key: 'nhl', label: 'NHL' },
]
const VIEW_TABS: { key: GamesView; label: string; title: string }[] = [
  { key: 'board', label: 'BOARD', title: 'One row per game with the best price at every market; double-click a game for its card' },
  { key: 'table', label: 'ALL LINES', title: 'Every game, market and line in one table, with a column per book' },
]

/** `table` (and the old `prices` links) open the all-lines table; everything else, including the retired `model` and `analysis` views, is the board. */
export function parseGamesView(v: string | undefined): GamesView {
  return v === 'table' || v === 'prices' ? 'table' : 'board'
}

/** The game an old analysis link points at (?view=analysis&game=KEY, or a bare ?game=KEY): the board opens that game's card on its Analysis tab. */
export function analysisGame(v: string | undefined, game: string | undefined): string | null {
  return game && (v === 'analysis' || !v) ? game : null
}

export function parseGameSport(v: string | undefined): GameSport {
  return v === 'nba' || v === 'nhl' ? v : 'all'
}

/** A shareable /ev/games URL: the view and the sport live in the query string, never in client state. BOARD and ALL are the defaults and stay out of it. */
export function gamesHref(view: GamesView, sport: GameSport): string {
  const sp = new URLSearchParams()
  if (view === 'table') sp.set('view', 'table')
  if (sport !== 'all') sp.set('sport', sport)
  const qs = sp.toString()
  return qs ? `/ev/games?${qs}` : '/ev/games'
}

/** The page toolbar's right side: the BOARD | ALL LINES switch and, on the table, the sport links (the games board has its own sport pills). Plain links, so each state is shareable. */
export function GamesNav({ view, sport }: { view: GamesView; sport: GameSport }) {
  return (
    <div className="flex items-center gap-y-2 gap-x-4 flex-wrap">
      <nav aria-label="Game lines view" className="inline-flex gap-0.5 p-[3px] bg-background-secondary-default border border-separator-border rounded-control">
        {VIEW_TABS.map(t => (
          <Link
            key={t.key}
            href={gamesHref(t.key, sport)}
            title={t.title}
            aria-current={view === t.key ? 'page' : undefined}
            className="mono text-[11.5px] font-semibold tracking-[0.03em] py-[5px] px-[11px] no-underline"
            style={{
              borderRadius: '6px',
              color: view === t.key ? '#ffffff' : 'var(--color-neutral-300)',
              background: view === t.key ? 'var(--color-background-tertiary-default)' : 'transparent',
              border: `1px solid ${view === t.key ? 'var(--color-border-button-hover)' : 'transparent'}`,
            }}
          >{t.label}</Link>
        ))}
      </nav>
      {view === 'table' && <nav aria-label="Game lines sport" className="flex gap-3.5 text-[12.5px]">
        {SPORT_TABS.map(t => (
          <Link className="no-underline"
            key={t.key}
            href={gamesHref(view, t.key)}
            aria-current={sport === t.key ? 'page' : undefined}
            style={{ color: sport === t.key ? 'var(--color-text-primary)' : 'var(--link)', fontWeight: sport === t.key ? 600 : 400 }}
          >{t.label}</Link>
        ))}
      </nav>}
    </div>
  )
}
