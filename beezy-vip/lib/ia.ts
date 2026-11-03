/**
 * Information architecture: the ONE place that says which pages exist in the top nav and which pages are views of
 * the same section. Five questions, five nav items; everything else is a tab inside one of them.
 *
 *   Scanner  what is mispriced right now?     /ev (Odds Terminal: every book's player-prop price) + /ev/games (NBA / NHL game lines:
 *            model estimate vs the books, UNVALIDATED) + /edge (prop research view)
 *   Picks    what does the model say today?   /picks (top 10 PLAYER picks, all sports) + /picks/games (game picks: the graded MLB card while it is in season, plus the NBA / NHL model's side per game, unvalidated) +
 *            /picks/history + /cheat-sheet. Picks is the tracked, graded record; unvalidated estimates live in the Scanner.
 *   Results  what happened, and is it real?   /results (ledger)      + /analytics tabs + /models (methodology)
 *   Tools    calculators and dashboards       /tools/*
 *   Home     proof, nothing else
 *
 * There is no global sport ribbon: it linked away from the page it sat on. A page that needs a sport filter has its own pills, built
 * from the sports that actually have games (lib/sports.ts sportsInSeason / sportsWithRows). Every sport still lives at /picks/[sport]
 * (picks board, /projections, /[system]); /sports/[sport] redirects there.
 */
import type { SportSlug } from '@/lib/sports'

export interface SectionTab { label: string; href: string; /** extra path prefixes that keep this tab active */ also?: string[] }
export interface Section { id: 'scanner' | 'picks' | 'results'; prefixes: string[]; /** paths that belong to the section only when they match exactly */ exact?: string[]; tabs: SectionTab[] }

export const SECTIONS: Section[] = [
  {
    id: 'scanner', prefixes: ['/ev', '/edge'],
    tabs: [{ label: 'Odds Terminal', href: '/ev' }, { label: 'Game Lines', href: '/ev/games' }, { label: 'Prop Research', href: '/edge' }],
  },
  {
    // The tab strip is for the all-sports views only; MLB and the other sports are chosen in the ribbon and have their own headers.
    id: 'picks', prefixes: ['/picks/history', '/picks/games', '/cheat-sheet'], exact: ['/picks'],
    tabs: [
      { label: 'Daily Card', href: '/cheat-sheet' },
      { label: 'Top Signals', href: '/picks' },
      { label: 'Game Picks', href: '/picks/games' },
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
  { label: 'Tools',        href: '/tools',   prefixes: ['/tools'] },
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
  if (slug === 'all') return pathname === '/picks' || under(pathname, '/picks/history') || under(pathname, '/picks/games') || under(pathname, '/cheat-sheet')
  return under(pathname, sportHref(slug))
}
