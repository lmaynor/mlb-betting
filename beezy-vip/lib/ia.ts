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

/**
 * The pipeline stage a surface belongs to (the stranger test, Q6(c)): candidate surfaces explore unvalidated
 * estimates, commitment surfaces state what the model officially plays, record surfaces grade what happened,
 * guide surfaces teach, and Home is proof. 'utility' covers account, auth, and legal infrastructure, which is
 * registered so no route is invisible to the gate even though it sits outside the product loop.
 */
export type PipelineStage = 'candidate' | 'commitment' | 'record' | 'guide' | 'proof' | 'utility'

export interface SectionTab { label: string; /** shorter label for a phone, where up to four tabs share the row without scrolling */ short?: string; href: string; /** extra path prefixes that keep this tab active */ also?: string[]; stage: PipelineStage; /** the one question this view answers for a first-time reader */ strangerQuestion: string }
export interface Section { id: 'scanner' | 'picks' | 'results'; /** display name, used for the tab strip's accessible label */ label: string; prefixes: string[]; /** paths that belong to the section only when they match exactly */ exact?: string[]; tabs: SectionTab[] }

export const SECTIONS: Section[] = [
  {
    id: 'scanner', label: 'Scanner', prefixes: ['/ev', '/edge'],
    tabs: [
      { label: 'Odds Screen', short: 'Odds', href: '/ev', stage: 'candidate', strangerQuestion: 'What is every book charging for this prop right now, and where does a price break from consensus?' },
      { label: 'Projections', href: '/ev/projections', stage: 'candidate', strangerQuestion: "What is the model's expected stat line (lambda) for every prop and game line today?" },
      { label: 'Game Lines', href: '/ev/games', stage: 'candidate', strangerQuestion: 'What are the full-game spreads, totals, and moneylines across books and game engines?' },
      { label: 'Prop Research', short: 'Props', href: '/edge', stage: 'candidate', strangerQuestion: 'Which flagged or top-ranked candidate props merit a bet, backed by L10 logs and usage?' },
    ],
  },
  {
    // The tab strip is for the all-sports views only; MLB and the other sports are chosen in the ribbon and have their own headers.
    id: 'picks', label: 'Picks', prefixes: ['/picks/history', '/cheat-sheet'], exact: ['/picks'],
    tabs: [
      { label: 'Daily Card', href: '/cheat-sheet', stage: 'commitment', strangerQuestion: 'Which official plays cleared every validation and consensus gate today?' },
      { label: 'Top Signals', href: '/picks', stage: 'candidate', strangerQuestion: 'Which candidate props rank highest right now, across all sports?' },
      { label: 'Graded History', href: '/picks/history', stage: 'record', strangerQuestion: 'How did past Daily Cards grade?' },
    ],
  },
  {
    id: 'results', label: 'Results', prefixes: ['/results', '/analytics', '/models'],
    tabs: [
      { label: 'Settled Bets', href: '/results', stage: 'record', strangerQuestion: 'What is the settled, graded record of every tracked bet?' },
      { label: 'Analytics', href: '/analytics', stage: 'record', strangerQuestion: 'Is the record profitable, calibrated, and healthy across books and systems?' },
      { label: 'Models', href: '/models', stage: 'record', strangerQuestion: 'How do the models work, and is their accuracy validated?' },
    ],
  },
]

export interface NavItem { label: string; href: string; live?: boolean; prefixes: string[]; stage: PipelineStage; /** the section's one-sentence acceptance answer (Q6(a)) */ strangerQuestion: string }

