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

const B = '1px solid var(--basalt)'
const CONTROL: React.CSSProperties = {
  fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '6px 9px', borderRadius: 'var(--radius)', border: B, background: 'var(--graphite)',
  color: 'var(--chalk)',
}
// each labelled control grows to share the row, so on a phone they stack two or one to a row instead of ragged wraps
const LABEL: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--fog)', letterSpacing: '0.08em', flex: '1 1 150px', minWidth: 0 }
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
          style={{ ...CONTROL, flex: 1, minWidth: '100px', height: '34px' }}
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
            height: '34px',
            color: active > 0 ? 'var(--signal)' : 'var(--chalk)',
            borderColor: active > 0 ? 'var(--signal)' : undefined,
            flexShrink: 0,
          }}
        >
          <span>Filters</span>
          {active > 0 && (
            <span
              style={{
                fontSize: '10px',
                background: 'var(--win-wash)',
                color: 'var(--signal)',
                padding: '1px 5px',
                borderRadius: '10px',
                fontWeight: 700,
              }}
            >
              {active}
            </span>
          )}
        </button>

        {active > 0 && (
          <button
            type="button"
            onClick={clear}
            style={{ ...CONTROL, cursor: 'pointer', color: 'var(--silver)', height: '34px', flexShrink: 0 }}
          >
            Clear
          </button>
        )}

        <MobileBottomSheet
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          title="Filter Picks"
          footer={
            <div style={{ display: 'flex', gap: '8px' }}>
              {active > 0 && (
                <button
                  type="button"
                  onClick={() => { clear(); setMobileOpen(false) }}
                  className="btn btn-ghost"
                  style={{ flex: 1, padding: '9px 0', fontSize: '12px' }}
                >
                  Clear All
                </button>
              )}
              <button
                type="button"
                onClick={() => setMobileOpen(false)}
                className="btn btn-primary"
                style={{ flex: 2, padding: '9px 0', fontSize: '12px' }}
              >
                Done
              </button>
            </div>
          }
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            {sport === 'all' ? (
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px', letterSpacing: '0.06em' }}>SPORT</div>
                <div style={{ display: 'flex', gap: '4px', background: '#090a0f', padding: '3px', borderRadius: '8px', border: B }}>
                  {[{ slug: 'all', shortName: 'ALL' }, ...LIVE_SPORTS].map(s => {
                    const isActive = sportParam === s.slug
                    return (
                      <button
                        key={s.slug}
                        type="button"
                        onClick={() => set('sport', s.slug, 'all')}
                        style={{
                          flex: 1,
                          minHeight: '36px',
                          borderRadius: '6px',
                          fontSize: '11.5px',
                          fontFamily: 'var(--font-mono)',
                          fontWeight: 600,
                          border: isActive ? '1px solid #3a4156' : '1px solid transparent',
                          background: isActive ? '#1b1f2b' : 'transparent',
                          color: isActive ? '#ffffff' : 'var(--silver)',
                          cursor: 'pointer',
                        }}
                      >
                        {s.shortName}
                      </button>
                    )
                  })}
                </div>
              </div>
            ) : !fixedMarket && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', background: '#090a0f', padding: '10px', borderRadius: '8px', border: B }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.06em' }}>MARKET / SYSTEM</span>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    style={{ padding: '3px 8px', fontSize: '11px', minHeight: '26px' }}
                    onClick={() => set('market', 'ALL', 'ALL')}
                  >
                    All Markets
                  </button>
                </div>
                <div style={{ display: 'grid', gap: '3px', maxHeight: '180px', overflowY: 'auto', WebkitOverflowScrolling: 'touch', paddingRight: '2px' }}>
                  {sport === 'mlb' && (
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        minHeight: '38px',
                        padding: '4px 8px',
                        borderRadius: '6px',
                        background: market === 'EV' ? 'rgba(87, 242, 135, 0.08)' : 'transparent',
                        border: market === 'EV' ? '1px solid rgba(87, 242, 135, 0.3)' : '1px solid transparent',
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => set('market', 'EV', 'ALL')}
                        style={{ background: 'transparent', border: 'none', color: market === 'EV' ? 'var(--chalk)' : 'var(--silver)', fontSize: '12px', fontWeight: market === 'EV' ? 700 : 400, textAlign: 'left', flex: 1, cursor: 'pointer', padding: 0 }}
                      >
                        +EV alerts
                      </button>
                      <button
                        type="button"
                        title="Select only +EV alerts"
                        aria-label="Only +EV alerts"
                        onClick={() => set('market', 'EV', 'ALL')}
                        style={{
                          fontSize: '10px',
                          fontFamily: 'var(--font-mono)',
                          fontWeight: 700,
                          letterSpacing: '0.04em',
                          padding: '3px 7px',
                          minHeight: '26px',
                          borderRadius: '4px',
                          border: market === 'EV' ? '1px solid var(--signal)' : `1px solid var(--basalt)`,
                          background: market === 'EV' ? 'var(--win-wash)' : 'transparent',
                          color: market === 'EV' ? 'var(--signal)' : 'var(--fog)',
                          cursor: 'pointer',
                          flexShrink: 0,
                        }}
                      >
                        ONLY
                      </button>
                    </div>
                  )}
                  {systems.map(s => {
                    const isActive = market === s.key
                    return (
                      <div
                        key={s.key}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          minHeight: '38px',
                          padding: '4px 8px',
                          borderRadius: '6px',
                          background: isActive ? 'rgba(87, 242, 135, 0.08)' : 'transparent',
                          border: isActive ? '1px solid rgba(87, 242, 135, 0.3)' : '1px solid transparent',
                        }}
                      >
                        <button
                          type="button"
                          onClick={() => set('market', s.key, 'ALL')}
                          style={{ background: 'transparent', border: 'none', color: isActive ? 'var(--chalk)' : 'var(--silver)', fontSize: '12px', fontWeight: isActive ? 700 : 400, textAlign: 'left', flex: 1, cursor: 'pointer', padding: 0 }}
                        >
                          {SYSTEM_LABEL_SHORT[s.key] ?? s.shortName}
                        </button>
                        <button
                          type="button"
                          title={`Select only ${SYSTEM_LABEL_SHORT[s.key] ?? s.shortName}`}
                          aria-label={`Only ${SYSTEM_LABEL_SHORT[s.key] ?? s.shortName}`}
                          onClick={() => set('market', s.key, 'ALL')}
                          style={{
                            fontSize: '10px',
                            fontFamily: 'var(--font-mono)',
                            fontWeight: 700,
                            letterSpacing: '0.04em',
                            padding: '3px 7px',
                            minHeight: '26px',
                            borderRadius: '4px',
                            border: isActive ? '1px solid var(--signal)' : `1px solid var(--basalt)`,
                            background: isActive ? 'var(--win-wash)' : 'transparent',
                            color: isActive ? 'var(--signal)' : 'var(--fog)',
                            cursor: 'pointer',
                            flexShrink: 0,
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
              <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px', letterSpacing: '0.06em' }}>SHOW</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', background: '#090a0f', padding: '4px', borderRadius: '8px', border: B }}>
                {[{ value: 'all', label: 'Everything' }, ...SIGNAL_OPTIONS].map(o => {
                  const isActive = signal.toLowerCase() === o.value.toLowerCase()
                  return (
                    <button
                      key={o.value}
                      type="button"
                      onClick={() => set('signal', o.value, 'all')}
                      style={{
                        padding: '6px 10px',
                        minHeight: '34px',
                        borderRadius: '6px',
                        fontSize: '11px',
                        fontFamily: 'var(--font-mono)',
                        fontWeight: 600,
                        border: isActive ? '1px solid #3a4156' : '1px solid transparent',
                        background: isActive ? '#1b1f2b' : 'transparent',
                        color: isActive ? '#ffffff' : 'var(--silver)',
                        cursor: 'pointer',
                      }}
                    >
                      {o.label}
                    </button>
                  )
                })}
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', background: '#090a0f', padding: '10px', borderRadius: '8px', border: B }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.06em' }}>BOOK</span>
                <div style={{ display: 'flex', gap: '4px' }}>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    style={{ padding: '3px 8px', fontSize: '11px', minHeight: '26px' }}
                    onClick={() => push(p => { p.delete('book'); p.delete('books') })}
                  >
                    All
                  </button>
                  {my.books.length > 0 && (
                    <button
                      type="button"
                      className="btn btn-ghost"
                      style={{ padding: '3px 8px', fontSize: '11px', minHeight: '26px' }}
                      onClick={() => push(p => { p.delete('book'); if (mineKey) p.set('books', mineKey) })}
                    >
                      My books
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn btn-ghost"
                    style={{ padding: '3px 8px', fontSize: '11px', minHeight: '26px' }}
                    onClick={() => push(p => { p.set('book', '__none__'); p.delete('books') })}
                  >
                    None
                  </button>
                </div>
              </div>

              <div style={{ display: 'grid', gap: '3px', maxHeight: '180px', overflowY: 'auto', WebkitOverflowScrolling: 'touch', paddingRight: '2px' }}>
                {BOOK_CHIPS.map(b => {
                  const param = bookParam(b)
                  const isSelected = bookValue === param
                  return (
                    <div
                      key={b}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        minHeight: '38px',
                        padding: '4px 8px',
                        borderRadius: '6px',
                        background: isSelected ? 'rgba(87, 242, 135, 0.08)' : 'transparent',
                        border: isSelected ? '1px solid rgba(87, 242, 135, 0.3)' : '1px solid transparent',
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => {
                          push(p => {
                            p.delete('books')
                            if (isSelected) p.delete('book')
                            else p.set('book', param)
                          })
                        }}
                        style={{ background: 'transparent', border: 'none', color: isSelected ? 'var(--chalk)' : 'var(--silver)', fontSize: '12px', fontWeight: isSelected ? 700 : 400, textAlign: 'left', flex: 1, cursor: 'pointer', padding: 0 }}
                      >
                        {b}
                      </button>
                      <button
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
                          letterSpacing: '0.04em',
                          padding: '3px 7px',
                          minHeight: '26px',
                          borderRadius: '4px',
                          border: isSelected ? '1px solid var(--signal)' : `1px solid var(--basalt)`,
                          background: isSelected ? 'var(--win-wash)' : 'transparent',
                          color: isSelected ? 'var(--signal)' : 'var(--fog)',
                          cursor: 'pointer',
                          flexShrink: 0,
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
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px', letterSpacing: '0.06em' }}>RESULT</div>
                <div style={{ display: 'flex', gap: '4px', background: '#090a0f', padding: '3px', borderRadius: '8px', border: B }}>
                  {['all', 'win', 'loss', 'pending'].map(r => {
                    const isActive = result.toLowerCase() === r
                    return (
                      <button
                        key={r}
                        type="button"
                        onClick={() => set('result', r, 'all')}
                        style={{
                          flex: 1,
                          minHeight: '36px',
                          borderRadius: '6px',
                          fontSize: '11.5px',
                          fontFamily: 'var(--font-mono)',
                          fontWeight: 600,
                          textTransform: 'uppercase',
                          border: isActive ? '1px solid #3a4156' : '1px solid transparent',
                          background: isActive ? '#1b1f2b' : 'transparent',
                          color: isActive ? (r === 'win' ? 'var(--signal)' : r === 'loss' ? 'var(--loss)' : '#ffffff') : 'var(--silver)',
                          cursor: 'pointer',
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
              <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px', letterSpacing: '0.06em' }}>SORT BY</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', background: '#090a0f', padding: '4px', borderRadius: '8px', border: B, marginBottom: '6px' }}>
                {sortOptions.map(o => {
                  const isActive = sort === o.value
                  return (
                    <button
                      key={o.value}
                      type="button"
                      onClick={() => push(p => { if (o.value === 'score') p.delete('sort'); else p.set('sort', o.value) })}
                      style={{
                        flex: '1 1 45%',
                        minHeight: '34px',
                        borderRadius: '6px',
                        fontSize: '11px',
                        fontFamily: 'var(--font-mono)',
                        fontWeight: 600,
                        border: isActive ? '1px solid #3a4156' : '1px solid transparent',
                        background: isActive ? '#1b1f2b' : 'transparent',
                        color: isActive ? '#ffffff' : 'var(--silver)',
                        cursor: 'pointer',
                      }}
                    >
                      {o.label}
                    </button>
                  )
                })}
              </div>

              <div style={{ display: 'flex', gap: '4px', background: '#090a0f', padding: '3px', borderRadius: '8px', border: B }}>
                <button
                  type="button"
                  onClick={() => push(p => { p.delete('dir') })}
                  style={{
                    flex: 1,
                    minHeight: '34px',
                    borderRadius: '6px',
                    fontSize: '11px',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 600,
                    border: dir === 'desc' ? '1px solid #3a4156' : '1px solid transparent',
                    background: dir === 'desc' ? '#1b1f2b' : 'transparent',
                    color: dir === 'desc' ? '#ffffff' : 'var(--silver)',
                    cursor: 'pointer',
                  }}
                >
                  High to Low (\u2193)
                </button>
                <button
                  type="button"
                  onClick={() => push(p => { p.set('dir', 'asc') })}
                  style={{
                    flex: 1,
                    minHeight: '34px',
                    borderRadius: '6px',
                    fontSize: '11px',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 600,
                    border: dir === 'asc' ? '1px solid #3a4156' : '1px solid transparent',
                    background: dir === 'asc' ? '#1b1f2b' : 'transparent',
                    color: dir === 'asc' ? '#ffffff' : 'var(--silver)',
                    cursor: 'pointer',
                  }}
                >
                  Low to High (\u2191)
                </button>
              </div>
            </div>

            {my.books.length === 0 && bookValue === 'MY' && (
              <span style={{ fontSize: '11px', color: 'var(--fog)' }}>Choose your books on the <Link href="/ev" style={{ color: 'var(--link)' }}>Odds Terminal</Link> first.</span>
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
        <button type="button" onClick={clear} style={{ ...CONTROL, cursor: 'pointer', color: 'var(--silver)' }}>Clear {active}</button>
      )}
      {my.books.length === 0 && bookValue === 'MY' && (
        <span style={{ fontSize: '11px', color: 'var(--fog)' }}>Choose your books on the <Link href="/ev" style={{ color: 'var(--link)' }}>Odds Terminal</Link> first.</span>
      )}
    </AlignedBar>
  )
}
