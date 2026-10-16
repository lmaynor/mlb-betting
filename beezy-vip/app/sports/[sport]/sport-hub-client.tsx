'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import type { EvBookOutlierRow, SportProjections } from '@/lib/types'
import { LockedNotice } from '@/components/picks/locked-notice'
import { propStatLabel, SPORT_PILLARS, STATUS_LABEL } from '@/lib/sports'
import { bookDisplay, CARD_STYLE } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import { playerSlug } from '@/lib/sport-assets'
import { SPORT_MODEL_SPECS, LIVE_ENGINE, type ModelValidation } from '@/lib/prop-model-specs'
import { ProjectionsBoard } from './projections-board'

import type { HubSport } from '@/lib/hub-data'

/**
 * NFL, NHL and NBA share this one template: the projection board and the live prices that beat the market as tabs, and the
 * systems behind them (what we project, how, and how it validated) as the page footer.
 */
type Tab = 'projections' | 'prices'
const TABS: { id: Tab; label: string }[] = [
  { id: 'projections', label: 'PROJECTIONS' },
  { id: 'prices', label: 'MARKET PRICES' },
]

/** Everything arrives already cut by the access policy (app/sports/[sport]/page.tsx); `locked*` count the withheld rows. */
interface Props {
  sport: HubSport
  projections: SportProjections | null
  lockedProjections?: number
  outliers: EvBookOutlierRow[]
  lockedPrices?: number
  block: { status: string; age_minutes: number | null } | null
}

