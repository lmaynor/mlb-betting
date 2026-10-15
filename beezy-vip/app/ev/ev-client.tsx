'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import type { Bet, EvBookOutlierRow, EvBookQuote, EvFeed, EvHeadline, EvMlbRow, EvNflRow, EvPropsBlock } from '@/lib/types'
import { apiGetEvFeed } from '@/lib/betting-api'
import { beezyscore, scoreTier, TIER_META } from '@/lib/beezy-score'
import { BOOK_META, bookDisplay, CARD_STYLE, pickLabel, SYSTEM_COLOR, SYSTEM_LABEL, teamAbbrev, resolveEvUnderlying } from '@/lib/tokens'
import { americanToDecimal, fairAmerican, fmtMoney, fmtPct, kellySizing } from '@/lib/ev-math'
import { formatOdds } from '@/lib/odds'
import { useBankroll } from '@/lib/use-bankroll'
import { propStatLabel, SPORT_PILLARS } from '@/lib/sports'
import { PlayerHeadshot } from '@/components/ui/sport-assets'

type SportFilter = 'all' | 'mlb' | 'nfl' | 'nba' | 'nhl'
type VenueFilter = 'all' | 'exchanges' | string

interface Row {
  key: string
  sport: 'mlb' | 'nfl' | 'nhl' | 'nba'
  tag: string
  tagColor: string
  matchup: string
  when: string
  title: string
  bookKey: string
  priceMain: string
  priceSub: string
  fairProb: number | null
  fairSub: string
  nBooks: number | null
  anchored: boolean
  ev: number
  decimal: number
  score: number | null
  shadow: boolean
  status: 'pending' | 'win' | 'loss' | 'push' | 'void'
  copy: string
  /** league player id (NHL playerId / ESPN athlete id) when the feed resolved one: drives the headshot */
  playerId?: number | string | null
  team?: string | null
  /** shadow row that failed the sweep's credibility gates (thin reference / EV above the cap): listed, but after credible rows */
  lowConf?: boolean
  /** target side of the flagged quote ('OVER' | 'UNDER'): decides which price the Compare books strip ranks by */
  side?: string
  /** every book quoting this line (line shopping); the Compare books drawer needs more than one */
  allBooks?: EvBookQuote[]
}

const HAIR = '#1e2230'
const SKY = '#38bdf8'
const MIN_EDGE = [
  { label: 'All (+2%+)', v: 0.02 },
  { label: '+3%+', v: 0.03 },
  { label: '+5%+', v: 0.05 },
  { label: '+8%+', v: 0.08 },
]
const VENUES: { id: VenueFilter; label: string }[] = [
  { id: 'all', label: 'ALL VENUES' },
  { id: 'exchanges', label: 'EXCHANGES ONLY (Kalshi / Novig)' },
  { id: 'draftkings', label: 'DRAFTKINGS' },
  { id: 'fanduel', label: 'FANDUEL' },
  { id: 'betmgm', label: 'BETMGM' },
  { id: 'caesars', label: 'CAESARS' },
  { id: 'bet365', label: 'BET365' },
  { id: 'hardrock', label: 'HARDROCK' },
]

function toBet(r: EvMlbRow): Bet {
  return {
    id: r.id, system: 'EV', game_date: r.game_date, game_pk: r.game_pk ?? 0, bet_type: r.bet_type,
    player: r.player, away_team: r.away_team, home_team: r.home_team, odds: r.odds, stake: r.stake,
    model_prob: r.model_prob ?? 0, market_prob: r.market_prob ?? 0, edge: r.edge, kelly_pct: r.kelly_pct,
    kelly_triggered: true, result: r.result, profit: r.profit, paper: true, book: r.book, notes: r.notes,
    created_at: r.created_at,
  }
}

