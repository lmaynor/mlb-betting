'use client'

import { useMemo, useState } from 'react'
import { BookLogo } from '@/components/ui/book-logo'
import type { GameOddsFeed } from '@/lib/game-odds'
import { formatOdds } from '@/lib/odds'
import {
  bestThreeWayPrices, bestTotalPrices, evVsFair, gameRows, oddsOnlyGames, SOCCER_MODEL_BADGE, threeWayConsensus,
  type OddsOnlyGame, type SoccerPricedGame, type SoccerProjectionsFeed, type ThreeWayConsensus, type ThreeWayLeg,
} from '@/lib/soccer-games'
import { bookDisplay } from '@/lib/tokens'
import { GREEN, HAIR, pctText, Pill, PillGroup } from './game-prices-parts'

type Tab = '1x2' | 'totals' | 'btts'
const TABS: { key: Tab; label: string }[] = [
  { key: '1x2', label: '1X2 (Home | Draw (Tie) | Away)' },
  { key: 'totals', label: 'Goals Totals' },
  { key: 'btts', label: 'BTTS' },
]
const TOTAL_LADDER = ['1.5', '2', '2.5', '3', '3.5', '4.5']
const LEGS: ThreeWayLeg[] = ['home', 'draw', 'away']

function price(p: number | null | undefined): string {
  return p == null ? '--' : formatOdds(p)
}

function evText(ev: number | null | undefined): string {
  return ev == null ? '' : ` (${ev >= 0 ? '+' : ''}${(ev * 100).toFixed(1)}%)`
}

/** Book identity cell: logo + display label, never the raw lowercase feed key. */
function BookCell({ book }: { book: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <BookLogo book={book} size={18} />
      <span>{bookDisplay(book).label}</span>
    </span>
  )
}

/** The 1X2 book table: every book's three prices with its own power de-vig, the best price per leg highlighted,
 *  the best book's EV against the consensus, and (when `model` is given) the model row under the consensus. */
function OneXTwoTable({ consensus, homeName, awayName, model }: {
  consensus: ThreeWayConsensus
  homeName: string
  awayName: string
  model: { p_home: number; p_draw: number; p_away: number } | null
}) {
  const best = bestThreeWayPrices(consensus)
  const legFair = (leg: ThreeWayLeg): number | null =>
    consensus.fair == null ? null : consensus.fair[leg === 'home' ? 0 : leg === 'draw' ? 1 : 2]
  const bestCell = (leg: ThreeWayLeg) => {
    const b = best[leg]
    const f = legFair(leg)
    if (!b) return '--'
    return `${price(b.price)} ${bookDisplay(b.book).label}${f == null ? '' : evText(evVsFair(b.price, f))}`
  }
  return (
    <table className="w-full border-collapse">
      <thead>
        <tr className="text-left mono text-[11px] text-neutral-300">
          <th scope="col" className="py-1 pr-3 font-semibold">BOOK</th>
          <th scope="col" className="py-1 pr-3 font-semibold">{homeName} (HOME)</th>
          <th scope="col" className="py-1 pr-3 font-semibold">DRAW (TIE)</th>
          <th scope="col" className="py-1 pr-3 font-semibold">{awayName} (AWAY)</th>
          <th scope="col" className="py-1 font-semibold">FAIR H / D / A (POWER DE-VIG)</th>
        </tr>
      </thead>
      <tbody>
        {consensus.books.map(b => (
          <tr key={b.book} className="border-t" style={{ borderColor: HAIR }}>
            <td className="mono py-[5px] pr-3 text-compact text-text-primary"><BookCell book={b.book} /></td>
            {LEGS.map(leg => {
              const isBest = best[leg] != null && b[leg] === best[leg]!.price
              return (
                <td key={leg} className="mono py-[5px] pr-3 text-compact"
                  style={isBest ? { color: GREEN, fontWeight: 800 } : undefined}>{price(b[leg])}</td>
              )
            })}
            <td className="mono py-[5px] text-compact text-text-secondary">
              {b.fair ? `${pctText(b.fair[0])} / ${pctText(b.fair[1])} / ${pctText(b.fair[2])}` : '--'}
            </td>
          </tr>
        ))}
        <tr className="border-t" style={{ borderColor: HAIR }}>
          <td className="mono py-[5px] pr-3 text-compact font-bold" style={{ color: GREEN }}>BEST PRICE (EV VS CONSENSUS)</td>
          {LEGS.map(leg => (
            <td key={leg} className="mono py-[5px] pr-3 text-compact font-bold" style={{ color: GREEN }}>{bestCell(leg)}</td>
          ))}
          <td className="mono py-[5px] text-compact"></td>
        </tr>
        <tr className="border-t" style={{ borderColor: HAIR }}>
          <td className="mono py-[5px] pr-3 text-compact font-bold text-text-primary">CONSENSUS ({consensus.nBooks} BOOKS)</td>
          <td className="mono py-[5px] pr-3 text-compact" colSpan={3}></td>
          <td className="mono py-[5px] text-compact font-bold text-text-primary">
            {consensus.fair ? `${pctText(consensus.fair[0])} / ${pctText(consensus.fair[1])} / ${pctText(consensus.fair[2])}` : '--'}
          </td>
        </tr>
        {model && (
          <tr className="border-t" style={{ borderColor: HAIR }}>
            <td className="mono py-[5px] pr-3 text-compact font-bold text-text-primary">MODEL (DIXON-COLES)</td>
            <td className="mono py-[5px] pr-3 text-compact" colSpan={3}></td>
            <td className="mono py-[5px] text-compact font-bold text-text-primary">
              {pctText(model.p_home)} / {pctText(model.p_draw)} / {pctText(model.p_away)}
            </td>
          </tr>
        )}
      </tbody>
    </table>
  )
}

