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
      <div className="text-text-primary font-semibold mb-1.5">{title}</div>
      <div className="text-text-secondary text-[13px] max-w-[62ch] mx-auto leading-[1.55]">{body}</div>
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
    <div className="grid gap-4">
      <div role="note" className="border border-[color:rgba(56,189,248,0.30)] bg-[rgba(56,189,248,0.06)] rounded-[10px] py-3 px-3.5 text-[12.5px] text-[var(--color-neutral-300)] leading-[1.55]">
        <strong className="text-[#38bdf8]">PREVIEW.</strong> {short} sportsbook and exchange prices beating the consensus of the other books on the same player, stat and line. Not yet tracked in the public results. {sweepStatus(block)}
        {' '}Kalshi asks are on the <Link href="/ev" style={{ color: 'var(--link)' }}>Odds Terminal</Link>.
      </div>
      {rows.length === 0
        ? <Empty title="No prices beating the market" body={`No ${short} prop price is beating the other books at the moment. Rows appear around game windows, before the game starts.`} cta={{ href: '/ev', label: 'Open the Odds Terminal' }} />
        : <OutlierTable rows={rows} sport={sport} confluence={confluence} />}
      <LockedNotice locked={locked} shown={rows.length} what={`${short} market prices`} plan={`All of them are on the ${short} Pass or All-Access Pro.`} />
    </div>
  )
}

function OutlierTable({ rows, sport, confluence }: { rows: EvBookOutlierRow[]; sport: ProjectionSport; confluence?: Set<string> }) {
  return (
    <div style={{ ...CARD_STYLE, overflowX: 'auto' }}>
      <table className="w-full border-collapse min-w-[760px] text-[13px]">
        <thead>
          <tr>{['Player / prop', 'Game', 'Side', 'Book', 'Price', 'Fair', 'Books', 'EV', 'Status'].map(h => (
            <th key={h} className="ui-heading text-left py-2.5 px-3 text-[10px] text-text-secondary border-b border-b-[color:var(--color-separator-border)]">{h}</th>))}</tr>
        </thead>
        <tbody>
          {rows.filter(r => !isOffshore(r.book)).slice(0, 60).map(r => {
            const b = bookDisplay(r.book)
            const point = r.point ?? r.line
            return (
              <tr key={`${r.game_key}-${r.player_norm ?? r.player}-${r.stat ?? r.market}-${point}-${r.side}-${r.book}`} style={{ borderBottom: '1px solid var(--color-border-table)' }}>
                <td className="py-2.5 px-3 text-text-primary font-semibold">
                  <span className="inline-flex items-center gap-2">
                    {headshotOf(sport, r) != null && <PlayerHeadshot sport={sport} id={headshotOf(sport, r)} name={r.player} size={28} />}
                    <span>{r.player} <span className="text-text-secondary font-normal">{point} {propStatLabel(r.stat ?? r.market)}</span></span>
                    {confluence?.has(confluenceKey(r.player, String(r.stat ?? r.market), String(r.side))) && <span className="ui-heading text-[10px] text-[#57F287] border border-[color:rgba(87,242,135,0.35)] rounded-[4px] py-[1px] px-[5px] whitespace-nowrap" title="The projection also favors this side">MODEL + MARKET</span>}
                  </span>
                </td>
                <td className="mono py-2.5 px-3 text-[var(--color-neutral-300)]">{(r.game_key ?? '').split(':')[2]}</td>
                <td className="py-2.5 px-3 text-[var(--color-neutral-300)]">{String(r.side).toUpperCase()}</td>
                <td className="py-2.5 px-3">
                  <span style={{ fontSize: '11px', fontWeight: 700, color: b.color, background: b.bg, border: `1px solid ${b.color}55`, borderRadius: '6px', padding: '3px 8px' }}>{b.label}</span>
                </td>
                <td className="mono py-2.5 px-3">{formatOdds(r.american)}</td>
                <td className="mono py-2.5 px-3">{r.p_fair != null ? `${(r.p_fair * 100).toFixed(1)}%` : '--'}</td>
                <td className="mono" style={{ padding: '10px 12px', color: r.n_books >= 3 ? 'var(--color-neutral-300)' : 'var(--warn)' }}>{r.n_books}</td>
                <td className="mono" style={{ padding: '10px 12px', fontWeight: 700, color: r.ev >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{r.ev >= 0 ? '+' : ''}{(r.ev * 100).toFixed(1)}%</td>
                <td className="ui-heading" style={{ padding: '10px 12px', fontSize: '10px', color: r.flag ? '#f59e0b' : 'var(--color-text-secondary)' }}>{r.flag ? 'FLAGGED' : reasonLabel(r.reason)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
