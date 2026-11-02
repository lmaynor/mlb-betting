import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { apiGetOddsBoard } from '@/lib/betting-api'
import { bookDisplay, canonBook } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'
import { useBankroll } from '@/lib/use-bankroll'
import { useMyBooks } from '@/lib/use-my-books'
import { type SportSlug } from '@/lib/sports'
import { PRE_LAUNCH } from '@/lib/launch'
import { useIsMobile } from '@/components/ui/mobile-sheet'
import { buildArbRows, buildLongshotRows, buildScreenRows, computeMiddles, computeOutliers, computeSgpProxies, STAT_LABEL, type ArbRow, type BoardRow, type DevigMethod, type LongshotRow, type LongshotSubfilter, type MiddleResult, type OddsBoard, type OddsScreenMode, type ScreenRow, type SideFilter } from '@/lib/odds-screen'
import { SportFilter, SortKey, LinesFilter, ArbView, OutlierView, OutlierOddsBand, SPORT_KEYS, BOOK_ORDER, PAGE, MIN_REF, LONGSHOT_MIN_REF, NEAR_ARB_PCT, MIDDLE_MIN_REF, POLL_MS, BadgeKey, BADGE_KEYS } from './odds-constants'
import { usd } from './odds-format'

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

/** Run `fn` when the browser is idle (a timer where requestIdleCallback is missing); returns a cancel function. */
function scheduleIdle(fn: () => void): () => void {
  if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
    const id = window.requestIdleCallback(fn, { timeout: 1500 })
    return () => window.cancelIdleCallback(id)
  }
  const id = setTimeout(fn, 120)
  return () => clearTimeout(id)
}

