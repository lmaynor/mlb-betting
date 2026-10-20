# AEO (Answer Engine Optimization) -- audit and tickets for beezy.fyi

_2026-10-19. Status: T1, T3, T4 and T8 (scoped to /results and /models) are built on branch `feat/aeo-basics`; the rest are open._

Source: the 11-principle AEO requirements spec (ENG-01..05, REQ-SEO-01..02, REQ-CNT-01..04). The spec was written for a SaaS
marketing site (pricing tiers, "[Brand] vs [Competitor]" pages). beezy.fyi is a Next.js 15 app on Vercel (`beezy-vip/`) with a
public analytics surface, so each requirement was checked against what actually exists and marked **applies / partly / N/A**.

## How this was checked
- Live fetches of `https://beezy.fyi/`, `/pricing`, `/learn`, `/picks/nfl` with a bot user-agent (`OAI-SearchBot`, `PerplexityBot`,
  `ClaudeBot`) via curl, plus reads of `beezy-vip/app`, `components/layout`, `public/robots.txt`, `app/sitemap.xml/route.ts`.
- NOT run: Lighthouse/axe (ENG-03 score), a Screaming Frog crawl (REQ-SEO-02), requests from the bots' verified IP ranges
  (the Vercel firewall / bot-protection check in REQ-SEO-01). Those tickets say so.

## Summary

| Req | Verdict | Current state |
|---|---|---|
| REQ-SEO-01 robots + WAF | Partly | `*` already `Allow: /`; AI bots are not blocked, but `Disallow: /api` etc. exist and naive per-bot groups would DROP them (see T1). |
| REQ-SEO-02 link hygiene | Applies | Not audited; no scheduled crawl. |
| ENG-01 SSR | **Already met** | `/`, `/pricing`, `/learn`, `/picks/nfl` return 100-340 KB of server-rendered HTML with prices and an `<h1>`; one `<main>` each. Dashboards that fetch client-side are not verified (T7). |
| ENG-02 semantic HTML | Mostly met | Single `<header>`, `<nav>`, `<main>`, `<footer>` in layout; 0 `<div onClick>`. Gap: three `<nav>`s share the page with only one labelled (T6). |
| ENG-03 ARIA / labels | Unknown | 28 inputs/selects vs 100 aria-label/label/htmlFor hits; 68 `<button>`s without an inline aria-label (many have text, so not all violations). Needs axe (T6). |
| ENG-04 text over image | Likely met | Charts are SVG/TSX; OG cards are social-only. Spot-check only (T7). |
| ENG-05 dates + schema | **Gap** | Zero JSON-LD anywhere. `/learn` articles have no date field at all (`lib/articles-static.ts` Article has slug/title/category/keyword/description/content). |
| REQ-CNT-01 entity definition | **Gap** | Home hero is a slogan ("Stop betting into vig. Strike market dislocations."); meta description is closer to a definition. |
| REQ-CNT-02 question-first H2 | **N/A / rewrite** | No `[Brand] vs [Competitor]` pages exist. Adapt to `/learn` and `/models` (T5). |
| REQ-CNT-03 "as of" anchoring | **Applies, high value** | Track-record and ROI figures are the site's core claims. |
| REQ-CNT-04 60-90 day refresh | Partly | Live pages refresh by data; `/learn` and `/pricing` copy is static. |

## Cross-cutting finding: sitemap `lastmod` is fake
`app/sitemap.xml/route.ts` sets `<lastmod>` to `new Date()` for every URL on every request. Answer engines that use lastmod as a
freshness signal see everything "modified now", which is indistinguishable from nothing being modified. Fix with real dates (T3).

---

## Tickets

### T1 -- robots.txt: explicit AI crawler groups (REQ-SEO-01) -- S, no risk if done as written
Current `public/robots.txt` has a single `User-agent: *` group. AI bots are already allowed by it, so this is about explicitness
and about not breaking the existing rules.

