'use client'

/**
 * /edge multi-sport prop research cockpit: master-detail (prop list | deep-dive inspector).
 *
 *  - LEFT: scannable prop cards (headshot, player, line, target book + price, +EV badge, L10 hit-rate pill).
 *  - RIGHT: inspector for the selected row: price callout, L10 game log vs the line (FormSparkline), opportunity /
 *    volume trend (UsageSparkline; MLB shows the Statcast charts instead) and the multi-book odds matrix.
 *  - >= 960px: two columns, inspector sticky. < 960px: one column; selecting a card opens the inspector as a
 *    full-screen sheet with a Close button (CSS in globals.css: .cockpit-grid / .cockpit-detail).
 *
 * Pure inline styles (this repo's rule); the only classes are the two layout hooks above, because a media query cannot
 * be written inline. ASCII only: typographic characters are entities in JSX text and \uXXXX escapes in strings.
 * SVG paint attributes use literal hex (var() does not resolve there).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import type { CockpitFeed, CockpitForm, CockpitGame, CockpitRow, EvBookQuote } from '@/lib/types'
import { apiGetEdgeCockpit } from '@/lib/betting-api'
import { bookDisplay, CARD_STYLE } from '@/lib/tokens'
import { americanToDecimal, fairAmerican } from '@/lib/ev-math'
import { formatOdds } from '@/lib/odds'
import { LIVE_SPORTS, propStatLabel, SPORT_PILLARS } from '@/lib/sports'
import { headshotUrlById } from '@/lib/sport-assets'
import { isOffshore, onshoreQuotes } from '@/lib/offshore'

export type CockpitSport = 'all' | 'mlb' | 'nfl' | 'nhl' | 'nba'
export const COCKPIT_SPORTS: CockpitSport[] = ['all', ...LIVE_SPORTS.map(p => p.slug)]

const HAIR = '#1e2230'
const GOOD = '#57F287'
const BAD = '#ef4444'
const NEUTRAL = '#4a5066'
const SKY = '#38bdf8'

// -- helpers --------------------------------------------------------------------------------------------------------
const isUnder = (r: CockpitRow) => String(r.side).toUpperCase() === 'UNDER'

function foldName(s: string | null | undefined): string {
  return (s ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim()
}

/** Did the player clear the flagged side of the line in this game? (Over: val > line, Under: val < line.) */
function sideHit(g: CockpitGame, line: number | null, under: boolean): boolean | null {
  if (g.val == null || line == null) return null
  return under ? g.val < line : g.val > line
}

/** Hit rate of the flagged side over the last `n` games of the log (0-1), or null with no games/line. */
function sideRate(games: CockpitGame[], line: number | null, under: boolean, n: number): number | null {
  const flags = games.slice(-n).map(g => sideHit(g, line, under)).filter((x): x is boolean => x != null)
  return flags.length ? flags.filter(Boolean).length / flags.length : null
}

const pct0 = (x: number | null | undefined) => (x == null ? '--' : `${Math.round(x * 100)}%`)

function rateColor(x: number | null): string {
  if (x == null) return 'var(--fog)'
  return x >= 0.6 ? GOOD : x >= 0.4 ? '#e5e7eb' : BAD
}

function propText(r: CockpitRow): string {
  const side = isUnder(r) ? 'Under' : 'Over'
  return `${side} ${r.line ?? ''} ${propStatLabel(r.stat)}`.replace(/\s+/g, ' ').trim()
}

function whenText(r: CockpitRow): string {
  const m = r.mins_to_kick
  if (m == null) return ''
  return m < 90 ? `in ${Math.round(m)}m` : `in ${(m / 60).toFixed(1)}h`
}

function initials(name: string | null | undefined): string {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return ((parts[0][0] ?? '') + (parts.length > 1 ? parts[parts.length - 1][0] ?? '' : '')).toUpperCase()
}

const evText = (ev: number | null) => (ev == null ? '--' : `${ev >= 0 ? '+' : ''}${(ev * 100).toFixed(1)}%`)

function sideDecimal(american: number | null | undefined): number | null {
  if (american == null || !isFinite(american) || american === 0) return null
  return americanToDecimal(american)
}

/** Books sorted best price for the flagged side first (unpriced last); `best` marks the top price(s). */
export function rankQuotes(quotes: EvBookQuote[], under: boolean): { quote: EvBookQuote; best: boolean }[] {
  const price = (q: EvBookQuote) => sideDecimal(under ? q.under : q.over)
  const top = Math.max(-Infinity, ...quotes.map(q => price(q) ?? -Infinity))
  return quotes
    .map((quote, i) => ({ quote, i, d: price(quote) }))
    .sort((a, b) => (b.d ?? -Infinity) - (a.d ?? -Infinity) || a.i - b.i)
    .map(({ quote, d }) => ({ quote, best: d != null && d === top }))
}

