import Link from 'next/link'

export type GamesView = 'prices' | 'model'
export type GameSport = 'all' | 'nba' | 'nhl' | 'soccer'

export const SPORT_TABS: { key: GameSport; label: string }[] = [
  { key: 'all', label: 'NBA + NHL' },
  { key: 'nba', label: 'NBA' },
  { key: 'nhl', label: 'NHL' },
  { key: 'soccer', label: 'SOCCER (EPL / UCL)' },
]
const VIEW_TABS: { key: GamesView; label: string; title: string }[] = [
  { key: 'prices', label: 'PRICES', title: 'Every book\'s moneyline, spread and total side by side, with best price, fair value and arbitrage' },
  { key: 'model', label: 'MODEL', title: 'The model\'s projection against the market' },
]

export function parseGamesView(v: string | undefined): GamesView {
  return v === 'model' ? 'model' : 'prices'
}

export function parseGameSport(v: string | undefined): GameSport {
  return v === 'nba' || v === 'nhl' || v === 'soccer' ? v : 'all'
}

/** A shareable /ev/games URL: the view and the sport live in the query string, never in client state. PRICES and ALL are the defaults and stay out of it. */
export function gamesHref(view: GamesView, sport: GameSport): string {
  const sp = new URLSearchParams()
  if (view === 'model') sp.set('view', 'model')
  if (sport !== 'all') sp.set('sport', sport)
  const qs = sp.toString()
  return qs ? `/ev/games?${qs}` : '/ev/games'
}

/** The page toolbar's right side: the PRICES | MODEL switch and the sport links. Both are plain links so each state is shareable. */
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
      <nav aria-label="Game lines sport" className="flex gap-3.5 text-[12.5px]">
        {SPORT_TABS.map(t => (
          <Link className="no-underline"
            key={t.key}
            href={gamesHref(view, t.key)}
            aria-current={sport === t.key ? 'page' : undefined}
            style={{ color: sport === t.key ? 'var(--color-text-primary)' : 'var(--link)', fontWeight: sport === t.key ? 600 : 400 }}
          >{t.label}</Link>
        ))}
      </nav>
    </div>
  )
}
