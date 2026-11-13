/**
 * Information architecture: the ONE canonical contract governing top-level navigation,
 * section views, population boundaries, filter vocabulary, and chrome budgets.
 *
 * Product Loop (the Stranger Test):
 *   CANDIDATE (Scanner) -> COMMITMENT (Picks) -> RECORD (Results) [+ Learn guides + Home proof]
 *
 * Five top-level NAV items (Tools removed per PR #249):
 *   - Home (/): Live proof of the Candidate -> Commitment -> Record loop (headline settled ROI/CLV,
 *     today's Daily Card or Gate Receipt, and top live Scanner candidates).
 *   - Scanner (/ev, /ev/projections, /ev/games, /edge): What is mispriced right now?
 *     * Odds Screen (/ev): What is every book charging for this prop right now, and where does a price break from consensus?
 *     * Projections (/ev/projections): What is the model's expected stat line (lambda) for every prop and game line today?
 *     * Game Lines (/ev/games): What are the full-game spreads, totals, and moneylines across books and game engines?
 *     * Prop Research (/edge): Which flagged or top-ranked candidate props merit a bet, backed by L10 logs and usage?
 *   - Picks (/picks, /picks/history): What is the model officially committing to today?
 *     * Today's Card (/picks; /cheat-sheet aliases here): Official plays that cleared every validation & consensus gate.
 *     * Past Cards (/picks/history): Daily archive of past official Daily Cards (1u flat).
 *     * Rule: Unvalidated estimates and candidate boards NEVER live in Picks; they live in Scanner.
 *   - Results (/results, /analytics, /models): What happened, and is it real?
 *     * Settled Bets (/results): Canonical graded ledger (toggleable: Official Daily Card 1u | All Tracked Paper | +EV Alerts).
 *     * Analytics (/analytics): Bankroll, calibration, book performance, and system health sub-views.
 *     * Models (/models): Methodology, validation status, and live accuracy specs.
 *   - Learn (/learn): How do these markets, de-vigged consensus, and models work?
 *
 * Population & Backfill Rule:
 *   Projections (/ev/projections) is the exhaustive league-day census (up to 1,200 rows/league, priced or unpriced).
 *   Prop Research (/edge) is a cross-sport shortlist combining Market Sweep flags + top 200 priced projection rows/league
 *     (plus MLB's scored Model Board). Every row/card in /edge MUST badge its source ("MARKET SWEEP" vs "MODEL PROJECTION"), expose a Source filter, and display "L10: n/a" (never sorting null as -1) when box-score logs are absent.
 *
 * Taxonomy, Filter, State & Chrome Rules:
 *   1. No Second Taxonomies: No page may render a secondary tab strip re-grouping its parent SECTIONS views (ResearchTabs deleted).
 *   2. Filter Vocabulary: "Market" = the stat/bet type being priced everywhere; model selectors = System(s); row origin = "Source".
 *   backend commitment = "Pick Tier" (Official/Tracked); candidate filter = "Signal Type" (?type=); score grade = "Score Band" (?band=).
 *   3. Population Defaults: Any default-on gate (e.g., MLB Qualified Only) renders as a named Preset chip, counts in active filters,
 *     and resets on Clear. Filtered views display "Showing X of Y."
 *   4. State Policy: URL query params are the Scanner-wide standard (shareable, reload-safe). Legacy ?tier= and ?result= params
 *     are ignored/stripped on all Scanner routes.
 *   5. Chrome & Mobile Parity: Max 2 pinned rows (<= 96px desktop, <= 88px mobile) above content. LiveTicker is non-sticky;
 *     at most 2 in-page control rows precede data. BOTTOM_NAV (5 job slots) + MobileMenu must reach every NAV and SECTIONS route.
 */

/**
 * Route notes the header block does not cover:
 * - There is no global sport ribbon: it linked away from the page it sat on. A page that needs a sport filter has its own pills, built
 *   from the sports that actually have games (lib/sports.ts sportsInSeason / sportsWithRows). Every sport still lives at /picks/[sport]
 *   (picks board, /[system]); projections are NOT part of it: /picks/[sport]/projections redirects to /ev/projections. /sports/[sport] redirects there.
 * - /picks/games (game picks) still resolves but is HIDDEN: no tab, no link, no sitemap entry, noindex.
 * - Analytics carries its four sub-views (Bankroll | Calibration | Books | Health) as internal sub-tabs on /analytics?tab=;
 *   the Results section strip holds the single Analytics tab (Q1(g)).
 */
import type { SportSlug } from '@/lib/sports'

export interface SectionTab { label: string; /** shorter label for a phone, where up to four tabs share the row without scrolling */ short?: string; href: string; /** extra path prefixes that keep this tab active */ also?: string[] }
export interface Section { id: 'scanner' | 'picks' | 'results'; /** display name, used for the tab strip's accessible label */ label: string; prefixes: string[]; /** paths that belong to the section only when they match exactly */ exact?: string[]; tabs: SectionTab[] }

export const SECTIONS: Section[] = [
  {
    id: 'scanner', label: 'Scanner', prefixes: ['/ev', '/edge'],
    tabs: [{ label: 'Odds Screen', short: 'Odds', href: '/ev' }, { label: 'Projections', href: '/ev/projections' }, { label: 'Game Lines', href: '/ev/games' }, { label: 'Prop Research', short: 'Props', href: '/edge' }],
  },
  {
    // The tab strip is for the all-sports views only; MLB and the other sports are chosen in the ribbon and have their own headers.
    id: 'picks', label: 'Picks', prefixes: ['/picks/history', '/cheat-sheet'], exact: ['/picks'],
    tabs: [
      { label: 'Daily Card', href: '/cheat-sheet' },
      { label: 'Top Signals', href: '/picks' },
      { label: 'Graded History', href: '/picks/history' },
    ],
  },
  {
    id: 'results', label: 'Results', prefixes: ['/results', '/analytics', '/models'],
    tabs: [
      { label: 'Settled Bets', href: '/results' },
      { label: 'Analytics', href: '/analytics' },
      { label: 'Models', href: '/models' },
    ],
  },
]

export interface NavItem { label: string; href: string; live?: boolean; prefixes: string[] }

export const NAV: NavItem[] = [
  { label: 'Home',         href: '/',        prefixes: [] },
  { label: 'Scanner',      href: '/ev',      prefixes: ['/ev', '/edge'], live: true },
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

/** Which tab of a section is current. A tab href may carry a ?tab= query (a sub-view state); compare path first, then that param. */
export function tabActive(pathname: string, tabParam: string | null, tab: SectionTab): boolean {
  const [path, query] = tab.href.split('?')
  const want = query ? new URLSearchParams(query).get('tab') : null
  if (want) return pathname === path && (tabParam ?? 'bankroll') === want
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