// -- feed hook (shared with the sport switcher in edge-client.tsx) -------------------------------------------------
function rememberIds(rows: CockpitRow[], ids: Map<string, number | string>) {
  for (const r of rows) if (r.player_id != null && r.player) ids.set(`${r.sport}|${foldName(r.player)}`, r.player_id)
}

/** Holds the cockpit feed, refreshes it every minute while the tab is visible, and keeps MLB headshot ids: those are
 * resolved server-side from the roster map, so a client refresh would otherwise drop them. */
export function useCockpitFeed(initial: CockpitFeed | null) {
  const ids = useRef<Map<string, number | string>>(new Map())
  const [feed, setFeed] = useState<CockpitFeed | null>(() => { rememberIds(initial?.rows ?? [], ids.current); return initial })
  const [updated, setUpdated] = useState<number>(() => Date.now())
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const f = await apiGetEdgeCockpit({ limit: 60 })
      rememberIds(f.rows, ids.current)
      setFeed({ ...f, rows: f.rows.map(r => (r.player_id == null && r.player ? { ...r, player_id: ids.current.get(`${r.sport}|${foldName(r.player)}`) ?? null } : r)) })
      setUpdated(Date.now())
    } catch { /* keep showing the last good feed */ } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    const id = setInterval(() => { if (document.visibilityState === 'visible') refresh() }, 60_000)
    return () => clearInterval(id)
  }, [refresh])

  return { feed, updated, loading, refresh }
}

// -- small presentational pieces ----------------------------------------------------------------------------------
function Avatar({ r, size }: { r: CockpitRow; size: number }) {
  const [bad, setBad] = useState(false)
  const src = headshotUrlById(r.sport, r.sport === 'nfl' ? r.headshot_id : r.player_id)
  return (
    <span style={{ width: size, height: size, flex: '0 0 auto', borderRadius: size > 48 ? '14px' : '10px', background: '#171a24', border: `1px solid ${HAIR}`, display: 'inline-flex', alignItems: 'flex-end', justifyContent: 'center', overflow: 'hidden', color: 'var(--fog)', fontSize: size > 48 ? '17px' : '12px', fontWeight: 700 }}>
      {src && !bad ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt={r.player ?? ''} width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer"
          onError={() => setBad(true)} style={{ objectFit: 'contain', objectPosition: 'center bottom', width: '100%', height: '100%' }} />
      ) : (
        <span style={{ alignSelf: 'center' }}>{initials(r.player)}</span>
      )}
    </span>
  )
}

function BookBadge({ book }: { book: string | null | undefined }) {
  const b = bookDisplay(book)
  return <span style={{ fontSize: '11px', fontWeight: 700, color: b.color, background: b.bg, border: `1px solid ${b.color}55`, borderRadius: '6px', padding: '2px 7px', whiteSpace: 'nowrap' }}>{b.label}</span>
}

function EvBadge({ ev, size = 'sm' }: { ev: number | null; size?: 'sm' | 'lg' }) {
  const pos = (ev ?? 0) >= 0
  return (
    <span className="mono" style={{ fontSize: size === 'lg' ? '15px' : '12.5px', fontWeight: 800, color: '#090a0f', background: pos ? GOOD : BAD, borderRadius: '6px', padding: size === 'lg' ? '5px 10px' : '3px 7px', whiteSpace: 'nowrap', display: 'inline-block' }}>
      {evText(ev)}
    </span>
  )
}

function Panel({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <section style={{ ...CARD_STYLE, padding: '14px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px', marginBottom: '10px', flexWrap: 'wrap' }}>
        <h3 className="dell-heading" style={{ fontSize: '10.5px', color: 'var(--fog)', letterSpacing: '0.08em' }}>{title}</h3>
        {sub && <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>{sub}</span>}
      </div>
      {children}
    </section>
  )
}

function StatPills({ items }: { items: { label: string; value: string; color?: string }[] }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(76px, 1fr))', gap: '8px', marginTop: '12px' }}>
      {items.map(it => (
        <div key={it.label} style={{ background: '#090a0f', border: `1px solid ${HAIR}`, borderRadius: '8px', padding: '7px 9px', minWidth: 0 }}>
          <div className="mono" style={{ fontSize: '15px', fontWeight: 700, color: it.color ?? 'var(--chalk)', lineHeight: 1.1 }}>{it.value}</div>
          <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginTop: '4px' }}>{it.label}</div>
        </div>
      ))}
    </div>
  )
}