/** The expanded per-game deep dive: the 1X2 tab (every book's three prices, its own power de-vig, the consensus,
 *  and Kalshi's three contracts), the matrix totals ladder, and BTTS. All maths runs client-side off the raw
 *  board prices (devigThreeWay) and the model's stored matrix markets. */
function GameDeepDive({ g, odds }: { g: SoccerPricedGame; odds: GameOddsFeed | null }) {
  const [tab, setTab] = useState<Tab>('1x2')
  const rows = useMemo(() => gameRows(odds, g.game_key), [odds, g.game_key])
  const consensus = useMemo(() => threeWayConsensus(rows.ml, 'power'), [rows.ml])
  return (
    <div className="py-3 px-[14px]">
      <div className="flex items-center gap-x-[14px] flex-wrap mb-[10px]">
        <PillGroup label="Market">
          {TABS.map(t => <Pill key={t.key} active={tab === t.key} onClick={() => setTab(t.key)}>{t.label}</Pill>)}
        </PillGroup>
        <span className="mono text-[11px] text-neutral-300">
          {'\u03bb'} home {g.lambda_home.toFixed(2)} {'\u00b7'} {'\u03bb'} away {g.lambda_away.toFixed(2)} {'\u00b7'} {'\u03c1'} {g.rho.toFixed(2)}
        </span>
        {g.sanity_warn && (
          <span className="mono text-[10.5px] font-bold tracking-fine text-warn border border-[color:#facc1555] rounded-chip py-0 px-[5px]">{g.sanity_warn}</span>
        )}
      </div>

      {tab === '1x2' && (
        <div>
          <OneXTwoTable consensus={consensus} homeName={g.home} awayName={g.away} model={g} />
          <p className="text-compact text-text-secondary mt-2 leading-[1.5]">
            Kalshi lists this match as three contracts on the same result: {g.home} wins, TIE (the match ends in a
            draw), {g.away} wins, each settling yes / no on the 90-minute result. The consensus above is the
            sportsbook reference those contracts are compared against.
          </p>
        </div>
      )}

      {tab === 'totals' && (
        <table className="w-full border-collapse">
          <thead>
            <tr className="text-left mono text-[11px] text-neutral-300">
              <th scope="col" className="py-1 pr-3 font-semibold">LINE</th>
              <th scope="col" className="py-1 pr-3 font-semibold">MODEL OVER</th>
              <th scope="col" className="py-1 pr-3 font-semibold">MODEL UNDER</th>
              <th scope="col" className="py-1 pr-3 font-semibold">PUSH</th>
              <th scope="col" className="py-1 pr-3 font-semibold">BEST OVER</th>
              <th scope="col" className="py-1 font-semibold">BEST UNDER</th>
            </tr>
          </thead>
          <tbody>
            {TOTAL_LADDER.map(k => {
              const t = g.totals[k]
              if (!t) return null
              const bestT = bestTotalPrices(rows.totals, parseFloat(k))
              return (
                <tr key={k} className="border-t" style={{ borderColor: HAIR }}>
                  <td className="mono py-[5px] pr-3 text-compact text-text-primary">O/U {k}</td>
                  <td className="mono py-[5px] pr-3 text-compact">{t.p_over == null ? '--' : pctText(t.p_over)}</td>
                  <td className="mono py-[5px] pr-3 text-compact">{t.p_under == null ? '--' : pctText(t.p_under)}</td>
                  <td className="mono py-[5px] pr-3 text-compact text-text-secondary">{t.p_push > 0 ? pctText(t.p_push) : '--'}</td>
                  <td className="mono py-[5px] pr-3 text-compact" style={bestT.over ? { color: GREEN, fontWeight: 800 } : undefined}>
                    {bestT.over ? `${price(bestT.over.price)} ${bookDisplay(bestT.over.book).label}` : '--'}
                  </td>
                  <td className="mono py-[5px] text-compact" style={bestT.under ? { color: GREEN, fontWeight: 800 } : undefined}>
                    {bestT.under ? `${price(bestT.under.price)} ${bookDisplay(bestT.under.book).label}` : '--'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {tab === 'btts' && (
        <div className="mono text-compact text-text-primary">
          <div>Model: Yes {pctText(g.p_btts_yes)} {'\u00b7'} No {pctText(g.p_btts_no)}</div>
          {rows.btts && (
            <table className="w-full border-collapse mt-2">
              <thead>
                <tr className="text-left mono text-[11px] text-neutral-300">
                  <th scope="col" className="py-1 pr-3 font-semibold">BOOK</th>
                  <th scope="col" className="py-1 pr-3 font-semibold">YES</th>
                  <th scope="col" className="py-1 font-semibold">NO</th>
                </tr>
              </thead>
              <tbody>
                {rows.btts.books.map(b => (
                  <tr key={b.book} className="border-t" style={{ borderColor: HAIR }}>
                    <td className="mono py-[5px] pr-3 text-compact text-text-primary"><BookCell book={b.book} /></td>
                    <td className="mono py-[5px] pr-3 text-compact">{price(b.a)}</td>
                    <td className="mono py-[5px] text-compact">{price(b.b)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="text-text-secondary mt-1">
            Double chance from the same matrix: 1X {pctText(g.p_1x)} {'\u00b7'} X2 {pctText(g.p_x2)} {'\u00b7'} 12 {pctText(g.p_12)}
            {'  \u00b7  Draw no bet: '}{g.home} {g.p_home_dnb == null ? '--' : pctText(g.p_home_dnb)} {'\u00b7'} {g.away} {g.p_away_dnb == null ? '--' : pctText(g.p_away_dnb)}
          </div>
        </div>
      )}
    </div>
  )
}

/** Fallback board: the model has priced no fixtures yet, but the odds feed has live soccer rows. The games are
 *  synthesized from the odds feed alone (no model columns exist for them), so the live three-way prices, the
 *  consensus and the best prices stay inspectable on /ev/games?sport=soccer. */
function OddsOnlyBoard({ games, open, setOpen }: {
  games: OddsOnlyGame[]
  open: string | null
  setOpen: (k: string | null) => void
}) {
  return (
    <div>
      <div role="status" className="py-2 px-0 text-neutral-300 text-compact">
        The Dixon-Coles scorer has not priced fixtures yet; showing the live sportsbook prices from the odds feed.
      </div>
      <div className="odds-table-wrap bg-background-primary-default" style={{ border: `1px solid ${HAIR}`, borderRadius: '8px', boxShadow: '0 1px 2px rgba(0,0,0,0.45)' }}>
        <table className="odds-table w-full border-collapse">
          <thead>
            <tr>
              <th scope="col" className="odds-prop">GAME</th>
              <th scope="col" className="odds-ev">BOOKS FAIR (H / D / A)</th>
              <th scope="col" className="odds-ev">BEST HOME</th>
              <th scope="col" className="odds-ev">BEST DRAW (TIE)</th>
              <th scope="col" className="odds-ev">BEST AWAY</th>
              <th scope="col" className="odds-ev"></th>
            </tr>
          </thead>
          <tbody>
            {games.map(g => {
              const best = bestThreeWayPrices(g.consensus)
              const isOpen = open === g.game_key
              return [
                <tr key={g.game_key}>
                  <td className="mono text-compact">
                    <span className="text-text-primary font-semibold">{g.away} @ {g.home}</span>
                    <span className="text-text-secondary"> {'\u00b7'} {g.start.slice(0, 10)} {g.start.slice(11, 16)} UTC</span>
                  </td>
                  <td className="mono text-compact">{g.consensus.fair ? `${pctText(g.consensus.fair[0])} / ${pctText(g.consensus.fair[1])} / ${pctText(g.consensus.fair[2])}` : '--'}</td>
                  <td className="mono text-compact">{best.home ? price(best.home.price) : '--'}</td>
                  <td className="mono text-compact">{best.draw ? price(best.draw.price) : '--'}</td>
                  <td className="mono text-compact">{best.away ? price(best.away.price) : '--'}</td>
                  <td className="mono text-compact">
                    <button type="button" className="btn btn-ghost text-compact" aria-expanded={isOpen}
                      onClick={() => setOpen(isOpen ? null : g.game_key)}>{isOpen ? 'Close' : 'Deep dive'}</button>
                  </td>
                </tr>,
                isOpen ? (
                  <tr key={`${g.game_key}-detail`}>
                    <td colSpan={6} style={{ padding: 0 }}>
                      <div className="py-3 px-[14px]">
                        <OneXTwoTable consensus={g.consensus} homeName={g.home} awayName={g.away} model={null} />
                        <p className="text-compact text-text-secondary mt-2 leading-[1.5]">
                          Kalshi lists this match as three contracts on the same result: {g.home} wins, TIE (the match
                          ends in a draw), {g.away} wins, each settling yes / no on the 90-minute result. The consensus
                          above is the sportsbook reference those contracts are compared against.
                        </p>
                      </div>
                    </td>
                  </tr>
                ) : null,
              ]
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** Soccer (EPL / UCL) for /ev/games: the Dixon-Coles model board with a per-game three-way deep dive. */
export function SoccerGamesClient({ projections, odds }: { projections: SoccerProjectionsFeed | null; odds: GameOddsFeed | null }) {
  const [open, setOpen] = useState<string | null>(null)
  const games = projections?.priced_games ?? []
  const oddsGames = useMemo(() => oddsOnlyGames(odds), [odds])
  const showOddsOnly = games.length === 0 && oddsGames.length > 0

  return (
    <div>
      <div className="flex items-center gap-y-[6px] gap-x-[14px] flex-wrap min-h-[38px] py-[6px] px-[14px] mb-[10px] bg-background-primary-default text-compact" style={{ border: `1px solid ${HAIR}`, borderRadius: '8px' }}>
        <span className="mono font-bold text-text-primary">SOCCER (EPL / UCL)</span>
        <span className="mono text-neutral-300">{games.length > 0 ? games.length : oddsGames.length} GAMES</span>
        <span className="mono text-[10.5px] font-bold tracking-fine text-warn border border-[color:#facc1555] rounded-chip py-0 px-[5px]">UNVALIDATED</span>
        <span className="mono text-neutral-300">{SOCCER_MODEL_BADGE}</span>
      </div>

      {games.length === 0 && !showOddsOnly ? (
        <div role="status" className="py-6 px-0 text-neutral-300 text-cozy">
          No soccer fixtures are priced yet. Premier League and Champions League games appear once the Dixon-Coles
          scorer has run against the posted slate.
        </div>
      ) : showOddsOnly ? (
        <OddsOnlyBoard games={oddsGames} open={open} setOpen={setOpen} />
      ) : (
        <div className="odds-table-wrap bg-background-primary-default" style={{ border: `1px solid ${HAIR}`, borderRadius: '8px', boxShadow: '0 1px 2px rgba(0,0,0,0.45)' }}>
          <table className="odds-table w-full border-collapse">
            <thead>
              <tr>
                <th scope="col" className="odds-prop">GAME</th>
                <th scope="col" className="odds-ev">MODEL 1X2 (H / D / A)</th>
                <th scope="col" className="odds-ev">BOOKS FAIR (H / D / A)</th>
                <th scope="col" className="odds-ev">MODEL BTTS YES</th>
                <th scope="col" className="odds-ev">MODEL O2.5</th>
                <th scope="col" className="odds-ev"></th>
              </tr>
            </thead>
            <tbody>
              {games.map(g => {
                const rows = gameRows(odds, g.game_key)
                const consensus = threeWayConsensus(rows.ml, 'power')
                const isOpen = open === g.game_key
                return [
                  <tr key={g.game_key}>
                    <td className="mono text-compact">
                      <span className="text-text-primary font-semibold">{g.away} @ {g.home}</span>
                      <span className="text-text-secondary"> {'\u00b7'} {g.league.toUpperCase()} {'\u00b7'} {g.game_date}</span>
                    </td>
                    <td className="mono text-compact">{pctText(g.p_home)} / {pctText(g.p_draw)} / {pctText(g.p_away)}</td>
                    <td className="mono text-compact">{consensus.fair ? `${pctText(consensus.fair[0])} / ${pctText(consensus.fair[1])} / ${pctText(consensus.fair[2])}` : '--'}</td>
                    <td className="mono text-compact">{pctText(g.p_btts_yes)}</td>
                    <td className="mono text-compact">{g.totals['2.5']?.p_over == null ? '--' : pctText(g.totals['2.5'].p_over)}</td>
                    <td className="mono text-compact">
                      <button type="button" className="btn btn-ghost text-compact" aria-expanded={isOpen}
                        onClick={() => setOpen(isOpen ? null : g.game_key)}>{isOpen ? 'Close' : 'Deep dive'}</button>
                    </td>
                  </tr>,
                  isOpen ? (
                    <tr key={`${g.game_key}-detail`}>
                      <td colSpan={6} style={{ padding: 0 }}>
                        <GameDeepDive g={g} odds={odds} />
                      </td>
                    </tr>
                  ) : null,
                ]
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-compact text-text-secondary mt-3 leading-[1.5]">
        {SOCCER_MODEL_BADGE}: the model probabilities come from one 11x11 Dixon-Coles scoreline matrix per game
        (attack / defense ratings fit on Understat match xG with a ClubElo anchor), so 1X2, totals, BTTS and double
        chance can never contradict each other. Fair values de-vig each book&apos;s three prices together (power
        method). Nothing here is a tracked pick and no soccer system has passed its validation gate.
      </p>
    </div>
  )
}
