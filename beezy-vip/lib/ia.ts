/**
 * Information architecture: the ONE place that says which pages exist in the top nav and which pages are views of
 * the same section. Five questions, five nav items; everything else is a tab inside one of them.
 *
 *   Scanner  what is mispriced right now?     /ev (Odds Terminal: every book's price)   + /edge (prop research view)
 *   Picks    what does the model say today?   /picks (top 10 PLAYER picks, all sports) + /picks/games + /picks/history + /cheat-sheet
 *   Results  what happened, and is it real?   /results (ledger)      + /analytics tabs + /models (methodology)
 *   Tools    calculators and dashboards       /tools/*
 *   Home     proof, nothing else
 *
 * Sport ribbon (under the nav): ALL SPORTS = /picks, then one item per LIVE pillar in lib/sports.ts, each at /picks/[sport]:
 * the same page hierarchy for every sport (picks board, /projections, /[system]). /sports/[sport] redirects to /picks/[sport].
 */
import type { SportSlug } from '@/lib/sports'

export interface SectionTab { label: string; href: string; /** extra path prefixes that keep this tab active */ also?: string[] }
export interface Section { id: 'scanner' | 'picks' | 'results'; prefixes: string[]; /** paths that belong to the section only when they match exactly */ exact?: string[]; tabs: SectionTab[] }

export const SECTIONS: Section[] = [
  {
    id: 'scanner', prefixes: ['/ev', '/edge'],
    tabs: [{ label: 'Odds Terminal', href: '/ev' }, { label: 'Prop Research', href: '/edge' }],
  },
  {
    // The tab strip is for the all-sports views only; MLB and the other sports are chosen in the ribbon and have their own headers.
    id: 'picks', prefixes: ['/picks/history', '/picks/games', '/cheat-sheet'], exact: ['/picks'],
    tabs: [
      { label: 'Daily Card', href: '/cheat-sheet' },
      { label: 'Top Signals', href: '/picks' },
      { label: 'Game Lines', href: '/picks/games' },
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