function mlbRow(r: EvMlbRow): Row {
  const bet = toBet(r)
  const under = resolveEvUnderlying(bet).system
  const score = beezyscore(bet)
  const matchup = `${teamAbbrev(r.away_team)} @ ${teamAbbrev(r.home_team)}`
  const title = pickLabel(bet)
  const book = bookDisplay(r.book)
  return {
    key: `mlb-${r.id}`,
    sport: 'mlb',
    tag: 'MLB', tagColor: SYSTEM_COLOR[under] ?? SYSTEM_COLOR.EV,
    matchup,
    when: r.game_date,
    title,
    bookKey: (r.book ?? '').toLowerCase(),
    priceMain: formatOdds(r.odds),
    priceSub: book.isExchange ? 'exchange' : 'sportsbook',
    fairProb: r.model_prob,
    fairSub: r.n_books != null ? `${r.n_books} books${r.anchored ? ' + Pinnacle' : ''}` : (r.anchored ? 'Pinnacle anchor' : 'consensus'),
    nBooks: r.n_books,
    anchored: r.anchored,
    ev: r.edge ?? 0,
    decimal: americanToDecimal(r.odds),
    score,
    shadow: false,
    status: r.result ?? 'pending',
    copy: `${title} ${matchup} @ ${book.label} ${formatOdds(r.odds)}`,
  }
}

function nflRow(r: EvNflRow): Row {
  const stat = SYSTEM_LABEL[(r.stat ?? '').toUpperCase()] ?? r.stat ?? ''
  const gm = (r.game_key ?? '').split(':')[2]?.replace('@', ' @ ') ?? ''
  const book = bookDisplay(r.venue)
  const mins = r.mins_to_kick
  const when = mins < 90 ? `kick in ${Math.round(mins)}m` : `kick in ${(mins / 60).toFixed(1)}h`
  const title = `${r.player ?? '?'} ${r.side === 'OVER' ? 'Over' : 'Under'} ${r.strike} ${stat}`
  return {
    key: `nfl-${r.ticker}-${r.side}`,
    sport: 'nfl',
    tag: 'NFL', tagColor: SPORT_PILLARS.nfl.accent,
    matchup: gm,
    when,
    title,
    bookKey: r.venue.toLowerCase(),
    priceMain: `${Math.round(r.ask * 100)}\u00a2`,
    priceSub: `ask on ${book.label}`,
    fairProb: r.fair_prob,
    fairSub: `${r.n_books ?? 0} book${r.n_books === 1 ? '' : 's'}${r.reason && !r.flag ? ` \u00b7 ${r.reason.replace(/_/g, ' ').toLowerCase()}` : ''}`,
    nBooks: r.n_books,
    anchored: false,
    ev: r.net_ev,
    decimal: r.ask > 0 ? 1 / r.ask : 1,
    score: null,
    shadow: true,
    status: 'pending',
    copy: `${title} (${gm}) YES-side ask ${Math.round(r.ask * 100)}c on ${book.label} -- shadow signal, unvalidated`,
  }
}

/** Sink A row (NFL book_outliers, NHL/NBA props): a book or exchange quote beating the consensus of the OTHER books.
 * Always SHADOW: no Beezy score, no stake sizing (EvCard keys both off `shadow`). */
function bookOutlierRow(r: EvBookOutlierRow, sport: 'nfl' | 'nhl' | 'nba'): Row {
  const book = bookDisplay(r.book)
  const point = r.point ?? r.line
  const gm = (r.game_key ?? '').split(':')[2]?.replace('@', ' @ ') ?? ''
  const matchup = gm || (r.away_team && r.home_team ? `${r.away_team} @ ${r.home_team}` : '')
  const mins = r.mins_to_kick
  const lead = sport === 'nfl' ? 'kick in' : 'starts in'
  const when = mins == null ? '' : mins < 90 ? `${lead} ${Math.round(mins)}m` : `${lead} ${(mins / 60).toFixed(1)}h`
  const title = `${r.player} ${String(r.side).toUpperCase() === 'OVER' ? 'Over' : 'Under'} ${point ?? ''} ${propStatLabel(r.stat ?? r.market)}`.replace(/\s+/g, ' ').trim()
  const priceMain = formatOdds(r.american)
  const gate = r.flag === false ? ` \u00b7 ${r.reason ? r.reason.replace(/_/g, ' ').toLowerCase() : 'outside alert gates'}` : ''
  return {
    key: `${sport}-${r.game_key}-${r.player_norm ?? r.player}-${r.stat ?? r.market}-${point}-${r.side}-${r.book}`,
    sport,
    tag: sport.toUpperCase(), tagColor: SPORT_PILLARS[sport].accent,
    matchup,
    when,
    title,
    bookKey: r.book.toLowerCase(),
    priceMain,
    priceSub: book.isExchange ? 'exchange (shadow)' : 'sportsbook (shadow)',
    fairProb: r.p_fair,
    fairSub: `${r.n_books} book${r.n_books === 1 ? '' : 's'}${gate}`,
    nBooks: r.n_books,
    anchored: false,
    ev: r.ev,
    decimal: r.decimal || americanToDecimal(r.american),
    score: null,
    shadow: true,
    status: 'pending',
    copy: `${title} (${matchup}) @ ${book.label} ${priceMain} -- shadow signal, unvalidated`,
    playerId: r.player_id ?? null,
    team: r.team ?? null,
    lowConf: r.flag === false,
    side: String(r.side).toUpperCase(),
    allBooks: r.all_books,
  }
}