export const NAV: NavItem[] = [
  { label: 'Home',         href: '/',        prefixes: [], stage: 'proof', strangerQuestion: 'Beezy scans every sportsbook and model projection to surface verified betting edges, commits to strict daily plays, and grades every single one in public.' },
  { label: 'Scanner',      href: '/ev',      prefixes: ['/ev', '/edge'], live: true, stage: 'candidate', strangerQuestion: 'Explore every live prop price, model projection, and market dislocation across books before deciding what merits a bet.' },
  { label: 'Picks',        href: '/picks',   prefixes: ['/picks', '/cheat-sheet', '/sports'], stage: 'commitment', strangerQuestion: "See today's official plays, the exact bets that cleared every model and consensus gate, and inspect past daily cards." },
  { label: 'Results',      href: '/results', prefixes: ['/results', '/analytics', '/models'], stage: 'record', strangerQuestion: 'Verify the permanent, settled track record, bankroll performance, calibration, and model methodology behind every graded play.' },
  { label: 'Learn',        href: '/learn',   prefixes: ['/learn'], stage: 'guide', strangerQuestion: "Understand the math, market mechanics, and grading rules behind +EV betting, de-vigged consensus, and Beezy's models." },
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

/**
 * The Odds Screen mode registry (Q6(c)): every /ev mode declares its stage and the analytical question it asks
 * (Q4(d): a mode is an analytical question, never a line format). app/ev/odds-constants.ts renders its MODES
 * from the same keys; tests/ia.test.ts fails if the two lists drift apart in either direction.
 */
export interface EvMode { key: string; label: string; stage: PipelineStage; strangerQuestion: string }

export const EV_MODES: EvMode[] = [
  { key: 'ev', label: '+EV Scanner', stage: 'candidate', strangerQuestion: "Where does a book's price beat the de-vigged fair value built from the reference books?" },
  { key: 'outliers', label: 'Market Outliers', stage: 'candidate', strangerQuestion: "Where does a single book's price break from the consensus of the other books?" },
  { key: 'arb', label: 'Arbitrage', stage: 'candidate', strangerQuestion: "Where do two books' prices on opposite sides lock in a return either way?" },
  { key: 'middles', label: 'Middles', stage: 'candidate', strangerQuestion: "Where do two books' lines leave a gap that both sides can win?" },
  { key: 'longshots', label: 'Alt & Tail Ladders', stage: 'candidate', strangerQuestion: 'Where across the entire slate do alternate or tail rungs break from a 4+ book two-sided consensus (VERIFIED DISLOCATION) or offer a +8% or better one-sided payout premium?' },
  { key: 'sgp', label: 'SGP Proxies', stage: 'candidate', strangerQuestion: 'What do same-game parlay combinations pay against their combined fair price?' },
]

/**
 * The route registry (Q6(c)): every page route in app/ is declared here with its stage and stranger question.
 * tests/ia.test.ts walks the app/ tree and fails on any page route missing from this list, or any entry here
 * with no page behind it. API routes (route.ts) are not pages and are out of scope by construction.
 */
export interface RouteEntry { path: string; stage: PipelineStage; strangerQuestion: string }

export const ROUTES: RouteEntry[] = [
  { path: '/', stage: 'proof', strangerQuestion: 'Beezy scans every sportsbook and model projection to surface verified betting edges, commits to strict daily plays, and grades every single one in public.' },
  { path: '/about', stage: 'guide', strangerQuestion: 'What is beezy.fyi, and who runs it?' },
  { path: '/analytics', stage: 'record', strangerQuestion: 'Is the record profitable, calibrated, and healthy across books and systems?' },
  { path: '/blocked', stage: 'utility', strangerQuestion: 'Why is this page unavailable in this region?' },
  { path: '/cheat-sheet', stage: 'commitment', strangerQuestion: 'Which official plays cleared every validation and consensus gate today?' },
  { path: '/compare/oddsjam-alternative', stage: 'guide', strangerQuestion: 'How does beezy compare with OddsJam?' },
  { path: '/dashboard', stage: 'utility', strangerQuestion: 'What is my account status and subscription?' },
  { path: '/dashboard/history', stage: 'utility', strangerQuestion: 'What picks has my account followed?' },
  { path: '/dashboard/picks', stage: 'utility', strangerQuestion: 'Which picks does my account track?' },
  { path: '/dashboard/settings', stage: 'utility', strangerQuestion: 'How do I manage my account settings?' },
  { path: '/edge', stage: 'candidate', strangerQuestion: 'Which flagged or top-ranked candidate props merit a bet, backed by L10 logs and usage?' },
  { path: '/edge/projections', stage: 'candidate', strangerQuestion: "What is the model's expected stat line (lambda) for every prop and game line today? (Redirects to /ev/projections.)" },
  { path: '/ev', stage: 'candidate', strangerQuestion: 'What is every book charging for this prop right now, and where does a price break from consensus?' },
  { path: '/ev/games', stage: 'candidate', strangerQuestion: 'What are the full-game spreads, totals, and moneylines across books and game engines?' },
  { path: '/ev/projections', stage: 'candidate', strangerQuestion: "What is the model's expected stat line (lambda) for every prop and game line today?" },
  { path: '/learn', stage: 'guide', strangerQuestion: 'How do these markets, de-vigged consensus, and models work?' },
  { path: '/learn/[slug]', stage: 'guide', strangerQuestion: 'How does this one market or concept work?' },
  { path: '/legal/privacy', stage: 'utility', strangerQuestion: 'How is my data handled?' },
  { path: '/legal/refunds', stage: 'utility', strangerQuestion: 'What is the refund policy?' },
  { path: '/legal/responsible-gambling', stage: 'utility', strangerQuestion: 'What responsible gambling resources exist?' },
  { path: '/legal/terms', stage: 'utility', strangerQuestion: 'What are the terms of service?' },
  { path: '/login', stage: 'utility', strangerQuestion: 'How do I sign in?' },
  { path: '/models', stage: 'record', strangerQuestion: 'How do the models work, and is their accuracy validated?' },
  { path: '/models/[slug]', stage: 'record', strangerQuestion: "What is this sport's model, and how accurate is it?" },
  { path: '/models/[slug]/[system]', stage: 'record', strangerQuestion: "What is this system's method, and how accurate is it?" },
  { path: '/picks', stage: 'candidate', strangerQuestion: 'Which candidate props rank highest right now, across all sports?' },
  { path: '/picks/[sport]', stage: 'candidate', strangerQuestion: "What does the model say for this sport's props today?" },
  { path: '/picks/[sport]/[system]', stage: 'candidate', strangerQuestion: "What does this one system say for this sport today?" },
  { path: '/picks/[sport]/projections', stage: 'candidate', strangerQuestion: "What is the model's expected stat line (lambda) for every prop and game line today? (Redirects to /ev/projections.)" },
  { path: '/picks/[sport]/systems', stage: 'candidate', strangerQuestion: "Which systems cover this sport, and how does each work?" },
  { path: '/picks/games', stage: 'candidate', strangerQuestion: 'What does the model say for whole games today? (Hidden: resolves, but no tab, link, or sitemap entry.)' },
  { path: '/picks/history', stage: 'record', strangerQuestion: 'How did past Daily Cards grade?' },
  { path: '/pricing', stage: 'guide', strangerQuestion: 'What does beezy cost, and what does each plan unlock?' },
  { path: '/results', stage: 'record', strangerQuestion: 'What is the settled, graded record of every tracked bet?' },
  { path: '/signup', stage: 'utility', strangerQuestion: 'How do I create an account?' },
  { path: '/sports/[sport]', stage: 'candidate', strangerQuestion: "What does the model say for this sport's props today? (Redirects to /picks/[sport].)" },
]