function NoData({ children }: { children: ReactNode }) {
  return <div style={{ fontSize: '12.5px', color: 'var(--fog)', lineHeight: 1.55, padding: '6px 0' }}>{children}</div>
}

// -- Panel 1: L10 game log vs the prop line ------------------------------------------------------------------------
export function FormSparkline({ form, under }: { form: CockpitForm; under: boolean }) {
  const games = form.games
  const line = form.line
  const W = 360, H = 176, padL = 30, padR = 10, padT = 22, padB = 30
  const vals = games.map(g => g.val ?? 0)
  const rawMax = Math.max(1, ...vals, line != null ? line * 1.2 : 0)
  const step = rawMax > 60 ? 20 : rawMax > 20 ? 10 : rawMax > 8 ? 5 : 2
  const max = Math.ceil(rawMax / step) * step
  const bw = (W - padL - padR) / Math.max(games.length, 1)
  const y = (v: number) => H - padB - (v / max) * (H - padT - padB)
  const ticks = [0, max / 2, max]
  const useOpp = games.some(g => g.opp)
  const l5 = sideRate(games, line, under, 5)
  const l10 = sideRate(games, line, under, 10)
  const sznOver = form.szn_rate
  const szn = sznOver == null ? null : under ? 1 - sznOver : sznOver
  const cleared = games.filter(g => sideHit(g, line, under)).length
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" style={{ width: '100%', height: 'auto', display: 'block' }}
        aria-label={`Last ${games.length} games versus the ${line ?? ''} line: ${under ? 'under' : 'over'} cleared in ${cleared} of ${games.length}`}>
        {ticks.map(t => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} stroke="#1e2230" strokeWidth="1" />
            <text x={padL - 5} y={y(t) + 3} textAnchor="end" fill="#7d8596" style={{ fontSize: '10px', fontFamily: 'monospace' }}>{Math.round(t)}</text>
          </g>
        ))}
        {games.map((g, i) => {
          const v = g.val ?? 0
          const hit = sideHit(g, line, under)
          const h = Math.max((v / max) * (H - padT - padB), 1)
          const x = padL + i * bw
          const fill = hit == null ? NEUTRAL : hit ? GOOD : BAD
          return (
            <g key={i}>
              <rect x={x + bw * 0.16} y={H - padB - h} width={bw * 0.68} height={h} rx="2" fill={fill} opacity="0.92" />
              <text x={x + bw / 2} y={H - padB - h - 4} textAnchor="middle" fill="#d1d5db" style={{ fontSize: '10px', fontFamily: 'monospace' }}>{v}</text>
              <text x={x + bw / 2} y={H - 12} textAnchor="middle" fill="#7d8596" style={{ fontSize: '10px', fontFamily: 'monospace' }}>
                {String((useOpp ? g.opp : g.date) ?? '').slice(useOpp ? 0 : -5, useOpp ? 4 : undefined)}
              </text>
            </g>
          )
        })}
        {line != null && (
          <g>
            <line x1={padL} x2={W - padR} y1={y(line)} y2={y(line)} stroke="#f3f4f6" strokeWidth="1.4" strokeDasharray="5 4" />
            <text x={W - padR} y={y(line) - 4} textAnchor="end" fill="#f3f4f6" style={{ fontSize: '10px', fontWeight: 700, fontFamily: 'monospace' }}>Line {line}</text>
          </g>
        )}
      </svg>
      <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', marginTop: '6px', fontSize: '11px', color: 'var(--silver)' }}>
        <span><i style={{ display: 'inline-block', width: 9, height: 9, background: GOOD, borderRadius: 2, marginRight: 5 }} />Cleared the {under ? 'under' : 'over'}</span>
        <span><i style={{ display: 'inline-block', width: 9, height: 9, background: BAD, borderRadius: 2, marginRight: 5 }} />Missed</span>
        <span style={{ color: 'var(--fog)' }}>{cleared} of {games.length} games</span>
      </div>
      <StatPills items={[
        { label: 'L5 HIT%', value: pct0(l5), color: rateColor(l5) },
        { label: 'L10 HIT%', value: pct0(l10), color: rateColor(l10) },
        { label: 'SEASON AVG', value: form.szn_avg == null ? '--' : String(form.szn_avg) },
        { label: 'LINE', value: line == null ? '--' : String(line) },
      ]} />
      {szn != null && (
        <div style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '8px' }}>
          Season hit rate {pct0(szn)} over {form.szn_games} games.
        </div>
      )}
    </div>
  )
}

