'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import {
  Tab as AriaTab,
  TabList as AriaTabList,
  TabPanel as AriaTabPanel,
  Tabs as AriaTabs,
} from 'react-aria-components'
import { BookLogo } from '@/components/ui/book-logo'
import { SportLogo } from '@/components/ui/sport-assets'
import { useFocusTrap } from '@/components/ui/use-focus-trap'
import { modelSideProb, type GameBoardEntry } from '@/lib/game-board'
import { buildLadderRung } from '@/lib/game-ladder'
import { altRows, gameHeadline, type AltRow, type ShopSide } from '@/lib/game-shop'
import { signedLine, startLabel, type TwoWayMarket } from '@/lib/game-odds'
import { hasMovement, moveForSide } from '@/lib/line-move'
import { formatOdds } from '@/lib/odds'
import { bookDisplay } from '@/lib/tokens'
import { Seg } from './game-detail'
import { GREEN, signedPct } from './game-prices-parts'
import { HeatStrip, type HeatRung } from './heat-strip'
import { ModelPane } from './model-pane'
import { Sparkline } from './sparkline'

/** 'analysis' is the Model tab; the id stays so old ?view=analysis links keep landing on it. */
export type PopupPane = TwoWayMarket | 'move' | 'analysis'
type LogoSport = Parameters<typeof SportLogo>[0]['sport']

const MARKET_NAME: Record<TwoWayMarket, string> = { ml: 'Moneyline', spread: 'Spread', total: 'Total' }
const ALT_WINDOW = 9

/** One side's best price as a short line: the book, the price, and the EV chip only when it is a real edge (amber "check" when too big to trust). */
function PriceChip({ s }: { s: ShopSide }) {
  if (s.american == null || s.book == null) return <span className="text-text-tertiary">--</span>
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap" data-testid="price-chip">
      <BookLogo book={s.book} size={16} />
      <b className="mono font-semibold" style={{ color: s.actionable ? GREEN : 'var(--color-text-primary)' }}>{formatOdds(s.american)}</b>
      {s.actionable && s.ev != null && <span className="mono text-[11px] font-semibold rounded-chip px-1" style={{ color: GREEN, background: 'rgba(87,242,135,0.10)' }}>{signedPct(s.ev)}</span>}
      {s.suspect && <span className="mono text-[10px] font-semibold text-warn" title="Unusually large against the market fair price: often a stale quote. Check the price at the book.">Check</span>}
    </span>
  )
}

/** The big two-side buttons of a line: each side's best price at its book, the next best elsewhere, and the EV chip only for a real edge. */
function SideButtons({ r }: { r: AltRow }) {
  return (
    <div className="grid grid-cols-2 gap-2" data-testid="side-buttons">
      {[r.a, r.b].map(s => (
        <div key={s.label} data-testid="side-button" data-actionable={s.actionable || undefined}
          className="rounded-xl border border-separator-border px-3 py-2.5 min-w-0 min-h-[116px]" style={{ background: 'var(--color-background-secondary-default)' }}>
          <div className="text-[12px] text-text-secondary truncate">{s.label}</div>
          <div className="mt-1 flex items-center gap-2 flex-wrap">
            {s.american != null && s.book != null ? (
              <>
                <b className="mono text-[22px] leading-[26px] font-semibold" style={{ color: s.actionable ? GREEN : s.suspect ? '#facc15' : 'var(--color-text-primary)' }}>{formatOdds(s.american)}</b>
                <BookLogo book={s.book} size={20} />
                <span className="text-[12px] text-text-secondary">{bookDisplay(s.book).label}</span>
              </>
            ) : <span className="text-text-tertiary">no price</span>}
          </div>
          <div className="mt-1 flex items-center gap-2 flex-wrap min-h-[20px]">
            {s.actionable && s.ev != null && <span className="mono text-[11px] font-semibold rounded-chip px-1.5" style={{ color: GREEN, background: 'rgba(87,242,135,0.10)' }}>{signedPct(s.ev)} vs fair</span>}
            {s.suspect && s.ev != null && <span className="mono text-[11px] font-semibold rounded-chip px-1.5 text-warn" style={{ background: 'rgba(250,204,21,0.10)' }} title="Far better than the market fair price: often a stale quote, sometimes a mispriced line. Confirm the price at the book.">{signedPct(s.ev)} Check</span>}
            {s.others.length > 0 && <span className="mono text-[11px] text-text-tertiary" title="The next best prices at other books">also {s.others.map(o => `${bookDisplay(o.book).short} ${formatOdds(o.american)}`).join(' · ')}</span>}
          </div>
        </div>
      ))}
    </div>
  )
}

