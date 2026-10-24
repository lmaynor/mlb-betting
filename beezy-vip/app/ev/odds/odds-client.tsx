'use client'

import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { apiGetOddsBoard } from '@/lib/betting-api'
import { bookDisplay, canonBook, SURFACE_CARD } from '@/lib/tokens'
import { fmtMoney, kellySizing } from '@/lib/ev-math'
import { formatOdds } from '@/lib/odds'
import { useBankroll } from '@/lib/use-bankroll'
import { useMyBooks } from '@/lib/use-my-books'
import { LIVE_SPORTS } from '@/lib/sports'
import type { ProjectionSport } from '@/lib/sports'
import { LockedNotice } from '@/components/picks/locked-notice'
import {
  buildScreenRows, STAT_LABEL, type BoardRow, type DevigMethod, type OddsBoard, type ScreenRow, type SideFilter,
} from '@/lib/odds-screen'

type SportFilter = 'all' | ProjectionSport
type SortKey = 'ev' | 'time' | 'player'

const HAIR = '#1e2230'
const SPORT_KEYS: ProjectionSport[] = ['nfl', 'nhl', 'nba']
// Sharp-first column order; any book not listed follows by how many lines it quotes.
const BOOK_ORDER = ['pinnacle', 'novig', 'prophetx', 'kalshi', 'draftkings', 'fanduel', 'betmgm', 'caesars', 'espnbet', 'thescore', 'fanatics', 'hardrock', 'betrivers', 'bet365']
const SHARP_PRESET = ['pinnacle', 'novig', 'prophetx']
const MIN_EV = [
  { label: 'All', v: -1 },
  { label: '0%+', v: 0 },
  { label: '1%+', v: 0.01 },
  { label: '2%+', v: 0.02 },
  { label: '3%+', v: 0.03 },
  { label: '5%+', v: 0.05 },
]
const PAGE = 150
const MIN_REF = 3          // reference books needed behind a fair value
// The banker rewrites the boards every 30 minutes, so a faster poll only re-downloads identical data.
const POLL_MS = 3 * 60_000

const CT_TIME = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', weekday: 'short', hour: 'numeric', minute: '2-digit' })

function startLabel(iso: string): string {
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '' : CT_TIME.format(d).replace(' AM', 'a').replace(' PM', 'p')
}

function ageLabel(mins: number | null): string {
  if (mins == null) return '--'
  return mins < 90 ? `${Math.max(1, Math.round(mins))}m` : mins < 2880 ? `${(mins / 60).toFixed(1)}h` : `${Math.round(mins / 1440)}d`
}

