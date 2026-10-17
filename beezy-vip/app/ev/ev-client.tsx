'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import type { Bet, EvBookOutlierRow, EvBookQuote, EvFeed, EvHeadline, EvMlbRow, EvNflRow, EvPropsBlock } from '@/lib/types'
import { apiGetEvFeed } from '@/lib/betting-api'
import { beezyscore, scoreTier, TIER_META } from '@/lib/beezy-score'
import { BOOK_META, bookDisplay, CARD_STYLE, SURFACE_CARD, pickLabel, SYSTEM_COLOR, SYSTEM_LABEL, teamAbbrev, resolveEvUnderlying } from '@/lib/tokens'
import { matchesMyBooks, useMyBooks } from '@/lib/use-my-books'
import { americanToDecimal, fairAmerican, fmtMoney, fmtPct, kellySizing } from '@/lib/ev-math'
import { formatOdds } from '@/lib/odds'
import { useBankroll } from '@/lib/use-bankroll'
import { siteDateKey } from '@/lib/dates'
import { LIVE_SPORTS, propStatLabel, SPORT_PILLARS } from '@/lib/sports'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import { ScoreLegend } from '@/components/ui/score-legend'
import { LockedNotice } from '@/components/picks/locked-notice'

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
  /** number of books that flagged this same prop (MLB rows are collapsed to the best-priced one) */
  bookCount?: number
}