/**
 * One market the way Pikkit lays out a spread or total: a line scroller to pick the line, the two big best-price buttons for it, and
 * every other line as a pair of tiles underneath (tap a tile to jump to that line). A moneyline has the buttons only.
 */
function MarketPane({ g, market, initialLine, bettable }: { g: GameBoardEntry; market: TwoWayMarket; initialLine?: number | null; bettable: ReadonlySet<string> | null }) {
  const rows = altRows(g, market)
  const main = rows.find(r => r.main) ?? rows[0] ?? null
  const [line, setLine] = useState<number | null>(initialLine !== undefined && rows.some(r => r.line === initialLine) ? initialLine : (main?.line ?? null))
  const [all, setAll] = useState(false)
  const [books, setBooks] = useState(false)
  const [bookSide, setBookSide] = useState<'a' | 'b'>(market === 'total' ? 'a' : 'b')
  const chipRef = useRef<HTMLButtonElement | null>(null)
  useEffect(() => { chipRef.current?.scrollIntoView?.({ block: 'nearest', inline: 'center' }) }, [line])
  if (rows.length === 0 || !main) return <p className="m-0 text-compact text-text-secondary" data-testid="market-empty">No book has posted this market yet.</p>
  const sel = rows.find(r => r.line === line) ?? main
  const chip = (x: number | null) => (x == null ? 'ML' : market === 'total' ? String(x) : signedLine(x))
  const mi = Math.max(0, rows.findIndex(r => r.main))
  const start = Math.max(0, Math.min(rows.length - ALT_WINDOW, mi - Math.floor(ALT_WINDOW / 2)))
  const tiles = all || rows.length <= ALT_WINDOW ? rows : rows.slice(start, start + ALT_WINDOW)
  const heatRungs: HeatRung[] = tiles.map(r => {
    const sp = bookSide === 'a' ? r.a : r.b
    return { line: r.line, label: sp.label, main: r.main && market !== 'ml', rung: buildLadderRung(r.row.row, bookSide, { pModel: modelSideProb(g.model, market, r.line, bookSide), pMarket: (bookSide === 'a' ? r.row.a : r.row.b).pFair, bettable }) }
  })
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: 'minmax(0, 1fr)' }} data-testid={`market-${market}`}>
      {market !== 'ml' && rows.length > 1 && (
        <div>
          <div className="text-[11px] text-text-tertiary mb-1">{market === 'total' ? 'Total points' : `${g.home} line`}</div>
          <div className="flex gap-1.5 overflow-x-auto pb-1" role="group" aria-label="Line" data-testid="line-scroller">
            {rows.map(r => {
              const on = r.line === sel.line
              return (
                <button key={r.row.key} ref={on ? chipRef : undefined} type="button" aria-pressed={on} data-testid={`line-chip-${r.line}`} onClick={() => setLine(r.line)}
                  title={r.thin ? 'Fewer than 3 books quote this line' : undefined}
                  className="mono flex-none min-w-[56px] h-9 rounded-control border text-[13px] font-semibold cursor-pointer transition-colors duration-150"
                  style={{
                    background: on ? 'var(--color-background-tertiary-default)' : 'transparent',
                    borderColor: on ? GREEN : 'var(--color-separator-border)',
                    color: on ? 'var(--color-text-primary)' : r.thin ? 'var(--color-text-tertiary)' : 'var(--color-text-secondary)',
                  }}>
                  {chip(r.line)}{r.main && <span className="block text-[10px] leading-[8px] -mt-px" style={{ color: GREEN }}>MAIN</span>}
                </button>
              )
            })}
          </div>
        </div>
      )}
      <SideButtons r={sel} />
      {sel.thin && <p className="m-0 text-[12px] text-text-tertiary">Fewer than 3 books quote this line, so no edge is claimed.</p>}
      {rows.length > 1 && (
        <div data-testid="line-tiles">
          <div className="text-[11px] text-text-tertiary mb-1">All lines</div>
          <div className="grid gap-1.5" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
            {tiles.map(r => (
              [r.a, r.b].map((s, i) => (
                <button key={`${r.row.key}-${i}`} type="button" onClick={() => setLine(r.line)} data-testid="line-tile" data-line={r.line ?? ''} data-selected={r.line === sel.line || undefined}
                  className="relative text-left rounded-control border px-2.5 py-1.5 cursor-pointer transition-colors duration-150 hover:bg-[color:var(--color-background-primary-hover)]"
                  style={{ borderColor: r.line === sel.line ? 'var(--color-border-button-hover)' : 'var(--color-separator-border)', background: 'transparent', opacity: r.thin ? 0.75 : 1 }}>
                  <span className="block text-[12px] text-text-secondary truncate">{s.label}</span>
                  <b className="mono text-[14px] font-semibold" style={{ color: s.actionable ? GREEN : 'var(--color-text-primary)' }}>{s.american != null ? formatOdds(s.american) : '--'}</b>
                  {s.actionable && s.ev != null && <span className="mono text-[10px] font-semibold ml-1.5" style={{ color: GREEN }}>{signedPct(s.ev)}</span>}
                  {s.book && <span className="absolute right-1.5 bottom-1.5"><BookLogo book={s.book} size={14} /></span>}
                </button>
              ))
            ))}
          </div>
          {rows.length > ALT_WINDOW && <button type="button" onClick={() => setAll(a => !a)} data-testid="alt-all" className="mt-2 text-[13px] text-link cursor-pointer bg-transparent border-0 p-0 hover:underline">{all ? 'Show nearby lines only' : `Show all ${rows.length} lines`}</button>}
        </div>
      )}
      <div data-testid="all-books">
        <button type="button" aria-expanded={books} onClick={() => setBooks(b => !b)} data-testid="all-books-toggle" className="text-[13px] text-link cursor-pointer bg-transparent border-0 p-0 hover:underline">
          {books ? 'Hide all books' : 'All books, line by line'}
        </button>
        {books && (
          <div className="mt-2 grid gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Seg label="Side" value={bookSide} options={[{ v: 'a', l: sel.a.label }, { v: 'b', l: sel.b.label }]} onChange={setBookSide} />
              <span className="text-[12px] text-text-tertiary">Green beats the market fair price (EV %), amber is a big gap to check.</span>
            </div>
            <HeatStrip rungs={heatRungs} selected={sel.line} onSelect={l => setLine(l)} />
          </div>
        )}
      </div>
    </div>
  )
}