**Gotcha in the spec:** a crawler obeys only the most specific matching group. Pasting the spec's blocks (`User-agent: OAI-SearchBot` /
`Allow: /`) makes those bots ignore the `*` group, so `Disallow: /api`, `/dashboard`, `/login`, `/signup`, `/tools/slate`,
`/tools/clv-tracker`, `/tools/bet-tracker` stop applying to them. Each AI group must repeat the full Disallow list (and keep
`Allow: /api/og`).

Also decide, because the spec only lists search/citation bots: **training crawlers** (`GPTBot`, `ClaudeBot`, `Google-Extended`,
`CCBot`, `Applebot-Extended`). The spec says nothing about them; leaving them on `*` allows them. That is a policy call for you.
Note the spec's `Claude-Web` is a legacy name; Anthropic's current agents are `ClaudeBot`, `Claude-User`, `Claude-SearchBot`.
Verify the current UA list against each vendor's docs before shipping.

Acceptance: `/robots.txt` 200; each named bot group contains the same Disallow set as `*`; a robots parser test (unit test in
`beezy-vip/tests`) asserts `/dashboard` and `/api/admin` are disallowed for every named bot and `/picks/nfl` is allowed.

### T2 -- Firewall / bot-protection check (REQ-SEO-01) -- S, needs your Vercel access
curl with bot UAs returned 200 on `/robots.txt` and `/pricing`, but spoofed UAs from my IP prove nothing about Vercel Bot
Protection / WAF rules, which key on verified IP ranges. Manual check in the Vercel dashboard (Firewall > Bot Management, any
custom rules, Attack Challenge Mode) and against the vendors' published IP lists. Also note `middleware.ts` geo-blocks
`/picks`, `/dashboard`, `/signup` by state once `BLOCKED_STATES` is set: bots arriving from a blocked region would be redirected to
`/blocked`. Decide whether verified bots should bypass that.

### T3 -- Real `lastmod` in the sitemap (cross-cutting) -- S
Replace `now` with a per-route date: article `dateModified` (T4) for `/learn/*`, git/commit or build date for static pages,
and the data-refresh timestamp (or omit `lastmod`) for live board routes. Drop `changefreq`/`priority`; major engines ignore them.
Acceptance: two consecutive requests an hour apart return identical `lastmod` for unchanged pages.

### T4 -- JSON-LD + visible dates (ENG-05) -- M
1. Add `datePublished`/`dateModified` to the `Article` type and the generator that writes `lib/articles-static.ts` (header says
   "Auto-generated -- do not edit manually": find the generator first; the source is `content/learn`).
2. Visible byline on `/learn/[slug]`: `Published YYYY-MM-DD / Last updated YYYY-MM-DD`.
3. JSON-LD per page type, server-rendered in the initial HTML: `Organization` + `WebSite` (root layout), `Article` (learn),
   `FAQPage` only where the page really has Q&A (T5), `BreadcrumbList` for `/picks/[sport]/...`.
   **Do not** add `Product`/`Offer` schema to `/pricing` unless the tiers are live and priced; tier gating is currently built but OFF
   (PRE_LAUNCH, see memory 2026-10-16), and marking up an unavailable offer is misleading structured data.
4. Dates must be real. Do not backfill every article with today's date; use git history for the first/last commit of each file.
Acceptance: Rich Results Test passes for one article; schema dates equal the visible dates; no JSON-LD on pages that have no
matching visible content.

### T5 -- Content rewrite: definition + question-first answers (REQ-CNT-01/02) -- M, needs your copy decisions
- **Entity definition** in the first `<p>` of `<main>` on `/` and `/models`: "beezy.fyi is a quantitative sports-analytics platform
  for bettors that publishes model projections, a live +EV terminal and a fully public paper-tracked record across MLB, NFL, NHL and
  NBA." Keep the hero slogan; put the definition directly under it. Align `metadata.description` in `app/layout.tsx`.
