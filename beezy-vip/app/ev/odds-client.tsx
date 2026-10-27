'use client'

import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { apiGetOddsBoard } from '@/lib/betting-api'
import { bookDisplay, canonBook, SURFACE_CARD } from '@/lib/tokens'
import { americanToDecimal, fmtMoney, kellySizing } from '@/lib/ev-math'
import { formatOdds } from '@/lib/odds'
import { useBankroll } from '@/lib/use-bankroll'
import { useMyBooks } from '@/lib/use-my-books'
import { LIVE_SPORTS } from '@/lib/sports'
import type { SportSlug } from '@/lib/sports'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import type { HeadshotSport } from '@/lib/sport-assets'
import { LedgerStrip } from './ledger-strip'
import type { EvHeadline } from '@/lib/types'
import { BookLogo } from '@/components/ui/book-logo'
import { LockedNotice } from '@/components/picks/locked-notice'
import { PRE_LAUNCH } from '@/lib/launch'
import {
  buildArbRows, buildLongshotRows, buildScreenRows, computeMiddles, fairAmericanOf, STAT_LABEL,
  type ArbRow, type BoardBook, type BoardRow, type DevigMethod, type LongshotRow, type LongshotSubfilter, type MiddleResult, type OddsBoard,
  type OddsScreenMode, type ScreenRow, type SideFilter,
} from '@/lib/odds-screen'

type SportFilter = 'all' | SportSlug
type SortKey = 'ev' | 'time' | 'player'
type LinesFilter = 'main' | 'all'
type ArbView = 'live' | 'near' | 'middle'

const HAIR = '#1e2230'
const GREEN = '#57F287'
const SKY = '#38bdf8'
const VIOLET = '#a78bfa'
const SPORT_KEYS: SportSlug[] = ['nfl', 'nhl', 'nba', 'mlb']
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
const ODDS_MIN = [
  { label: 'All Alts', v: 0 },
  { label: '+200+', v: 200 },
  { label: '+350+', v: 350 },
  { label: '+500+', v: 500 },
]
const PAGE = 150
const MIN_REF = 3          // reference books needed behind a fair value on the Standard view
const LONGSHOT_MIN_REF = 2 // ... on the Alt & Longshots view, where a line is rarely quoted by many books
const CELL_EDGE = 0.015    // a book cell is tinted when its price beats the row's fair value by this much EV
const NEAR_ARB_PCT = -1.5
const MID_COST = [2, 5, 8]                       // the most a missed middle may cost, percent of the outlay
const MID_GAP = [{ label: 'ANY', v: 0 }, { label: '2+', v: 2 }, { label: '3+', v: 3 }, { label: '5+', v: 5 }]
const MIDDLE_MIN_REF = 2
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

function signed(x: number, digits = 1): string {
  return `${x >= 0 ? '+' : ''}${x.toFixed(digits)}`
}

