'use client'

import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useState } from 'react'
import Link from 'next/link'
import { AlignedBar } from '@/components/picks/aligned-bar'
import { BOOK_CHIPS, bookParam, SIGNAL_OPTIONS, SORT_OPTIONS } from '@/lib/picks-filters'
import { getSystemsBySport, kindOfSystem, type PickKind } from '@/lib/pick-systems'
import { LIVE_SPORTS, type SportSlug } from '@/lib/sports'
import { SYSTEM_LABEL_SHORT } from '@/lib/tokens'
import { useMyBooks } from '@/lib/use-my-books'
import { MobileBottomSheet, useIsMobile } from '@/components/ui/mobile-sheet'

const B = '1px solid var(--color-separator-border)'
const CONTROL: React.CSSProperties = {
  fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '6px 9px', borderRadius: 'var(--radius-2lg)', border: B, background: 'var(--color-background-primary-default)',
  color: 'var(--color-text-primary)',
}
// each labelled control grows to share the row, so on a phone they stack two or one to a row instead of ragged wraps
const LABEL: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--color-text-secondary)', letterSpacing: '0.08em', flex: '1 1 150px', minWidth: 0 }
const SELECT: React.CSSProperties = { flex: '1 1 auto', minWidth: 0 }

/**
 * The ONE set of filters on a picks page (every sport, and a system's page): search, market, signal, book, sort, and for a
 * settled MLB day the result. Games are picked in the games strip above and the day in the day toggle, so nothing here
 * repeats them. State lives in the query string, so the server page reads the same params (lib/picks-filters.ts) and a
 * filtered view is a shareable link.
 *
 *   sport = 'all'   the cross-sport Top 10: SPORT instead of MARKET
 *   fixedMarket     a system's page: the market is already chosen, so there is no market menu
 *   showResult      the settled MLB card
 */
