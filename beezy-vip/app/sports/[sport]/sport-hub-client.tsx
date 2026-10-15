'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import type { Bet, EvBookOutlierRow, EvFeed, EvNflRow, EvPropsBlock, SystemStats, TodaySlate } from '@/lib/types'
import type { SparklinePoint } from '@/lib/betting-api'
import { propStatLabel, SPORT_PILLARS, STATUS_LABEL, type SportSlug } from '@/lib/sports'
import { getPickSystemByKey } from '@/lib/pick-systems'
import { beezyscore } from '@/lib/beezy-score'
import { bookDisplay, CARD_STYLE, pickLabel, SYSTEM_COLOR, SYSTEM_LABEL, teamAbbrev } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'
import { PicksTable } from '@/components/picks/picks-table'
import { SystemSparkline } from '@/components/landing/system-sparkline'
import { PlayerHeadshot } from '@/components/ui/sport-assets'

const GATE = 200
type Tab = 'today' | 'models' | 'slate'
const TABS: { id: Tab; label: string }[] = [
  { id: 'today', label: "TODAY'S PICKS & +EV" },
  { id: 'models', label: 'MODELS & SYSTEMS' },
  { id: 'slate', label: 'SLATE & MATCHUPS' },
]

interface Props {
  sport: SportSlug
  picks: Bet[]
  stats: SystemStats[]
  sparklines: Record<string, SparklinePoint[]>
  slate: TodaySlate | null
  feed: EvFeed | null
}

export function SportHubClient(props: Props) {
  const pillar = SPORT_PILLARS[props.sport]
  const [tab, setTab] = useState<Tab>('today')

  return (
    <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '28px 24px 64px' }}>
      <header style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', padding: '16px 20px', marginBottom: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px 20px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }} title={`${pillar.description} Source: ${pillar.primarySource}`}>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0, letterSpacing: '-0.01em' }}>{pillar.name}</h1>
          <span className="dell-heading" style={{ fontSize: '10px', color: pillar.accent, border: `1px solid ${pillar.accent}66`, borderRadius: '6px', padding: '2px 8px' }}>{pillar.seasonBadge}</span>
          <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>{STATUS_LABEL[pillar.status]}</span>
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

      {tab === 'today' && <TodayTab {...props} />}
      {tab === 'models' && <ModelsTab {...props} />}
      {tab === 'slate' && <SlateTab {...props} />}
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

// -- Tab 1 --------------------------------------------------------------------

function TodayTab({ sport, picks, feed }: Props) {
  if (sport === 'mlb') return <MlbToday picks={picks} feed={feed} />
  if (sport === 'nfl') return <NflToday feed={feed} />
  const p = SPORT_PILLARS[sport]
  const block = sport === 'nhl' || sport === 'nba' ? feed?.[sport] : null
  if (block && block.rows.length > 0) return <PropsToday sport={sport as 'nhl' | 'nba'} block={block} />
  return (
    <Empty
      title={`${p.shortName}: no open props rows`}
      body={sport === 'nba'
        ? 'No NBA props quote is beating its consensus right now. Box scores are ingested nightly and picks are not yet tracked for NBA. Rows appear here when the props scanner finds one before tip-off.'
        : 'No NHL props quote is beating its consensus right now. Rows appear here when the props scanner finds one before puck drop; NHL picks are not yet tracked in the public ledger.'}
      cta={{ href: '/ev', label: 'Open the +EV terminal' }}
    />
  )
}

