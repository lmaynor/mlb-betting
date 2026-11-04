/**
 * The Game Lines board (/ev/games): every game as one entry with its main moneyline / spread / total, every alternate rung, the
 * model's view of each, and the fields the cards and the deep-dive read. Pure functions over the two feeds (the price feed and
 * the model feed), so the page logic is pinned by tests and the components only draw.
 *
 * Model probabilities come from the feed's per-rung `ladder` when it has one. An older feed (no ladder) falls back to a normal
 * curve around `mu_margin` / `sigma_margin` / `mu_total` / `sigma_total` when those exist (NBA); with neither, a rung has no
 * model probability and the chart draws no model curve for it, rather than a guess. Everything here is UNVALIDATED.
 */
import { orderRungs, mainLineOf, type LadderSide } from '@/lib/game-ladder'
import type { GameMetadata, GameModelLadder, GamePricedGame, GameProjectionsFeed } from '@/lib/game-lines'
import { moveScore, summarizeMove, type MoveSummary } from '@/lib/line-move'
import { buildGamePriceRows, feedRows, sideLabels, type TwoWayMarket, type GameOddsFeed, type GamePriceOptions, type GamePriceRow } from '@/lib/game-odds'

const MARKETS: TwoWayMarket[] = ['ml', 'spread', 'total']

/** Standard normal CDF (Abramowitz-Stegun 26.2.17, |error| < 7.5e-8). */
export function normalCdf(x: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(x))
  const d = 0.3989423 * Math.exp((-x * x) / 2)
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
  return x > 0 ? 1 - p : p
}

export interface GameDriver { label: string; value: string }

export interface GameModel {
  engine: string | null
  /** projected score (home, away) */
  projHome: number | null
  projAway: number | null
  /** home minus away */
  margin: number | null
  total: number | null
  pHome: number | null
  pAway: number | null
  ladder: GameModelLadder | null
  normal: { mu: number; sigma: number; muTotal: number | null; sigmaTotal: number | null } | null
  drivers: GameDriver[]
}

export interface GameBoardEntry {
  key: string
  sport: string
  away: string
  home: string
  start: string
  rows: Record<TwoWayMarket, GamePriceRow[]>
  main: Record<TwoWayMarket, GamePriceRow | null>
  /** most books on any main line */
  nBooks: number
  /** best EV (either side, MAIN lines only: an alt rung's EV never headlines a card) */
  bestEv: number | null
  arbLive: boolean
  /** largest model-minus-market probability on a main line (UNVALIDATED), null with no model */
  modelGap: number | null
  model: GameModel | null
  /** movement since the board first saw the game, per market (null = fewer than two snapshots) */
  move: Record<TwoWayMarket, MoveSummary | null>
  /** the price feed carries history at all (an older backend does not, and then nothing about movement is shown) */
  hasHistory: boolean
  moveScore: number
}

const near = (a: number, b: number) => Math.abs(a - b) < 1e-6

function pushNorm(pa: number, pb: number): [number, number] | null {
  const t = pa + pb
  return t > 0 ? [pa / t, pb / t] : null
}

/**
 * The model's probability for one side of one rung, push-adjusted so it is comparable to a de-vigged two-way price.
 * `side` follows the price rows: 'a' = away / over, 'b' = home / under. Null when the model has nothing for the rung.
 */