// -- Panel 2: opportunity / volume trend ---------------------------------------------------------------------------
function mean(xs: number[]): number { return xs.reduce((a, b) => a + b, 0) / xs.length }

export function UsageSparkline({ form }: { form: CockpitForm }) {
  const pts = form.games.map(g => g.usage)
  const nums = pts.filter((u): u is number => u != null)
  const label = form.usage_label ?? 'Volume'
  if (nums.length < 2) return <NoData>No {label.toLowerCase()} history for this player yet.</NoData>
  const W = 360, H = 118, padL = 30, padR = 10, padT = 16, padB = 22
  const maxV = Math.max(...nums) * 1.12 || 1
  const minV = Math.min(...nums, 0)
  const span = maxV - minV || 1
  const n = form.games.length
  const x = (i: number) => padL + (n === 1 ? 0 : (i / (n - 1)) * (W - padL - padR))
  const y = (v: number) => H - padB - ((v - minV) / span) * (H - padT - padB)
  const idx = pts.map((u, i) => (u == null ? -1 : i)).filter(i => i >= 0)
  const line = idx.map((i, k) => `${k === 0 ? 'M' : 'L'}${x(i).toFixed(1)} ${y(pts[i] as number).toFixed(1)}`).join(' ')
  const area = `${line} L${x(idx[idx.length - 1]).toFixed(1)} ${y(minV).toFixed(1)} L${x(idx[0]).toFixed(1)} ${y(minV).toFixed(1)} Z`
  const avg = mean(nums)
  const recent = mean(nums.slice(-3))
  const prior = nums.length > 3 ? mean(nums.slice(0, -3)) : null
  const change = prior != null && prior > 0 ? (recent - prior) / prior : null
  const trend = change == null ? 'steady' : change > 0.08 ? 'trending up' : change < -0.08 ? 'trending down' : 'steady'
  const trendColor = trend === 'trending up' ? GOOD : trend === 'trending down' ? BAD : '#e5e7eb'
  const fmt = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1))
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" style={{ width: '100%', height: 'auto', display: 'block' }}
        aria-label={`${label} over the last ${n} games, ${trend}`}>
        <path d={area} fill="#38bdf8" opacity="0.12" />
        <line x1={padL} x2={W - padR} y1={y(avg)} y2={y(avg)} stroke="#7d8596" strokeWidth="1" strokeDasharray="4 4" />
        <text x={padL - 5} y={y(avg) + 3} textAnchor="end" fill="#9ca3af" style={{ fontSize: '10px', fontFamily: 'monospace' }}>{fmt(avg)}</text>
        <path d={line} fill="none" stroke="#38bdf8" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {idx.map(i => (
          <g key={i}>
            <circle cx={x(i)} cy={y(pts[i] as number)} r="3" fill="#38bdf8" />
            <text x={x(i)} y={y(pts[i] as number) - 7} textAnchor="middle" fill="#d1d5db" style={{ fontSize: '10px', fontFamily: 'monospace' }}>{fmt(pts[i] as number)}</text>
          </g>
        ))}
        {form.games.map((g, i) => (
          <text key={i} x={x(i)} y={H - 7} textAnchor="middle" fill="#7d8596" style={{ fontSize: '10px', fontFamily: 'monospace' }}>
            {String(g.opp ?? g.date ?? '').slice(0, 4)}
          </text>
        ))}
      </svg>
      <StatPills items={[
        { label: `${label.toUpperCase()} L10 AVG`, value: fmt(avg) },
        { label: 'LAST 3 AVG', value: fmt(recent) },
        { label: 'TREND', value: change == null ? 'steady' : `${change >= 0 ? '+' : ''}${Math.round(change * 100)}%`, color: trendColor },
      ]} />
      <div style={{ fontSize: '12px', color: 'var(--silver)', marginTop: '8px', lineHeight: 1.5 }}>
        {label} are <b style={{ color: trendColor }}>{trend}</b>
        {prior != null ? <>: {fmt(recent)} over the last 3 games vs {fmt(prior)} before that.</> : '.'}
      </div>
    </div>
  )
}

