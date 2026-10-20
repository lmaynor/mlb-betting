/**
 * The "POST TO X" cheat sheets on the Daily Card. Pure (no React, no Next) so the page and the tests share one formatter.
 *
 *   Tuesday +EV NBA Cheat Sheet <emoji>
 *
 *   1. Jalen Williams U 4.5 Reb -110 DK | +14.6% EV
 *
 * One OVERALL sheet then one sheet per sport that has picks. A sheet is built from the SAME list the card shows for that chip
 * (the page passes the card's own arrays), so the screenshot and the text always list the same picks in the same order.
 *
 * Honest labels: a pick's percentage is an "EV" only when it is a real price EV (a tracked +EV alert, or a projection where model
 * AND market agree). A model-only pick shows a model-vs-market probability gap, which can sit beside a NEGATIVE price EV, so its
 * line says "edge", and the "+EV" in the header appears only when every listed line is an EV.
 *
 * Hard 280 cap: a sheet is never longer than POST_LIMIT. If it does not fit, first-name initials are used, and only then are the
 * lowest-ranked lines dropped (the UI says how many). The main post carries no link; the link is in LINK_REPLY.
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
  /** lines shown / lines offered (fewer shown means the 280 cap dropped the lowest-ranked ones) */
  lines: number
  offered: number
}

export const SHEET_SIZE = 5
/** X's post limit. A hard cap: no sheet is ever longer. */
export const POST_LIMIT = 280

/** The second post: the only place the link lives. Typed by the owner. */
export const LINK_REPLY = "For today's full slate of +EV bets and model projections, check out https://beezy.fyi and join our Discord."

/** `edge` is a real price EV only for these bases: a tracked +EV alert, or a projection where model AND market agree. */
const EV_BASES = new Set<TopPick['basis']>(['ev', 'confluence'])

export const isEvBasis = (p: Pick<SheetPick, 'basis'>): boolean => EV_BASES.has(p.basis)

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

/**
 * Book names as bettors type them. Only abbreviations that are genuinely standard are used; every other book keeps its full
 * display name (the site's own short codes such as NOV / PRX / HRB are not something to post). `espnbet` is the key the feeds
 * still send for the book that is now theScore Bet.
 */
const BOOK_POST_NAME: Record<string, string> = { draftkings: 'DK', fanduel: 'FD', betmgm: 'MGM', caesars: 'CZR' }
export const bookPostName = (book: string | null | undefined): string => (book ? BOOK_POST_NAME[book.toLowerCase()] ?? bookDisplay(book).label : '')

/** "Jalen Williams" -> "J. Williams"; "P.J. Washington Jr." and single names are left alone. Used only when a sheet would not fit. */
export function abbrevName(who: string): string {
  const parts = who.trim().split(/\s+/)
  if (parts.length < 2 || parts[0].includes('.')) return who.trim()
  return `${parts[0][0]}. ${parts.slice(1).join(' ')}`
}

function line(p: SheetPick, rank: number, tagSport: boolean, abbrev: boolean): string {
  const book = bookPostName(p.book)
  const name = abbrev ? abbrevName(p.who) : p.who
  const who = [tagSport ? SPORT_PILLARS[p.sport].shortName : '', name].filter(Boolean).join(' ')
  const price = p.odds == null ? '' : fmtOdds(p.odds)
  const head = [`${rank}.`, who, shortMarket(p.what), price, book].filter(Boolean).join(' ')
  const tag = p.edge != null && p.edge > 0 ? ` | +${(p.edge * 100).toFixed(1)}% ${isEvBasis(p) ? 'EV' : 'edge'}` : ''
  return `${head}${tag}`
}

function render(id: 'all' | SportSlug, picks: SheetPick[], weekday: string, abbrev: boolean): string {
  const name = id === 'all' ? '' : `${SPORT_PILLARS[id].shortName} `
  const emoji = id === 'all' ? '' : ` ${EMOJI[id]}`
  const ev = picks.length > 0 && picks.every(isEvBasis) ? '+EV ' : ''
  const header = `${weekday} ${ev}${name}Cheat Sheet${emoji}`
  return `${header}\n\n${picks.map((p, i) => line(p, i + 1, id === 'all', abbrev)).join('\n')}`
}

/** Longest sheet <= POST_LIMIT: all lines with full names, then all with initials, then fewer lines (never fewer than one). */
function sheetFor(id: 'all' | SportSlug, picks: SheetPick[], weekday: string): Sheet {
  const offered = picks.slice(0, SHEET_SIZE)
  for (let n = offered.length; n >= 1; n--) {
    for (const abbrev of [false, true]) {
      const text = render(id, offered.slice(0, n), weekday, abbrev)
      if (postLength(text) <= POST_LIMIT) return { id, label: id === 'all' ? 'OVERALL' : SPORT_PILLARS[id].shortName, text, lines: n, offered: offered.length }
    }
  }
  // unreachable in practice (one initialled line is far under 280); the cap is still enforced
  return { id, label: id === 'all' ? 'OVERALL' : SPORT_PILLARS[id].shortName, text: render(id, offered.slice(0, 1), weekday, true).slice(0, POST_LIMIT), lines: 1, offered: offered.length }
}

/**
 * `all` = the picks the card shows under its "All" chip; `bySport[s]` = the picks it shows under sport s's chip (both best first).
 * Returns the overall sheet, then one per sport with picks in registry order. With picks in only one sport the overall sheet is
 * that sport's, so it is returned alone.
 */
export function buildSheets(all: SheetPick[], bySport: Partial<Record<SportSlug, SheetPick[]>>, weekday: string): Sheet[] {
  const sports = SPORT_SLUGS.filter(s => (bySport[s]?.length ?? 0) > 0)
  if (sports.length === 0) return all.length ? [sheetFor('all', all, weekday)] : []
  if (sports.length === 1) return [sheetFor(sports[0], bySport[sports[0]] as SheetPick[], weekday)]
  return [sheetFor('all', all, weekday), ...sports.map(s => sheetFor(s, bySport[s] as SheetPick[], weekday))]
}

/** X counts an emoji as 2 characters, which is exactly what String.length reports for a surrogate pair. */
export const postLength = (text: string): number => text.length
