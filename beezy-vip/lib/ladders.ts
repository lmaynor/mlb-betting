import { americanToDecimal } from '@/lib/ev-math'
import type { BoardRow, LadderBestSide } from '@/lib/odds-screen'

export const LADDER_MARKET_ORDER = ['rec_yds', 'rush_yds', 'receptions', 'pass_yds', 'pass_tds', 'anytime_td'] as const
export const LADDER_DISP_MAX = 0.06
export const LADDER_MIN_REF = 4
export const NOISE_FLOOR: Record<LadderCategory, number> = { yardage: 2.5, count: 5.0, milestone: 0 }

export type LadderCategory = 'yardage' | 'count' | 'milestone'
export type LadderKind = 'curves' | 'milestone'
export type LadderSegment = 'curves' | 'milestones' | 'all'
export type LadderState = 'verified' | 'market' | 'thin'

export interface LadderBookBar {
  book: string
  over: number | null
  under: number | null
  ts: string | null
  /** minutes this book trails the rung's freshest two-sided quote; null when unknown */
  lagMin: number | null
  /** lag > 30: badge stripped; lag > 120: greyed and out of consensus */
  staleBand: 'fresh' | 'stale' | 'grey'
  staleOpener: boolean
  overCap: boolean
  underCap: boolean
}

export interface LadderSide {
  side: 'OVER' | 'UNDER'
  book: string
  american: number
  /** net EV % of the best price vs the leave-one-out consensus; null when no LOO consensus */
  netEvPct: number | null
  pConsLoo: number | null
  dispLoo: number | null
  nRefLoo: number
  verified: boolean
  /** why the side is not verified (first failing gate), for tests/debug; null when verified */
  gateFail: string | null
}

export interface LadderRung {
  line: number
  row: BoardRow
  books: LadderBookBar[]
  nRef: number
  disp: number | null
  pCons: number | null
  rungVig: number | null
  /** best OVER and best UNDER candidates from the server payload (null when absent) */
  over: LadderSide | null
  under: LadderSide | null
  /** the side shown as the card hero: the verified side if any, else the side with the higher net EV, else OVER */
  hero: LadderSide | null
  verified: boolean
  state: LadderState
  /** one-sided payout premium of the best over vs leave-one-out peers; null when it does not clear +8%/+3% at >= 4 books */
  premiumPct: number | null
  /** cap prices hidden at this rung */
  capsHidden: number
  /** plateau: this rung's best over price equals the previous (lower) rung's best over price */
  flatRung: boolean
}

export interface LadderGroup {
  key: string
  gameKey: string
  commence: string
  away: string
  home: string
  player: string
  playerNorm: string
  headshotId?: number | string | null
  team?: string | null
  stat: string
  category: LadderCategory
  kind: LadderKind
  rungs: LadderRung[]
  modalOpen: number | null
  modalNow: number | null
  grpBooksAny: number
  grpBooks2sided: number
  verified: boolean
  premium: boolean
  state: LadderState
  defaultRung: number // index into rungs
}

export function ladderCategoryOf(stat: string): LadderCategory | null {
  if (stat === 'rec_yds' || stat === 'rush_yds' || stat === 'pass_yds') return 'yardage'
  if (stat === 'receptions' || stat === 'pass_tds') return 'count'
  if (stat === 'anytime_td') return 'milestone'
  return null
}

function selectHero(over: LadderSide | null, under: LadderSide | null): LadderSide | null {
  if (!over && !under) return null
  if (!over) return under
  if (!under) return over
  if (over.verified && !under.verified) return over
  if (under.verified && !over.verified) return under
  if (over.verified && under.verified) {
    return (under.netEvPct ?? -Infinity) > (over.netEvPct ?? -Infinity) ? under : over
  }
  const overEv = over.netEvPct
  const underEv = under.netEvPct
  if (overEv != null && underEv != null) {
    if (underEv > overEv) return under
    return over
  }
  if (overEv != null) return over
  if (underEv != null) return under
  return over
}

