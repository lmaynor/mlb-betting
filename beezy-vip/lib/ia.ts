/**
 * Information architecture: the ONE place that says which pages exist in the top nav and which pages are views of
 * the same section. Five questions, five section nav items, plus the guides; everything else is a tab inside one of them.
 *
 *   Scanner  what is mispriced right now?     /ev (Odds Terminal: every book's player-prop price) + /ev/projections (the model's lambda for every
 *            player prop and NBA / NHL game line, UNVALIDATED) + /ev/games (every game's lines and prices: board + card, UNVALIDATED) + /edge (prop research view)
 *   Picks    what does the model say today?   /cheat-sheet (the Daily Card: every official play) + /picks (top signals, all sports) +
 *            /picks/history (those plays, graded). Picks is the tracked, graded record; unvalidated estimates live in the Scanner.
 *            /picks/games (game picks) still resolves but is HIDDEN: no tab, no link, no sitemap entry, noindex.
 *   Results  what happened, and is it real?   /results (ledger)      + /analytics tabs + /models (methodology)
 *   Learn    how does this market work?       /learn (the guides: one per market and concept; the system, model and tool pages link back to them)
 *   Home     proof, nothing else
 *
 * There is no global sport ribbon: it linked away from the page it sat on. A page that needs a sport filter has its own pills, built
 * from the sports that actually have games (lib/sports.ts sportsInSeason / sportsWithRows). Every sport still lives at /picks/[sport]
 * (picks board, /[system]); projections are NOT part of it: /picks/[sport]/projections redirects to /ev/projections. /sports/[sport] redirects there.
 */
import type { SportSlug } from '@/lib/sports'

export interface SectionTab { label: string; /** shorter label for a phone, where up to four tabs share the row without scrolling */ short?: string; href: string; /** extra path prefixes that keep this tab active */ also?: string[] }
export interface Section { id: 'scanner' | 'picks' | 'results'; prefixes: string[]; /** paths that belong to the section only when they match exactly */ exact?: string[]; tabs: SectionTab[] }

export const SECTIONS: Section[] = [
  {
    id: 'scanner', prefixes: ['/ev', '/edge'],
    tabs: [{ label: 'Odds Terminal', short: 'Odds', href: '/ev' }, { label: 'Projections', href: '/ev/projections' }, { label: 'Game Lines', href: '/ev/games' }, { label: 'Prop Research', short: 'Props', href: '/edge' }],
  },
  {
    // The tab strip is for the all-sports views only; MLB and the other sports are chosen in the ribbon and have their own headers.
    id: 'picks', prefixes: ['/picks/history', '/cheat-sheet'], exact: ['/picks'],
    tabs: [
      { label: 'Daily Card', href: '/cheat-sheet' },
      { label: 'Top Signals', href: '/picks' },
      { label: 'Graded History', href: '/picks/history' },
    ],
  },
  {
    id: 'results', prefixes: ['/results', '/analytics', '/models'],
    tabs: [
      { label: 'Ledger', href: '/results' },
      { label: 'Bankroll', href: '/analytics?tab=bankroll' },
      { label: 'Calibration', href: '/analytics?tab=calibration' },
      { label: 'Books', href: '/analytics?tab=books' },
      { label: 'Health', href: '/analytics?tab=health' },
      { label: 'Models', href: '/models' },
    ],
  },
]

export interface NavItem { label: string; href: string; live?: boolean; prefixes: string[] }

export const NAV: NavItem[] = [
  { label: 'Home',         href: '/',        prefixes: [] },
  { label: 'Odds Terminal', href: '/ev',     prefixes: ['/ev', '/edge'], live: true },
  { label: 'Picks',        href: '/picks',   prefixes: ['/picks', '/cheat-sheet', '/sports'] },
  { label: 'Results',      href: '/results', prefixes: ['/results', '/analytics', '/models'] },
  { label: 'Learn',        href: '/learn',   prefixes: ['/learn'] },
]

const under = (path: string, prefix: string) => path === prefix || path.startsWith(prefix + '/')

export function navActive(pathname: string, item: NavItem): boolean {
  return item.href === '/' ? pathname === '/' : item.prefixes.some(p => under(pathname, p))
}

export function sectionFor(pathname: string): Section | null {
  return SECTIONS.find(s => s.prefixes.some(p => under(pathname, p)) || (s.exact ?? []).includes(pathname)) ?? null
}

/** Which tab of a section is current. Tabs may carry a ?tab= query (analytics); compare path first, then that param. */
export function tabActive(pathname: string, tabParam: string | null, tab: SectionTab): boolean {
  const [path, query] = tab.href.split('?')
  const want = query ? new URLSearchParams(query).get('tab') : null
  if (path === '/analytics') return pathname === '/analytics' && (tabParam ?? 'bankroll') === want
  if (path === '/picks') return pathname === '/picks'
  if (path === '/ev') return pathname === '/ev'                 // /ev/games is its own tab
  return under(pathname, path)
}

/** Sport ribbon target: ALL SPORTS is the cross-sport Top 10; every sport is /picks/[sport]. */
export function sportHref(slug: SportSlug | 'all'): string {
  return slug === 'all' ? '/picks' : `/picks/${slug}`
}

/** Which ribbon item is current. Pages outside the Picks family (home, /ev, /results) light none. */
export function sportActive(pathname: string, slug: SportSlug | 'all'): boolean {
  if (slug === 'all') return pathname === '/picks' || under(pathname, '/picks/history') || under(pathname, '/cheat-sheet')
  return under(pathname, sportHref(slug))
}
