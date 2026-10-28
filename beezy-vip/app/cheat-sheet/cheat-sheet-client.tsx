'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { BrandWordmark } from '@/components/ui/brand-mark'
import { LockedNotice } from '@/components/picks/locked-notice'
import { LINK_REPLY, POST_LIMIT, postLength, SHEET_SIZE, type Sheet } from '@/lib/cheat-sheet-post'
import { sportHref } from '@/lib/ia'
import { scoreTier, TIER_COLOR, TIER_LABEL } from '@/lib/beezy-score'
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
  const bullets = splitNotes(pick.bet?.notes)
  const isGame = !pick.what // game picks carry the matchup, not a player
  const market = isGame ? pick.game : pick.what
  const price = [fmtOdds(pick.odds), bookText(pick.book)].filter(Boolean).join(DOT)
  const meta = [price, isGame ? '' : pick.game].filter(Boolean).join(DOT)
  // Projection rows carry no analyst notes, so the expanded view falls back to the matchup.
  const details = bullets.length ? bullets.slice(0, 3) : [pick.game].filter(Boolean)

  return (
    <div
      onClick={onToggle}
      style={{
        background: 'var(--graphite)', borderBottom: '1px solid var(--basalt)', borderLeft: `3px solid ${tierColor}`,
        display: 'flex', alignItems: 'stretch', minHeight: '108px', overflow: 'hidden', position: 'relative', cursor: 'pointer',
      }}
    >
      <div style={{ width: '96px', minWidth: '96px', position: 'relative', overflow: 'hidden', background: `${color}18`, borderRight: `1px solid ${color}44`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {pick.headshotUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={pick.headshotUrl} alt={pick.who}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center top' }}
          />
        ) : isGame && (pick.awayLogoUrl || pick.homeLogoUrl) ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px', zIndex: 1 }}>
            {pick.awayLogoUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={pick.awayLogoUrl} alt="" width={34} height={34} style={{ objectFit: 'contain' }} />
            )}
            <span className="mono" style={{ fontSize: '10px', color: `${color}99` }}>@</span>
            {pick.homeLogoUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={pick.homeLogoUrl} alt="" width={34} height={34} style={{ objectFit: 'contain' }} />
            )}
          </div>
        ) : (
          <span className="mono" style={{ fontSize: '13px', color, fontWeight: 800, zIndex: 1 }}>{pillar.shortName}</span>
        )}
        <span className="mono" style={{ position: 'absolute', top: 0, left: 0, zIndex: 2, fontSize: '10px', fontWeight: 800, color: 'var(--ash)', background: 'var(--carbon)', padding: '2px 6px', borderBottomRightRadius: '4px' }}>#{rank}</span>
      </div>

      <div style={{ flex: 1, padding: '10px 12px', display: 'flex', flexDirection: 'column', justifyContent: 'center', minWidth: 0 }}>
        <div style={{ marginBottom: '6px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', minWidth: 0 }}>
            <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', padding: '2px 6px', borderRadius: '4px', background: `${color}20`, color, border: `1px solid ${color}` }}>{pillar.shortName}</span>
          </span>
          {edge && <span className="mono" style={{ fontSize: '11px', fontWeight: 800, color: tierColor, whiteSpace: 'nowrap' }}>{edge}</span>}
        </div>
        <div style={{ fontSize: '16px', fontWeight: 800, color: 'var(--ash)', lineHeight: 1.15, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pick.who}</div>
        {market && <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--silver)', lineHeight: 1.25, marginTop: '2px' }}>{market}</div>}
        {meta && <div className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '3px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{meta}</div>}
        {expanded && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', marginTop: '8px' }}>
            {details.map((b, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '6px' }}>
                <span style={{ color, fontSize: '11px', lineHeight: '15px', flexShrink: 0 }}>&bull;</span>
                <span style={{ fontSize: '12px', color: 'var(--fog)', lineHeight: '15px' }}>{b}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div title={`Beezy Score ${pick.score}. Not a win probability.`}
        style={{ minWidth: '64px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '4px', borderLeft: `1px solid ${color}18`, padding: '8px 6px', background: `${tierColor}08` }}>
        <span className="mono" style={{ fontSize: '22px', fontWeight: 800, color: tierColor, lineHeight: 1 }}>{pick.score}</span>
        {isOfficialCardPlay(pick) ? (
          <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.04em', padding: '2px 5px', borderRadius: '4px', border: '1px solid #57F287', background: 'rgba(87,242,135,0.12)', color: '#57F287', whiteSpace: 'nowrap' }}>OFFICIAL PLAY</span>
        ) : isEvOutlier(pick) ? (
          <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.04em', padding: '2px 5px', borderRadius: '4px', border: '1px solid #38bdf8', background: 'rgba(56,189,248,0.12)', color: '#38bdf8', whiteSpace: 'nowrap' }}>+EV OUTLIER</span>
        ) : null}
      </div>
    </div>
  )
}

function YesterdayRow({ pick }: { pick: CardPick }) {
  const win = pick.result === 'win'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 0', borderBottom: '1px solid var(--basalt)' }}>
      <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.06em', padding: '2px 5px', borderRadius: '4px', color: win ? 'var(--signal)' : 'var(--loss)', border: `1px solid ${win ? 'var(--signal)' : 'var(--loss)'}`, flexShrink: 0 }}>
        {win ? 'W' : pick.result === 'loss' ? 'L' : '-'}
      </span>
      <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {pick.who}{pick.what ? ` / ${pick.what}` : ''}
      </span>
    </div>
  )
}

const copyBtn = (done: boolean): React.CSSProperties => ({
  fontSize: '11px', letterSpacing: '0.08em', padding: '7px 16px', cursor: 'pointer', borderRadius: 'var(--radius-sm)',
  background: done ? 'var(--signal)' : 'transparent', color: done ? '#000' : 'var(--silver)', border: '1px solid var(--basalt)',
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
    width: '100%', background: 'var(--carbon)', color: 'var(--silver)', border: '1px solid var(--basalt)', borderRadius: 'var(--radius-sm)',
    fontSize: '12px', lineHeight: 1.5, padding: '10px 12px', resize: 'none', boxSizing: 'border-box',
  }

  return (
    <div style={{ border: '1px solid var(--basalt)', background: 'var(--graphite)', padding: '16px', marginBottom: '16px', borderRadius: 'var(--radius)', width: '100%', boxSizing: 'border-box' }}>
      <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'var(--fog)', marginBottom: '12px' }}>POST TO X</div>
      {sheets.map(sh => {
        const n = postLength(sh.text)
        return (
          <div key={sh.id} style={{ marginBottom: '14px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
              <span className="dell-heading" style={{ fontSize: '11px', letterSpacing: '0.08em', color: 'var(--chalk)' }}>{sh.label}</span>
              <span className="mono" style={{ fontSize: '10px', color: 'var(--steel)' }}>{n}/{POST_LIMIT}</span>
              <div style={{ flex: 1 }} />
              <button onClick={() => copy(sh.id, sh.text)} className="mono" style={copyBtn(copied === sh.id)}>{copied === sh.id ? 'COPIED' : 'COPY'}</button>
            </div>
            <textarea readOnly value={sh.text} rows={sh.lines + 2} onFocus={e => e.currentTarget.select()} aria-label={`${sh.label} post`} className="mono" style={field} />
            {sh.lines < sh.offered && <p style={{ fontSize: '11px', color: 'var(--fog)', margin: '4px 0 0' }}>Showing {sh.lines} of {sh.offered}: the lowest-ranked {sh.offered - sh.lines === 1 ? 'pick was' : 'picks were'} cut so the post fits {POST_LIMIT}.</p>}
          </div>
        )
      })}
      <div style={{ borderTop: '1px solid var(--basalt)', paddingTop: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
          <span className="dell-heading" style={{ fontSize: '11px', letterSpacing: '0.08em', color: 'var(--chalk)' }}>REPLY</span>
          <div style={{ flex: 1 }} />
          <button onClick={() => copy('reply', LINK_REPLY)} className="mono" style={copyBtn(copied === 'reply')}>{copied === 'reply' ? 'COPIED' : 'COPY'}</button>
        </div>
        <textarea readOnly value={LINK_REPLY} rows={3} onFocus={e => e.currentTarget.select()} aria-label="Link reply" className="mono" style={field} />
      </div>
      <p style={{ fontSize: '11px', color: 'var(--fog)', margin: '10px 0 0' }}>
        Each box lists the same picks, in the same order, as the card above for that sport. No links in the posts (X deprioritizes them): post a sheet,
        then paste the REPLY under it. A line says EV only when it is a real +EV price; otherwise it says edge (model vs market). NFL, NHL and NBA rows are
        unvalidated projection signals.
      </p>
    </div>
  )
}

const chip = (on: boolean): React.CSSProperties => ({
  padding: '5px 10px', fontSize: '10px', cursor: 'pointer', borderRadius: '4px', letterSpacing: '0.06em', textTransform: 'uppercase',
  border: `1px solid ${on ? 'var(--steel)' : 'var(--basalt)'}`, background: on ? '#131a1e' : 'transparent', color: on ? 'var(--chalk)' : 'var(--fog)',
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
    <div style={{ background: 'var(--carbon)', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '18px 12px 40px' }}>
      <div className="cheat-sheet-layout">
        <div style={{ width: '100%', maxWidth: '390px', border: '1px solid var(--basalt)', background: 'var(--carbon)', borderRadius: 'var(--radius)', overflow: 'hidden', flexShrink: 0 }}>
          {/* Header */}
          <div style={{ background: 'var(--graphite)', padding: '16px 16px 12px', borderBottom: '1px solid var(--basalt)' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '10px', gap: '12px' }}>
              <div>
                <BrandWordmark size="lg" />
                <div style={{ fontSize: '15px', fontWeight: 800, color: 'var(--ash)', lineHeight: 1.15, marginTop: '8px' }}>{mode === 'players' ? 'Top player picks' : 'Game picks'}</div>
                <div className="mono" style={{ fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.1em', marginTop: '5px' }}>{today}</div>
                <div style={{ fontSize: '11.5px', color: 'var(--silver)', lineHeight: 1.45, marginTop: '8px' }}>
                  Strict dual-engine card (1-5 plays/day max). Requires 4+ book consensus, 3-10% sweet-spot EV, and Model + Market confluence.
                </div>
                <div style={{ fontSize: '11px', color: '#57F287', background: 'rgba(87,242,135,0.08)', border: '1px solid rgba(87,242,135,0.22)', borderRadius: '4px', padding: '6px 10px', marginTop: '8px', lineHeight: 1.4 }}>
                  <span className="mono" style={{ fontWeight: 700 }}>{shown.length}</span> Official Play{shown.length !== 1 ? 's' : ''} locked for today&apos;s slate (no filler plays added).
                </div>
              </div>
              <button onClick={() => setExpanded(allExpanded ? new Set() : new Set(shown.map(p => p.key)))} className="mono"
                style={{ fontSize: '10px', letterSpacing: '0.08em', color: allExpanded ? 'var(--chalk)' : 'var(--fog)', background: 'transparent', border: `1px solid ${allExpanded ? 'var(--steel)' : 'var(--basalt)'}`, padding: '4px 7px', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                {allExpanded ? 'COLLAPSE' : 'EXPAND'}
              </button>
            </div>

            <div role="group" aria-label="Pick type" style={{ display: 'flex', gap: '6px', marginBottom: '8px' }}>
              <button className="mono" aria-pressed={mode === 'players'} onClick={() => { setMode('players'); setExpanded(new Set()) }} style={chip(mode === 'players')}>Player picks</button>
              <button className="mono" aria-pressed={mode === 'games'} onClick={() => { setMode('games'); setExpanded(new Set()) }} style={chip(mode === 'games')}>Game picks</button>
            </div>
            {mode === 'players' && sports.length > 1 && (
              <div role="group" aria-label="Sport" style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                {(['all', ...sports] as SportFilter[]).map(s => (
                  <button key={s} className="mono" aria-pressed={sport === s} onClick={() => { setSport(s); setExpanded(new Set()) }} style={chip(sport === s)}>
                    {s === 'all' ? 'All' : SPORT_PILLARS[s].shortName}
                  </button>
                ))}
              </div>
            )}
          </div>

          {list.length > 0 && (
            <div style={{ padding: '7px 16px', borderBottom: '1px solid var(--basalt)', display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center' }}>
              <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.08em' }}>{list.length > DEFAULT_LIMIT ? `TOP ${DEFAULT_LIMIT} OF ${list.length}` : `${list.length} ${list.length === 1 ? 'PICK' : 'PICKS'}`}</span>
              {strong > 0 && <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--signal)', letterSpacing: '0.06em' }}>{strong} STRONG</span>}
              {lean > 0 && <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--signal)', letterSpacing: '0.06em' }}>{lean} LEAN</span>}
              {best > 0 && <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--signal)', letterSpacing: '0.06em' }}>BEST EDGE +{best.toFixed(1)}%</span>}
            </div>
          )}

          {list.length === 0 ? (
            <div style={{ padding: '20px 16px' }}>
              {mode === 'games' && gamesLocked > 0 ? (
                <LockedNotice locked={gamesLocked} what="MLB game picks" plan="Game picks are on the MLB Pass or All-Access Pro." />
              ) : (
                <>
                  <div className="mono" style={{ fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.08em', marginBottom: '14px', textAlign: 'center' }}>
                    NO PICKS ON THE CARD YET. THEY POST WHEN TODAY&apos;S LINES ARE IN.
                  </div>
                  {yesterday.length > 0 && (
                    <>
                      <div className="mono" style={{ fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.1em', marginBottom: '6px', textAlign: 'center' }}>YESTERDAY&apos;S RESULTS</div>
                      {yesterday.map(p => <YesterdayRow key={p.key} pick={p} />)}
                      {ydayWins + ydayLosses > 0 && (
                        <div className="mono" style={{ fontSize: '10px', color: 'var(--fog)', textAlign: 'center', marginTop: '10px' }}>{ydayWins}-{ydayLosses} yesterday</div>
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
                <div className="mono" style={{ padding: '8px 16px', fontSize: '10px', letterSpacing: '0.08em', color: 'var(--fog)', borderTop: '1px solid var(--basalt)', textAlign: 'center' }}>
                  +{list.length - DEFAULT_LIMIT} MORE ON <Link href={mode === 'games' ? '/picks/games' : '/picks'} style={{ color: 'var(--link)' }}>THE PICKS PAGE</Link>
                </div>
              )}
              {mode === 'games' && <div style={{ padding: '0 12px 12px' }}><LockedNotice locked={gamesLocked} shown={games.length} what="MLB game picks" plan="The full set is on the MLB Pass or All-Access Pro." /></div>}
            </>
          )}

          <div style={{ borderTop: '1px solid var(--basalt)', padding: '11px 16px', display: 'flex', alignItems: 'center', gap: '10px', background: 'var(--carbon)' }}>
            <button onClick={handleShare} className="dell-heading"
              style={{ fontSize: '10px', letterSpacing: '0.08em', padding: '6px 12px', background: 'var(--signal)', color: '#000', border: '1px solid var(--basalt)', borderRadius: 'var(--radius)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
              {copied ? 'COPIED' : 'SHARE CARD'}
            </button>
            <div style={{ flex: 1 }} />
            <BrandWordmark size="sm" />
          </div>
        </div>

        {/* Mobile: the sidebar is hidden on phones, so the POST TO X sheets sit under the card */}
        {canPost && <div className="cheat-sheet-mobile-post"><PostToX sheets={sheets} /></div>}

        {/* Desktop sidebar */}
        <aside className="cheat-sheet-sidebar">
          {canPost && <PostToX sheets={sheets} />}

          <div style={{ border: '1px solid var(--basalt)', background: 'var(--graphite)', padding: '16px', marginBottom: '16px', borderRadius: 'var(--radius)' }}>
            <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'var(--fog)', marginBottom: '10px' }}>WHAT IS THIS</div>
            <p style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.6, margin: '0 0 10px' }}>
              The share-ready version of today&apos;s Top 10 player picks across MLB, NFL, NHL and NBA, ranked by Beezy Score. MLB picks are tracked and settled in the public record; NFL, NHL and NBA picks come from the projection engine and are not tracked yet. Game picks are a separate, paid set.
            </p>
            <p style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.6, margin: 0 }}>
              Tap a pick to see why it made the card. Each sport&apos;s systems and record are on its Systems tab:
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', marginTop: '8px', fontSize: '12.5px' }}>
              {SPORT_SLUGS.map(s => <Link key={s} href={`${sportHref(s)}/systems`} style={{ color: 'var(--link)' }}>{SPORT_PILLARS[s].shortName}</Link>)}
            </div>
          </div>

          <div style={{ border: '1px solid var(--basalt)', background: 'var(--graphite)', padding: '16px', borderRadius: 'var(--radius)' }}>
            <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'var(--fog)', marginBottom: '10px' }}>SCORE LEGEND</div>
            {[
              { tier: 'strong', desc: 'Score 70+: large edge, many books agree' },
              { tier: 'lean', desc: 'Score 45-69: positive edge, moderate confidence' },
              { tier: 'watch', desc: 'Score under 45: small edge, listed for reference' },
            ].map(row => {
              const t = row.tier as 'strong' | 'lean' | 'watch'
              return (
                <div key={t} style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
                  <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.08em', padding: '2px 6px', borderRadius: '4px', color: TIER_COLOR[t], border: `1px solid ${TIER_COLOR[t]}`, flexShrink: 0 }}>{TIER_LABEL[t]}</span>
                  <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>{row.desc}</span>
                </div>
              )
            })}
          </div>
        </aside>
      </div>

      <Link href="/picks" className="mono cheat-sheet-picks-link" style={{ marginTop: '16px', fontSize: '10px', color: 'var(--fog)', textDecoration: 'none', letterSpacing: '0.06em' }}>ALL PICKS &rarr;</Link>
    </div>
  )
}
