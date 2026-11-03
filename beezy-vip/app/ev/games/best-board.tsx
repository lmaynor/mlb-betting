'use client'

import { useMemo, useState } from 'react'
import { LockedNotice } from '@/components/picks/locked-notice'
import { BookLogo } from '@/components/ui/book-logo'
import { useIsMobile } from '@/components/ui/mobile-sheet'
import { SportLogo } from '@/components/ui/sport-assets'
import { BOARD_SORTS, buildGameBoard, sortBoard, sportsWithGames, type BoardSort, type GameBoardEntry } from '@/lib/game-board'
import { ageText, GAME_MIN_REF, GAME_PRICES_LABEL, GAME_PRICES_NOTE, gameOddsUnavailable, startLabel, type GameMarket, type GameOddsFeed } from '@/lib/game-odds'
import { edgesNow, gameEdges, primaryMove, shopMarket, type EdgeItem, type ShopSide } from '@/lib/game-shop'
import { moveForSide } from '@/lib/line-move'
import { formatOdds } from '@/lib/odds'
import { useBankroll } from '@/lib/use-bankroll'
import { useMyBooks } from '@/lib/use-my-books'
import { GamePopup, type PopupPane } from './game-popup'
import { GREEN, HAIR, Pill, PillGroup, Select, signedPct } from './game-prices-parts'
import { Sparkline } from './sparkline'
import { useGameFeeds, type GameBoardFeeds } from './use-game-feeds'

type LogoSport = Parameters<typeof SportLogo>[0]['sport']

/** One side of a main line in a table cell: its label, then the best price at its book. The EV chip appears only for a real edge. */
function SideCell({ s }: { s: ShopSide }) {
  return (
    <span className="flex items-center gap-1.5 whitespace-nowrap leading-[20px]">
      <span className="text-[11px] text-text-tertiary min-w-[52px]">{s.label}</span>
      {s.american != null && s.book != null ? (
        <>
          <BookLogo book={s.book} size={14} />
          <b className="mono text-[12.5px] font-semibold" style={{ color: s.actionable ? GREEN : 'var(--color-text-primary)' }}>{formatOdds(s.american)}</b>
          {s.actionable && s.ev != null && <span className="mono text-[10px] font-semibold rounded-chip px-1" style={{ color: GREEN, background: 'rgba(87,242,135,0.10)' }}>{signedPct(s.ev)}</span>}
        </>
      ) : <span className="text-text-tertiary">--</span>}
    </span>
  )
}

function MarketCell({ g, market }: { g: GameBoardEntry; market: GameMarket }) {
  const sm = shopMarket(g, market)
  if (!sm) return <span className="text-text-tertiary">--</span>
  return <span className="grid"><SideCell s={sm.a} /><SideCell s={sm.b} /></span>
}

function MoveCell({ g }: { g: GameBoardEntry }) {
  const mv = primaryMove(g)
  if (!mv) return <span className="text-text-tertiary">--</span>
  const f = moveForSide(mv.m, mv.side)
  const values = f.pSeries.length >= 2 ? f.pSeries : mv.m.lineSeries.filter((x): x is number => x != null)
  const text = mv.m.lineMoved && mv.m.lineOpen != null && mv.m.lineNow != null
    ? `${mv.market === 'total' ? 'O/U ' : `${g.home} `}${mv.m.lineOpen} → ${mv.m.lineNow}`
    : f.pDelta != null ? `${f.pDelta >= 0 ? '▲' : '▼'} ${Math.abs(f.pDelta * 100).toFixed(1)} pts` : ''
  return (
    <span className="inline-flex items-center gap-1.5" data-testid="row-move" title="Since the board first saw this game. Market data, not a signal.">
      <Sparkline values={values} w={48} h={18} label={`${g.away} at ${g.home} movement`} />
      <span className="mono text-[10.5px] text-text-secondary whitespace-nowrap">{text}</span>
    </span>
  )
}