export function FilterBar({ sport = 'mlb', kind, fixedMarket = false, showResult = false }: {
  sport?: SportSlug | 'all'; kind?: PickKind; fixedMarket?: boolean; showResult?: boolean
}) {
  const router = useRouter()
  const pathname = usePathname()
  const sp = useSearchParams()
  const my = useMyBooks()

  const market = sp.get('market') ?? 'ALL'
  const signal = sp.get('signal') ?? 'ALL'
  const book = sp.get('book') ?? 'ALL'
  const books = sp.get('books') ?? ''
  const sportParam = (sp.get('sport') ?? 'ALL').toLowerCase()
  const result = sp.get('result') ?? 'ALL'
  const sort = sp.get('sort') ?? 'score'
  const dir = sp.get('dir') ?? 'desc'
  const qParam = sp.get('q') ?? ''
  const mineKey = my.books.join(',')

  const push = useCallback((mutate: (p: URLSearchParams) => void) => {
    const params = new URLSearchParams(sp.toString())
    mutate(params)
    const qs = params.toString()
    router.push(qs ? `${pathname}?${qs}` : pathname, { scroll: false })
  }, [router, sp, pathname])
  const set = (key: string, value: string, dflt: string) => push(p => { if (value === dflt) p.delete(key); else p.set(key, value) })

  const systems = sport === 'all' ? [] : getSystemsBySport(sport).filter(s => sport !== 'mlb' || !kind || kindOfSystem(s.key) === kind)
  const sortOptions = sport === 'all' ? SORT_OPTIONS.filter(o => o.value === 'score' || o.value === 'edge' || o.value === 'odds') : SORT_OPTIONS
  const bookValue = books !== '' && books === mineKey ? 'MY' : book !== 'ALL' ? book.toLowerCase() : 'ALL'

  const active = [!fixedMarket && market !== 'ALL', signal !== 'ALL', bookValue !== 'ALL', sportParam !== 'all', result !== 'ALL', qParam !== ''].filter(Boolean).length
  const clear = () => push(p => { for (const k of ['market', 'signal', 'book', 'books', 'sport', 'result', 'q', 'game', 'tier']) { if (!(fixedMarket && k === 'market')) p.delete(k) } })
  const isMobile = useIsMobile(768)
  const [mobileOpen, setMobileOpen] = useState(false)

  if (isMobile) {
    return (
      <AlignedBar role="search" style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 10px' }}>
        <input
          key={qParam}
          type="search"
          aria-label="Search player, team or city"
          placeholder="Search player, team..."
          defaultValue={qParam}
          onKeyDown={e => { if (e.key === 'Enter') set('q', e.currentTarget.value.trim(), '') }}
          onBlur={e => { if (e.currentTarget.value.trim() !== qParam) set('q', e.currentTarget.value.trim(), '') }}
          style={{ ...CONTROL, flex: 1, minWidth: '100px', height: '44px' }}
        />

        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          style={{
            ...CONTROL,
            display: 'inline-flex',
            alignItems: 'center',
            gap: '5px',
            cursor: 'pointer',
            height: '44px',
            color: active > 0 ? 'var(--signal)' : 'var(--color-text-primary)',
            borderColor: active > 0 ? 'var(--signal)' : undefined,
            flexShrink: 0,
          }}
        >
          <span>Filters</span>
          {active > 0 && (
            <span className="text-micro text-signal py-[1px] px-[5px] rounded-[10px] font-bold"
              style={{ background: 'var(--win-wash)' }}
            >
              {active}
            </span>
          )}
        </button>

        {active > 0 && (
          <button
            type="button"
            onClick={clear}
            style={{ ...CONTROL, cursor: 'pointer', color: 'var(--color-neutral-300)', height: '44px', flexShrink: 0 }}
          >
            Clear
          </button>
        )}

        <MobileBottomSheet
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          title="Filter Picks"
          footer={
            <div className="flex gap-2">
              {active > 0 && (
                <button
                  type="button"
                  onClick={() => { clear(); setMobileOpen(false) }}
                  className="btn btn-ghost flex-1 py-[9px] px-0 text-compact"
                 
                >
                  Clear All
                </button>
              )}
              <button
                type="button"
                onClick={() => setMobileOpen(false)}
                className="btn btn-primary py-[9px] px-0 text-compact"
                style={{ flex: 2 }}
              >
                Done
              </button>
            </div>
          }
        >
          <div className="flex flex-col gap-3.5">
            {sport === 'all' ? (
              <div>
                <div className="ui-heading text-micro text-text-secondary mb-1.5 tracking-head">SPORT</div>
                <div className="flex gap-1 bg-background-secondary-default p-[3px]" style={{ borderRadius: '8px', border: B }}>
                  {[{ slug: 'all', shortName: 'ALL' }, ...LIVE_SPORTS].map(s => {
                    const isActive = sportParam === s.slug
                    return (
                      <button className="flex-1 min-h-9 cursor-pointer"
                        key={s.slug}
                        type="button"
                        onClick={() => set('sport', s.slug, 'all')}
                        style={{
                          borderRadius: '6px',
                          fontSize: '11.5px',
                          fontFamily: 'var(--font-mono)',
                          fontWeight: 600,
                          border: isActive ? '1px solid var(--color-border-button-hover)' : '1px solid transparent',
                          background: isActive ? 'var(--color-background-tertiary-default)' : 'transparent',
                          color: isActive ? 'var(--color-text-primary)' : 'var(--color-neutral-300)',
                        }}
                      >
                        {s.shortName}
                      </button>
                    )
                  })}
                </div>
              </div>
            ) : !fixedMarket && (
              <div className="flex flex-col gap-[6px] bg-background-secondary-default p-[10px]" style={{ borderRadius: '8px', border: B }}>
                <div className="flex items-center justify-between">
                  <span className="ui-heading text-micro text-text-secondary tracking-head">MARKET / SYSTEM</span>
                  <button
                    type="button"
                    className="btn btn-ghost py-[3px] px-2 text-mini min-h-[26px]"
                   
                    onClick={() => set('market', 'ALL', 'ALL')}
                  >
                    All Markets
                  </button>
                </div>
                <div className="grid gap-[3px] max-h-[180px] overflow-y-auto pr-[2px]" style={{ WebkitOverflowScrolling: 'touch' }}>
                  {sport === 'mlb' && (
                    <div className="flex items-center justify-between min-h-[38px] py-1 px-2"
                      style={{
                        borderRadius: '6px',
                        background: market === 'EV' ? 'rgba(87, 242, 135, 0.08)' : 'transparent',
                        border: market === 'EV' ? '1px solid rgba(87, 242, 135, 0.3)' : '1px solid transparent',
                      }}
                    >
                      <button className="bg-transparent border-0 text-left flex-1 cursor-pointer p-0"
                        type="button"
                        onClick={() => set('market', market === 'EV' ? 'ALL' : 'EV', 'ALL')}
                        aria-pressed={market === 'EV'}
                        style={{ color: market === 'EV' ? 'var(--color-text-primary)' : 'var(--color-neutral-300)', fontSize: '12px', fontWeight: market === 'EV' ? 700 : 400 }}
                      >
                        +EV alerts
                      </button>
                      <button className="tracking-fine py-[3px] px-[7px] min-h-[26px] cursor-pointer shrink-0"
                        type="button"
                        title="Select only +EV alerts"
                        aria-label="Only +EV alerts"
                        onClick={() => push(p => { p.set('market', 'EV') })}
                        style={{
                          fontSize: '10px',
                          fontFamily: 'var(--font-mono)',
                          fontWeight: 700,
                          borderRadius: '4px',
                          border: market === 'EV' ? '1px solid var(--signal)' : `1px solid var(--color-separator-border)`,
                          background: market === 'EV' ? 'var(--win-wash)' : 'transparent',
                          color: market === 'EV' ? 'var(--signal)' : 'var(--color-text-secondary)',
                        }}
                      >
                        ONLY
                      </button>
                    </div>
                  )}
                  {systems.map(s => {
                    const isActive = market === s.key
                    return (
                      <div className="flex items-center justify-between min-h-[38px] py-1 px-2"
                        key={s.key}
                        style={{
                          borderRadius: '6px',
                          background: isActive ? 'rgba(87, 242, 135, 0.08)' : 'transparent',
                          border: isActive ? '1px solid rgba(87, 242, 135, 0.3)' : '1px solid transparent',
                        }}
                      >
                        <button className="bg-transparent border-0 text-left flex-1 cursor-pointer p-0"
                          type="button"
                          onClick={() => set('market', isActive ? 'ALL' : s.key, 'ALL')}
                          aria-pressed={isActive}
                          style={{ color: isActive ? 'var(--color-text-primary)' : 'var(--color-neutral-300)', fontSize: '12px', fontWeight: isActive ? 700 : 400 }}
                        >
                          {SYSTEM_LABEL_SHORT[s.key] ?? s.shortName}
                        </button>
                        <button className="tracking-fine py-[3px] px-[7px] min-h-[26px] cursor-pointer shrink-0"
                          type="button"
                          title={`Select only ${SYSTEM_LABEL_SHORT[s.key] ?? s.shortName}`}
                          aria-label={`Only ${SYSTEM_LABEL_SHORT[s.key] ?? s.shortName}`}
                          onClick={() => push(p => { p.set('market', s.key) })}
                          style={{
                            fontSize: '10px',
                            fontFamily: 'var(--font-mono)',
                            fontWeight: 700,
                            borderRadius: '4px',
                            border: isActive ? '1px solid var(--signal)' : `1px solid var(--color-separator-border)`,
                            background: isActive ? 'var(--win-wash)' : 'transparent',
                            color: isActive ? 'var(--signal)' : 'var(--color-text-secondary)',
                          }}
                        >
                          ONLY
                        </button>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            <div>
              <div className="ui-heading text-micro text-text-secondary mb-1.5 tracking-head">SHOW</div>
              <div className="flex flex-wrap gap-1 bg-background-secondary-default p-1" style={{ borderRadius: '8px', border: B }}>
                {[{ value: 'all', label: 'Everything' }, ...SIGNAL_OPTIONS].map(o => {
                  const isActive = signal.toLowerCase() === o.value.toLowerCase()
                  return (
                    <button className="py-[6px] px-[10px] min-h-[34px] cursor-pointer"
                      key={o.value}
                      type="button"
                      onClick={() => set('signal', o.value, 'all')}
                      style={{
                        borderRadius: '6px',
                        fontSize: '11px',
                        fontFamily: 'var(--font-mono)',
                        fontWeight: 600,
                        border: isActive ? '1px solid var(--color-border-button-hover)' : '1px solid transparent',
                        background: isActive ? 'var(--color-background-tertiary-default)' : 'transparent',
                        color: isActive ? 'var(--color-text-primary)' : 'var(--color-neutral-300)',
                      }}
                    >
                      {o.label}
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="flex flex-col gap-[6px] bg-background-secondary-default p-[10px]" style={{ borderRadius: '8px', border: B }}>
              <div className="flex items-center justify-between">
                <span className="ui-heading text-micro text-text-secondary tracking-head">BOOK</span>
                <div className="flex gap-1">
                  <button
                    type="button"
                    className="btn btn-ghost py-[3px] px-2 text-mini min-h-[26px]"
                   
                    onClick={() => push(p => { p.delete('book'); p.delete('books') })}
                  >
                    All
                  </button>
                  {my.books.length > 0 && (
                    <button
                      type="button"
                      className="btn btn-ghost py-[3px] px-2 text-mini min-h-[26px]"
                     
                      onClick={() => push(p => { p.delete('book'); if (mineKey) p.set('books', mineKey) })}
                    >
                      My books
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn btn-ghost py-[3px] px-2 text-mini min-h-[26px]"
                   
                    onClick={() => push(p => { p.set('book', '__none__'); p.delete('books') })}
                  >
                    None
                  </button>
                </div>
              </div>

              <div className="grid gap-[3px] max-h-[180px] overflow-y-auto pr-[2px]" style={{ WebkitOverflowScrolling: 'touch' }}>
                {BOOK_CHIPS.map(b => {
                  const param = bookParam(b)
                  const isSelected = bookValue === param
                  return (
                    <div className="flex items-center justify-between min-h-[38px] py-1 px-2"
                      key={b}
                      style={{
                        borderRadius: '6px',
                        background: isSelected ? 'rgba(87, 242, 135, 0.08)' : 'transparent',
                        border: isSelected ? '1px solid rgba(87, 242, 135, 0.3)' : '1px solid transparent',
                      }}
                    >
                      <button className="bg-transparent border-0 text-left flex-1 cursor-pointer p-0"
                        type="button"
                        onClick={() => {
                          push(p => {
                            p.delete('books')
                            if (isSelected) p.delete('book')
                            else p.set('book', param)
                          })
                        }}
                        style={{ color: isSelected ? 'var(--color-text-primary)' : 'var(--color-neutral-300)', fontSize: '12px', fontWeight: isSelected ? 700 : 400 }}
                      >
                        {b}
                      </button>
                      <button className="tracking-fine py-[3px] px-[7px] min-h-[26px] cursor-pointer shrink-0"
                        type="button"
                        title={`Select only ${b}`}
                        aria-label={`Only ${b}`}
                        onClick={(e) => {
                          e.preventDefault()
                          e.stopPropagation()
                          push(p => {
                            p.delete('books')
                            p.set('book', param)
                          })
                        }}
                        style={{
                          fontSize: '10px',
                          fontFamily: 'var(--font-mono)',
                          fontWeight: 700,
                          borderRadius: '4px',
                          border: isSelected ? '1px solid var(--signal)' : `1px solid var(--color-separator-border)`,
                          background: isSelected ? 'var(--win-wash)' : 'transparent',
                          color: isSelected ? 'var(--signal)' : 'var(--color-text-secondary)',
                        }}
                      >
                        ONLY
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>

            {showResult && (
              <div>
                <div className="ui-heading text-micro text-text-secondary mb-1.5 tracking-head">RESULT</div>
                <div className="flex gap-1 bg-background-secondary-default p-[3px]" style={{ borderRadius: '8px', border: B }}>
                  {['all', 'win', 'loss', 'pending'].map(r => {
                    const isActive = result.toLowerCase() === r
                    return (
                      <button className="flex-1 min-h-9 uppercase cursor-pointer"
                        key={r}
                        type="button"
                        onClick={() => set('result', r, 'all')}
                        style={{
                          borderRadius: '6px',
                          fontSize: '11.5px',
                          fontFamily: 'var(--font-mono)',
                          fontWeight: 600,
                          border: isActive ? '1px solid var(--color-border-button-hover)' : '1px solid transparent',
                          background: isActive ? 'var(--color-background-tertiary-default)' : 'transparent',
                          color: isActive ? (r === 'win' ? 'var(--signal)' : r === 'loss' ? 'var(--loss)' : 'var(--color-text-primary)') : 'var(--color-neutral-300)',
                        }}
                      >
                        {r}
                      </button>
                    )
                  })}
                </div>
              </div>
            )}

            <div>
              <div className="ui-heading text-micro text-text-secondary mb-1.5 tracking-head">SORT BY</div>
              <div className="flex flex-wrap gap-1 bg-background-secondary-default p-1 mb-[6px]" style={{ borderRadius: '8px', border: B }}>
                {sortOptions.map(o => {
                  const isActive = sort === o.value
                  return (
                    <button className="min-h-[34px] cursor-pointer"
                      key={o.value}
                      type="button"
                      onClick={() => push(p => { if (o.value === 'score') p.delete('sort'); else p.set('sort', o.value) })}
                      style={{
                        flex: '1 1 45%',
                        borderRadius: '6px',
                        fontSize: '11px',
                        fontFamily: 'var(--font-mono)',
                        fontWeight: 600,
                        border: isActive ? '1px solid var(--color-border-button-hover)' : '1px solid transparent',
                        background: isActive ? 'var(--color-background-tertiary-default)' : 'transparent',
                        color: isActive ? 'var(--color-text-primary)' : 'var(--color-neutral-300)',
                      }}
                    >
                      {o.label}
                    </button>
                  )
                })}
              </div>

              <div className="flex gap-1 bg-background-secondary-default p-[3px]" style={{ borderRadius: '8px', border: B }}>
                <button className="flex-1 min-h-[34px] cursor-pointer"
                  type="button"
                  onClick={() => push(p => { p.delete('dir') })}
                  style={{
                    borderRadius: '6px',
                    fontSize: '11px',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 600,
                    border: dir === 'desc' ? '1px solid var(--color-border-button-hover)' : '1px solid transparent',
                    background: dir === 'desc' ? 'var(--color-background-tertiary-default)' : 'transparent',
                    color: dir === 'desc' ? 'var(--color-text-primary)' : 'var(--color-neutral-300)',
                  }}
                >
                  High to Low ({'\u2193'})
                </button>
                <button className="flex-1 min-h-[34px] cursor-pointer"
                  type="button"
                  onClick={() => push(p => { p.set('dir', 'asc') })}
                  style={{
                    borderRadius: '6px',
                    fontSize: '11px',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 600,
                    border: dir === 'asc' ? '1px solid var(--color-border-button-hover)' : '1px solid transparent',
                    background: dir === 'asc' ? 'var(--color-background-tertiary-default)' : 'transparent',
                    color: dir === 'asc' ? 'var(--color-text-primary)' : 'var(--color-neutral-300)',
                  }}
                >
                  Low to High ({'\u2191'})
                </button>
              </div>
            </div>

            {my.books.length === 0 && bookValue === 'MY' && (
              <span className="text-mini text-text-secondary">Choose your books on the <Link className="text-link" href="/ev">Odds Terminal</Link> first.</span>
            )}
          </div>
        </MobileBottomSheet>
      </AlignedBar>
    )
  }

  return (
    <AlignedBar role="search" style={{ display: 'flex', alignItems: 'center', gap: '8px 14px', flexWrap: 'wrap' }}>
      <input key={qParam} type="search" aria-label="Search player, team or city" placeholder="Player, team or city" defaultValue={qParam}
        onKeyDown={e => { if (e.key === 'Enter') set('q', e.currentTarget.value.trim(), '') }}
        onBlur={e => { if (e.currentTarget.value.trim() !== qParam) set('q', e.currentTarget.value.trim(), '') }}
        style={{ ...CONTROL, flex: '1 1 180px', minWidth: '160px', maxWidth: '260px' }} />

      {sport === 'all' ? (
        <label style={LABEL}>SPORT
          <select aria-label="Sport" value={sportParam} onChange={e => set('sport', e.target.value, 'all')} style={{ ...CONTROL, ...SELECT }}>
            <option value="all">All sports</option>
            {LIVE_SPORTS.map(p => <option key={p.slug} value={p.slug}>{p.shortName}</option>)}
          </select>
        </label>
      ) : !fixedMarket && (
        <label style={LABEL}>MARKET
          <select aria-label="Market" value={market} onChange={e => set('market', e.target.value, 'ALL')} style={{ ...CONTROL, ...SELECT }}>
            <option value="ALL">All</option>
            {sport === 'mlb' && <option value="EV">+EV alerts</option>}
            {systems.map(s => <option key={s.key} value={s.key}>{SYSTEM_LABEL_SHORT[s.key] ?? s.shortName}</option>)}
          </select>
        </label>
      )}

      <label style={LABEL}>SHOW
        <select aria-label="Signal" value={signal.toLowerCase()} onChange={e => set('signal', e.target.value, 'all')} style={{ ...CONTROL, ...SELECT }}>
          <option value="all">Everything</option>
          {SIGNAL_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>

      <label style={LABEL}>BOOK
        <select aria-label="Book" value={bookValue}
          onChange={e => {
            const v = e.target.value
            push(p => {
              p.delete('book'); p.delete('books')
              if (v === 'MY') { if (mineKey) p.set('books', mineKey) } else if (v !== 'ALL') p.set('book', BOOK_CHIPS.find(b => bookParam(b) === v) ?? v)
            })
          }} style={{ ...CONTROL, ...SELECT }}>
          <option value="ALL">All books</option>
          <option value="MY">{my.books.length > 0 ? `My books (${my.books.length})` : 'My books (none set)'}</option>
          {BOOK_CHIPS.map(b => <option key={b} value={bookParam(b)}>{b}</option>)}
        </select>
      </label>

      {showResult && (
        <label style={LABEL}>RESULT
          <select aria-label="Result" value={result.toLowerCase()} onChange={e => set('result', e.target.value, 'all')} style={{ ...CONTROL, ...SELECT }}>
            <option value="all">All</option><option value="win">Win</option><option value="loss">Loss</option><option value="pending">Pending</option>
          </select>
        </label>
      )}

      <label style={{ ...LABEL, flex: '1 1 220px' }}>SORT
        <select aria-label="Sort" value={sort} onChange={e => push(p => { if (e.target.value === 'score') p.delete('sort'); else p.set('sort', e.target.value) })} style={{ ...CONTROL, ...SELECT }}>
          {sortOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <button type="button" aria-label={dir === 'desc' ? 'Sorted high to low; switch to low to high' : 'Sorted low to high; switch to high to low'}
          onClick={() => push(p => { if (dir === 'desc') p.set('dir', 'asc'); else p.delete('dir') })} style={{ ...CONTROL, cursor: 'pointer' }}>
          {dir === 'desc' ? 'High-Low' : 'Low-High'}
        </button>
      </label>

      {active > 0 && (
        <button type="button" onClick={clear} style={{ ...CONTROL, cursor: 'pointer', color: 'var(--color-neutral-300)' }}>Clear {active}</button>
      )}
      {my.books.length === 0 && bookValue === 'MY' && (
        <span className="text-mini text-text-secondary">Choose your books on the <Link className="text-link" href="/ev">Odds Terminal</Link> first.</span>
      )}
    </AlignedBar>
  )
}