function usd(x: number): string {
  return `$${x.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** Clipboard write with the textarea fallback for pages without clipboard permission; false when both fail. */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch { return false }
  }
}

function fairText(p: number | null): string {
  if (p == null || !(p > 0 && p < 1)) return '--'
  return formatOdds(fairAmericanOf(p))
}

const MODES: { key: OddsScreenMode; label: string }[] = [
  { key: 'ev', label: '+EV Scanner' },
  { key: 'arb', label: 'Arb & Middles' },
  { key: 'longshots', label: 'Alt Lines & Tail (+200+)' },
]

export function OddsClient({ initial, initialLocked, headline }: { initial: Partial<Record<SportSlug, OddsBoard>>; initialLocked: number; headline: EvHeadline | null }) {
  const [boards, setBoards] = useState(initial)
  const [locked, setLocked] = useState(initialLocked)
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState<OddsScreenMode>('ev')
  const [sport, setSport] = useState<SportFilter>('all')
  const [stat, setStat] = useState('all')
  const [game, setGame] = useState('all')
  const [side, setSide] = useState<SideFilter>('both')
  const [lines, setLines] = useState<LinesFilter>('all')
  const [minEv, setMinEv] = useState(0)
  const [q, setQ] = useState('')
  const [method, setMethod] = useState<DevigMethod>('proportional')
  const [refs, setRefs] = useState<string[]>([])                 // empty = every book
  const [shown, setShown] = useState<string[] | null>(null)      // null = the default set (see defaultShown)
  const [sort, setSort] = useState<SortKey>('ev')
  const [limit, setLimit] = useState(PAGE)
  const [arbView, setArbView] = useState<ArbView>('live')
  const [exchanges, setExchanges] = useState(true)
  const [outlayText, setOutlayText] = useState('100')
  const [midCost, setMidCost] = useState(5)
  const [midGap, setMidGap] = useState(0)
  const [midPosEv, setMidPosEv] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const [longSub, setLongSub] = useState<LongshotSubfilter>('all')
  const [oddsMin, setOddsMin] = useState(0)
  const [useCurve, setUseCurve] = useState(true)
  const [bankroll] = useBankroll(1000)
  const myBooks = useMyBooks()

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const next = await Promise.all(SPORT_KEYS.map(s => apiGetOddsBoard(s, { limit: 3000 }).catch(() => null)))
      const out: Partial<Record<SportSlug, OddsBoard>> = {}
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

  // prop / game scope (search excluded): what the mode badges count
  const scoped = useMemo(() => allRows.filter(r => (stat === 'all' || r.stat === stat) && (game === 'all' || r.game_key === game)), [allRows, stat, game])
  const deferredQ = useDeferredValue(q)      // typing stays responsive; the table catches up
  const filtered = useMemo(() => {
    const needle = deferredQ.trim().toLowerCase()
    if (!needle) return scoped
    return scoped.filter(r => r.player.toLowerCase().includes(needle) || r.away.toLowerCase().includes(needle) || r.home.toLowerCase().includes(needle))
  }, [scoped, deferredQ])

  // ---- Standard +EV: one row per prop side
  const screen = useMemo(() => {
    if (mode !== 'ev') return []
    const src = lines === 'main' ? filtered.filter(r => !r.is_alt) : filtered
    // a row with no fair value has no EV: it only shows under MIN EV = All (price comparison), never as a +EV row
    const rows = buildScreenRows(src, { refs, method, bettable, minRef: MIN_REF, side }).filter(r => (r.ev == null ? minEv < 0 : r.ev >= minEv))
    const by: Record<SortKey, (a: ScreenRow, b: ScreenRow) => number> = {
      ev: (a, b) => (b.ev ?? -Infinity) - (a.ev ?? -Infinity),
      time: (a, b) => a.row.commence.localeCompare(b.row.commence) || (b.ev ?? -Infinity) - (a.ev ?? -Infinity),
      player: (a, b) => a.row.player.localeCompare(b.row.player) || (b.ev ?? -Infinity) - (a.ev ?? -Infinity),
    }
    return rows.sort(by[sort])
  }, [mode, filtered, lines, refs, method, bettable, side, minEv, sort])

  // ---- Arbitrage: one row per line, best margin first
  const arbs = useMemo(() => {
    if (mode !== 'arb' || arbView === 'middle') return []
    const all = buildArbRows(filtered, { bettable, includeExchanges: exchanges, minNetArbPct: NEAR_ARB_PCT })
    return (arbView === 'live' ? all.filter(r => r.arb.netArbPct > 0) : all).sort((a, b) => b.arb.netArbPct - a.arb.netArbPct)
  }, [mode, filtered, bettable, exchanges, arbView])

  // ---- Middles: the over of a lower line + the under of a higher line, same player and stat
  const middles = useMemo(() => {
    if (mode !== 'arb' || arbView !== 'middle') return []
    return computeMiddles(filtered, { bettable, includeExchanges: exchanges, maxCostPct: midCost, minGap: midGap, onlyPositiveEv: midPosEv, refs, method, minRef: MIDDLE_MIN_REF, useCurve })
  }, [mode, arbView, filtered, bettable, exchanges, midCost, midGap, midPosEv, refs, method, useCurve])

  // ---- Alt & Longshots: one row per line, best EV (either side) first
  const longshots = useMemo(() => {
    if (mode !== 'longshots') return []
    const rows = buildLongshotRows(filtered, { refs, method, bettable, minRef: LONGSHOT_MIN_REF, useCurve, sub: longSub, minEv: Math.max(minEv, 0), minOverAm: oddsMin })
    return rows.sort((a, b) => (b.bestEv ?? -Infinity) - (a.bestEv ?? -Infinity))
  }, [mode, filtered, refs, method, bettable, useCurve, longSub, minEv, oddsMin])

  // Mode badges: counted from deferred inputs so a click paints the table first and the other two counts follow.
  const badgeIn = useMemo(() => ({ scoped, refs, method, bettable, minEv, lines, side, exchanges, useCurve }),
    [scoped, refs, method, bettable, minEv, lines, side, exchanges, useCurve])
  const dBadgeIn = useDeferredValue(badgeIn)
  const counts = useMemo(() => {
    const d = dBadgeIn
    const floor = Math.max(d.minEv, 0)
    const src = d.lines === 'main' ? d.scoped.filter(r => !r.is_alt) : d.scoped
    return {
      ev: buildScreenRows(src, { refs: d.refs, method: d.method, bettable: d.bettable, minRef: MIN_REF, side: d.side }).filter(r => r.ev != null && r.ev >= floor).length,
      arb: buildArbRows(d.scoped, { bettable: d.bettable, includeExchanges: d.exchanges, minNetArbPct: 0 }).length,
      long: buildLongshotRows(d.scoped, { refs: d.refs, method: d.method, bettable: d.bettable, minRef: LONGSHOT_MIN_REF, useCurve: d.useCurve, sub: 'all', minEv: 0, minOverAm: 0 })
        .filter(r => r.bestEv != null && r.bestEv >= floor * 100).length,
    }
  }, [dBadgeIn])

  const nRows = mode === 'ev' ? screen.length : mode === 'arb' ? (arbView === 'middle' ? middles.length : arbs.length) : longshots.length

  const meta = useMemo(() => {
    const ages = active.map(s => boards[s]!.age_minutes).filter((n): n is number => n != null)
    const sources = [...new Set(active.flatMap(s => boards[s]!.lines_source))]
    return { age: ages.length ? Math.max(...ages) : null, stale: active.some(s => boards[s]!.stale), sources }
  }, [active, boards])

  // no board at all, or every board the API answered with is flagged unavailable (the banker has not written it yet)
  const unpublished = active.length === 0 || active.every(s => boards[s]!.status === 'unavailable')

  const refWeight = refs.length ? Math.round(100 / refs.length) : null
  const refSet = useMemo(() => new Set(refs), [refs])
  const outlay = useMemo(() => { const n = parseFloat(outlayText); return isFinite(n) && n > 0 ? n : 100 }, [outlayText])

  const copyHedge = useCallback(async (a: ArbRow) => {
    const k = outlay / 100
    const text = `${a.row.player} ${STAT_LABEL[a.row.stat] ?? a.row.stat} ${a.row.line}: OVER ${formatOdds(a.arb.bestOverAm)} ${bookDisplay(a.arb.bestOverBook).label} ${usd(a.arb.stakeOverPer100 * k)}`
      + ` / UNDER ${formatOdds(a.arb.bestUnderAm)} ${bookDisplay(a.arb.bestUnderBook).label} ${usd(a.arb.stakeUnderPer100 * k)}`
      + ` = ${usd(a.arb.guaranteedProfitPer100 * k)} locked on ${usd(outlay)}`
    if (await copyText(text)) {
      setCopied(a.key)
      setTimeout(() => setCopied(c => (c === a.key ? null : c)), 1500)
    }
  }, [outlay])

  const copyMiddle = useCallback(async (m: MiddleResult) => {
    const k = outlay / 100
    const text = `${m.low.player} ${STAT_LABEL[m.low.stat] ?? m.low.stat} middle ${m.low.line}-${m.high.line}: OVER ${m.low.line} ${formatOdds(m.overAm)} ${bookDisplay(m.overBook).label} ${usd(m.stakeOverPer100 * k)}`
      + ` / UNDER ${m.high.line} ${formatOdds(m.underAm)} ${bookDisplay(m.underBook).label} ${usd(m.stakeUnderPer100 * k)}`
      + ` = ${m.missPct < 0 ? '-' : '+'}${usd(Math.abs(m.missPct) / 100 * outlay)} if one leg wins, +${usd(m.hitPct / 100 * outlay)} if it lands inside`
    if (await copyText(text)) {
      setCopied(m.key)
      setTimeout(() => setCopied(c => (c === m.key ? null : c)), 1500)
    }
  }, [outlay])

  const emptyBody = mode === 'arb' && arbView === 'middle'
    ? { title: 'No middles at this cost', body: 'A middle needs the over of a lower line and the under of a higher line, priced close enough that a miss costs little. Raise the max cost, loosen the window filter, or include exchanges.' }
    : mode === 'arb'
    ? { title: arbView === 'live' ? 'No live arbitrage on the board' : 'No near arbitrage on the board',
        body: 'A live arb needs the best over and the best under at two different books to pay more than the outlay together. Try Near Arbs, include exchanges, or widen the books you can bet at.' }
    : mode === 'longshots'
      ? { title: 'No longshots clear these filters', body: 'Set the strategy to All, lower the odds range or MIN EV, or turn on curve-fitted fair value. Alt rungs are quoted by few books, so many have no fair value without the curve.' }
      : { title: 'Nothing clears these filters', body: 'Set MIN EV to All to see every line, widen the devig books, or clear the prop and game filters. A +EV row needs at least three devig books pricing the line both ways.' }
  const refsNote = mode === 'longshots' ? 'Two books behind a fair value (the curve stands in below that).' : 'Needs 3 books behind a row.'

  return (
    <div style={{ maxWidth: '1680px', margin: '0 auto', padding: '20px 24px 64px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px 20px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0, letterSpacing: '-0.01em' }}>Odds Terminal</h1>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="mono" style={{ fontSize: '12px', color: meta.stale ? '#facc15' : 'var(--fog)' }}>
            {nRows} ROWS &middot; LINES {ageLabel(meta.age).toUpperCase()} OLD{meta.sources.length ? ` \u00b7 ${meta.sources.join(' + ').toUpperCase()}` : ''}
          </span>
          <button onClick={refresh} disabled={loading} className="btn btn-ghost" style={{ padding: '5px 12px', fontSize: '12px' }}>{loading ? 'Refreshing...' : 'Refresh'}</button>
        </div>
      </div>

      {PRE_LAUNCH && (
        <div style={{ ...SURFACE_CARD, padding: '10px 16px', marginBottom: '10px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', borderColor: 'rgba(87,242,135,0.35)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.08em', color: 'var(--signal)', background: 'var(--brand-dim)', padding: '3px 8px', borderRadius: '4px' }}>PRE-LAUNCH PREVIEW</span>
            <span style={{ fontSize: '12.5px', color: 'var(--silver)' }}>The full Odds Terminal is unlocked for early visitors (included in All-Access Pro at launch).</span>
          </div>
          <Link href="/pricing" style={{ fontSize: '11.5px', color: 'var(--signal)', textDecoration: 'none', fontWeight: 600 }}>Lock pre-launch pricing &rarr;</Link>
        </div>
      )}

      <LedgerStrip h={headline} />

      <section style={{ ...SURFACE_CARD, padding: 0, marginBottom: '10px' }} aria-label="Filters">
        <div style={{ padding: '10px 14px', display: 'flex', gap: '10px 18px', flexWrap: 'wrap', alignItems: 'center' }}>
          <PillGroup label="Mode">
            {MODES.map(m => {
              const n = m.key === 'ev' ? counts.ev : m.key === 'arb' ? counts.arb : counts.long
              const hot = m.key === 'arb' && n > 0
              return (
                <Pill key={m.key} active={mode === m.key} onClick={() => { setMode(m.key); setLimit(PAGE) }}>
                  {m.label}
                  <span className="mono" style={{ marginLeft: '7px', fontSize: '10.5px', color: hot ? GREEN : 'var(--fog)' }}>{n}</span>
                </Pill>
              )
            })}
          </PillGroup>
          <Field label="SPORT">
            <PillGroup>
              {(['all', ...LIVE_SPORTS.map(p => p.slug)] as SportFilter[]).map(s => (
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
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginLeft: 'auto', flexWrap: 'wrap' }}>
            <input
              aria-label="Search player or team" placeholder="Search player / team" value={q} onChange={e => { setQ(e.target.value); setLimit(PAGE) }}
              style={{ background: '#090a0f', color: 'var(--chalk)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 10px', fontSize: '12px', width: '190px' }}
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
              align="right"
            />
            <BookPicker
              label={refs.length ? `REFERENCE BOOKS (${refs.length})` : 'REFERENCE BOOKS (ALL)'}
              books={bookList} selected={refs} onChange={setRefs} emptyMeansAll
              presets={[{ label: 'Sharp', keys: SHARP_PRESET.filter(k => bookList.some(b => b.key === k)) }]}
              note={`Fair value is the equal-weight average of these books' de-vigged lines. None selected uses every book. ${refsNote}`}
              align="right"
              header={
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>DEVIG</span>
                  <PillGroup>
                    <Pill active={method === 'proportional'} onClick={() => setMethod('proportional')} title="Remove the vig in proportion to each side's implied probability">PROPORTIONAL</Pill>
                    <Pill active={method === 'power'} onClick={() => setMethod('power')} title="Raise both implied probabilities to the power that sums them to 1 (shifts less to the favourite)">POWER</Pill>
                  </PillGroup>
                </div>
              }
            />
          </div>
        </div>

        <div style={{ padding: '8px 14px', borderTop: `1px solid ${HAIR}`, display: 'flex', gap: '10px 18px', flexWrap: 'wrap', alignItems: 'center' }}>
          {mode === 'ev' && (
            <>
              <Field label="LINES">
                <PillGroup>
                  <Pill active={lines === 'main'} onClick={() => setLines('main')} title="Standard over/under line quoted by most sportsbooks">Main Lines</Pill>
                  <Pill active={lines === 'all'} onClick={() => setLines('all')} title="Includes alternate milestone rungs and shifted lines">Main + Alt Ladders</Pill>
                </PillGroup>
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
              <Field label="SORT" htmlFor="odds-sort">
                <Select id="odds-sort" value={sort} onChange={v => setSort(v as SortKey)} options={[{ v: 'ev', l: 'Best EV %' }, { v: 'time', l: 'Game time' }, { v: 'player', l: 'Player' }]} />
              </Field>
            </>
          )}
          {mode === 'arb' && (
            <>
              <Field label="SHOW">
                <PillGroup>
                  <Pill active={arbView === 'live'} onClick={() => { setArbView('live'); setLimit(PAGE) }}>LIVE ARBS (&gt; 0%)</Pill>
                  <Pill active={arbView === 'near'} onClick={() => { setArbView('near'); setLimit(PAGE) }} title="Within 1.5% of break-even: bonus-bet conversion and near zero-hold line shopping">NEAR ARBS (&gt; -1.5%)</Pill>
                  <Pill active={arbView === 'middle'} onClick={() => { setArbView('middle'); setLimit(PAGE) }} title="Over a lower line plus under a higher line: both win if the result lands between them, a small known cost otherwise">MIDDLES</Pill>
                </PillGroup>
              </Field>
              {arbView === 'middle' && (
                <>
                  <Field label="MAX COST">
                    <PillGroup>
                      {MID_COST.map(c => <Pill key={c} active={midCost === c} onClick={() => { setMidCost(c); setLimit(PAGE) }} title="The most a miss may cost, as a share of the outlay">{`${c}%`}</Pill>)}
                    </PillGroup>
                  </Field>
                  <Field label="VALUE">
                    <PillGroup>
                      <Pill active={midPosEv} onClick={() => { setMidPosEv(v => !v); setLimit(PAGE) }} title="Only middles whose window chance, estimated from the reference books' fair price at each line, beats the break-even chance">+EV ONLY</Pill>
                    </PillGroup>
                  </Field>
                  <Field label="WINDOW">
                    <PillGroup>
                      {MID_GAP.map(g => <Pill key={g.v} active={midGap === g.v} onClick={() => { setMidGap(g.v); setLimit(PAGE) }} title="Narrowest gap between the two lines, in points">{g.label}</Pill>)}
                    </PillGroup>
                  </Field>
                </>
              )}
              <Field label="EXCHANGES">
                <PillGroup>
                  <Pill active={exchanges} onClick={() => setExchanges(v => !v)} title="Kalshi, Novig and ProphetX quotes. Kalshi's taker fee is taken off its leg; Novig charges none before the game.">INCLUDE KALSHI / NOVIG (FEE-ADJUSTED)</Pill>
                </PillGroup>
              </Field>
              <Field label="OUTLAY" htmlFor="odds-outlay">
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                  <span className="mono" style={{ color: 'var(--fog)', fontSize: '12px' }}>$</span>
                  <input
                    id="odds-outlay" inputMode="decimal" value={outlayText} onChange={e => setOutlayText(e.target.value.replace(/[^0-9.]/g, ''))}
                    style={{ background: '#090a0f', color: 'var(--chalk)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 10px', fontSize: '12px', width: '90px', fontFamily: 'var(--font-mono), monospace' }}
                  />
                </span>
              </Field>
            </>
          )}
          {mode === 'longshots' && (
            <>
              <Field label="STRATEGY">
                <PillGroup>
                  <Pill active={longSub === 'all'} onClick={() => { setLongSub('all'); setLimit(PAGE) }} title="Every alt line and every over paying +200 or more, with both angles priced">All Tail Lines</Pill>
                  <Pill active={longSub === 'plus_ev_over'} onClick={() => { setLongSub('plus_ev_over'); setLimit(PAGE) }} title="Longshot Over/Yes price pays more than its true fair probability">Buy Over (+EV Milestone)</Pill>
                  <Pill active={longSub === 'fade_under'} onClick={() => { setLongSub('fade_under'); setLimit(PAGE) }} title="House-side play: fade an overpriced retail milestone by buying Under or Exchange NO">Lay the Longshot (Buy Under / NO)</Pill>
                </PillGroup>
              </Field>
              <Field label="MIN PAYOUT">
                <PillGroup>
                  {ODDS_MIN.map(m => <Pill key={m.v} active={oddsMin === m.v} onClick={() => { setOddsMin(m.v); setLimit(PAGE) }}>{m.label}</Pill>)}
                </PillGroup>
              </Field>
              <Field label="MIN EV">
                <PillGroup>
                  {MIN_EV.map(m => <Pill key={m.v} active={minEv === m.v} onClick={() => setMinEv(m.v)}>{m.label}</Pill>)}
                </PillGroup>
              </Field>
              <Field label="FAIR SOURCE">
                <PillGroup>
                  <Pill active={useCurve} onClick={() => setUseCurve(true)} title="Uses exact multi-book de-vigged fair odds when available, or interpolates from the player's main-line curve when fewer than 2 books price an alt rung">Multi-Book + Player Curve</Pill>
                  <Pill active={!useCurve} onClick={() => setUseCurve(false)} title="Requires at least 2 reference books quoting this exact alternate line">Exact Books Only</Pill>
                </PillGroup>
              </Field>
            </>
          )}
        </div>
      </section>

      {unpublished ? (
        <Empty title="Odds board not published yet" body="The price feed behind this screen is not available right now. It is rewritten every 30 minutes while games are on the board; check back shortly." />
      ) : allRows.length === 0 ? (
        <Empty title="No prop prices posted" body="No book has posted NFL, NHL or NBA player props for the games on the board yet." />
      ) : nRows === 0 ? (
        <Empty title={emptyBody.title} body={emptyBody.body} />
      ) : (
        <div className="odds-table-wrap" style={{ ...SURFACE_CARD, padding: 0 }}>
          <table className="odds-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th scope="col" className="odds-prop" style={{ textAlign: 'left', minWidth: '210px' }}>PROP</th>
                <th scope="col" style={{ textAlign: 'left' }}>GAME</th>
                {mode === 'ev' && (
                  <>
                    <th scope="col" className="odds-ev" title="Fair price of this side from the reference books">FAIR ODDS</th>
                    <th scope="col" className="odds-ev" title="EV of the best bettable price against the fair value">BEST EDGE</th>
                    <th scope="col" style={{ textAlign: 'right' }} title="Quarter Kelly at your bankroll, edge shrunk the way the +EV Terminal sizes">KELLY / LINE</th>
                  </>
                )}
                {mode === 'arb' && arbView === 'middle' && (
                  <>
                    <th scope="col" className="odds-ev" title="Width of the window between the two lines, and what a miss and a hit pay">MIDDLE</th>
                    <th scope="col" className="odds-ev">LEGS</th>
                    <th scope="col" className="odds-ev">STAKE SPLIT ({usd(outlay).replace('.00', '')})</th>
                  </>
                )}
                {mode === 'arb' && arbView !== 'middle' && (
                  <>
                    <th scope="col" className="odds-ev" title="Margin on the outlay with the best over and under at two different books, net of Kalshi's fee">ARB / HOLD</th>
                    <th scope="col" className="odds-ev">HEDGE LEGS</th>
                    <th scope="col" className="odds-ev">STAKE SPLIT ({usd(outlay).replace('.00', '')})</th>
                  </>
                )}
                {mode === 'longshots' && (
                  <>
                    <th scope="col" className="odds-ev" title="Fair over / under price: the reference books' consensus, or the player's curve when they are too few">FAIR ANCHOR</th>
                    <th scope="col" className="odds-ev" title="The side with the higher EV">BEST ACTION</th>
                    <th scope="col" style={{ textAlign: 'right' }} title="Implied probability of the best price against the fair probability, for the best action's side">IMPLIED / TRUE</th>
                  </>
                )}
                {columns.map(c => {
                  const b = bookDisplay(c.key)
                  const isRef = refSet.has(c.key)
                  return (
                    <th key={c.key} scope="col" className="odds-book" title={`${b.label}${c.offshore ? ' (offshore: reference only)' : ''}`}>
                      <BookLogo book={c.key} size={34} style={{ display: 'block', margin: '0 auto 2px' }} />
                      <span className="mono odds-sub">{isRef && refWeight != null && mode !== 'arb' ? `${refWeight}%` : c.offshore ? 'ref' : '\u00a0'}</span>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {mode === 'ev' && screen.slice(0, limit).map(r => <EvRow key={r.key} r={r} columns={columns} bankroll={bankroll} />)}
              {mode === 'arb' && arbView === 'middle' && middles.slice(0, limit).map(r => <MiddleTableRow key={r.key} r={r} columns={columns} outlay={outlay} copied={copied === r.key} onCopy={copyMiddle} />)}
              {mode === 'arb' && arbView !== 'middle' && arbs.slice(0, limit).map(r => <ArbTableRow key={r.key} r={r} columns={columns} outlay={outlay} copied={copied === r.key} onCopy={copyHedge} />)}
              {mode === 'longshots' && longshots.slice(0, limit).map(r => <LongshotTableRow key={r.key} r={r} columns={columns} />)}
            </tbody>
          </table>
        </div>
      )}
      {nRows > limit && (
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: '10px' }}>
          <button className="btn btn-ghost" style={{ padding: '6px 14px', fontSize: '12px' }} onClick={() => setLimit(l => l + PAGE)}>Show {Math.min(PAGE, nRows - limit)} more ({nRows - limit} remaining)</button>
        </div>
      )}
      <LockedNotice locked={locked} shown={allRows.length} what="prop lines" plan="The odds screen is part of All-Access Pro." />

      <p className="times" style={{ color: 'var(--fog)', fontSize: '12px', marginTop: '24px', maxWidth: '90ch', lineHeight: 1.6 }}>
        {mode === 'ev' && <>Each cell is a book&apos;s price on the row&apos;s side above its price on the other side; green marks the best bettable price and a tint marks a book
          beating the fair price by 1.5% or more. FV is the fair price from the reference books you pick, averaged with equal weight after each book&apos;s vig is removed;
          a book is never measured against its own line. EV is fair probability times payout minus one at the best price.</>}
        {mode === 'arb' && arbView === 'middle' && <>A middle is the over of a lower line plus the under of a higher line: if the result lands strictly between them both bets win,
          anywhere else one wins and one loses, so it is a small known cost for a chance at a double win, not a risk-free bet. Stakes equalise the payout when only one leg wins.
          BREAK-EVEN is the chance the window must hit for the middle to pay for itself; P(WINDOW) is the reference books&apos; own estimate from the two lines (or the player&apos;s
          curve), so it is only as good as those prices. Each cell shows the over of the lower line above the under of the higher line; green outlines mark the two legs.
          Kalshi&apos;s taker fee is taken off a Kalshi leg. Confirm both lines before placing either, and mind each book&apos;s limits and grading rules.</>}
        {mode === 'arb' && arbView !== 'middle' && <>Each cell is a book&apos;s over price above its under price; the green outline marks the two legs of the best hedge. Stakes lock the same payout on
          either outcome. Kalshi&apos;s taker fee (0.07 &times; p &times; (1 &minus; p) per contract) is taken off its leg; other exchange commissions are not modelled.
          Prop arbs are often a stale quote or a mismatched line: confirm both legs before placing either, and mind each book&apos;s limits and grading rules.</>}
        {mode === 'longshots' && <>Each cell is a book&apos;s over price above its under price. Alt lines are rungs off the player&apos;s main line. FAIR ANCHOR is the reference
          books&apos; consensus (N BKS) or, where fewer than two price the exact line, a fair probability read off the player&apos;s own multi-line curve (CURVE). The curve is an
          unvalidated estimate: treat a CURVE edge as a place to look, not a price to trust. Snipe is the longshot over against fair; Fade is the under or NO against fair.</>}
        {' '}These are unvalidated market signals, not part of the public track record. Lines move or limit quickly: check the price at the book. Not financial advice.
        21+. If gambling is a problem, call 1-800-GAMBLER.{' '}
        <Link href="/legal/responsible-gambling" style={{ color: 'var(--link)' }}>Responsible gambling</Link>
      </p>
    </div>
  )
}

type CellState = 'best' | 'arb' | undefined

/**
 * One book's cell: two prices, the upper line first. Standard rows put the row's side on top; Arbitrage and Longshots rows
 * always put the over on top. `tint` marks a price that beats the row's fair value by CELL_EDGE; `best` is the best bettable
 * price (green text); `arb` outlines a leg of the hedge.
 */
function PriceCell({ top, bottom, topState, bottomState, tint, best }: {
  top: number | null; bottom: number | null; topState?: CellState; bottomState?: CellState; tint?: boolean; best?: boolean
}) {
  const style = (state: CellState, main: boolean): React.CSSProperties => ({
    display: 'inline-block', lineHeight: 1.3, padding: '0 4px', borderRadius: '3px',
    fontSize: main ? '12.5px' : '10.5px', fontWeight: state ? 800 : main ? 600 : 400,
    color: state ? GREEN : main ? 'var(--chalk)' : 'var(--fog)',
    outline: state === 'arb' ? `1px solid ${GREEN}` : 'none', outlineOffset: '1px',
  })
  return (
    <td className="odds-cell" data-best={best ? 'true' : undefined} data-edge={tint ? 'true' : undefined} style={{ borderLeft: `1px solid ${HAIR}` }}>
      {top == null && bottom == null ? <span className="mono odds-sub">&ndash;</span> : (
        <>
          <div><span className="mono" style={style(topState, true)}>{top == null ? '\u2013' : formatOdds(top)}</span></div>
          <div><span className="mono" style={style(bottomState, false)}>{bottom == null ? '\u2013' : formatOdds(bottom)}</span></div>
        </>
      )}
    </td>
  )
}

function TierPill({ alt, modalPoint }: { alt: boolean; modalPoint?: number | null }) {
  const tip = alt
    ? (modalPoint != null ? `Main line: ${modalPoint}` : 'An alternate line: a point or more off the main line')
    : 'The main line'
  return (
    <span className="mono" title={tip}
      style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.04em', padding: '0 5px', borderRadius: '4px', border: `1px solid ${alt ? '#3a4156' : HAIR}`, color: alt ? '#9ca3af' : 'var(--fog)' }}>
      {alt ? '[ALT]' : 'MAIN'}
    </span>
  )
}

function PropCells({ row, label }: { row: BoardRow; label: string }) {
  const mainLineTip = row.modal_point != null ? `Main line: ${row.modal_point}` : undefined
  return (
    <>
      <td className="odds-prop">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ flexShrink: 0, width: 30, height: 30, display: 'inline-flex' }}>
            <PlayerHeadshot sport={row.game_key.split(':')[0] as HeadshotSport} id={row.headshot_id} name={row.player} size={30} />
          </span>
          <div>
            <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--chalk)', lineHeight: 1.3 }}>{row.player}</div>
            <div className="mono odds-sub" style={{ fontSize: '11px', display: 'flex', alignItems: 'center', gap: '5px' }}>
              <span>{label} {STAT_LABEL[row.stat] ?? row.stat}</span>
              {row.is_alt === true && (
                <span title={mainLineTip} style={{ fontSize: '10px', color: '#9ca3af', fontFamily: 'var(--font-mono), monospace' }}>[ALT]</span>
              )}
            </div>
          </div>
        </div>
      </td>
      <td>
        <div className="mono" style={{ fontSize: '11.5px', color: 'var(--silver)' }}>{row.away} @ {row.home}</div>
        <div className="mono odds-sub">{startLabel(row.commence)}</div>
      </td>
    </>
  )
}

