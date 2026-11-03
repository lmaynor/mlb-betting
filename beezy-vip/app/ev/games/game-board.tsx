'use client'

import { useEffect, useMemo, useState } from 'react'
import { LockedNotice } from '@/components/picks/locked-notice'
import { SportLogo } from '@/components/ui/sport-assets'
import { BOARD_SORTS, buildGameBoard, sortBoard, sportsWithGames, type BoardSort, type GameBoardEntry } from '@/lib/game-board'
import {
  ageText, feedRows, gameBookColumns, gameOddsUnavailable, GAME_MIN_REF, GAME_PRICES_LABEL, GAME_PRICES_NOTE, SHARP_BOOKS, signedLine, startLabel,
  type GameOddsFeed,
} from '@/lib/game-odds'
import type { DevigMethod } from '@/lib/odds-screen'
import { useGameFeeds, type GameBoardFeeds } from './use-game-feeds'
import { useBankroll } from '@/lib/use-bankroll'
import { useMyBooks } from '@/lib/use-my-books'
import { moveForSide } from '@/lib/line-move'
import { primaryMove } from '@/lib/game-shop'
import { GameDetail } from './game-detail'
import { Sparkline } from './sparkline'
import { BookPicker, GREEN, HAIR, Pill, PillGroup, SKY, Select, Toggle } from './game-prices-parts'

type LogoSport = Parameters<typeof SportLogo>[0]['sport']

export type { GameBoardFeeds }

function evChip(g: GameBoardEntry): { text: string; color: string } | null {
  if (g.bestEv == null || g.bestEv <= 0.005) return null
  return { text: `${(g.bestEv * 100).toFixed(1)}% EV`, color: GREEN }
}

/** One game in the strip: both crests, the start, the two main lines, and only the badges that are true (EV, arb, model gap). */
function GameCard({ g, on, onPick }: { g: GameBoardEntry; on: boolean; onPick: () => void }) {
  const sp = g.main.spread
  const to = g.main.total
  const ev = evChip(g)
  const sport = g.sport as LogoSport
  return (
    <button type="button" onClick={onPick} aria-pressed={on} data-testid="game-card" data-game={g.key}
      aria-label={`${g.away} at ${g.home}, ${startLabel(g.start)}`}
      className="flex-none w-[184px] text-left cursor-pointer rounded-xl border p-2.5 transition-colors duration-150 hover:bg-[color:var(--color-background-primary-hover)]"
      style={{ scrollSnapAlign: 'start', background: on ? 'rgba(87,242,135,0.05)' : 'var(--color-background-primary-default)', borderColor: on ? GREEN : 'var(--color-separator-border)' }}>
      <span className="flex items-center gap-1.5">
        <SportLogo sport={sport} team={g.away} size={22} />
        <span className="text-compact font-semibold text-text-primary">{g.away}</span>
        <span className="text-text-tertiary text-[11px]">@</span>
        <SportLogo sport={sport} team={g.home} size={22} />
        <span className="text-compact font-semibold text-text-primary">{g.home}</span>
      </span>
      <span className="mono block text-[11px] text-text-secondary mt-1">{g.sport.toUpperCase()} &middot; {startLabel(g.start)}</span>
      <span className="mono block text-[11.5px] text-text-primary mt-1 whitespace-nowrap overflow-hidden text-ellipsis">
        {sp ? `${g.home} ${signedLine(sp.row.line ?? 0)}` : 'No spread'}{to ? ` · ${to.row.line}` : ''}
      </span>
      {(() => {
        const mv = primaryMove(g)
        if (!mv) return null
        const f = moveForSide(mv.m, mv.side)
        const pts = f.pDelta == null ? null : Math.abs(f.pDelta * 100)
        const text = mv.m.lineMoved && mv.m.lineOpen != null && mv.m.lineNow != null
          ? (mv.market === 'total' ? `O/U ${mv.m.lineOpen} \u2192 ${mv.m.lineNow}` : `${g.home} ${signedLine(mv.m.lineOpen)} \u2192 ${signedLine(mv.m.lineNow)}`)
          : pts != null ? `${f.pDelta! >= 0 ? '\u25b2' : '\u25bc'} ${pts.toFixed(1)} pts` : null
        return (
          <span className="flex items-center gap-1.5 mt-1" data-testid="card-move" title={`Since the board first saw this game: ${mv.market === 'ml' ? 'moneyline' : mv.market} ${mv.m.lineMoved ? 'line moved' : 'fair price moved'}. Market data, not a signal.`}>
            <Sparkline values={f.pSeries.length >= 2 ? f.pSeries : (mv.m.lineSeries.filter((x): x is number => x != null))} w={56} h={18} label={`${g.away} at ${g.home} fair price movement`} />
            {text && <span className="mono text-[10.5px] text-text-secondary whitespace-nowrap">{text}</span>}
          </span>
        )
      })()}
      <span className="flex flex-wrap gap-1 mt-1.5 min-h-[18px]">
        {ev && <span className="mono text-[10px] font-semibold rounded-chip px-1" style={{ color: ev.color, background: 'rgba(87,242,135,0.08)' }}>{ev.text}</span>}
        {g.arbLive && <span className="mono text-[10px] font-semibold tracking-fine text-warn border border-[color:#facc1555] rounded-chip px-1">ARB</span>}
        {g.modelGap != null && g.modelGap > 0.02 && <span className="mono text-[10px] font-semibold rounded-chip px-1" style={{ color: SKY, background: 'rgba(56,189,248,0.10)' }} title="Our model vs the market fair price (unvalidated)">MODEL +{(g.modelGap * 100).toFixed(1)}%</span>}
      </span>
    </button>
  )
}