export function OddsClient({ initial, initialLocked }: { initial: Partial<Record<ProjectionSport, OddsBoard>>; initialLocked: number }) {
  const [boards, setBoards] = useState(initial)
  const [locked, setLocked] = useState(initialLocked)
  const [loading, setLoading] = useState(false)
  const [sport, setSport] = useState<SportFilter>('all')
  const [stat, setStat] = useState('all')
  const [game, setGame] = useState('all')
  const [side, setSide] = useState<SideFilter>('both')
  const [minEv, setMinEv] = useState(0)
  const [q, setQ] = useState('')
  const [method, setMethod] = useState<DevigMethod>('proportional')
  const [refs, setRefs] = useState<string[]>([])                 // empty = every book
  const [shown, setShown] = useState<string[] | null>(null)      // null = the default set (see defaultShown)
  const [sort, setSort] = useState<SortKey>('ev')
  const [limit, setLimit] = useState(PAGE)
  const [bankroll] = useBankroll(1000)
  const myBooks = useMyBooks()

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const next = await Promise.all(SPORT_KEYS.map(s => apiGetOddsBoard(s, { limit: 3000 }).catch(() => null)))
      const out: Partial<Record<ProjectionSport, OddsBoard>> = {}
      let lk = 0
      next.forEach((b, i) => { if (b) { out[SPORT_KEYS[i]] = b; lk += b.locked ?? 0 } })
      if (Object.keys(out).length) {
        // identical data (same as_of for every sport): keep the old objects so nothing is recomputed or re-rendered
        setBoards(prev => SPORT_KEYS.every(k => prev[k]?.as_of === out[k]?.as_of) ? prev : out)
        setLocked(lk)
      }
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    const id = setInterval(() => { if (document.visibilityState === 'visible') refresh() }, POLL_MS)
    return () => clearInterval(id)
  }, [refresh])

  const active = useMemo(() => SPORT_KEYS.filter(s => (sport === 'all' || sport === s) && boards[s]), [boards, sport])
  const allRows = useMemo<BoardRow[]>(() => active.flatMap(s => boards[s]!.rows), [active, boards])
  const offshore = useMemo(() => new Set(active.flatMap(s => boards[s]!.offshore_books)), [active, boards])

  // Every book with a price in the current sport selection, sharp books first, then by how many lines it quotes.
  const bookList = useMemo(() => {
    const counts = new Map<string, number>()
    // PartyCasino (BetMGM) and SugarHouse (BetRivers) are skins with identical prices: one column each, not two.
    for (const r of allRows) for (const b of r.books) if (canonBook(b.book) === b.book) counts.set(b.book, (counts.get(b.book) ?? 0) + 1)
    const rank = (k: string) => { const i = BOOK_ORDER.indexOf(k); return i < 0 ? BOOK_ORDER.length : i }
    return [...counts.keys()].sort((a, b) => rank(a) - rank(b) || (counts.get(b)! - counts.get(a)!) || a.localeCompare(b))
      .map(k => ({ key: k, n: counts.get(k)!, offshore: offshore.has(k) }))
  }, [allRows, offshore])

  // Default columns: books quoting at least 10% of the lines (a book with a handful of lines is noise as a column).
  const defaultShown = useMemo(() => bookList.filter(b => b.n >= Math.max(1, allRows.length * 0.1)).map(b => b.key), [bookList, allRows.length])
  const shownSet = useMemo(() => new Set(shown ?? defaultShown), [shown, defaultShown])
  const columns = useMemo(() => bookList.filter(b => shownSet.has(b.key) || refs.includes(b.key)), [bookList, shownSet, refs])
  const bettable = useMemo(() => new Set(bookList.filter(b => shownSet.has(b.key) && !b.offshore).map(b => b.key)), [bookList, shownSet])

  const statOptions = useMemo(() => [...new Set(allRows.map(r => r.stat))].sort((a, b) => (STAT_LABEL[a] ?? a).localeCompare(STAT_LABEL[b] ?? b)), [allRows])
  const games = useMemo(() => {
    const seen = new Map<string, { key: string; label: string; start: string }>()
    for (const s of active) for (const g of boards[s]!.games) seen.set(g.game_key, { key: g.game_key, label: `${g.away} @ ${g.home}`, start: g.start })
    return [...seen.values()].sort((a, b) => a.start.localeCompare(b.start))
  }, [active, boards])

  const deferredQ = useDeferredValue(q)      // typing stays responsive; the table catches up
  const filtered = useMemo(() => {
    const needle = deferredQ.trim().toLowerCase()
    return allRows.filter(r => (stat === 'all' || r.stat === stat) && (game === 'all' || r.game_key === game)
      && (!needle || r.player.toLowerCase().includes(needle) || r.away.toLowerCase().includes(needle) || r.home.toLowerCase().includes(needle)))
  }, [allRows, stat, game, deferredQ])

  const screen = useMemo(() => {
    // a row with no fair value has no EV: it only shows under MIN EV = All (price comparison), never as a +EV row
    const rows = buildScreenRows(filtered, { refs, method, bettable, minRef: MIN_REF, side }).filter(r => (r.ev == null ? minEv < 0 : r.ev >= minEv))
    const by: Record<SortKey, (a: ScreenRow, b: ScreenRow) => number> = {
      ev: (a, b) => (b.ev ?? -Infinity) - (a.ev ?? -Infinity),
      time: (a, b) => a.row.commence.localeCompare(b.row.commence) || (b.ev ?? -Infinity) - (a.ev ?? -Infinity),
      player: (a, b) => a.row.player.localeCompare(b.row.player) || (b.ev ?? -Infinity) - (a.ev ?? -Infinity),
    }
    return rows.sort(by[sort])
  }, [filtered, refs, method, bettable, side, minEv, sort])

  const meta = useMemo(() => {
    const ages = active.map(s => boards[s]!.age_minutes).filter((n): n is number => n != null)
    const sources = [...new Set(active.flatMap(s => boards[s]!.lines_source))]
    return { age: ages.length ? Math.max(...ages) : null, stale: active.some(s => boards[s]!.stale), sources }
  }, [active, boards])

  // no board at all, or every board the API answered with is flagged unavailable (the banker has not written it yet)
  const unpublished = active.length === 0 || active.every(s => boards[s]!.status === 'unavailable')

  const refWeight = refs.length ? Math.round(100 / refs.length) : null
  const refSet = useMemo(() => new Set(refs), [refs])

  return (
    <div style={{ maxWidth: '1680px', margin: '0 auto', padding: '20px 24px 64px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px 20px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0, letterSpacing: '-0.01em' }}>Odds Screen</h1>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="mono" style={{ fontSize: '12px', color: meta.stale ? '#facc15' : 'var(--fog)' }}>
            {screen.length} ROWS &middot; LINES {ageLabel(meta.age).toUpperCase()} OLD{meta.sources.length ? ` \u00b7 ${meta.sources.join(' + ').toUpperCase()}` : ''}
          </span>
          <button onClick={refresh} disabled={loading} className="btn btn-ghost" style={{ padding: '5px 12px', fontSize: '12px' }}>{loading ? 'Refreshing...' : 'Refresh'}</button>
        </div>
      </div>

      <section style={{ ...SURFACE_CARD, padding: '10px 14px', marginBottom: '10px', display: 'flex', gap: '10px 18px', flexWrap: 'wrap', alignItems: 'center' }} aria-label="Filters">
        <Field label="SPORT">
          <PillGroup>
            {(['all', ...LIVE_SPORTS.map(p => p.slug).filter((s): s is ProjectionSport => s !== 'mlb')] as SportFilter[]).map(s => (
              <Pill key={s} active={sport === s} onClick={() => { setSport(s); setStat('all'); setGame('all'); setLimit(PAGE) }}>{s.toUpperCase()}</Pill>
            ))}
          </PillGroup>
        </Field>
        <Field label="PROP" htmlFor="odds-stat">
          <Select id="odds-stat" value={stat} onChange={v => { setStat(v); setLimit(PAGE) }}
            options={[{ v: 'all', l: 'All props' }, ...statOptions.map(s => ({ v: s, l: STAT_LABEL[s] ?? s }))]} />
        </Field>
        <Field label="GAME" htmlFor="odds-game">
          <Select id="odds-game" value={game} onChange={v => { setGame(v); setLimit(PAGE) }}
            options={[{ v: 'all', l: 'All games' }, ...games.map(g => ({ v: g.key, l: `${g.label} ${startLabel(g.start)}` }))]} />
        </Field>
        <Field label="O/U">
          <PillGroup>
            {(['both', 'over', 'under'] as SideFilter[]).map(s => <Pill key={s} active={side === s} onClick={() => setSide(s)}>{s === 'both' ? 'BOTH' : s === 'over' ? 'OVER' : 'UNDER'}</Pill>)}
          </PillGroup>
        </Field>
        <Field label="MIN EV">
          <PillGroup>
            {MIN_EV.map(m => <Pill key={m.v} active={minEv === m.v} onClick={() => setMinEv(m.v)}>{m.label}</Pill>)}
          </PillGroup>
        </Field>
        <Field label="DEVIG">
          <PillGroup>
            <Pill active={method === 'proportional'} onClick={() => setMethod('proportional')} title="Remove the vig in proportion to each side's implied probability">PROPORTIONAL</Pill>
            <Pill active={method === 'power'} onClick={() => setMethod('power')} title="Raise both implied probabilities to the power that sums them to 1 (shifts less to the favourite)">POWER</Pill>
          </PillGroup>
        </Field>
        <BookPicker
          label={refs.length ? `DEVIG BOOKS (${refs.length})` : 'DEVIG BOOKS (ALL)'}
          books={bookList} selected={refs} onChange={setRefs} emptyMeansAll
          presets={[{ label: 'Sharp', keys: SHARP_PRESET.filter(k => bookList.some(b => b.key === k)) }]}
          note="Fair value is the equal-weight average of these books' de-vigged lines. None selected uses every book. Needs 3 books behind a row."
        />
        <BookPicker
          label={`BOOKS (${columns.length})`}
          books={bookList} selected={[...shownSet]} onChange={next => setShown(next)}
          presets={[
            { label: 'Defaults', keys: defaultShown },
            ...(myBooks.enabled ? [{ label: 'My books', keys: myBooks.books }] : []),
            { label: 'All', keys: bookList.map(b => b.key) },
          ]}
          note="Columns shown and books you can bet at. Offshore books show their price but never count as the best price."
        />
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginLeft: 'auto' }}>
          <Field label="SORT" htmlFor="odds-sort">
            <Select id="odds-sort" value={sort} onChange={v => setSort(v as SortKey)} options={[{ v: 'ev', l: 'EV %' }, { v: 'time', l: 'Start time' }, { v: 'player', l: 'Player' }]} />
          </Field>
          <input
            aria-label="Search player or team" placeholder="Search player / team" value={q} onChange={e => { setQ(e.target.value); setLimit(PAGE) }}
            style={{ background: '#090a0f', color: 'var(--chalk)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 10px', fontSize: '12px', width: '190px' }}
          />
        </div>
      </section>

      {unpublished ? (
        <Empty title="Odds board not published yet" body="The price feed behind this screen is not available right now. It is rewritten every 30 minutes while games are on the board; check back shortly." />
      ) : allRows.length === 0 ? (
        <Empty title="No prop prices posted" body="No book has posted NFL, NHL or NBA player props for the games on the board yet." />
      ) : screen.length === 0 ? (
        <Empty title="Nothing clears these filters" body="Set MIN EV to All to see every line, widen the devig books, or clear the prop and game filters. A +EV row needs at least three devig books pricing the line both ways." />
      ) : (
        <div className="odds-table-wrap" style={{ ...SURFACE_CARD, padding: 0 }}>
          <table className="odds-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th scope="col" className="odds-ev">EV</th>
                <th scope="col" style={{ textAlign: 'left' }}>BEST</th>
                <th scope="col" style={{ textAlign: 'left', minWidth: '210px' }}>PROP</th>
                <th scope="col" style={{ textAlign: 'left' }}>GAME</th>
                <th scope="col" style={{ textAlign: 'right' }}>IMPL</th>
                <th scope="col" style={{ textAlign: 'right' }} title="Quarter Kelly at your bankroll, edge shrunk the way the +EV Terminal sizes">&frac14; KELLY</th>
                {columns.map(c => {
                  const b = bookDisplay(c.key)
                  const isRef = refSet.has(c.key)
                  return (
                    <th key={c.key} scope="col" className="odds-book" title={`${b.label}${c.offshore ? ' (offshore: reference only)' : ''}`}>
                      <BookLogo book={c.key} size={22} />
                      <span className="mono odds-sub">{isRef && refWeight != null ? `${refWeight}%` : c.offshore ? 'ref' : '\u00a0'}</span>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {screen.slice(0, limit).map(r => <Row key={r.key} r={r} columns={columns} bankroll={bankroll} />)}
            </tbody>
          </table>
        </div>
      )}
      {screen.length > limit && (
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: '10px' }}>
          <button className="btn btn-ghost" style={{ padding: '6px 14px', fontSize: '12px' }} onClick={() => setLimit(l => l + PAGE)}>Show {Math.min(PAGE, screen.length - limit)} more of {screen.length - limit}</button>
        </div>
      )}
      <LockedNotice locked={locked} shown={allRows.length} what="prop lines" plan="The odds screen is part of All-Access Pro." />

      <p className="times" style={{ color: 'var(--fog)', fontSize: '12px', marginTop: '24px', maxWidth: '90ch', lineHeight: 1.6 }}>
        Each cell is a book&apos;s over price above its under price; green marks the best bettable price on the row&apos;s side. FV is the
        fair price from the devig books you pick, averaged with equal weight after each book&apos;s vig is removed; a book is never
        measured against its own line. EV is fair probability times payout minus one at the best price. These are unvalidated market
        signals, not part of the public track record. Lines move or limit quickly: check the price at the book. Not financial advice.
        21+. If gambling is a problem, call 1-800-GAMBLER.{' '}
        <Link href="/legal/responsible-gambling" style={{ color: 'var(--link)' }}>Responsible gambling</Link>
      </p>
    </div>
  )
}

/** Book logo from /logos/books/<key>.png; falls back to the colored short-code chip when no PNG is shipped for that book. */
function BookLogo({ book, size, style }: { book: string; size: number; style?: React.CSSProperties }) {
  const [missing, setMissing] = useState(false)
  const meta = bookDisplay(book)
  if (missing) {
    return <span style={{ fontSize: '10.5px', fontWeight: 700, color: meta.color, background: meta.bg, border: `1px solid ${meta.color}55`, borderRadius: '6px', padding: '1px 6px', ...style }}>{meta.short}</span>
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`/logos/books/${canonBook(book)}.png`} alt={meta.label} title={meta.label}
      width={size} height={size} loading="lazy" onError={() => setMissing(true)}
      style={{ width: size, height: size, objectFit: 'contain', borderRadius: '4px', verticalAlign: 'middle', ...style }}
    />
  )
}

