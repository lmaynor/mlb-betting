'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import type { Bet, EvFeed, EvNflRow, SystemStats, TodaySlate } from '@/lib/types'
import type { SparklinePoint } from '@/lib/betting-api'
import { SPORT_PILLARS, STATUS_LABEL, type SportSlug } from '@/lib/sports'
import { getPickSystemByKey } from '@/lib/pick-systems'
import { beezyscore } from '@/lib/beezy-score'
import { bookDisplay, CARD_STYLE, pickLabel, SYSTEM_COLOR, SYSTEM_LABEL, teamAbbrev } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'
import { PicksTable } from '@/components/picks/picks-table'
import { SystemSparkline } from '@/components/landing/system-sparkline'

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
      <section style={{ ...CARD_STYLE, borderRadius: '14px', padding: '28px', marginBottom: '22px', background: `linear-gradient(135deg, ${pillar.accentBg}, #11131a 60%)`, borderColor: `${pillar.accent}44` }}>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap', marginBottom: '12px' }}>
          <span className="dell-heading" style={{ fontSize: '10.5px', color: pillar.accent, border: `1px solid ${pillar.accent}66`, borderRadius: '6px', padding: '3px 9px' }}>{pillar.seasonBadge}</span>
          <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)' }}>{STATUS_LABEL[pillar.status]}</span>
        </div>
        <h1 className="dell-display" style={{ fontSize: 'clamp(26px, 4vw, 38px)', color: 'var(--chalk)' }}>{pillar.name}</h1>
        <p className="times" style={{ color: 'var(--silver)', fontSize: '15px', maxWidth: '74ch', marginTop: '10px' }}>{pillar.description}</p>
        <div className="mono" style={{ fontSize: '12px', color: 'var(--fog)', marginTop: '14px' }}>SOURCE &middot; {pillar.primarySource}</div>
      </section>

      <div role="tablist" style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '20px', borderBottom: '1px solid #1e2230', paddingBottom: '12px' }}>
        {TABS.map(t => (
          <button
            key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
            style={{
              fontFamily: 'var(--font-text), sans-serif', fontSize: '12px', fontWeight: 600, letterSpacing: '0.04em',
              padding: '8px 14px', borderRadius: '6px', cursor: 'pointer',
              color: tab === t.id ? '#090a0f' : 'var(--silver)',
              background: tab === t.id ? pillar.accent : 'var(--surface-elevated)',
              border: `1px solid ${tab === t.id ? pillar.accent : '#1e2230'}`,
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

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
  return (
    <Empty
      title={`${p.shortName}: nothing to show yet`}
      body={sport === 'nba'
        ? 'Only historical box scores are ingested. There is no live odds feed, model or tracker for the NBA, so no picks or +EV alerts exist. This page will light up when that changes.'
        : 'Market adapters exist but no sweep is running and nothing is tracked. This page will light up when that changes.'}
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

function NflToday({ feed }: { feed: EvFeed | null }) {
  const nfl = feed?.nfl
  const rows: EvNflRow[] = nfl?.rows ?? []
  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <div role="note" style={{ border: '1px solid rgba(245,158,11,0.35)', background: 'rgba(245,158,11,0.07)', borderRadius: '10px', padding: '12px 14px', fontSize: '12.5px', color: 'var(--silver)', lineHeight: 1.55 }}>
        <strong style={{ color: '#f59e0b' }}>SHADOW DATA.</strong> Kalshi asks vs a sportsbook consensus, net of fee. Nothing here is validated and the sweep places no
        orders. {nfl?.status === 'shadow_live' ? 'Sweep is running.' : nfl?.age_minutes != null ? `Sweep idle; last run ${(nfl.age_minutes / 60).toFixed(1)}h ago.` : 'No sweep output available.'}
      </div>
      {rows.length === 0 ? (
        <Empty title="No NFL rows" body="The sweep runs around game windows. Rows appear when a Kalshi ask beats the consensus before kickoff." />
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
        <SectionTitle title={`Today's slate · ${slate!.total_games} games`} sub="Probable starters and the number of tracked picks per game." />
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
  if (sport === 'nfl') {
    const byGame = new Map<string, { n: number; flagged: number; mins: number }>()
    for (const r of feed?.nfl?.rows ?? []) {
      const k = (r.game_key ?? '').split(':')[2] ?? '?'
      const cur = byGame.get(k) ?? { n: 0, flagged: 0, mins: r.mins_to_kick }
      cur.n++; if (r.flag) cur.flagged++; cur.mins = Math.min(cur.mins, r.mins_to_kick)
      byGame.set(k, cur)
    }
    if (byGame.size === 0) return <Empty title="No upcoming games in the sweep" body="Games appear here while the sweep has open Kalshi prop markets before kickoff." />
    return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '12px' }}>
        {[...byGame.entries()].sort((a, b) => a[1].mins - b[1].mins).map(([k, v]) => (
          <div key={k} style={{ ...CARD_STYLE, padding: '14px 16px' }}>
            <div className="mono" style={{ color: 'var(--chalk)', fontWeight: 700 }}>{k.replace('@', ' @ ')}</div>
            <div style={{ fontSize: '12px', color: 'var(--fog)', marginTop: '6px' }}>kick in {v.mins < 90 ? `${Math.round(v.mins)}m` : `${(v.mins / 60).toFixed(1)}h`} &middot; {v.n} comparable rows &middot; {v.flagged} flagged</div>
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
