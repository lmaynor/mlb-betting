'use client'

import { useCallback, useDeferredValue, useEffect, useMemo, useState } from 'react'
import { LockedNotice } from '@/components/picks/locked-notice'
import { useIsMobile } from '@/components/ui/mobile-sheet'
import { apiGetGameOddsBoard } from '@/lib/betting-api'
import {
  ageText, buildGamePriceRows, feedRows, filterGamePriceRows, GAME_MIN_REF, GAME_PRICES_LABEL, GAME_PRICES_NOTE, gameArbHedgeText, gameBookColumns,
  gameOddsUnavailable, SHARP_BOOKS, sortGamePriceRows,
  type GameMarket, type GameOddsFeed, type GameSort,
} from '@/lib/game-odds'
import type { DevigMethod } from '@/lib/odds-screen'
import { useBankroll } from '@/lib/use-bankroll'
import { useMyBooks } from '@/lib/use-my-books'
import { BookPicker, copyText, HAIR, Pill, PillGroup, PriceCard, PriceTableRow, Select, Toggle } from './game-prices-parts'

type Market = 'all' | GameMarket
const MARKETS: { key: Market; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'ml', label: 'Moneyline' },
  { key: 'spread', label: 'Spread' },
  { key: 'total', label: 'Total' },
]
const MIN_EV = [
  { l: 'All rows', v: '-1' }, { l: '0%+', v: '0' }, { l: '1%+', v: '0.01' }, { l: '2%+', v: '0.02' }, { l: '3%+', v: '0.03' }, { l: '5%+', v: '0.05' },
]
const SORTS: { v: GameSort; l: string }[] = [{ v: 'ev', l: 'Best EV' }, { v: 'time', l: 'Start time' }, { v: 'game', l: 'Game' }]
const PAGE = 120
const OUTLAY = 100
// The banker rewrites the boards every 30 minutes, so a faster poll only re-downloads identical data.
const POLL_MS = 3 * 60_000

/**
 * NBA / NHL game-line PRICES for /ev/games: every book's moneyline, spread and total side by side, with the best bettable price,
 * a fair value de-vigged from the reference books the viewer picks, EV, and game-line arbitrage. All maths is lib/game-odds.ts
 * (an adapter onto lib/odds-screen.ts). Offshore books are shown but are never the best price. UNVALIDATED market data.
 */