/** Order two American prices (null = no quote) by payout: +150 > +110 > -105 > -130. Higher decimal payout is better. */
function priceDecimal(american: number | null | undefined): number | null {
  if (american == null || !isFinite(american) || american === 0) return null
  return americanToDecimal(american)
}

/** Strip rows for the Compare books drawer: best price for the target side first, unpriced books last. */
function rankBookQuotes(quotes: EvBookQuote[], side: string | undefined): { quote: EvBookQuote; best: boolean }[] {
  const pick = (q: EvBookQuote) => priceDecimal(String(side).toUpperCase() === 'UNDER' ? q.under : q.over)
  const known = side === 'OVER' || side === 'UNDER'
  const top = known ? Math.max(...quotes.map(q => pick(q) ?? -Infinity)) : -Infinity
  return quotes
    .map((quote, i) => ({ quote, i, d: known ? pick(quote) : null }))
    .sort((a, b) => (b.d ?? -Infinity) - (a.d ?? -Infinity) || a.i - b.i)
    .map(({ quote, d }) => ({ quote, best: known && d != null && d === top }))
}

export function EvClient({ initialFeed, headline }: { initialFeed: EvFeed | null; headline: EvHeadline | null }) {
  const [feed, setFeed] = useState<EvFeed | null>(initialFeed)
  const [updated, setUpdated] = useState<number>(() => Date.now())
  const [now, setNow] = useState<number>(() => Date.now())
  const [loading, setLoading] = useState(false)
  const [sport, setSport] = useState<SportFilter>('all')
  const [venue, setVenue] = useState<VenueFilter>('all')
  const [minEdge, setMinEdge] = useState(0.02)
  const [includeSettled, setIncludeSettled] = useState(false)
  const [bankroll, setBankroll] = useBankroll(1000)
  const [bankrollText, setBankrollText] = useState('')
  const [copied, setCopied] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setFeed(await apiGetEvFeed({ minEv: 0.02, limit: 150 }))
      setUpdated(Date.now())
    } catch { /* keep showing the last good feed */ } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    const id = setInterval(() => { if (document.visibilityState === 'visible') refresh() }, 60_000)
    const tick = setInterval(() => setNow(Date.now()), 15_000)
    return () => { clearInterval(id); clearInterval(tick) }
  }, [refresh])

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = []
    for (const r of feed?.mlb.rows ?? []) out.push(mlbRow(r))
    for (const r of feed?.nfl?.book_outliers ?? []) out.push(bookOutlierRow(r, 'nfl'))
    for (const r of feed?.nfl?.rows ?? []) out.push(nflRow(r))
    for (const r of feed?.nhl?.rows ?? []) out.push(bookOutlierRow(r, 'nhl'))
    for (const r of feed?.nba?.rows ?? []) out.push(bookOutlierRow(r, 'nba'))
    return out
      .filter(r => (sport === 'all' || r.sport === sport))
      .filter(r => (venue === 'all' ? true : venue === 'exchanges' ? !!BOOK_META[r.bookKey]?.isExchange : r.bookKey === venue))
      .filter(r => r.ev >= minEdge)
      .filter(r => includeSettled || r.status === 'pending')
      .sort((a, b) => Number(a.shadow) - Number(b.shadow) || Number(!!a.lowConf) - Number(!!b.lowConf) || b.ev - a.ev)
  }, [feed, sport, venue, minEdge, includeSettled])

  const nfl = feed?.nfl ?? null
  const ageSecs = Math.max(0, Math.round((now - updated) / 1000))
  const ageLabel = ageSecs < 90 ? `${ageSecs}s ago` : `${Math.round(ageSecs / 60)}m ago`

  async function copy(r: Row) {
    try { await navigator.clipboard.writeText(r.copy); setCopied(r.key); setTimeout(() => setCopied(c => (c === r.key ? null : c)), 1500) } catch { /* clipboard blocked */ }
  }

  return (
    <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '28px 24px 64px' }}>
      <header style={{ marginBottom: '22px' }}>
        <div className="dell-heading" style={{ color: 'var(--signal)', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span className="live-dot" /> +EV TERMINAL
        </div>
        <h1 className="dell-display" style={{ fontSize: 'clamp(26px, 4vw, 36px)', color: 'var(--chalk)' }}>
          Prices that lag the market, sized to your bankroll.
        </h1>
        <p className="times" style={{ color: 'var(--silver)', fontSize: '15px', maxWidth: '72ch', marginTop: '10px' }}>
          Every row is a sportsbook or exchange price beating the de-vigged consensus of other books. MLB rows carry a
          0&ndash;100 Beezy Score and are tracked and settled publicly at the flagged price. NFL, NHL and NBA rows are
          market-consensus signals, sized at 1/4 Kelly once at least three reference books agree.
        </p>
      </header>

      <TrackRecord h={headline} />

      {/* Controls */}
      <section style={{ ...CARD_STYLE, padding: '14px 16px', margin: '18px 0', display: 'grid', gap: '12px' }} aria-label="Filters">
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
          <Label>SPORT</Label>
          <PillGroup>
            {(['all', 'mlb', 'nfl', 'nhl', 'nba'] as SportFilter[]).map(s => (
              <Pill key={s} active={sport === s} onClick={() => setSport(s)}>{s.toUpperCase()}</Pill>
            ))}
          </PillGroup>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
          <Label>VENUE</Label>
          <PillGroup>
            {VENUES.map(v => <Pill key={v.id} active={venue === v.id} onClick={() => setVenue(v.id)}>{v.label}</Pill>)}
          </PillGroup>
        </div>
        <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
            <Label>MIN EDGE</Label>
            <PillGroup>
              {MIN_EDGE.map(m => <Pill key={m.v} active={minEdge === m.v} onClick={() => setMinEdge(m.v)}>{m.label}</Pill>)}
            </PillGroup>
            <PillGroup>
              <Pill active={includeSettled} onClick={() => setIncludeSettled(v => !v)}>INCLUDE SETTLED</Pill>
            </PillGroup>
          </div>
          <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
            <Label htmlFor="ev-bankroll">BANKROLL</Label>
            <div style={{ background: '#090a0f', border: '1px solid #2e3446', borderRadius: '6px', padding: '0 10px', height: '34px', display: 'inline-flex', alignItems: 'center' }}>
              <span className="mono" style={{ color: 'var(--fog)', fontSize: '13px', marginRight: '4px' }}>$</span>
              <input
                id="ev-bankroll" className="mono" inputMode="decimal"
                value={bankrollText === '' ? String(bankroll) : bankrollText}
                onChange={e => { setBankrollText(e.target.value); const n = parseFloat(e.target.value.replace(/[$,]/g, '')); if (isFinite(n) && n > 0) setBankroll(n) }}
                onBlur={() => setBankrollText('')}
                style={{ background: 'transparent', border: 'none', color: 'var(--chalk)', width: '84px', fontSize: '13px', textAlign: 'right', outline: 'none' }}
              />
            </div>
            <span style={{ fontSize: '11.5px', color: 'var(--fog)' }}>1/4 Kelly, 5% cap</span>
          </div>
        </div>
      </section>

      {nfl && (sport === 'all' || sport === 'nfl') && <NflBanner nfl={nfl} />}
      {(sport === 'nhl' || sport === 'nba') && feed?.[sport] && <PropsBanner sport={sport} block={feed[sport]!} />}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', margin: '6px 2px 10px' }}>
        <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>{rows.length} ROWS &middot; UPDATED {ageLabel.toUpperCase()}</span>
        <button onClick={refresh} disabled={loading} className="btn btn-ghost" style={{ padding: '5px 12px', fontSize: '12px' }}>{loading ? 'Refreshing...' : 'Refresh'}</button>
      </div>

      {feed == null ? (
        <Empty title="Feed unavailable" body="The odds API did not respond. This page retries every minute." />
      ) : rows.length === 0 ? (
        <Empty
          title="Nothing clears your filters right now"
          body="The scanner re-checks lines every 15 minutes through the betting day. Lower the minimum edge, widen the venue filter, or include settled rows."
        />
      ) : (
        <div style={{ display: 'grid', gap: '8px' }}>
          {rows.map(r => <EvCard key={r.key} r={r} bankroll={bankroll} copied={copied === r.key} onCopy={() => copy(r)} />)}
        </div>
      )}

      <p className="times" style={{ color: 'var(--fog)', fontSize: '12px', marginTop: '28px', maxWidth: '90ch', lineHeight: 1.6 }}>
        Not financial advice. Edge is a model of fair value, not a guarantee; soft lines move or limit quickly and
        past paper results do not predict future returns. 21+. If gambling is a problem, call 1-800-GAMBLER.{' '}
        <Link href="/legal/responsible-gambling" style={{ color: 'var(--link)' }}>Responsible gambling</Link>
      </p>
    </div>
  )
}