function Row({ r, columns, bankroll }: { r: ScreenRow; columns: { key: string; offshore: boolean }[]; bankroll: number }) {
  const dec = r.implied > 0 ? 1 / r.implied : 1
  const size = r.pFair == null ? null : kellySizing(bankroll, r.pFair, dec)
  const byBook = new Map(r.row.books.map(b => [b.book, b]))
  const evColor = r.ev != null && r.ev > 0 ? 'var(--signal)' : 'var(--fog)'
  return (
    <tr data-row="odds">
      <td className="odds-ev">
        {r.ev == null || r.fairAmerican == null ? (
          <>
            <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: 'var(--fog)' }}>--</div>
            <div className="mono odds-sub" title={`Only ${r.nRef} devig book${r.nRef === 1 ? '' : 's'} priced this line both ways; ${MIN_REF} are needed for a fair value`}>{r.nRef} of {MIN_REF} bk</div>
          </>
        ) : (
          <>
            <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: evColor }}>{r.ev >= 0 ? '+' : ''}{(r.ev * 100).toFixed(1)}%</div>
            <div className="mono odds-sub" title={`${r.nRef} devig books`}>FV {r.fairAmerican > 0 ? `+${r.fairAmerican}` : r.fairAmerican}</div>
          </>
        )}
      </td>
      <td>
        <BookLogo book={r.bestBook} size={18} style={{ marginRight: '6px' }} />
        <span className="mono" style={{ fontSize: '13px', fontWeight: 700, color: 'var(--chalk)' }}>{formatOdds(r.bestAmerican)}</span>
      </td>
      <td>
        <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--chalk)', lineHeight: 1.3 }}>{r.row.player}</div>
        <div className="mono odds-sub" style={{ fontSize: '11px' }}>{r.label} {STAT_LABEL[r.row.stat] ?? r.row.stat}</div>
      </td>
      <td>
        <div className="mono" style={{ fontSize: '11.5px', color: 'var(--silver)' }}>{r.row.away} @ {r.row.home}</div>
        <div className="mono odds-sub">{startLabel(r.row.commence)}</div>
      </td>
      <td style={{ textAlign: 'right' }}><span className="mono" style={{ fontSize: '12px', color: 'var(--silver)' }}>{(r.implied * 100).toFixed(1)}%</span></td>
      <td style={{ textAlign: 'right' }}>
        <span className="mono" style={{ fontSize: '12px', color: size && size.dollars > 0 ? 'var(--chalk)' : 'var(--fog)' }}>{size && size.dollars > 0 ? fmtMoney(size.dollars) : '--'}</span>
      </td>
      {columns.map(c => {
        const q = byBook.get(c.key)
        const target = q ? (r.side === 'OVER' ? q.over : q.under) : null
        const other = q ? (r.side === 'OVER' ? q.under : q.over) : null
        const isBest = !c.offshore && r.bestTies.includes(c.key)
        return (
          <td key={c.key} className="odds-cell" data-best={isBest ? 'true' : undefined} style={{ borderLeft: `1px solid ${HAIR}` }}>
            {target == null && other == null ? <span className="mono odds-sub">&ndash;</span> : (
              <>
                <div className="mono" style={{ fontSize: '12.5px', fontWeight: isBest ? 800 : 600, color: isBest ? 'var(--signal)' : 'var(--chalk)' }}>{target == null ? '\u2013' : formatOdds(target)}</div>
                <div className="mono odds-sub">{other == null ? '\u2013' : formatOdds(other)}</div>
              </>
            )}
          </td>
        )
      })}
    </tr>
  )
}

