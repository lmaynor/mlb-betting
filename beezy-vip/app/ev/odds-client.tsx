'use client'

import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { apiGetGameProjections, apiGetOddsBoard } from '@/lib/betting-api'
import type { GamePricedGame, GameProjectionRow, GameProjectionsFeed, GamePricedMarket } from '@/lib/game-lines'
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
import { HorizontalPillStrip, MobileBottomSheet, useIsMobile } from '@/components/ui/mobile-sheet'
import {
  buildArbRows, buildLongshotRows, buildScreenRows, computeMiddles, computeOutliers, computeSgpProxies, fairAmericanOf, STAT_LABEL,
  type ArbRow, type BoardBook, type BoardRow, type DevigMethod, type LongshotRow, type LongshotSubfilter, type MiddleResult, type OddsBoard,
  type OddsScreenMode, type OutlierRow, type ScreenRow, type SgpProxyRow, type SideFilter,
} from '@/lib/odds-screen'

type SportFilter = 'all' | SportSlug
type SortKey = 'ev' | 'time' | 'player'
type LinesFilter = 'main' | 'all'
type ArbView = 'live' | 'near'
type OutlierView = 'cards' | 'table'
type OutlierOddsBand = 'all' | 'main' | 'plus_money' | 'longshots'

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

const OUTLIER_GAP_OPTIONS = [
  { label: '+8%+', v: 8 },
  { label: '+15%+', v: 15 },
  { label: '+25%+', v: 25 },
  { label: '+40%+', v: 40 },
]
const OUTLIER_ODDS_OPTIONS: { label: string; v: OutlierOddsBand }[] = [
  { label: 'All Odds', v: 'all' },
  { label: 'Main Lines (<+200)', v: 'main' },
  { label: 'Plus Money (+100+)', v: 'plus_money' },
  { label: 'Tail (+250+)', v: 'longshots' },
]
const OUTLIER_MIN_BOOKS = [
  { label: '3+ Books', v: 3 },
  { label: '4+ Books', v: 4 },
  { label: '5+ Books', v: 5 },
]

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
  { key: 'outliers', label: 'Market Outliers' },
  { key: 'arb', label: 'Arbitrage' },
  { key: 'middles', label: 'Middles' },
  { key: 'longshots', label: 'Alt & Tail Ladders' },
  { key: 'sgp', label: 'SGP Proxies' },
  { key: 'game_lines', label: 'Game Lines' },
]

const SGP_ARCHETYPE_OPTIONS = [
  { v: 'all', label: 'All Combos' },
  { v: 'stacks', label: 'Teammate Stacks (NHL Linemates / QB+WR / PG+Scorer)' },
  { v: 'milestones', label: 'TD & DD Milestones (10+R/10+A)' },
  { v: 'combos', label: 'Stat Combos (P+R / P+A / R+A / G+A)' },
]

const SGP_MIN_EV_OPTIONS = [
  { v: 0, label: 'All' },
  { v: 2, label: '+2%+' },
  { v: 5, label: '+5%+' },
  { v: 10, label: '+10%+' },
]