function Label({ children, htmlFor }: { children: React.ReactNode; htmlFor?: string }) {
  return <label htmlFor={htmlFor} className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', minWidth: '62px' }}>{children}</label>
}

function Pill({ children, active, onClick, disabled, title }: { children: React.ReactNode; active: boolean; onClick: () => void; disabled?: boolean; title?: string }) {
  return (
    <button
      onClick={onClick} disabled={disabled} title={title} aria-pressed={active}
      style={{
        fontFamily: 'var(--font-text), sans-serif', fontSize: '11.5px', fontWeight: 600, letterSpacing: '0.03em',
        padding: '6px 11px', borderRadius: '6px', cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.4 : 1,
        color: active ? '#ffffff' : 'var(--silver)',
        background: active ? '#1b1f2b' : 'transparent',
        border: `1px solid ${active ? '#3a4156' : 'transparent'}`,
        boxShadow: active ? 'inset 0 1px 0 rgba(255,255,255,0.06)' : 'none',
      }}
    >
      {children}
    </button>
  )
}

/** Recessed container for a row of segmented filter buttons: the active one is an elevated slate surface, so the brand
 * green stays reserved for +EV values. */
function PillGroup({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'inline-flex', flexWrap: 'wrap', gap: '2px', padding: '3px', background: '#090a0f', border: `1px solid ${HAIR}`, borderRadius: '8px', maxWidth: '100%' }}>
      {children}
    </div>
  )
}