// -- Panel 3: multi-book odds matrix -------------------------------------------------------------------------------
export function OddsMatrix({ r }: { r: CockpitRow }) {
  const under = isUnder(r)
  const shown = onshoreQuotes(r.all_books)
  const quotes: EvBookQuote[] = shown.length
    ? shown
    : r.book && !isOffshore(r.book) && r.american != null
      ? [{ book: r.book, over: under ? null : r.american, under: under ? r.american : null, fair: null }]
      : []
  if (!quotes.length) return <NoData>No book quotes were captured for this line.</NoData>
  const ranked = rankQuotes(quotes, under)
  const cols = 'minmax(110px, 1.5fr) 1fr 1fr 1fr'
  const cell = (american: number | null, strong: boolean) => (
    <span className="mono" style={{ fontSize: '13.5px', fontWeight: strong ? 800 : 600, color: strong ? GOOD : american == null ? 'var(--fog)' : 'var(--chalk)' }}>
      {american == null ? <>&ndash;</> : formatOdds(american)}
    </span>
  )
  return (
    <div>
      <div role="table" aria-label={`Prices from every book quoting ${propText(r)}`} style={{ overflowX: 'auto' }}>
        <div role="row" style={{ display: 'grid', gridTemplateColumns: cols, gap: '8px', padding: '0 4px 6px', borderBottom: `1px solid ${HAIR}`, minWidth: '320px' }}>
          {['BOOK', 'OVER', 'UNDER', 'FAIR O%'].map(h => (
            <span key={h} role="columnheader" className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>{h}</span>
          ))}
        </div>
        {ranked.map(({ quote, best }) => {
          const flagged = (quote.book ?? '').toLowerCase() === (r.book ?? '').toLowerCase()
          return (
            <div key={quote.book} role="row" data-best={best ? 'true' : undefined}
              style={{ display: 'grid', gridTemplateColumns: cols, gap: '8px', alignItems: 'center', padding: '8px 4px', borderBottom: '1px solid #151822', minWidth: '320px', background: best ? 'rgba(87, 242, 135, 0.06)' : 'transparent' }}>
              <span role="cell" style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                <BookBadge book={quote.book} />
                {flagged && <span className="dell-heading" style={{ fontSize: '10px', color: SKY }}>TARGET</span>}
              </span>
              <span role="cell">{cell(quote.over, best && !under)}</span>
              <span role="cell">{cell(quote.under, best && under)}</span>
              <span role="cell" className="mono" style={{ fontSize: '12.5px', color: 'var(--ash)' }}>{quote.fair == null ? <>&ndash;</> : `${(quote.fair * 100).toFixed(1)}%`}</span>
            </div>
          )
        })}
      </div>
      <div style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '8px', lineHeight: 1.5 }}>
        Highlighted row = best {under ? 'under' : 'over'} price. {r.all_books && r.all_books.length > 1 ? '' : 'Only the flagged quote was captured for this line: check other books before you bet.'}
      </div>
    </div>
  )
}

