'use client'
/**
 * The Bankroll Challenge page body: board + bet slip (right rail on desktop, bottom sheet on a phone), My bets, Pool and
 * Leaderboard tabs. SAMPLE MODE: placing a bet only updates this page's own state (nothing is sent anywhere); the feed
 * adapter that talks to the contest API is milestone M2. All numbers shown come from lib/contest (a mirror of the Python engine).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import { BankrollChart } from './bankroll-chart'
import { BlackjackTable } from './blackjack-table'
import { Leaderboard } from './leaderboard'
import { NameEditor } from './name-editor'
import { OddsFormatProvider, OddsFormatSelect } from './odds-format'
import { SlipPanel } from './slip-panel'

type Tab = 'board' | 'bets' | 'pool' | 'leaders' | 'blackjack'
const TABS: { id: Tab; label: string }[] = [{ id: 'board', label: 'Board' }, { id: 'bets', label: 'My Bets' }, { id: 'pool', label: 'Pool' }, { id: 'leaders', label: 'Leaderboard' }]
const BLACKJACK_TAB: { id: Tab; label: string } = { id: 'blackjack', label: 'Blackjack' }

/** What the server said when it refused: `error: { code, message, detail }` (contest/service.py RequestError.body). */
type ApiError = { code?: string; message?: string; detail?: Record<string, unknown> }
const errorOf = (json: unknown): ApiError => (json as { error?: ApiError } | null)?.error ?? {}
const newKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `k${Date.now()}${Math.random().toString(36).slice(2)}`)

/**
 * `slug` set (and `feed.sample` false) means LIVE: every action goes to /api/contest/<slug>/* and the page replaces its state with the fresh
 * feed the server returns. Without it the page runs on sample data and nothing leaves the browser.
 */
export function ContestClient(props: { feed: ContestFeed; slug?: string }) {
  return <OddsFormatProvider><ContestBody {...props} /></OddsFormatProvider>
}