/** The best edge of a game as one chip: the label, the price at its book and the EV. Blank (a dash) when there is none, which is the honest answer most of the time. */
function EdgeCell({ g }: { g: GameBoardEntry }) {
  const e = gameEdges(g)[0]
  if (!e) return <span className="text-text-tertiary" title="No price beats the market fair price by 2% or more on 3+ books">--</span>
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap" data-testid="row-edge">
      <BookLogo book={e.shop.book!} size={14} />
      <span className="text-[11.5px] text-text-primary">{e.shop.label}{e.alt ? ' (alt)' : ''}</span>
      <b className="mono text-[12px] font-semibold" style={{ color: GREEN }}>{formatOdds(e.shop.american!)}</b>
      <span className="mono text-[10.5px] font-semibold rounded-chip px-1" style={{ color: GREEN, background: 'rgba(87,242,135,0.10)' }}>{signedPct(e.shop.ev!)}</span>
    </span>
  )
}

/**
 * The Game Lines board. Rows, not cards: each game is one line with the best price at every market, the best edge if there is one, and
 * how it has moved. Double-click a row (tap on a phone, Enter, or the arrow) for a brief card: SHOP, MOVEMENT, ALT LINES. Above the
 * table, "Edges now" lists the few real edges on the slate (against the market fair price, 3+ books, not suspiciously large), or says
 * there are none. UNVALIDATED market data.
 */