// -- Inspector ------------------------------------------------------------------------------------------------------
function Inspector({ r, renderMlbExtras, onClose }: { r: CockpitRow; renderMlbExtras?: (r: CockpitRow) => ReactNode; onClose: () => void }) {
  const under = isUnder(r)
  const accent = SPORT_PILLARS[r.sport]?.accent ?? GOOD
  const fair = r.p_fair != null && r.p_fair > 0 && r.p_fair < 1 ? fairAmerican(r.p_fair) : null
  const book = bookDisplay(r.book)
  const form = r.recentForm
  const mlbExtras = r.sport === 'mlb' && renderMlbExtras ? renderMlbExtras(r) : null
  return (
    <div style={{ display: 'grid', gap: '12px' }}>
      <header style={{ ...CARD_STYLE, padding: '16px', display: 'grid', gap: '12px' }}>
        <div style={{ display: 'flex', gap: '14px', alignItems: 'center' }}>
          <Avatar r={r} size={56} />
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginBottom: '3px' }}>
              <span className="dell-heading" style={{ fontSize: '10px', color: accent, border: `1px solid ${accent}55`, borderRadius: '4px', padding: '1px 6px' }}>{r.sport.toUpperCase()}</span>
              <span className="mono" style={{ fontSize: '11.5px', color: 'var(--fog)' }}>{[r.team, r.matchup, whenText(r)].filter(Boolean).join(' \u00b7 ')}</span>
            </div>
            <h2 className="dell-display" style={{ fontSize: 'clamp(19px, 3vw, 24px)', color: 'var(--chalk)', lineHeight: 1.15, overflowWrap: 'anywhere' }}>{r.player ?? 'Unknown player'}</h2>
            <div style={{ fontSize: '14px', color: 'var(--ash)', marginTop: '3px', fontWeight: 600 }}>{propText(r)}</div>
          </div>
          <button onClick={onClose} className="btn btn-ghost cockpit-close" aria-label="Close inspector" style={{ padding: '6px 12px', fontSize: '12px', alignSelf: 'flex-start' }}>Close</button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', background: '#090a0f', border: `1px solid ${HAIR}`, borderRadius: '8px', padding: '10px 12px' }}>
          <EvBadge ev={r.ev} size="lg" />
          <div style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.5, minWidth: 0, flex: '1 1 200px' }}>
            Best Price: <b className="mono" style={{ color: 'var(--chalk)' }}>{r.american != null ? formatOdds(r.american) : '--'}</b> on{' '}
            <b style={{ color: book.color }}>{book.label}</b>
            {fair && <> vs <b className="mono" style={{ color: 'var(--chalk)' }}>{fair}</b> Consensus Fair</>}
          </div>
        </div>
        <div style={{ fontSize: '11.5px', color: 'var(--fog)', lineHeight: 1.5 }}>
          {r.n_books != null ? `De-vigged consensus of ${r.n_books} reference book${r.n_books === 1 ? '' : 's'}.` : 'De-vigged market consensus.'}
          {r.shadow ? ' Preview signal: not yet tracked in the public results.' : r.source === 'model' ? ' Model probability vs the market line.' : ' Tracked and settled publicly at the flagged price.'}
        </div>
      </header>

      <Panel title={`L${form?.n_games ?? 10} GAME LOG VS LINE`} sub={form ? `${propStatLabel(r.stat)} \u00b7 ${under ? 'Under' : 'Over'} ${form.line ?? ''}` : undefined}>
        {form && form.games.length ? <FormSparkline form={form} under={under} /> : <NoData>No game log is available for this player and stat yet. The line and price above are still live.</NoData>}
      </Panel>

      <Panel title={r.sport === 'mlb' ? 'CONTACT QUALITY AND BATTED BALLS' : 'OPPORTUNITY AND VOLUME TREND'} sub={r.sport === 'mlb' ? 'Statcast' : form?.usage_label ?? undefined}>
        {r.sport === 'mlb'
          ? (mlbExtras ?? <NoData>Statcast batted-ball charts are not built for this player today.</NoData>)
          : form && form.games.length ? <UsageSparkline form={form} /> : <NoData>No volume history for this player yet.</NoData>}
      </Panel>

      <Panel title="MULTI-BOOK ODDS MATRIX" sub={r.all_books ? `${onshoreQuotes(r.all_books).length} books` : undefined}>
        <OddsMatrix r={r} />
      </Panel>
    </div>
  )
}