function dec(am: number): number { return americanToDecimal(am) }

function EvRow({ r, columns, bankroll }: { r: ScreenRow; columns: { key: string; offshore: boolean }[]; bankroll: number }) {
  const d = r.implied > 0 ? 1 / r.implied : 1
  const size = r.pFair == null ? null : kellySizing(bankroll, r.pFair, d)
  const byBook = new Map(r.row.books.map(b => [b.book, b]))
  const evColor = r.ev != null && r.ev > 0 ? GREEN : 'var(--fog)'
  return (
    <tr data-row="odds">
      <PropCells row={r.row} label={r.label} />
      <td className="odds-ev">
        {r.fairAmerican == null || r.pFair == null ? (
          <>
            <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: 'var(--fog)' }}>--</div>
            <div className="mono odds-sub" title={`Only ${r.nRef} reference book${r.nRef === 1 ? '' : 's'} priced this line both ways; ${MIN_REF} are needed for a fair value`}>{r.nRef} of {MIN_REF} bk</div>
          </>
        ) : (
          <>
            <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: 'var(--chalk)' }}>{r.fairAmerican > 0 ? `+${r.fairAmerican}` : r.fairAmerican}</div>
            <div className="mono odds-sub" title={`${r.nRef} reference books; the other side's fair price is ${fairText(1 - r.pFair)}`}>{r.nRef} BKS &middot; {fairText(1 - r.pFair)}</div>
          </>
        )}
      </td>
      <td className="odds-ev">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <BookLogo book={r.bestBook} size={28} />
          <div>
            <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: evColor }}>{r.ev == null ? '--' : `${signed(r.ev * 100)}%`}</div>
            <div className="mono odds-sub">{r.side} {formatOdds(r.bestAmerican)}</div>
          </div>
        </div>
      </td>
      <td style={{ textAlign: 'right' }}>
        <div className="mono" style={{ fontSize: '12px', color: size && size.dollars > 0 ? 'var(--chalk)' : 'var(--fog)' }}>{size && size.dollars > 0 ? fmtMoney(size.dollars) : '--'}</div>
        <div style={{ marginTop: '2px' }}><TierPill alt={r.row.is_alt === true} modalPoint={r.row.modal_point} /></div>
      </td>
      {columns.map(c => {
        const q = byBook.get(c.key)
        const target = q ? (r.side === 'OVER' ? q.over : q.under) : null
        const other = q ? (r.side === 'OVER' ? q.under : q.over) : null
        const isBest = !c.offshore && r.bestTies.includes(c.key)
        const edge = !c.offshore && r.pFair != null && target != null && r.pFair * dec(target) - 1 >= CELL_EDGE
        return <PriceCell key={c.key} top={target} bottom={other} topState={isBest ? 'best' : undefined} best={isBest} tint={edge} />
      })}
    </tr>
  )
}