export function useOddsScreen({ initial, initialLocked, fullAccess }: { initial: Partial<Record<SportSlug, OddsBoard>>; initialLocked: number; fullAccess: boolean }) {
  const sp = useSearchParams()
  const viewParam = sp?.get('view')
  const initialMode: OddsScreenMode = (viewParam && ['ev', 'outliers', 'arb', 'middles', 'longshots', 'sgp'].includes(viewParam))
    ? (viewParam as OddsScreenMode)
    : 'ev'

  const [boards, setBoards] = useState(initial)
  const [locked, setLocked] = useState(initialLocked)
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState<OddsScreenMode>(initialMode)
  const [sport, setSport] = useState<SportFilter>('all')
  const [stat, setStat] = useState('all')
  const [game, setGame] = useState('all')
  const [side, setSide] = useState<SideFilter>('both')
  const [lines, setLines] = useState<LinesFilter>('all')
  const [minEv, setMinEv] = useState(0)
  const [sgpArch, setSgpArch] = useState<string>('all')
  const [sgpMinEv, setSgpMinEv] = useState<number>(0)
  const [sgpBook, setSgpBook] = useState<string>('all')
  const [sgpExtreme, setSgpExtreme] = useState(false)
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
  const [outlierGap, setOutlierGap] = useState(8)
  const [outlierReqEv, setOutlierReqEv] = useState(false)
  const [outlierOddsBand, setOutlierOddsBand] = useState<OutlierOddsBand>('all')
  const [outlierMinBooks, setOutlierMinBooks] = useState(3)
  const [outlierView, setOutlierView] = useState<OutlierView>('cards')
  const [revealed, setRevealed] = useState<Set<string>>(new Set())
  const [revealMsg, setRevealMsg] = useState<string | null>(null)
  const [bankroll] = useBankroll(1000)
  const myBooks = useMyBooks()
  const isMobile = useIsMobile(768)
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false)
  const [mobileView, setMobileView] = useState<'cards' | 'table'>('cards')
  const [inspectItem, setInspectItem] = useState<
    | { type: 'ev'; row: ScreenRow }
    | { type: 'arb'; row: ArbRow }
    | { type: 'middles'; row: MiddleResult }
    | { type: 'longshots'; row: LongshotRow }
    | null
  >(null)

  const activeFilterCount = useMemo(() => {
    let count = 0
    if (sport !== 'all') count++
    if (stat !== 'all') count++
    if (game !== 'all') count++
    if (q.trim() !== '') count++
    if (refs.length > 0) count++
    if (mode === 'ev') {
      if (lines !== 'all') count++
      if (side !== 'both') count++
      if (minEv > 0) count++
      if (sort !== 'ev') count++
    } else if (mode === 'outliers') {
      if (outlierGap !== 8) count++
      if (outlierReqEv) count++
      if (outlierOddsBand !== 'all') count++
      if (outlierMinBooks !== 3) count++
    } else if (mode === 'arb') {
      if (arbView !== 'live') count++
      if (!exchanges) count++
      if (outlayText !== '100') count++
    } else if (mode === 'middles') {
      if (midCost !== 5) count++
      if (midPosEv) count++
      if (midGap !== 0) count++
      if (!exchanges) count++
      if (outlayText !== '100') count++
    } else if (mode === 'longshots') {
      if (longSub !== 'all') count++
      if (oddsMin > 0) count++
      if (minEv > 0) count++
      if (!useCurve) count++
    } else if (mode === 'sgp') {
      if (sgpArch !== 'all') count++
      if (sgpMinEv > 0) count++
      if (sgpBook !== 'all') count++
      if (sgpExtreme) count++
      if (!useCurve) count++
    }
    return count
  }, [sport, stat, game, q, refs.length, mode, lines, side, minEv, sort, outlierGap, outlierReqEv, outlierOddsBand, outlierMinBooks, arbView, exchanges, outlayText, midCost, midPosEv, midGap, longSub, oddsMin, useCurve, sgpArch, sgpMinEv, sgpBook, sgpExtreme])

  const [prevViewParam, setPrevViewParam] = useState(viewParam)
  if (viewParam !== prevViewParam) {
    setPrevViewParam(viewParam)
    if (viewParam && ['ev', 'outliers', 'arb', 'middles', 'longshots', 'sgp'].includes(viewParam)) {
      setMode(viewParam as OddsScreenMode)
    }
  }

  const switchMode = useCallback((m: OddsScreenMode) => {
    setMode(m)
    setLimit(PAGE)
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href)
      if (m === 'ev') url.searchParams.delete('view')
      else url.searchParams.set('view', m)
      window.history.replaceState(null, '', url.pathname + url.search)
    }
  }, [])

  const handleReveal = useCallback((key: string) => {
    if (fullAccess) {
      setRevealed(prev => new Set(prev).add(key))
      if (PRE_LAUNCH) {
        setRevealMsg('Pre-launch preview: all books are unlocked for early visitors.')
        setTimeout(() => setRevealMsg(null), 3500)
      }
    } else {
      window.location.href = '/pricing'
    }
  }, [fullAccess])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const nextBoards = await Promise.all(SPORT_KEYS.map(s => apiGetOddsBoard(s, { limit: 3000 }).catch(() => null)))
      const out: Partial<Record<SportSlug, OddsBoard>> = {}
      let lk = 0
      nextBoards.forEach((b, i) => { if (b) { out[SPORT_KEYS[i]] = b; lk += b.locked ?? 0 } })
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

  // ---- Market Outliers: single-book price dislocations vs peer consensus
  const outliers = useMemo(() => {
    if (mode !== 'outliers') return []
    return computeOutliers(filtered, {
      refs,
      method,
      bettable,
      minPeers: outlierMinBooks,
      minPremiumPct: outlierGap,
      minEvPct: outlierReqEv ? 2.0 : 0,
      oddsBand: outlierOddsBand,
      side,
      useCurve,
    })
  }, [mode, filtered, refs, method, bettable, outlierMinBooks, outlierGap, outlierReqEv, outlierOddsBand, side, useCurve])

  // ---- Arbitrage: one row per line, best margin first
  const arbs = useMemo(() => {
    if (mode !== 'arb') return []
    const all = buildArbRows(filtered, { bettable, includeExchanges: exchanges, minNetArbPct: NEAR_ARB_PCT })
    return (arbView === 'live' ? all.filter(r => r.arb.netArbPct > 0) : all).sort((a, b) => b.arb.netArbPct - a.arb.netArbPct)
  }, [mode, filtered, bettable, exchanges, arbView])

  // ---- Middles: the over of a lower line + the under of a higher line, same player and stat
  const middles = useMemo(() => {
    if (mode !== 'middles') return []
    return computeMiddles(filtered, { bettable, includeExchanges: exchanges, maxCostPct: midCost, minGap: midGap, onlyPositiveEv: midPosEv, refs, method, minRef: MIDDLE_MIN_REF, useCurve })
  }, [mode, filtered, bettable, exchanges, midCost, midGap, midPosEv, refs, method, useCurve])

  // ---- Alt & Longshots: one row per line, best EV (either side) first
  const longshots = useMemo(() => {
    if (mode !== 'longshots') return []
    const rows = buildLongshotRows(filtered, { refs, method, bettable, minRef: LONGSHOT_MIN_REF, useCurve, sub: longSub, minEv: Math.max(minEv, 0), minOverAm: oddsMin })
    return rows.sort((a, b) => (b.bestEv ?? -Infinity) - (a.bestEv ?? -Infinity))
  }, [mode, filtered, refs, method, bettable, useCurve, longSub, minEv, oddsMin])

  // ---- SGP Proxies: modelled same-book SGP tickets (assumed correlation constants: unvalidated model output, not quotes)
  const sgpAll = useMemo(() => {
    if (mode !== 'sgp') return []
    const all = computeSgpProxies(filtered, {
      refs,
      method,
      bettable,
      minEvPct: sgpMinEv,
      book: sgpBook === 'all' ? undefined : sgpBook,
      useCurve,
      minRef: MIN_REF,
    })
    if (sgpArch === 'milestones') return all.filter(r => r.archetype === 'nba_reb_ast_10_10')
    if (sgpArch === 'combos') return all.filter(r => r.archetype.includes('_vs_'))
    if (sgpArch === 'stacks') return all.filter(r => r.archetype.includes('stack'))
    return all
  }, [mode, filtered, refs, method, bettable, sgpMinEv, sgpBook, useCurve, sgpArch])
  // rows above SGP_ARTIFACT_EV_PCT are flagged model artifacts: hidden (and counted) unless 'Include extreme' is on
  const sgpExtremeCount = useMemo(() => sgpAll.filter(r => r.modelArtifact).length, [sgpAll])
  const sgpProxies = useMemo(() => sgpExtreme ? sgpAll : sgpAll.filter(r => !r.modelArtifact), [sgpAll, sgpExtreme])

  // Mode badges. The ACTIVE mode's badge is its own row count (already computed for the table). The other modes' counts
  // are computed one engine at a time when the browser is idle, from deferred inputs, so a filter change or a mode click
  // paints the table first and never runs all six engines in one task.
  const badgeIn = useMemo(() => ({ scoped, refs, method, bettable, minEv, lines, side, exchanges, useCurve }),
    [scoped, refs, method, bettable, minEv, lines, side, exchanges, useCurve])
  const dBadgeIn = useDeferredValue(badgeIn)
  const [badgeVals, setBadgeVals] = useState<Partial<Record<BadgeKey, number>>>({})
  const badgeCache = useRef<{ src: object | null; vals: Partial<Record<BadgeKey, number>> }>({ src: null, vals: {} })
  useEffect(() => {
    const d = dBadgeIn
    const cache = badgeCache.current
    if (cache.src !== d) { cache.src = d; cache.vals = {} }
    const floor = Math.max(d.minEv, 0)
    const compute = (k: BadgeKey): number => {
      switch (k) {
        case 'ev': {
          const src = d.lines === 'main' ? d.scoped.filter(r => !r.is_alt) : d.scoped
          return buildScreenRows(src, { refs: d.refs, method: d.method, bettable: d.bettable, minRef: MIN_REF, side: d.side }).filter(r => r.ev != null && r.ev >= floor).length
        }
        case 'outliers':
          return computeOutliers(d.scoped, { refs: d.refs, method: d.method, bettable: d.bettable, minPeers: 3, minPremiumPct: 8, minEvPct: 0, side: d.side, useCurve: d.useCurve }).length
        case 'arb':
          return buildArbRows(d.scoped, { bettable: d.bettable, includeExchanges: d.exchanges, minNetArbPct: 0 }).length
        case 'middles':
          return computeMiddles(d.scoped, { bettable: d.bettable, includeExchanges: d.exchanges, maxCostPct: 5, minGap: 0, onlyPositiveEv: false, refs: d.refs, method: d.method, minRef: MIDDLE_MIN_REF, useCurve: d.useCurve }).length
        case 'longshots':
          return buildLongshotRows(d.scoped, { refs: d.refs, method: d.method, bettable: d.bettable, minRef: LONGSHOT_MIN_REF, useCurve: d.useCurve, sub: 'all', minEv: 0, minOverAm: 0 })
            .filter(r => r.bestEv != null && r.bestEv >= floor * 100).length
        case 'sgp':
          // flagged model artifacts are not counted (they are hidden by default)
          return computeSgpProxies(d.scoped, { refs: d.refs, method: d.method, bettable: d.bettable, minEvPct: 0, useCurve: d.useCurve, minRef: MIN_REF }).filter(r => !r.modelArtifact).length
      }
    }
    const todo = BADGE_KEYS.filter(k => k !== mode)
    let cancel: (() => void) | null = null
    let dead = false
    const step = () => {
      if (dead) return
      const k = todo.shift()
      if (!k) return
      if (cache.vals[k] == null) {
        cache.vals[k] = compute(k)
        setBadgeVals({ ...cache.vals })
      }
      if (todo.length) cancel = scheduleIdle(step)
    }
    cancel = scheduleIdle(step)
    return () => { dead = true; cancel?.() }
  }, [dBadgeIn, mode])

  const nRows = mode === 'ev' ? screen.length
    : mode === 'outliers' ? outliers.length
    : mode === 'arb' ? arbs.length
    : mode === 'middles' ? middles.length
    : mode === 'longshots' ? longshots.length
    : sgpProxies.length
  const badgeFor = (k: BadgeKey): number | null => k === mode ? nRows : (badgeVals[k] ?? null)

  const meta = useMemo(() => {
    const activeWithRows = active.filter(s => (boards[s]?.count ?? 0) > 0)
    const target = activeWithRows.length > 0 ? activeWithRows : active
    const hasRows = activeWithRows.length > 0
    const ages = (hasRows ? target : target.filter(s => boards[s]!.status === 'live'))
      .map(s => boards[s]!.age_minutes)
      .filter((n): n is number => n != null)
    const sources = [...new Set(target.flatMap(s => boards[s]!.lines_source))]
    return {
      age: ages.length ? Math.max(...ages) : null,
      stale: target.some(s => Boolean(boards[s]!.stale && (boards[s]!.count ?? 0) > 0)),
      sources,
    }
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

  const emptyBody = mode === 'outliers'
    ? { title: 'No market outliers clear these filters', body: 'An outlier needs one bettable book hanging a price significantly above the market average of at least 3 peer books. Lower the MIN GAP threshold, widen books, or turn off Verified +EV Only.' }
    : mode === 'sgp'
    ? { title: 'No SGP proxies clear these filters', body: 'SGP Proxies models two-leg same-book tickets from single-leg prices (unvalidated: assumed correlation and hold). Rows above 45% model EV are hidden as likely artifacts unless you include them; lower the MIN EV floor or switch archetypes.' }
    : mode === 'middles'
    ? { title: 'No middles at this cost', body: 'A middle needs the over of a lower line and the under of a higher line, priced close enough that a miss costs little. Raise the max cost, loosen the window filter, or include exchanges.' }
    : mode === 'arb'
    ? { title: arbView === 'live' ? 'No live arbitrage on the board' : 'No near arbitrage on the board',
        body: 'A live arb needs the best over and the best under at two different books to pay more than the outlay together. Try Near Arbs, include exchanges, or widen the books you can bet at.' }
    : mode === 'longshots'
      ? { title: 'No longshots clear these filters', body: 'Set the strategy to All, lower the odds range or MIN EV, or turn on curve-fitted fair value. Alt rungs are quoted by few books, so many have no fair value without the curve.' }
      : { title: 'Nothing clears these filters', body: 'Set MIN EV to All to see every line, widen the devig books, or clear the prop and game filters. A +EV row needs at least three devig books pricing the line both ways.' }
  const refsNote = mode === 'longshots' ? 'Two books behind a fair value (the curve stands in below that).' : 'Needs 3 books behind a row.'

  return { isMobile, meta, nRows, loading, refresh, mode, limit, setLimit, locked, allRows, columns, inspectItem, setInspectItem, stat, setStat, game, setGame, side, setSide, lines, setLines, minEv, setMinEv, sgpArch, setSgpArch, sgpMinEv, setSgpMinEv, sgpBook, setSgpBook, setQ, method, setMethod, refs, setRefs, setShown, sort, setSort, arbView, setArbView, exchanges, setExchanges, outlayText, setOutlayText, midCost, setMidCost, midGap, setMidGap, midPosEv, setMidPosEv, longSub, setLongSub, oddsMin, setOddsMin, useCurve, setUseCurve, outlierGap, setOutlierGap, outlierReqEv, setOutlierReqEv, outlierOddsBand, setOutlierOddsBand, outlierMinBooks, setOutlierMinBooks, outlierView, setOutlierView, myBooks, mobileFiltersOpen, setMobileFiltersOpen, bookList, defaultShown, shownSet, statOptions, games, refsNote, sport, setSport, q, activeFilterCount, switchMode, badgeFor, copied, revealed, revealMsg, setRevealMsg, bankroll, setMobileView, handleReveal, screen, outliers, arbs, middles, longshots, sgpProxies, outlay, copyHedge, copyMiddle, fullAccess, refWeight, refSet, sgpExtreme, setSgpExtreme, mobileView, sgpExtremeCount, unpublished, emptyBody }
}

export type OddsScreen = ReturnType<typeof useOddsScreen>
