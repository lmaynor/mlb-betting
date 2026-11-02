'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { BrandWordmark } from '@/components/ui/brand-mark'
import { LockedNotice } from '@/components/picks/locked-notice'
import { LINK_REPLY, POST_LIMIT, postLength, SHEET_SIZE, type Sheet } from '@/lib/cheat-sheet-post'
import { sportHref } from '@/lib/ia'
import { scoreTier, TIER_COLOR, TIER_LABEL } from '@/lib/beezy-score'
import { readableNotes } from '@/lib/bet-notes'
import { SPORT_PILLARS, SPORT_SLUGS, type SportSlug } from '@/lib/sports'
import { SYSTEM_COLOR, bookText } from '@/lib/tokens'
import { isOfficialCardPlay, isEvOutlier, type TopPick } from '@/lib/top-picks'

/** A top pick plus the images the card shows. */
export interface CardPick extends TopPick {
  headshotUrl: string | null
  awayLogoUrl: string | null
  homeLogoUrl: string | null
}

type Mode = 'players' | 'games'
type SportFilter = 'all' | SportSlug

// the card shows this many picks and each POST TO X sheet is built from the first SHEET_SIZE of the same list: keep them equal
const DEFAULT_LIMIT = SHEET_SIZE
const DOT = ' \u00b7 '

const fmtOdds = (o: number | null) => (o == null ? null : o > 0 ? `+${o}` : String(o))
const fmtEdge = (e: number | null) => (e == null || e <= 0 ? null : `+${(e * 100).toFixed(1)}%`)

function splitNotes(notes: string | null | undefined) {
  if (!notes) return []
  return notes
    .replaceAll(` ${String.fromCharCode(194, 183)} `, ' / ')
    .replaceAll(String.fromCharCode(183), ' / ')
    .split(' / ')
    .map(s => s.trim())
    .filter(Boolean)
}