export function OddsClient({ initial, initialLocked, headline, initialGames }: { initial: Partial<Record<SportSlug, OddsBoard>>; initialLocked: number; headline: EvHeadline | null; initialGames?: Record<string, GameProjectionsFeed | null> }) {
  const sp = useSearchParams()
  const viewParam = sp?.get('view')
  const initialMode: OddsScreenMode = (viewParam && ['ev', 'outliers', 'arb', 'middles', 'longshots', 'sgp', 'game_lines'].includes(viewParam))
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
  const [gamesData, setGamesData] = useState<Record<string, GameProjectionsFeed | null>>(initialGames ?? {})
  const [gameMarket, setGameMarket] = useState<'all' | 'spread' | 'total' | 'ml'>('all')
  const [gameMatchup, setGameMatchup] = useState<string>('all')
  const [gameSport, setGameSport] = useState<'all' | 'nba' | 'nhl'>('all')
  const [gameSide, setGameSide] = useState<'all' | 'home_over' | 'away_under'>('all')
  const [gameMinEdge, setGameMinEdge] = useState<number>(0)
  const [gameView, setGameView] = useState<'cards' | 'table'>('cards')
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
      if (!useCurve) count++
    } else if (mode === 'game_lines') {
      if (gameMarket !== 'all') count++
      if (gameMatchup !== 'all') count++
      if (gameSport !== 'all') count++
      if (gameSide !== 'all') count++
      if (gameMinEdge > 0) count++
    }
    return count
  }, [sport, stat, game, q, refs.length, mode, lines, side, minEv, sort, outlierGap, outlierReqEv, outlierOddsBand, outlierMinBooks, arbView, exchanges, outlayText, midCost, midPosEv, midGap, longSub, oddsMin, useCurve, sgpArch, sgpMinEv, sgpBook, gameMarket, gameMatchup, gameSport, gameSide, gameMinEdge])

  const [prevViewParam, setPrevViewParam] = useState(viewParam)
  if (viewParam !== prevViewParam) {
    setPrevViewParam(viewParam)
    if (viewParam && ['ev', 'outliers', 'arb', 'middles', 'longshots', 'sgp', 'game_lines'].includes(viewParam)) {
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
    if (PRE_LAUNCH) {
      setRevealed(prev => new Set(prev).add(key))
      setRevealMsg('Pre-launch preview: Book unmasked! All books unlocked for early visitors.')
      setTimeout(() => setRevealMsg(null), 3500)
    } else {
      window.location.href = '/pricing'
    }
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [nextBoards, nbaG, nhlG] = await Promise.all([
        Promise.all(SPORT_KEYS.map(s => apiGetOddsBoard(s, { limit: 3000 }).catch(() => null))),
        apiGetGameProjections('nba').catch(() => null),
        apiGetGameProjections('nhl').catch(() => null),
      ])
      const out: Partial<Record<SportSlug, OddsBoard>> = {}
      let lk = 0
      nextBoards.forEach((b, i) => { if (b) { out[SPORT_KEYS[i]] = b; lk += b.locked ?? 0 } })
      if (Object.keys(out).length) {
        // identical data (same as_of for every sport): keep the old objects so nothing is recomputed or re-rendered
        setBoards(prev => SPORT_KEYS.every(k => prev[k]?.as_of === out[k]?.as_of) ? prev : out)
        setLocked(lk)
      }
      setGamesData({ nba: nbaG, nhl: nhlG })
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (mode === 'game_lines' && !gamesData.nba && !gamesData.nhl) {
      Promise.all([
        apiGetGameProjections('nba').catch(() => null),
        apiGetGameProjections('nhl').catch(() => null),
      ]).then(([nbaG, nhlG]) => {
        setGamesData({ nba: nbaG, nhl: nhlG })
      })
    }
  }, [mode, gamesData.nba, gamesData.nhl])

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

  // ---- SGP Proxies: multi-leg combinations beating direct markets or fair correlated joint value
  const sgpProxies = useMemo(() => {
    if (mode !== 'sgp') return []
    const all = computeSgpProxies(filtered, {
      refs,
      method,
      bettable,
      minEvPct: sgpMinEv,
      book: sgpBook === 'all' ? undefined : sgpBook,
      useCurve,
    })
    if (sgpArch === 'milestones') {
      return all.filter(r => r.archetype === 'nba_reb_ast_10_10' || r.archetype === 'nba_dd_proxy')
    }
    if (sgpArch === 'combos') {
      return all.filter(r => r.archetype.includes('_vs_'))
    }
    if (sgpArch === 'stacks') {
      return all.filter(r => r.archetype.includes('stack'))
    }
    return all
  }, [mode, filtered, refs, method, bettable, sgpMinEv, sgpBook, useCurve, sgpArch])

  const gameLinesData = useMemo(() => {
    const sports = gameSport === 'all' ? ['nba', 'nhl'] : [gameSport]
    const pricedGames: (GamePricedGame & { sport: string; filteredMarkets: GamePricedMarket[] })[] = []
    const allRows: GameProjectionRow[] = []
    const matchups: { key: string; label: string; sport: string }[] = []

    for (const s of ['nba', 'nhl']) {
      const feed = gamesData[s]
      if (!feed) continue
      if (feed.games) {
        for (const g of feed.games) {
          matchups.push({
            key: g.game_key,
            label: `${g.away} @ ${g.home} (${s.toUpperCase()})`,
            sport: s,
          })
        }
      }
    }

    for (const s of sports) {
      const feed = gamesData[s]
      if (!feed) continue

      if (feed.priced_games) {
        for (const g of feed.priced_games) {
          if (gameMatchup !== 'all' && g.game_key !== gameMatchup) continue
          if (q) {
            const ql = q.toLowerCase().trim()
            const match = `${g.away_team} ${g.home_team}`.toLowerCase().includes(ql)
            if (!match) continue
          }

          const filteredMarkets = (g.markets || []).filter(m => {
            if (gameMarket !== 'all') {
              const sys = (m.system || '').toLowerCase()
              if (gameMarket === 'spread' && !sys.includes('spread')) return false
              if (gameMarket === 'total' && !sys.includes('total')) return false
              if (gameMarket === 'ml' && !sys.includes('ml')) return false
            }
            if (gameSide !== 'all') {
              const sd = (m.side || '').toLowerCase()
              if (gameSide === 'home_over' && sd !== 'home' && sd !== 'over') return false
              if (gameSide === 'away_under' && sd !== 'away' && sd !== 'under') return false
            }
            if (gameMinEdge > 0 && (m.model_edge == null || m.model_edge < gameMinEdge)) return false
            return true
          })

          if (filteredMarkets.length > 0 || (gameMarket === 'all' && gameMinEdge === 0 && !q)) {
            pricedGames.push({ ...g, sport: s, filteredMarkets })
          }
        }
      }

      if (feed.rows) {
        for (const r of feed.rows) {
          if (gameMatchup !== 'all' && r.game_key !== gameMatchup) continue
          if (gameMarket !== 'all') {
            const m = (r.stat || r.system || '').toLowerCase()
            if (gameMarket === 'spread' && !m.includes('spread') && !m.includes('puck')) continue
            if (gameMarket === 'total' && !m.includes('total')) continue
            if (gameMarket === 'ml' && !m.includes('ml') && !m.includes('moneyline')) continue
          }
          if (gameSide !== 'all') {
            const sd = (r.side || '').toLowerCase()
            if (gameSide === 'home_over' && sd !== 'home' && sd !== 'over') continue
            if (gameSide === 'away_under' && sd !== 'away' && sd !== 'under') continue
          }
          if (gameMinEdge > 0 && (r.model_edge == null || r.model_edge < gameMinEdge)) continue
          if (q) {
            const ql = q.toLowerCase().trim()
            const match = (r.player || '').toLowerCase().includes(ql) ||
              (r.team || '').toLowerCase().includes(ql) ||
              (r.opp || '').toLowerCase().includes(ql) ||
              (r.stat_label || '').toLowerCase().includes(ql)
            if (!match) continue
          }
          allRows.push(r)
        }
      }
    }

    return { pricedGames, rows: allRows, matchupOptions: matchups }
  }, [gamesData, gameSport, gameMarket, gameMatchup, gameSide, gameMinEdge, q])

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
      outliers: computeOutliers(d.scoped, { refs: d.refs, method: d.method, bettable: d.bettable, minPeers: 3, minPremiumPct: 8, minEvPct: 0, side: d.side, useCurve: d.useCurve }).length,
      arb: buildArbRows(d.scoped, { bettable: d.bettable, includeExchanges: d.exchanges, minNetArbPct: 0 }).length,
      middles: computeMiddles(d.scoped, { bettable: d.bettable, includeExchanges: d.exchanges, maxCostPct: 5, minGap: 0, onlyPositiveEv: false, refs: d.refs, method: d.method, minRef: MIDDLE_MIN_REF, useCurve: d.useCurve }).length,
      long: buildLongshotRows(d.scoped, { refs: d.refs, method: d.method, bettable: d.bettable, minRef: LONGSHOT_MIN_REF, useCurve: d.useCurve, sub: 'all', minEv: 0, minOverAm: 0 })
        .filter(r => r.bestEv != null && r.bestEv >= floor * 100).length,
      sgp: computeSgpProxies(d.scoped, { refs: d.refs, method: d.method, bettable: d.bettable, minEvPct: 0, useCurve: d.useCurve }).length,
      gameLines: gameLinesData.rows.length,
    }
  }, [dBadgeIn, gameLinesData.rows.length])

  const nRows = mode === 'ev' ? screen.length
    : mode === 'outliers' ? outliers.length
    : mode === 'arb' ? arbs.length
    : mode === 'middles' ? middles.length
    : mode === 'longshots' ? longshots.length
    : mode === 'sgp' ? sgpProxies.length
    : gameLinesData.rows.length

  const meta = useMemo(() => {
    if (mode === 'game_lines') {
      const gList = Object.values(gamesData).filter((g): g is GameProjectionsFeed => Boolean(g))
      const ages = gList.map(g => g.age_minutes).filter((n): n is number => n != null)
      return {
        age: ages.length ? Math.max(...ages) : null,
        stale: gList.some(g => g.stale),
        sources: ['KALMAN_JOINT_T_V1', 'DISTRIBUTIONAL_MATRIX_V1'],
      }
    }
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
  }, [mode, gamesData, active, boards])

  // no board at all, or every board the API answered with is flagged unavailable (the banker has not written it yet)
  const unpublished = mode === 'game_lines'
    ? Object.values(gamesData).length > 0 && Object.values(gamesData).every(g => g?.status === 'unavailable')
    : (active.length === 0 || active.every(s => boards[s]!.status === 'unavailable'))

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
    ? { title: 'No SGP proxies clear these filters', body: 'SGP Proxies scans for multi-leg milestone and stat combinations that beat the book\'s direct pricing or fair correlated joint value. Try lowering the MIN EV floor or switching archetypes.' }
    : mode === 'game_lines'
    ? { title: 'No game lines clear these filters', body: 'Quantitative margin, total, and moneyline models price today\'s NBA and NHL games. Try clearing the market or matchup filters, or lowering the MIN EDGE threshold.' }
    : mode === 'middles'
    ? { title: 'No middles at this cost', body: 'A middle needs the over of a lower line and the under of a higher line, priced close enough that a miss costs little. Raise the max cost, loosen the window filter, or include exchanges.' }
    : mode === 'arb'
    ? { title: arbView === 'live' ? 'No live arbitrage on the board' : 'No near arbitrage on the board',
        body: 'A live arb needs the best over and the best under at two different books to pay more than the outlay together. Try Near Arbs, include exchanges, or widen the books you can bet at.' }
    : mode === 'longshots'
      ? { title: 'No longshots clear these filters', body: 'Set the strategy to All, lower the odds range or MIN EV, or turn on curve-fitted fair value. Alt rungs are quoted by few books, so many have no fair value without the curve.' }
      : { title: 'Nothing clears these filters', body: 'Set MIN EV to All to see every line, widen the devig books, or clear the prop and game filters. A +EV row needs at least three devig books pricing the line both ways.' }
  const refsNote = mode === 'longshots' ? 'Two books behind a fair value (the curve stands in below that).' : 'Needs 3 books behind a row.'

  return (
    <div style={{ maxWidth: '1680px', margin: '0 auto', padding: isMobile ? '12px 12px 64px' : '20px 24px 64px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px 20px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0, letterSpacing: '-0.01em' }}>Odds Terminal</h1>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="mono" style={{ fontSize: '12px', color: meta.stale ? '#facc15' : 'var(--fog)' }}>
            {nRows} ROWS &middot; {meta.age != null ? `LINES ${ageLabel(meta.age).toUpperCase()} OLD` : 'OFFSEASON / NO GAMES'}{meta.sources.length ? ` \u00b7 ${meta.sources.join(' + ').toUpperCase()}` : ''}
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

      {isMobile ? (
        <section style={{ ...SURFACE_CARD, padding: 0, marginBottom: '10px' }} aria-label="Filters">
          <HorizontalPillStrip padding="8px 10px" gap="6px">
            {MODES.map(m => {
              const n = m.key === 'ev' ? counts.ev
                : m.key === 'outliers' ? counts.outliers
                : m.key === 'arb' ? counts.arb
                : m.key === 'middles' ? counts.middles
                : m.key === 'longshots' ? counts.long
                : m.key === 'sgp' ? counts.sgp
                : counts.gameLines
              const hot = (m.key === 'arb' && n > 0) || (m.key === 'outliers' && n > 0) || (m.key === 'sgp' && n > 0) || (m.key === 'game_lines' && n > 0)
              return (
                <Pill key={m.key} active={mode === m.key} onClick={() => switchMode(m.key)}>
                  {m.label}
                  <span className="mono" style={{ marginLeft: '6px', fontSize: '10.5px', color: hot ? GREEN : 'var(--fog)' }}>{n}</span>
                </Pill>
              )
            })}
          </HorizontalPillStrip>

          <div style={{ padding: '8px 10px', borderTop: `1px solid ${HAIR}`, display: 'flex', gap: '8px', alignItems: 'center' }}>
            <HorizontalPillStrip gap="4px" padding="0" style={{ width: 'auto', flexShrink: 0 }}>
              {(['all', ...LIVE_SPORTS.map(p => p.slug)] as SportFilter[]).map(s => (
                <button
                  key={s}
                  type="button"
                  onClick={() => { setSport(s); setStat('all'); setGame('all'); setLimit(PAGE) }}
                  style={{
                    fontFamily: 'var(--font-mono), monospace',
                    fontSize: '11px',
                    fontWeight: 700,
                    padding: '5px 8px',
                    borderRadius: '4px',
                    border: `1px solid ${sport === s ? '#3a4156' : HAIR}`,
                    background: sport === s ? '#1b1f2b' : 'transparent',
                    color: sport === s ? '#ffffff' : 'var(--fog)',
                    cursor: 'pointer',
                  }}
                >
                  {s.toUpperCase()}
                </button>
              ))}
            </HorizontalPillStrip>

            <input
              aria-label="Search player or team"
              placeholder="Search player / team"
              value={q}
              onChange={e => { setQ(e.target.value); setLimit(PAGE) }}
              style={{
                background: '#090a0f',
                color: 'var(--chalk)',
                border: `1px solid ${HAIR}`,
                borderRadius: '6px',
                height: '32px',
                padding: '0 8px',
                fontSize: '11.5px',
                flex: 1,
                minWidth: '80px',
              }}
            />

            <button
              type="button"
              onClick={() => setMobileFiltersOpen(true)}
              className="btn btn-ghost"
              style={{
                padding: '5px 10px',
                fontSize: '11.5px',
                height: '32px',
                flexShrink: 0,
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                borderColor: activeFilterCount > 0 ? GREEN : HAIR,
                color: activeFilterCount > 0 ? GREEN : 'var(--chalk)',
              }}
            >
              <span>Filters</span>
              {activeFilterCount > 0 && (
                <span
                  className="mono"
                  style={{
                    fontSize: '10px',
                    background: 'rgba(87, 242, 135, 0.15)',
                    color: GREEN,
                    padding: '1px 5px',
                    borderRadius: '10px',
                    fontWeight: 700,
                  }}
                >
                  {activeFilterCount}
                </span>
              )}
            </button>
          </div>
        </section>
      ) : (
        <section style={{ ...SURFACE_CARD, padding: 0, marginBottom: '10px' }} aria-label="Filters">
          <div style={{ padding: '10px 14px', display: 'flex', gap: '10px 18px', flexWrap: 'wrap', alignItems: 'center' }}>
            <PillGroup label="Mode">
              {MODES.map(m => {
                const n = m.key === 'ev' ? counts.ev
                  : m.key === 'outliers' ? counts.outliers
                  : m.key === 'arb' ? counts.arb
                  : m.key === 'middles' ? counts.middles
                  : m.key === 'longshots' ? counts.long
                  : m.key === 'sgp' ? counts.sgp
                  : counts.gameLines
                const hot = (m.key === 'arb' && n > 0) || (m.key === 'outliers' && n > 0) || (m.key === 'sgp' && n > 0) || (m.key === 'game_lines' && n > 0)
                return (
                  <Pill key={m.key} active={mode === m.key} onClick={() => switchMode(m.key)}>
                    {m.label}
                    <span className="mono" style={{ marginLeft: '7px', fontSize: '10.5px', color: hot ? GREEN : 'var(--fog)' }}>{n}</span>
                  </Pill>
                )
              })}
            </PillGroup>
            {mode !== 'game_lines' ? (
              <>
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
              </>
            ) : null}
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
                  { label: 'None', keys: [] },
                ]}
                note="Columns shown and books you can bet at. Offshore books show their price but never count as the best price."
                align="right"
              />
              <BookPicker
                label={refs.length ? `REFERENCE BOOKS (${refs.length})` : 'REFERENCE BOOKS (ALL)'}
                books={bookList} selected={refs} onChange={setRefs} emptyMeansAll
                presets={[
                  { label: 'Sharp', keys: SHARP_PRESET.filter(k => bookList.some(b => b.key === k)) },
                  { label: 'All', keys: [] },
                ]}
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
            {mode === 'outliers' && (
              <>
                <Field label="MIN GAP">
                  <PillGroup>
                    {OUTLIER_GAP_OPTIONS.map(g => (
                      <Pill key={g.v} active={outlierGap === g.v} onClick={() => { setOutlierGap(g.v); setLimit(PAGE) }}>
                        {g.label}
                      </Pill>
                    ))}
                  </PillGroup>
                </Field>
                <Field label="REQUIRE +EV">
                  <PillGroup>
                    <Pill active={!outlierReqEv} onClick={() => { setOutlierReqEv(false); setLimit(PAGE) }}>All Outliers</Pill>
                    <Pill active={outlierReqEv} onClick={() => { setOutlierReqEv(true); setLimit(PAGE) }} title="Require verified positive EV against reference books or player curve">Verified +EV Only</Pill>
                  </PillGroup>
                </Field>
                <Field label="ODDS BAND">
                  <PillGroup>
                    {OUTLIER_ODDS_OPTIONS.map(b => (
                      <Pill key={b.v} active={outlierOddsBand === b.v} onClick={() => { setOutlierOddsBand(b.v); setLimit(PAGE) }}>
                        {b.label}
                      </Pill>
                    ))}
                  </PillGroup>
                </Field>
                <Field label="MIN BOOKS">
                  <PillGroup>
                    {OUTLIER_MIN_BOOKS.map(b => (
                      <Pill key={b.v} active={outlierMinBooks === b.v} onClick={() => { setOutlierMinBooks(b.v); setLimit(PAGE) }}>
                        {b.label}
                      </Pill>
                    ))}
                  </PillGroup>
                </Field>
                <Field label="VIEW">
                  <PillGroup>
                    <Pill active={outlierView === 'cards'} onClick={() => setOutlierView('cards')}>Visual Cards</Pill>
                    <Pill active={outlierView === 'table'} onClick={() => setOutlierView('table')}>Compact Table</Pill>
                  </PillGroup>
                </Field>
              </>
            )}
            {mode === 'arb' && (
              <>
                <Field label="SHOW">
                  <PillGroup>
                    <Pill active={arbView === 'live'} onClick={() => { setArbView('live'); setLimit(PAGE) }}>LIVE ARBS (&gt; 0%)</Pill>
                    <Pill active={arbView === 'near'} onClick={() => { setArbView('near'); setLimit(PAGE) }} title="Within 1.5% of break-even: bonus-bet conversion and near zero-hold line shopping">NEAR ARBS (&gt; -1.5%)</Pill>
                  </PillGroup>
                </Field>
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
            {mode === 'middles' && (
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
                <Field label="EXCHANGES">
                  <PillGroup>
                    <Pill active={exchanges} onClick={() => setExchanges(v => !v)} title="Kalshi, Novig and ProphetX quotes. Kalshi's taker fee is taken off its leg; Novig charges none before the game.">INCLUDE KALSHI / NOVIG (FEE-ADJUSTED)</Pill>
                  </PillGroup>
                </Field>
                <Field label="OUTLAY" htmlFor="odds-outlay-mid">
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                    <span className="mono" style={{ color: 'var(--fog)', fontSize: '12px' }}>$</span>
                    <input
                      id="odds-outlay-mid" inputMode="decimal" value={outlayText} onChange={e => setOutlayText(e.target.value.replace(/[^0-9.]/g, ''))}
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
            {mode === 'sgp' && (
              <>
                <Field label="ARCHETYPE">
                  <PillGroup>
                    {SGP_ARCHETYPE_OPTIONS.map(a => (
                      <Pill key={a.v} active={sgpArch === a.v} onClick={() => { setSgpArch(a.v); setLimit(PAGE) }}>
                        {a.label}
                      </Pill>
                    ))}
                  </PillGroup>
                </Field>
                <Field label="MIN EV">
                  <PillGroup>
                    {SGP_MIN_EV_OPTIONS.map(e => (
                      <Pill key={e.v} active={sgpMinEv === e.v} onClick={() => { setSgpMinEv(e.v); setLimit(PAGE) }}>
                        {e.label}
                      </Pill>
                    ))}
                  </PillGroup>
                </Field>
                <Field label="BOOK" htmlFor="sgp-book">
                  <Select
                    id="sgp-book"
                    value={sgpBook}
                    onChange={v => { setSgpBook(v); setLimit(PAGE) }}
                    options={[
                      { v: 'all', l: 'All Books' },
                      ...columns.filter(c => !c.offshore).map(c => ({ v: c.key, l: bookDisplay(c.key).label })),
                    ]}
                  />
                </Field>
                <Field label="FAIR SOURCE">
                  <PillGroup>
                    <Pill active={useCurve} onClick={() => setUseCurve(true)} title="Uses multi-book de-vigged fair probabilities with monotone curve fallback">Multi-Book + Curve</Pill>
                    <Pill active={!useCurve} onClick={() => setUseCurve(false)} title="Requires at least 2 reference books quoting each leg">Exact Books Only</Pill>
                  </PillGroup>
                </Field>
              </>
            )}
            {mode === 'game_lines' && (
              <>
                <Field label="MARKET">
                  <PillGroup>
                    <Pill active={gameMarket === 'all'} onClick={() => { setGameMarket('all'); setLimit(PAGE) }}>All Lines</Pill>
                    <Pill active={gameMarket === 'spread'} onClick={() => { setGameMarket('spread'); setLimit(PAGE) }}>Spread / Puck Line</Pill>
                    <Pill active={gameMarket === 'total'} onClick={() => { setGameMarket('total'); setLimit(PAGE) }}>Total</Pill>
                    <Pill active={gameMarket === 'ml'} onClick={() => { setGameMarket('ml'); setLimit(PAGE) }}>Moneyline</Pill>
                  </PillGroup>
                </Field>
                <Field label="GAME / MATCHUP" htmlFor="game-matchup-select">
                  <Select
                    id="game-matchup-select"
                    value={gameMatchup}
                    onChange={v => { setGameMatchup(v); setLimit(PAGE) }}
                    options={[
                      { v: 'all', l: `All Matchups (${gameLinesData.matchupOptions.length})` },
                      ...gameLinesData.matchupOptions.map(m => ({ v: m.key, l: m.label })),
                    ]}
                  />
                </Field>
                <Field label="SPORT">
                  <PillGroup>
                    <Pill active={gameSport === 'all'} onClick={() => { setGameSport('all'); setLimit(PAGE) }}>All</Pill>
                    <Pill active={gameSport === 'nba'} onClick={() => { setGameSport('nba'); setLimit(PAGE) }}>NBA</Pill>
                    <Pill active={gameSport === 'nhl'} onClick={() => { setGameSport('nhl'); setLimit(PAGE) }}>NHL</Pill>
                  </PillGroup>
                </Field>
                <Field label="SIDE">
                  <PillGroup>
                    <Pill active={gameSide === 'all'} onClick={() => { setGameSide('all'); setLimit(PAGE) }}>Both</Pill>
                    <Pill active={gameSide === 'home_over'} onClick={() => { setGameSide('home_over'); setLimit(PAGE) }}>Home / Over</Pill>
                    <Pill active={gameSide === 'away_under'} onClick={() => { setGameSide('away_under'); setLimit(PAGE) }}>Away / Under</Pill>
                  </PillGroup>
                </Field>
                <Field label="MIN EDGE">
                  <PillGroup>
                    {[
                      { label: 'All', v: 0 },
                      { label: '1%+', v: 0.01 },
                      { label: '2%+', v: 0.02 },
                      { label: '3%+', v: 0.03 },
                      { label: '5%+', v: 0.05 },
                    ].map(m => (
                      <Pill key={m.v} active={gameMinEdge === m.v} onClick={() => { setGameMinEdge(m.v); setLimit(PAGE) }}>{m.label}</Pill>
                    ))}
                  </PillGroup>
                </Field>
                <Field label="VIEW">
                  <PillGroup>
                    <Pill active={gameView === 'cards'} onClick={() => setGameView('cards')}>Game Cards</Pill>
                    <Pill active={gameView === 'table'} onClick={() => setGameView('table')}>Data Matrix</Pill>
                  </PillGroup>
                </Field>
              </>
            )}
          </div>
        </section>
      )}

      {/* Mobile Filter Sheet */}
      <MobileBottomSheet
        open={mobileFiltersOpen}
        onClose={() => setMobileFiltersOpen(false)}
        title="Terminal Filters"
        subtitle={`${MODES.find(m => m.key === mode)?.label} \u00b7 ${nRows} matching lines`}
        footer={
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              type="button"
              onClick={() => {
                setStat('all')
                setGame('all')
                setQ('')
                setMinEv(0)
                setLines('all')
                setSide('both')
                setSort('ev')
                setRefs([])
                setOutlierGap(8)
                setOutlierReqEv(false)
                setOutlierOddsBand('all')
                setOutlierMinBooks(3)
                setArbView('live')
                setExchanges(true)
                setOutlayText('100')
                setMidCost(5)
                setMidGap(0)
                setMidPosEv(false)
                setLongSub('all')
                setOddsMin(0)
                setUseCurve(true)
                setSgpArch('all')
                setSgpMinEv(0)
                setSgpBook('all')
              }}
              className="btn btn-ghost"
              style={{ flex: 1, padding: '9px 0', fontSize: '12px' }}
            >
              Reset
            </button>
            <button
              type="button"
              onClick={() => setMobileFiltersOpen(false)}
              className="btn btn-primary"
              style={{ flex: 2, padding: '9px 0', fontSize: '12px' }}
            >
              Show {nRows} Results
            </button>
          </div>
        }
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {mode === 'ev' && (
            <>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>LINES</div>
                <PillGroup>
                  <Pill active={lines === 'main'} onClick={() => setLines('main')}>Main Lines</Pill>
                  <Pill active={lines === 'all'} onClick={() => setLines('all')}>Main + Alt Ladders</Pill>
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>O/U</div>
                <PillGroup>
                  {(['both', 'over', 'under'] as SideFilter[]).map(s => <Pill key={s} active={side === s} onClick={() => setSide(s)}>{s.toUpperCase()}</Pill>)}
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>MIN EV</div>
                <PillGroup>
                  {MIN_EV.map(m => <Pill key={m.v} active={minEv === m.v} onClick={() => setMinEv(m.v)}>{m.label}</Pill>)}
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>SORT</div>
                <Select id="mobile-ev-sort" value={sort} onChange={v => setSort(v as SortKey)} options={[{ v: 'ev', l: 'Best EV %' }, { v: 'time', l: 'Game time' }, { v: 'player', l: 'Player' }]} />
              </div>
            </>
          )}

          {mode === 'outliers' && (
            <>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>MIN GAP</div>
                <PillGroup>
                  {OUTLIER_GAP_OPTIONS.map(g => (
                    <Pill key={g.v} active={outlierGap === g.v} onClick={() => { setOutlierGap(g.v); setLimit(PAGE) }}>{g.label}</Pill>
                  ))}
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>REQUIRE +EV</div>
                <PillGroup>
                  <Pill active={!outlierReqEv} onClick={() => { setOutlierReqEv(false); setLimit(PAGE) }}>All Outliers</Pill>
                  <Pill active={outlierReqEv} onClick={() => { setOutlierReqEv(true); setLimit(PAGE) }}>Verified +EV Only</Pill>
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>ODDS BAND</div>
                <PillGroup>
                  {OUTLIER_ODDS_OPTIONS.map(b => (
                    <Pill key={b.v} active={outlierOddsBand === b.v} onClick={() => { setOutlierOddsBand(b.v); setLimit(PAGE) }}>{b.label}</Pill>
                  ))}
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>MIN BOOKS</div>
                <PillGroup>
                  {OUTLIER_MIN_BOOKS.map(b => (
                    <Pill key={b.v} active={outlierMinBooks === b.v} onClick={() => { setOutlierMinBooks(b.v); setLimit(PAGE) }}>{b.label}</Pill>
                  ))}
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>VIEW</div>
                <PillGroup>
                  <Pill active={outlierView === 'cards'} onClick={() => setOutlierView('cards')}>Visual Cards</Pill>
                  <Pill active={outlierView === 'table'} onClick={() => setOutlierView('table')}>Compact Table</Pill>
                </PillGroup>
              </div>
            </>
          )}

          {mode === 'arb' && (
            <>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>SHOW</div>
                <PillGroup>
                  <Pill active={arbView === 'live'} onClick={() => { setArbView('live'); setLimit(PAGE) }}>LIVE ARBS (&gt; 0%)</Pill>
                  <Pill active={arbView === 'near'} onClick={() => { setArbView('near'); setLimit(PAGE) }}>NEAR ARBS (&gt; -1.5%)</Pill>
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>EXCHANGES</div>
                <PillGroup>
                  <Pill active={exchanges} onClick={() => setExchanges(v => !v)}>INCLUDE KALSHI / NOVIG</Pill>
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>OUTLAY ($)</div>
                <input
                  inputMode="decimal"
                  value={outlayText}
                  onChange={e => setOutlayText(e.target.value.replace(/[^0-9.]/g, ''))}
                  style={{ background: '#090a0f', color: 'var(--chalk)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 10px', fontSize: '12px', width: '120px', fontFamily: 'var(--font-mono), monospace' }}
                />
              </div>
            </>
          )}

          {mode === 'middles' && (
            <>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>MAX COST</div>
                <PillGroup>
                  {MID_COST.map(c => <Pill key={c} active={midCost === c} onClick={() => { setMidCost(c); setLimit(PAGE) }}>{`${c}%`}</Pill>)}
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>VALUE</div>
                <PillGroup>
                  <Pill active={midPosEv} onClick={() => { setMidPosEv(v => !v); setLimit(PAGE) }}>+EV ONLY</Pill>
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>WINDOW</div>
                <PillGroup>
                  {MID_GAP.map(g => <Pill key={g.v} active={midGap === g.v} onClick={() => { setMidGap(g.v); setLimit(PAGE) }}>{g.label}</Pill>)}
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>EXCHANGES</div>
                <PillGroup>
                  <Pill active={exchanges} onClick={() => setExchanges(v => !v)}>INCLUDE KALSHI / NOVIG</Pill>
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>OUTLAY ($)</div>
                <input
                  inputMode="decimal"
                  value={outlayText}
                  onChange={e => setOutlayText(e.target.value.replace(/[^0-9.]/g, ''))}
                  style={{ background: '#090a0f', color: 'var(--chalk)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 10px', fontSize: '12px', width: '120px', fontFamily: 'var(--font-mono), monospace' }}
                />
              </div>
            </>
          )}

          {mode === 'longshots' && (
            <>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>STRATEGY</div>
                <PillGroup>
                  <Pill active={longSub === 'all'} onClick={() => { setLongSub('all'); setLimit(PAGE) }}>All</Pill>
                  <Pill active={longSub === 'plus_ev_over'} onClick={() => { setLongSub('plus_ev_over'); setLimit(PAGE) }}>Buy Over</Pill>
                  <Pill active={longSub === 'fade_under'} onClick={() => { setLongSub('fade_under'); setLimit(PAGE) }}>Fade Under</Pill>
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>MIN PAYOUT</div>
                <PillGroup>
                  {ODDS_MIN.map(m => <Pill key={m.v} active={oddsMin === m.v} onClick={() => { setOddsMin(m.v); setLimit(PAGE) }}>{m.label}</Pill>)}
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>MIN EV</div>
                <PillGroup>
                  {MIN_EV.map(m => <Pill key={m.v} active={minEv === m.v} onClick={() => setMinEv(m.v)}>{m.label}</Pill>)}
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>FAIR SOURCE</div>
                <PillGroup>
                  <Pill active={useCurve} onClick={() => setUseCurve(true)}>Multi-Book + Curve</Pill>
                  <Pill active={!useCurve} onClick={() => setUseCurve(false)}>Exact Books Only</Pill>
                </PillGroup>
              </div>
            </>
          )}

          {mode === 'sgp' && (
            <>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>ARCHETYPE</div>
                <PillGroup>
                  {SGP_ARCHETYPE_OPTIONS.map(a => (
                    <Pill key={a.v} active={sgpArch === a.v} onClick={() => { setSgpArch(a.v); setLimit(PAGE) }}>{a.label}</Pill>
                  ))}
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>MIN EV</div>
                <PillGroup>
                  {SGP_MIN_EV_OPTIONS.map(e => (
                    <Pill key={e.v} active={sgpMinEv === e.v} onClick={() => { setSgpMinEv(e.v); setLimit(PAGE) }}>{e.label}</Pill>
                  ))}
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>BOOK</div>
                <Select
                  id="mobile-sgp-book"
                  value={sgpBook}
                  onChange={v => { setSgpBook(v); setLimit(PAGE) }}
                  options={[
                    { v: 'all', l: 'All Books' },
                    ...columns.filter(c => !c.offshore).map(c => ({ v: c.key, l: bookDisplay(c.key).label })),
                  ]}
                />
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>FAIR SOURCE</div>
                <PillGroup>
                  <Pill active={useCurve} onClick={() => setUseCurve(true)}>Multi-Book + Curve</Pill>
                  <Pill active={!useCurve} onClick={() => setUseCurve(false)}>Exact Books Only</Pill>
                </PillGroup>
              </div>
            </>
          )}

          {mode === 'game_lines' && (
            <>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>MARKET</div>
                <PillGroup>
                  <Pill active={gameMarket === 'all'} onClick={() => { setGameMarket('all'); setLimit(PAGE) }}>All Lines</Pill>
                  <Pill active={gameMarket === 'spread'} onClick={() => { setGameMarket('spread'); setLimit(PAGE) }}>Spread</Pill>
                  <Pill active={gameMarket === 'total'} onClick={() => { setGameMarket('total'); setLimit(PAGE) }}>Total</Pill>
                  <Pill active={gameMarket === 'ml'} onClick={() => { setGameMarket('ml'); setLimit(PAGE) }}>ML</Pill>
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>GAME / MATCHUP</div>
                <select
                  id="mobile-game-matchup-select"
                  value={gameMatchup}
                  onChange={e => { setGameMatchup(e.target.value); setLimit(PAGE) }}
                  style={{
                    width: '100%',
                    background: '#090a0f',
                    color: 'var(--chalk)',
                    border: `1px solid ${HAIR}`,
                    borderRadius: '8px',
                    minHeight: '40px',
                    padding: '0 12px',
                    fontSize: '13px',
                    cursor: 'pointer',
                  }}
                >
                  <option value="all">All Matchups ({gameLinesData.matchupOptions.length})</option>
                  {gameLinesData.matchupOptions.map(m => (
                    <option key={m.key} value={m.key}>{m.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>SPORT</div>
                <PillGroup>
                  <Pill active={gameSport === 'all'} onClick={() => { setGameSport('all'); setLimit(PAGE) }}>All</Pill>
                  <Pill active={gameSport === 'nba'} onClick={() => { setGameSport('nba'); setLimit(PAGE) }}>NBA</Pill>
                  <Pill active={gameSport === 'nhl'} onClick={() => { setGameSport('nhl'); setLimit(PAGE) }}>NHL</Pill>
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>SIDE</div>
                <PillGroup>
                  <Pill active={gameSide === 'all'} onClick={() => { setGameSide('all'); setLimit(PAGE) }}>Both</Pill>
                  <Pill active={gameSide === 'home_over'} onClick={() => { setGameSide('home_over'); setLimit(PAGE) }}>Home / Over</Pill>
                  <Pill active={gameSide === 'away_under'} onClick={() => { setGameSide('away_under'); setLimit(PAGE) }}>Away / Under</Pill>
                </PillGroup>
              </div>
              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>MIN EDGE</div>
                <PillGroup>
                  {[
                    { label: 'All', v: 0 },
                    { label: '1%+', v: 0.01 },
                    { label: '2%+', v: 0.02 },
                    { label: '3%+', v: 0.03 },
                    { label: '5%+', v: 0.05 },
                  ].map(m => (
                    <Pill key={m.v} active={gameMinEdge === m.v} onClick={() => { setGameMinEdge(m.v); setLimit(PAGE) }}>{m.label}</Pill>
                  ))}
                </PillGroup>
              </div>
            </>
          )}

          {mode !== 'game_lines' && (
            <>
              <div style={{ borderTop: `1px solid ${HAIR}`, paddingTop: '12px' }}>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>PROP MARKET</div>
                <select
                  id="mobile-odds-stat"
                  value={stat}
                  onChange={e => { setStat(e.target.value); setLimit(PAGE) }}
                  style={{
                    width: '100%',
                    background: '#090a0f',
                    color: 'var(--chalk)',
                    border: `1px solid ${HAIR}`,
                    borderRadius: '8px',
                    minHeight: '40px',
                    padding: '0 12px',
                    fontSize: '13px',
                    cursor: 'pointer',
                  }}
                >
                  <option value="all">All props</option>
                  {statOptions.map(s => <option key={s} value={s}>{STAT_LABEL[s] ?? s}</option>)}
                </select>
              </div>

              <div>
                <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '6px' }}>GAME</div>
                <select
                  id="mobile-odds-game"
                  value={game}
                  onChange={e => { setGame(e.target.value); setLimit(PAGE) }}
                  style={{
                    width: '100%',
                    background: '#090a0f',
                    color: 'var(--chalk)',
                    border: `1px solid ${HAIR}`,
                    borderRadius: '8px',
                    minHeight: '40px',
                    padding: '0 12px',
                    fontSize: '13px',
                    cursor: 'pointer',
                  }}
                >
                  <option value="all">All games</option>
                  {games.map(g => <option key={g.key} value={g.key}>{`${g.label} ${startLabel(g.start)}`}</option>)}
                </select>
              </div>
            </>
          )}

          <MobileBookChecklist
            title="SPORTSBOOKS"
            subtitle="Columns shown and books you can bet at. Offshore books show price but never count as best."
            books={bookList}
            selected={[...shownSet]}
            onChange={next => setShown(next)}
            presets={[
              { label: 'Defaults', keys: defaultShown },
              ...(myBooks.enabled ? [{ label: 'My books', keys: myBooks.books }] : []),
              { label: 'All', keys: bookList.map(b => b.key) },
              { label: 'None', keys: [] },
            ]}
          />

          <MobileBookChecklist
            title="REFERENCE BOOKS"
            subtitle={`Fair value is the equal-weight average of these books' de-vigged lines. None selected uses every book. ${refsNote}`}
            books={bookList}
            selected={refs}
            onChange={setRefs}
            emptyMeansAll
            presets={[
              { label: 'Sharp', keys: SHARP_PRESET.filter(k => bookList.some(b => b.key === k)) },
              { label: 'All', keys: [] },
            ]}
            extraHeader={
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '4px' }}>
                <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>DEVIG METHOD</span>
                <PillGroup>
                  <Pill active={method === 'proportional'} onClick={() => setMethod('proportional')}>PROPORTIONAL</Pill>
                  <Pill active={method === 'power'} onClick={() => setMethod('power')}>POWER</Pill>
                </PillGroup>
              </div>
            }
          />
        </div>
      </MobileBottomSheet>

      {unpublished ? (
        <Empty title="Odds board not published yet" body="The price feed behind this screen is not available right now. It is rewritten every 30 minutes while games are on the board; check back shortly." />
      ) : (allRows.length === 0 && mode !== 'game_lines') ? (
        <Empty title="No prop prices posted" body={sport === 'mlb' ? 'MLB is in the offseason (resumes in March). Switch to NFL, NBA, or NHL for active boards.' : 'No book has posted player props for the games on the board yet.'} />
      ) : nRows === 0 ? (
        <Empty title={emptyBody.title} body={emptyBody.body} />
      ) : isMobile && mobileView === 'cards' ? (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
            <div style={{ display: 'inline-flex', padding: '2px', background: '#090a0f', border: `1px solid ${HAIR}`, borderRadius: '6px' }}>
              <button
                type="button"
                onClick={() => setMobileView('cards')}
                style={{
                  fontFamily: 'var(--font-text), sans-serif',
                  fontSize: '11px',
                  fontWeight: 600,
                  padding: '4px 10px',
                  borderRadius: '4px',
                  border: 0,
                  cursor: 'pointer',
                  background: '#1b1f2b',
                  color: '#ffffff',
                }}
              >
                Cards ({nRows})
              </button>
              <button
                type="button"
                onClick={() => setMobileView('table')}
                style={{
                  fontFamily: 'var(--font-text), sans-serif',
                  fontSize: '11px',
                  fontWeight: 600,
                  padding: '4px 10px',
                  borderRadius: '4px',
                  border: 0,
                  cursor: 'pointer',
                  background: 'transparent',
                  color: 'var(--fog)',
                }}
              >
                Spreadsheet
              </button>
            </div>
            <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>
              {meta.age != null ? ageLabel(meta.age) : ''}
            </span>
          </div>

          {mode === 'outliers' ? (
            <div>
              {revealMsg && (
                <div role="status" style={{ ...SURFACE_CARD, padding: '10px 14px', marginBottom: '12px', borderColor: 'rgba(87,242,135,0.4)', color: GREEN, fontSize: '12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span>{revealMsg}</span>
                  <button onClick={() => setRevealMsg(null)} className="btn btn-ghost" style={{ padding: '2px 8px', fontSize: '11px' }}>Dismiss</button>
                </div>
              )}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '12px' }}>
                {outliers.slice(0, limit).map((item, idx) => (
                  <OutlierCard
                    key={item.key}
                    item={item}
                    isLocked={!revealed.has(item.key) && idx >= 3 && !PRE_LAUNCH}
                    onReveal={() => handleReveal(item.key)}
                  />
                ))}
              </div>
            </div>
          ) : mode === 'ev' ? (
            <div>
              {screen.slice(0, limit).map(r => (
                <MobileEvRowCard
                  key={r.key}
                  r={r}
                  bankroll={bankroll}
                  onInspect={() => setInspectItem({ type: 'ev', row: r })}
                />
              ))}
            </div>
          ) : mode === 'arb' ? (
            <div>
              {arbs.slice(0, limit).map(r => (
                <MobileArbCard
                  key={r.key}
                  r={r}
                  outlay={outlay}
                  copied={copied === r.key}
                  onCopy={copyHedge}
                  onInspect={() => setInspectItem({ type: 'arb', row: r })}
                />
              ))}
            </div>
          ) : mode === 'middles' ? (
            <div>
              {middles.slice(0, limit).map(r => (
                <MobileMiddleCard
                  key={r.key}
                  r={r}
                  outlay={outlay}
                  copied={copied === r.key}
                  onCopy={copyMiddle}
                  onInspect={() => setInspectItem({ type: 'middles', row: r })}
                />
              ))}
            </div>
          ) : mode === 'longshots' ? (
            <div>
              {longshots.slice(0, limit).map(r => (
                <MobileLongshotCard
                  key={r.key}
                  r={r}
                  onInspect={() => setInspectItem({ type: 'longshots', row: r })}
                />
              ))}
            </div>
          ) : mode === 'game_lines' ? (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '14px' }}>
              {gameLinesData.pricedGames.slice(0, limit).map((g, idx) => (
                <GameCard key={`${g.sport}-${g.game_key}-${idx}`} game={g} />
              ))}
            </div>
          ) : (
            <div>
              {sgpProxies.slice(0, limit).map(r => (
                <MobileSgpCard key={r.key} r={r} />
              ))}
            </div>
          )}
        </div>
      ) : mode === 'outliers' && outlierView === 'cards' ? (
        <div>
          {isMobile && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
              <div style={{ display: 'inline-flex', padding: '2px', background: '#090a0f', border: `1px solid ${HAIR}`, borderRadius: '6px' }}>
                <button
                  type="button"
                  onClick={() => setMobileView('cards')}
                  style={{
                    fontFamily: 'var(--font-text), sans-serif',
                    fontSize: '11px',
                    fontWeight: 600,
                    padding: '4px 10px',
                    borderRadius: '4px',
                    border: 0,
                    cursor: 'pointer',
                    background: 'transparent',
                    color: 'var(--fog)',
                  }}
                >
                  Cards ({nRows})
                </button>
                <button
                  type="button"
                  onClick={() => setMobileView('table')}
                  style={{
                    fontFamily: 'var(--font-text), sans-serif',
                    fontSize: '11px',
                    fontWeight: 600,
                    padding: '4px 10px',
                    borderRadius: '4px',
                    border: 0,
                    cursor: 'pointer',
                    background: '#1b1f2b',
                    color: '#ffffff',
                  }}
                >
                  Spreadsheet
                </button>
              </div>
            </div>
          )}
          {revealMsg && (
            <div role="status" style={{ ...SURFACE_CARD, padding: '10px 14px', marginBottom: '12px', borderColor: 'rgba(87,242,135,0.4)', color: GREEN, fontSize: '12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span>{revealMsg}</span>
              <button onClick={() => setRevealMsg(null)} className="btn btn-ghost" style={{ padding: '2px 8px', fontSize: '11px' }}>Dismiss</button>
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fill, minmax(420px, 1fr))', gap: '14px' }}>
            {outliers.slice(0, limit).map((item, idx) => (
              <OutlierCard
                key={item.key}
                item={item}
                isLocked={!revealed.has(item.key) && idx >= 3 && !PRE_LAUNCH}
                onReveal={() => handleReveal(item.key)}
              />
            ))}
          </div>
        </div>
      ) : mode === 'sgp' ? (
        <div>
          {isMobile && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
              <div style={{ display: 'inline-flex', padding: '2px', background: '#090a0f', border: `1px solid ${HAIR}`, borderRadius: '6px' }}>
                <button
                  type="button"
                  onClick={() => setMobileView('cards')}
                  style={{
                    fontFamily: 'var(--font-text), sans-serif',
                    fontSize: '11px',
                    fontWeight: 600,
                    padding: '4px 10px',
                    borderRadius: '4px',
                    border: 0,
                    cursor: 'pointer',
                    background: 'transparent',
                    color: 'var(--fog)',
                  }}
                >
                  Cards ({nRows})
                </button>
                <button
                  type="button"
                  onClick={() => setMobileView('table')}
                  style={{
                    fontFamily: 'var(--font-text), sans-serif',
                    fontSize: '11px',
                    fontWeight: 600,
                    padding: '4px 10px',
                    borderRadius: '4px',
                    border: 0,
                    cursor: 'pointer',
                    background: '#1b1f2b',
                    color: '#ffffff',
                  }}
                >
                  Spreadsheet
                </button>
              </div>
            </div>
          )}
          <div className="odds-table-wrap" style={{ ...SURFACE_CARD, padding: 0 }}>
            <table className="odds-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${HAIR}`, background: '#0d0f15' }}>
                  <th scope="col" style={{ textAlign: 'left', minWidth: '220px', padding: '10px 14px' }}>PLAYER &amp; MATCHUP</th>
                  <th scope="col" style={{ textAlign: 'left', minWidth: '280px', padding: '10px 14px' }}>SGP LEGS (SAME BOOK)</th>
                  <th scope="col" style={{ textAlign: 'left', minWidth: '150px', padding: '10px 14px' }}>TRUE CORRELATED FAIR</th>
                  <th scope="col" style={{ textAlign: 'left', minWidth: '220px', padding: '10px 14px' }}>EST. SGP PRICE vs DIRECT</th>
                  <th scope="col" style={{ textAlign: 'right', minWidth: '170px', padding: '10px 14px' }}>EDGE &amp; ACTION</th>
                </tr>
              </thead>
              <tbody>
                {sgpProxies.slice(0, limit).map(r => (
                  <SgpTableRow key={r.key} r={r} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : mode === 'game_lines' ? (
        <GameLinesView
          pricedGames={gameLinesData.pricedGames}
          rows={gameLinesData.rows}
          view={gameView}
          limit={limit}
          onSwitchView={setGameView}
          isMobile={isMobile}
        />
      ) : (
        <div>
          {isMobile && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
              <div style={{ display: 'inline-flex', padding: '2px', background: '#090a0f', border: `1px solid ${HAIR}`, borderRadius: '6px' }}>
                <button
                  type="button"
                  onClick={() => setMobileView('cards')}
                  style={{
                    fontFamily: 'var(--font-text), sans-serif',
                    fontSize: '11px',
                    fontWeight: 600,
                    padding: '4px 10px',
                    borderRadius: '4px',
                    border: 0,
                    cursor: 'pointer',
                    background: 'transparent',
                    color: 'var(--fog)',
                  }}
                >
                  Cards ({nRows})
                </button>
                <button
                  type="button"
                  onClick={() => setMobileView('table')}
                  style={{
                    fontFamily: 'var(--font-text), sans-serif',
                    fontSize: '11px',
                    fontWeight: 600,
                    padding: '4px 10px',
                    borderRadius: '4px',
                    border: 0,
                    cursor: 'pointer',
                    background: '#1b1f2b',
                    color: '#ffffff',
                  }}
                >
                  Spreadsheet
                </button>
              </div>
            </div>
          )}
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
                {mode === 'outliers' && (
                  <>
                    <th scope="col" className="odds-ev" title="Average price across all peer books quoting this side">MARKET AVG</th>
                    <th scope="col" className="odds-ev" title="The single book hanging a number off the market">OUTLIER BOOK</th>
                    <th scope="col" style={{ textAlign: 'right' }} title="Payout premium above peer average, and de-vigged EV">DISLOCATION</th>
                  </>
                )}
                {mode === 'middles' && (
                  <>
                    <th scope="col" className="odds-ev" title="Width of the window between the two lines, and what a miss and a hit pay">MIDDLE</th>
                    <th scope="col" className="odds-ev">LEGS</th>
                    <th scope="col" className="odds-ev">STAKE SPLIT ({usd(outlay).replace('.00', '')})</th>
                  </>
                )}
                {mode === 'arb' && (
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
                      <span className="mono odds-sub">{isRef && refWeight != null && mode !== 'arb' && mode !== 'middles' ? `${refWeight}%` : c.offshore ? 'ref' : '\u00a0'}</span>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {mode === 'ev' && screen.slice(0, limit).map(r => <EvRow key={r.key} r={r} columns={columns} bankroll={bankroll} />)}
              {mode === 'outliers' && outliers.slice(0, limit).map((r, idx) => (
                <OutlierTableRow
                  key={r.key}
                  r={r}
                  columns={columns}
                  isLocked={!revealed.has(r.key) && idx >= 3 && !PRE_LAUNCH}
                  onReveal={() => handleReveal(r.key)}
                />
              ))}
              {mode === 'middles' && middles.slice(0, limit).map(r => <MiddleTableRow key={r.key} r={r} columns={columns} outlay={outlay} copied={copied === r.key} onCopy={copyMiddle} />)}
              {mode === 'arb' && arbs.slice(0, limit).map(r => <ArbTableRow key={r.key} r={r} columns={columns} outlay={outlay} copied={copied === r.key} onCopy={copyHedge} />)}
              {mode === 'longshots' && longshots.slice(0, limit).map(r => <LongshotTableRow key={r.key} r={r} columns={columns} />)}
            </tbody>
          </table>
        </div>
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
        {mode === 'outliers' && <>Market Outliers visualizes single-book price dislocations where one book hangs odds far above the rest of the market.
          MARKET AVG is the leave-self-out consensus across peer books. The green bar shows the outlier book&apos;s payout relative to peer books.
          Verified +EV requires at least two reference books or the player&apos;s multi-line curve confirming positive expected value.</>}
        {mode === 'middles' && <>A middle is the over of a lower line plus the under of a higher line: if the result lands strictly between them both bets win,
          anywhere else one wins and one loses, so it is a small known cost for a chance at a double win, not a risk-free bet. Stakes equalise the payout when only one leg wins.
          BREAK-EVEN is the chance the window must hit for the middle to pay for itself; P(WINDOW) is the reference books&apos; own estimate from the two lines (or the player&apos;s
          curve), so it is only as good as those prices. Each cell shows the over of the lower line above the under of the higher line; green outlines mark the two legs.
          Kalshi&apos;s taker fee is taken off a Kalshi leg. Confirm both lines before placing either, and mind each book&apos;s limits and grading rules.</>}
        {mode === 'arb' && <>Each cell is a book&apos;s over price above its under price; the green outline marks the two legs of the best hedge. Stakes lock the same payout on
          either outcome. Kalshi&apos;s taker fee (0.07 &times; p &times; (1 &minus; p) per contract) is taken off its leg; other exchange commissions are not modelled.
          Prop arbs are often a stale quote or a mismatched line: confirm both legs before placing either, and mind each book&apos;s limits and grading rules.</>}
        {mode === 'longshots' && <>Each cell is a book&apos;s over price above its under price. Alt lines are rungs off the player&apos;s main line. FAIR ANCHOR is the reference
          books&apos; consensus (N BKS) or, where fewer than two price the exact line, a fair probability read off the player&apos;s own multi-line curve (CURVE). The curve is an
          unvalidated estimate: treat a CURVE edge as a place to look, not a price to trust. Snipe is the longshot over against fair; Fade is the under or NO against fair.</>}
        {mode === 'sgp' && <>SGP Proxies identifies multi-leg same-game milestone and stat combinations whose synthetic SGP pricing beats official standalone markets or fair joint probability using Gaussian copula correlation. Est. SGP Odds models the target book&apos;s internal SGP builder; True Fair estimates the correlated outcome under empirical stat \u03c1.</>}
        {mode === 'game_lines' && <>Game Lines prices full-game spreads, totals, and moneylines using institutional quantitative distribution models (NBA Student-t joint margin-total dispersion, NHL Dixon-Coles bivariate Poisson with regulation empty-net and 3v3 OT adjustments). Model win probability integrates pace, defensive four factors, rest fatigue, and active injury drags. Fair market probability is de-vigged multi-book consensus. Edge is model probability minus fair market probability.</>}
        {' '}These are unvalidated market signals, not part of the public track record. Lines move or limit quickly: check the price at the book. Not financial advice.
        21+. If gambling is a problem, call 1-800-GAMBLER.{' '}
        <Link href="/legal/responsible-gambling" style={{ color: 'var(--link)' }}>Responsible gambling</Link>
      </p>

      <MobileCompareSheet
        item={inspectItem}
        columns={columns}
        onClose={() => setInspectItem(null)}
      />
    </div>
  )
}

function MobileEvRowCard({
  r,
  bankroll,
  onInspect,
}: {
  r: ScreenRow
  bankroll: number
  onInspect: () => void
}) {
  const d = r.implied > 0 ? 1 / r.implied : 1
  const size = r.pFair == null ? null : kellySizing(bankroll, r.pFair, d)
  const evColor = r.ev != null && r.ev > 0 ? GREEN : 'var(--fog)'
  const sportSlug = r.row.game_key.split(':')[0] as HeadshotSport

  return (
    <div
      style={{
        ...SURFACE_CARD,
        padding: '12px',
        marginBottom: '10px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <span style={{ width: 34, height: 34, flexShrink: 0, display: 'inline-flex' }}>
          <PlayerHeadshot sport={sportSlug} id={r.row.headshot_id} name={r.row.player} size={34} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: '13.5px', fontWeight: 700, color: 'var(--chalk)', lineHeight: 1.25, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {r.row.player}
          </div>
          <div className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '2px' }}>
            {r.row.away} @ {r.row.home} &middot; {startLabel(r.row.commence)}
          </div>
        </div>
        <TierPill alt={r.row.is_alt === true} modalPoint={r.row.modal_point} />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 10px', background: '#090a0f', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
        <div>
          <span style={{ fontSize: '12.5px', fontWeight: 700, color: 'var(--chalk)' }}>
            {r.label} {STAT_LABEL[r.row.stat] ?? r.row.stat}
          </span>
          <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '2px' }}>
            Fair: {r.fairAmerican != null ? (r.fairAmerican > 0 ? `+${r.fairAmerican}` : `${r.fairAmerican}`) : '--'} ({r.nRef} bks)
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <span className="mono" style={{ fontSize: '13px', fontWeight: 700, color: evColor }}>
            {r.ev == null ? '--' : `${signed(r.ev * 100)}% EV`}
          </span>
          {size && size.dollars > 0 && (
            <div className="mono" style={{ fontSize: '10.5px', color: 'var(--silver)', marginTop: '2px' }}>
              Kelly {fmtMoney(size.dollars)}
            </div>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
          <BookLogo book={r.bestBook} size={22} />
          <div>
            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--chalk)' }}>
              {bookDisplay(r.bestBook).label}
            </div>
            <div className="mono" style={{ fontSize: '11px', color: GREEN, fontWeight: 700 }}>
              {r.side} {formatOdds(r.bestAmerican)}
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={onInspect}
          className="btn btn-ghost"
          style={{ padding: '5px 10px', fontSize: '11px', fontWeight: 600 }}
        >
          Compare All Books ({r.row.books.length}) &rarr;
        </button>
      </div>
    </div>
  )
}

function MobileArbCard({
  r,
  outlay,
  copied,
  onCopy,
  onInspect,
}: {
  r: ArbRow
  outlay: number
  copied: boolean
  onCopy: (r: ArbRow) => void
  onInspect: () => void
}) {
  const a = r.arb
  const k = outlay / 100
  const live = a.netArbPct > 0
  const profit = a.guaranteedProfitPer100 * k
  const sportSlug = r.row.game_key.split(':')[0] as HeadshotSport

  return (
    <div
      style={{
        ...SURFACE_CARD,
        padding: '12px',
        marginBottom: '10px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <span style={{ width: 34, height: 34, flexShrink: 0, display: 'inline-flex' }}>
          <PlayerHeadshot sport={sportSlug} id={r.row.headshot_id} name={r.row.player} size={34} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: '13.5px', fontWeight: 700, color: 'var(--chalk)', lineHeight: 1.25, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {r.row.player}
          </div>
          <div className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '2px' }}>
            {r.row.line} {STAT_LABEL[r.row.stat] ?? r.row.stat} &middot; {r.row.away} @ {r.row.home}
          </div>
        </div>
        <span
          className="mono"
          style={{
            fontSize: '11px',
            fontWeight: 700,
            padding: '2px 7px',
            borderRadius: '4px',
            background: live ? 'rgba(87, 242, 135, 0.12)' : 'rgba(56, 189, 248, 0.12)',
            color: live ? GREEN : SKY,
            border: `1px solid ${live ? 'rgba(87, 242, 135, 0.3)' : 'rgba(56, 189, 248, 0.3)'}`,
          }}
        >
          {signed(a.netArbPct, 2)}% {live ? 'ARB' : 'HOLD'}
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
        <div style={{ padding: '8px', background: '#090a0f', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <BookLogo book={a.bestOverBook} size={18} />
            <span style={{ fontSize: '11px', color: 'var(--silver)', fontWeight: 600 }}>{bookDisplay(a.bestOverBook).label}</span>
          </div>
          <div className="mono" style={{ fontSize: '12px', fontWeight: 700, color: 'var(--chalk)', marginTop: '4px' }}>
            OVER {formatOdds(a.bestOverAm)}
          </div>
          <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '2px' }}>
            Stake: {usd(a.stakeOverPer100 * k)}
          </div>
        </div>

        <div style={{ padding: '8px', background: '#090a0f', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <BookLogo book={a.bestUnderBook} size={18} />
            <span style={{ fontSize: '11px', color: 'var(--silver)', fontWeight: 600 }}>{bookDisplay(a.bestUnderBook).label}</span>
          </div>
          <div className="mono" style={{ fontSize: '12px', fontWeight: 700, color: 'var(--chalk)', marginTop: '4px' }}>
            UNDER {formatOdds(a.bestUnderAm)}
          </div>
          <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '2px' }}>
            Stake: {usd(a.stakeUnderPer100 * k)}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', borderTop: `1px solid #151822`, paddingTop: '8px' }}>
        <span className="mono" style={{ fontSize: '11px', color: live ? GREEN : SKY, fontWeight: 700 }}>
          {profit >= 0 ? '+' : '-'}{usd(Math.abs(profit))} {live ? 'locked' : 'hold'} on {usd(outlay)}
        </span>
        <div style={{ display: 'flex', gap: '6px' }}>
          <button type="button" onClick={() => onCopy(r)} className="btn btn-ghost" style={{ padding: '4px 8px', fontSize: '10.5px' }}>
            {copied ? 'Copied!' : 'Copy'}
          </button>
          <button type="button" onClick={onInspect} className="btn btn-ghost" style={{ padding: '4px 8px', fontSize: '10.5px' }}>
            Books &rarr;
          </button>
        </div>
      </div>
    </div>
  )
}

function MobileMiddleCard({
  r,
  outlay,
  copied,
  onCopy,
  onInspect,
}: {
  r: MiddleResult
  outlay: number
  copied: boolean
  onCopy: (r: MiddleResult) => void
  onInspect: () => void
}) {
  const k = outlay / 100
  const free = r.missPct >= 0
  const sportSlug = r.low.game_key.split(':')[0] as HeadshotSport

  return (
    <div
      style={{
        ...SURFACE_CARD,
        padding: '12px',
        marginBottom: '10px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <span style={{ width: 34, height: 34, flexShrink: 0, display: 'inline-flex' }}>
          <PlayerHeadshot sport={sportSlug} id={r.low.headshot_id} name={r.low.player} size={34} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: '13.5px', fontWeight: 700, color: 'var(--chalk)', lineHeight: 1.25, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {r.low.player}
          </div>
          <div className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '2px' }}>
            {STAT_LABEL[r.low.stat] ?? r.low.stat} &middot; {r.low.away} @ {r.low.home}
          </div>
        </div>
        <span
          className="mono"
          style={{
            fontSize: '11px',
            fontWeight: 700,
            padding: '2px 7px',
            borderRadius: '4px',
            background: free ? 'rgba(87, 242, 135, 0.12)' : 'rgba(56, 189, 248, 0.12)',
            color: free ? GREEN : SKY,
            border: `1px solid ${free ? 'rgba(87, 242, 135, 0.3)' : 'rgba(56, 189, 248, 0.3)'}`,
          }}
        >
          {r.gap % 1 === 0 ? r.gap : r.gap.toFixed(1)} PT WINDOW
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
        <div style={{ padding: '8px', background: '#090a0f', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <BookLogo book={r.overBook} size={18} />
            <span style={{ fontSize: '11px', color: 'var(--silver)', fontWeight: 600 }}>{bookDisplay(r.overBook).label}</span>
          </div>
          <div className="mono" style={{ fontSize: '12px', fontWeight: 700, color: 'var(--chalk)', marginTop: '4px' }}>
            OVER {r.low.line} ({formatOdds(r.overAm)})
          </div>
          <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '2px' }}>
            Stake: {usd(r.stakeOverPer100 * k)}
          </div>
        </div>

        <div style={{ padding: '8px', background: '#090a0f', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <BookLogo book={r.underBook} size={18} />
            <span style={{ fontSize: '11px', color: 'var(--silver)', fontWeight: 600 }}>{bookDisplay(r.underBook).label}</span>
          </div>
          <div className="mono" style={{ fontSize: '12px', fontWeight: 700, color: 'var(--chalk)', marginTop: '4px' }}>
            UNDER {r.high.line} ({formatOdds(r.underAm)})
          </div>
          <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '2px' }}>
            Stake: {usd(r.stakeUnderPer100 * k)}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', borderTop: `1px solid #151822`, paddingTop: '8px' }}>
        <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)' }}>
          Miss: {signed(r.missPct)}% &middot; Hit: {signed(r.hitPct)}%
        </div>
        <div style={{ display: 'flex', gap: '6px' }}>
          <button type="button" onClick={() => onCopy(r)} className="btn btn-ghost" style={{ padding: '4px 8px', fontSize: '10.5px' }}>
            {copied ? 'Copied!' : 'Copy'}
          </button>
          <button type="button" onClick={onInspect} className="btn btn-ghost" style={{ padding: '4px 8px', fontSize: '10.5px' }}>
            Books &rarr;
          </button>
        </div>
      </div>
    </div>
  )
}

function MobileLongshotCard({
  r,
  onInspect,
}: {
  r: LongshotRow
  onInspect: () => void
}) {
  const m = r.m
  const sportSlug = r.row.game_key.split(':')[0] as HeadshotSport
  const over = r.side !== 'UNDER'
  const actionBook = over ? m.bestOverBook : m.bestUnderBook
  const actionAm = over ? m.bestOverAm : m.bestUnderAm
  const actionEv = r.bestEv
  const fairProb = over ? m.fairOverProb : m.fairUnderProb
  const sideLabel = over ? 'OVER' : 'UNDER'

  return (
    <div
      style={{
        ...SURFACE_CARD,
        padding: '12px',
        marginBottom: '10px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <span style={{ width: 34, height: 34, flexShrink: 0, display: 'inline-flex' }}>
          <PlayerHeadshot sport={sportSlug} id={r.row.headshot_id} name={r.row.player} size={34} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: '13.5px', fontWeight: 700, color: 'var(--chalk)', lineHeight: 1.25, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {r.row.player}
          </div>
          <div className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '2px' }}>
            {r.row.line} {STAT_LABEL[r.row.stat] ?? r.row.stat} &middot; {r.row.away} @ {r.row.home}
          </div>
        </div>
        <TierPill alt={r.row.is_alt === true} modalPoint={r.row.modal_point} />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 10px', background: '#090a0f', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
        <div>
          <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--chalk)' }}>
            Best Play: {sideLabel} {actionAm != null ? formatOdds(actionAm) : '--'}
          </span>
          <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '2px' }}>
            Fair: {fairText(fairProb)} {m.fairSource === 'curve' ? '(EST. CURVE)' : `(${m.nRef} BKS)`}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <span className="mono" style={{ fontSize: '12.5px', fontWeight: 700, color: actionEv != null && actionEv > 0 ? GREEN : 'var(--fog)' }}>
            {actionEv != null ? `${signed(actionEv)}% EV` : '--'}
          </span>
          {actionBook && (
            <div style={{ fontSize: '10.5px', color: 'var(--silver)', marginTop: '2px' }}>
              @ {bookDisplay(actionBook).label}
            </div>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          type="button"
          onClick={onInspect}
          className="btn btn-ghost"
          style={{ padding: '5px 10px', fontSize: '11px', fontWeight: 600 }}
        >
          Compare All Books ({r.row.books.length}) &rarr;
        </button>
      </div>
    </div>
  )
}

function MobileSgpCard({ r }: { r: SgpProxyRow }) {
  const isPositive = r.evPct > 0
  const vsDir = r.vsDirectPct
  const badgeColor = r.archetype === 'nba_reb_ast_10_10'
    ? 'rgba(168, 85, 247, 0.2)'
    : r.archetype.includes('vs_')
    ? 'rgba(59, 130, 246, 0.2)'
    : 'rgba(234, 179, 8, 0.2)'

  const badgeTextColor = r.archetype === 'nba_reb_ast_10_10'
    ? '#c084fc'
    : r.archetype.includes('vs_')
    ? '#60a5fa'
    : '#facc15'

  return (
    <div
      style={{
        ...SURFACE_CARD,
        padding: '12px',
        marginBottom: '10px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <PlayerHeadshot id={r.headshot_id} name={r.player} sport={r.sport as HeadshotSport} size={36} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ fontSize: '13.5px', fontWeight: 700, color: 'var(--chalk)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {r.player}
            </span>
            <span
              style={{
                fontSize: '10px',
                fontWeight: 700,
                padding: '1px 5px',
                borderRadius: '4px',
                background: badgeColor,
                color: badgeTextColor,
                letterSpacing: '0.03em',
                flexShrink: 0,
              }}
            >
              {r.archetypeLabel}
            </span>
          </div>
          <div className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '2px' }}>
            {r.team ? `${r.team} \u00b7 ` : ''}{r.away} @ {r.home} &middot; {startLabel(r.commence)}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
        {r.legs.map((leg, idx) => (
          <span
            key={idx}
            className="mono"
            style={{
              fontSize: '11px',
              padding: '2px 7px',
              borderRadius: '4px',
              background: '#090a0f',
              border: `1px solid ${HAIR}`,
              color: 'var(--chalk)',
            }}
          >
            {leg.label} ({formatOdds(leg.bookAm)})
          </span>
        ))}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 10px', background: '#090a0f', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <BookLogo book={r.book} size={18} />
            <span className="mono" style={{ fontSize: '13px', fontWeight: 700, color: GREEN }}>
              Est. {formatOdds(r.estSgpAm)}
            </span>
          </div>
          <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '2px' }}>
            Fair: {formatOdds(r.fairJointAm)} ({(r.fairJointProb * 100).toFixed(1)}%)
          </div>
        </div>

        <div style={{ textAlign: 'right' }}>
          <span
            className="mono"
            style={{
              fontSize: '12px',
              fontWeight: 700,
              padding: '2px 7px',
              borderRadius: '4px',
              background: isPositive ? 'rgba(87, 242, 135, 0.12)' : 'rgba(255, 255, 255, 0.04)',
              color: isPositive ? GREEN : 'var(--fog)',
              border: isPositive ? '1px solid rgba(87, 242, 135, 0.3)' : `1px solid ${HAIR}`,
            }}
          >
            {r.evPct > 0 ? `+${r.evPct.toFixed(1)}%` : `${r.evPct.toFixed(1)}%`} EV
          </span>
          {vsDir != null && vsDir > 0 && (
            <div style={{ fontSize: '10px', color: GREEN, marginTop: '2px' }}>
              +{vsDir.toFixed(1)}% vs direct
            </div>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <a
          href={r.deeplink || '#'}
          target="_blank"
          rel="noopener noreferrer"
          className="btn btn-ghost"
          style={{
            padding: '5px 12px',
            fontSize: '11px',
            fontWeight: 600,
            color: 'var(--chalk)',
            border: `1px solid ${HAIR}`,
            borderRadius: '4px',
            textDecoration: 'none',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
          }}
        >
          Build SGP on {bookDisplay(r.book).label} &rarr;
        </a>
      </div>
    </div>
  )
}

function MobileCompareSheet({
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
      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        {item.type === 'ev' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
            <div style={{ padding: '8px 10px', background: '#090a0f', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
              <div className="mono" style={{ fontSize: '10px', color: 'var(--fog)', textTransform: 'uppercase' }}>Consensus Fair</div>
              <div className="mono" style={{ fontSize: '14px', fontWeight: 700, color: 'var(--chalk)', marginTop: '2px' }}>
                {item.row.fairAmerican != null ? (item.row.fairAmerican > 0 ? `+${item.row.fairAmerican}` : `${item.row.fairAmerican}`) : '--'}
              </div>
              <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '2px' }}>
                {item.row.nRef} reference books
              </div>
            </div>

            <div style={{ padding: '8px 10px', background: '#090a0f', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
              <div className="mono" style={{ fontSize: '10px', color: 'var(--fog)', textTransform: 'uppercase' }}>Best Odds &middot; {bookDisplay(item.row.bestBook).label}</div>
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
          <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '8px', letterSpacing: '0.06em' }}>
            ALL REPORTING BOOKS ({row.books.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
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
                    background: isBest ? 'rgba(87, 242, 135, 0.06)' : '#090a0f',
                    border: `1px solid ${isBest ? 'rgba(87, 242, 135, 0.35)' : HAIR}`,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <BookLogo book={c.key} size={22} />
                    <div>
                      <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--chalk)' }}>
                        {bookDisplay(c.key).label}
                        {c.offshore && <span style={{ fontSize: '10px', color: 'var(--fog)', marginLeft: '4px' }}>(offshore)</span>}
                      </div>
                    </div>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <div style={{ textAlign: 'right' }}>
                      <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: isBest && item.type === 'ev' && item.row.side === 'OVER' ? GREEN : 'var(--chalk)' }}>
                        O {q.over != null ? formatOdds(q.over) : '--'}
                      </span>
                      <span style={{ margin: '0 4px', color: 'var(--fog)' }}>/</span>
                      <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: isBest && item.type === 'ev' && item.row.side === 'UNDER' ? GREEN : 'var(--chalk)' }}>
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

function OutlierCard({
  item,
  isLocked,
  onReveal,
}: {
  item: OutlierRow
  isLocked: boolean
  onReveal: () => void
}) {
  const sport = item.row.game_key.split(':')[0]
  const evBadge = item.trueEvPct != null
    ? `${signed(item.trueEvPct)}% EV \u00b7 +${item.payoutPremiumPct.toFixed(1)}% vs Mkt`
    : `+${item.payoutPremiumPct.toFixed(1)}% vs Mkt`

  return (
    <div style={{ ...SURFACE_CARD, padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      {/* Row 1: Matchup & Dislocation Pill */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span className="mono" style={{ fontSize: '10.5px', fontWeight: 700, padding: '2px 6px', borderRadius: '4px', background: '#171a24', color: '#9ca3af', border: `1px solid ${HAIR}` }}>
            {sport.toUpperCase()}
          </span>
          <span className="mono" style={{ fontSize: '11.5px', color: 'var(--silver)' }}>
            {item.row.away} @ {item.row.home} &middot; {startLabel(item.row.commence)}
          </span>
        </div>
        <div className="mono" style={{ fontSize: '11px', fontWeight: 700, color: GREEN, background: 'rgba(87,242,135,0.08)', padding: '3px 8px', borderRadius: '4px', border: '1px solid rgba(87,242,135,0.25)' }}>
          {evBadge}
        </div>
      </div>

      {/* Row 2: Player, Prop Line & Giant Outlier Price */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ flexShrink: 0, width: 36, height: 36, display: 'inline-flex' }}>
            <PlayerHeadshot sport={sport as HeadshotSport} id={item.row.headshot_id} name={item.row.player} size={36} />
          </span>
          <div>
            <div style={{ fontSize: '15px', fontWeight: 700, color: '#f3f4f6', lineHeight: 1.25 }}>{item.row.player}</div>
            <div className="mono" style={{ fontSize: '12px', color: '#9ca3af', display: 'flex', alignItems: 'center', gap: '6px', marginTop: '3px' }}>
              <span>{item.side === 'OVER' ? 'O' : 'U'} {item.row.line} {STAT_LABEL[item.row.stat] ?? item.row.stat}</span>
              {item.row.is_alt === true && (
                <span style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.04em', padding: '0 4px', borderRadius: '3px', border: '1px solid #3a4156', color: '#9ca3af' }}>[ALT]</span>
              )}
            </div>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="mono" style={{ fontSize: '24px', fontWeight: 800, color: GREEN, letterSpacing: '-0.02em', lineHeight: 1 }}>
            {item.outlierAmerican > 0 ? `+${item.outlierAmerican}` : `${item.outlierAmerican}`}
          </div>
          <div className="mono" style={{ fontSize: '10px', color: '#7d8596', marginTop: '3px', letterSpacing: '0.04em' }}>OUTLIER PRICE</div>
        </div>
      </div>

      {/* Row 3: Target Book / Reveal Control */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', background: '#090a0f', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
        {isLocked ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
              <span style={{ fontSize: '13px', color: '#9ca3af' }}>\ud83d\udd12</span>
              <span style={{ fontSize: '12px', fontWeight: 600, color: '#9ca3af' }}>Book hidden</span>
              <span className="mono" style={{ fontSize: '10.5px', color: '#7d8596' }}>({item.nPeers} peer books)</span>
            </div>
            <button onClick={onReveal} className="btn btn-ghost" style={{ padding: '3px 9px', fontSize: '11px', color: GREEN, borderColor: 'rgba(87,242,135,0.3)' }}>
              Reveal book &rarr;
            </button>
          </>
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <BookLogo book={item.outlierBook} size={22} />
              <span style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--chalk)' }}>{bookDisplay(item.outlierBook).label}</span>
              <span className="mono" style={{ fontSize: '11px', color: '#7d8596', marginLeft: '4px' }}>Implied: {(item.outlierImplied * 100).toFixed(1)}%</span>
            </div>
            <span className="mono" style={{ fontSize: '10.5px', color: GREEN, fontWeight: 700, letterSpacing: '0.04em' }}>BEST PRICE</span>
          </>
        )}
      </div>

      {/* Row 4: Horizontal Price Comparison Bar Chart */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', paddingTop: '6px', borderTop: '1px solid #151822' }}>
        {/* Outlier Book Row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div style={{ width: '32px', display: 'flex', justifyContent: 'center', flexShrink: 0 }}>
            {isLocked ? <span style={{ fontSize: '12px', color: '#7d8596' }}>\ud83d\udd12</span> : <BookLogo book={item.outlierBook} size={20} />}
          </div>
          <div style={{ flex: 1, height: '8px', background: '#171a24', borderRadius: '4px', overflow: 'hidden' }}>
            <div style={{ width: '100%', height: '100%', background: GREEN, borderRadius: '4px' }} />
          </div>
          <div className="mono" style={{ width: '56px', textAlign: 'right', fontSize: '12px', fontWeight: 700, color: GREEN, flexShrink: 0 }}>
            {item.outlierAmerican > 0 ? `+${item.outlierAmerican}` : `${item.outlierAmerican}`}
          </div>
        </div>

        {/* Peer Books Rows (Up to 5) */}
        {item.peers.slice(0, 5).map((peer, idx) => (
          <div key={`${peer.book}-${idx}`} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{ width: '32px', display: 'flex', justifyContent: 'center', flexShrink: 0 }}>
              <BookLogo book={peer.book} size={20} />
            </div>
            <div style={{ flex: 1, height: '8px', background: '#171a24', borderRadius: '4px', overflow: 'hidden' }}>
              <div style={{ width: `${Math.round(peer.barRatio * 100)}%`, height: '100%', background: '#334155', borderRadius: '4px' }} />
            </div>
            <div className="mono" style={{ width: '56px', textAlign: 'right', fontSize: '11.5px', color: '#9ca3af', flexShrink: 0 }}>
              {peer.american > 0 ? `+${peer.american}` : `${peer.american}`}
            </div>
          </div>
        ))}

        {/* Footer Summary Line */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '4px', fontSize: '11px', color: '#7d8596' }}>
          <div>
            <span>Market avg &middot; {item.nPeers} books</span>
            {item.fairAmerican != null && (
              <span style={{ marginLeft: '6px' }}>&middot; Fair: {item.fairAmerican > 0 ? `+${item.fairAmerican}` : `${item.fairAmerican}`}</span>
            )}
          </div>
          <div className="mono" style={{ color: '#d1d5db', fontWeight: 600 }}>
            {item.marketAvgAmerican > 0 ? `+${item.marketAvgAmerican}` : `${item.marketAvgAmerican}`}
          </div>
        </div>
      </div>
    </div>
  )
}

function OutlierTableRow({
  r,
  columns,
  isLocked,
  onReveal,
}: {
  r: OutlierRow
  columns: { key: string; offshore: boolean }[]
  isLocked: boolean
  onReveal: () => void
}) {
  const byBook = new Map(r.row.books.map(b => [b.book, b]))
  const gapColor = r.trueEvPct != null && r.trueEvPct > 0 ? GREEN : SKY

  return (
    <tr data-row="odds">
      <PropCells row={r.row} label={r.label} />
      <td className="odds-ev">
        <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: 'var(--chalk)' }}>
          {r.marketAvgAmerican > 0 ? `+${r.marketAvgAmerican}` : `${r.marketAvgAmerican}`}
        </div>
        <div className="mono odds-sub">
          {r.nPeers} PEERS &middot; FAIR {r.fairAmerican != null ? (r.fairAmerican > 0 ? `+${r.fairAmerican}` : `${r.fairAmerican}`) : '--'}
        </div>
      </td>
      <td className="odds-ev">
        {isLocked ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ fontSize: '12px' }}>\ud83d\udd12</span>
            <button onClick={onReveal} className="btn btn-ghost" style={{ padding: '2px 8px', fontSize: '11px', color: GREEN }}>Reveal book</button>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <BookLogo book={r.outlierBook} size={28} />
            <div>
              <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: GREEN }}>
                {r.side} {r.outlierAmerican > 0 ? `+${r.outlierAmerican}` : `${r.outlierAmerican}`}
              </div>
              <div className="mono odds-sub">{bookDisplay(r.outlierBook).label}</div>
            </div>
          </div>
        )}
      </td>
      <td style={{ textAlign: 'right' }}>
        <div className="mono" style={{ fontSize: '12.5px', fontWeight: 700, color: gapColor }}>
          +{r.payoutPremiumPct.toFixed(1)}% MKT
        </div>
        <div className="mono odds-sub" style={{ color: r.trueEvPct != null && r.trueEvPct > 0 ? GREEN : 'var(--fog)' }}>
          {r.trueEvPct != null ? `${signed(r.trueEvPct)}% EV` : 'NO FAIR EV'}
        </div>
      </td>
      {columns.map(c => {
        const q = byBook.get(c.key)
        const target = q ? (r.side === 'OVER' ? q.over : q.under) : null
        const other = q ? (r.side === 'OVER' ? q.under : q.over) : null
        const isOutlier = !isLocked && c.key === r.outlierBook
        return <PriceCell key={c.key} top={target} bottom={other} topState={isOutlier ? 'best' : undefined} best={isOutlier} tint={isOutlier} />
      })}
    </tr>
  )
}

