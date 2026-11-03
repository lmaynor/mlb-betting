'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { BookLogo } from '@/components/ui/book-logo'
import { SportLogo } from '@/components/ui/sport-assets'
import { useFocusTrap } from '@/components/ui/use-focus-trap'
import type { GameBoardEntry } from '@/lib/game-board'
import { altRows, gameHeadline, shopMarket, type ShopSide } from '@/lib/game-shop'
import { signedLine, startLabel, type GameMarket } from '@/lib/game-odds'
import { hasMovement, moveForSide } from '@/lib/line-move'
import { formatOdds } from '@/lib/odds'
import { bookDisplay } from '@/lib/tokens'
import { Seg } from './game-detail'
import { GREEN, signedPct } from './game-prices-parts'
import { Sparkline } from './sparkline'

export type PopupPane = 'shop' | 'move' | 'alt'
type LogoSport = Parameters<typeof SportLogo>[0]['sport']

const MARKET_NAME: Record<GameMarket, string> = { ml: 'Moneyline', spread: 'Spread', total: 'Total' }

/** One side's best price as a short line: label, the price at its book, and the EV chip only when it is an edge (amber "check" when it is too big to trust). */
export function PriceChip({ s, compact }: { s: ShopSide; compact?: boolean }) {
  if (s.american == null || s.book == null) return <span className="text-text-tertiary">--</span>
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap" data-testid="price-chip">
      <BookLogo book={s.book} size={compact ? 14 : 18} />
      <b className="mono font-semibold" style={{ color: s.actionable ? GREEN : 'var(--color-text-primary)' }}>{formatOdds(s.american)}</b>
      {s.actionable && s.ev != null && <span className="mono text-[10.5px] font-semibold rounded-chip px-1" style={{ color: GREEN, background: 'rgba(87,242,135,0.10)' }}>{signedPct(s.ev)}</span>}
      {s.suspect && <span className="mono text-[10px] font-semibold tracking-fine text-warn" title="Unusually large against the market fair price: often a stale quote. Check the price at the book.">CHECK</span>}
    </span>
  )
}

function ShopPane({ g }: { g: GameBoardEntry }) {
  const markets = (['ml', 'spread', 'total'] as const).map(m => shopMarket(g, m)).filter((x): x is NonNullable<typeof x> => !!x)
  if (markets.length === 0) return <p className="m-0 text-compact text-text-secondary">No book has posted this game's lines yet.</p>
  return (
    <div className="grid gap-3" data-testid="shop-pane">
      {markets.map(sm => (
        <section key={sm.market} aria-label={MARKET_NAME[sm.market]}>
          <h3 className="m-0 mb-1 text-[11px] font-semibold tracking-fine uppercase text-text-tertiary">
            {MARKET_NAME[sm.market]}{sm.row.row.line != null ? <span className="mono"> &middot; {sm.market === 'total' ? sm.row.row.line : `${g.home} ${signedLine(sm.row.row.line)}`}</span> : null}
          </h3>
          {[sm.a, sm.b].map(s => (
            <div key={s.label} data-testid="shop-row" className="grid grid-cols-1 sm:grid-cols-[minmax(84px,120px)_minmax(0,1fr)] items-center gap-x-3 gap-y-0.5 py-1.5 border-t border-t-[color:var(--color-separator-border)]">
              <span className="text-compact font-semibold text-text-primary">{s.label}</span>
              <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <PriceChip s={s} />
                {s.others.length > 0 && (
                  <span className="mono text-[11.5px] text-text-tertiary" title="The next best prices at other books">
                    also {s.others.map(o => `${bookDisplay(o.book).short} ${formatOdds(o.american)}`).join(' · ')}
                  </span>
                )}
              </span>
            </div>
          ))}
        </section>
      ))}
    </div>
  )
}