function MlbToday({ picks, feed }: { picks: Bet[]; feed: EvFeed | null }) {
  const [market, setMarket] = useState<string>('ALL')
  const markets = SPORT_PILLARS.mlb.markets.filter(m => m.system)
  const filtered = useMemo(() => picks
    .filter(p => market === 'ALL' || p.system === market)
    .sort((a, b) => beezyscore(b) - beezyscore(a)), [picks, market])
  const ev = (feed?.mlb.rows ?? []).filter(r => r.result == null).slice(0, 6)

  return (
    <div style={{ display: 'grid', gap: '26px' }}>
      <section>
        <SectionTitle title="Model card" sub="Kelly-triggered picks from the MLB systems, ranked by Beezy Score." />
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '14px' }}>
          {['ALL', ...markets.map(m => m.key)].map(k => (
            <button key={k} onClick={() => setMarket(k)} aria-pressed={market === k}
              style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '11.5px', fontWeight: 600, padding: '6px 11px', borderRadius: '6px', cursor: 'pointer',
                color: market === k ? '#090a0f' : (SYSTEM_COLOR[k] ?? 'var(--silver)'),
                background: market === k ? (SYSTEM_COLOR[k] ?? 'var(--signal)') : 'var(--surface-elevated)',
                border: `1px solid ${market === k ? (SYSTEM_COLOR[k] ?? 'var(--signal)') : '#1e2230'}` }}>
              {k === 'ALL' ? 'ALL' : (SYSTEM_LABEL[k] ?? k).toUpperCase()}
            </button>
          ))}
        </div>
        {filtered.length === 0
          ? <Empty title="No picks on the card" body="Today's card publishes after the ~11:00 AM CT model run. Try another market or check back after lineups post." />
          : <PicksTable bets={filtered} sort="score" dir="desc" />}
      </section>

      <section>
        <SectionTitle title="Live +EV alerts" sub="Soft lines the 15-minute scanner flagged against consensus. Paper-tracked at the flagged price." />
        {ev.length === 0 ? (
          <Empty title="No open +EV alerts" body="Nothing above +3% is open right now." cta={{ href: '/ev', label: 'Open the +EV terminal' }} />
        ) : (
          <div style={{ ...CARD_STYLE, overflow: 'hidden' }}>
            {ev.map(r => {
              const b = bookDisplay(r.book)
              const bet = { ...r, system: 'EV', game_pk: r.game_pk ?? 0, model_prob: r.model_prob ?? 0, market_prob: r.market_prob ?? 0, kelly_triggered: true, paper: true } as unknown as Bet
              return (
                <div key={r.id} style={{ display: 'flex', gap: '14px', alignItems: 'center', padding: '11px 16px', borderBottom: '1px solid #151822', flexWrap: 'wrap' }}>
                  <span style={{ flex: '1 1 260px', minWidth: 0, color: 'var(--chalk)', fontSize: '13.5px', fontWeight: 600 }}>{pickLabel(bet)}</span>
                  <span style={{ fontSize: '11px', fontWeight: 700, color: b.color, background: b.bg, border: `1px solid ${b.color}55`, borderRadius: '6px', padding: '3px 8px' }}>{b.label}</span>
                  <span className="mono" style={{ fontSize: '14px', color: 'var(--chalk)', minWidth: '52px' }}>{formatOdds(r.odds)}</span>
                  <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: 'var(--signal)' }}>+{((r.edge ?? 0) * 100).toFixed(1)}% EV</span>
                </div>
              )
            })}
            <div style={{ padding: '10px 16px' }}><Link href="/ev" style={{ color: 'var(--link)', fontSize: '13px' }}>All +EV alerts, sized to your bankroll &rarr;</Link></div>
          </div>
        )}
      </section>
    </div>
  )
}

function ShadowNote({ children }: { children: React.ReactNode }) {
  return (
    <div role="note" style={{ border: '1px solid rgba(56,189,248,0.30)', background: 'rgba(56,189,248,0.06)', borderRadius: '10px', padding: '12px 14px', fontSize: '12.5px', color: 'var(--silver)', lineHeight: 1.55 }}>
      <strong style={{ color: '#38bdf8' }}>MARKET SCANNER.</strong> {children}
    </div>
  )
}

function sweepStatus(block: { status: string; age_minutes: number | null } | null | undefined): string {
  if (block?.status === 'shadow_live') return 'Sweep is running.'
  if (block?.age_minutes != null) return `Sweep idle; last run ${(block.age_minutes / 60).toFixed(1)}h ago.`
  return 'No sweep output available.'
}

