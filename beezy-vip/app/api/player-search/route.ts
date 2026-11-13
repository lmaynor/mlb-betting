import { NextRequest, NextResponse } from 'next/server'
import { limitCockpit } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { foldPlayer } from '@/lib/cockpit-players'
import { loadCockpit } from '@/lib/cockpit-loader'
import { propStatLabel } from '@/lib/sports'
import type { CockpitRow } from '@/lib/types'

/**
 * Player-entity search (T-4): a name resolves to every prop that player has on today's Prop Research
 * universe (the sweep feed merged with the projection boards, exactly the rows /edge shows), each prop
 * carrying its source and its flagged price. The result links land in the cockpit inspector for that row
 * (/edge?sport=&prop=), never on a game page. The viewer's cockpit cut is applied first, so a locked
 * league's rows are not searchable either.
 */
export interface PlayerSearchProp {
  id: string
  stat: string
  label: string
  side: string
  line: number | null
  odds: number | null
  book: string | null
  source: 'sweep' | 'model'
}

export interface PlayerSearchPlayer {
  player: string
  sport: string
  team: string | null
  matchup: string | null
  props: PlayerSearchProp[]
}

const MAX_PLAYERS = 8
const MAX_PROPS = 24

function toProp(r: CockpitRow): PlayerSearchProp {
  const side = (r.side ?? '').toUpperCase() === 'UNDER' ? 'Under' : 'Over'
  return {
    id: r.id,
    stat: r.stat ?? '',
    label: `${side} ${r.line ?? ''} ${r.stat ? propStatLabel(r.stat) : ''}`.replace(/\s+/g, ' ').trim(),
    side,
    line: r.line,
    odds: r.american,
    book: r.book,
    source: r.source === 'model' ? 'model' : 'sweep',
  }
}

export async function GET(req: NextRequest) {
  const q = foldPlayer(req.nextUrl.searchParams.get('q') ?? '')
  if (q.length < 2) return NextResponse.json({ players: [] })
  const feed = await loadCockpit({ limit: 200 }).catch(() => null)
  if (!feed) return NextResponse.json({ players: [] }, { status: 502 })
  const cut = limitCockpit(feed.rows, await getViewer())

  const byPlayer = new Map<string, PlayerSearchPlayer & { bestEv: number }>()
  for (const r of cut.rows) {
    if (!r.player) continue
    if (!foldPlayer(r.player).includes(q)) continue
    const key = `${r.sport}|${foldPlayer(r.player)}`
    let entry = byPlayer.get(key)
    if (!entry) {
      entry = { player: r.player, sport: r.sport, team: r.team, matchup: r.matchup, props: [], bestEv: -Infinity }
      byPlayer.set(key, entry)
    }
    entry.props.push(toProp(r))
    entry.bestEv = Math.max(entry.bestEv, r.ev ?? -Infinity)
  }

  const players = [...byPlayer.values()]
    .sort((a, b) => b.bestEv - a.bestEv)
    .slice(0, MAX_PLAYERS)
    .map(p => ({ player: p.player, sport: p.sport, team: p.team, matchup: p.matchup, props: p.props.slice(0, MAX_PROPS) }))
  return NextResponse.json({ players })
}
