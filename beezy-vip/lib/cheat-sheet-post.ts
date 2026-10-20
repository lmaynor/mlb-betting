/**
 * The "POST TO X" cheat sheets on the Daily Card. Pure (no React, no Next) so the page and the tests share one formatter.
 *
 *   Tuesday +EV NBA Cheat Sheet <emoji>
 *
 *   1. Jalen Williams U 4.5 Reb -110 ESPN BET | +14.6% EV
 *
 * One OVERALL sheet (top 5 across every sport) followed by one sheet per sport that has picks (top 5 each). Only picks whose
 * percentage really is an EV are listed: a pick built from the model alone shows a model-vs-market probability gap, which can
 * sit beside a NEGATIVE price EV, and calling that "+EV" in a public post would be false. The main post carries no link
 * (X deprioritizes them); the link goes in the separate LINK_REPLY.
 */
import { SPORT_PILLARS, SPORT_SLUGS, type SportSlug } from '@/lib/sports'
import { bookDisplay } from '@/lib/tokens'
import type { TopPick } from '@/lib/top-picks'

/** The fields a sheet line needs; the page sends only these to the client. */
export type SheetPick = Pick<TopPick, 'sport' | 'who' | 'what' | 'odds' | 'book' | 'edge' | 'basis'>

export interface Sheet {
  id: 'all' | SportSlug
  /** short chip label for the box */
  label: string
  text: string
  lines: number
}

export const SHEET_SIZE = 5
/** X's standard post limit; a longer sheet needs X Premium's long post or one line trimmed. */
export const POST_LIMIT = 280

/** The second post: the only place the link lives. Typed by the owner. */
export const LINK_REPLY = "For today's full slate of +EV bets and model projections, check out https://beezy.fyi and join our Discord."

/** `edge` is a real price EV only for these bases: a tracked +EV alert, or a projection where model AND market agree. */
const EV_BASES = new Set<TopPick['basis']>(['ev', 'confluence'])

export const isEvPick = (p: SheetPick): boolean => EV_BASES.has(p.basis) && p.odds != null && p.edge != null && p.edge > 0

// Emoji built from code points (not typed) so this file stays ASCII.
const EMOJI: Record<SportSlug, string> = {
  mlb: String.fromCodePoint(0x26be),
  nfl: String.fromCodePoint(0x1f3c8),
  nhl: String.fromCodePoint(0x1f3d2),
  nba: String.fromCodePoint(0x1f3c0),
}

/** Site stat label -> the short form bettors type. Anything not listed passes through unchanged. */
const STAT_SHORT: Record<string, string> = {
  Rebounds: 'Reb', Assists: 'Ast', Points: 'Pts', '3-Pointers': '3PM', 'Pts+Reb+Ast': 'PRA', 'Pts+Reb': 'PR', 'Pts+Ast': 'PA',
  'Reb+Ast': 'RA', Steals: 'Stl', Blocks: 'Blk', 'Stl+Blk': 'Stocks', Turnovers: 'TO',
  'Shots on Goal': 'SOG', 'Blocked Shots': 'Blk',
  Receptions: 'Rec', 'Pass Attempts': 'Pass Att', Completions: 'Cmp', Carries: 'Car', Interceptions: 'INT', 'Pass TDs': 'Pass TD',
  'Total Bases': 'TB', 'Earned Runs': 'ER', 'Stolen Bases': 'SB', 'Home Runs': 'HR', Strikeouts: 'Ks', 'Pitcher Outs': 'Outs',
}

/** "Over 4.5 Rebounds" -> "O 4.5 Reb". */
export function shortMarket(what: string): string {
  const m = what.trim().match(/^(Over|Under)\s+(\S+)\s*(.*)$/i)
  if (!m) return what.trim()
  const side = m[1].toLowerCase() === 'over' ? 'O' : 'U'
  const stat = m[3].trim()
  return [side, m[2], STAT_SHORT[stat] ?? stat].filter(Boolean).join(' ')
}

const fmtOdds = (o: number) => (o > 0 ? `+${o}` : String(o))

function line(p: SheetPick, rank: number, tagSport: boolean): string {
  const book = p.book ? bookDisplay(p.book).label : ''
  const who = [tagSport ? SPORT_PILLARS[p.sport].shortName : '', p.who].filter(Boolean).join(' ')
  const head = [`${rank}.`, who, shortMarket(p.what), fmtOdds(p.odds as number), book].filter(Boolean).join(' ')
  return `${head} | +${((p.edge as number) * 100).toFixed(1)}% EV`
}

function sheetFor(id: 'all' | SportSlug, picks: SheetPick[], weekday: string): Sheet {
  const top = picks.slice(0, SHEET_SIZE)
  const name = id === 'all' ? '' : `${SPORT_PILLARS[id].shortName} `
  const emoji = id === 'all' ? '' : ` ${EMOJI[id]}`
  const header = `${weekday} +EV ${name}Cheat Sheet${emoji}`
  return {
    id,
    label: id === 'all' ? 'OVERALL' : SPORT_PILLARS[id].shortName,
    text: `${header}\n\n${top.map((p, i) => line(p, i + 1, id === 'all')).join('\n')}`,
    lines: top.length,
  }
}

/**
 * `picks` must already be in rank order (best first). Returns the overall sheet, then one sheet per sport that has picks, in
 * registry order. When only one sport has picks the overall sheet already is that sport's list, so it is returned alone.
 */
export function buildSheets(picks: SheetPick[], weekday: string): Sheet[] {
  const ev = picks.filter(isEvPick)
  if (ev.length === 0) return []
  const sports = SPORT_SLUGS.filter(s => ev.some(p => p.sport === s))
  if (sports.length === 1) return [sheetFor(sports[0], ev, weekday)]
  return [sheetFor('all', ev, weekday), ...sports.map(s => sheetFor(s, ev.filter(p => p.sport === s), weekday))]
}

/** X counts an emoji as 2 characters, which is exactly what String.length reports for a surrogate pair. */
export const postLength = (text: string): number => text.length
