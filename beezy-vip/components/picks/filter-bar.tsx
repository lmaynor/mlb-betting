'use client'

import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { BOOK_CHIPS, bookParam, SORT_OPTIONS, TIER_OPTIONS } from '@/lib/picks-filters'
import { getSystemsBySport, kindOfSystem, type PickKind } from '@/lib/pick-systems'
import { LIVE_SPORTS, SPORT_PILLARS, type SportSlug } from '@/lib/sports'
import { SYSTEM_COLOR, SYSTEM_LABEL_SHORT } from '@/lib/tokens'
import { useMyBooks } from '@/lib/use-my-books'
import { addDaysToDateKey, siteDateKey } from '@/lib/dates'

const B = '1px solid var(--basalt)'

const CHIP_BASE: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '6px 11px', borderRadius: 'var(--radius-pill)',
  border: B, background: 'var(--graphite)', color: 'var(--silver)', fontSize: '11px', fontFamily: 'var(--font-mono)',
  letterSpacing: '0.04em', cursor: 'pointer', whiteSpace: 'nowrap' as const, userSelect: 'none' as const,
  WebkitTapHighlightColor: 'transparent', transition: 'color var(--dur), border-color var(--dur), background var(--dur)',
}
const CHIP_ACTIVE: React.CSSProperties = { ...CHIP_BASE, color: 'var(--chalk)', borderColor: 'var(--steel)', background: 'var(--slate)' }

const DATE_LABELS: Record<string, string> = { today: 'Today', yesterday: 'Yesterday' }
const RESULT_LABELS: Record<string, string> = { ALL: 'All', WIN: 'Win', LOSS: 'Loss', PENDING: 'Pending' }
const RESULTS = ['ALL', 'WIN', 'LOSS', 'PENDING']

/**
 * The one filter bar every picks page uses (/picks and /picks/[sport], any sport). State lives in the query string, so the
 * server page reads the same params (lib/picks-filters.ts) and the URL is shareable.
 *
 *   sport = 'all'   cross-sport Top 10: SPORT chips, TIER, BOOK, search, sort
 *   sport = <slug>  MARKET chips from that sport's registered systems, TIER, BOOK, search, sort;
 *                   plus DATE and RESULT when the sport's board is the tracked (settled) card
 * `kind` narrows MLB's market chips to player or game systems (the two MLB cards).
 */
