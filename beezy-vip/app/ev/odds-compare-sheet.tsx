import { bookDisplay } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'
import { BookLogo } from '@/components/ui/book-logo'
import { MobileBottomSheet } from '@/components/ui/mobile-sheet'
import { STAT_LABEL, type ArbRow, type LongshotRow, type MiddleResult, type ScreenRow } from '@/lib/odds-screen'
import { HAIR, GREEN } from './odds-constants'
import { startLabel, signed } from './odds-format'

export function MobileCompareSheet({
  item,
  columns,
  onClose,
}: {
  item:
    | { type: 'ev'; row: ScreenRow }
    | { type: 'arb'; row: ArbRow }
    | { type: 'middles'; row: MiddleResult }
    | { type: 'longshots'; row: LongshotRow }
    | null
  columns: { key: string; offshore: boolean }[]
  onClose: () => void
}) {
  if (!item) return null

  const row = item.type === 'middles' ? item.row.low : item.row.row
  const byBook = new Map(row.books.map(b => [b.book, b]))

  let title = row.player
  const subtitle = `${row.away} @ ${row.home} \u00b7 ${startLabel(row.commence)}`
  if (item.type === 'ev') {
    title = `${row.player} \u00b7 ${item.row.label} ${STAT_LABEL[row.stat] ?? row.stat}`
  } else if (item.type === 'middles') {
    title = `${row.player} \u00b7 o${item.row.low.line} / u${item.row.high.line} ${STAT_LABEL[row.stat] ?? row.stat}`
  } else if (item.type === 'arb' || item.type === 'longshots') {
    title = `${row.player} \u00b7 ${row.line} ${STAT_LABEL[row.stat] ?? row.stat}`
  }

  return (
    <MobileBottomSheet
      open={Boolean(item)}
      onClose={onClose}
      title={title}
      subtitle={subtitle}
      maxHeight="85vh"
    >
      <div className="flex flex-col gap-3.5">
        {item.type === 'ev' && (
          <div className="grid grid-cols-2 gap-2">
            <div style={{ padding: '8px 10px', background: 'var(--color-background-secondary-default)', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
              <div className="mono text-micro text-text-secondary uppercase">Consensus Fair</div>
              <div className="mono text-[14px] font-bold text-text-primary mt-0.5">
                {item.row.fairAmerican != null ? (item.row.fairAmerican > 0 ? `+${item.row.fairAmerican}` : `${item.row.fairAmerican}`) : '--'}
              </div>
              <div className="mono mt-0.5 text-[10.5px] text-text-secondary">
                {item.row.nRef} reference books
              </div>
            </div>

            <div style={{ padding: '8px 10px', background: 'var(--color-background-secondary-default)', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
              <div className="mono text-micro text-text-secondary uppercase">Best Odds &middot; {bookDisplay(item.row.bestBook).label}</div>
              <div className="mono" style={{ fontSize: '14px', fontWeight: 700, color: GREEN, marginTop: '2px' }}>
                {item.row.side} {formatOdds(item.row.bestAmerican)}
              </div>
              <div className="mono" style={{ fontSize: '10.5px', color: GREEN, marginTop: '2px' }}>
                {item.row.ev != null ? `${signed(item.row.ev * 100)}% EV` : '--'}
              </div>
            </div>
          </div>
        )}

        <div>
          <div className="ui-heading text-micro text-text-secondary mb-2 tracking-head">
            ALL REPORTING BOOKS ({row.books.length})
          </div>
          <div className="flex flex-col gap-1.5">
            {columns.map(c => {
              const q = byBook.get(c.key)
              if (!q || (q.over == null && q.under == null)) return null
              const isBest = item.type === 'ev' && item.row.bestTies.includes(c.key)
              return (
                <div
                  key={c.key}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '8px 10px',
                    borderRadius: '6px',
                    background: isBest ? 'rgba(87, 242, 135, 0.06)' : 'var(--color-background-secondary-default)',
                    border: `1px solid ${isBest ? 'rgba(87, 242, 135, 0.35)' : HAIR}`,
                  }}
                >
                  <div className="flex items-center gap-2">
                    <BookLogo book={c.key} size={22} />
                    <div>
                      <div className="text-compact font-semibold text-text-primary">
                        {bookDisplay(c.key).label}
                        {c.offshore && <span className="text-micro text-text-secondary ml-1">(offshore)</span>}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-3">
                    <div className="text-right">
                      <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: isBest && item.type === 'ev' && item.row.side === 'OVER' ? GREEN : 'var(--color-text-primary)' }}>
                        O {q.over != null ? formatOdds(q.over) : '--'}
                      </span>
                      <span style={{ margin: '0 4px', color: 'var(--color-text-secondary)' }}>/</span>
                      <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: isBest && item.type === 'ev' && item.row.side === 'UNDER' ? GREEN : 'var(--color-text-primary)' }}>
                        U {q.under != null ? formatOdds(q.under) : '--'}
                      </span>
                    </div>
                    {isBest && (
                      <span className="mono" style={{ fontSize: '10px', fontWeight: 700, color: GREEN, padding: '1px 5px', borderRadius: '3px', background: 'rgba(87, 242, 135, 0.15)' }}>
                        BEST
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </MobileBottomSheet>
  )
}