function ContestBody({ feed: initial, slug }: { feed: ContestFeed; slug?: string }) {
  const live = !initial.sample && !!slug
  const [feed, setFeed] = useState(initial)
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
  const [notice, setNotice] = useState<string | null>(null)
  const [placing, setPlacing] = useState(false)
  const attempt = useRef<{ sig: string; key: string } | null>(null)      // the idempotency key of the slip being placed: a retry of the SAME slip reuses it
  const isMobile = useIsMobile()

  // `now` starts at the server render time (no hydration mismatch) and then ticks, so closing times track the real clock
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 30_000); return () => clearInterval(t) }, [])

  // LIVE MODE: the server is the only source of truth. Every response is the player's whole fresh feed and replaces local state.
  const applyFeed = useCallback((f: ContestFeed) => { setFeed(f); setMe(f.me); setBalance(f.balanceCents); setBets(f.myBets); setNow(Date.parse(f.nowIso)) }, [])
  const call = useCallback(async (action: string, body?: unknown): Promise<{ ok: boolean; json: unknown }> => {
    try {
      const res = await fetch(`/api/contest/${slug}/${action}`, body === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      return { ok: res.ok, json: await res.json().catch(() => ({})) }
    } catch {
      return { ok: false, json: { error: { code: 'OFFLINE', message: 'Could not reach the contest. Check your connection and try again.' } } }
    }
  }, [slug])
  const refresh = useCallback(async () => { const r = await call('feed'); if (r.ok) applyFeed(r.json as ContestFeed) }, [call, applyFeed])
  // scores, game state and prices move on their own: pull a fresh feed every minute while nothing is being placed
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => { if (!placing && document.visibilityState === 'visible') void refresh() }, 60_000)
    return () => clearInterval(t)
  }, [live, placing, refresh])

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
  const cashOut = useCallback((offers: { id: string; cents: number }[]): void | Promise<string | null> => {
    if (live) {
      return (async () => {
        const r = await call('cashout', { betIds: offers.map(o => Number(o.id)), expectedCents: offers.reduce((a, o) => a + o.cents, 0) })
        if (r.ok) { applyFeed(r.json as ContestFeed); return null }
        const e = errorOf(r.json)
        await refresh()
        if (e.code === 'VALUE_CHANGED') return `The cash-out value moved to ${fmtMoney(Number(e.detail?.cents ?? 0))}. Review it and try again.`
        return e.message || 'That bet cannot be cashed out right now.'
      })()
    }
    const paid = new Map(offers.map(o => [o.id, o.cents]))
    setBets(bs => bs.map(b => paid.has(b.id) ? { ...b, status: 'cashed_out' as const, payoutCents: paid.get(b.id) } : b))
    setBalance(b => b + offers.reduce((a, o) => a + o.cents, 0))
  }, [live, call, applyFeed, refresh])

  // Bet Again: the same picks of a settled bet, back on the slip at TODAY's prices (a moved price is the board's, never the old one)
  const rebet = useCallback((b: PlacedBet) => {
    const picks = b.legs.map(l => { const g = feed.games.find(x => x.id === l.gameId); return g ? makePick(g, l.market, l.side) : null })
    if (picks.some(p => p === null)) return
    setReceipt(null)
    setNotice(null)
    setSlip(s => ({ ...s, kind: b.kind === 'round_robin' ? 'parlay' : b.kind, chosen: true, picks: picks as Pick[], teaserPoints: b.kind === 'teaser' ? b.teaserPoints : s.teaserPoints }))
    setTab('board')
  }, [feed.games])

  const atRisk = useMemo(() => bets.filter(b => b.status === 'pending').reduce((a, b) => a + b.stakeCents, 0), [bets])

  const placeLive = useCallback(async (stake: number): Promise<boolean> => {
    if (placing) return false
    setPlacing(true)
    setNotice(null)
    const game = feed.games.find(x => x.id === slip.picks[0]?.gameId)
    const body = {
      kind: slip.kind, stakeCents: stake, teaserPoints: slip.teaserPoints, robinSizes: slip.robinSizes,
      picks: slip.picks.map(p => ({ gameId: p.gameId, market: p.market, side: p.side, line: p.line, price: p.price })),
      ...(slip.kind === 'sgp' ? { ticketPrice: sgpPrice(slip.picks, game, rules) } : {}),
    }
    // the same slip and stake retried (a dropped connection, a double click) reuses its key, so it can only ever be placed once
    const sig = JSON.stringify(body)
    if (attempt.current?.sig !== sig) attempt.current = { sig, key: newKey() }
    const before = new Set(bets.map(b => b.id))
    const r = await call('place', { ...body, key: attempt.current.key })
    if (r.ok) {
      const f = r.json as ContestFeed
      const fresh = f.myBets.filter(b => !before.has(b.id))
      applyFeed(f)
      attempt.current = null
      setSlip(s => ({ ...s, picks: [] }))
      setReceipt({ kind: slip.kind, bets: fresh, riskCents: fresh.reduce((a, x) => a + x.stakeCents, 0), payoutCents: fresh.reduce((a, x) => a + x.stakeCents + x.toWinCents, 0), placedAt: f.nowIso })
      setPlacing(false)
      return true
    } else {
      const e = errorOf(r.json)
      const d = e.detail as { game_id?: number; market?: string; side?: string; line?: number | null; price?: number } | undefined
      if (e.code === 'PRICE_CHANGED' && d?.game_id != null && d.price != null) {
        // show the player the number that is on offer now; they place again if they still want it
        setSlip(s => ({ ...s, picks: s.picks.map(p => p.gameId === d.game_id && p.market === d.market && p.side === d.side ? { ...p, line: d.line ?? p.line, price: Number(d.price) } : p) }))
        setNotice('A price moved. The slip now shows the current price. Review it and place again.')
        attempt.current = null
      } else if (e.code === 'PRICE_CHANGED') {
        setNotice('The ticket price moved. Review the slip and place again.')
        attempt.current = null
      } else if (e.code === 'QUOTE_STALE' || e.code === 'NO_QUOTE' || e.code === 'GAME_CLOSED') {
        setNotice(e.code === 'GAME_CLOSED' ? 'Betting on one of those games has closed.' : 'Those prices are out of date. The board has been refreshed.')
      } else {
        setNotice(e.message || 'The bet was not placed.')
      }
      await refresh()
    }
    setPlacing(false)
    return false
  }, [placing, feed.games, slip, rules, bets, call, applyFeed, refresh])

  const place = useCallback((stake: number): void | Promise<boolean> => {
    if (live) return placeLive(stake)
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
  }, [live, placeLive, now, rules, seq, slip, feed.games])

  const panel = (
    <SlipPanel slip={slip} games={feed.games} rules={rules} balanceCents={balance} receipt={receipt}
      onDoneReceipt={() => { setReceipt(null); setSheet(false) }} onViewBets={() => { setReceipt(null); setSheet(false); setTab('bets') }}
      onKind={kind => setSlip(s => ({ ...s, kind, chosen: true,
        // a teaser already moves the number and a same-game parlay is priced on the main lines, so an alt line goes back to the main line there
        picks: kind === 'teaser' || kind === 'sgp' ? s.picks.map(p => { const g = feed.games.find(x => x.id === p.gameId); return (g && makePick(g, p.market, p.side)) || p }) : s.picks }))}
      onLine={(from, to) => setSlip(s => ({ ...s, picks: s.picks.map(p => samePick(p, from) ? to : p) }))} onPoints={teaserPoints => setSlip(s => ({ ...s, teaserPoints }))}
      onSizes={robinSizes => setSlip(s => ({ ...s, robinSizes }))} onRemove={p => { setNotice(null); toggle(p) }} onClear={() => { setNotice(null); setSlip(s => ({ ...s, picks: [] })) }}
      onPlace={place} notice={notice} busy={placing} />
  )

  // SAMPLE MODE: joining and renaming only change this page's state. The real calls go to the contest API, which enforces the invite code,
  // uniqueness per contest and the rename limit (store.join_contest / rename_entry); this mirrors its rules so the form behaves the same.
  const board = useMemo(() => feed.leaderboard.map(r => r.mine ? { ...r, name: me.displayName } : r), [feed.leaderboard, me.displayName])
  const taken = (key: string) => feed.leaderboard.some(r => !r.mine && nameKey(r.name) === key)
  const join = useCallback((input: { inviteCode: string; displayName: string }): JoinResult | Promise<JoinResult> => {
    const v = validateName(input.displayName)
    if (!v.ok) return { ok: false, message: v.message }
    if (live) return call('join', { inviteCode: input.inviteCode, displayName: v.name }).then(r => { if (r.ok) { applyFeed(r.json as ContestFeed); return { ok: true } as const } return { ok: false, message: errorOf(r.json).message || 'Could not join.' } as const })
    if (taken(v.key)) return { ok: false, message: 'That name is taken in this contest.' }
    setMe(m => ({ ...m, joined: true, displayName: v.name, renamesLeft: MAX_RENAMES }))
    return { ok: true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed.leaderboard])
  const rename = useCallback((next: string): JoinResult | Promise<JoinResult> => {
    const v = validateName(next)
    if (!v.ok) return { ok: false, message: v.message }
    if (live) return call('rename', { displayName: v.name }).then(r => { if (r.ok) { applyFeed(r.json as ContestFeed); return { ok: true } as const } return { ok: false, message: errorOf(r.json).message || 'Could not rename.' } as const })
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
        <JoinPanel contestName={feed.name} suggested={me.suggestedName} onJoin={join} note={feed.sample ? 'Sample mode: any invite code works here. The real server checks it.' : undefined} />
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
        <OddsFormatSelect />
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
          {tab === 'bets' && <BankrollChart points={feed.history ?? []} start={rules.startBankrollCents} />}
          {tab === 'bets' && <MyBets bets={bets} games={feed.games} rules={rules} now={now} onCashOut={cashOut} onRebet={rebet} />}
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
