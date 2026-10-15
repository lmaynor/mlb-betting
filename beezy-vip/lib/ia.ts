/**
 * Information architecture: the ONE place that says which pages exist in the top nav and which pages are views of
 * the same section. Five questions, five nav items; everything else is a tab inside one of them.
 *
 *   Scanner  what is mispriced right now?     /ev (table)            + /edge (prop research view)
 *   Picks    what does the model say today?   /picks (today)         + /picks/mlb (systems) + /cheat-sheet (share card)
 *   Results  what happened, and is it real?   /results (ledger)      + /analytics tabs + /models (methodology)
 *   Tools    calculators and dashboards       /tools/*
 *   Home     proof, nothing else
 *
 * /sports/mlb is retired (it duplicated Picks); NFL, NHL and NBA keep status pages at /sports/[sport].
 */
import type { SportSlug } from '@/lib/sports'

export interface SectionTab { label: string; href: string; /** extra path prefixes that keep this tab active */ also?: string[] }
export interface Section { id: 'scanner' | 'picks' | 'results'; prefixes: string[]; tabs: SectionTab[] }

export const SECTIONS: Section[] = [
  {
    id: 'scanner', prefixes: ['/ev', '/edge'],
    tabs: [{ label: '+EV Terminal', href: '/ev' }, { label: 'Prop Research', href: '/edge' }],
  },
  {
    id: 'picks', prefixes: ['/picks', '/cheat-sheet'],
    tabs: [
      { label: 'Today', href: '/picks' },
      { label: 'Systems', href: '/picks/mlb' },
      { label: 'Daily Card', href: '/cheat-sheet' },
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
  { label: '+EV Terminal', href: '/ev',      prefixes: ['/ev', '/edge'], live: true },
  { label: 'Picks',        href: '/picks',   prefixes: ['/picks', '/cheat-sheet'] },
  { label: 'Results',      href: '/results', prefixes: ['/results', '/analytics', '/models'] },
  { label: 'Tools',        href: '/tools',   prefixes: ['/tools'] },
]

const under = (path: string, prefix: string) => path === prefix || path.startsWith(prefix + '/')

export function navActive(pathname: string, item: NavItem): boolean {
  return item.href === '/' ? pathname === '/' : item.prefixes.some(p => under(pathname, p))
}

export function sectionFor(pathname: string): Section | null {
  return SECTIONS.find(s => s.prefixes.some(p => under(pathname, p))) ?? null
}

/** Which tab of a section is current. Tabs may carry a ?tab= query (analytics); compare path first, then that param. */
export function tabActive(pathname: string, tabParam: string | null, tab: SectionTab): boolean {
  const [path, query] = tab.href.split('?')
  const want = query ? new URLSearchParams(query).get('tab') : null
  if (path === '/analytics') return pathname === '/analytics' && (tabParam ?? 'bankroll') === want
  if (path === '/picks') return pathname === '/picks'
  if (path === '/picks/mlb') return under(pathname, '/picks/mlb')
  return under(pathname, path)
}

/** Sport ribbon target: MLB is just Picks now; the other sports have their own status pages. */
export function sportHref(slug: SportSlug): string {
  return slug === 'mlb' ? '/picks' : `/sports/${slug}`
}