export function modelSideProb(model: GameModel | null, market: TwoWayMarket, line: number | null, side: LadderSide): number | null {
  if (!model) return null
  const L = model.ladder
  if (market === 'ml') {
    if (L?.ml) { const n = pushNorm(L.ml.p_away, L.ml.p_home); if (n) return side === 'a' ? n[0] : n[1] }
    if (model.normal) { const ph = normalCdf(model.normal.mu / model.normal.sigma); return side === 'a' ? 1 - ph : ph }
    return null
  }
  if (line == null) return null
  if (market === 'spread') {
    const r = L?.spread?.find(x => near(x.line, line))
    if (r) { const n = pushNorm(r.p_away, r.p_home); if (n) return side === 'a' ? n[0] : n[1] }
    if (model.normal) { const ph = normalCdf((model.normal.mu + line) / model.normal.sigma); return side === 'a' ? 1 - ph : ph }   // a push has no mass in a continuous curve
    return null
  }
  const r = L?.total?.find(x => near(x.line, line))
  if (r) { const n = pushNorm(r.p_over, r.p_under); if (n) return side === 'a' ? n[0] : n[1] }
  const nm = model.normal
  if (nm && nm.muTotal != null && nm.sigmaTotal) { const po = 1 - normalCdf((line - nm.muTotal) / nm.sigmaTotal); return side === 'a' ? po : 1 - po }
  return null
}

const num = (x: unknown): number | null => (typeof x === 'number' && isFinite(x) ? x : null)

/** The model summary of one game: projected score, win probability and the drivers that exist in the payload. */
export function buildGameModel(sport: string, away: string, home: string, priced: GamePricedGame | undefined, meta: GameMetadata | undefined, engine: string | null): GameModel | null {
  if (!priced && !meta) return null
  const lh = num(priced?.lambda_h) ?? num(meta?.lambda_h)
  const la = num(priced?.lambda_a) ?? num(meta?.lambda_a)
  const muM = num(priced?.mu_margin) ?? num(meta?.mu_margin)
  const muT = num(priced?.mu_total) ?? num(priced?.exp_total) ?? num(meta?.mu_total) ?? num(meta?.exp_total)
  const sigM = num(priced?.sigma_margin)
  const sigT = num(priced?.sigma_total)
  const pace = num(priced?.exp_pace) ?? num(meta?.exp_pace)

  let projHome: number | null = null
  let projAway: number | null = null
  if (lh != null && la != null) { projHome = lh; projAway = la }
  else if (muM != null && muT != null) { projHome = (muT + muM) / 2; projAway = (muT - muM) / 2 }
  const margin = muM ?? (projHome != null && projAway != null ? projHome - projAway : null)
  const total = muT ?? (projHome != null && projAway != null ? projHome + projAway : null)

  const normal = muM != null && sigM != null && sigM > 0 ? { mu: muM, sigma: sigM, muTotal: muT, sigmaTotal: sigT } : null
  const ladder = priced?.ladder ?? null
  let pHome: number | null = null
  if (ladder?.ml) pHome = pushNorm(ladder.ml.p_away, ladder.ml.p_home)?.[1] ?? null
  else if (normal) pHome = normalCdf(normal.mu / normal.sigma)

  const drivers: GameDriver[] = []
  if (lh != null && la != null) drivers.push({ label: 'Expected goals', value: `${away} ${la.toFixed(2)} – ${home} ${lh.toFixed(2)}` })
  if (pace != null) drivers.push({ label: 'Expected pace', value: `${pace.toFixed(1)} poss` })
  const ag = meta?.away_goalie, hg = meta?.home_goalie
  if (ag || hg) drivers.push({ label: 'Goalies', value: `${ag ?? '?'} / ${hg ?? '?'}` })
  if (meta?.is_b2b_road === true) drivers.push({ label: 'Rest', value: `${away} on the second night of a back-to-back` })
  const rh = num(meta?.rest_home), ra = num(meta?.rest_away)
  if (rh != null && ra != null) drivers.push({ label: 'Rest days', value: `${away} ${ra} \u00b7 ${home} ${rh}` })
  if (meta?.b2b_away === true || meta?.b2b_home === true) {
    drivers.push({ label: 'Back-to-back', value: [meta?.b2b_away === true ? away : null, meta?.b2b_home === true ? home : null].filter(Boolean).join(' and ') })
  }
  const pl = num(meta?.away_points_lost), hl = num(meta?.home_points_lost)
  if ((pl != null && pl > 0) || (hl != null && hl > 0)) drivers.push({ label: 'Injuries (pts lost)', value: `${away} ${(pl ?? 0).toFixed(1)} \u00b7 ${home} ${(hl ?? 0).toFixed(1)}` })
  return { engine, projHome, projAway, margin, total, pHome, pAway: pHome == null ? null : 1 - pHome, ladder, normal, drivers }
}