export function FilterBar({ sport = 'mlb', kind, defaultOpen = false }: { sport?: SportSlug | 'all'; kind?: PickKind; defaultOpen?: boolean } = {}) {
  const router = useRouter()
  const pathname = usePathname()
  const sp = useSearchParams()
  const [open, setOpen] = useState(defaultOpen)
  const my = useMyBooks()
  const pillar = sport === 'all' ? null : SPORT_PILLARS[sport]
  const tracked = pillar?.board === 'tracked'

  const market = sp.get('market') ?? 'ALL'
  const date = sp.get('date') ?? 'today'
  const status = sp.get('status') ?? 'ALL'
  const book = sp.get('book') ?? 'ALL'
  const books = sp.get('books') ?? ''
  const tier = (sp.get('tier') ?? 'ALL').toUpperCase()
  const sportParam = sp.get('sport') ?? 'ALL'
  const sort = sp.get('sort') ?? 'score'
  const dir = sp.get('dir') ?? 'desc'
  const qParam = sp.get('q') ?? ''
  const [q, setQ] = useState(qParam)
  useEffect(() => { setQ(qParam) }, [qParam])

  const push = useCallback((mutate: (p: URLSearchParams) => void) => {
    const params = new URLSearchParams(sp.toString())
    mutate(params)
    const qs = params.toString()
    router.push(qs ? `${pathname}?${qs}` : pathname)
  }, [router, sp, pathname])

  const set = useCallback((key: string, value: string, defaultVal: string) => {
    push(p => { if (value === defaultVal) p.delete(key); else p.set(key, value) })
  }, [push])

  const toggleSort = useCallback((value: string) => {
    push(p => {
      if (sort === value) p.set('dir', dir === 'desc' ? 'asc' : 'desc')
      else { p.set('sort', value); p.delete('dir') }
    })
  }, [push, sort, dir])

  const markets = sport === 'all' ? [] : getSystemsBySport(sport).filter(s => sport !== 'mlb' || !kind || kindOfSystem(s.key) === kind).map(s => s.key)
  const MARKETS = ['ALL', ...(sport === 'mlb' ? ['EV'] : []), ...markets]
  const sortOptions = sport === 'all' ? SORT_OPTIONS.filter(o => o.value === 'score' || o.value === 'edge' || o.value === 'odds') : SORT_OPTIONS
  const yesterdayKey = addDaysToDateKey(siteDateKey(), -1)
  const dateChoice = date === 'today' ? 'today' : date === yesterdayKey ? 'yesterday' : date
  const mineKey = my.books.join(',')
  const mineActive = books !== '' && books === mineKey

  const activeCount = [
    market !== 'ALL', sportParam !== 'ALL', tracked && date !== 'today', status !== 'ALL', book !== 'ALL', books !== '', tier !== 'ALL', qParam !== '',
  ].filter(Boolean).length

  function chip(label: string, active: boolean, onClick: () => void, dot?: string) {
    return (
      <button key={label} onClick={onClick} aria-pressed={active} style={active ? CHIP_ACTIVE : CHIP_BASE}>
        {dot && <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: dot, flexShrink: 0 }} />}
        {label}
      </button>
    )
  }
  function row(label: string, children: React.ReactNode) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', padding: '8px 16px', borderBottom: B }}>
        <span style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--fog)', letterSpacing: '0.08em', minWidth: '44px' }}>{label}</span>
        {children}
      </div>
    )
  }
  const marketChip = (k: string) => chip(k === 'ALL' || k === 'EV' ? k : (SYSTEM_LABEL_SHORT[k] ?? k), market === k, () => set('market', k, 'ALL'), k === 'ALL' ? 'var(--fog)' : (SYSTEM_COLOR[k] ?? 'var(--fog)'))

  return (
    <div style={{ borderBottom: B, background: 'var(--carbon)' }}>
      {/* one toolbar row: filters toggle + active summary, search, sort */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', padding: '8px 16px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <button onClick={() => setOpen(o => !o)} aria-expanded={open}
            style={{ ...CHIP_BASE, color: open ? 'var(--chalk)' : 'var(--silver)', borderColor: open ? 'var(--steel)' : 'var(--basalt)', background: open ? 'var(--slate)' : 'var(--graphite)', gap: '8px' }}>
            FILTERS
            {activeCount > 0 && (
              <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '17px', height: '17px', borderRadius: 'var(--radius-pill)', background: 'var(--signal)', color: 'var(--carbon)', fontSize: '9px', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>{activeCount}</span>
            )}
            <span style={{ fontSize: '9px', color: 'var(--fog)' }}>{open ? '^' : 'v'}</span>
          </button>
          {!open && activeCount > 0 && (
            <>
              {market !== 'ALL' && <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{SYSTEM_LABEL_SHORT[market] ?? market}</span>}
              {sportParam !== 'ALL' && <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{sportParam.toUpperCase()}</span>}
              {tier !== 'ALL' && <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{tier}</span>}
              {tracked && date !== 'today' && <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{DATE_LABELS[dateChoice] ?? date}</span>}
              {status !== 'ALL' && <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{RESULT_LABELS[status] ?? status}</span>}
              {book !== 'ALL' && <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>{book}</span>}
              {books !== '' && <span style={{ ...CHIP_ACTIVE, fontSize: '10px', padding: '3px 8px' }}>MY BOOKS</span>}
            </>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <form onSubmit={e => { e.preventDefault(); set('q', q.trim(), '') }} role="search" style={{ display: 'inline-flex' }}>
            <input type="search" aria-label="Search player, team or city" placeholder="Player, team or city" value={q} onChange={e => setQ(e.target.value)}
              onBlur={() => { if (q.trim() !== qParam) set('q', q.trim(), '') }}
              style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '5px 9px', borderRadius: 'var(--radius-pill)', border: B, background: 'var(--graphite)', color: 'var(--chalk)', width: '150px' }} />
          </form>
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--fog)', letterSpacing: '0.08em', marginRight: '4px' }}>SORT</span>
            {sortOptions.map(({ value, label }) => {
              const active = sort === value
              return (
                <button key={value} onClick={() => toggleSort(value)} aria-pressed={active} style={{ ...(active ? CHIP_ACTIVE : CHIP_BASE), padding: '4px 8px', gap: '4px' }}>
                  {label}
                  {active && <span style={{ fontSize: '9px', opacity: 0.7 }}>{dir === 'desc' ? 'v' : '^'}</span>}
                </button>
              )
            })}
          </div>
        </div>
      </div>

      {open && (
        <div style={{ borderTop: B }}>
          {sport === 'all'
            ? row('SPORT', [chip('ALL', sportParam === 'ALL', () => set('sport', 'ALL', 'ALL'), 'var(--fog)'),
                ...LIVE_SPORTS.map(p => chip(p.shortName, sportParam.toLowerCase() === p.slug, () => set('sport', p.slug, 'ALL'), p.accent))])
            : row('MARKET', MARKETS.map(marketChip))}
          {tracked && row('DATE', Object.keys(DATE_LABELS).map(d => chip(DATE_LABELS[d], dateChoice === d, () => set('date', d === 'yesterday' ? yesterdayKey : 'today', 'today'))))}
          {tracked && row('RESULT', RESULTS.map(r => chip(RESULT_LABELS[r], status === r, () => set('status', r, 'ALL'))))}
          {row('TIER', [chip('ALL', tier === 'ALL', () => set('tier', 'ALL', 'ALL')), ...TIER_OPTIONS.map(t => chip(t.label, tier === t.value, () => set('tier', t.value, 'ALL')))])}
          {row('BOOK', [
            chip('ALL BOOKS', book === 'ALL' && books === '', () => push(p => { p.delete('book'); p.delete('books') })),
            chip('MY BOOKS', mineActive, () => push(p => { p.delete('book'); if (mineKey) p.set('books', mineKey); else p.delete('books') })),
            ...BOOK_CHIPS.map(b => chip(b, book.toLowerCase() === bookParam(b), () => push(p => { p.delete('books'); if (book.toLowerCase() === bookParam(b)) p.delete('book'); else p.set('book', b) }))),
          ])}
          {my.books.length === 0 && (
            <div style={{ padding: '6px 16px 8px 76px', fontSize: '11px', color: 'var(--fog)', borderBottom: B }}>
              MY BOOKS is empty: choose the books you can bet at on the <Link href="/ev" style={{ color: 'var(--link)' }}>+EV terminal</Link>.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