function SgpTableRow({ r }: { r: SgpProxyRow }) {
  const isPositive = r.evPct > 0
  const vsDir = r.vsDirectPct
  const badgeColor = r.archetype === 'nba_reb_ast_10_10'
    ? 'rgba(168, 85, 247, 0.2)'
    : r.archetype.includes('vs_')
    ? 'rgba(59, 130, 246, 0.2)'
    : 'rgba(234, 179, 8, 0.2)'

  const badgeTextColor = r.archetype === 'nba_reb_ast_10_10'
    ? '#c084fc'
    : r.archetype.includes('vs_')
    ? '#60a5fa'
    : '#facc15'

  return (
    <tr style={{ borderBottom: `1px solid ${HAIR}` }}>
      <td style={{ padding: '10px 14px', minWidth: '220px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <PlayerHeadshot id={r.headshot_id} name={r.player} sport={r.sport as HeadshotSport} size={36} />
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontWeight: 600, color: 'var(--chalk)', fontSize: '13px' }}>{r.player}</span>
              <span style={{
                fontSize: '10px',
                fontWeight: 700,
                padding: '1px 5px',
                borderRadius: '4px',
                background: badgeColor,
                color: badgeTextColor,
                letterSpacing: '0.03em',
              }}>
                {r.archetypeLabel}
              </span>
            </div>
            <div className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '2px' }}>
              {r.team ? `${r.team} \u00b7 ` : ''}{r.away} @ {r.home} \u00b7 {startLabel(r.commence)}
            </div>
          </div>
        </div>
      </td>
      <td style={{ padding: '10px 14px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
            {r.legs.map((leg, idx) => (
              <span
                key={idx}
                className="mono"
                style={{
                  fontSize: '11px',
                  padding: '2px 6px',
                  borderRadius: '4px',
                  background: 'rgba(255, 255, 255, 0.04)',
                  border: `1px solid ${HAIR}`,
                  color: 'var(--chalk)',
                }}
              >
                {leg.label} ({formatOdds(leg.bookAm)})
              </span>
            ))}
            <span style={{ fontSize: '11px', color: 'var(--fog)' }}>
              @ <span style={{ textTransform: 'capitalize', color: 'var(--chalk)', fontWeight: 500 }}>{bookDisplay(r.book).label}</span>
            </span>
          </div>
          <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)' }}>
            Uncorrelated parlay: {formatOdds(r.naiveSingleProductAm)}
          </div>
        </div>
      </td>
      <td style={{ padding: '10px 14px' }}>
        <div className="mono" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--chalk)' }}>
          {formatOdds(r.fairJointAm)}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginTop: '2px' }}>
          <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>
            {(r.fairJointProb * 100).toFixed(1)}% fair
          </span>
          <span
            className="mono"
            style={{
              fontSize: '10px',
              padding: '1px 4px',
              borderRadius: '3px',
              background: 'rgba(255, 255, 255, 0.05)',
              color: 'var(--fog)',
            }}
            title={`Empirical true correlation rho = ${r.rhoTrue}`}
          >
            \u03c1={r.rhoTrue > 0 ? `+${r.rhoTrue}` : r.rhoTrue}
          </span>
        </div>
      </td>
      <td style={{ padding: '10px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
          <BookLogo book={r.book} size={22} />
          <span className="mono" style={{ fontSize: '13px', fontWeight: 700, color: GREEN }}>
            Est. SGP: {formatOdds(r.estSgpAm)}
          </span>
          <span style={{ fontSize: '10px', fontWeight: 700, padding: '1px 4px', borderRadius: '3px', background: 'rgba(87, 242, 135, 0.1)', color: GREEN, border: '1px solid rgba(87, 242, 135, 0.25)' }}>
            EST. BOOK SGP
          </span>
        </div>
        {r.directMarketAm != null ? (
          <div style={{ fontSize: '11px', color: vsDir != null && vsDir > 0 ? GREEN : 'var(--fog)', marginTop: '2px' }}>
            {vsDir != null && vsDir > 0 ? (
              <>Beats direct {r.directMarketLabel || 'market'} ({formatOdds(r.directMarketAm)}) by +{vsDir.toFixed(1)}%</>
            ) : (
              <>Direct {r.directMarketLabel || 'market'}: {formatOdds(r.directMarketAm)}</>
            )}
          </div>
        ) : (
          <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '2px' }}>
            Book copula (\u03c1={r.rhoBook}, hold={Math.round(r.holdBook * 100)}%)
          </div>
        )}
        {r.runnerUpBooks && r.runnerUpBooks.length > 0 && (
          <div style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '3px' }}>
            Also at: {r.runnerUpBooks.map(b => `${bookDisplay(b.book).label} (${b.evPct > 0 ? `+${b.evPct.toFixed(1)}%` : `${b.evPct.toFixed(1)}%`} EV)`).join(', ')}
          </div>
        )}
      </td>
      <td style={{ padding: '10px 14px', textAlign: 'right' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '10px' }}>
          <span
            className="mono"
            style={{
              fontSize: '12px',
              fontWeight: 700,
              padding: '3px 8px',
              borderRadius: '4px',
              background: isPositive ? 'rgba(87, 242, 135, 0.12)' : 'rgba(255, 255, 255, 0.04)',
              color: isPositive ? GREEN : 'var(--fog)',
              border: isPositive ? '1px solid rgba(87, 242, 135, 0.3)' : `1px solid ${HAIR}`,
            }}
          >
            {r.evPct > 0 ? `+${r.evPct.toFixed(1)}%` : `${r.evPct.toFixed(1)}%`} EV
          </span>
          <a
            href={r.deeplink || '#'}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-ghost"
            style={{
              padding: '4px 8px',
              fontSize: '11px',
              fontWeight: 600,
              color: 'var(--chalk)',
              border: `1px solid ${HAIR}`,
              borderRadius: '4px',
              textDecoration: 'none',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
            }}
          >
            Build SGP \u2197
          </a>
        </div>
      </td>
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
  const hasNonePreset = presets.some(p => p.label.toLowerCase() === 'none')
  const hasAllPreset = presets.some(p => p.label.toLowerCase() === 'all')

  return (
    <div ref={box} style={{ position: 'relative' }}>
      <button onClick={() => setOpen(v => !v)} aria-expanded={open} className="btn btn-ghost" style={{ padding: '6px 11px', fontSize: '11.5px', height: '34px' }}>{label}</button>
      {open && (
        <div role="group" aria-label={label} style={{ position: 'absolute', zIndex: 30, top: '38px', ...(align === 'right' ? { right: 0 } : { left: 0 }), width: '290px', ...SURFACE_CARD, padding: '10px', display: 'grid', gap: '8px' }}>
          {header}
          <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', alignItems: 'center' }}>
            {presets.map(p => <button key={p.label} type="button" className="btn btn-ghost" style={{ padding: '3px 8px', fontSize: '11px' }} onClick={() => onChange(p.keys)}>{p.label}</button>)}
            {emptyMeansAll && !hasAllPreset && <button type="button" className="btn btn-ghost" style={{ padding: '3px 8px', fontSize: '11px' }} onClick={() => onChange([])}>All</button>}
            {!emptyMeansAll && !hasAllPreset && <button type="button" className="btn btn-ghost" style={{ padding: '3px 8px', fontSize: '11px' }} onClick={() => onChange(books.map(b => b.key))}>All</button>}
            {!hasNonePreset && <button type="button" className="btn btn-ghost" style={{ padding: '3px 8px', fontSize: '11px' }} onClick={() => onChange([])}>None</button>}
          </div>
          <div style={{ display: 'grid', gap: '2px', maxHeight: '280px', overflowY: 'auto' }}>
            {books.map(b => {
              const meta = bookDisplay(b.key)
              const isOnly = on.has(b.key) && on.size === 1
              return (
                <div
                  key={b.key}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '6px',
                    padding: '3px 6px',
                    borderRadius: '4px',
                    background: isOnly ? 'rgba(87, 242, 135, 0.08)' : on.has(b.key) ? 'rgba(255, 255, 255, 0.03)' : 'transparent',
                  }}
                >
                  <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: on.has(b.key) ? 'var(--chalk)' : 'var(--silver)', cursor: 'pointer', flex: 1, minWidth: 0 }}>
                    <input type="checkbox" checked={on.has(b.key)} onChange={() => toggle(b.key)} style={{ cursor: 'pointer', accentColor: 'var(--signal)' }} />
                    <span style={{ minWidth: '24px', display: 'inline-flex', justifyContent: 'center' }}><BookLogo book={b.key} size={22} /></span>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{meta.label}{b.offshore ? ' (offshore)' : ''}</span>
                    <span className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginRight: '2px' }}>{b.n}</span>
                  </label>
                  <button
                    type="button"
                    title={`Select only ${meta.label}`}
                    aria-label={`Only ${meta.label}`}
                    onClick={(e) => {
                      e.preventDefault()
                      e.stopPropagation()
                      onChange([b.key])
                    }}
                    style={{
                      fontSize: '10px',
                      fontFamily: 'var(--font-mono)',
                      fontWeight: 600,
                      letterSpacing: '0.04em',
                      padding: '2px 6px',
                      borderRadius: '4px',
                      border: isOnly ? '1px solid var(--signal)' : `1px solid ${HAIR}`,
                      background: isOnly ? 'var(--win-wash)' : 'transparent',
                      color: isOnly ? 'var(--signal)' : 'var(--fog)',
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
          <span style={{ fontSize: '11px', color: 'var(--fog)', lineHeight: 1.45 }}>{note}</span>
        </div>
      )}
    </div>
  )
}