function computePremiumPct(bars: LadderBookBar[]): number | null {
  const candidates: { book: string; dec: number }[] = []
  for (const b of bars) {
    if (b.over != null && isFinite(b.over) && b.over !== 0 && !b.overCap) {
      candidates.push({ book: b.book, dec: americanToDecimal(b.over) })
    }
  }
  if (candidates.length < 4) return null

  candidates.sort((a, b) => b.dec - a.dec)
  const best = candidates[0]
  const runnerUp = candidates[1]
  const peers = candidates.slice(1)
  const sumPeerDec = peers.reduce((sum, p) => sum + p.dec, 0)
  const avgPeerDec = sumPeerDec / peers.length

  if (best.dec >= avgPeerDec * 1.08 && best.dec >= runnerUp.dec * 1.03) {
    return ((best.dec / avgPeerDec) - 1) * 100
  }
  return null
}

function bestOverAmerican(bars: LadderBookBar[]): number | null {
  let bestDec = -Infinity
  let bestAm: number | null = null
  for (const b of bars) {
    if (b.over != null && isFinite(b.over) && b.over !== 0 && !b.overCap) {
      const dec = americanToDecimal(b.over)
      if (dec > bestDec) {
        bestDec = dec
        bestAm = b.over
      }
    }
  }
  return bestAm
}

function buildLadderSide(
  bestSide: LadderBestSide | null | undefined,
  side: 'OVER' | 'UNDER',
  row: BoardRow,
  bars: LadderBookBar[],
  freshestAny: number | null,
  category: LadderCategory
): LadderSide | null {
  if (!bestSide) return null

  const pConsLoo = bestSide.p_cons_loo
  let netEvPct: number | null = null
  if (pConsLoo != null) {
    const p = side === 'OVER' ? pConsLoo : 1 - pConsLoo
    netEvPct = (p * americanToDecimal(bestSide.american) - 1) * 100
  }

  const targetBar = bars.find(b => b.book === bestSide.book)

  let gateFail: string | null = null
  if (bestSide.n_ref_loo < LADDER_MIN_REF) {
    gateFail = 'refs'
  } else if (bestSide.disp_loo == null || bestSide.disp_loo > LADDER_DISP_MAX) {
    gateFail = 'disp'
  } else {
    // Gate 3: 'fresh'
    // Target book is fresh when ts is null or (freshestAny - ts) <= 30 min (freshestAny is max ts over all books at the rung).
    // If target book is two-sided and fresh, it contributed to row.n_fresh_ref and is subtracted for leave-one-out.
    const isTwoSided = targetBar ? targetBar.over != null && targetBar.under != null : false
    const targetTsMs = targetBar?.ts ? Date.parse(targetBar.ts) : null
    const isTsValid = targetTsMs != null && !isNaN(targetTsMs)
    const targetIsFresh = !isTsValid || freshestAny == null || ((freshestAny - targetTsMs) <= 30 * 60000)
    const targetContributedToFresh = isTwoSided && targetIsFresh
    const freshLoo = (row.n_fresh_ref ?? 0) - (targetContributedToFresh ? 1 : 0)

    if (freshLoo < LADDER_MIN_REF) {
      gateFail = 'fresh'
    } else if (targetBar?.staleOpener) {
      gateFail = 'stale_opener'
    } else if (targetBar?.lagMin != null && targetBar.lagMin > 30) {
      gateFail = 'lag'
    } else if (netEvPct == null || netEvPct < NOISE_FLOOR[category]) {
      gateFail = 'floor'
    }
  }

  return {
    side,
    book: bestSide.book,
    american: bestSide.american,
    netEvPct,
    pConsLoo: bestSide.p_cons_loo,
    dispLoo: bestSide.disp_loo,
    nRefLoo: bestSide.n_ref_loo,
    verified: gateFail === null,
    gateFail,
  }
}

