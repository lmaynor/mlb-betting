'use client'

import { useMemo, useState } from 'react'
import type { GameOddsFeed } from '@/lib/game-odds'
import { formatOdds } from '@/lib/odds'
import { gameRows, SOCCER_MODEL_BADGE, threeWayConsensus, type SoccerPricedGame, type SoccerProjectionsFeed } from '@/lib/soccer-games'
import { HAIR, pctText, Pill, PillGroup } from './game-prices-parts'

type Tab = '1x2' | 'totals' | 'btts'
const TABS: { key: Tab; label: string }[] = [
  { key: '1x2', label: '1X2 (Home | Draw (Tie) | Away)' },
  { key: 'totals', label: 'Goals Totals' },
  { key: 'btts', label: 'BTTS' },
]
const TOTAL_LADDER = ['1.5', '2', '2.5', '3', '3.5', '4.5']

function price(p: number | null | undefined): string {
  return p == null ? '--' : formatOdds(p)
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
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-left mono text-[11px] text-neutral-300">
                <th scope="col" className="py-1 pr-3 font-semibold">BOOK</th>
                <th scope="col" className="py-1 pr-3 font-semibold">{g.home} (HOME)</th>
                <th scope="col" className="py-1 pr-3 font-semibold">DRAW (TIE)</th>
                <th scope="col" className="py-1 pr-3 font-semibold">{g.away} (AWAY)</th>
                <th scope="col" className="py-1 font-semibold">FAIR H / D / A (POWER DE-VIG)</th>
              </tr>
            </thead>
            <tbody>
              {consensus.books.map(b => (
                <tr key={b.book} className="border-t" style={{ borderColor: HAIR }}>
                  <td className="mono py-[5px] pr-3 text-compact text-text-primary">{b.book}</td>
                  <td className="mono py-[5px] pr-3 text-compact">{price(b.home)}</td>
                  <td className="mono py-[5px] pr-3 text-compact">{price(b.draw)}</td>
                  <td className="mono py-[5px] pr-3 text-compact">{price(b.away)}</td>
                  <td className="mono py-[5px] text-compact text-text-secondary">
                    {b.fair ? `${pctText(b.fair[0])} / ${pctText(b.fair[1])} / ${pctText(b.fair[2])}` : '--'}
                  </td>
                </tr>
              ))}
              <tr className="border-t" style={{ borderColor: HAIR }}>
                <td className="mono py-[5px] pr-3 text-compact font-bold text-text-primary">CONSENSUS ({consensus.nBooks} BOOKS)</td>
                <td className="mono py-[5px] pr-3 text-compact" colSpan={3}></td>
                <td className="mono py-[5px] text-compact font-bold text-text-primary">
                  {consensus.fair ? `${pctText(consensus.fair[0])} / ${pctText(consensus.fair[1])} / ${pctText(consensus.fair[2])}` : '--'}
                </td>
              </tr>
              <tr className="border-t" style={{ borderColor: HAIR }}>
                <td className="mono py-[5px] pr-3 text-compact font-bold text-text-primary">MODEL (DIXON-COLES)</td>
                <td className="mono py-[5px] pr-3 text-compact" colSpan={3}></td>
                <td className="mono py-[5px] text-compact font-bold text-text-primary">
                  {pctText(g.p_home)} / {pctText(g.p_draw)} / {pctText(g.p_away)}
                </td>
              </tr>
            </tbody>
          </table>
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
              <th scope="col" className="py-1 font-semibold">PUSH</th>
            </tr>
          </thead>
          <tbody>
            {TOTAL_LADDER.map(k => {
              const t = g.totals[k]
              if (!t) return null
              return (
                <tr key={k} className="border-t" style={{ borderColor: HAIR }}>
                  <td className="mono py-[5px] pr-3 text-compact text-text-primary">O/U {k}</td>
                  <td className="mono py-[5px] pr-3 text-compact">{t.p_over == null ? '--' : pctText(t.p_over)}</td>
                  <td className="mono py-[5px] pr-3 text-compact">{t.p_under == null ? '--' : pctText(t.p_under)}</td>
                  <td className="mono py-[5px] text-compact text-text-secondary">{t.p_push > 0 ? pctText(t.p_push) : '--'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {tab === 'btts' && (
        <div className="mono text-compact text-text-primary">
          Model: Yes {pctText(g.p_btts_yes)} {'\u00b7'} No {pctText(g.p_btts_no)}
          {rows.btts && (
            <span className="text-text-secondary">
              {'  \u00b7  Books: '}{rows.btts.books.map(b => `${b.book} ${price(b.a)} / ${price(b.b)}`).join('  \u00b7  ')} (Yes / No)
            </span>
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

/** Soccer (EPL / UCL) for /ev/games: the Dixon-Coles model board with a per-game three-way deep dive. */
export function SoccerGamesClient({ projections, odds }: { projections: SoccerProjectionsFeed | null; odds: GameOddsFeed | null }) {
  const [open, setOpen] = useState<string | null>(null)
  const games = projections?.priced_games ?? []
  const unavailable = !projections || projections.status === 'unavailable'

  return (
    <div>
      <div className="flex items-center gap-y-[6px] gap-x-[14px] flex-wrap min-h-[38px] py-[6px] px-[14px] mb-[10px] bg-background-primary-default text-compact" style={{ border: `1px solid ${HAIR}`, borderRadius: '8px' }}>
        <span className="mono font-bold text-text-primary">SOCCER (EPL / UCL)</span>
        <span className="mono text-neutral-300">{games.length} GAMES</span>
        <span className="mono text-[10.5px] font-bold tracking-fine text-warn border border-[color:#facc1555] rounded-chip py-0 px-[5px]">UNVALIDATED</span>
        <span className="mono text-neutral-300">{SOCCER_MODEL_BADGE}</span>
      </div>

      {unavailable || games.length === 0 ? (
        <div role="status" className="py-6 px-0 text-neutral-300 text-cozy">
          No soccer fixtures are priced yet. Premier League and Champions League games appear once the Dixon-Coles
          scorer has run against the posted slate.
        </div>
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