function BookTag({ book, size = 16 }: { book: string; size?: number }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
      <BookLogo book={book} size={size} />
      <span style={{ fontSize: '11.5px', color: 'var(--silver)' }}>{bookDisplay(book).label}</span>
    </span>
  )
}

function ArbTableRow({ r, columns, outlay, copied, onCopy }: {
  r: ArbRow; columns: { key: string; offshore: boolean }[]; outlay: number; copied: boolean; onCopy: (r: ArbRow) => void
}) {
  const a = r.arb
  const k = outlay / 100
  const byBook = new Map(r.row.books.map(b => [b.book, b]))
  const live = a.netArbPct > 0
  const profit = a.guaranteedProfitPer100 * k
  return (
    <tr data-row="odds">
      <PropCells row={r.row} label={String(r.row.line)} />
      <td className="odds-ev">
        <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: live ? GREEN : SKY }}
          title={a.feeLeg ? `Gross ${signed(a.arbPct, 2)}%, net of Kalshi's taker fee ${signed(a.netArbPct, 2)}%` : `${signed(a.netArbPct, 2)}%, no fee on either leg`}>
          {signed(a.netArbPct, 2)}% {live ? 'ARB' : 'HOLD'}
        </div>
        <div className="mono odds-sub">{a.feeLeg ? 'NET OF KALSHI FEE' : '\u00a0'}</div>
      </td>
      <td className="odds-ev">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span className="mono" style={{ fontSize: '11.5px', fontWeight: 700, color: 'var(--chalk)', minWidth: '84px' }}>OVER {formatOdds(a.bestOverAm)}</span>
          <BookTag book={a.bestOverBook} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '3px' }}>
          <span className="mono" style={{ fontSize: '11.5px', fontWeight: 700, color: 'var(--chalk)', minWidth: '84px' }}>UNDER {formatOdds(a.bestUnderAm)}</span>
          <BookTag book={a.bestUnderBook} />
        </div>
      </td>
      <td className="odds-ev">
        <div className="mono" style={{ fontSize: '12px', color: 'var(--chalk)' }}>{usd(a.stakeOverPer100 * k)} Over / {usd(a.stakeUnderPer100 * k)} Under</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '2px' }}>
          <span className="mono odds-sub" style={{ color: live ? GREEN : SKY }}>{profit >= 0 ? '+' : '-'}{usd(Math.abs(profit))} {live ? 'locked' : 'at this hold'}</span>
          <button className="btn btn-ghost" onClick={() => onCopy(r)} style={{ padding: '1px 8px', fontSize: '10.5px' }}>{copied ? 'Copied' : 'Copy hedge'}</button>
        </div>
      </td>
      {columns.map(c => {
        const q = byBook.get(c.key)
        return (
          <PriceCell key={c.key} top={q ? q.over : null} bottom={q ? q.under : null}
            topState={c.key === a.bestOverBook ? 'arb' : undefined} bottomState={c.key === a.bestUnderBook ? 'arb' : undefined} />
        )
      })}
    </tr>
  )
}