function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
      <label htmlFor={htmlFor} className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>{label}</label>
      {children}
    </div>
  )
}

function Select({ id, value, onChange, options }: { id: string; value: string; onChange: (v: string) => void; options: { v: string; l: string }[] }) {
  return (
    <select id={id} value={value} onChange={e => onChange(e.target.value)}
      style={{ background: '#090a0f', color: 'var(--chalk)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 8px', fontSize: '12px', maxWidth: '200px' }}>
      {options.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
    </select>
  )
}

function Pill({ children, active, onClick, title }: { children: React.ReactNode; active: boolean; onClick: () => void; title?: string }) {
  return (
    <button onClick={onClick} title={title} aria-pressed={active}
      style={{
        fontFamily: 'var(--font-text), sans-serif', fontSize: '11.5px', fontWeight: 600, letterSpacing: '0.03em', padding: '6px 11px', borderRadius: '6px', cursor: 'pointer',
        color: active ? '#ffffff' : 'var(--silver)', background: active ? '#1b1f2b' : 'transparent', border: `1px solid ${active ? '#3a4156' : 'transparent'}`,
      }}>
      {children}
    </button>
  )
}

function PillGroup({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'inline-flex', flexWrap: 'wrap', gap: '2px', padding: '3px', background: '#090a0f', border: `1px solid ${HAIR}`, borderRadius: '8px', maxWidth: '100%' }}>{children}</div>
}