export function SportHubClient({ sport, projections, lockedProjections = 0, outliers, lockedPrices = 0, block }: Props) {
  const pillar = SPORT_PILLARS[sport]
  const [tab, setTab] = useState<Tab>('projections')

  // A price is also a model signal when the projection board has a confluence row for the same player, stat and side.
  const confluence = useMemo(() => new Set(
    (projections?.rows ?? []).filter(r => r.confluence && r.side).map(r => `${playerSlug(r.player)}|${r.stat}|${r.side}`),
  ), [projections])

  return (
    <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '28px 24px 64px' }}>
      <header style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', padding: '16px 20px', marginBottom: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px 20px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }} title={`${pillar.description} Source: ${pillar.primarySource}`}>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0, letterSpacing: '-0.01em' }}>{pillar.name}</h1>
          <span className="dell-heading" style={{ fontSize: '10px', color: pillar.accent, border: `1px solid ${pillar.accent}66`, borderRadius: '6px', padding: '2px 8px' }}>{STATUS_LABEL[pillar.status]}</span>
        </div>
        <div role="tablist" style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {TABS.map(t => (
            <button
              key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
              style={{
                fontFamily: 'var(--font-text), sans-serif', fontSize: '12px', fontWeight: 600, letterSpacing: '0.04em',
                padding: '7px 12px', borderRadius: '6px', cursor: 'pointer',
                color: tab === t.id ? '#090a0f' : 'var(--silver)',
                background: tab === t.id ? pillar.accent : 'var(--surface-elevated)',
                border: `1px solid ${tab === t.id ? pillar.accent : '#1e2230'}`,
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </header>

      {tab === 'projections' && (
        <>
          <ProjectionsBoard sport={sport} data={projections} />
          <LockedNotice locked={lockedProjections} shown={projections?.rows.length ?? 0} what={`${pillar.shortName} projections`} plan={`The full board is on the ${pillar.shortName} Pass or All-Access Pro.`} />
        </>
      )}
      {tab === 'prices' && (
        <>
          <PricesTab sport={sport} rows={outliers} block={block} confluence={confluence} />
          <LockedNotice locked={lockedPrices} shown={outliers.length} what={`${pillar.shortName} market prices`} plan={`All of them are on the ${pillar.shortName} Pass or All-Access Pro.`} />
        </>
      )}
      <section id="systems" aria-labelledby="systems-h" style={{ marginTop: '36px' }}>
        <h2 id="systems-h" style={{ fontSize: '15px', fontWeight: 700, color: 'var(--chalk)', margin: '0 0 10px' }}>{pillar.shortName} systems</h2>
        <PropModelsTable sport={sport} />
      </section>
    </div>
  )
}

function Empty({ title, body, cta }: { title: string; body: string; cta?: { href: string; label: string } }) {
  return (
    <div style={{ ...CARD_STYLE, padding: '36px 20px', textAlign: 'center' }}>
      <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>{title}</div>
      <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '62ch', margin: '0 auto', lineHeight: 1.55 }}>{body}</div>
      {cta && <Link href={cta.href} className="btn btn-ghost" style={{ marginTop: '16px' }}>{cta.label}</Link>}
    </div>
  )
}

function ShadowNote({ children }: { children: React.ReactNode }) {
  return (
    <div role="note" style={{ border: '1px solid rgba(56,189,248,0.30)', background: 'rgba(56,189,248,0.06)', borderRadius: '10px', padding: '12px 14px', fontSize: '12.5px', color: 'var(--silver)', lineHeight: 1.55 }}>
      <strong style={{ color: '#38bdf8' }}>PREVIEW.</strong> {children}
    </div>
  )
}

function sweepStatus(block: { status: string; age_minutes: number | null } | null | undefined): string {
  if (block?.status === 'shadow_live') return 'Updating live.'
  if (block?.age_minutes != null) return `Last updated ${(block.age_minutes / 60).toFixed(1)}h ago.`
  return 'No data yet.'
}

const REASON_LABEL: Record<string, string> = { thin_consensus: 'Few books', thin_cons: 'Few books', ev_cap: 'EV too high to trust', thin_reference: 'Few books' }
const reasonLabel = (r: string | null | undefined) => REASON_LABEL[r ?? ''] ?? (r ?? '').replace(/_/g, ' ')

/** Same layout for every sport: books and exchanges pricing a prop better than the consensus of the other books. */
function PricesTab({ sport, rows, block, confluence }: { sport: HubSport; rows: EvBookOutlierRow[]; block: { status: string; age_minutes: number | null } | null; confluence: Set<string> }) {
  const short = SPORT_PILLARS[sport].shortName
  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <ShadowNote>
        {short} sportsbook and exchange prices beating the consensus of the other books on the same player, stat and line. Not yet tracked in the public results. {sweepStatus(block)}
        {' '}Kalshi asks are on the <Link href="/ev" style={{ color: 'var(--link)' }}>+EV terminal</Link>.
      </ShadowNote>
      {rows.length === 0
        ? <Empty title="No prices beating the market" body={`No ${short} prop price is beating the other books at the moment. Rows appear around game windows, before the game starts.`} cta={{ href: '/ev', label: 'Open the +EV terminal' }} />
        : <OutlierTable rows={rows} sport={sport} confluence={confluence} />}
    </div>
  )
}

