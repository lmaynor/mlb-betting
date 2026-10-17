'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { LockedNotice } from '@/components/picks/locked-notice'
import { sportHref } from '@/lib/ia'
import { scoreTier, TIER_COLOR, TIER_LABEL } from '@/lib/beezy-score'
import { SPORT_PILLARS, SPORT_SLUGS, type SportSlug } from '@/lib/sports'
import { SYSTEM_COLOR } from '@/lib/tokens'
import type { TopPick } from '@/lib/top-picks'

/** A top pick plus the images the card shows. */
export interface CardPick extends TopPick {
  headshotUrl: string | null
  awayLogoUrl: string | null
  homeLogoUrl: string | null
}

type Mode = 'players' | 'games'
type SportFilter = 'all' | SportSlug

const DEFAULT_LIMIT = 5

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
  const sub = [isGame ? pick.game : pick.what, fmtOdds(pick.odds), pick.book].filter(Boolean).join(' / ')

  return (
    <div
      onClick={onToggle}
      style={{
        background: 'var(--graphite)', borderBottom: '1px solid var(--basalt)', borderLeft: `3px solid ${tierColor}`,
        display: 'flex', alignItems: 'stretch', minHeight: '96px', overflow: 'hidden', position: 'relative', cursor: 'pointer',
      }}
    >
      <div style={{ width: '80px', minWidth: '80px', position: 'relative', overflow: 'hidden', background: `${color}18`, borderRight: `1px solid ${color}44`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span className="mono" style={{ position: 'absolute', top: '6px', left: '7px', fontSize: '10px', color: `${color}99`, fontWeight: 800 }}>#{rank}</span>
        {pick.headshotUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={pick.headshotUrl} alt={pick.who} width={68} height={84}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            style={{ objectFit: 'contain', objectPosition: 'bottom center', width: '68px', height: '84px', position: 'absolute', bottom: 0, left: '50%', transform: 'translateX(-50%)' }}
          />
        ) : isGame && (pick.awayLogoUrl || pick.homeLogoUrl) ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px', zIndex: 1 }}>
            {pick.awayLogoUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={pick.awayLogoUrl} alt="" width={30} height={30} style={{ objectFit: 'contain' }} />
            )}
            <span className="mono" style={{ fontSize: '10px', color: `${color}66` }}>@</span>
            {pick.homeLogoUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={pick.homeLogoUrl} alt="" width={30} height={30} style={{ objectFit: 'contain' }} />
            )}
          </div>
        ) : (
          <span className="mono" style={{ fontSize: '13px', color, fontWeight: 800, zIndex: 1 }}>{pillar.shortName}</span>
        )}
      </div>

      <div style={{ flex: 1, padding: '10px 12px', display: 'flex', flexDirection: 'column', justifyContent: 'center', minWidth: 0 }}>
        <div style={{ marginBottom: '5px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', minWidth: 0 }}>
            <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', padding: '2px 6px', borderRadius: '4px', background: `${color}20`, color, border: `1px solid ${color}` }}>{pillar.shortName}</span>
            {!pick.tracked && (
              <span className="dell-heading" title="Projections are not tracked in the public results yet." style={{ fontSize: '10px', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.35)', borderRadius: '4px', padding: '1px 5px' }}>SHADOW &middot; CALIBRATING</span>
            )}
          </span>
          {edge && <span className="mono" style={{ fontSize: '10px', fontWeight: 800, color: tierColor, letterSpacing: '0.04em' }}>{edge} EDGE</span>}
        </div>
        <div style={{ fontSize: '15px', fontWeight: 800, color: 'var(--ash)', lineHeight: 1.12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginBottom: '3px' }}>{pick.who}</div>
        <div className="mono" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: expanded && bullets.length ? '6px' : 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{sub}</div>
        {expanded && bullets.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {bullets.slice(0, 3).map((b, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '5px' }}>
                <span style={{ color, fontSize: '10px', lineHeight: '14px', flexShrink: 0 }}>{'>'}</span>
                <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)', lineHeight: '14px' }}>{b}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div title={`${TIER_LABEL[scoreTier(pick.score)]}. Beezy Score is a 0-100 strength score, not a win probability.`}
        style={{ minWidth: '64px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '4px', borderLeft: `1px solid ${color}18`, padding: '8px 6px', background: `${tierColor}08` }}>
        <span className="mono" style={{ fontSize: '22px', fontWeight: 800, color: tierColor, lineHeight: 1 }}>{pick.score}</span>
        <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.08em', padding: '2px 6px', borderRadius: '999px', border: `1px solid ${tierColor}`, color: tierColor }}>{TIER_LABEL[scoreTier(pick.score)]}</span>
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

const chip = (on: boolean): React.CSSProperties => ({
  padding: '5px 10px', fontSize: '10px', cursor: 'pointer', borderRadius: '4px', letterSpacing: '0.06em', textTransform: 'uppercase',
  border: `1px solid ${on ? 'var(--steel)' : 'var(--basalt)'}`, background: on ? '#131a1e' : 'transparent', color: on ? 'var(--chalk)' : 'var(--fog)',
})

/**
 * The shareable Daily Card. It is the free Top 10 player picks (same list as /picks), with a Game picks toggle that follows the
 * game-picks access policy. Players can be narrowed to one sport; sports are never blended inside a sport filter.
 */
export function CheatSheetClient({ players, games, gamesLocked, today, yesterday }: {
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
  const [tweetCopied, setTweetCopied] = useState(false)

  const sports = useMemo(() => (SPORT_SLUGS as readonly SportSlug[]).filter(s => players.some(p => p.sport === s)), [players])
  const list = mode === 'games' ? games : players.filter(p => sport === 'all' || p.sport === sport)
  const shown = list.slice(0, DEFAULT_LIMIT)
  const allExpanded = shown.length > 0 && shown.every(p => expanded.has(p.key))
  const strong = list.filter(p => scoreTier(p.score) === 'strong').length
  const lean = list.filter(p => scoreTier(p.score) === 'lean').length
  const best = list.reduce((m, p) => Math.max(m, (p.edge ?? 0) * 100), 0)
  const ydayWins = yesterday.filter(p => p.result === 'win').length
  const ydayLosses = yesterday.filter(p => p.result === 'loss').length

  const toggle = (key: string) => setExpanded(prev => { const n = new Set(prev); if (n.has(key)) n.delete(key); else n.add(key); return n })

  // Tweet text for X: no external links (the algo deprioritizes them -- the card link lives in bio), under 280 chars.
  const tweetText = useMemo(() => {
    const ranked = players.filter(p => scoreTier(p.score) !== 'watch')
    const use = (ranked.length ? ranked : players).slice(0, 4)
    const header = `Top player picks - ${today}`
    const lines = use.map(p => {
      const e = fmtEdge(p.edge)
      return `${SPORT_PILLARS[p.sport].shortName}: ${p.who} ${p.what} ${fmtOdds(p.odds) ?? ''}${e ? ` (${e} edge)` : ''}`.replace(/\s+/g, ' ').trim()
    })
    const rec = ydayWins + ydayLosses > 0 ? `\nYesterday (MLB): ${ydayWins}-${ydayLosses}.` : ''
    const footer = `\nModel vs the market line on every play.${rec}\nFull card + record in bio.`
    let text = `${header}\n\n${lines.join('\n')}${footer}`
    while (text.length > 278 && lines.length > 2) {
      lines.pop()
      text = `${header}\n\n${lines.join('\n')}${footer}`
    }
    return text
  }, [players, today, ydayWins, ydayLosses])

  async function copyTweet() {
    try { await navigator.clipboard.writeText(tweetText); setTweetCopied(true); setTimeout(() => setTweetCopied(false), 2000) } catch { /* clipboard unavailable */ }
  }

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
                <div className="dell-display" style={{ fontSize: '22px', color: 'var(--ash)', lineHeight: 1 }}>BEEZY<span style={{ color: 'var(--signal)' }}>.FYI</span></div>
                <div style={{ fontSize: '15px', fontWeight: 800, color: 'var(--ash)', lineHeight: 1.15, marginTop: '8px' }}>{mode === 'players' ? 'Top player picks' : 'Game picks'}</div>
                <div className="mono" style={{ fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.1em', marginTop: '5px' }}>{today}</div>
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
              <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.08em' }}>{list.length} PICKS</span>
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
                    NOTHING SCORED YET &mdash; MLB CARD PUBLISHES ~11:00 AM CT
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
            <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.08em' }}>BEEZY.FYI</span>
          </div>
        </div>

        {/* Desktop sidebar */}
        <aside className="cheat-sheet-sidebar">
          <div style={{ border: '1px solid var(--basalt)', background: 'var(--graphite)', padding: '16px', marginBottom: '16px', borderRadius: 'var(--radius)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
              <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'var(--fog)' }}>POST TO X</span>
              <span className="mono" style={{ fontSize: '10px', color: tweetText.length > 280 ? 'var(--loss)' : 'var(--steel)' }}>{tweetText.length}/280</span>
              <div style={{ flex: 1 }} />
              <button onClick={copyTweet} className="mono" style={{ fontSize: '10px', letterSpacing: '0.08em', padding: '5px 12px', background: tweetCopied ? 'var(--signal)' : 'transparent', color: tweetCopied ? '#000' : 'var(--signal)', border: '1px solid var(--signal-led)', borderRadius: 'var(--radius)', cursor: 'pointer' }}>
                {tweetCopied ? 'COPIED' : 'COPY'}
              </button>
            </div>
            <textarea readOnly value={tweetText} rows={Math.min(12, tweetText.split('\n').length + 1)} onFocus={e => e.currentTarget.select()} aria-label="Tweet text" className="mono"
              style={{ width: '100%', background: 'var(--carbon)', color: 'var(--silver)', border: '1px solid var(--basalt)', borderRadius: 'var(--radius-sm)', fontSize: '11px', lineHeight: 1.5, padding: '10px 12px', resize: 'vertical' }} />
            <p style={{ fontSize: '11px', color: 'var(--fog)', margin: '8px 0 0' }}>No links in the text (X deprioritizes them); the card link stays in your bio. Attach the card screenshot for reach.</p>
          </div>

          <div style={{ border: '1px solid var(--basalt)', background: 'var(--graphite)', padding: '16px', marginBottom: '16px', borderRadius: 'var(--radius)' }}>
            <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'var(--fog)', marginBottom: '10px' }}>WHAT IS THIS</div>
            <p style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.6, margin: '0 0 10px' }}>
              The share-ready version of today&apos;s Top 10 player picks across MLB, NFL, NHL and NBA, ranked by Beezy Score. MLB picks are tracked and settled in public; NFL, NHL and NBA rows are projections and are marked UNVALIDATED. Game picks are a separate, paid set.
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
