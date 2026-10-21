'use client'

import { isOffshore } from '@/lib/offshore'
import { useMemo } from 'react'
import Link from 'next/link'
import { LockedNotice } from '@/components/picks/locked-notice'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import { formatOdds } from '@/lib/odds'
import { confluenceKey } from '@/lib/board-rows'
import { propStatLabel, SPORT_PILLARS, type ProjectionSport } from '@/lib/sports'
import { bookDisplay, CARD_STYLE } from '@/lib/tokens'
import type { EvBookOutlierRow } from '@/lib/types'

function Empty({ title, body, cta }: { title: string; body: string; cta?: { href: string; label: string } }) {
  return (
    <div style={{ ...CARD_STYLE, padding: '36px 20px', textAlign: 'center' }}>
      <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>{title}</div>
      <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '62ch', margin: '0 auto', lineHeight: 1.55 }}>{body}</div>
      {cta && <Link href={cta.href} className="btn btn-ghost" style={{ marginTop: '16px' }}>{cta.label}</Link>}
    </div>
  )
}

function sweepStatus(block: { status: string; age_minutes: number | null } | null | undefined): string {
  if (block?.status === 'shadow_live') return 'Updating live.'
  if (block?.age_minutes != null) return `Last updated ${(block.age_minutes / 60).toFixed(1)}h ago.`
  return 'No data yet.'
}

/** NFL price rows carry no player id (the feed's id is not a league id): they use the name-resolved token; the others use their league id. */
const headshotOf = (sport: ProjectionSport, r: EvBookOutlierRow) => (sport === 'nfl' ? r.headshot_id : (r.player_id ?? r.headshot_id)) ?? null

const REASON_LABEL: Record<string, string> = { thin_consensus: 'Few books', thin_cons: 'Few books', ev_cap: 'EV too high to trust', thin_reference: 'Few books' }
const reasonLabel = (r: string | null | undefined) => REASON_LABEL[r ?? ''] ?? (r ?? '').replace(/_/g, ' ')

/**
 * Books and exchanges pricing a prop better than the consensus of the other books: the same table for every projection
 * sport. `confluenceKeys` marks the rows the projection board also favors (a plain array so a server page can pass it).
 */
export function MarketPrices({ sport, rows, locked = 0, block, confluenceKeys = [] }: {
  sport: ProjectionSport
  rows: EvBookOutlierRow[]
  locked?: number
  block: { status: string; age_minutes: number | null } | null
  confluenceKeys?: string[]
}) {
  const short = SPORT_PILLARS[sport].shortName
  const confluence = useMemo(() => new Set(confluenceKeys), [confluenceKeys])
  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <div role="note" style={{ border: '1px solid rgba(56,189,248,0.30)', background: 'rgba(56,189,248,0.06)', borderRadius: '10px', padding: '12px 14px', fontSize: '12.5px', color: 'var(--silver)', lineHeight: 1.55 }}>
        <strong style={{ color: '#38bdf8' }}>PREVIEW.</strong> {short} sportsbook and exchange prices beating the consensus of the other books on the same player, stat and line. Not yet tracked in the public results. {sweepStatus(block)}
        {' '}Kalshi asks are on the <Link href="/ev" style={{ color: 'var(--link)' }}>+EV terminal</Link>.
      </div>
      {rows.length === 0
        ? <Empty title="No prices beating the market" body={`No ${short} prop price is beating the other books at the moment. Rows appear around game windows, before the game starts.`} cta={{ href: '/ev', label: 'Open the +EV terminal' }} />
        : <OutlierTable rows={rows} sport={sport} confluence={confluence} />}
      <LockedNotice locked={locked} shown={rows.length} what={`${short} market prices`} plan={`All of them are on the ${short} Pass or All-Access Pro.`} />
    </div>
  )
}

function OutlierTable({ rows, sport, confluence }: { rows: EvBookOutlierRow[]; sport: ProjectionSport; confluence?: Set<string> }) {
  return (
    <div style={{ ...CARD_STYLE, overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '760px', fontSize: '13px' }}>
        <thead>
          <tr>{['Player / prop', 'Game', 'Side', 'Book', 'Price', 'Fair', 'Books', 'EV', 'Status'].map(h => (
            <th key={h} className="dell-heading" style={{ textAlign: 'left', padding: '10px 12px', fontSize: '10px', color: 'var(--fog)', borderBottom: '1px solid #1e2230' }}>{h}</th>))}</tr>
        </thead>
        <tbody>
          {rows.filter(r => !isOffshore(r.book)).slice(0, 60).map(r => {
            const b = bookDisplay(r.book)
            const point = r.point ?? r.line
            return (
              <tr key={`${r.game_key}-${r.player_norm ?? r.player}-${r.stat ?? r.market}-${point}-${r.side}-${r.book}`} style={{ borderBottom: '1px solid #151822' }}>
                <td style={{ padding: '10px 12px', color: 'var(--chalk)', fontWeight: 600 }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                    {headshotOf(sport, r) != null && <PlayerHeadshot sport={sport} id={headshotOf(sport, r)} name={r.player} size={28} />}
                    <span>{r.player} <span style={{ color: 'var(--fog)', fontWeight: 400 }}>{point} {propStatLabel(r.stat ?? r.market)}</span></span>
                    {confluence?.has(confluenceKey(r.player, String(r.stat ?? r.market), String(r.side))) && <span className="dell-heading" title="The projection also favors this side" style={{ fontSize: '10px', color: '#57F287', border: '1px solid rgba(87,242,135,0.35)', borderRadius: '4px', padding: '1px 5px', whiteSpace: 'nowrap' }}>MODEL + MARKET</span>}
                  </span>
                </td>
                <td className="mono" style={{ padding: '10px 12px', color: 'var(--silver)' }}>{(r.game_key ?? '').split(':')[2]}</td>
                <td style={{ padding: '10px 12px', color: 'var(--silver)' }}>{String(r.side).toUpperCase()}</td>
                <td style={{ padding: '10px 12px' }}>
                  <span style={{ fontSize: '11px', fontWeight: 700, color: b.color, background: b.bg, border: `1px solid ${b.color}55`, borderRadius: '6px', padding: '3px 8px' }}>{b.label}</span>
                </td>
                <td className="mono" style={{ padding: '10px 12px' }}>{formatOdds(r.american)}</td>
                <td className="mono" style={{ padding: '10px 12px' }}>{r.p_fair != null ? `${(r.p_fair * 100).toFixed(1)}%` : '--'}</td>
                <td className="mono" style={{ padding: '10px 12px', color: r.n_books >= 3 ? 'var(--silver)' : 'var(--warn)' }}>{r.n_books}</td>
                <td className="mono" style={{ padding: '10px 12px', fontWeight: 700, color: r.ev >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{r.ev >= 0 ? '+' : ''}{(r.ev * 100).toFixed(1)}%</td>
                <td className="dell-heading" style={{ padding: '10px 12px', fontSize: '10px', color: r.flag ? '#f59e0b' : 'var(--fog)' }}>{r.flag ? 'FLAGGED' : reasonLabel(r.reason)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