function OutlierTable({ rows, sport, confluence }: { rows: EvBookOutlierRow[]; sport: HubSport; confluence?: Set<string> }) {
  return (
    <div style={{ ...CARD_STYLE, overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '760px', fontSize: '13px' }}>
        <thead>
          <tr>{['Player / prop', 'Game', 'Side', 'Book', 'Price', 'Fair', 'Books', 'EV', 'Status'].map(h => (
            <th key={h} className="dell-heading" style={{ textAlign: 'left', padding: '10px 12px', fontSize: '10px', color: 'var(--fog)', borderBottom: '1px solid #1e2230' }}>{h}</th>))}</tr>
        </thead>
        <tbody>
          {rows.slice(0, 60).map(r => {
            const b = bookDisplay(r.book)
            const point = r.point ?? r.line
            return (
              <tr key={`${r.game_key}-${r.player_norm ?? r.player}-${r.stat ?? r.market}-${point}-${r.side}-${r.book}`} style={{ borderBottom: '1px solid #151822' }}>
                <td style={{ padding: '10px 12px', color: 'var(--chalk)', fontWeight: 600 }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                    {sport !== 'nfl' && r.player_id != null && <PlayerHeadshot sport={sport} id={r.player_id} name={r.player} size={28} />}
                    <span>{r.player} <span style={{ color: 'var(--fog)', fontWeight: 400 }}>{point} {propStatLabel(r.stat ?? r.market)}</span></span>
                    {confluence?.has(`${playerSlug(r.player)}|${r.stat ?? r.market}|${String(r.side).toLowerCase()}`) && <span className="dell-heading" title="The projection also favors this side" style={{ fontSize: '9px', color: '#57F287', border: '1px solid rgba(87,242,135,0.35)', borderRadius: '4px', padding: '1px 5px', whiteSpace: 'nowrap' }}>MODEL + MARKET</span>}
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
                <td className="dell-heading" style={{ padding: '10px 12px', fontSize: '9.5px', color: r.flag ? '#f59e0b' : 'var(--fog)' }}>{r.flag ? 'FLAGGED' : reasonLabel(r.reason)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const fmtNum = (x: number | null, d: number) => (x == null ? '--' : x.toFixed(d))
const pair = (a: number | null, b: number | null, d: number) => (a == null ? '--' : `${a.toFixed(d)} vs ${fmtNum(b, d)}`)

function PropModelsTable({ sport }: { sport: HubSport }) {
  const spec = SPORT_MODEL_SPECS[sport]
  const pillar = SPORT_PILLARS[sport]
  const th = { textAlign: 'left', padding: '9px 10px', fontSize: '10px', color: 'var(--fog)', borderBottom: '1px solid #1e2230', background: '#11131a', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
  const td = { padding: '10px', borderBottom: '1px solid #151822', verticalAlign: 'top' } as const
  const v = (m: { validation: ModelValidation | null }) => m.validation
  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <div style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', boxShadow: '0 1px 2px rgba(0,0,0,0.45)', overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '1020px', fontSize: '12.5px' }}>
          <thead>
            <tr>{['Market', 'Distribution', 'Validation sample', 'MAE (model vs baseline)', 'CRPS (model vs baseline)', 'Brier', 'ECE', 'Result'].map(h => <th key={h} className="dell-heading" style={th}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {spec.models.map(m => (
              <tr key={m.key}>
                <td style={{ ...td, color: 'var(--chalk)', fontWeight: 600, whiteSpace: 'nowrap' }}>{m.label}</td>
                <td style={{ ...td, color: 'var(--silver)', minWidth: '210px' }}>{m.distribution}</td>
                <td style={{ ...td, color: 'var(--silver)', minWidth: '190px' }}>{v(m)?.sample ?? '--'}</td>
                <td className="mono" style={{ ...td, whiteSpace: 'nowrap' }}>{v(m) ? pair(v(m)!.mae, v(m)!.baselineMae, 3) : '--'}</td>
                <td className="mono" style={{ ...td, whiteSpace: 'nowrap' }}>{v(m) ? pair(v(m)!.crps, v(m)!.baselineCrps, 3) : '--'}</td>
                <td className="mono" style={td}>{v(m) ? fmtNum(v(m)!.brier, 4) : '--'}</td>
                <td className="mono" style={td}>{v(m) ? fmtNum(v(m)!.ece, 4) : '--'}</td>
                <td style={{ ...td, color: 'var(--silver)', minWidth: '200px' }}>{m.verdict}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', padding: '14px 16px', display: 'grid', gap: '12px', fontSize: '12.5px', color: 'var(--silver)', lineHeight: 1.55 }}>
        <div>
          <div className="dell-heading" style={{ fontSize: '10px', color: pillar.accent, marginBottom: '6px' }}>KEY FEATURES</div>
          <ul style={{ margin: 0, paddingLeft: '18px' }}>{spec.features.map(f => <li key={f}>{f}</li>)}</ul>
        </div>
        <div><strong style={{ color: 'var(--chalk)' }}>Against prices.</strong> {spec.marketVerdict}</div>
        <div><strong style={{ color: 'var(--chalk)' }}>What the daily board runs.</strong> {LIVE_ENGINE.summary}</div>
        <div style={{ color: 'var(--fog)', fontSize: '11.5px' }}>Source: {spec.sources} Baselines are the best trailing-average forecaster in each study. MAE and CRPS: lower is better.</div>
      </div>
    </div>
  )
}