/** Mobile-native SwiftUI style checklist for books with presets, Only buttons and full-width touch targets. */
function MobileBookChecklist({
  title,
  subtitle,
  books,
  selected,
  onChange,
  presets,
  emptyMeansAll,
  extraHeader,
}: {
  title: string
  subtitle?: string
  books: { key: string; n: number; offshore: boolean }[]
  selected: string[]
  onChange: (next: string[]) => void
  presets: { label: string; keys: string[] }[]
  emptyMeansAll?: boolean
  extraHeader?: React.ReactNode
}) {
  const [collapsed, setCollapsed] = useState(false)
  const on = new Set(selected)
  const toggle = (k: string) => onChange(on.has(k) ? selected.filter(x => x !== k) : [...selected, k])
  const countLabel = emptyMeansAll && selected.length === 0 ? 'ALL' : `${selected.length}`
  const hasNonePreset = presets.some(p => p.label.toLowerCase() === 'none')
  const hasAllPreset = presets.some(p => p.label.toLowerCase() === 'all')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '10px', background: '#090a0f', borderRadius: '8px', border: `1px solid ${HAIR}` }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <button
          type="button"
          onClick={() => setCollapsed(v => !v)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            background: 'transparent',
            border: 'none',
            padding: 0,
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <span className="dell-heading" style={{ fontSize: '11px', color: 'var(--chalk)', letterSpacing: '0.06em' }}>
            {title} ({countLabel})
          </span>
          <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>
            {collapsed ? '[\u25bc Show]' : '[\u25b2 Hide]'}
          </span>
        </button>
      </div>

      {!collapsed && (
        <>
          {extraHeader}

          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
            {presets.map(p => (
              <button
                key={p.label}
                type="button"
                className="btn btn-ghost"
                style={{ padding: '4px 10px', fontSize: '11px', minHeight: '30px' }}
                onClick={() => onChange(p.keys)}
              >
                {p.label}
              </button>
            ))}
            {emptyMeansAll && !hasAllPreset && (
              <button
                type="button"
                className="btn btn-ghost"
                style={{ padding: '4px 10px', fontSize: '11px', minHeight: '30px' }}
                onClick={() => onChange([])}
              >
                All
              </button>
            )}
            {!emptyMeansAll && !hasAllPreset && (
              <button
                type="button"
                className="btn btn-ghost"
                style={{ padding: '4px 10px', fontSize: '11px', minHeight: '30px' }}
                onClick={() => onChange(books.map(b => b.key))}
              >
                All
              </button>
            )}
            {!hasNonePreset && (
              <button
                type="button"
                className="btn btn-ghost"
                style={{ padding: '4px 10px', fontSize: '11px', minHeight: '30px' }}
                onClick={() => onChange([])}
              >
                None
              </button>
            )}
          </div>

          <div style={{ display: 'grid', gap: '4px', maxHeight: '220px', overflowY: 'auto', WebkitOverflowScrolling: 'touch', paddingRight: '2px' }}>
            {books.map(b => {
              const meta = bookDisplay(b.key)
              const isOnly = on.has(b.key) && on.size === 1
              const checked = on.has(b.key)
              return (
                <div
                  key={b.key}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    minHeight: '42px',
                    padding: '6px 8px',
                    borderRadius: '6px',
                    background: isOnly ? 'rgba(87, 242, 135, 0.08)' : checked ? '#141824' : 'transparent',
                    border: isOnly ? '1px solid rgba(87, 242, 135, 0.3)' : checked ? '1px solid #1e2536' : '1px solid transparent',
                  }}
                >
                  <label
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '10px',
                      fontSize: '12.5px',
                      color: checked ? 'var(--chalk)' : 'var(--silver)',
                      cursor: 'pointer',
                      flex: 1,
                      minWidth: 0,
                      userSelect: 'none',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(b.key)}
                      style={{ cursor: 'pointer', width: '16px', height: '16px', accentColor: 'var(--signal)' }}
                    />
                    <span style={{ minWidth: '24px', display: 'inline-flex', justifyContent: 'center' }}>
                      <BookLogo book={b.key} size={24} />
                    </span>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: checked ? 600 : 400 }}>
                      {meta.label}{b.offshore ? ' (offshore)' : ''}
                    </span>
                    <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginRight: '6px' }}>
                      {b.n}
                    </span>
                  </label>

                  <button
                    type="button"
                    title={`Select only ${meta.label}`}
                    aria-label={`Only ${meta.label}`}
                    onClick={(e) => {
                      e.preventDefault()
                      e.stopPropagation()
                      onChange([b.key])
                    }}
                    style={{
                      fontSize: '10px',
                      fontFamily: 'var(--font-mono)',
                      fontWeight: 700,
                      letterSpacing: '0.04em',
                      padding: '4px 8px',
                      minHeight: '30px',
                      minWidth: '46px',
                      borderRadius: '4px',
                      border: isOnly ? '1px solid var(--signal)' : `1px solid ${HAIR}`,
                      background: isOnly ? 'var(--win-wash)' : 'transparent',
                      color: isOnly ? 'var(--signal)' : 'var(--fog)',
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

          {subtitle && (
            <span style={{ fontSize: '11px', color: 'var(--fog)', lineHeight: 1.45 }}>{subtitle}</span>
          )}
        </>
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

function GameCard({ game }: { game: GamePricedGame & { sport: string; filteredMarkets: GamePricedMarket[] } }) {
  const sport = (game.sport || '').toLowerCase()
  const isNba = sport === 'nba'
  const isNhl = sport === 'nhl'

  const expMargin = game.mu_margin != null ? (
    game.mu_margin > 0
      ? `${game.home_team} -${Math.abs(game.mu_margin).toFixed(1)}`
      : `${game.away_team} -${Math.abs(game.mu_margin).toFixed(1)}`
  ) : null

  return (
    <div style={{ ...SURFACE_CARD, padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span className="mono" style={{
            fontSize: '11px',
            fontWeight: 700,
            padding: '2px 7px',
            borderRadius: '4px',
            background: isNba ? 'rgba(249,115,22,0.15)' : 'rgba(56,189,248,0.15)',
            color: isNba ? '#f97316' : '#38bdf8',
            border: `1px solid ${isNba ? 'rgba(249,115,22,0.35)' : 'rgba(56,189,248,0.35)'}`,
          }}>
            {sport.toUpperCase()}
          </span>
          <span style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)' }}>
            {game.away_team} @ {game.home_team}
          </span>
        </div>
        <div className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>
          {game.game_date}
        </div>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gap: '8px',
        padding: '10px 12px',
        background: '#090a0f',
        borderRadius: '6px',
        border: `1px solid ${HAIR}`,
      }}>
        {isNba && (
          <>
            <div>
              <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>MODEL MARGIN</div>
              <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: '#f3f4f6', marginTop: '2px' }}>
                {expMargin ?? '--'}
              </div>
            </div>
            <div>
              <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>MODEL TOTAL</div>
              <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: '#f3f4f6', marginTop: '2px' }}>
                {game.mu_total != null ? `${game.mu_total.toFixed(1)} pts` : '--'}
              </div>
            </div>
            <div>
              <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>EXPECTED PACE</div>
              <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: SKY, marginTop: '2px' }}>
                {game.exp_pace != null ? `${game.exp_pace.toFixed(1)} poss` : '--'}
              </div>
            </div>
          </>
        )}
        {isNhl && (
          <>
            <div>
              <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>EXPECTED GOALS</div>
              <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: '#f3f4f6', marginTop: '2px' }}>
                {game.lambda_a != null && game.lambda_h != null
                  ? `${game.away_team} ${game.lambda_a.toFixed(1)} - ${game.home_team} ${game.lambda_h.toFixed(1)}`
                  : '--'}
              </div>
            </div>
            <div>
              <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>MODEL TOTAL</div>
              <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: '#f3f4f6', marginTop: '2px' }}>
                {game.exp_total != null ? `${game.exp_total.toFixed(2)} goals` : '--'}
              </div>
            </div>
            <div>
              <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>DISPERSION</div>
              <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: SKY, marginTop: '2px' }}>
                {game.sigma_total != null ? `\u03c3 ${game.sigma_total.toFixed(1)}` : '--'}
              </div>
            </div>
          </>
        )}
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${HAIR}`, color: 'var(--fog)', fontSize: '10px' }}>
              <th scope="col" style={{ textAlign: 'left', padding: '6px 8px', fontWeight: 600 }}>MARKET / SIDE</th>
              <th scope="col" style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 600 }}>LINE</th>
              <th scope="col" style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 600 }}>MODEL %</th>
              <th scope="col" style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 600 }}>MKT FAIR</th>
              <th scope="col" style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 600 }}>EDGE</th>
              <th scope="col" style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 600 }}>BEST PRICE</th>
            </tr>
          </thead>
          <tbody>
            {game.filteredMarkets.map((m, idx) => {
              const hasEdge = m.model_edge != null && m.model_edge >= 0.015
              const sideLabel = m.side === 'home' ? game.home_team : (m.side === 'away' ? game.away_team : m.side.toUpperCase())
              return (
                <tr key={`${m.system}-${m.side}-${idx}`} style={{ borderBottom: '1px solid #151822', background: hasEdge ? 'rgba(87,242,135,0.03)' : 'transparent' }}>
                  <td style={{ padding: '8px', color: '#f3f4f6' }}>
                    <div style={{ fontWeight: 600 }}>{m.market_label}</div>
                    <div className="mono" style={{ fontSize: '11px', color: 'var(--silver)' }}>{sideLabel}</div>
                  </td>
                  <td className="mono" style={{ textAlign: 'right', padding: '8px', color: '#f3f4f6', fontWeight: 600 }}>
                    {m.line != null ? (m.line > 0 ? `+${m.line}` : `${m.line}`) : 'ML'}
                  </td>
                  <td className="mono" style={{ textAlign: 'right', padding: '8px', color: '#f3f4f6' }}>
                    {(m.p_model * 100).toFixed(1)}%
                  </td>
                  <td className="mono" style={{ textAlign: 'right', padding: '8px', color: 'var(--fog)' }}>
                    {m.p_fair != null ? `${(m.p_fair * 100).toFixed(1)}%` : '--'}
                  </td>
                  <td className="mono" style={{ textAlign: 'right', padding: '8px', fontWeight: 700, color: hasEdge ? GREEN : (m.model_edge != null && m.model_edge < 0 ? '#ff5a4f' : 'var(--silver)') }}>
                    {m.model_edge != null ? `${m_edge(m.model_edge)}` : '--'}
                  </td>
                  <td style={{ textAlign: 'right', padding: '8px' }}>
                    {m.best_odds != null ? (
                      <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', justifyContent: 'flex-end' }}>
                        <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: hasEdge ? GREEN : 'var(--chalk)' }}>
                          {formatOdds(m.best_odds)}
                        </span>
                        {m.best_book && <BookTag book={m.best_book} size={15} />}
                      </div>
                    ) : (
                      <span className="mono" style={{ color: 'var(--fog)' }}>--</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function GameLinesTable({ rows, limit }: { rows: GameProjectionRow[]; limit: number }) {
  return (
    <div className="odds-table-wrap" style={{ ...SURFACE_CARD, padding: 0 }}>
      <table className="odds-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: `1px solid ${HAIR}`, background: '#0d0f15' }}>
            <th scope="col" style={{ textAlign: 'left', minWidth: '70px', padding: '10px 12px' }}>SPORT</th>
            <th scope="col" style={{ textAlign: 'left', minWidth: '180px', padding: '10px 12px' }}>MATCHUP</th>
            <th scope="col" style={{ textAlign: 'left', minWidth: '120px', padding: '10px 12px' }}>MARKET</th>
            <th scope="col" style={{ textAlign: 'left', minWidth: '140px', padding: '10px 12px' }}>SELECTION / LINE</th>
            <th scope="col" style={{ textAlign: 'right', minWidth: '100px', padding: '10px 12px' }}>MODEL PROB</th>
            <th scope="col" style={{ textAlign: 'right', minWidth: '100px', padding: '10px 12px' }}>MKT FAIR</th>
            <th scope="col" style={{ textAlign: 'right', minWidth: '100px', padding: '10px 12px' }}>EDGE</th>
            <th scope="col" style={{ textAlign: 'right', minWidth: '100px', padding: '10px 12px' }}>MARKET EV</th>
            <th scope="col" style={{ textAlign: 'right', minWidth: '160px', padding: '10px 12px' }}>BEST ODDS &amp; BOOK</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, limit).map((r, idx) => {
            const isNba = r.sport.toLowerCase() === 'nba'
            const hasEdge = r.model_edge != null && r.model_edge >= 0.015
            const bestOdds = r.best_over_odds ?? r.best_under_odds
            const bestBook = r.best_over_book ?? r.best_under_book
            const sideDisplay = r.team ? `${r.team} ${r.side.toUpperCase()}` : r.side.toUpperCase()
            const lineDisplay = r.line != null ? (r.line > 0 ? `+${r.line}` : `${r.line}`) : ''

            return (
              <tr key={`${r.game_key}-${r.system}-${r.side}-${idx}`} data-row="odds">
                <td style={{ padding: '9px 12px' }}>
                  <span className="mono" style={{
                    fontSize: '10.5px',
                    fontWeight: 700,
                    padding: '2px 6px',
                    borderRadius: '4px',
                    background: isNba ? 'rgba(249,115,22,0.15)' : 'rgba(56,189,248,0.15)',
                    color: isNba ? '#f97316' : '#38bdf8',
                    border: `1px solid ${isNba ? 'rgba(249,115,22,0.35)' : 'rgba(56,189,248,0.35)'}`,
                  }}>
                    {r.sport.toUpperCase()}
                  </span>
                </td>
                <td style={{ padding: '9px 12px', fontWeight: 600, color: 'var(--chalk)' }}>
                  {r.player}
                  <div className="mono" style={{ fontSize: '10.5px', color: '#7d8596', fontWeight: 400 }}>
                    {startLabel(r.commence)}
                  </div>
                </td>
                <td style={{ padding: '9px 12px', color: 'var(--silver)' }}>
                  {r.stat_label}
                </td>
                <td className="mono" style={{ padding: '9px 12px', fontWeight: 600, color: '#f3f4f6' }}>
                  {sideDisplay} {lineDisplay}
                </td>
                <td className="mono" style={{ textAlign: 'right', padding: '9px 12px', color: '#f3f4f6' }}>
                  {(r.p_model_side * 100).toFixed(1)}%
                </td>
                <td className="mono" style={{ textAlign: 'right', padding: '9px 12px', color: '#7d8596' }}>
                  {r.p_mkt_side != null ? `${(r.p_mkt_side * 100).toFixed(1)}%` : '--'}
                </td>
                <td className="mono" style={{ textAlign: 'right', padding: '9px 12px', fontWeight: 700, color: hasEdge ? GREEN : (r.model_edge != null && r.model_edge < 0 ? '#ff5a4f' : 'var(--silver)') }}>
                  {r.model_edge != null ? `${m_edge(r.model_edge)}` : '--'}
                </td>
                <td className="mono" style={{ textAlign: 'right', padding: '9px 12px', color: r.market_ev != null && r.market_ev > 0 ? GREEN : '#7d8596' }}>
                  {r.market_ev != null ? `${r.market_ev > 0 ? '+' : ''}${(r.market_ev * 100).toFixed(1)}%` : '--'}
                </td>
                <td style={{ textAlign: 'right', padding: '9px 12px' }}>
                  {bestOdds != null ? (
                    <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', justifyContent: 'flex-end' }}>
                      <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: hasEdge ? GREEN : 'var(--chalk)' }}>
                        {formatOdds(bestOdds)}
                      </span>
                      {bestBook && <BookTag book={bestBook} size={15} />}
                    </div>
                  ) : (
                    <span className="mono" style={{ color: '#7d8596' }}>--</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function m_edge(edge: number): string {
  return `${edge > 0 ? '+' : ''}${(edge * 100).toFixed(1)}%`
}

function GameLinesView({
  pricedGames,
  rows,
  view,
  limit,
  onSwitchView,
  isMobile,
}: {
  pricedGames: (GamePricedGame & { sport: string; filteredMarkets: GamePricedMarket[] })[]
  rows: GameProjectionRow[]
  view: 'cards' | 'table'
  limit: number
  onSwitchView: (v: 'cards' | 'table') => void
  isMobile: boolean
}) {
  return (
    <div>
      {isMobile && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
          <div style={{ display: 'inline-flex', padding: '2px', background: '#090a0f', border: `1px solid ${HAIR}`, borderRadius: '6px' }}>
            <button
              type="button"
              onClick={() => onSwitchView('cards')}
              style={{
                fontFamily: 'var(--font-text), sans-serif',
                fontSize: '11px',
                fontWeight: 600,
                padding: '4px 10px',
                borderRadius: '4px',
                border: 0,
                cursor: 'pointer',
                background: view === 'cards' ? '#1b1f2b' : 'transparent',
                color: view === 'cards' ? '#ffffff' : 'var(--fog)',
              }}
            >
              Game Cards ({pricedGames.length})
            </button>
            <button
              type="button"
              onClick={() => onSwitchView('table')}
              style={{
                fontFamily: 'var(--font-text), sans-serif',
                fontSize: '11px',
                fontWeight: 600,
                padding: '4px 10px',
                borderRadius: '4px',
                border: 0,
                cursor: 'pointer',
                background: view === 'table' ? '#1b1f2b' : 'transparent',
                color: view === 'table' ? '#ffffff' : 'var(--fog)',
              }}
            >
              Data Matrix ({rows.length})
            </button>
          </div>
        </div>
      )}

      {view === 'cards' ? (
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fill, minmax(520px, 1fr))', gap: '16px' }}>
          {pricedGames.slice(0, limit).map((g, idx) => (
            <GameCard key={`${g.sport}-${g.game_key}-${idx}`} game={g} />
          ))}
        </div>
      ) : (
        <GameLinesTable rows={rows} limit={limit} />
      )}
    </div>
  )
}