function MovePane({ g }: { g: GameBoardEntry }) {
  if (!g.hasHistory) return <p className="m-0 text-compact text-text-secondary" data-testid="move-empty">Line movement is not available yet.</p>
  const rows = (['spread', 'total', 'ml'] as const).map(market => ({ market, m: g.move[market] })).filter(x => hasMovement(x.m))
  if (rows.length === 0) return <p className="m-0 text-compact text-text-secondary" data-testid="move-empty">Movement tracking started recently: not enough snapshots yet to draw how these lines have moved.</p>
  return (
    <div className="grid gap-3" data-testid="move-pane">
      {rows.map(({ market, m }) => {
        const side = market === 'total' ? 'a' : 'b'
        const f = moveForSide(m!, side)
        const word = market === 'total' ? 'O/U' : g.home
        const fmt = (x: number | null) => (x == null ? '--' : market === 'total' ? String(x) : signedLine(x))
        return (
          <section key={market} aria-label={MARKET_NAME[market]} className="grid gap-1" data-testid={`move-${market}`}>
            <h3 className="m-0 text-[11px] font-semibold tracking-fine uppercase text-text-tertiary">{MARKET_NAME[market]} <span className="normal-case font-normal">since {startLabel(m!.since)}</span></h3>
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
      <p className="m-0 text-[11.5px] text-text-tertiary">Market fair price, de-vigged from the books. Shown, never read as a signal.</p>
    </div>
  )
}

const ALT_WINDOW = 9

function AltPane({ g }: { g: GameBoardEntry }) {
  const avail = (['spread', 'total'] as const).filter(m => g.rows[m].length > 1)
  const [market, setMarket] = useState<'spread' | 'total'>(avail[0] ?? 'spread')
  const [all, setAll] = useState(false)
  if (avail.length === 0) return <p className="m-0 text-compact text-text-secondary" data-testid="alt-empty">This game has no alternate lines posted.</p>
  const rows = altRows(g, market)
  const mi = Math.max(0, rows.findIndex(r => r.main))
  const start = Math.max(0, Math.min(rows.length - ALT_WINDOW, mi - Math.floor(ALT_WINDOW / 2)))
  const shown = all || rows.length <= ALT_WINDOW ? rows : rows.slice(start, start + ALT_WINDOW)
  return (
    <div data-testid="alt-pane">
      {avail.length > 1 && <div className="mb-2"><Seg label="Alt market" value={market} options={avail.map(m => ({ v: m, l: MARKET_NAME[m] }))} onChange={v => { setMarket(v); setAll(false) }} /></div>}
      <div className="grid text-[11px] text-text-tertiary pb-1" style={{ gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)' }}><span>{market === 'total' ? 'Over' : g.away} best price</span><span>{market === 'total' ? 'Under' : g.home} best price</span></div>
      {shown.map(r => (
        <div key={r.row.key} data-testid="alt-row" data-line={r.line ?? ''} className="grid items-center gap-x-3 py-1.5 border-t border-t-[color:var(--color-separator-border)]" style={{ gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', opacity: r.thin ? 0.7 : 1 }}>
          <span className="inline-flex items-center gap-2 flex-wrap"><span className="text-compact font-semibold text-text-primary">{r.a.label}</span><PriceChip s={r.a} compact /></span>
          <span className="inline-flex items-center gap-2 flex-wrap"><span className="text-compact font-semibold text-text-primary">{r.b.label}</span><PriceChip s={r.b} compact />
            {r.main && <span className="mono text-[9px] font-semibold tracking-fine" style={{ color: GREEN }}>MAIN</span>}
            {r.thin && <span className="mono text-[9px] font-semibold tracking-fine text-warn" title="Fewer than 3 books: no edge is claimed">THIN</span>}
          </span>
        </div>
      ))}
      {rows.length > ALT_WINDOW && <button type="button" onClick={() => setAll(a => !a)} data-testid="alt-all" className="mt-2 text-[12.5px] text-link cursor-pointer bg-transparent border-0 p-0 hover:underline">{all ? 'Show nearby lines only' : `Show all ${rows.length} lines`}</button>}
    </div>
  )
}

/**
 * The brief card behind a row of the board: one honest sentence about the game, then three panes. SHOP is where to bet each side
 * (best price at each book, the next best elsewhere, an EV chip only when it is a real edge), MOVE is how the line and the fair price
 * have moved, ALT is the alternate lines. The full analysis page is one link away. Esc, the backdrop and the close button dismiss it.
 */
export function GamePopup({ game, initialPane = 'shop', onClose }: { game: GameBoardEntry; initialPane?: PopupPane; onClose: () => void }) {
  const [pane, setPane] = useState<PopupPane>(initialPane)
  const ref = useRef<HTMLDivElement>(null)
  useFocusTrap(ref, true)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = prev }
  }, [onClose])

  const sport = game.sport as LogoSport
  return (
    <div className="fixed inset-0 z-[90] flex items-end sm:items-center justify-center bg-black/60" data-testid="game-popup-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={`${game.away} at ${game.home}`} data-testid="game-popup" tabIndex={-1}
        className="w-full sm:max-w-[640px] max-h-[88vh] overflow-y-auto bg-background-primary-default border border-separator-border rounded-t-xl sm:rounded-xl shadow-card">
        <header className="flex items-start justify-between gap-3 px-4 pt-3.5 pb-2">
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5"><SportLogo sport={sport} team={game.away} size={26} /><SportLogo sport={sport} team={game.home} size={26} /></span>
            <div>
              <h2 className="text-[16px] leading-[22px] font-bold text-text-primary m-0">{game.away} @ {game.home}</h2>
              <div className="mono text-[12px] text-text-secondary">{game.sport.toUpperCase()} &middot; {startLabel(game.start)} &middot; {game.nBooks} books</div>
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="size-8 rounded-control border border-separator-border bg-transparent text-text-secondary hover:text-text-primary cursor-pointer text-[16px] leading-none">&times;</button>
        </header>
        <p className="m-0 px-4 pb-3 text-[13.5px] leading-[1.5] text-text-primary" data-testid="popup-headline">{gameHeadline(game)}</p>
        <div className="px-4 pb-3"><Seg label="Card" value={pane} options={[{ v: 'shop', l: 'Shop' }, { v: 'move', l: 'Movement' }, { v: 'alt', l: 'Alt lines' }]} onChange={v => setPane(v)} /></div>
        <div className="px-4 pb-4 border-t border-separator-border pt-3">
          {pane === 'shop' && <ShopPane g={game} />}
          {pane === 'move' && <MovePane g={game} />}
          {pane === 'alt' && <AltPane g={game} />}
        </div>
        <footer className="flex items-center justify-between gap-3 px-4 py-2.5 border-t border-separator-border text-[12px] text-text-tertiary">
          <span>Prices move: check the number at the book. Not financial advice.</span>
          <Link className="text-link whitespace-nowrap" href={`/ev/games?view=analysis&game=${encodeURIComponent(game.key)}`}>Full analysis &rarr;</Link>
        </footer>
      </div>
    </div>
  )
}