function MovePane({ g }: { g: GameBoardEntry }) {
  if (!g.hasHistory) return <p className="m-0 text-compact text-text-secondary" data-testid="move-empty">Line movement is not available yet.</p>
  const rows = (['spread', 'total', 'ml'] as const).map(market => ({ market, m: g.move[market] })).filter(x => hasMovement(x.m))
  if (rows.length === 0) return <p className="m-0 text-compact text-text-secondary" data-testid="move-empty">Movement tracking started recently: not enough snapshots yet to draw how these lines have moved.</p>
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: 'minmax(0, 1fr)' }} data-testid="move-pane">
      {rows.map(({ market, m }) => {
        const side = market === 'total' ? 'a' : 'b'
        const f = moveForSide(m!, side)
        const word = market === 'total' ? 'O/U' : g.home
        const fmt = (x: number | null) => (x == null ? '--' : market === 'total' ? String(x) : signedLine(x))
        return (
          <section key={market} aria-label={MARKET_NAME[market]} className="grid gap-1" data-testid={`move-${market}`}>
            <h3 className="m-0 text-[11px] font-semibold text-text-tertiary">{MARKET_NAME[market]} <span className="normal-case font-normal">since {startLabel(m!.since)}</span></h3>
            <div className="flex flex-wrap items-center gap-x-8 gap-y-1.5 text-compact">
              {market !== 'ml' && (
                <span className="inline-flex items-center gap-2">
                  <Sparkline values={m!.lineSeries.filter((x): x is number => x != null)} step w={72} h={22} color="#9ca3af" label={`${MARKET_NAME[market]} line movement`} />
                  <span className="mono text-text-primary">{m!.lineMoved ? `${word} ${fmt(m!.lineOpen)} → ${fmt(m!.lineNow)}` : `${word} ${fmt(m!.lineNow)}, unchanged`}</span>
                </span>
              )}
              <span className="inline-flex items-center gap-2">
                <Sparkline values={f.pSeries} w={72} h={22} label={`${MARKET_NAME[market]} fair price movement`} />
                {f.pOpen != null && f.pNow != null && f.pDelta != null
                  ? <span className="mono text-text-primary">{(f.pOpen * 100).toFixed(1)}% &rarr; {(f.pNow * 100).toFixed(1)}% <span className="text-text-secondary">({f.pDelta >= 0 ? '+' : ''}{(f.pDelta * 100).toFixed(1)} pts{market === 'total' ? ', over' : `, ${g.home}`})</span></span>
                  : <span className="mono text-text-tertiary">not enough comparable snapshots</span>}
              </span>
            </div>
          </section>
        )
      })}
      <p className="m-0 text-[12px] text-text-tertiary">Market fair price, de-vigged from the books. Shown, never read as a signal.</p>
    </div>
  )
}