function Kpi({ label, value, sub, color }: { label: string; value: string; sub: string; color?: string }) {
  return (
    <div style={{ ...CARD_STYLE, padding: '16px 18px', minWidth: 0 }}>
      <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '10px' }}>{label}</div>
      <div className="mono" style={{ fontSize: 'clamp(22px, 2.6vw, 30px)', fontWeight: 700, color: color ?? 'var(--chalk)', lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: '12px', color: 'var(--silver)', marginTop: '8px', lineHeight: 1.45 }}>{sub}</div>
    </div>
  )
}

function TrackRecord({ h }: { h: EvHeadline | null }) {
  if (!h) {
    return <div style={{ ...CARD_STYLE, padding: '18px', color: 'var(--fog)', fontSize: '13px' }}>Track record unavailable right now.</div>
  }
  const pos = (x: number | null) => (x ?? 0) >= 0 ? 'var(--signal)' : 'var(--loss)'
  return (
    <section aria-label="Paper track record">
      <div className="dell-heading" style={{ fontSize: '10.5px', color: 'var(--fog)', marginBottom: '10px' }}>
        PAPER TRACK RECORD &middot; FLAGGED PRICE AT FLAG TIME
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '12px' }}>
        <Kpi label="TRACKED +EV ALERTS" value={`${h.graded.toLocaleString()} graded`} sub={`${h.wins.toLocaleString()} W \u00b7 ${h.losses.toLocaleString()} L \u00b7 ${h.pushes} push \u00b7 ${h.voids.toLocaleString()} void \u00b7 ${h.pending} pending`} />
        <Kpi label={`FLAT STAKE ($${h.flat_stake_unit}) P&L`} value={fmtMoney(h.flat_pnl_dollars, true)} color={pos(h.flat_roi_pct)} sub={`${h.flat_roi_pct != null ? (h.flat_roi_pct > 0 ? '+' : '') + h.flat_roi_pct.toFixed(2) : '--'}% ROI, voids excluded`} />
        <Kpi label={`1/4 KELLY ON ${fmtMoney(h.bankroll)}`} value={fmtMoney(h.kelly_ending_bankroll)} color={pos(h.kelly_roi_pct)} sub={`${h.kelly_roi_pct != null ? (h.kelly_roi_pct > 0 ? '+' : '') + h.kelly_roi_pct.toFixed(2) : '--'}% ROI on staked, ${fmtPct(h.kelly_cap, 0)} per-bet cap, non-compounding`} />
        <Kpi label="EXCHANGE SWEEP" value="Kalshi + Novig" sub="NFL props priced against Kalshi and Novig and logged for review. No orders are placed. Novig pre-game fee is $0." />
      </div>
      <details style={{ margin: '12px 2px 0' }}>
        <summary className="dell-heading" style={{ fontSize: '10.5px', color: 'var(--silver)', cursor: 'pointer', letterSpacing: '0.06em' }}>
          Methodology &amp; Audit Notes
        </summary>
        <p className="times" style={{ fontSize: '12px', color: 'var(--fog)', margin: '10px 0 0', lineHeight: 1.6, maxWidth: '100ch' }}>
          Paper results only: no slippage, limits or line movement, and the same prop flagged at several books counts several
          times ({h.unique_props_graded.toLocaleString()} unique props behind {h.graded.toLocaleString()} graded alerts). Treat it as
          promising and unverified for execution. Methodology and per-book results on the{' '}
          <Link href="/analytics" style={{ color: 'var(--link)' }}>analytics page</Link>.
        </p>
      </details>
    </section>
  )
}