const HAIR = '#1e2230'
const BAR_STYLE = SURFACE_CARD
const COLUMNS: { label: string; w?: string; right?: boolean }[] = [
  { label: 'SIGNAL / SCORE', w: '112px' },
  { label: 'SPORT & MATCHUP', w: '150px' },
  { label: 'PROPOSITION' },
  { label: 'TARGET BOOK & PRICE', w: '170px' },
  { label: 'CONSENSUS FAIR', w: '160px' },
  { label: 'NET EV', w: '72px', right: true },
  { label: '1/4 KELLY STAKE', w: '130px', right: true },
  { label: 'ACTIONS', w: '210px', right: true },
]
const SKY = '#38bdf8'
const MIN_EDGE = [
  { label: 'All (+2%+)', v: 0.02 },
  { label: '+3%+', v: 0.03 },
  { label: '+5%+', v: 0.05 },
  { label: '+8%+', v: 0.08 },
]
const FIXED_VENUES: { id: VenueFilter; label: string }[] = [
  { id: 'all', label: 'All venues' },
  { id: 'exchanges', label: 'Exchanges only (Kalshi / Novig)' },
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
    copy: `${title} @ ${book.label} ${formatOdds(r.odds)} (${matchup}, EV +${((r.edge ?? 0) * 100).toFixed(1)}%)`,
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
    // A Kalshi ask measured against a single reference book is not a consensus: list it, but after credible rows.
    lowConf: !r.flag || (r.n_books ?? 0) < 3,
    side: String(r.side).toUpperCase(),
    copy: `${title} (${gm}) ${r.side === 'OVER' ? 'YES (Over)' : 'NO (Under)'} at ${Math.round(r.ask * 100)}c on ${book.label} -- preview signal, not yet tracked`,
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
    priceSub: book.isExchange ? 'exchange (preview)' : 'sportsbook (preview)',
    fairProb: r.p_fair,
    fairSub: `${r.n_books} book${r.n_books === 1 ? '' : 's'}${gate}`,
    nBooks: r.n_books,
    anchored: false,
    ev: r.ev,
    decimal: r.decimal || americanToDecimal(r.american),
    score: null,
    shadow: true,
    status: 'pending',
    copy: `${title} (${matchup}) @ ${book.label} ${priceMain} -- preview signal, not yet tracked`,
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
  const [showThin, setShowThin] = useState(false)
  const [showMyBooks, setShowMyBooks] = useState(false)
  const myBooks = useMyBooks()
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

  // Everything the feed carries, before any user filter: the venue list is built from it so every option has rows.
  const allRows = useMemo<Row[]>(() => {
    const today = siteDateKey()
    const out: Row[] = []
    // A pending MLB alert from an earlier date is a finished game awaiting grading, not a live price.
    for (const r of feed?.mlb.rows ?? []) if (r.result != null || r.game_date >= today) out.push(mlbRow(r))
    for (const r of feed?.nfl?.book_outliers ?? []) out.push(bookOutlierRow(r, 'nfl'))
    for (const r of feed?.nfl?.rows ?? []) out.push(nflRow(r))
    for (const r of feed?.nhl?.rows ?? []) out.push(bookOutlierRow(r, 'nhl'))
    for (const r of feed?.nba?.rows ?? []) out.push(bookOutlierRow(r, 'nba'))
    // Pinnacle is the sharp reference, not a book a user can bet at.
    return out.filter(r => r.bookKey !== 'pinnacle')
  }, [feed])

  const venueOptions = useMemo(() => {
    const counts = new Map<string, number>()
    for (const r of allRows) counts.set(r.bookKey, (counts.get(r.bookKey) ?? 0) + 1)
    const books = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([k, n]) => ({ id: k, label: `${bookDisplay(k).label} (${n})` }))
    return [...FIXED_VENUES, ...books]
  }, [allRows])

  // Every book that could be listed: the ones in this feed plus the known ones, never the sharp reference book.
  const myBookOptions = useMemo(() => {
    const keys = new Set<string>([...Object.keys(BOOK_META), ...allRows.map(r => r.bookKey), ...myBooks.books])
    keys.delete('pinnacle'); keys.delete('espnbet')
    return [...keys].filter(Boolean).sort((a, b) => bookDisplay(a).label.localeCompare(bookDisplay(b).label))
  }, [allRows, myBooks.books])

  const rows = useMemo<Row[]>(() => {
    const filtered = allRows
      .filter(r => (sport === 'all' || r.sport === sport))
      .filter(r => (venue === 'all' ? true : venue === 'exchanges' ? !!BOOK_META[r.bookKey]?.isExchange : r.bookKey === venue))
      .filter(r => matchesMyBooks(r.bookKey, myBooks.books))
      .filter(r => r.ev >= minEdge)
      .filter(r => showThin || !r.lowConf)
      .filter(r => includeSettled || r.status === 'pending')
    // MLB: the same prop flagged at several books is one play (each book would otherwise be staked again).
    const best = new Map<string, Row>()
    const out: Row[] = []
    for (const r of filtered) {
      if (r.sport !== 'mlb') { out.push(r); continue }
      const k = `${r.matchup}|${r.title}|${r.status}`
      const cur = best.get(k)
      if (!cur) { best.set(k, { ...r, bookCount: 1 }); out.push(best.get(k)!) }
      else { cur.bookCount = (cur.bookCount ?? 1) + 1; if (r.ev > cur.ev) Object.assign(cur, { ...r, bookCount: cur.bookCount }) }
    }
    return out.sort((a, b) => Number(a.shadow) - Number(b.shadow) || Number(!!a.lowConf) - Number(!!b.lowConf) || (b.score ?? 0) - (a.score ?? 0) || b.ev - a.ev)
  }, [allRows, sport, venue, minEdge, includeSettled, showThin, myBooks.books])

  const thinHidden = useMemo(() => allRows.filter(r => r.lowConf && r.ev >= minEdge && (sport === 'all' || r.sport === sport)).length, [allRows, minEdge, sport])

  const nfl = feed?.nfl ?? null
  // Age of the data, not of the page: the newest alert logged or the freshest sweep block, whichever is later.
  const lastScan = useMemo(() => {
    const stamps = [
      ...(feed?.mlb.rows ?? []).map(r => Date.parse(r.created_at)),
      ...[feed?.nfl, feed?.nhl, feed?.nba].map(b => (b?.as_of ? Date.parse(b.as_of) : NaN)),
    ].filter(n => isFinite(n))
    return stamps.length ? Math.max(...stamps) : updated
  }, [feed, updated])
  const ageSecs = Math.max(0, Math.round((now - lastScan) / 1000))
  const ageLabel = ageSecs < 5400 ? `${Math.max(1, Math.round(ageSecs / 60))}m ago` : ageSecs < 172800 ? `${(ageSecs / 3600).toFixed(1)}h ago` : `${Math.round(ageSecs / 86400)}d ago`

  async function copy(r: Row) {
    try { await navigator.clipboard.writeText(r.copy); setCopied(r.key); setTimeout(() => setCopied(c => (c === r.key ? null : c)), 1500) } catch { /* clipboard blocked */ }
  }

  return (
    <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '20px 24px 64px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px 20px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0, letterSpacing: '-0.01em' }}>+EV Terminal</h1>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>{rows.length} ROWS &middot; LAST SCAN {ageLabel.toUpperCase()}</span>
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
          <button onClick={refresh} disabled={loading} className="btn btn-ghost" style={{ padding: '5px 12px', fontSize: '12px' }}>{loading ? 'Refreshing...' : 'Refresh'}</button>
        </div>
      </div>

      <LedgerStrip h={headline} />

      <section style={{ ...BAR_STYLE, padding: '10px 14px', margin: '10px 0', display: 'flex', gap: '10px 22px', flexWrap: 'wrap', alignItems: 'center' }} aria-label="Filters">
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <Label>SPORT</Label>
          <PillGroup>
            {(['all', ...LIVE_SPORTS.map(p => p.slug)] as SportFilter[]).map(s => (
              <Pill key={s} active={sport === s} onClick={() => setSport(s)}>{s.toUpperCase()}</Pill>
            ))}
          </PillGroup>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <Label htmlFor="ev-venue">VENUE</Label>
          <select
            id="ev-venue" value={venue} onChange={e => setVenue(e.target.value)}
            style={{ background: '#090a0f', color: 'var(--chalk)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 8px', fontSize: '12px' }}
          >
            {venueOptions.map(v => <option key={v.id} value={v.id}>{v.label}</option>)}
          </select>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <Label>MIN EDGE</Label>
          <PillGroup>
            {MIN_EDGE.map(m => <Pill key={m.v} active={minEdge === m.v} onClick={() => setMinEdge(m.v)}>{m.label}</Pill>)}
          </PillGroup>
          <PillGroup>
            <Pill active={includeSettled} onClick={() => setIncludeSettled(v => !v)}>INCLUDE SETTLED</Pill>
            <Pill active={showThin} onClick={() => setShowThin(v => !v)}>{`THIN CONSENSUS${thinHidden ? ` (${thinHidden})` : ''}`}</Pill>
            <Pill active={showMyBooks || myBooks.enabled} onClick={() => setShowMyBooks(v => !v)}>{`MY BOOKS${myBooks.enabled ? ` (${myBooks.books.length})` : ''}`}</Pill>
          </PillGroup>
        </div>
        <span style={{ fontSize: '11.5px', color: 'var(--fog)', marginLeft: 'auto' }}>1/4 Kelly, 5% cap</span>
      </section>

      {showMyBooks && (
        <section aria-label="My books" style={{ ...BAR_STYLE, padding: '10px 14px', margin: '0 0 10px', display: 'flex', gap: '8px 12px', flexWrap: 'wrap', alignItems: 'center' }}>
          <Label>MY BOOKS</Label>
          <span style={{ fontSize: '11.5px', color: 'var(--fog)' }}>Pick the books you can bet at in your state. None selected shows every book. Saved in this browser.</span>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', flexBasis: '100%' }}>
            {myBookOptions.map(k => {
              const on = myBooks.books.includes(k)
              const b = bookDisplay(k)
              return (
                <button key={k} role="checkbox" aria-checked={on} onClick={() => myBooks.toggle(k)}
                  style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '11.5px', fontWeight: 600, padding: '5px 10px', borderRadius: '6px', cursor: 'pointer',
                    color: on ? '#090a0f' : 'var(--silver)', background: on ? b.color : '#090a0f', border: `1px solid ${on ? b.color : HAIR}`, transition: 'background 120ms ease, border-color 120ms ease' }}>
                  {b.label}
                </button>
              )
            })}
            {myBooks.enabled && <button onClick={myBooks.clear} className="btn btn-ghost" style={{ padding: '4px 10px', fontSize: '11.5px' }}>Clear</button>}
          </div>
        </section>
      )}

      {nfl && (sport === 'all' || sport === 'nfl') && <NflBanner nfl={nfl} />}
      {(sport === 'nhl' || sport === 'nba') && feed?.[sport] && <PropsBanner sport={sport} block={feed[sport]!} />}

      {feed == null ? (
        <Empty title="Temporarily unavailable" body="Live prices could not be loaded. This page retries every minute." />
      ) : rows.length === 0 ? (
        <Empty
          title="Nothing clears your filters right now"
          body="Lines are re-checked every 15 minutes through the betting day. Lower the minimum edge, widen the venue filter, or include settled rows."
        />
      ) : (
        <div className="ev-table-wrap" style={{ ...BAR_STYLE, padding: 0 }}>
          <table className="ev-table" style={{ width: '100%', borderCollapse: 'collapse', minWidth: '1040px' }}>
            <thead>
              <tr>
                {COLUMNS.map(c => (
                  <th key={c.label} scope="col" style={{ textAlign: c.right ? 'right' : 'left', width: c.w }}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(r => <EvRow key={r.key} r={r} bankroll={bankroll} copied={copied === r.key} onCopy={() => copy(r)} />)}
            </tbody>
          </table>
        </div>
      )}
      <LockedNotice locked={feed?.locked ?? 0} shown={rows.length} what="live +EV prices" plan="The +EV Terminal is part of All-Access Pro." />

      <ScoreLegend />

      <p className="times" style={{ color: 'var(--fog)', fontSize: '12px', marginTop: '28px', maxWidth: '90ch', lineHeight: 1.6 }}>
        Every row is a sportsbook or exchange price beating the de-vigged consensus of the other books. MLB rows carry a
        0&ndash;100 Beezy Score and are tracked and settled publicly at the flagged price; NFL, NHL and NBA rows are
        market-consensus signals, sized at 1/4 Kelly once at least three reference books agree. Not financial advice. Edge is a
        model of fair value, not a guarantee; soft lines move or limit quickly and past paper results do not predict future
        returns. 21+. If gambling is a problem, call 1-800-GAMBLER.{' '}
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

function LedgerStrip({ h }: { h: EvHeadline | null }) {
  if (!h) {
    return <div style={{ ...BAR_STYLE, padding: '0 14px', minHeight: '38px', display: 'flex', alignItems: 'center', color: 'var(--fog)', fontSize: '12.5px' }}>Track record unavailable right now.</div>
  }
  const tone = (x: number | null) => (x ?? 0) >= 0 ? 'var(--signal)' : 'var(--loss)'
  const roi = (x: number | null) => (x == null ? '--' : `${x > 0 ? '+' : ''}${x.toFixed(1)}%`)
  return (
    <section aria-label="Paper track record" style={{ ...BAR_STYLE, padding: '0 14px', minHeight: '38px', display: 'flex', alignItems: 'center', gap: '8px 18px', flexWrap: 'wrap' }}>
      <span className="mono" style={{ fontSize: '12.5px', color: 'var(--silver)' }}>
        {h.graded.toLocaleString()} graded &middot; {h.wins.toLocaleString()}W {h.losses.toLocaleString()}L {h.pushes}P &middot; {h.pending} pending
      </span>
      <span className="mono" style={{ fontSize: '12.5px', color: 'var(--silver)' }}>
        <span style={{ color: tone(h.flat_roi_pct), fontWeight: 700 }}>{fmtMoney(h.flat_pnl_dollars, true)} flat ({roi(h.flat_roi_pct)} ROI)</span>
      </span>
      <span className="mono" style={{ fontSize: '12.5px', color: 'var(--silver)' }}>
        {fmtMoney(h.bankroll)} &rarr; <span style={{ color: tone(h.kelly_roi_pct), fontWeight: 700 }}>{fmtMoney(h.kelly_ending_bankroll)} 1/4-Kelly ({roi(h.kelly_roi_pct)} ROI)</span>
      </span>
      <span style={{ fontSize: '11.5px', color: 'var(--fog)' }}>paper, flagged price at flag time</span>
      <details style={{ marginLeft: 'auto', position: 'relative' }}>
        <summary style={{ fontSize: '11.5px', color: 'var(--silver)', cursor: 'pointer' }}>Methodology &amp; Audit Notes</summary>
        <p className="times" style={{ position: 'absolute', right: 0, top: '28px', zIndex: 5, width: 'min(440px, 80vw)', background: 'var(--surface-elevated)', border: `1px solid ${HAIR}`, borderRadius: '8px', padding: '12px 14px', fontSize: '12px', color: 'var(--silver)', margin: 0, lineHeight: 1.6 }}>
          Paper results only: no slippage, limits or line movement, and the same prop flagged at several books counts several
          times ({h.unique_props_graded.toLocaleString()} unique props behind {h.graded.toLocaleString()} graded alerts). Voids are excluded
          from ROI; Kelly is {fmtPct(h.kelly_cap, 0)} per-bet capped and non-compounding. Treat it as promising and unverified for
          execution. Methodology and per-book results on the{' '}
          <Link href="/analytics?tab=bankroll" style={{ color: 'var(--link)' }}>analytics page</Link>.
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
      <strong style={{ color: SKY }}>{label} PREVIEW.</strong>{' '}
      {body} Not yet tracked in the public results.{' '}
      {status === 'unavailable'
        ? 'No data yet.'
        : idle
          ? `Last updated${hrs != null ? ` ${hrs < 48 ? `${hrs.toFixed(1)}h` : `${Math.round(hrs / 24)}d`} ago` : ' earlier'}; refreshes around game windows.`
          : 'Updating live.'}
    </div>
  )
}

function NflBanner({ nfl }: { nfl: NonNullable<EvFeed['nfl']> }) {
  return (
    <ShadowBanner label="NFL" status={nfl.status} ageMinutes={nfl.age_minutes}
      body="Sportsbook and exchange prices beating the consensus of the other books, plus Kalshi asks. Many props have only 1-2 reference books." />
  )
}

function PropsBanner({ sport, block }: { sport: 'nhl' | 'nba'; block: EvPropsBlock }) {
  return (
    <ShadowBanner label={SPORT_PILLARS[sport].status === 'data_only' ? `${sport.toUpperCase()} COMING SOON` : sport.toUpperCase()} status={block.status} ageMinutes={block.age_minutes}
      body="Sportsbook prices beating the consensus of the other books on the same player, stat and line." />
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

function BookStrip({ id, r }: { id: string; r: Row }) {
  const ranked = rankBookQuotes(r.allBooks ?? [], r.side)
  const price = (american: number | null, best: boolean) => (
    <span className="mono" style={{ fontSize: '13px', fontWeight: best ? 800 : 600, color: best ? 'var(--signal)' : 'var(--chalk)' }}>
      {american == null ? <>&ndash;</> : formatOdds(american)}
    </span>
  )
  return (
    <div id={id} role="group" aria-label={`All books for ${r.title}`} style={{ flex: '1 0 100%', gridColumn: '1 / -1', borderTop: `1px solid ${HAIR}`, paddingTop: '12px' }}>
      <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '8px' }}>
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
                {best && <span className="dell-heading" style={{ fontSize: '10px', color: '#57F287' }} aria-label={`Best ${bestSide} price`}>BEST</span>}
              </div>
              <div style={{ display: 'flex', gap: '12px' }}>
                <div><div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>OVER</div>{price(quote.over, best && bestSide === 'over')}</div>
                <div><div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>UNDER</div>{price(quote.under, best && bestSide === 'under')}</div>
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

function EvRow({ r, bankroll, copied, onCopy }: { r: Row; bankroll: number; copied: boolean; onCopy: () => void }) {
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
    <>
      <tr data-row="ev" style={{ borderLeft: `3px solid ${r.score == null ? HAIR : meta.color}` }}>
        <td>
          {r.score == null ? (
            <div style={{ display: 'grid', gap: '3px', justifyItems: 'start' }}>
              <span className="dell-heading" style={{ fontSize: '10px', fontWeight: 700, color: SKY, background: 'rgba(56,189,248,0.10)', border: '1px solid rgba(56,189,248,0.40)', borderRadius: '6px', padding: '2px 7px', letterSpacing: '0.06em' }}>MARKET</span>
              <span className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)' }}>{r.nBooks ?? 0} BK</span>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span className="mono" style={{ fontSize: '20px', fontWeight: 800, color: meta.color, lineHeight: 1 }} aria-label={`Beezy score ${r.score}`}>{r.score}</span>
              <span className="dell-heading" style={{ fontSize: '10px', color: meta.color, background: meta.bg, border: `1px solid ${meta.border}`, borderRadius: '6px', padding: '2px 6px' }}>{meta.label}</span>
            </div>
          )}
        </td>
        <td>
          <span className="dell-heading" style={{ fontSize: '10px', color: r.tagColor, border: `1px solid ${r.tagColor}55`, borderRadius: '4px', padding: '1px 6px', marginRight: '8px' }}>{r.tag}</span>
          <span className="mono" style={{ fontSize: '11.5px', color: 'var(--fog)' }}>{r.matchup}</span>
          <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '3px' }}>{r.when}</div>
        </td>
        <td>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            {(r.sport === 'nhl' || r.sport === 'nba' || r.sport === 'mlb') && r.playerId != null && (
              <PlayerHeadshot sport={r.sport} id={r.playerId} name={r.title} size={30} />
            )}
            <span style={{ fontSize: '13.5px', fontWeight: 600, color: 'var(--chalk)', lineHeight: 1.35 }}>{r.title}</span>
          </div>
        </td>
        <td>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '11px', fontWeight: 700, color: book.color, background: book.bg, border: `1px solid ${book.color}55`, borderRadius: '6px', padding: '2px 7px' }}>{book.label}</span>
            <span className="mono" style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)' }}>{r.priceMain}</span>
          </div>
          <div style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '3px' }}>{r.priceSub}</div>
        </td>
        <td>
          <div className="mono" style={{ fontSize: '13px', color: 'var(--ash)' }}>
            {r.fairProb != null ? `${(r.fairProb * 100).toFixed(1)}% (${fairAmerican(r.fairProb)})` : '--'}
          </div>
          <div style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '3px' }}>{r.fairSub}</div>
        </td>
        <td style={{ textAlign: 'right' }}>
          <span className="mono" style={{ fontSize: '13px', fontWeight: 700, color: r.ev >= 0 ? 'var(--signal)' : 'var(--loss)' }}>
            {r.ev >= 0 ? '+' : ''}{(r.ev * 100).toFixed(1)}%
          </span>
        </td>
        <td style={{ textAlign: 'right' }}>
          {size ? (
            <>
              <div className="mono" style={{ fontSize: '14px', fontWeight: 700, color: size.dollars > 0 ? 'var(--chalk)' : 'var(--fog)' }}>{size.dollars > 0 ? fmtMoney(size.dollars) : '--'}</div>
              <div className="mono" style={{ fontSize: '10.5px', color: 'var(--fog)', marginTop: '3px' }}>{size.dollars > 0 ? `${size.units.toFixed(2)}% of bankroll` : 'below Kelly floor'}</div>
            </>
          ) : (
            <span style={{ fontSize: '11.5px', color: 'var(--fog)' }}>Too few books to size</span>
          )}
        </td>
        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
          {r.status !== 'pending' && <span className="dell-heading" style={{ fontSize: '10px', color: statusColor, marginRight: '8px' }}>{r.status.toUpperCase()}</span>}
          {nQuotes > 1 && (
            <button
              onClick={() => setCompare(v => !v)} className="btn btn-ghost" style={{ padding: '4px 10px', fontSize: '12px', marginRight: '6px' }}
              aria-expanded={compare} aria-controls={stripId} aria-label={`Compare ${nQuotes} books for ${r.title}`}
            >
              Compare {nQuotes} books
            </button>
          )}
          <button onClick={onCopy} className="btn btn-ghost" style={{ padding: '4px 10px', fontSize: '12px' }}>{copied ? 'Copied' : 'Copy'}</button>
        </td>
      </tr>
      {compare && nQuotes > 1 && (
        <tr><td colSpan={COLUMNS.length} style={{ padding: '0 16px 14px' }}><BookStrip id={stripId} r={r} /></td></tr>
      )}
    </>
  )
}