/**
 * The card behind a row of the board. One honest sentence about the game, then tabs: one per market (a line scroller, the two big
 * best-price buttons, every line as tiles), MOVEMENT (how the line and the fair price have moved) and MODEL (where our projection disagrees with
 * the market, and why). Every book on every line is the "All books" row under a market. Esc, the backdrop and the close button dismiss it.
 */
export function GamePopup({ game, initialPane, initialLine, bettable = null, onClose }: {
  game: GameBoardEntry; initialPane?: PopupPane; initialLine?: number | null; bettable?: ReadonlySet<string> | null; ageLabel?: string; onClose: () => void
}) {
  const markets = (['ml', 'spread', 'total'] as const).filter(m => game.rows[m].length > 0)
  const first: PopupPane = initialPane ?? (markets.includes('spread') ? 'spread' : (markets[0] ?? 'move'))
  const [pane, setPane] = useState<PopupPane>(first)
  const ref = useRef<HTMLDivElement>(null)
  const tabsRef = useRef<HTMLDivElement>(null)
  useFocusTrap(ref, true)
  // on a phone the tab row scrolls sideways: keep the open tab in view (Movement / Model sit off the right edge)
  useEffect(() => { tabsRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' }) }, [pane])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = prev }
  }, [onClose])

  const tabs: { v: PopupPane; l: string }[] = [...markets.map(m => ({ v: m as PopupPane, l: MARKET_NAME[m] })), { v: 'move', l: 'Movement' }, { v: 'analysis', l: 'Model' }]
  const sport = game.sport as LogoSport
  return (
    <div className="fixed inset-0 z-[90] flex items-end sm:items-center justify-center bg-black/60" data-testid="game-popup-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={`${game.away} at ${game.home}`} data-testid="game-popup" tabIndex={-1}
        className="w-full sm:max-w-[720px] h-[88dvh] sm:h-[680px] max-h-[92dvh] flex flex-col bg-background-primary-default border border-separator-border rounded-t-xl sm:rounded-xl shadow-card">
        <header className="flex-none flex items-start justify-between gap-3 px-4 pt-3.5 pb-2">
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5"><SportLogo sport={sport} team={game.away} size={30} /><SportLogo sport={sport} team={game.home} size={30} /></span>
            <div>
              <h2 className="text-[16px] leading-[22px] font-semibold text-text-primary m-0">{game.away} @ {game.home}</h2>
              <div className="mono text-[12px] text-text-secondary">{game.sport.toUpperCase()} &middot; {startLabel(game.start)} &middot; {game.nBooks} books</div>
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="size-8 rounded-control border border-separator-border bg-transparent text-text-secondary hover:text-text-primary cursor-pointer text-[16px] leading-none">&times;</button>
        </header>
        <p className="flex-none m-0 px-4 pb-3 text-[14px] leading-[1.5] text-text-primary" data-testid="popup-headline">{gameHeadline(game)}</p>
        <AriaTabs selectedKey={pane} onSelectionChange={k => setPane(k as PopupPane)} className="contents">
          <div className="flex-none px-4 border-b border-separator-border overflow-x-auto">
            <AriaTabList ref={tabsRef} aria-label="Card" className="flex gap-1 min-w-max">
              {tabs.map(t => (
                <AriaTab key={t.v} id={t.v} data-testid={`tab-${t.v}`}
                  className="px-3 py-2.5 text-[13px] font-semibold bg-transparent border-0 cursor-pointer -mb-px border-b-2 transition-colors duration-150"
                  style={{ color: pane === t.v ? 'var(--color-text-primary)' : 'var(--color-text-secondary)', borderBottom: `2px solid ${pane === t.v ? GREEN : 'transparent'}` }}>{t.l}</AriaTab>
              ))}
            </AriaTabList>
          </div>
          {tabs.map(t => (
            <AriaTabPanel key={t.v} id={t.v} className="flex-1 min-h-0 overflow-y-auto px-4 pb-4 pt-3" data-testid={pane === t.v ? 'popup-body' : undefined}>
              {t.v === 'ml' || t.v === 'spread' || t.v === 'total'
                ? <MarketPane key={t.v} g={game} market={t.v} initialLine={t.v === first ? initialLine : undefined} bettable={bettable} />
                : t.v === 'move' ? <MovePane g={game} />
                : t.v === 'analysis' ? <ModelPane game={game} /> : null}
            </AriaTabPanel>
          ))}
        </AriaTabs>
        <footer className="flex-none px-4 py-2.5 border-t border-separator-border text-[12px] text-text-tertiary">
          Prices move: check the number at the book. Not financial advice.{' '}
          <Link className="text-link" href={`/ev/games?view=analysis&game=${encodeURIComponent(game.key)}`}>Open as a page</Link>
        </footer>
      </div>
    </div>
  )
}