// -- List card ------------------------------------------------------------------------------------------------------
function PropCard({ r, active, onSelect }: { r: CockpitRow; active: boolean; onSelect: () => void }) {
  const under = isUnder(r)
  const accent = SPORT_PILLARS[r.sport]?.accent ?? GOOD
  const l10 = r.recentForm ? sideRate(r.recentForm.games, r.recentForm.line, under, 10) : null
  const meta = [r.matchup, whenText(r)].filter(Boolean).join(' \u00b7 ')
  return (
    <button onClick={onSelect} aria-current={active || undefined} data-row-id={r.id}
      style={{ width: '100%', textAlign: 'left', cursor: 'pointer', color: 'var(--ash)', display: 'grid', gridTemplateColumns: '44px minmax(0, 1fr) auto', gap: '12px', alignItems: 'center', padding: '12px', borderRadius: '10px', background: active ? '#171a24' : '#11131a', border: `1px solid ${active ? '#3a4156' : HAIR}`, boxShadow: active ? 'inset 3px 0 0 #57F287' : 'none', opacity: r.flag ? 1 : 0.82 }}>
      <Avatar r={r} size={44} />
      <span style={{ minWidth: 0, display: 'grid', gap: '3px' }}>
        <span style={{ fontSize: '14.5px', fontWeight: 700, color: 'var(--chalk)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.player ?? 'Unknown player'}</span>
        <span style={{ fontSize: '13px', color: 'var(--ash)', fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{propText(r)}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '7px', minWidth: 0 }}>
          <span className="dell-heading" style={{ fontSize: '10px', color: accent, border: `1px solid ${accent}55`, borderRadius: '4px', padding: '1px 5px', flex: '0 0 auto' }}>{r.sport.toUpperCase()}</span>
          <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{meta}</span>
        </span>
      </span>
      <span style={{ display: 'grid', gap: '6px', justifyItems: 'end' }}>
        <EvBadge ev={r.ev} />
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
          <BookBadge book={r.book} />
          <span className="mono" style={{ fontSize: '13px', fontWeight: 700, color: 'var(--chalk)' }}>{r.american != null ? formatOdds(r.american) : '--'}</span>
        </span>
        <span className="mono" style={{ fontSize: '11px', fontWeight: 700, color: rateColor(l10), border: `1px solid ${HAIR}`, background: '#090a0f', borderRadius: '999px', padding: '2px 8px' }}>
          L10: {pct0(l10)}
        </span>
      </span>
    </button>
  )
}

// -- Filters --------------------------------------------------------------------------------------------------------
/** Recessed segmented group: active = elevated slate surface, never the brand fill (that stays reserved for +EV). */
function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { id: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="group" aria-label={label} style={{ display: 'inline-flex', gap: '2px', padding: '3px', background: '#090a0f', border: `1px solid ${HAIR}`, borderRadius: '8px', maxWidth: '100%', overflowX: 'auto' }}>
      {options.map(o => {
        const active = value === o.id
        return (
          <button key={o.id} onClick={() => onChange(o.id)} aria-pressed={active}
            style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '11.5px', fontWeight: 600, letterSpacing: '0.03em', padding: '6px 11px', borderRadius: '6px', cursor: 'pointer', whiteSpace: 'nowrap', color: active ? '#ffffff' : 'var(--silver)', background: active ? '#1b1f2b' : 'transparent', border: `1px solid ${active ? '#3a4156' : 'transparent'}`, boxShadow: active ? 'inset 0 1px 0 rgba(255,255,255,0.06)' : 'none' }}>
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

type SortKey = 'ev' | 'l10' | 'start'

// -- Cockpit --------------------------------------------------------------------------------------------------------
export function Cockpit({ feed, sport, updated, loading, onRefresh, renderMlbExtras }: {
  feed: CockpitFeed | null
  sport: CockpitSport
  updated: number
  loading: boolean
  onRefresh: () => void
  renderMlbExtras?: (r: CockpitRow) => ReactNode
}) {
  const [market, setMarket] = useState<string>('all')
  const [sort, setSort] = useState<SortKey>('ev')
  const [credibleOnly, setCredibleOnly] = useState(false)
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [drawer, setDrawer] = useState(false)
  const [now, setNow] = useState<number>(() => Date.now())
  const detailRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15_000)
    return () => clearInterval(t)
  }, [])

  const inSport = useMemo(() => (feed?.rows ?? []).filter(r => sport === 'all' || r.sport === sport), [feed, sport])

  const markets = useMemo(() => {
    const m = new Map<string, number>()
    for (const r of inSport) if (r.stat) m.set(r.stat, (m.get(r.stat) ?? 0) + 1)
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1])
  }, [inSport])

  // a market chip from another sport must not leave the list empty after switching sports
  const activeMarket = market === 'all' || markets.some(([k]) => k === market) ? market : 'all'

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = inSport
      .filter(r => activeMarket === 'all' || r.stat === activeMarket)
      .filter(r => !credibleOnly || r.flag)
      .filter(r => !q || (r.player ?? '').toLowerCase().includes(q) || (r.matchup ?? '').toLowerCase().includes(q) || (r.team ?? '').toLowerCase().includes(q))
    const l10 = (r: CockpitRow) => (r.recentForm ? sideRate(r.recentForm.games, r.recentForm.line, isUnder(r), 10) : null)
    return list.slice().sort((a, b) => {
      if (sort === 'l10') return (l10(b) ?? -1) - (l10(a) ?? -1) || (b.ev ?? 0) - (a.ev ?? 0)
      if (sort === 'start') return (a.mins_to_kick ?? 1e9) - (b.mins_to_kick ?? 1e9) || (b.ev ?? 0) - (a.ev ?? 0)
      return Number(!a.flag) - Number(!b.flag) || (b.ev ?? 0) - (a.ev ?? 0)
    })
  }, [inSport, activeMarket, credibleOnly, query, sort])

  const selected = useMemo(() => rows.find(r => r.id === selectedId) ?? rows[0] ?? null, [rows, selectedId])

  const select = useCallback((r: CockpitRow) => { setSelectedId(r.id); setDrawer(true) }, [])

  // phone: the open inspector is a full-screen sheet, so lock the page behind it and let Escape close it
  useEffect(() => {
    if (!drawer || typeof window === 'undefined' || typeof window.matchMedia !== 'function' || !window.matchMedia('(max-width: 959px)').matches) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') setDrawer(false) }
    window.addEventListener('keydown', onKey)
    detailRef.current?.scrollTo?.({ top: 0 })
    return () => { document.body.style.overflow = prev; window.removeEventListener('keydown', onKey) }
  }, [drawer, selected?.id])

  const onListKeyDown = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const idx = rows.findIndex(r => r.id === selected?.id)
    const next = rows[e.key === 'ArrowDown' ? Math.min(idx + 1, rows.length - 1) : Math.max(idx - 1, 0)]
    if (next) setSelectedId(next.id)
  }

  const meta = sport === 'all' ? null : feed?.sports?.[sport] ?? null
  const ageSecs = Math.max(0, Math.round((now - updated) / 1000))
  const ageLabel = ageSecs < 90 ? `${ageSecs}s ago` : `${Math.round(ageSecs / 60)}m ago`
  const statusLine = (() => {
    if (!meta) return null
    if (meta.status === 'unavailable') return `${sport.toUpperCase()}: coming soon.`
    if (meta.stale || meta.status === 'idle') {
      const h = meta.age_minutes != null ? meta.age_minutes / 60 : null
      return `${sport.toUpperCase()} lines${h != null ? ` were last refreshed ${h < 48 ? `${h.toFixed(1)}h` : `${Math.round(h / 24)}d`} ago and` : ''} update around game windows.`
    }
    return null
  })()

  if (feed == null) {
    return (
      <div style={{ ...CARD_STYLE, padding: '36px 20px', textAlign: 'center' }}>
        <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>Prop research is temporarily unavailable</div>
        <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '60ch', margin: '0 auto', lineHeight: 1.55 }}>
          Prop data is temporarily unavailable. This page retries every minute.
        </div>
      </div>
    )
  }

  return (
    <div>
      <section aria-label="Cockpit filters" style={{ display: 'grid', gap: '10px', margin: '0 0 14px' }}>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
          <Segmented<string> label="Market" value={activeMarket} onChange={setMarket}
            options={[{ id: 'all', label: `ALL MARKETS ${inSport.length}` }, ...markets.map(([k, n]) => ({ id: k, label: `${propStatLabel(k).toUpperCase()} ${n}` }))]} />
        </div>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
            <Segmented<SortKey> label="Sort" value={sort} onChange={setSort}
              options={[{ id: 'ev', label: 'BEST +EV' }, { id: 'l10', label: 'L10 HIT%' }, { id: 'start', label: 'STARTS SOON' }]} />
            <Segmented<string> label="Confidence" value={credibleOnly ? 'credible' : 'all'} onChange={v => setCredibleOnly(v === 'credible')}
              options={[{ id: 'all', label: 'ALL SIGNALS' }, { id: 'credible', label: 'CREDIBLE ONLY' }]} />
          </div>
          <input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search player or team" aria-label="Search player or team"
            style={{ background: '#090a0f', border: '1px solid #2e3446', borderRadius: '6px', color: 'var(--chalk)', fontSize: '13px', height: '34px', padding: '0 10px', minWidth: 0, flex: '1 1 180px', maxWidth: '300px' }} />
        </div>
      </section>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '10px', margin: '0 2px 10px', flexWrap: 'wrap' }}>
        <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>{rows.length} PROPS &middot; UPDATED {ageLabel.toUpperCase()}</span>
        <button onClick={onRefresh} disabled={loading} className="btn btn-ghost" style={{ padding: '5px 12px', fontSize: '12px' }}>{loading ? 'Refreshing...' : 'Refresh'}</button>
      </div>
      {statusLine && (
        <div role="note" style={{ border: `1px solid ${HAIR}`, background: '#11131a', borderRadius: '8px', padding: '10px 12px', margin: '0 0 12px', fontSize: '12.5px', color: 'var(--silver)', lineHeight: 1.5 }}>{statusLine}</div>
      )}

      {rows.length === 0 ? (
        <div style={{ ...CARD_STYLE, padding: '36px 20px', textAlign: 'center' }}>
          <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>No props match right now</div>
          <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '60ch', margin: '0 auto', lineHeight: 1.55 }}>
            The scanner re-checks lines every 15 minutes through the betting day. Try another sport, clear the market filter, or switch off Credible only.
          </div>
        </div>
      ) : (
        <div className="cockpit-grid">
          <div role="list" aria-label="Prop candidates" onKeyDown={onListKeyDown} style={{ display: 'grid', gap: '8px', minWidth: 0 }}>
            {rows.map(r => (
              <div role="listitem" key={r.id}>
                <PropCard r={r} active={selected?.id === r.id} onSelect={() => select(r)} />
              </div>
            ))}
          </div>
          <aside ref={detailRef} className={`cockpit-detail${drawer ? ' is-open' : ''}`} aria-label="Prop inspector">
            {selected && <Inspector key={selected.id} r={selected} renderMlbExtras={renderMlbExtras} onClose={() => setDrawer(false)} />}
          </aside>
        </div>
      )}
    </div>
  )
}