- **Question-first H2**: there are no vs-competitor pages, so apply the pattern to `/learn` articles and `/models`
  ("Does the model beat the closing line?"). The spec's template tells pages to recommend the product and concede a use case to
  the competitor. For a betting-model site the honest version is the existing "no edge claimable" stance, e.g. say which
  systems have NO_EDGE. Do not write copy that implies a profitable edge the data does not support (project rule: CLV, not ROI,
  is the go/no-go). Also check the legal copy under `/legal` before any "better choice" language.
- Decide whether competitor comparison pages (vs OddsJam, Unabated, etc.) are wanted at all; that is a product decision, not an AEO one.

### T6 -- Accessibility pass (ENG-02/03) -- M
Run axe-core / Lighthouse on `/`, `/pricing`, `/picks/nfl`, `/ev`, `/learn/[slug]`; fix to 95+.
Known suspects: icon-only controls in `components/layout/bottom-nav.tsx` and `live-ticker.tsx` (links with no `aria-label` seen),
the filter bar inputs, the three `<nav>` landmarks (`nav.tsx`, `bottom-nav.tsx`, `section-tabs.tsx`; give each a distinct label), and
whether the spec's "exactly one `<header>`" holds once the ticker/chrome is counted. Add `eslint-plugin-jsx-a11y` if not enabled.

### T7 -- Render-without-JS check on data pages (ENG-01/04) -- S
`/models` is `force-dynamic` and `/picks/*`, `/ev`, `/results` pull from the Cloud Run API. Verify with JS disabled that the
headline numbers (win rate, ROI, CLV, row counts) are in the initial HTML rather than hydrated afterwards. The four pages curl'd
had prices/numbers in the HTML, but these were not inspected for client-only tables. Also spot-check that no table or chart exists
only as an image.

### T8 -- "As of" anchoring on track-record claims (REQ-CNT-03) -- M
Every ROI / hit-rate / sample-size / "N settled bets" figure in body copy (home, `/results`, `/models`, `/learn`, system pages) gets
an explicit date ("As of 2026-10-19, 1,234 settled paper bets ..."), generated from the data's own timestamp rather than
hard-coded so it cannot go stale. Remove unanchored "currently / recently / now / this season" around factual claims
(grep list: `currently`, `recently`, `right now`, `this year`, `this season`). Keep the existing "paper-tracked" labeling next to
every figure.

### T9 -- Link hygiene (REQ-SEO-02) -- S
Crawl `https://beezy.fyi` (Screaming Frog free tier is 500 URLs; the site has more with `/picks/[sport]/[system]`, so use
Sitebulb or a small script against `sitemap.xml`). Zero internal 4xx/5xx; flatten redirect chains (note the Clerk and `/blocked`
redirects are intentional). Schedule monthly: a GitHub Actions cron running a link checker against the sitemap is cheaper than
a paid crawler. Expected hot spots: `/learn/*` internal links in markdown, and any links to routes removed in the picks redesign.

### T10 -- Refresh SOP (REQ-CNT-04) -- S, process only
Tier-1 pages for this site: `/`, `/models`, `/results`, `/pricing`, top `/learn` articles. Every 60-90 days verify claims, bump
`dateModified`. Fold into the existing handoff cadence or a scheduled reminder; no code beyond T3/T4.

## Suggested order
1. T1 + T2 + T3 (an afternoon; unblocks and fixes the lastmod lie).
2. T4 (schema + real dates) and T8 (as-of anchoring): the actual citation-quality work.
3. T5 after you rule on the competitor-page and copy questions.
4. T6, T7, T9 as audits; T10 as a calendar item.

## Decisions I need from you
1. Training crawlers (`GPTBot`, `ClaudeBot`, `Google-Extended`, `CCBot`): allow, or block while allowing the search bots?
2. Do you want competitor-comparison pages at all?
3. Is `/pricing` live enough to carry `Offer` schema, or should it stay unmarked until PRE_LAUNCH ends?
4. Where do `/learn` articles get generated (the "do not edit manually" header)? I did not locate the generator.