function ShadowBanner({ label, body, status, ageMinutes }: { label: string; body: string; status: string; ageMinutes: number | null }) {
  const idle = status !== 'shadow_live'
  const hrs = ageMinutes != null ? ageMinutes / 60 : null
  return (
    <div role="note" style={{ border: '1px solid rgba(56,189,248,0.30)', background: 'rgba(56,189,248,0.06)', borderRadius: '10px', padding: '12px 14px', marginBottom: '10px', fontSize: '12.5px', color: 'var(--silver)', lineHeight: 1.55 }}>
      <strong style={{ color: SKY }}>{label} MARKET SCANNER.</strong>{' '}
      {body} Signals are not yet part of the public track record.{' '}
      {status === 'unavailable'
        ? 'No sweep output is available yet.'
        : idle
          ? `The sweep is idle${hrs != null ? ` (last run ${hrs < 48 ? `${hrs.toFixed(1)}h` : `${Math.round(hrs / 24)}d`} ago)` : ''}; it runs around game windows.`
          : 'Sweep is running.'}
    </div>
  )
}

function NflBanner({ nfl }: { nfl: NonNullable<EvFeed['nfl']> }) {
  return (
    <ShadowBanner label="NFL" status={nfl.status} ageMinutes={nfl.age_minutes}
      body="Sportsbook and exchange quotes beating the consensus of the other books, plus Kalshi asks vs that consensus (net of fee). Most props have 1-2 reference books." />
  )
}

function PropsBanner({ sport, block }: { sport: 'nhl' | 'nba'; block: EvPropsBlock }) {
  return (
    <ShadowBanner label={sport.toUpperCase()} status={block.status} ageMinutes={block.age_minutes}
      body="Sportsbook quotes beating the consensus of the other books at the same player, stat and line." />
  )
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div style={{ ...CARD_STYLE, padding: '36px 20px', textAlign: 'center' }}>
      <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>{title}</div>
      <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '60ch', margin: '0 auto', lineHeight: 1.55 }}>{body}</div>
    </div>
  )
}

function Cell({ label, children, basis, grow = 0 }: { label: string; children: React.ReactNode; basis: string; grow?: number }) {
  return (
    <div style={{ flex: `${grow} 1 ${basis}`, minWidth: 0 }}>
      <div className="dell-heading" style={{ fontSize: '9px', color: 'var(--fog)', marginBottom: '4px' }}>{label}</div>
      {children}
    </div>
  )
}

