'use client'
/**
 * The Bankroll Challenge page body: board + bet slip (right rail on desktop, bottom sheet on a phone), My bets, Pool and
 * Leaderboard tabs. SAMPLE MODE: placing a bet only updates this page's own state (nothing is sent anywhere); the feed
 * adapter that talks to the contest API is milestone M2. All numbers shown come from lib/contest (a mirror of the Python engine).
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { MobileBottomSheet, useIsMobile } from '@/components/ui/mobile-sheet'
import { MAX_RENAMES, nameKey, validateName } from '@/lib/contest/names'
import { fmtMoney } from '@/lib/contest/odds'
import { sgpPrice } from '@/lib/contest/sgp'
import {
  combinations, makePick, previewParlay, previewRobin, previewSgp, previewStraight, previewTeaser, samePick, teaserPointOptions, type SlipState,
} from '@/lib/contest/slip'
import type { BetKind, ContestFeed, Pick, PlacedBet, Receipt } from '@/lib/contest/types'
import { Board } from './board'
import { MyBets, Pool } from './bets-table'
import { JoinPanel, type JoinResult } from './join-panel'
import { BlackjackTable } from './blackjack-table'
import { Leaderboard } from './leaderboard'
import { NameEditor } from './name-editor'
import { SlipPanel } from './slip-panel'

type Tab = 'board' | 'bets' | 'pool' | 'leaders' | 'blackjack'
const TABS: { id: Tab; label: string }[] = [{ id: 'board', label: 'Board' }, { id: 'bets', label: 'My Bets' }, { id: 'pool', label: 'Pool' }, { id: 'leaders', label: 'Leaderboard' }]
const BLACKJACK_TAB: { id: Tab; label: string } = { id: 'blackjack', label: 'Blackjack' }

export function ContestClient({ feed }: { feed: ContestFeed }) {
  const { rules } = feed
  const [tab, setTab] = useState<Tab>('board')
  const [slip, setSlip] = useState<SlipState>({ kind: 'straight', chosen: false, picks: [], teaserPoints: teaserPointOptions(rules)[0] ?? 6, robinSizes: [2] })
  const [me, setMe] = useState(feed.me)
  const [balance, setBalance] = useState(feed.balanceCents)
  const [bets, setBets] = useState<PlacedBet[]>(feed.myBets)
  const [sheet, setSheet] = useState(false)
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [now, setNow] = useState(() => Date.parse(feed.nowIso))
  const [seq, setSeq] = useState(1)
  const isMobile = useIsMobile()

  // `now` starts at the server render time (no hydration mismatch) and then ticks, so closing times track the real clock
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 30_000); return () => clearInterval(t) }, [])

  const toggle = useCallback((p: Pick) => { setReceipt(null); setSlip(s => {
    // a chosen Straight may stack picks on one game; everywhere else a new pick on a game replaces the old one
    const stacking = s.chosen === true && s.kind === 'straight'
    // a same-game parlay holds one spread-or-moneyline and one total from ONE game: a new pick replaces the other pick of its kind, and a pick on another game starts over
    const sgp = s.kind === 'sgp'
    const sgpNext = [...s.picks.filter(x => x.gameId === p.gameId && (x.market === 'total') !== (p.market === 'total')), p]
    const next = s.picks.some(x => samePick(x, p)) ? s.picks.filter(x => !samePick(x, p)) : sgp ? sgpNext : [...(stacking ? s.picks : s.picks.filter(x => x.gameId !== p.gameId)), p]
    // until the player picks a bet type, the slip follows the picks: one price is a straight, two or more a parlay
    const kind: BetKind = s.chosen ? s.kind : next.length >= 2 ? 'parlay' : 'straight'
    return { ...s, kind, picks: next }
  }) }, [])

  // SAMPLE MODE: a cash out only updates this page's own state; the server re-prices and refuses if the number moved (store.cash_out)
  const cashOut = useCallback((offers: { id: string; cents: number }[]) => {
    const paid = new Map(offers.map(o => [o.id, o.cents]))
    setBets(bs => bs.map(b => paid.has(b.id) ? { ...b, status: 'cashed_out' as const, payoutCents: paid.get(b.id) } : b))
    setBalance(b => b + offers.reduce((a, o) => a + o.cents, 0))
  }, [])

  const atRisk = useMemo(() => bets.filter(b => b.status === 'pending').reduce((a, b) => a + b.stakeCents, 0), [bets])

  const place = useCallback((stake: number) => {
    const at = new Date(now).toISOString()
    const mk = (id: string, kind: BetKind, picks: Pick[], risk: number, payout: number, groupId: string | null = null): PlacedBet => ({
      id, kind, groupId, stakeCents: risk, toWinCents: payout - risk, status: 'pending', placedAt: at, teaserPoints: kind === 'teaser' ? slip.teaserPoints : 0,
      legs: picks.map(p => ({ gameId: p.gameId, market: p.market, side: p.side, line: p.line, price: p.price, result: null })),
    })
    const out: PlacedBet[] = []
    if (slip.kind === 'straight') slip.picks.forEach((p, i) => out.push(mk(`s${seq}-${i}`, 'straight', [p], stake, previewStraight(p, stake).payoutCents)))
    else if (slip.kind === 'parlay') out.push(mk(`s${seq}`, 'parlay', slip.picks, stake, previewParlay(slip.picks, stake).payoutCents))
    else if (slip.kind === 'teaser') { const pv = previewTeaser(slip.picks, stake, slip.teaserPoints, rules); if (pv) out.push(mk(`s${seq}`, 'teaser', slip.picks, stake, pv.payoutCents)) }
    else if (slip.kind === 'sgp') {
      const game = feed.games.find(x => x.id === slip.picks[0]?.gameId)
      const pv = previewSgp(slip.picks, game, stake, rules)
      if (pv) out.push({ ...mk(`s${seq}`, 'sgp', slip.picks, stake, pv.payoutCents), ticketPrice: sgpPrice(slip.picks, game, rules) })
    }
    else {
      let i = 0
      for (const k of slip.robinSizes) for (const c of combinations(slip.picks.length, k)) {
        const ps = c.map(ix => slip.picks[ix])
        out.push(mk(`s${seq}-${i++}`, 'parlay', ps, stake, previewRobin(ps, [ps.length], stake).payoutCents, `rr${seq}`))
      }
    }
    if (out.length === 0) return
    setBets(b => [...out, ...b])
    setBalance(b => b - out.reduce((a, x) => a + x.stakeCents, 0))
    setSlip(s => ({ ...s, picks: [] }))
    setSeq(n => n + 1)
    // a confirmation replaces the slip; the player chooses View My Bets or Keep Betting (nothing jumps away on its own)
    setReceipt({ kind: slip.kind, bets: out, riskCents: out.reduce((a, x) => a + x.stakeCents, 0), payoutCents: out.reduce((a, x) => a + x.stakeCents + x.toWinCents, 0), placedAt: at })
  }, [now, rules, seq, slip, feed.games])

  const panel = (
    <SlipPanel slip={slip} games={feed.games} rules={rules} balanceCents={balance} receipt={receipt}
      onDoneReceipt={() => { setReceipt(null); setSheet(false) }} onViewBets={() => { setReceipt(null); setSheet(false); setTab('bets') }}
      onKind={kind => setSlip(s => ({ ...s, kind, chosen: true,
        // a teaser already moves the number and a same-game parlay is priced on the main lines, so an alt line goes back to the main line there
        picks: kind === 'teaser' || kind === 'sgp' ? s.picks.map(p => { const g = feed.games.find(x => x.id === p.gameId); return (g && makePick(g, p.market, p.side)) || p }) : s.picks }))}
      onLine={(from, to) => setSlip(s => ({ ...s, picks: s.picks.map(p => samePick(p, from) ? to : p) }))} onPoints={teaserPoints => setSlip(s => ({ ...s, teaserPoints }))}
      onSizes={robinSizes => setSlip(s => ({ ...s, robinSizes }))} onRemove={p => toggle(p)} onClear={() => setSlip(s => ({ ...s, picks: [] }))} onPlace={place} />
  )

  // SAMPLE MODE: joining and renaming only change this page's state. The real calls go to the contest API, which enforces the invite code,
  // uniqueness per contest and the rename limit (store.join_contest / rename_entry); this mirrors its rules so the form behaves the same.
  const board = useMemo(() => feed.leaderboard.map(r => r.mine ? { ...r, name: me.displayName } : r), [feed.leaderboard, me.displayName])
  const taken = (key: string) => feed.leaderboard.some(r => !r.mine && nameKey(r.name) === key)
  const join = useCallback((input: { inviteCode: string; displayName: string }): JoinResult => {
    const v = validateName(input.displayName)
    if (!v.ok) return { ok: false, message: v.message }
    if (taken(v.key)) return { ok: false, message: 'That name is taken in this contest.' }
    setMe(m => ({ ...m, joined: true, displayName: v.name, renamesLeft: MAX_RENAMES }))
    return { ok: true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed.leaderboard])
  const rename = useCallback((next: string): JoinResult => {
    const v = validateName(next)
    if (!v.ok) return { ok: false, message: v.message }
    if (taken(v.key)) return { ok: false, message: 'That name is taken in this contest.' }
    const changed = v.key !== nameKey(me.displayName)
    if (changed && me.renamesLeft <= 0) return { ok: false, message: `A name can be changed ${MAX_RENAMES} times.` }
    setMe(m => ({ ...m, displayName: v.name, renamesLeft: changed ? m.renamesLeft - 1 : m.renamesLeft }))
    return { ok: true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed.leaderboard, me.displayName, me.renamesLeft])
  const mine = board.find(r => r.mine)
  // the slip rail follows the work: always beside the board, elsewhere only while a slip or a confirmation exists
  const slipVisible = tab === 'board' || slip.picks.length > 0 || receipt !== null
  const showRail = !isMobile && slipVisible
  const bankroll = balance + atRisk
  // the hidden lifeline: its tab exists only while the bankroll is under the threshold (or while you are sitting at it, so a hand is never lost)
  const lifelineOpen = rules.blackjackEnabled && bankroll < rules.blackjackThresholdCents
  const tabs = lifelineOpen || tab === 'blackjack' ? [...TABS, BLACKJACK_TAB] : TABS
  if (!me.joined) {
    return (
      <div className="max-w-[1400px] mx-auto pt-4 px-4 sm:px-6 pb-16">
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: '0 0 12px' }}>{feed.name}</h1>
        <JoinPanel contestName={feed.name} suggested={me.suggestedName} onJoin={join} note="Sample mode: any invite code works here. The real server checks it." />
      </div>
    )
  }
  return (
    <div className="max-w-[1400px] mx-auto pt-4 px-4 sm:px-6 pb-40 lg:pb-8">
      <div className="flex flex-wrap items-center justify-between gap-3" style={{ marginBottom: 10 }}>
        <div className="flex items-center gap-3">
          <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>{feed.name}</h1>
          {feed.sample && <span className="mono" title="Invented games, lines and players to exercise the page. Placing a bet changes only this page." style={{ fontSize: 11, color: '#facc15' }}>SAMPLE DATA</span>}
        </div>
        <div role="tablist" aria-label="Contest sections" className="flex gap-1">
          {tabs.map(t => (
            <button key={t.id} role="tab" type="button" aria-selected={tab === t.id} onClick={() => setTab(t.id)} className="contest-tab"
              style={{ padding: '6px 12px', fontSize: 13, fontWeight: 600, borderRadius: 6, color: tab === t.id ? '#f3f4f6' : '#7d8596', background: tab === t.id ? '#1b1f2b' : 'transparent', border: `1px solid ${tab === t.id ? '#2e3446' : 'transparent'}` }}>{t.label}</button>
          ))}
        </div>
      </div>

      <div className="mono flex flex-wrap items-center gap-x-4 gap-y-1" aria-label="Your standing" style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: 8, padding: '9px 14px', fontSize: 13, marginBottom: 12 }}>
        <NameEditor name={me.displayName} renamesLeft={me.renamesLeft} onRename={rename} />
        <span><span style={{ color: '#7d8596' }}>Available </span><b>{fmtMoney(balance)}</b></span>
        <span><span style={{ color: '#7d8596' }}>At risk </span><b>{fmtMoney(atRisk)}</b></span>
        <span><span style={{ color: '#7d8596' }}>Bankroll </span><b>{fmtMoney(bankroll)}</b> <span style={{ color: bankroll >= rules.startBankrollCents ? '#57f287' : '#ef4444' }}>{fmtMoney(bankroll - rules.startBankrollCents, { sign: true })}</span></span>
        {mine && <span><span style={{ color: '#7d8596' }}>Rank </span><b>{mine.rank}</b> of {board.length}</span>}
        {lifelineOpen && tab !== 'blackjack' && (
          <button type="button" onClick={() => setTab('blackjack')} className="contest-chip" style={{ fontSize: 12, color: '#9ca3af', minHeight: 44 }}>
            Under {fmtMoney(rules.blackjackThresholdCents, { whole: true })}: the blackjack table is open
          </button>
        )}
      </div>

      <div className={`grid gap-4 items-start ${showRail ? 'lg:grid-cols-[minmax(0,1fr)_340px]' : ''}`}>
        <div className="min-w-0">
          {tab === 'board' && <Board games={feed.games} picks={slip.picks} now={now} rules={rules} onToggle={toggle} />}
          {tab === 'bets' && <MyBets bets={bets} games={feed.games} rules={rules} now={now} onCashOut={cashOut} />}
          {tab === 'pool' && <Pool bets={feed.pool} games={feed.games} />}
          {tab === 'leaders' && <Leaderboard rows={board} start={rules.startBankrollCents} />}
          {(lifelineOpen || tab === 'blackjack') && (
            <div hidden={tab !== 'blackjack'}>
              <BlackjackTable balanceCents={balance} bankrollCents={bankroll} thresholdCents={rules.blackjackThresholdCents} minChipCents={rules.blackjackMinChipCents}
                onMoney={d => setBalance(b => b + d)} />
            </div>
          )}
          <details style={{ margin: '10px 2px 0', maxWidth: '70ch' }}>
            <summary className="mono" style={{ fontSize: 12, color: '#9ca3af', cursor: 'pointer' }}>How it works</summary>
            <ul className="mono" style={{ fontSize: 12, color: '#9ca3af', lineHeight: 1.6, margin: '8px 0 0', paddingLeft: 16 }}>
              <li>Play money only. Nothing is wagered.</li>
              <li>Prices are the contest reference lines, refreshed on a schedule. A price that moves before you place is refused and shown again.</li>
              <li>Betting closes {rules.closeBufferMin} minutes before kickoff.</li>
              <li>Your picks stay hidden from other players until each game kicks off.</li>
              <li>On a spread or total, the + and &minus; on your slip move the number half a point at a time. Alt lines are priced by the contest from the main price.</li>
              <li>A same-game parlay pairs a spread or the moneyline with the total from one game. The two move together, so it is priced as a pair, on the main lines.</li>
              <li>A teaser moves every line the chosen points in your favor and pays from a fixed table.</li>
              <li>A round robin bets every combination of your picks for the sizes you choose, as separate parlays.</li>
            </ul>
          </details>
        </div>
        {showRail && <aside className="lg:sticky lg:top-4" aria-label="Bet Slip panel">{panel}</aside>}
      </div>

      {isMobile && slipVisible && (
        <>
          <button type="button" onClick={() => setSheet(true)} className="btn-primary mono" aria-label={`Open Bet Slip, ${slip.picks.length} picks`}
            style={{ position: 'fixed', left: 16, right: 16, bottom: 'calc(72px + env(safe-area-inset-bottom))', height: 46, zIndex: 40, fontSize: 14, fontWeight: 600 }}>
            Bet Slip{slip.picks.length ? ` \u00b7 ${slip.picks.length}` : ''}
          </button>
          <MobileBottomSheet open={sheet} onClose={() => setSheet(false)} title="Bet Slip">{panel}</MobileBottomSheet>
        </>
      )}
    </div>
  )
}