function computeDefaultRung(rungs: LadderRung[], modalNow: number | null): number {
  if (rungs.length === 0) return 0
  const verifiedIdx = rungs.findIndex(r => r.verified)
  if (verifiedIdx !== -1) return verifiedIdx

  if (modalNow != null) {
    const modalIdx = rungs.findIndex(r => r.line === modalNow)
    if (modalIdx !== -1) return modalIdx
  }

  let bestIdx = 0
  for (let i = 1; i < rungs.length; i++) {
    const cur = rungs[i]
    const best = rungs[bestIdx]
    if (cur.nRef > best.nRef) {
      bestIdx = i
    } else if (cur.nRef === best.nRef) {
      if (modalNow != null) {
        const distCur = Math.abs(cur.line - modalNow)
        const distBest = Math.abs(best.line - modalNow)
        if (distCur < distBest) {
          bestIdx = i
        } else if (distCur === distBest && cur.line < best.line) {
          bestIdx = i
        }
      } else if (cur.line < best.line) {
        bestIdx = i
      }
    }
  }
  return bestIdx
}

export function groupLadderRows(rows: BoardRow[], nowMs: number): LadderGroup[] {
  const groupsMap = new Map<string, { category: LadderCategory; rows: BoardRow[] }>()

  for (const row of rows) {
    const cat = ladderCategoryOf(row.stat)
    if (!cat) continue
    const cMs = Date.parse(row.commence)
    if (isNaN(cMs) || cMs <= nowMs) continue

    const key = `${row.game_key}|${row.player_norm}|${row.stat}`
    let g = groupsMap.get(key)
    if (!g) {
      g = { category: cat, rows: [] }
      groupsMap.set(key, g)
    }
    g.rows.push(row)
  }

  const result: LadderGroup[] = []

  for (const [key, { category, rows: groupRows }] of groupsMap.entries()) {
    groupRows.sort((a, b) => a.line - b.line)
    const firstRow = groupRows[0]

    let hasAnyTwoSided = false
    const booksAny = new Set<string>()
    const books2sided = new Set<string>()

    for (const r of groupRows) {
      for (const b of r.books) {
        if (b.over != null || b.under != null) {
          booksAny.add(b.book)
        }
        if (b.over != null && b.under != null) {
          hasAnyTwoSided = true
          books2sided.add(b.book)
        }
      }
    }

    const kind: LadderKind = (category === 'milestone' || !hasAnyTwoSided) ? 'milestone' : 'curves'

    const rungs: LadderRung[] = []
    let prevBestOverAm: number | null = null

    for (let i = 0; i < groupRows.length; i++) {
      const row = groupRows[i]

      let freshest: number | null = null
      let freshestAny: number | null = null

      for (const b of row.books) {
        if (b.ts) {
          const t = Date.parse(b.ts)
          if (!isNaN(t)) {
            if (freshestAny == null || t > freshestAny) freshestAny = t
            if (b.over != null && b.under != null) {
              if (freshest == null || t > freshest) freshest = t
            }
          }
        }
      }

      const bars: LadderBookBar[] = row.books.map(b => {
        let lagMin: number | null = null
        if (freshest != null && b.ts) {
          const t = Date.parse(b.ts)
          if (!isNaN(t)) {
            lagMin = (freshest - t) / 60000
          }
        }
        let staleBand: 'fresh' | 'stale' | 'grey' = 'fresh'
        if (lagMin != null) {
          if (lagMin > 120) {
            staleBand = 'grey'
          } else if (lagMin > 30) {
            staleBand = 'stale'
          } else {
            staleBand = 'fresh'
          }
        }
        return {
          book: b.book,
          over: b.over,
          under: b.under,
          ts: b.ts ?? null,
          lagMin,
          staleBand,
          staleOpener: Boolean(b.stale_opener),
          overCap: Boolean(b.over_cap),
          underCap: Boolean(b.under_cap),
        }
      })

      const capsHidden = bars.reduce((acc, bar) => acc + (bar.overCap ? 1 : 0) + (bar.underCap ? 1 : 0), 0)

      const over = buildLadderSide(row.best_over, 'OVER', row, bars, freshestAny, category)
      const under = buildLadderSide(row.best_under, 'UNDER', row, bars, freshestAny, category)
      const hero = selectHero(over, under)

      const rungVerified = Boolean(over?.verified || under?.verified)
      const nRef = row.n_ref ?? 0
      const rungState: LadderState = rungVerified ? 'verified' : (nRef >= 2 ? 'market' : 'thin')

      const premiumPct = computePremiumPct(bars)

      const currBestOverAm = bestOverAmerican(bars)
      const flatRung = i > 0 && prevBestOverAm != null && currBestOverAm != null && currBestOverAm === prevBestOverAm
      prevBestOverAm = currBestOverAm

      rungs.push({
        line: row.line,
        row,
        books: bars,
        nRef,
        disp: row.disp ?? null,
        pCons: row.p_cons ?? null,
        rungVig: row.rung_vig ?? null,
        over,
        under,
        hero,
        verified: rungVerified,
        state: rungState,
        premiumPct,
        capsHidden,
        flatRung,
      })
    }

    const modalOpen = (groupRows.find(r => r.modal_open != null)?.modal_open) ?? firstRow.modal_open ?? null
    const modalNow = (groupRows.find(r => r.modal_point != null)?.modal_point) ?? firstRow.modal_point ?? null

    const grpBooksAny = firstRow.grp_books_any ?? booksAny.size
    const grpBooks2sided = firstRow.grp_books_2sided ?? books2sided.size

    const groupVerified = kind === 'curves' && rungs.some(r => r.verified)
    const groupPremium = rungs.some(r => r.premiumPct != null)
    const groupState: LadderState = groupVerified ? 'verified' : (rungs.some(r => r.state === 'market') ? 'market' : 'thin')

    const defaultRung = computeDefaultRung(rungs, modalNow)

    result.push({
      key,
      gameKey: firstRow.game_key,
      commence: firstRow.commence,
      away: firstRow.away,
      home: firstRow.home,
      player: firstRow.player,
      playerNorm: firstRow.player_norm,
      headshotId: firstRow.headshot_id ?? null,
      team: firstRow.team ?? null,
      stat: firstRow.stat,
      category,
      kind,
      rungs,
      modalOpen,
      modalNow,
      grpBooksAny,
      grpBooks2sided,
      verified: groupVerified,
      premium: groupPremium,
      state: groupState,
      defaultRung,
    })
  }

  return result
}

