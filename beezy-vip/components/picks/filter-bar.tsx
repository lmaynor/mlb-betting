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

            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <label style={LABEL}>SORT
                <select aria-label="Sort" value={sort} onChange={e => push(p => { if (e.target.value === 'score') p.delete('sort'); else p.set('sort', e.target.value) })} style={{ ...CONTROL, ...SELECT }}>
                  {sortOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <button type="button" aria-label={dir === 'desc' ? 'Sorted high to low; switch to low to high' : 'Sorted low to high; switch to high to low'}
                onClick={() => push(p => { if (dir === 'desc') p.set('dir', 'asc'); else p.delete('dir') })} style={{ ...CONTROL, cursor: 'pointer', marginTop: '4px' }}>
                {dir === 'desc' ? 'Order: High to Low' : 'Order: Low to High'}
              </button>
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