function PickCard({ pick, rank, expanded, onToggle }: { pick: CardPick; rank: number; expanded: boolean; onToggle: () => void }) {
  const pillar = SPORT_PILLARS[pick.sport]
  const color = pick.bet?.system ? (SYSTEM_COLOR[pick.bet.system] ?? pillar.accent) : pillar.accent
  const tierColor = TIER_COLOR[scoreTier(pick.score)]
  const edge = fmtEdge(pick.edge)
  const bullets = splitNotes(readableNotes(pick.bet?.notes))
  const isGame = !pick.what // game picks carry the matchup, not a player
  const market = isGame ? pick.game : pick.what
  const price = [fmtOdds(pick.odds), bookText(pick.book)].filter(Boolean).join(DOT)
  const meta = [price, isGame ? '' : pick.game].filter(Boolean).join(DOT)
  // Projection rows carry no analyst notes, so the expanded view falls back to the matchup.
  const details = bullets.length ? bullets.slice(0, 3) : [pick.game].filter(Boolean)

  return (
    <div className="bg-background-primary-default flex items-stretch min-h-[108px] overflow-hidden relative cursor-pointer"
      onClick={onToggle}
      style={{
        borderBottom: '1px solid var(--color-separator-border)',
        borderLeft: `3px solid ${tierColor}`,
      }}
    >
      <div className="w-[96px] min-w-[96px] relative overflow-hidden flex items-center justify-center" style={{ background: `${color}18`, borderRight: `1px solid ${color}44` }}>
        {pick.headshotUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={pick.headshotUrl} alt={pick.who}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center top' }}
          />
        ) : isGame && (pick.awayLogoUrl || pick.homeLogoUrl) ? (
          <div className="flex flex-col items-center gap-1 z-[1]">
            {pick.awayLogoUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={pick.awayLogoUrl} alt="" width={34} height={34} style={{ objectFit: 'contain' }} />
            )}
            <span className="mono text-micro" style={{ color: `${color}99` }}>@</span>
            {pick.homeLogoUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={pick.homeLogoUrl} alt="" width={34} height={34} style={{ objectFit: 'contain' }} />
            )}
          </div>
        ) : (
          <span className="mono" style={{ fontSize: '13px', color, fontWeight: 800, zIndex: 1 }}>{pillar.shortName}</span>
        )}
        <span className="mono absolute top-0 left-0 z-[2] text-micro font-extrabold text-text-primary bg-background-full py-[2px] px-[6px]" style={{ borderBottomRightRadius: '4px' }}>#{rank}</span>
      </div>

      <div className="flex-1 py-2.5 px-3 flex flex-col justify-center min-w-0">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <span className="inline-flex items-center gap-1.5 min-w-0">
            <span className="ui-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', padding: '2px 6px', borderRadius: '4px', background: `${color}20`, color, border: `1px solid ${color}` }}>{pillar.shortName}</span>
          </span>
          {edge && <span className="mono text-mini font-extrabold whitespace-nowrap" style={{ color: tierColor }}>{edge}</span>}
        </div>
        <div className="text-[16px] font-extrabold text-text-primary leading-[1.15] overflow-hidden text-ellipsis whitespace-nowrap">{pick.who}</div>
        {market && <div className="text-cozy font-semibold text-neutral-300 leading-[1.25] mt-0.5">{market}</div>}
        {meta && <div className="mono text-mini text-text-secondary mt-[3px] overflow-hidden text-ellipsis whitespace-nowrap">{meta}</div>}
        {expanded && (
          <div className="flex flex-col gap-[3px] mt-2">
            {details.map((b, i) => (
              <div key={i} className="flex items-start gap-1.5">
                <span style={{ color, fontSize: '11px', lineHeight: '15px', flexShrink: 0 }}>&bull;</span>
                <span className="text-compact text-text-secondary" style={{ lineHeight: '15px' }}>{b}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="min-w-16 flex flex-col items-center justify-center gap-1 py-2 px-[6px]" title={`Beezy Score ${pick.score}. Not a win probability.`}
        style={{ borderLeft: `1px solid ${color}18`, background: `${tierColor}08` }}>
        <span className="mono text-[22px] font-extrabold leading-[1]" style={{ color: tierColor }}>{pick.score}</span>
        {isOfficialCardPlay(pick) ? (
          <span className="ui-heading text-micro tracking-fine py-0.5 px-[5px] rounded-chip border border-[color:#57F287] bg-[rgba(87,242,135,0.12)] text-signal whitespace-nowrap">OFFICIAL PLAY</span>
        ) : isEvOutlier(pick) ? (
          <span className="ui-heading text-micro tracking-fine py-0.5 px-[5px] rounded-chip border border-[color:#38bdf8] bg-[rgba(56,189,248,0.12)] text-link whitespace-nowrap">+EV OUTLIER</span>
        ) : null}
      </div>
    </div>
  )
}

function YesterdayRow({ pick }: { pick: CardPick }) {
  const win = pick.result === 'win'
  return (
    <div className="flex items-center gap-2 py-1.5 px-0 border-b border-b-[color:var(--color-separator-border)]">
      <span className="ui-heading text-micro tracking-head py-[2px] px-[5px] shrink-0" style={{ borderRadius: '4px', color: win ? 'var(--signal)' : 'var(--loss)', border: `1px solid ${win ? 'var(--signal)' : 'var(--loss)'}` }}>
        {win ? 'W' : pick.result === 'loss' ? 'L' : '-'}
      </span>
      <span className="mono text-micro text-text-secondary overflow-hidden text-ellipsis whitespace-nowrap">
        {pick.who}{pick.what ? ` / ${pick.what}` : ''}
      </span>
    </div>
  )
}

const copyBtn = (done: boolean): React.CSSProperties => ({
  fontSize: '11px', letterSpacing: '0.08em', padding: '7px 16px', cursor: 'pointer', borderRadius: 'var(--radius-sm)',
  background: done ? 'var(--signal)' : 'transparent', color: done ? '#000' : 'var(--color-neutral-300)', border: '1px solid var(--color-separator-border)',
})

/**
 * POST TO X. One box per sheet (overall first, then each sport), each its own post with its own COPY, then the one link reply.
 * Read-only on purpose: a focused editable field makes iOS zoom the page; edit in X after pasting.
 */
function PostToX({ sheets }: { sheets: Sheet[] }) {
  const [copied, setCopied] = useState<string | null>(null)
  if (sheets.length === 0) return null

  async function copy(id: string, text: string) {
    try { await navigator.clipboard.writeText(text); setCopied(id); setTimeout(() => setCopied(c => (c === id ? null : c)), 2000) } catch { /* clipboard unavailable */ }
  }
  const field: React.CSSProperties = {
    width: '100%', background: 'var(--color-background-full)', color: 'var(--color-neutral-300)', border: '1px solid var(--color-separator-border)', borderRadius: 'var(--radius-sm)',
    fontSize: '12px', lineHeight: 1.5, padding: '10px 12px', resize: 'none', boxSizing: 'border-box',
  }

  return (
    <div className="border border-separator-border bg-background-primary-default p-4 mb-4 rounded-[var(--radius)] w-full box-border">
      <div className="ui-heading text-micro tracking-[0.12em] text-text-secondary mb-3">POST TO X</div>
      {sheets.map(sh => {
        const n = postLength(sh.text)
        return (
          <div key={sh.id} className="mb-3.5">
            <div className="flex items-center gap-2.5 mb-1.5">
              <span className="ui-heading text-mini tracking-caps text-text-primary">{sh.label}</span>
              <span className="mono text-micro text-border-button-active">{n}/{POST_LIMIT}</span>
              <div  className="flex-1"/>
              <button onClick={() => copy(sh.id, sh.text)} className="mono" style={copyBtn(copied === sh.id)}>{copied === sh.id ? 'COPIED' : 'COPY'}</button>
            </div>
            <textarea readOnly value={sh.text} rows={sh.lines + 2} onFocus={e => e.currentTarget.select()} aria-label={`${sh.label} post`} className="mono" style={field} />
            {sh.lines < sh.offered && <p className="text-mini text-text-secondary" style={{ margin: '4px 0 0' }}>Showing {sh.lines} of {sh.offered}: the lowest-ranked {sh.offered - sh.lines === 1 ? 'pick was' : 'picks were'} cut so the post fits {POST_LIMIT}.</p>}
          </div>
        )
      })}
      <div className="border-t border-t-[color:var(--color-separator-border)] pt-3">
        <div className="flex items-center gap-2.5 mb-1.5">
          <span className="ui-heading text-mini tracking-caps text-text-primary">REPLY</span>
          <div  className="flex-1"/>
          <button onClick={() => copy('reply', LINK_REPLY)} className="mono" style={copyBtn(copied === 'reply')}>{copied === 'reply' ? 'COPIED' : 'COPY'}</button>
        </div>
        <textarea readOnly value={LINK_REPLY} rows={3} onFocus={e => e.currentTarget.select()} aria-label="Link reply" className="mono" style={field} />
      </div>
      <p className="text-mini text-text-secondary" style={{ margin: '10px 0 0' }}>
        Each box lists the same picks, in the same order, as the card above for that sport. No links in the posts (X deprioritizes them): post a sheet,
        then paste the REPLY under it. A line says EV only when it is a real +EV price; otherwise it says edge (model vs market). NFL, NHL and NBA rows are
        unvalidated projection signals.
      </p>
    </div>
  )
}

const chip = (on: boolean): React.CSSProperties => ({
  padding: '5px 10px', fontSize: '10px', cursor: 'pointer', borderRadius: '4px', letterSpacing: '0.06em', textTransform: 'uppercase',
  border: `1px solid ${on ? 'var(--color-border-button-active)' : 'var(--color-separator-border)'}`, background: on ? '#131a1e' : 'transparent', color: on ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
})

/**
 * The shareable Daily Card. It is the free Top 10 player picks (same list as /picks), with a Game picks toggle that follows the
 * game-picks access policy. Players can be narrowed to one sport; sports are never blended inside a sport filter.
 */
export function CheatSheetClient({ sheets, canPost = false, sportPicks, players, games, gamesLocked, today, yesterday }: {
  sheets: Sheet[]
  canPost?: boolean
  /** the best picks per sport (the overall Top 10 can be all one sport); the sport chips and the POST TO X boxes read these */
  sportPicks: Partial<Record<SportSlug, CardPick[]>>
  players: CardPick[]
  games: CardPick[]
  gamesLocked: number
  today: string
  yesterday: CardPick[]
}) {
  const [mode, setMode] = useState<Mode>('players')
  const [sport, setSport] = useState<SportFilter>('all')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [copied, setCopied] = useState(false)

  const sports = useMemo(() => (SPORT_SLUGS as readonly SportSlug[]).filter(s => (sportPicks[s]?.length ?? 0) > 0), [sportPicks])
  const list = mode === 'games' ? games : sport === 'all' ? players : (sportPicks[sport] ?? [])
  const shown = list.slice(0, DEFAULT_LIMIT)
  const allExpanded = shown.length > 0 && shown.every(p => expanded.has(p.key))
  const strong = list.filter(p => scoreTier(p.score) === 'strong').length
  const lean = list.filter(p => scoreTier(p.score) === 'lean').length
  const best = list.reduce((m, p) => Math.max(m, (p.edge ?? 0) * 100), 0)
  const ydayWins = yesterday.filter(p => p.result === 'win').length
  const ydayLosses = yesterday.filter(p => p.result === 'loss').length

  const toggle = (key: string) => setExpanded(prev => { const n = new Set(prev); if (n.has(key)) n.delete(key); else n.add(key); return n })

  async function handleShare() {
    const url = 'https://beezy.fyi/cheat-sheet'
    if (typeof navigator !== 'undefined' && navigator.share) {
      try { await navigator.share({ title: 'Beezy.FYI Daily Card', url }) } catch { /* cancelled */ }
    } else {
      try { await navigator.clipboard.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 2000) } catch { /* clipboard not available */ }
    }
  }

  return (
    <div className="bg-background-full flex flex-col items-center pt-4.5 px-3 pb-10">
      <div className="cheat-sheet-layout">
        <div className="w-full max-w-[390px] border border-separator-border bg-background-full rounded-[var(--radius)] overflow-hidden shrink-0">
          {/* Header */}
          <div className="bg-background-primary-default pt-4 px-4 pb-3 border-b border-b-[color:var(--color-separator-border)]">
            <div className="flex items-start justify-between mb-2.5 gap-3">
              <div>
                <BrandWordmark size="lg" />
                <div className="text-[15px] font-extrabold text-text-primary leading-[1.15] mt-2">{mode === 'players' ? 'Top player picks' : 'Game picks'}</div>
                <div className="mono text-micro text-text-secondary tracking-label mt-[5px]">{today}</div>
                <div className="text-[11.5px] text-neutral-300 leading-[1.45] mt-2">
                  Strict dual-engine card (1-5 plays/day max). Requires 4+ book consensus, 3-10% sweet-spot EV, and Model + Market confluence.
                </div>
                <div className="text-mini text-signal bg-[rgba(87,242,135,0.08)] border border-[color:rgba(87,242,135,0.22)] rounded-chip py-1.5 px-2.5 mt-2 leading-[1.4]">
                  <span className="mono font-bold">{shown.length}</span> Official Play{shown.length !== 1 ? 's' : ''} locked for today&apos;s slate (no filler plays added).
                </div>
              </div>
              <button onClick={() => setExpanded(allExpanded ? new Set() : new Set(shown.map(p => p.key)))} className="mono text-micro tracking-caps bg-transparent py-1 px-[7px] cursor-pointer whitespace-nowrap"
                style={{ color: allExpanded ? 'var(--color-text-primary)' : 'var(--color-text-secondary)', border: `1px solid ${allExpanded ? 'var(--color-border-button-active)' : 'var(--color-separator-border)'}` }}>
                {allExpanded ? 'COLLAPSE' : 'EXPAND'}
              </button>
            </div>

            <div role="group" aria-label="Pick type" className="flex gap-1.5 mb-2">
              <button className="mono" aria-pressed={mode === 'players'} onClick={() => { setMode('players'); setExpanded(new Set()) }} style={chip(mode === 'players')}>Player picks</button>
              <button className="mono" aria-pressed={mode === 'games'} onClick={() => { setMode('games'); setExpanded(new Set()) }} style={chip(mode === 'games')}>Game picks</button>
            </div>
            {mode === 'players' && sports.length > 1 && (
              <div role="group" aria-label="Sport" className="flex flex-wrap gap-1.5">
                {(['all', ...sports] as SportFilter[]).map(s => (
                  <button key={s} className="mono" aria-pressed={sport === s} onClick={() => { setSport(s); setExpanded(new Set()) }} style={chip(sport === s)}>
                    {s === 'all' ? 'All' : SPORT_PILLARS[s].shortName}
                  </button>
                ))}
              </div>
            )}
          </div>

          {list.length > 0 && (
            <div className="py-[7px] px-4 border-b border-b-[color:var(--color-separator-border)] flex flex-wrap gap-2.5 items-center">
              <span className="mono text-micro text-text-secondary tracking-caps">{list.length > DEFAULT_LIMIT ? `TOP ${DEFAULT_LIMIT} OF ${list.length}` : `${list.length} ${list.length === 1 ? 'PICK' : 'PICKS'}`}</span>
              {strong > 0 && <span className="ui-heading text-micro text-signal tracking-head">{strong} STRONG</span>}
              {lean > 0 && <span className="ui-heading text-micro text-signal tracking-head">{lean} LEAN</span>}
              {best > 0 && <span className="ui-heading text-micro text-signal tracking-head">BEST EDGE +{best.toFixed(1)}%</span>}
            </div>
          )}

          {list.length === 0 ? (
            <div className="py-5 px-4">
              {mode === 'games' && gamesLocked > 0 ? (
                <LockedNotice locked={gamesLocked} what="MLB game picks" plan="Game picks are on the MLB Pass or All-Access Pro." />
              ) : (
                <>
                  <div className="mono text-micro text-text-secondary tracking-caps mb-3.5 text-center">
                    NO PICKS ON THE CARD YET. THEY POST WHEN TODAY&apos;S LINES ARE IN.
                  </div>
                  {yesterday.length > 0 && (
                    <>
                      <div className="mono text-micro text-text-secondary tracking-label mb-1.5 text-center">YESTERDAY&apos;S RESULTS</div>
                      {yesterday.map(p => <YesterdayRow key={p.key} pick={p} />)}
                      {ydayWins + ydayLosses > 0 && (
                        <div className="mono text-micro text-text-secondary text-center mt-2.5">{ydayWins}-{ydayLosses} yesterday</div>
                      )}
                    </>
                  )}
                </>
              )}
            </div>
          ) : (
            <>
              {shown.map((p, i) => <PickCard key={p.key} pick={p} rank={i + 1} expanded={expanded.has(p.key)} onToggle={() => toggle(p.key)} />)}
              {list.length > DEFAULT_LIMIT && (
                <div className="mono py-2 px-4 text-micro tracking-caps text-text-secondary border-t border-t-[color:var(--color-separator-border)] text-center">
                  +{list.length - DEFAULT_LIMIT} MORE ON <Link className="text-link" href={mode === 'games' ? '/picks/games' : '/picks'}>THE PICKS PAGE</Link>
                </div>
              )}
              {mode === 'games' && <div className="pt-0 px-3 pb-3"><LockedNotice locked={gamesLocked} shown={games.length} what="MLB game picks" plan="The full set is on the MLB Pass or All-Access Pro." /></div>}
            </>
          )}

          <div className="border-t border-t-[color:var(--color-separator-border)] py-[11px] px-4 flex items-center gap-2.5 bg-background-full">
            <button onClick={handleShare} className="ui-heading text-micro tracking-caps py-[6px] px-3 text-black border border-separator-border rounded-[var(--radius)] cursor-pointer whitespace-nowrap"
              style={{ background: 'var(--signal)' }}>
              {copied ? 'COPIED' : 'SHARE CARD'}
            </button>
            <div  className="flex-1"/>
            <BrandWordmark size="sm" />
          </div>
        </div>

        {/* Mobile: the sidebar is hidden on phones, so the POST TO X sheets sit under the card */}
        {canPost && <div className="cheat-sheet-mobile-post"><PostToX sheets={sheets} /></div>}

        {/* Desktop sidebar */}
        <aside className="cheat-sheet-sidebar">
          {canPost && <PostToX sheets={sheets} />}

          <div className="border border-separator-border bg-background-primary-default p-4 mb-4 rounded-[var(--radius)]">
            <div className="ui-heading text-micro tracking-[0.12em] text-text-secondary mb-2.5">WHAT IS THIS</div>
            <p className="text-cozy text-neutral-300 leading-[1.6]" style={{ margin: '0 0 10px' }}>
              The share-ready version of today&apos;s Top 10 player picks across MLB, NFL, NHL and NBA, ranked by Beezy Score. MLB picks are tracked and settled in the public record; NFL, NHL and NBA picks come from the projection engine and are not tracked yet. Game picks are a separate, paid set.
            </p>
            <p className="text-cozy text-neutral-300 leading-[1.6] m-0">
              Tap a pick to see why it made the card. Each sport&apos;s systems and record are on its Systems tab:
            </p>
            <div className="flex flex-wrap gap-y-1.5 gap-x-3.5 mt-2 text-[12.5px]">
              {SPORT_SLUGS.map(s => <Link className="text-link" key={s} href={`${sportHref(s)}/systems`}>{SPORT_PILLARS[s].shortName}</Link>)}
            </div>
          </div>

          <div className="border border-separator-border bg-background-primary-default p-4 rounded-[var(--radius)]">
            <div className="ui-heading text-micro tracking-[0.12em] text-text-secondary mb-2.5">SCORE LEGEND</div>
            {[
              { tier: 'strong', desc: 'Score 70+: large edge, many books agree' },
              { tier: 'lean', desc: 'Score 45-69: positive edge, moderate confidence' },
              { tier: 'watch', desc: 'Score under 45: small edge, listed for reference' },
            ].map(row => {
              const t = row.tier as 'strong' | 'lean' | 'watch'
              return (
                <div key={t} className="flex items-center gap-2.5 mb-2">
                  <span className="ui-heading text-micro tracking-caps py-[2px] px-[6px] shrink-0" style={{ borderRadius: '4px', color: TIER_COLOR[t], border: `1px solid ${TIER_COLOR[t]}` }}>{TIER_LABEL[t]}</span>
                  <span className="mono text-micro text-text-secondary">{row.desc}</span>
                </div>
              )
            })}
          </div>
        </aside>
      </div>

      <Link href="/picks" className="mono cheat-sheet-picks-link mt-4 text-micro text-text-secondary no-underline tracking-head">ALL PICKS &rarr;</Link>
    </div>
  )
}