export function selectLadderFeed(
  groups: LadderGroup[],
  segment: LadderSegment,
  showAll: boolean
): LadderGroup[] {
  const filtered = groups.filter(g => {
    if (segment === 'curves' && g.kind !== 'curves') return false
    if (segment === 'milestones' && g.kind !== 'milestone') return false
    if (!showAll) {
      if (g.kind === 'curves' && !g.verified) return false
      if (g.kind === 'milestone' && !g.premium) return false
    }
    return true
  })

  return [...filtered].sort((a, b) => {
    const timeA = Date.parse(a.commence)
    const timeB = Date.parse(b.commence)
    if (timeA !== timeB) return timeA - timeB

    if (a.verified !== b.verified) return a.verified ? -1 : 1

    if (b.grpBooks2sided !== a.grpBooks2sided) return b.grpBooks2sided - a.grpBooks2sided

    const orderA = LADDER_MARKET_ORDER.indexOf(a.stat as any)
    const orderB = LADDER_MARKET_ORDER.indexOf(b.stat as any)
    const idxA = orderA === -1 ? 999 : orderA
    const idxB = orderB === -1 ? 999 : orderB
    if (idxA !== idxB) return idxA - idxB

    return a.player.localeCompare(b.player)
  })
}

export function ladderGroupFor(
  rows: BoardRow[],
  gameKey: string,
  playerNorm: string,
  stat: string,
  nowMs: number
): LadderGroup | null {
  const filtered = rows.filter(
    r => r.game_key === gameKey && r.player_norm === playerNorm && r.stat === stat
  )
  const groups = groupLadderRows(filtered, nowMs)
  return groups.length > 0 ? groups[0] : null
}

/**
 * Non-overlap guard (IA Q7 guard: a ladder group renders inline or in the mode, never duplicated within one view state).
 * In Alt Ladders mode the mode itself is the ladder surface, so the inspect sheet must not render a second copy of the same LadderCard.
 */
export function ladderInlineAllowed(mode: string): boolean {
  return mode !== 'longshots'
}