function MiddleTableRow({ r, columns, outlay, copied, onCopy }: {
  r: MiddleResult; columns: { key: string; offshore: boolean }[]; outlay: number; copied: boolean; onCopy: (r: MiddleResult) => void
}) {
  const k = outlay / 100
  const low = new Map(r.low.books.map(b => [b.book, b]))
  const high = new Map(r.high.books.map(b => [b.book, b]))
  const free = r.missPct >= 0
  return (
    <tr data-row="odds">
      <PropCells row={r.low} label={`o${r.low.line} / u${r.high.line}`} />
      <td className="odds-ev">
        <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: free ? GREEN : SKY }}
          title={r.feeLeg ? "Net of Kalshi's taker fee" : 'Both legs win only if the result lands strictly between the two lines'}>
          {r.gap % 1 === 0 ? r.gap : r.gap.toFixed(1)} PT WINDOW
        </div>
        <div className="mono odds-sub" title="Profit on the outlay if one leg wins (a miss) and if both win (inside the window)">
          MISS {signed(r.missPct)}% &middot; HIT {signed(r.hitPct)}%
        </div>
      </td>
      <td className="odds-ev">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span className="mono" style={{ fontSize: '11.5px', fontWeight: 700, color: 'var(--chalk)', minWidth: '104px' }}>OVER {r.low.line} {formatOdds(r.overAm)}</span>
          <BookTag book={r.overBook} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '3px' }}>
          <span className="mono" style={{ fontSize: '11.5px', fontWeight: 700, color: 'var(--chalk)', minWidth: '104px' }}>UNDER {r.high.line} {formatOdds(r.underAm)}</span>
          <BookTag book={r.underBook} />
        </div>
      </td>
      <td className="odds-ev">
        <div className="mono" style={{ fontSize: '12px', color: 'var(--chalk)' }}>{usd(r.stakeOverPer100 * k)} Over / {usd(r.stakeUnderPer100 * k)} Under</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '2px' }}>
          <span className="mono odds-sub" title="The chance the window must hit for the middle to break even; the estimate comes from the reference books' fair price at each line">
            BREAK-EVEN {(r.breakevenProb * 100).toFixed(1)}%{r.midProb != null ? ` \u00b7 P(WINDOW) ${(r.midProb * 100).toFixed(1)}%` : ''}
          </span>
          <button className="btn btn-ghost" onClick={() => onCopy(r)} style={{ padding: '1px 8px', fontSize: '10.5px' }}>{copied ? 'Copied' : 'Copy middle'}</button>
        </div>
      </td>
      {columns.map(c => {
        const o = low.get(c.key)
        const u = high.get(c.key)
        return (
          <PriceCell key={c.key} top={o ? o.over : null} bottom={u ? u.under : null}
            topState={c.key === r.overBook ? 'arb' : undefined} bottomState={c.key === r.underBook ? 'arb' : undefined} />
        )
      })}
    </tr>
  )
}

