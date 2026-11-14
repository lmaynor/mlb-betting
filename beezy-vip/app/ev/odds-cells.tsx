import { bookDisplay } from '@/lib/tokens'
import { cx } from '@/utils/cx'
import { formatOdds } from '@/lib/odds'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import { type HeadshotSport } from '@/lib/sport-assets'
import { BookLogo } from '@/components/ui/book-logo'
import { STAT_LABEL, type BoardRow } from '@/lib/odds-screen'
import { HAIR } from './odds-constants'
import { startLabel } from './odds-format'

type CellState = 'best' | 'arb' | undefined

/**
 * One book's cell: two prices, the upper line first. Standard rows put the row's side on top; Arbitrage and Longshots rows
 * always put the over on top. `tint` marks a price that beats the row's fair value by CELL_EDGE; `best` is the best bettable
 * price (green text); `arb` outlines a leg of the hedge.
 */
export function PriceCell({ top, bottom, topState, bottomState, tint, best }: {
  top: number | null; bottom: number | null; topState?: CellState; bottomState?: CellState; tint?: boolean; best?: boolean
}) {
  // Two inline-block prices split by a newline (white-space: pre-line) instead of a div per line: three elements per cell, not five.
  const price = (state: CellState, main: boolean) => cx(
    'mono inline-block rounded-[3px] px-1 outline-offset-1',
    main ? 'text-[13px]' : 'text-[11px]',
    'leading-[1.3]',
    state ? 'font-semibold text-signal' : main ? 'font-semibold text-text-primary' : 'font-normal text-text-secondary',
    state === 'arb' ? 'outline outline-1 outline-signal' : 'outline-none',
  )
  return (
    <td className={cx('odds-cell', top != null || bottom != null ? 'whitespace-pre-line' : undefined)} data-best={best ? 'true' : undefined} data-edge={tint ? 'true' : undefined} style={{ borderLeft: `1px solid ${HAIR}` }}>
      {top == null && bottom == null ? <span className="mono odds-sub">&ndash;</span> : (
        <>
          <span className={price(topState, true)}>{top == null ? '\u2013' : formatOdds(top)}</span>
          {'\n'}
          <span className={price(bottomState, false)}>{bottom == null ? '\u2013' : formatOdds(bottom)}</span>
        </>
      )}
    </td>
  )
}

export function TierPill({ alt, modalPoint }: { alt: boolean; modalPoint?: number | null }) {
  const tip = alt
    ? (modalPoint != null ? `Main line: ${modalPoint}` : 'An alternate line: a point or more off the main line')
    : 'The main line'
  return (
    <span className="mono" title={tip}
      style={{ fontSize: '10px', fontWeight: 600, padding: '0 5px', borderRadius: '4px', border: `1px solid ${alt ? 'var(--color-border-button-hover)' : HAIR}`, color: alt ? 'var(--color-neutral-400)' : 'var(--color-text-secondary)' }}>
      {alt ? 'Alt' : 'Main'}
    </span>
  )
}

export function PropCells({ row, label }: { row: BoardRow; label: string }) {
  const mainLineTip = row.modal_point != null ? `Main line: ${row.modal_point}` : undefined
  return (
    <>
      <td className="odds-prop">
        <div className="flex items-center gap-2">
          <span className="shrink-0 w-[30px] h-[30px] inline-flex">
            <PlayerHeadshot sport={row.game_key.split(':')[0] as HeadshotSport} id={row.headshot_id} name={row.player} size={30} />
          </span>
          <div>
            <div className="text-cozy font-semibold text-text-primary leading-[1.3]">{row.player}</div>
            <div className="mono odds-sub text-mini flex items-center gap-[5px]">
              <span>{label} {STAT_LABEL[row.stat] ?? row.stat}</span>
              {row.is_alt === true && (
                <span title={mainLineTip} style={{ fontSize: '10px', color: 'var(--color-neutral-400)', fontFamily: 'var(--font-mono), monospace' }}>[ALT]</span>
              )}
            </div>
          </div>
        </div>
      </td>
      <td>
        <div className="mono text-[12px] text-neutral-300">{row.away} @ {row.home}</div>
        <div className="mono odds-sub">{startLabel(row.commence)}</div>
      </td>
    </>
  )
}

export function BookTag({ book, size = 16 }: { book: string; size?: number }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <BookLogo book={book} size={size} />
      <span className="text-[12px] text-neutral-300">{bookDisplay(book).label}</span>
    </span>
  )
}
