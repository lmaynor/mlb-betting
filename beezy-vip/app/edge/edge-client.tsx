'use client'

import { useMemo, useState } from 'react'
import type { Bet } from '@/lib/types'
import { SYSTEM_COLOR, SYSTEM_PILL } from '@/lib/tokens'

export interface EdgePick extends Bet {
  headshotUrl: string | null
  awayLogoUrl: string | null
  homeLogoUrl: string | null
  modelProbPct: number | null
  marketProbPct: number | null
  edgePctValue: number | null
}

// -- sport scaffolding (NBA slots in here once it has a model) ----------------
type SportKey = 'mlb' | 'nba'
const SPORTS: { key: SportKey; label: string; live: boolean }[] = [
  { key: 'mlb', label: 'MLB', live: true },
  { key: 'nba', label: 'NBA', live: false },
]

const PITCHER_SYSTEMS = new Set(['K', 'OUTS', 'PITCHER_ER'])
const BATTER_SYSTEMS = new Set(['HR', 'BATTER_HITS', 'BATTER_TB', 'BATTER_K'])
const GAME_SYSTEMS = new Set(['NRFI', 'F5', 'F1H', 'F3', 'F7', 'GAME'])

type Group = 'all' | 'batter' | 'pitcher' | 'game'
const GROUPS: { key: Group; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'batter', label: 'Batters' },
  { key: 'pitcher', label: 'Pitchers' },
  { key: 'game', label: 'Game' },
]

function groupOf(system: string): Group {
  if (BATTER_SYSTEMS.has(system)) return 'batter'
  if (PITCHER_SYSTEMS.has(system)) return 'pitcher'
  return 'game'
}

// -- formatting ---------------------------------------------------------------
const fmtOdds = (o: number) => (o > 0 ? `+${o}` : String(o))
const fmtPct = (p: number | null) => (p == null ? '--' : `${p.toFixed(0)}%`)
const fmtEdge = (e: number | null) => (e == null ? '--' : `${e >= 0 ? '+' : ''}${e.toFixed(1)}%`)

function americanToImplied(o: number): number {
  return o < 0 ? (-o) / (-o + 100) : 100 / (o + 100)
}

function betTypeLabel(betType: string | null | undefined, system: string): string {
  if (!betType) return system
  const prefix = system + '_'
  const cleaned = betType.toUpperCase().startsWith(prefix) ? betType.slice(prefix.length) : betType
  return cleaned.replace(/_/g, ' ')
}

function notesBullets(notes: string | null | undefined): string[] {
  if (!notes) return []
  const mid = String.fromCharCode(183) // middle dot
  return notes.split(mid).join(' / ')
    .split(' / ').map(s => s.trim()).filter(Boolean)
}

function titleOf(p: EdgePick): string {
  if (groupOf(p.system) === 'game') return `${p.away_team ?? '?'} @ ${p.home_team ?? '?'}`
  return p.player ?? betTypeLabel(p.bet_type, p.system)
}

// -- probability track: the centerpiece ---------------------------------------
function EdgeTrack({ model, market, color }: { model: number; market: number; color: string }) {
  const lo = Math.min(model, market)
  const hi = Math.max(model, market)
  return (
    <div className="edge-track" aria-hidden>
      <div className="edge-track-rail" />
      {/* the edge band between market and model */}
      <div className="edge-track-band"
        style={{ left: `${lo}%`, width: `${Math.max(hi - lo, 0.5)}%`, background: color }} />
      {/* market marker */}
      <div className="edge-track-mark edge-track-market" style={{ left: `${market}%` }} />
      {/* model marker */}
      <div className="edge-track-mark edge-track-model" style={{ left: `${model}%`, background: color, borderColor: color }} />
    </div>
  )
}