export function BestBoard({ feeds: initial, initialSport }: { feeds: GameBoardFeeds; initialSport?: string }) {
  const { odds, proj, feedSports } = useGameFeeds(initial, { model: false })
  const [sport, setSport] = useState(initialSport ?? 'all')
  const [sort, setSort] = useState<BoardSort>('time')
  const [sel, setSel] = useState<string | null>(null)
  const [open, setOpen] = useState<{ key: string; pane: PopupPane } | null>(null)
  const [bankroll] = useBankroll(1000)
  const myBooks = useMyBooks()
  const isMobile = useIsMobile(768)

  const offshore = useMemo(() => new Set(feedSports.flatMap(s => odds[s]?.offshore_books ?? [])), [feedSports, odds])
  const bettable = useMemo(() => (myBooks.enabled ? new Set(myBooks.books) : null), [myBooks.enabled, myBooks.books])
  const opts = useMemo(() => ({ refs: [] as string[], method: 'proportional' as const, bettable, offshore, minRef: GAME_MIN_REF, includeExchanges: true, bankroll }), [bettable, offshore, bankroll])
  const entries = useMemo(() => buildGameBoard({ odds, proj }, opts), [odds, proj, opts])
  const available = useMemo(() => sportsWithGames(entries), [entries])
  const activeSport = sport === 'all' || available.includes(sport) ? sport : 'all'
  const games = useMemo(() => sortBoard(entries.filter(g => activeSport === 'all' || g.sport === activeSport), sort), [entries, activeSport, sort])
  const edges = useMemo(() => edgesNow(games, 5, 1), [games])
  const popup = open ? games.find(g => g.key === open.key) ?? null : null

  const live = feedSports.map(s => odds[s]).filter((f): f is GameOddsFeed => !!f)
  const locked = live.reduce((n, f) => n + (f.locked ?? 0), 0)
  const stale = live.some(f => f.stale)
  const ages = live.map(f => f.age_minutes).filter((n): n is number => n != null && isFinite(n))
  const age = ages.length ? Math.max(...ages) : null
  const unavailable = gameOddsUnavailable(Object.values(odds))
  const quiet = feedSports.filter(s => !available.includes(s)).map(s => `${s.toUpperCase()}: ${odds[s]?.status === 'awaiting_lines' ? 'the next slate\'s lines are not posted yet' : 'no games with prices right now'}`)

  const openCard = (key: string, pane: PopupPane = 'shop') => setOpen({ key, pane })

  return (
    <div>
      <div className="flex items-center gap-y-[6px] gap-x-[14px] flex-wrap min-h-[38px] py-[6px] px-[14px] mb-[10px] bg-background-primary-default text-compact" data-testid="game-board-strip" style={{ border: `1px solid ${HAIR}`, borderRadius: '8px' }}>
        <span className="mono font-bold text-text-primary">{activeSport === 'all' ? 'ALL SPORTS' : activeSport.toUpperCase()}</span>
        <span className="mono text-neutral-300">{games.length} {games.length === 1 ? 'GAME' : 'GAMES'}</span>
        <span className="mono text-neutral-300">LINES {ageText(age)} OLD</span>
        {myBooks.enabled && <span className="mono text-neutral-300" title="Best prices are limited to your books">MY BOOKS ({myBooks.books.length})</span>}
        {stale && <span className="mono text-[10.5px] font-bold tracking-fine text-warn border border-[color:#facc1555] rounded-chip py-0 px-[5px]">STALE</span>}
        <span className="text-neutral-300"><span className="mono text-[10.5px] font-bold tracking-fine text-warn">{GAME_PRICES_LABEL}</span> {GAME_PRICES_NOTE}</span>
      </div>

      {unavailable ? (
        <div role="status" className="py-6 text-neutral-300 text-cozy">Game-line prices are not published yet. Moneyline, spread and total prices refresh with the odds banker; check back shortly.</div>
      ) : entries.length === 0 ? (
        <div role="status" className="py-6 text-neutral-300 text-cozy">No book has posted game lines for this slate yet. Prices appear as soon as a slate and its lines are posted.</div>
      ) : (
        <>
          <section aria-label="Edges now" className="mb-3 bg-background-primary-default border border-separator-border rounded-xl px-3.5 py-2.5" data-testid="edges-now">
            <div className="flex items-baseline justify-between gap-2 mb-1.5">
              <span className="text-compact font-semibold text-text-primary">Edges now</span>
              <span className="text-[11.5px] text-text-tertiary">2%+ against the market fair price, 3+ books{myBooks.enabled ? ', at your books' : ''}</span>
            </div>
            {edges.length === 0 ? (
              <p className="m-0 text-compact text-text-secondary" data-testid="edges-none">Nothing clears the bar right now. The market is tight on every game below.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {edges.map((e: EdgeItem) => (
                  <button key={`${e.game.key}-${e.market}-${e.line}-${e.side}`} type="button" data-testid="edge-chip" onClick={() => openCard(e.game.key, e.alt ? 'alt' : 'shop')}
                    className="inline-flex items-center gap-2 rounded-control border border-separator-border bg-transparent px-2.5 py-1.5 text-left cursor-pointer hover:bg-[color:var(--color-background-primary-hover)] transition-colors duration-150">
                    <span className="text-[11.5px] text-text-secondary">{e.game.away} @ {e.game.home}</span>
                    <span className="text-compact font-semibold text-text-primary">{e.shop.label}{e.alt ? ' (alt)' : ''}</span>
                    <BookLogo book={e.shop.book!} size={14} />
                    <b className="mono font-semibold" style={{ color: GREEN }}>{formatOdds(e.shop.american!)}</b>
                    <span className="mono text-[10.5px] font-semibold rounded-chip px-1" style={{ color: GREEN, background: 'rgba(87,242,135,0.10)' }}>{signedPct(e.shop.ev!)}</span>
                  </button>
                ))}
              </div>
            )}
          </section>

          <div className="flex flex-wrap items-center gap-y-2 gap-x-3.5 mb-2.5">
            <PillGroup label="Sport">
              <Pill active={activeSport === 'all'} onClick={() => setSport('all')}>All sports</Pill>
              {available.map(s => <Pill key={s} active={activeSport === s} onClick={() => setSport(s)}>{s.toUpperCase()}</Pill>)}
            </PillGroup>
            <Select id="bb-sort" label="SORT" value={sort} onChange={v => setSort(v as BoardSort)} options={BOARD_SORTS.map(s => ({ v: s.v, l: s.l }))} />
            <span className="text-[11.5px] text-text-tertiary hidden md:inline">Double-click a game for its card</span>
          </div>
          {quiet.length > 0 && <p className="text-[12px] text-text-secondary m-0 mb-2.5" data-testid="quiet-sports">{quiet.join(' · ')}</p>}

          {games.length === 0 ? (
            <div role="status" className="py-6 text-neutral-300 text-cozy">No games for this sport right now.</div>
          ) : (
            <div className="bg-background-primary-default border border-separator-border rounded-xl shadow-card overflow-x-auto">
              <table className="w-full border-collapse text-compact" data-testid="best-board">
                <thead>
                  <tr className="text-[11px] text-text-tertiary" style={{ background: 'var(--color-background-secondary-default)' }}>
                    <th scope="col" className="text-left font-medium px-3 py-2">Game</th>
                    <th scope="col" className="text-left font-medium px-3 py-2 hidden md:table-cell">Moneyline</th>
                    <th scope="col" className="text-left font-medium px-3 py-2 hidden md:table-cell">Spread</th>
                    <th scope="col" className="text-left font-medium px-3 py-2 hidden md:table-cell">Total</th>
                    <th scope="col" className="text-left font-medium px-3 py-2" title="The best price that beats the market fair price by 2% or more">Edge</th>
                    <th scope="col" className="text-left font-medium px-3 py-2 hidden md:table-cell" title="How the line or fair price has moved since the board first saw the game">Move</th>
                    <th scope="col" className="px-2 py-2"><span className="sr-only">Open</span></th>
                  </tr>
                </thead>
                <tbody>
                  {games.map(g => {
                    const on = sel === g.key
                    const sport = g.sport as LogoSport
                    return (
                      <tr key={g.key} data-testid="board-row" data-game={g.key} aria-selected={on} tabIndex={0}
                        onClick={() => { setSel(g.key); if (isMobile) openCard(g.key) }}
                        onDoubleClick={() => openCard(g.key)}
                        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); openCard(g.key) } }}
                        className="cursor-pointer border-t border-t-[color:var(--color-separator-border)] transition-colors duration-150 hover:bg-[color:var(--color-background-primary-hover)] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[color:var(--signal)]"
                        style={{ background: on ? 'var(--color-background-primary-hover)' : undefined }}>
                        <td className="px-3 py-2 text-left whitespace-nowrap" style={{ boxShadow: `inset 3px 0 0 ${on ? GREEN : 'transparent'}` }}>
                          <span className="inline-flex items-center gap-1.5 font-semibold text-text-primary"><SportLogo sport={sport} team={g.away} size={18} />{g.away}<span className="text-text-tertiary font-normal">@</span><SportLogo sport={sport} team={g.home} size={18} />{g.home}</span>
                          <span className="mono block text-[11px] text-text-secondary">{g.sport.toUpperCase()} &middot; {startLabel(g.start)}</span>
                          <span className="block mt-1 md:hidden"><MoveCell g={g} /></span>
                        </td>
                        <td className="px-3 py-2 hidden md:table-cell"><MarketCell g={g} market="ml" /></td>
                        <td className="px-3 py-2 hidden md:table-cell"><MarketCell g={g} market="spread" /></td>
                        <td className="px-3 py-2 hidden md:table-cell"><MarketCell g={g} market="total" /></td>
                        <td className="px-3 py-2"><EdgeCell g={g} /></td>
                        <td className="px-3 py-2 hidden md:table-cell"><MoveCell g={g} /></td>
                        <td className="px-2 py-2 text-right">
                          <button type="button" onClick={e => { e.stopPropagation(); openCard(g.key) }} aria-label={`Open the card for ${g.away} at ${g.home}`} title="Open the card (or double-click the row)"
                            className="size-7 rounded-control border border-separator-border bg-transparent text-text-secondary hover:text-text-primary cursor-pointer text-[14px] leading-none">&rsaquo;</button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          <LockedNotice locked={locked} shown={entries.length} what="game-line rows" plan="The game-line price screen is part of All-Access Pro." />
        </>
      )}
      {popup && <GamePopup key={popup.key} game={popup} initialPane={open!.pane} onClose={() => setOpen(null)} />}
    </div>
  )
}