/** "NO 77c" on an exchange (the price is a cent price), the American price elsewhere. */
function underText(book: string, am: number): string {
  return book === 'kalshi' || book === 'novig' ? `NO ${Math.round((1 / dec(am)) * 100)}c` : `UNDER ${formatOdds(am)}`
}

function LongshotTableRow({ r, columns }: { r: LongshotRow; columns: { key: string; offshore: boolean }[] }) {
  const m = r.m
  const byBook = new Map<string, BoardBook>(r.row.books.map(b => [b.book, b]))
  const evOf = r.bestEv
  const hot = evOf != null && evOf > 0
  const over = r.side !== 'UNDER'
  const implied = over ? m.overImplied : m.underImplied
  const truth = over ? m.fairOverProb : m.fairUnderProb
  const book = over ? m.bestOverBook : m.bestUnderBook
  const am = over ? m.bestOverAm : m.bestUnderAm
  const color = !hot ? 'var(--fog)' : over ? GREEN : SKY
  return (
    <tr data-row="odds">
      <PropCells row={r.row} label={String(r.row.line)} />
      <td className="odds-ev">
        {m.fairOverProb == null ? (
          <>
            <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: 'var(--fog)' }}>--</div>
            <div className="mono odds-sub" title="Fewer than two reference books price this exact line and no curve covers it">NO FAIR VALUE</div>
          </>
        ) : (
          <>
            <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: 'var(--chalk)' }}>{fairText(m.fairOverProb)} / {fairText(m.fairUnderProb)}</div>
            <div className="mono odds-sub" style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
              {m.fairSource === 'curve' ? (
                <span title={`Implied from player's main line (${r.row.modal_point ?? 'main'}) across reference books`}
                  style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.04em', padding: '0 5px', borderRadius: '4px', border: `1px solid ${VIOLET}66`, color: VIOLET }}>EST. FAIR</span>
              ) : <span>{m.nRef} BKS</span>}
              <TierPill alt={m.isAltLine} modalPoint={r.row.modal_point} />
            </div>
          </>
        )}
      </td>
      <td className="odds-ev">
        {book == null || am == null ? (
          <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: 'var(--fog)' }}>--</div>
        ) : (
          <>
            <div className="mono" style={{ fontSize: '12.5px', fontWeight: 700, color }}>
              {over ? `SNIPE OVER ${formatOdds(am)}` : `FADE ${underText(book, am)}`}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '2px' }}>
              <BookLogo book={book} size={16} />
              <span className="mono odds-sub" style={{ display: 'inline' }}>{bookDisplay(book).label}{evOf == null ? '' : ` \u00b7 ${signed(evOf)}% EV`}</span>
            </div>
          </>
        )}
      </td>
      <td style={{ textAlign: 'right' }}>
        <div className="mono" style={{ fontSize: '12px', color: 'var(--silver)' }}>
          {implied == null ? '--' : `${(implied * 100).toFixed(1)}%`} / <span style={{ color: truth != null && implied != null && truth > implied ? GREEN : 'var(--silver)' }}>{truth == null ? '--' : `${(truth * 100).toFixed(1)}%`}</span>
        </div>
        <div className="mono odds-sub">{over ? 'OVER' : 'UNDER'} IMPL / TRUE</div>
      </td>
      {columns.map(c => {
        const q = byBook.get(c.key)
        const o = q ? q.over : null
        const u = q ? q.under : null
        const edgeO = !c.offshore && m.fairOverProb != null && o != null && m.fairOverProb * dec(o) - 1 >= CELL_EDGE
        const edgeU = !c.offshore && m.fairUnderProb != null && u != null && m.fairUnderProb * dec(u) - 1 >= CELL_EDGE
        return (
          <PriceCell key={c.key} top={o} bottom={u} tint={edgeO || edgeU}
            topState={c.key === m.bestOverBook ? 'best' : undefined} bottomState={c.key === m.bestUnderBook ? 'best' : undefined} />
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

function PillGroup({ children, label }: { children: React.ReactNode; label?: string }) {
  return <div role={label ? 'group' : undefined} aria-label={label} style={{ display: 'inline-flex', flexWrap: 'wrap', gap: '2px', padding: '3px', background: '#090a0f', border: `1px solid ${HAIR}`, borderRadius: '8px', maxWidth: '100%' }}>{children}</div>
}

/** A button that opens a checklist of books (a popover closed by outside click or Escape). `emptyMeansAll`: none ticked = every book. */
function BookPicker({ label, books, selected, onChange, presets, note, emptyMeansAll, align, header }: {
  label: string
  books: { key: string; n: number; offshore: boolean }[]
  selected: string[]
  onChange: (next: string[]) => void
  presets: { label: string; keys: string[] }[]
  note: string
  emptyMeansAll?: boolean
  /** which edge the popover hangs from (the right-hand pickers open leftwards so they stay on screen) */
  align?: 'left' | 'right'
  /** extra controls above the presets */
  header?: React.ReactNode
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
        <div role="group" aria-label={label} style={{ position: 'absolute', zIndex: 30, top: '38px', ...(align === 'right' ? { right: 0 } : { left: 0 }), width: '280px', ...SURFACE_CARD, padding: '10px', display: 'grid', gap: '8px' }}>
          {header}
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
                  <span style={{ minWidth: '28px', display: 'inline-flex', justifyContent: 'center' }}><BookLogo book={b.key} size={24} /></span>
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