/** Sink A table (NFL book_outliers, NHL/NBA props): a book/exchange quote vs the consensus of the OTHER books. */
function OutlierTable({ rows, sport }: { rows: EvBookOutlierRow[]; sport: 'nfl' | 'nhl' | 'nba' }) {
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
                <td className="dell-heading" style={{ padding: '10px 12px', fontSize: '9.5px', color: r.flag ? '#f59e0b' : 'var(--fog)' }}>{r.flag ? 'FLAGGED' : (r.reason ?? '').replace(/_/g, ' ')}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function PropsToday({ sport, block }: { sport: 'nhl' | 'nba'; block: EvPropsBlock }) {
  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <ShadowNote>
        {SPORT_PILLARS[sport].shortName} sportsbook quotes beating the de-vigged consensus of the other books at the same player, stat and line. These are
        market-consensus signals: no orders are placed and they are not yet part of the public track record. {sweepStatus(block)}
      </ShadowNote>
      <OutlierTable rows={block.rows} sport={sport} />
    </div>
  )
}

function NflToday({ feed }: { feed: EvFeed | null }) {
  const nfl = feed?.nfl
  const rows: EvNflRow[] = nfl?.rows ?? []
  const outliers: EvBookOutlierRow[] = nfl?.book_outliers ?? []
  return (
    <div style={{ display: 'grid', gap: '26px' }}>
      <ShadowNote>
        Sportsbook and exchange quotes versus a de-vigged sportsbook consensus, plus Kalshi asks net of fee. These are market-consensus signals: no
        orders are placed and they are not yet part of the public track record. {sweepStatus(nfl)}
      </ShadowNote>

      <section>
        <SectionTitle title={'NFL Sportsbook & Exchange Outliers (Sink A)'} sub="A book or exchange quote beating the leave-self-out consensus of the other books, from the same pull as the Kalshi comparison." />
        {outliers.length === 0 ? (
          <Empty title="No outliers right now" body="The sweep runs around game windows. Rows appear when a book or exchange price beats a consensus of at least four other books before kickoff." />
        ) : (
          <OutlierTable rows={outliers} sport="nfl" />
        )}
      </section>

      <section>
        <SectionTitle title="Kalshi Order-Book Comparison" sub="Kalshi asks vs the sportsbook consensus at the same line. Needs Kalshi to be reachable from the sweep; otherwise this stays empty." />
        {rows.length === 0 ? (
          <Empty title="No Kalshi rows" body="Rows appear when a Kalshi ask beats the consensus before kickoff." />
        ) : (
          <div style={{ ...CARD_STYLE, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '720px', fontSize: '13px' }}>
              <thead>
                <tr>{['Player / prop', 'Game', 'Side', 'Ask', 'Fair', 'Books', 'Net EV', 'Status'].map(h => (
                  <th key={h} className="dell-heading" style={{ textAlign: 'left', padding: '10px 12px', fontSize: '10px', color: 'var(--fog)', borderBottom: '1px solid #1e2230' }}>{h}</th>))}</tr>
              </thead>
              <tbody>
                {rows.slice(0, 60).map(r => (
                  <tr key={`${r.ticker}-${r.side}`} style={{ borderBottom: '1px solid #151822' }}>
                    <td style={{ padding: '10px 12px', color: 'var(--chalk)', fontWeight: 600 }}>{r.player} <span style={{ color: 'var(--fog)', fontWeight: 400 }}>{r.strike} {SYSTEM_LABEL[(r.stat ?? '').toUpperCase()] ?? r.stat}</span></td>
                    <td className="mono" style={{ padding: '10px 12px', color: 'var(--silver)' }}>{(r.game_key ?? '').split(':')[2]}</td>
                    <td style={{ padding: '10px 12px', color: 'var(--silver)' }}>{r.side}</td>
                    <td className="mono" style={{ padding: '10px 12px' }}>{Math.round(r.ask * 100)}&cent;</td>
                    <td className="mono" style={{ padding: '10px 12px' }}>{r.fair_prob != null ? `${(r.fair_prob * 100).toFixed(1)}%` : '--'}</td>
                    <td className="mono" style={{ padding: '10px 12px', color: (r.n_books ?? 0) >= 3 ? 'var(--silver)' : 'var(--warn)' }}>{r.n_books ?? 0}</td>
                    <td className="mono" style={{ padding: '10px 12px', fontWeight: 700, color: r.net_ev >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{r.net_ev >= 0 ? '+' : ''}{(r.net_ev * 100).toFixed(1)}%</td>
                    <td className="dell-heading" style={{ padding: '10px 12px', fontSize: '9.5px', color: r.flag ? '#f59e0b' : 'var(--fog)' }}>{r.flag ? 'FLAGGED' : (r.reason ?? '').replace(/_/g, ' ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

// -- Tab 2 --------------------------------------------------------------------

function ModelsTab({ sport, stats, sparklines }: Props) {
  const pillar = SPORT_PILLARS[sport]
  const byKey = new Map(stats.map(s => [s.system, s]))
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(290px, 1fr))', gap: '14px' }}>
      {pillar.markets.map(m => {
        const s = m.system ? byKey.get(m.system) : undefined
        const slug = m.system ? getPickSystemByKey(m.system)?.slug : undefined
        const color = (m.system && SYSTEM_COLOR[m.system]) || pillar.accent
        const n = s ? Number(s.total_bets) : 0
        const roi = s ? Number(s.roi) : null
        const card = (
          <div className="card-hover" style={{ ...CARD_STYLE, padding: '16px', height: '100%', display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', alignItems: 'flex-start' }}>
              <div style={{ color: 'var(--chalk)', fontWeight: 600, fontSize: '14px', lineHeight: 1.3 }}>{m.label}</div>
              <span className="dell-heading" style={{ fontSize: '9px', color, border: `1px solid ${color}55`, borderRadius: '4px', padding: '2px 6px', whiteSpace: 'nowrap' }}>{m.status}</span>
            </div>
            {s ? (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
                  <Stat label="BETS" value={String(n)} />
                  <Stat label="HIT RATE" value={`${Number(s.win_rate).toFixed(1)}%`} />
                  <Stat label="ROI" value={`${roi! > 0 ? '+' : ''}${roi!.toFixed(1)}%`} color={roi! >= 0 ? 'var(--signal)' : 'var(--loss)'} />
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10.5px', color: 'var(--fog)', marginBottom: '5px' }}>
                    <span>200-bet gate</span><span className="mono">{Math.min(n, GATE)}/{GATE}</span>
                  </div>
                  <div style={{ height: '5px', background: '#1b1f2b', borderRadius: '3px', overflow: 'hidden' }}>
                    <div style={{ width: `${Math.min(100, (n / GATE) * 100)}%`, height: '100%', background: n >= GATE ? 'var(--signal)' : color }} />
                  </div>
                </div>
                <SystemSparkline data={sparklines[m.system!] ?? []} positive={roi! >= 0} />
              </>
            ) : (
              <div style={{ fontSize: '12px', color: 'var(--fog)' }}>{m.system ? 'No settled bets yet.' : 'Not tracked yet.'}</div>
            )}
          </div>
        )
        return slug ? <Link key={m.key} href={`/picks/mlb/${slug}`} style={{ textDecoration: 'none' }}>{card}</Link> : <div key={m.key}>{card}</div>
      })}
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className="dell-heading" style={{ fontSize: '8.5px', color: 'var(--fog)', marginBottom: '3px' }}>{label}</div>
      <div className="mono" style={{ fontSize: '14px', fontWeight: 700, color: color ?? 'var(--chalk)' }}>{value}</div>
    </div>
  )
}

// -- Tab 3 --------------------------------------------------------------------

function SlateTab({ sport, slate, feed }: Props) {
  if (sport === 'mlb') {
    const games = slate?.games ?? []
    if (games.length === 0) return <Empty title="No slate available" body="The schedule did not load or there are no games today." />
    return (
      <div>
        <SectionTitle title={`Today's slate \u00b7 ${slate!.total_games} games`} sub="Probable starters and the number of tracked picks per game." />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '12px' }}>
          {games.map(g => (
            <div key={g.game_pk} style={{ ...CARD_STYLE, padding: '14px 16px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span className="mono" style={{ color: 'var(--chalk)', fontWeight: 700 }}>{teamAbbrev(g.away_team)} @ {teamAbbrev(g.home_team)}</span>
                <span className="mono" style={{ fontSize: '11.5px', color: 'var(--fog)' }}>{g.start_time ?? 'TBD'}</span>
              </div>
              <div style={{ fontSize: '12px', color: 'var(--silver)', marginTop: '8px', lineHeight: 1.6 }}>
                <div>{g.away_pitcher ?? 'TBD'} <span style={{ color: 'var(--fog)' }}>vs</span> {g.home_pitcher ?? 'TBD'}</div>
                <div style={{ color: g.picks.length ? 'var(--signal)' : 'var(--fog)' }}>{g.picks.length} tracked pick{g.picks.length === 1 ? '' : 's'}</div>
              </div>
            </div>
          ))}
        </div>
        <p className="times" style={{ marginTop: '16px', fontSize: '13px', color: 'var(--fog)' }}>
          Park and weather context: <Link href="/tools/nrfi-conditions" style={{ color: 'var(--link)' }}>NRFI conditions</Link> &middot; <Link href="/tools/pitcher-matchups" style={{ color: 'var(--link)' }}>pitcher matchups</Link>
        </p>
      </div>
    )
  }
  if (sport === 'nfl' || sport === 'nhl' || sport === 'nba') {
    const items: { game_key: string | null; mins: number | null | undefined; flag: boolean }[] = []
    if (sport === 'nfl') {
      for (const r of feed?.nfl?.rows ?? []) items.push({ game_key: r.game_key, mins: r.mins_to_kick, flag: r.flag })
      for (const r of feed?.nfl?.book_outliers ?? []) items.push({ game_key: r.game_key, mins: r.mins_to_kick, flag: !!r.flag })
    } else {
      for (const r of feed?.[sport]?.rows ?? []) items.push({ game_key: r.game_key, mins: r.mins_to_kick, flag: !!r.flag })
    }
    const byGame = new Map<string, { n: number; flagged: number; mins: number }>()
    for (const it of items) {
      const k = (it.game_key ?? '').split(':')[2] ?? '?'
      const cur = byGame.get(k) ?? { n: 0, flagged: 0, mins: it.mins ?? Infinity }
      cur.n++; if (it.flag) cur.flagged++; cur.mins = Math.min(cur.mins, it.mins ?? Infinity)
      byGame.set(k, cur)
    }
    if (byGame.size === 0) return <Empty title="No upcoming games in the sweep" body="Games appear here while the sweep has open rows before the game starts." />
    const lead = sport === 'nfl' ? 'kick in' : 'starts in'
    return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '12px' }}>
        {[...byGame.entries()].sort((a, b) => a[1].mins - b[1].mins).map(([k, v]) => (
          <div key={k} style={{ ...CARD_STYLE, padding: '14px 16px' }}>
            <div className="mono" style={{ color: 'var(--chalk)', fontWeight: 700 }}>{k.replace('@', ' @ ')}</div>
            <div style={{ fontSize: '12px', color: 'var(--fog)', marginTop: '6px' }}>{Number.isFinite(v.mins) ? `${lead} ${v.mins < 90 ? `${Math.round(v.mins)}m` : `${(v.mins / 60).toFixed(1)}h`}` : 'time TBD'} &middot; {v.n} comparable rows &middot; {v.flagged} flagged</div>
          </div>
        ))}
      </div>
    )
  }
  return <Empty title="No schedule feed" body="This sport has no live schedule or consensus-line feed wired up yet." />
}

function SectionTitle({ title, sub }: { title: string; sub?: string }) {
  return (
    <div style={{ marginBottom: '14px' }}>
      <h2 className="dell-display" style={{ fontSize: '20px', color: 'var(--chalk)' }}>{title}</h2>
      {sub && <p className="times" style={{ fontSize: '13px', color: 'var(--fog)', marginTop: '5px' }}>{sub}</p>}
    </div>
  )
}