/** A button that opens a checklist of books (a popover closed by outside click or Escape). `emptyMeansAll`: none ticked = every book. */
function BookPicker({ label, books, selected, onChange, presets, note, emptyMeansAll }: {
  label: string
  books: { key: string; n: number; offshore: boolean }[]
  selected: string[]
  onChange: (next: string[]) => void
  presets: { label: string; keys: string[] }[]
  note: string
  emptyMeansAll?: boolean
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])
  const on = new Set(selected)
  const toggle = (k: string) => onChange(on.has(k) ? selected.filter(x => x !== k) : [...selected, k])
  return (
    <div ref={box} style={{ position: 'relative' }}>
      <button onClick={() => setOpen(v => !v)} aria-expanded={open} className="btn btn-ghost" style={{ padding: '6px 11px', fontSize: '11.5px', height: '34px' }}>{label}</button>
      {open && (
        <div role="group" aria-label={label} style={{ position: 'absolute', zIndex: 30, top: '38px', left: 0, width: '280px', ...SURFACE_CARD, padding: '10px', display: 'grid', gap: '8px' }}>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {presets.map(p => <button key={p.label} className="btn btn-ghost" style={{ padding: '3px 9px', fontSize: '11px' }} onClick={() => onChange(p.keys)}>{p.label}</button>)}
            {emptyMeansAll && <button className="btn btn-ghost" style={{ padding: '3px 9px', fontSize: '11px' }} onClick={() => onChange([])}>All books</button>}
          </div>
          <div style={{ display: 'grid', gap: '2px', maxHeight: '280px', overflowY: 'auto' }}>
            {books.map(b => {
              const meta = bookDisplay(b.key)
              return (
                <label key={b.key} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '4px 6px', fontSize: '12px', color: 'var(--silver)', cursor: 'pointer' }}>
                  <input type="checkbox" checked={on.has(b.key)} onChange={() => toggle(b.key)} />
                  <span style={{ minWidth: '34px', display: 'inline-flex' }}><BookLogo book={b.key} size={18} /></span>
                  <span style={{ flex: 1 }}>{meta.label}{b.offshore ? ' (offshore)' : ''}</span>
                  <span className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)' }}>{b.n}</span>
                </label>
              )
            })}
          </div>
          <span style={{ fontSize: '11px', color: 'var(--fog)', lineHeight: 1.45 }}>{note}</span>
        </div>
      )}
    </div>
  )
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div style={{ ...SURFACE_CARD, padding: '36px 20px', textAlign: 'center' }}>
      <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>{title}</div>
      <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '60ch', margin: '0 auto', lineHeight: 1.55 }}>{body}</div>
    </div>
  )
}