export interface BoardFeeds {
  odds: Record<string, GameOddsFeed | null | undefined>
  proj: Record<string, GameProjectionsFeed | null | undefined>
}

/** Every game with prices, as board entries (unsorted). `opts` is the reference-book / de-vig setup of the price maths. */
export function buildGameBoard(feeds: BoardFeeds, opts: GamePriceOptions): GameBoardEntry[] {
  const sports = Object.keys(feeds.odds).filter(s => feeds.odds[s])
  const rows = sports.flatMap(s => feedRows(feeds.odds[s]))
  const priced = buildGamePriceRows(rows, opts)

  const byGame = new Map<string, GamePriceRow[]>()
  for (const r of priced) byGame.set(r.row.game_key, [...(byGame.get(r.row.game_key) ?? []), r])

  const out: GameBoardEntry[] = []
  for (const [key, list] of byGame) {
    const first = list[0].row
    const sport = first.sport
    const proj = feeds.proj[sport]
    const pg = proj?.priced_games?.find(g => g.game_key === key)
    const meta = proj?.games?.find(g => g.game_key === key)
    const model = buildGameModel(sport, first.away, first.home, pg, meta, typeof proj?.engine === 'string' ? proj.engine : null)

    const rowsBy = { ml: [] as GamePriceRow[], spread: [] as GamePriceRow[], total: [] as GamePriceRow[] }
    for (const r of list) if (r.row.market !== 'btts') rowsBy[r.row.market].push(r)
    const main = { ml: null as GamePriceRow | null, spread: null as GamePriceRow | null, total: null as GamePriceRow | null }
    for (const m of MARKETS) {
      rowsBy[m] = orderRungs(rowsBy[m].map(r => ({ line: r.row.line, r })), m, 'b').map(x => x.r)
      if (m === 'ml') main.ml = rowsBy.ml[0] ?? null
      else { const ml = mainLineOf(rowsBy[m].map(r => r.row)); main[m] = rowsBy[m].find(r => r.row.line === ml) ?? null }
    }

    const mains = MARKETS.map(m => main[m]).filter((r): r is GamePriceRow => !!r)
    const evs = mains.map(r => r.bestEv).filter((x): x is number => x != null)
    let gap: number | null = null
    for (const r of mains) {
      if (r.row.market === 'btts') continue
      for (const side of ['a', 'b'] as const) {
        const pm = modelSideProb(model, r.row.market, r.row.line, side)
        const pf = (side === 'a' ? r.a : r.b).pFair
        if (pm != null && pf != null) gap = Math.max(gap ?? -Infinity, pm - pf)
      }
    }
    const hist = feeds.odds[sport]?.history
    const move = { ml: null as MoveSummary | null, spread: null as MoveSummary | null, total: null as MoveSummary | null }
    for (const m of MARKETS) move[m] = summarizeMove(hist?.[`${key}|${m}`])
    out.push({
      key, sport, away: first.away, home: first.home, start: first.commence, rows: rowsBy, main,
      move, hasHistory: hist != null, moveScore: moveScore([move.ml, move.spread, move.total]),
      nBooks: Math.max(0, ...mains.map(r => r.row.books.length)),
      bestEv: evs.length ? Math.max(...evs) : null,
      arbLive: list.some(r => r.arbLive),
      modelGap: gap,
      model,
    })
  }
  return out
}

export type BoardSort = 'time' | 'ev' | 'arb' | 'gap' | 'move'