function BookStrip({ id, r }: { id: string; r: Row }) {
  const ranked = rankBookQuotes(r.allBooks ?? [], r.side)
  const price = (american: number | null, best: boolean) => (
    <span className="mono" style={{ fontSize: '13px', fontWeight: best ? 800 : 600, color: best ? 'var(--signal)' : 'var(--chalk)' }}>
      {american == null ? <>&ndash;</> : formatOdds(american)}
    </span>
  )
  return (
    <div id={id} role="group" aria-label={`All books for ${r.title}`} style={{ flex: '1 0 100%', gridColumn: '1 / -1', borderTop: `1px solid ${HAIR}`, paddingTop: '12px' }}>
      <div className="dell-heading" style={{ fontSize: '9px', color: 'var(--fog)', marginBottom: '8px' }}>
        LINE SHOPPING &middot; FAIR = DE-VIGGED P(OVER) AT EACH BOOK
      </div>
      <div style={{ display: 'flex', gap: '8px', overflowX: 'auto', paddingBottom: '4px' }}>
        {ranked.map(({ quote, best }) => {
          const b = bookDisplay(quote.book)
          const bestSide = r.side === 'UNDER' ? 'under' : 'over'
          return (
            <div
              key={quote.book} data-best={best ? 'true' : undefined}
              style={{ flex: '0 0 auto', minWidth: '132px', background: 'var(--surface-elevated)', border: `1px solid ${best ? '#57F287' : HAIR}`, borderRadius: '8px', padding: '8px 10px', display: 'grid', gap: '6px' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                <span style={{ fontSize: '11px', fontWeight: 700, color: b.color, background: b.bg, border: `1px solid ${b.color}55`, borderRadius: '6px', padding: '2px 7px' }}>{b.label}</span>
                {best && <span className="dell-heading" style={{ fontSize: '8.5px', color: '#57F287' }} aria-label={`Best ${bestSide} price`}>BEST</span>}
              </div>
              <div style={{ display: 'flex', gap: '12px' }}>
                <div><div className="dell-heading" style={{ fontSize: '8.5px', color: 'var(--fog)' }}>OVER</div>{price(quote.over, best && bestSide === 'over')}</div>
                <div><div className="dell-heading" style={{ fontSize: '8.5px', color: 'var(--fog)' }}>UNDER</div>{price(quote.under, best && bestSide === 'under')}</div>
              </div>
              <div className="mono" style={{ fontSize: '11px', color: 'var(--ash)' }}>
                fair {quote.fair != null ? `${(quote.fair * 100).toFixed(1)}%` : <>&ndash;</>}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function EvCard({ r, bankroll, copied, onCopy }: { r: Row; bankroll: number; copied: boolean; onCopy: () => void }) {
  const [compare, setCompare] = useState(false)
  const nQuotes = r.allBooks?.length ?? 0
  const stripId = `compare-${r.key.replace(/[^A-Za-z0-9_-]/g, '-')}`
  const book = bookDisplay(r.bookKey)
  const tier = r.score != null ? scoreTier(r.score) : 'watch'
  const meta = TIER_META[tier]
  // a de-vigged consensus of 3+ reference books is a valid anchor for fractional Kelly, tracked or not
  const size = !r.shadow || ((r.nBooks ?? 0) >= 3 && r.fairProb != null) ? kellySizing(bankroll, r.fairProb, r.decimal) : null
  const statusColor = r.status === 'win' ? 'var(--signal)' : r.status === 'loss' ? 'var(--loss)' : 'var(--fog)'
  return (
    <article className="ev-card-grid" style={{ ...CARD_STYLE, padding: '14px 16px', boxShadow: r.score == null ? CARD_STYLE.boxShadow : meta.glow, borderColor: r.score == null ? HAIR : meta.border }}>
      <div style={{ flex: '0 0 74px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '5px' }}>
        {r.score == null ? (
          <>
            <span className="dell-heading" style={{ fontSize: '10px', fontWeight: 700, color: SKY, background: 'rgba(56,189,248,0.10)', border: '1px solid rgba(56,189,248,0.40)', borderRadius: '6px', padding: '3px 7px', letterSpacing: '0.06em' }}>MARKET</span>
            <span className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)' }}>{r.nBooks ?? 0} BK</span>
          </>
        ) : (
          <>
            <span className="mono" style={{ fontSize: '24px', fontWeight: 800, color: meta.color, lineHeight: 1 }} aria-label={`Beezy score ${r.score}`}>{r.score}</span>
            <span className="dell-heading" style={{ fontSize: '8.5px', color: meta.color, background: meta.bg, border: `1px solid ${meta.border}`, borderRadius: '6px', padding: '2px 6px' }}>{meta.label}</span>
          </>
        )}
      </div>

      <Cell label="PLAY" basis="260px" grow={2}>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '4px' }}>
          <span className="dell-heading" style={{ fontSize: '9px', color: r.tagColor, border: `1px solid ${r.tagColor}55`, borderRadius: '4px', padding: '1px 6px' }}>{r.tag}</span>
          <span className="mono" style={{ fontSize: '11.5px', color: 'var(--fog)' }}>{r.matchup} &middot; {r.when}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {(r.sport === 'nhl' || r.sport === 'nba' || r.sport === 'mlb') && r.playerId != null && (
            <PlayerHeadshot sport={r.sport} id={r.playerId} name={r.title} size={36} />
          )}
          <div style={{ fontSize: '14.5px', fontWeight: 600, color: 'var(--chalk)', lineHeight: 1.35 }}>{r.title}</div>
        </div>
      </Cell>

      <Cell label="TARGET PRICE" basis="150px">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '11px', fontWeight: 700, color: book.color, background: book.bg, border: `1px solid ${book.color}55`, borderRadius: '6px', padding: '3px 8px' }}>{book.label}</span>
          <span className="mono" style={{ fontSize: '17px', fontWeight: 700, color: 'var(--chalk)' }}>{r.priceMain}</span>
        </div>
        <div style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '4px' }}>{r.priceSub}</div>
      </Cell>

      <Cell label="CONSENSUS FAIR" basis="150px">
        <div className="mono" style={{ fontSize: '14px', color: 'var(--ash)' }}>
          {r.fairProb != null ? `${(r.fairProb * 100).toFixed(1)}% (${fairAmerican(r.fairProb)})` : '--'}
        </div>
        <div style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '4px' }}>{r.fairSub}</div>
      </Cell>

      <Cell label="NET EV" basis="84px">
        <span className="mono" style={{ fontSize: '13px', fontWeight: 700, color: '#090a0f', background: r.ev >= 0 ? 'var(--signal)' : 'var(--loss)', borderRadius: '6px', padding: '4px 8px', display: 'inline-block' }}>
          {r.ev >= 0 ? '+' : ''}{(r.ev * 100).toFixed(1)}%
        </span>
      </Cell>

      <Cell label="1/4 KELLY STAKE" basis="130px">
        {size ? (
          <>
            <div className="mono" style={{ fontSize: '15px', fontWeight: 700, color: size.dollars > 0 ? 'var(--chalk)' : 'var(--fog)' }}>{size.dollars > 0 ? fmtMoney(size.dollars) : '--'}</div>
            <div className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '4px' }}>{size.dollars > 0 ? `${size.units.toFixed(2)}% of bankroll` : 'below Kelly floor'}</div>
          </>
        ) : (
          <div style={{ fontSize: '11.5px', color: 'var(--fog)', lineHeight: 1.4 }}>Thin (&lt;3 bks)</div>
        )}
      </Cell>

      <div style={{ flex: '0 0 auto', display: 'flex', gap: '8px', alignItems: 'center' }}>
        {r.status !== 'pending' && <span className="dell-heading" style={{ fontSize: '10px', color: statusColor }}>{r.status.toUpperCase()}</span>}
        {nQuotes > 1 && (
          <button
            onClick={() => setCompare(v => !v)} className="btn btn-ghost" style={{ padding: '6px 12px', fontSize: '12px' }}
            aria-expanded={compare} aria-controls={stripId} aria-label={`Compare ${nQuotes} books for ${r.title}`}
          >
            Compare {nQuotes} books
          </button>
        )}
        <button onClick={onCopy} className="btn btn-ghost" style={{ padding: '6px 12px', fontSize: '12px' }}>{copied ? 'Copied' : 'Copy bet'}</button>
      </div>
      {compare && nQuotes > 1 && <BookStrip id={stripId} r={r} />}
    </article>
  )
}