/**
 * The Game Lines board: every game as a card across the top, one deep-dive below for the selected game. All sports that publish a
 * game-line feed appear (a sport with no games is simply not offered); the price maths is lib/game-odds.ts and the board logic
 * lib/game-board.ts. UNVALIDATED market data and model estimates.
 */
export function GameBoard({ feeds: initial, initialGame, initialSport }: { feeds: GameBoardFeeds; initialGame?: string; initialSport?: string }) {
  const { odds, proj, feedSports } = useGameFeeds(initial)
  const [sport, setSport] = useState(initialSport ?? 'all')
  const [sort, setSort] = useState<BoardSort>('time')
  const [picked, setPicked] = useState<string | null>(initialGame ?? null)
  const [refs, setRefs] = useState<string[]>([])
  const [method, setMethod] = useState<DevigMethod>('proportional')
  const [exchanges, setExchanges] = useState(true)
  const [bankroll] = useBankroll(1000)
  const myBooks = useMyBooks()

  const offshore = useMemo(() => new Set(feedSports.flatMap(s => odds[s]?.offshore_books ?? [])), [feedSports, odds])
  const bettable = useMemo(() => (myBooks.enabled ? new Set(myBooks.books) : null), [myBooks.enabled, myBooks.books])
  const opts = useMemo(() => ({ refs, method, bettable, offshore, minRef: GAME_MIN_REF, includeExchanges: exchanges, bankroll }), [refs, method, bettable, offshore, exchanges, bankroll])

  const entries = useMemo(() => buildGameBoard({ odds, proj }, opts), [odds, proj, opts])
  const available = useMemo(() => sportsWithGames(entries), [entries])
  const activeSport = sport === 'all' || available.includes(sport) ? sport : 'all'
  const games = useMemo(() => sortBoard(entries.filter(g => activeSport === 'all' || g.sport === activeSport), sort), [entries, activeSport, sort])
  const selected = games.find(g => g.key === picked) ?? games[0] ?? null
  // A sport with a feed but no game is not a pill, so say why instead of leaving it to look missing.
  const quiet = feedSports.filter(s => !available.includes(s)).map(s => `${s.toUpperCase()}: ${odds[s]?.status === 'awaiting_lines' ? 'the next slate\'s lines are not posted yet' : 'no games with prices right now'}`)

  useEffect(() => {
    if (typeof window === 'undefined' || !selected) return
    const url = new URL(window.location.href)
    if (url.searchParams.get('game') === selected.key) return
    url.searchParams.set('game', selected.key)
    window.history.replaceState(null, '', url.pathname + url.search)
  }, [selected])

  const allRows = useMemo(() => feedSports.flatMap(s => feedRows(odds[s])), [feedSports, odds])
  const bookList = useMemo(() => gameBookColumns(allRows, offshore), [allRows, offshore])
  const refPresets = [{ label: 'Sharp', keys: SHARP_BOOKS }, ...(myBooks.enabled ? [{ label: 'My books', keys: myBooks.books }] : [])]

  const live = feedSports.map(s => odds[s]).filter((f): f is GameOddsFeed => !!f)
  const locked = live.reduce((n, f) => n + (f.locked ?? 0), 0)
  const stale = live.some(f => f.stale)
  const ages = live.map(f => f.age_minutes).filter((n): n is number => n != null && isFinite(n))
  const age = ages.length ? Math.max(...ages) : null
  const unavailable = gameOddsUnavailable(Object.values(odds))

  return (
    <div>
      <div className="flex items-center gap-y-[6px] gap-x-[14px] flex-wrap min-h-[38px] py-[6px] px-[14px] mb-[10px] bg-background-primary-default text-compact" data-testid="game-board-strip" style={{ border: `1px solid ${HAIR}`, borderRadius: '8px' }}>
        <span className="mono font-bold text-text-primary">{activeSport === 'all' ? 'ALL SPORTS' : activeSport.toUpperCase()}</span>
        <span className="mono text-neutral-300">{games.length} {games.length === 1 ? 'GAME' : 'GAMES'}</span>
        <span className="mono text-neutral-300">LINES {ageText(age)} OLD</span>
        {stale && <span className="mono text-[10.5px] font-bold tracking-fine text-warn border border-[color:#facc1555] rounded-chip py-0 px-[5px]">STALE</span>}
        <span className="text-neutral-300"><span className="mono text-[10.5px] font-bold tracking-fine text-warn">{GAME_PRICES_LABEL}</span> {GAME_PRICES_NOTE}</span>
      </div>

      {unavailable ? (
        <div role="status" className="py-6 text-neutral-300 text-cozy">Game-line prices are not published yet. Moneyline, spread and total prices refresh with the odds banker; check back shortly.</div>
      ) : entries.length === 0 ? (
        <div role="status" className="py-6 text-neutral-300 text-cozy">No book has posted game lines for this slate yet. Prices appear as soon as a slate and its lines are posted.</div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-y-2 gap-x-3.5 mb-2.5">
            <PillGroup label="Sport">
              <Pill active={activeSport === 'all'} onClick={() => setSport('all')}>All sports</Pill>
              {available.map(s => <Pill key={s} active={activeSport === s} onClick={() => setSport(s)}>{s.toUpperCase()}</Pill>)}
            </PillGroup>
            <Select id="gb-sort" label="SORT" value={sort} onChange={v => setSort(v as BoardSort)} options={BOARD_SORTS.map(s => ({ v: s.v, l: s.l }))} />
            <details className="text-compact">
              <summary className="cursor-pointer text-text-secondary hover:text-text-primary select-none">Advanced</summary>
              <div className="flex flex-wrap items-center gap-y-2 gap-x-3.5 mt-2">
                <BookPicker label={refs.length ? `Reference books (${refs.length})` : 'Reference books (all)'} books={bookList} selected={refs} onChange={setRefs} presets={refPresets} emptyMeansAll
                  note="The market fair price is the average of each reference book's own de-vigged price, leaving out the book you would bet at. It needs 3 reference books on the line. Default: every book." />
                <PillGroup label="Fair value method">
                  <Pill active={method === 'proportional'} onClick={() => setMethod('proportional')} title="Takes the book's margin out of both sides in proportion to their odds. The standard choice.">Standard</Pill>
                  <Pill active={method === 'power'} onClick={() => setMethod('power')} title="Takes less margin from the favorite and more from the longshot. Use it when longshot prices look inflated.">Favorite-longshot</Pill>
                </PillGroup>
                <Toggle label="Exchanges" checked={exchanges} onChange={setExchanges} title="Count Kalshi, Novig and ProphetX in the arbitrage search (Kalshi's taker fee is taken off its leg)" />
              </div>
            </details>
          </div>

          {quiet.length > 0 && <p className="text-[12px] text-text-secondary m-0 mb-2.5" data-testid="quiet-sports">{quiet.join(' \u00b7 ')}</p>}
          {games.length === 0 ? (
            <div role="status" className="py-6 text-neutral-300 text-cozy">No games for this sport right now.</div>
          ) : (
            <>
              <nav aria-label="Games" className="flex gap-2 overflow-x-auto pb-2 mb-2.5" style={{ scrollSnapType: 'x proximity' }} data-testid="game-strip">
                {games.map(g => <GameCard key={g.key} g={g} on={selected?.key === g.key} onPick={() => setPicked(g.key)} />)}
              </nav>
              {selected && <GameDetail key={selected.key} game={selected} bettable={bettable} ageLabel={`${ageText(age)} old`} />}
            </>
          )}
          <LockedNotice locked={locked} shown={entries.length} what="game-line rows" plan="The game-line price screen is part of All-Access Pro." />
        </>
      )}
    </div>
  )
}