export const BOARD_SORTS: { v: BoardSort; l: string; title: string }[] = [
  { v: 'time', l: 'Start time', title: 'Earliest game first' },
  { v: 'ev', l: 'Best EV', title: 'Best EV on a main line, against the market fair price' },
  { v: 'arb', l: 'Arbitrage', title: 'Games with a live arbitrage first, biggest locked margin first' },
  { v: 'gap', l: 'Model gap', title: 'Biggest gap between our model and the market fair price (unvalidated)' },
  { v: 'move', l: 'Movement', title: 'Games whose line or fair price has moved the most since the board first saw them' },
]

export function sortBoard(games: GameBoardEntry[], sort: BoardSort): GameBoardEntry[] {
  const out = games.slice()
  const t = (g: GameBoardEntry) => g.start ?? ''
  const nullsLast = (a: number | null, b: number | null) => (b ?? -Infinity) - (a ?? -Infinity)
  const arbMargin = (g: GameBoardEntry) => Math.max(0, ...[...g.rows.ml, ...g.rows.spread, ...g.rows.total].filter(r => r.arbLive && r.arb).map(r => r.arb!.netArbPct))
  if (sort === 'ev') out.sort((a, b) => nullsLast(a.bestEv, b.bestEv) || t(a).localeCompare(t(b)))
  else if (sort === 'move') out.sort((a, b) => b.moveScore - a.moveScore || t(a).localeCompare(t(b)))
  else if (sort === 'gap') out.sort((a, b) => nullsLast(a.modelGap, b.modelGap) || t(a).localeCompare(t(b)))
  else if (sort === 'arb') out.sort((a, b) => arbMargin(b) - arbMargin(a) || t(a).localeCompare(t(b)))
  else out.sort((a, b) => t(a).localeCompare(t(b)) || a.key.localeCompare(b.key))
  return out
}

/** Games for the sport pills: a sport is offered only when its feed has at least one game with prices. */
export function sportsWithGames(games: readonly GameBoardEntry[]): string[] {
  return [...new Set(games.map(g => g.sport))]
}

/** The model feed limited to the games the (access-cut) price feed still carries, so a withheld game never rides along in `priced_games` or `games`. */
export function restrictProjToOdds(proj: GameProjectionsFeed | null, odds: GameOddsFeed | null): GameProjectionsFeed | null {
  if (!proj) return null
  const keep = new Set((odds?.rows ?? []).map(r => r.game_key))
  return { ...proj, priced_games: (proj.priced_games ?? []).filter(g => keep.has(g.game_key)), games: (proj.games ?? []).filter(g => keep.has(g.game_key)) }
}

export interface GameLean {
  market: TwoWayMarket
  side: LadderSide
  /** 'FLA -1.5', 'O 6.5', 'DET ML' */
  label: string
  line: number | null
  pModel: number
  pMarket: number
  /** model minus market fair probability: UNVALIDATED, never an edge claim */
  gap: number
  bestBook: string | null
  bestAmerican: number | null
  /** EV of the best price against the market fair value (a market fact) */
  evMarket: number | null
}

/** The side of a main line where our model is furthest above the market fair price: the game's "lean". Null when the model has no view or never exceeds the market. */
export function leanOf(g: GameBoardEntry): GameLean | null {
  let best: GameLean | null = null
  for (const m of MARKETS) {
    const r = g.main[m]
    if (!r) continue
    for (const side of ['a', 'b'] as const) {
      const sp = side === 'a' ? r.a : r.b
      const pm = modelSideProb(g.model, m, r.row.line, side)
      if (pm == null || sp.pFair == null) continue
      const gap = pm - sp.pFair
      if (gap > 0 && (!best || gap > best.gap)) {
        best = { market: m, side, label: sideLabels(r.row)[side], line: r.row.line, pModel: pm, pMarket: sp.pFair, gap, bestBook: sp.book, bestAmerican: sp.american, evMarket: sp.ev }
      }
    }
  }
  return best
}