export function GamePricesClient({ feeds: initial, sport }: { feeds: Record<string, GameOddsFeed | null>; sport: 'all' | 'nba' | 'nhl' }) {
  const [feeds, setFeeds] = useState(initial)
  const [market, setMarket] = useState<Market>('all')
  const [game, setGame] = useState('all')
  const [q, setQ] = useState('')
  const [minEv, setMinEv] = useState(-1)
  const [refs, setRefs] = useState<string[]>([])
  const [shown, setShown] = useState<string[] | null>(null)
  const [method, setMethod] = useState<DevigMethod>('proportional')
  const [exchanges, setExchanges] = useState(true)
  const [arbOnly, setArbOnly] = useState(false)
  const [sort, setSort] = useState<GameSort>('ev')
  const [limit, setLimit] = useState(PAGE)
  const [copied, setCopied] = useState<string | null>(null)
  const [bankroll] = useBankroll(1000)
  const myBooks = useMyBooks()
  const isMobile = useIsMobile(768)
  const deferredQ = useDeferredValue(q)

  const sports = useMemo(() => (sport === 'all' ? ['nba', 'nhl'] : [sport]), [sport])
  const active = useMemo(() => sports.map(s => feeds[s]).filter((f): f is GameOddsFeed => !!f), [sports, feeds])

  useEffect(() => {
    const id = setInterval(async () => {
      if (document.visibilityState !== 'visible') return
      const next = await Promise.all(sports.map(s => apiGetGameOddsBoard(s, { limit: 3000 }).catch(() => null)))
      setFeeds(prev => {
        const out = { ...prev }
        let changed = false
        next.forEach((f, i) => { if (f && Array.isArray(f.rows) && prev[sports[i]]?.as_of !== f.as_of) { out[sports[i]] = f; changed = true } })
        return changed ? out : prev
      })
    }, POLL_MS)
    return () => clearInterval(id)
  }, [sports])

  const allRows = useMemo(() => active.flatMap(f => feedRows(f)), [active])
  const offshore = useMemo(() => new Set(active.flatMap(f => f.offshore_books ?? [])), [active])
  const bookList = useMemo(() => gameBookColumns(allRows, offshore), [allRows, offshore])
  const defaultShown = useMemo(() => bookList.filter(b => b.n >= Math.max(1, allRows.length * 0.1)).map(b => b.key), [bookList, allRows.length])
  const shownSet = useMemo(() => new Set(shown ?? defaultShown), [shown, defaultShown])
  const columns = useMemo(() => bookList.filter(b => shownSet.has(b.key) || refs.includes(b.key)), [bookList, shownSet, refs])
  const bettable = useMemo(() => new Set(bookList.filter(b => shownSet.has(b.key) && !b.offshore).map(b => b.key)), [bookList, shownSet])

  const priced = useMemo(
    () => buildGamePriceRows(allRows, { refs, method, bettable, offshore, minRef: GAME_MIN_REF, includeExchanges: exchanges, bankroll }),
    [allRows, refs, method, bettable, offshore, exchanges, bankroll],
  )
  const visible = useMemo(
    () => sortGamePriceRows(filterGamePriceRows(priced, { market, game, q: deferredQ, minEv, arbOnly }), sort),
    [priced, market, game, deferredQ, minEv, arbOnly, sort],
  )

  const games = useMemo(() => {
    const seen = new Map<string, { key: string; label: string; start: string }>()
    for (const f of active) for (const g of f.games ?? []) seen.set(g.game_key, { key: g.game_key, label: `${g.away} @ ${g.home}`, start: g.start })
    return [...seen.values()].sort((a, b) => a.start.localeCompare(b.start))
  }, [active])

  const locked = active.reduce((n, f) => n + (f.locked ?? 0), 0)
  const unavailable = gameOddsUnavailable(sports.map(s => feeds[s]))
  const stale = active.some(f => f.stale)
  const ages = active.map(f => f.age_minutes).filter((n): n is number => n != null && isFinite(n))
  const age = ages.length ? Math.max(...ages) : null
  const sportLabel = sport === 'all' ? 'NBA + NHL' : sport.toUpperCase()

  const copyHedge = useCallback(async (r: Parameters<typeof gameArbHedgeText>[0]) => {
    const text = gameArbHedgeText(r, OUTLAY)
    if (text && await copyText(text)) {
      setCopied(r.key)
      setTimeout(() => setCopied(c => (c === r.key ? null : c)), 1500)
    }
  }, [])

  const refPresets = [{ label: 'Sharp', keys: SHARP_BOOKS }, ...(myBooks.enabled ? [{ label: 'My books', keys: myBooks.books }] : [])]
  const shownPresets = [{ label: 'Default', keys: defaultShown }, ...(myBooks.enabled ? [{ label: 'My books', keys: myBooks.books }] : [])]
  const reset = () => setLimit(PAGE)

  return (
    <div>
      <div className="flex items-center gap-y-[6px] gap-x-[14px] flex-wrap min-h-[38px] py-[6px] px-[14px] mb-[10px] bg-background-primary-default text-compact" data-testid="game-prices-strip" style={{ border: `1px solid ${HAIR}`, borderRadius: '8px' }}>
        <span className="mono font-semibold text-text-primary">{sportLabel}</span>
        <span className="mono text-neutral-300">{visible.length} rows</span>
        <span className="mono text-neutral-300" title={active.map(f => f.lines_source).filter(Boolean).join(', ') || undefined}>Lines {ageText(age)} old</span>
        {stale && <span className="mono text-[11px] font-semibold text-warn border border-[color:#facc1555] rounded-chip py-0 px-[5px]">Stale</span>}
        <span className="text-neutral-300">
          <span className="mono text-[11px] font-semibold text-warn">{GAME_PRICES_LABEL}</span> {GAME_PRICES_NOTE}
        </span>
      </div>

      {unavailable ? (
        <div role="status" className="py-6 px-0 text-neutral-300 text-cozy">
          Game-line prices are not published yet. NBA and NHL moneyline, spread and total prices refresh with the odds banker; check back shortly.
        </div>
      ) : allRows.length === 0 && locked === 0 ? (
        <div role="status" className="py-6 px-0 text-neutral-300 text-cozy">
          No book has posted game lines for this slate yet. Prices appear as soon as a slate and its lines are posted.
        </div>
      ) : (
        <>
          <div className="bg-background-primary-default py-[10px] px-3 flex flex-wrap items-center gap-y-2 gap-x-[14px] mb-[10px]" style={{ border: `1px solid ${HAIR}`, borderRadius: '8px' }}>
            <PillGroup label="Market">
              {MARKETS.map(m => <Pill key={m.key} active={market === m.key} onClick={() => { setMarket(m.key); reset() }}>{m.label}</Pill>)}
            </PillGroup>
            <Select id="gp-game" label="GAME" value={game} onChange={v => { setGame(v); reset() }} options={[{ v: 'all', l: 'All games' }, ...games.map(g => ({ v: g.key, l: g.label }))]} />
            <Select id="gp-minev" label="Min EV" value={String(minEv)} onChange={v => { setMinEv(Number(v)); reset() }} options={MIN_EV} />
            <BookPicker label={refs.length ? `Reference books (${refs.length})` : 'Reference books (all)'} books={bookList} selected={refs} onChange={setRefs} presets={refPresets} emptyMeansAll
              note="The fair value is the average of each reference book's own de-vigged price, leaving out the book you would bet at. It needs 3 reference books on the line. Default: every book." />
            <BookPicker label={`Books shown (${columns.length})`} books={bookList} selected={shown ?? defaultShown} onChange={setShown} presets={shownPresets}
              note="A shown book can be a best price, except offshore books: those are shown for comparison and never counted as bettable." />
            <PillGroup label="De-vig method">
              <Pill active={method === 'proportional'} onClick={() => setMethod('proportional')} title="Remove the vig in proportion to each side's implied probability">Proportional</Pill>
              <Pill active={method === 'power'} onClick={() => setMethod('power')} title="Power method: shades the favorite less and the longshot more">Power</Pill>
            </PillGroup>
            <Toggle label="Exchanges" checked={exchanges} onChange={setExchanges} title="Count Kalshi, Novig and ProphetX in the arbitrage search (Kalshi's taker fee is taken off its leg)" />
            <Toggle label="Arbs only" checked={arbOnly} onChange={v => { setArbOnly(v); reset() }} />
            <Select id="gp-sort" label="SORT" value={sort} onChange={v => setSort(v as GameSort)} options={SORTS.map(s => ({ v: s.v, l: s.l }))} />
            <input className="bg-background-secondary-default text-text-primary h-8 py-0 px-[10px] text-compact w-[140px]" type="search" aria-label="Search teams" placeholder="Search team" value={q} onChange={e => { setQ(e.target.value); reset() }}
              style={{ border: `1px solid ${HAIR}`, borderRadius: '6px' }} />
          </div>

          {visible.length === 0 ? (
            <div role="status" className="py-6 px-0 text-neutral-300 text-cozy">
              No lines match these filters. Lower MIN EV, switch the market or clear the search.
            </div>
          ) : isMobile ? (
            <div className="grid gap-2">
              {visible.slice(0, limit).map(r => <PriceCard key={r.key} r={r} columns={columns} copied={copied === r.key} onCopy={copyHedge} />)}
            </div>
          ) : (
            <div className="odds-table-wrap bg-background-primary-default" style={{ border: `1px solid ${HAIR}`, borderRadius: '8px', boxShadow: '0 1px 2px rgba(0,0,0,0.45)' }}>
              <table className="odds-table w-full border-collapse">
                <thead>
                  <tr>
                    <th scope="col" className="odds-prop">GAME</th>
                    <th scope="col" className="odds-ev" title="Best bettable price on the away side (moneyline, spread) or the over (total)">Best away / over</th>
                    <th scope="col" className="odds-ev" title="Best bettable price on the home side (moneyline, spread) or the under (total)">Best home / under</th>
                    <th scope="col" className="odds-ev" title="De-vigged fair probability from the reference books, away / over above home / under">Fair %</th>
                    <th scope="col" className="odds-ev" title="EV of each side at its best bettable price against the fair value">EV</th>
                    <th scope="col" title="1/4 Kelly stake on the best +EV side">1/4 KELLY</th>
                    {columns.map(c => (
                      <th key={c.key} scope="col" className="odds-book" title={`${c.key}${c.offshore ? ' (offshore: shown, never the best price)' : ''}. Away / over above, home / under below.`}>
                        {c.key.toUpperCase().slice(0, 8)}{c.offshore ? '*' : ''}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visible.slice(0, limit).map(r => <PriceTableRow key={r.key} r={r} columns={columns} bankroll={bankroll} copied={copied === r.key} onCopy={copyHedge} />)}
                </tbody>
              </table>
            </div>
          )}
          {visible.length > limit && (
            <button type="button" className="btn btn-ghost mt-[10px] text-compact" onClick={() => setLimit(l => l + PAGE)}>Show more ({visible.length - limit} left)</button>
          )}
          <LockedNotice locked={locked} shown={allRows.length} what="game-line rows" plan="The game-line price screen is part of All-Access Pro." />
          <p className="text-compact text-text-secondary mt-3 leading-[1.5]">
            Each book cell shows the away side (or over) above the home side (or under); green marks the best bettable price and an outlined price is a leg of an arbitrage. A spread row is
            labelled by the home line (home -3.5 means the away side gets +3.5); different lines of one game are separate rows. Offshore books (*) are shown for comparison and never
            counted as bettable. Fair value de-vigs each reference book two ways and averages them; whole-number lines can push, which this ignores. An arbitrage nets Kalshi&apos;s taker
            fee off its leg. {GAME_PRICES_LABEL} {GAME_PRICES_NOTE}: lines move or limit quickly, so check the price at the book. Not financial advice.
          </p>
        </>
      )}
    </div>
  )
}