// -- detail panel -------------------------------------------------------------
function Detail({ p }: { p: EdgePick }) {
  const color = SYSTEM_COLOR[p.system] ?? '#9aa0aa'
  const pill = SYSTEM_PILL[p.system] ?? SYSTEM_PILL.ALL
  const isGame = groupOf(p.system) === 'game'
  const model = p.modelProbPct ?? 0
  const market = p.marketProbPct ?? americanToImplied(p.odds) * 100
  const fairOdds = (() => {
    if (p.modelProbPct == null) return null
    const dp = p.modelProbPct / 100
    if (dp <= 0 || dp >= 1) return null
    return dp >= 0.5 ? -Math.round((dp / (1 - dp)) * 100) : Math.round(((1 - dp) / dp) * 100)
  })()
  const bullets = notesBullets(p.notes)

  return (
    <div className="edge-detail">
      <div className="edge-detail-head">
        <div className="edge-portrait" style={{ background: `${color}14`, borderColor: `${color}40` }}>
          {!isGame && p.headshotUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={p.headshotUrl} alt={p.player ?? ''} className="edge-portrait-img"
              onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} />
          ) : (
            <div className="edge-logos">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {p.awayLogoUrl && <img src={p.awayLogoUrl} alt="" className="edge-logo" />}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {p.homeLogoUrl && <img src={p.homeLogoUrl} alt="" className="edge-logo" />}
            </div>
          )}
        </div>
        <div className="edge-detail-id">
          <span className="edge-pill" style={{ background: pill.bg, color: pill.color, border: pill.border }}>
            {p.system}
          </span>
          <h2 className="edge-detail-name">{titleOf(p)}</h2>
          <div className="edge-detail-sub">
            {isGame ? null : <span>{p.away_team} @ {p.home_team}</span>}
            <span className="edge-strong" style={{ color }}>{betTypeLabel(p.bet_type, p.system)}</span>
            <span>{fmtOdds(p.odds)}</span>
            {p.book && <span className="edge-book">{p.book}</span>}
          </div>
        </div>
      </div>

      {/* THE EDGE block */}
      <div className="edge-block">
        <div className="edge-block-top">
          <div className="edge-hero" style={{ color }}>{fmtEdge(p.edgePctValue)}</div>
          <div className="edge-hero-label">edge over the market</div>
        </div>
        <EdgeTrack model={model} market={market} color={color} />
        <div className="edge-readout">
          <div className="edge-read">
            <span className="edge-read-dot" style={{ background: color }} />
            <span className="edge-read-k">Our model</span>
            <span className="edge-read-v">{fmtPct(p.modelProbPct)}</span>
          </div>
          <div className="edge-read">
            <span className="edge-read-dot edge-read-dot-market" />
            <span className="edge-read-k">Market (de-vig)</span>
            <span className="edge-read-v">{fmtPct(market)}</span>
          </div>
        </div>
        <div className="edge-meta">
          {p.kelly_pct != null && (
            <div className="edge-meta-cell">
              <span className="edge-meta-k">Kelly stake</span>
              <span className="edge-meta-v">{(p.kelly_pct <= 1 ? p.kelly_pct * 100 : p.kelly_pct).toFixed(1)}%</span>
            </div>
          )}
          {fairOdds != null && (
            <div className="edge-meta-cell">
              <span className="edge-meta-k">Fair vs offered</span>
              <span className="edge-meta-v">{fmtOdds(fairOdds)} <span className="edge-meta-dim">/ {fmtOdds(p.odds)}</span></span>
            </div>
          )}
        </div>
      </div>

      {bullets.length > 0 && (
        <div className="edge-why">
          <div className="edge-why-h">Why we like it</div>
          <ul className="edge-why-list">
            {bullets.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}

export function EdgeClient({ picks, updated }: { picks: EdgePick[]; updated: string }) {
  const [sport, setSport] = useState<SportKey>('mlb')
  const [group, setGroup] = useState<Group>('all')
  const [selectedId, setSelectedId] = useState<number | null>(picks[0]?.id ?? null)

  const visible = useMemo(
    () => (group === 'all' ? picks : picks.filter(p => groupOf(p.system) === group)),
    [picks, group],
  )
  const selected = useMemo(
    () => visible.find(p => p.id === selectedId) ?? visible[0] ?? null,
    [visible, selectedId],
  )

  return (
    <main className="edge">
      <Styles />
      <header className="edge-header">
        <div className="edge-title-row">
          <h1 className="edge-title">THE EDGE</h1>
          <span className="edge-updated">
            <span className="edge-live-dot" /> UPDATED HOURLY <span className="edge-updated-time">{updated}</span>
          </span>
        </div>
        <p className="edge-tagline">Our model probability vs the market line on every pick. The gap is your edge.</p>
        <div className="edge-sports" role="tablist" aria-label="Sport">
          {SPORTS.map(s => (
            <button key={s.key} role="tab" aria-selected={sport === s.key}
              disabled={!s.live}
              onClick={() => s.live && setSport(s.key)}
              className={`edge-sport${sport === s.key ? ' is-active' : ''}${s.live ? '' : ' is-soon'}`}>
              {s.label}{!s.live && <span className="edge-soon">soon</span>}
            </button>
          ))}
        </div>
      </header>

      <div className="edge-filters" role="tablist" aria-label="Pick type">
        {GROUPS.map(g => {
          const n = g.key === 'all' ? picks.length : picks.filter(p => groupOf(p.system) === g.key).length
          return (
            <button key={g.key} role="tab" aria-selected={group === g.key}
              onClick={() => setGroup(g.key)}
              className={`edge-filter${group === g.key ? ' is-active' : ''}`}>
              {g.label} <span className="edge-filter-n">{n}</span>
            </button>
          )
        })}
      </div>

      {visible.length === 0 ? (
        <div className="edge-empty">
          <p className="edge-empty-h">No picks posted yet{group === 'all' ? '' : ' in this category'}.</p>
          <p className="edge-empty-s">Picks publish through the day &mdash; check back, or see <a href="/results" className="edge-link">recent results</a>.</p>
        </div>
      ) : (
        <div className="edge-grid">
          <ol className="edge-list">
            {visible.map((p, i) => {
              const color = SYSTEM_COLOR[p.system] ?? '#9aa0aa'
              const active = selected?.id === p.id
              return (
                <li key={p.id}>
                  <button className={`edge-row${active ? ' is-active' : ''}`} onClick={() => setSelectedId(p.id)}>
                    <span className="edge-row-rank">{i + 1}</span>
                    <span className="edge-row-thumb" style={{ background: `${color}14`, borderColor: `${color}38` }}>
                      {groupOf(p.system) !== 'game' && p.headshotUrl ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={p.headshotUrl} alt="" className="edge-row-img"
                          onError={e => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden' }} />
                      ) : (
                        // eslint-disable-next-line @next/next/no-img-element
                        p.homeLogoUrl ? <img src={p.homeLogoUrl} alt="" className="edge-row-logo" /> : null
                      )}
                    </span>
                    <span className="edge-row-main">
                      <span className="edge-row-name">{titleOf(p)}</span>
                      <span className="edge-row-meta">
                        <span className="edge-row-sys" style={{ color }}>{p.system}</span>
                        <span>{betTypeLabel(p.bet_type, p.system)}</span>
                        <span className="edge-row-odds">{fmtOdds(p.odds)}</span>
                      </span>
                    </span>
                    <span className="edge-row-edge" style={{ color }}>{fmtEdge(p.edgePctValue)}</span>
                  </button>
                </li>
              )
            })}
          </ol>
          <div className="edge-panel" key={selected?.id ?? 'none'}>
            {selected && <Detail p={selected} />}
          </div>
        </div>
      )}
    </main>
  )
}

// -- scoped styles (media queries + motion + hover; inline can't express these)
function Styles() {
  return (
    <style>{`
.edge { max-width: 1120px; margin: 0 auto; padding: 28px 16px 80px; color: #f5f5f7;
  font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
.edge-header { border-bottom: 1px solid #000; padding-bottom: 18px; margin-bottom: 18px; }
.edge-title-row { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.edge-title { font-size: clamp(1.9rem, 6vw, 3rem); font-weight: 860; letter-spacing: -0.04em;
  margin: 0; text-wrap: balance; }
.edge-updated { display: inline-flex; align-items: center; gap: 7px; font-size: 11px; font-weight: 700;
  letter-spacing: 0.08em; color: #b3bd95; }
.edge-updated-time { color: #8a8a93; font-weight: 600; letter-spacing: 0; }
.edge-live-dot { width: 7px; height: 7px; border-radius: 50%; background: #b3bd95;
  box-shadow: 0 0 0 0 #b3bd9577; animation: edgepulse 2.4s ease-out infinite; }
@keyframes edgepulse { 0% { box-shadow: 0 0 0 0 #b3bd9566; } 70% { box-shadow: 0 0 0 7px #b3bd9500; } 100% { box-shadow: 0 0 0 0 #b3bd9500; } }
.edge-tagline { color: #b6b6be; font-size: 14px; margin: 8px 0 0; max-width: 60ch; line-height: 1.5; }
.edge-sports { display: flex; gap: 6px; margin-top: 16px; }
.edge-sport { background: #111114; color: #9aa0aa; border: 1px solid #1f1f24; padding: 6px 14px;
  font-size: 12px; font-weight: 800; letter-spacing: 0.06em; cursor: pointer; transition: color .18s, border-color .18s, background .18s; }
.edge-sport.is-active { color: #f5f5f7; border-color: #f5f5f7; background: #17171b; }
.edge-sport.is-soon { cursor: not-allowed; opacity: 0.55; display: inline-flex; align-items: center; gap: 6px; }
.edge-soon { font-size: 8px; font-weight: 800; letter-spacing: 0.1em; color: #6f6f78; border: 1px solid #2a2a31; padding: 1px 4px; }
.edge-filters { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 18px; }
.edge-filter { background: transparent; color: #9aa0aa; border: 1px solid #1f1f24; padding: 6px 12px;
  font-size: 12px; font-weight: 700; cursor: pointer; transition: color .18s, border-color .18s; }
.edge-filter:hover { color: #d8d8de; }
.edge-filter.is-active { color: #0b0b0d; background: #f5f5f7; border-color: #f5f5f7; }
.edge-filter-n { font-variant-numeric: tabular-nums; opacity: 0.6; margin-left: 2px; }
.edge-grid { display: grid; grid-template-columns: 1fr; gap: 16px; align-items: start; }
@media (min-width: 900px) { .edge-grid { grid-template-columns: minmax(320px, 0.92fr) 1.08fr; }
  .edge-panel { position: sticky; top: 16px; } }
.edge-list { list-style: none; margin: 0; padding: 0; border: 1px solid #000; background: #0d0d10; }
.edge-row { width: 100%; display: flex; align-items: center; gap: 11px; padding: 11px 12px; text-align: left;
  background: #111114; color: #f5f5f7; border: 0; border-bottom: 1px solid #000; cursor: pointer;
  transition: background .16s ease-out, transform .16s ease-out; }
.edge-row:last-child { border-bottom: 0; }
.edge-row:hover { background: #17171b; }
.edge-row.is-active { background: #1b1b20; box-shadow: inset 3px 0 0 currentColor; }
.edge-row-rank { font-size: 10px; font-weight: 800; color: #6f6f78; min-width: 14px; font-variant-numeric: tabular-nums; }
.edge-row-thumb { width: 38px; height: 38px; min-width: 38px; border: 1px solid; display: flex;
  align-items: center; justify-content: center; overflow: hidden; }
.edge-row-img { width: 38px; height: 46px; object-fit: cover; object-position: top; }
.edge-row-logo { width: 24px; height: 24px; object-fit: contain; }
.edge-row-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.edge-row-name { font-size: 14px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.edge-row-meta { display: flex; gap: 8px; font-size: 11px; color: #9aa0aa; align-items: center; }
.edge-row-sys { font-weight: 800; letter-spacing: 0.04em; }
.edge-row-odds { font-variant-numeric: tabular-nums; color: #b6b6be; }
.edge-row-edge { font-size: 18px; font-weight: 840; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
.edge-panel { border: 1px solid #000; background: #0d0d10; min-height: 200px; animation: edgein .22s ease-out; }
@keyframes edgein { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
.edge-detail { padding: 18px; }
.edge-detail-head { display: flex; gap: 14px; align-items: flex-start; padding-bottom: 16px; border-bottom: 1px solid #1f1f24; }
.edge-portrait { width: 88px; height: 96px; min-width: 88px; border: 1px solid; display: flex;
  align-items: flex-end; justify-content: center; overflow: hidden; }
.edge-portrait-img { width: 88px; height: 100px; object-fit: cover; object-position: top; }
.edge-logos { display: flex; gap: 6px; align-items: center; height: 100%; }
.edge-logo { width: 34px; height: 34px; object-fit: contain; }
.edge-detail-id { flex: 1; min-width: 0; }
.edge-pill { display: inline-block; font-size: 10px; font-weight: 800; letter-spacing: 0.06em; padding: 2px 7px; }
.edge-detail-name { font-size: clamp(1.25rem, 3.4vw, 1.7rem); font-weight: 820; letter-spacing: -0.02em;
  margin: 8px 0 6px; line-height: 1.05; text-wrap: balance; }
.edge-detail-sub { display: flex; gap: 10px; flex-wrap: wrap; font-size: 13px; color: #9aa0aa; align-items: center; }
.edge-strong { font-weight: 800; }
.edge-block { padding: 18px 0 4px; }
.edge-block-top { display: flex; align-items: baseline; gap: 10px; }
.edge-hero { font-size: clamp(2.4rem, 8vw, 3.4rem); font-weight: 880; letter-spacing: -0.04em; line-height: 1; font-variant-numeric: tabular-nums; }
.edge-hero-label { font-size: 12px; color: #9aa0aa; font-weight: 600; }
.edge-track { position: relative; height: 30px; margin: 18px 0 10px; }
.edge-track-rail { position: absolute; top: 13px; left: 0; right: 0; height: 4px; background: #232329; }
.edge-track-band { position: absolute; top: 12px; height: 6px; opacity: 0.85; }
.edge-track-mark { position: absolute; top: 6px; width: 3px; height: 18px; transform: translateX(-50%); }
.edge-track-market { background: #d8d8de; }
.edge-track-model { width: 11px; height: 22px; top: 4px; border: 2px solid; border-radius: 1px; }
.edge-readout { display: flex; gap: 20px; margin-top: 6px; }
.edge-read { display: flex; align-items: center; gap: 7px; font-size: 12px; }
.edge-read-dot { width: 9px; height: 9px; border-radius: 2px; }
.edge-read-dot-market { background: #d8d8de; }
.edge-read-k { color: #9aa0aa; }
.edge-read-v { color: #f5f5f7; font-weight: 800; font-variant-numeric: tabular-nums; }
.edge-meta { display: flex; gap: 26px; margin-top: 18px; padding-top: 16px; border-top: 1px solid #1f1f24; }
.edge-meta-k { display: block; font-size: 11px; color: #8a8a93; letter-spacing: 0.04em; margin-bottom: 3px; }
.edge-meta-v { font-size: 16px; font-weight: 800; font-variant-numeric: tabular-nums; }
.edge-meta-dim { color: #8a8a93; font-weight: 600; }
.edge-why { margin-top: 18px; padding-top: 16px; border-top: 1px solid #1f1f24; }
.edge-why-h { font-size: 11px; font-weight: 800; letter-spacing: 0.08em; color: #8a8a93; margin-bottom: 8px; }
.edge-why-list { margin: 0; padding-left: 16px; color: #c4c4cc; font-size: 13px; line-height: 1.7; }
.edge-empty { border: 1px solid #000; background: #0d0d10; padding: 40px 24px; text-align: center; }
.edge-empty-h { font-size: 16px; font-weight: 700; margin: 0 0 6px; }
.edge-empty-s { color: #9aa0aa; font-size: 13px; margin: 0; }
.edge-link { color: #b3bd95; }
@media (prefers-reduced-motion: reduce) {
  .edge-live-dot { animation: none; }
  .edge-panel { animation: none; }
  .edge-row, .edge-sport, .edge-filter { transition: none; }
}
`}</style>
  )
}
