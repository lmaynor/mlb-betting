export const revalidate = 300

import Link from 'next/link'
import type { Metadata } from 'next'
import { PLANS } from '@/lib/plans'
import { getLedgerSummary } from '@/lib/ledger-summary'
import { formatDateKey } from '@/lib/dates'

export const metadata: Metadata = {
  title: 'beezy vs. OddsJam & BettingPros: Pricing, Projections & Public Ledger ($0–$39/mo)',
  description:
    'Compare beezy ($0–$39/mo) against OddsJam and BettingPros on pricing, proprietary model projections, live +EV line shopping, and a public paper-tracked results ledger.',
  alternates: { canonical: '/compare/oddsjam-alternative' },
}

const th = { textAlign: 'left' as const, padding: '8px 10px', fontSize: '12px', color: 'var(--color-text-secondary)', borderBottom: '1px solid var(--color-separator-border)', position: 'sticky' as const, top: 0, background: 'var(--color-background-primary-default)' }
const td = { padding: '8px 10px', fontSize: '13px', color: '#d1d5db', borderBottom: '1px solid var(--color-separator-border)', verticalAlign: 'top' as const }

const ROWS: { tool: string; price: string; what: string; href?: string }[] = [
  { tool: 'beezy.fyi', price: `$${PLANS.single.price}/mo one league, $${PLANS.monthly.price}/mo all four, $${PLANS.annual.price}/yr`, what: 'Market-consensus +EV scanner plus projection models; public paper-tracked ledger', href: '/pricing' },
  { tool: 'OddsJam', price: 'Positive EV Global $399.99/mo shown ($499 struck through); Sharp Money $6.60/day; Platinum $13.60 to $20/day', what: 'Odds comparison and +EV / arbitrage scanning across 100+ books; results are tracked by the user', href: 'https://oddsjam.com/subscribe' },
  { tool: 'Props.cash', price: '$19.99/mo or $199.99/yr, all sports', what: 'Prop research built around hit rates and historical trends', href: 'https://props.cash/' },
  { tool: 'Outlier', price: 'Free tier; Premium $19.99/mo; Premium+ $29.99/mo; Pro $79.99/mo', what: 'Prop insights, odds comparison and line movement; EV+ indicators and arbitrage feed on higher tiers', href: 'https://www.outlier.bet/#plans' },
  { tool: 'Unabated', price: 'Props+ $99/mo, Premium $199/mo, Concierge $799/mo', what: 'Market-implied line and fair-price tools aimed at sharp bettors', href: 'https://tools.unabated.com/pricing' },
  { tool: 'Pikkit', price: 'Free core app; Pro $29.99/mo or $199.99/yr', what: 'Bet tracking, line shopping and closing line value; it does not generate picks', href: 'https://pikkit.com/pro' },
  { tool: 'Betstamp', price: 'Free app; PRO Main $249/mo ($149/mo billed annually); Props and Live tiers contact sales', what: 'Odds comparison, bet tracking with ROI and CLV, and a pro odds screen with edge detection', href: 'https://www.betstamp.com/pro' },
]

export default async function CompareOddsJamAlternativePage() {
  const summary = await getLedgerSummary().catch(() => null)

  const startDateLong = summary?.startDate
    ? formatDateKey(summary.startDate.slice(0, 10), { month: 'long', day: 'numeric', year: 'numeric' })
    : null
  const endDateLong = summary?.endDate
    ? formatDateKey(summary.endDate.slice(0, 10), { month: 'long', day: 'numeric', year: 'numeric' })
    : null

  const roiStr = summary?.roi != null
    ? `${summary.roi >= 0 ? '+' : ''}${summary.roi.toFixed(2)}%`
    : null

  const clvStr = summary?.avgClv != null
    ? `${summary.avgClv >= 0 ? '+' : ''}${summary.avgClv.toFixed(2)}%`
    : null

  const gradedLeaguesStr = summary?.gradedLeagues?.length
    ? summary.gradedLeagues.join(', ')
    : 'MLB'

  const unsettledLeaguesStr = summary?.unsettledLeagues?.length
    ? (summary.unsettledLeagues.length === 3
        ? `${summary.unsettledLeagues[0]}, ${summary.unsettledLeagues[1]} and ${summary.unsettledLeagues[2]}`
        : summary.unsettledLeagues.join(', '))
    : 'NFL, NBA and NHL'

  const stakingBasis = summary?.stakingBasis || 'paper stakes as recorded'

  const beezyLedgerCell = summary ? (
    <>
      Yes &mdash; {summary.totalGraded.toLocaleString()} paper-graded plays ({roiStr ? `${roiStr} ` : ''}{stakingBasis} ROI{startDateLong && endDateLong ? `, ${startDateLong}–${endDateLong}` : ''}), wins and losses published on <Link href="/results">/results</Link> ({gradedLeaguesStr} settled; {unsettledLeaguesStr} not yet in public results)
    </>
  ) : (
    <>
      Yes &mdash; paper-graded plays, wins and losses published on <Link href="/results">/results</Link> ({gradedLeaguesStr} settled; {unsettledLeaguesStr} not yet in public results)
    </>
  )

  const beezyClvCell = clvStr
    ? `Publishes ${clvStr} average CLV on graded plays; CLV data exports on $299/yr plan (no personal sportsbook-sync tracker)`
    : 'Publishes average CLV on graded plays; CLV data exports on $299/yr plan (no personal sportsbook-sync tracker)'

  const comparisonRows = [
    {
      feature: 'Paid Pricing',
      beezy: '$19/mo single-sport; $39/mo all-access; $299/yr annual',
      oddsjam: '$199.99–$499.99 per month; monthly or yearly billing, yearly saves 15%',
      bettingpros: 'Plus $29.99/mo month-to-month or $119.99/yr ($9.99/mo); Elite $49.99/mo or $199.99/yr ($16.67/mo)',
    },
    {
      feature: 'Free Tier',
      beezy: 'Yes — $0/mo Explorer',
      oddsjam: 'No permanent free tier — 7-day free trial on all plans',
      bettingpros: 'Yes — free account and app',
    },
    {
      feature: 'Projection & Pricing Method',
      beezy: 'Proprietary quantitative models (minutes, usage, injury redistribution) + live +EV odds scanner',
      oddsjam: 'Market-based odds scanning across 40+ books: arbitrage, positive EV, middles, promo conversion',
      bettingpros: 'Consensus projections and editorial coverage',
    },
    {
      feature: 'Live +EV Scanner',
      beezy: (
        <>
          Yes (<Link href="/ev">/ev</Link> and <Link href="/ev/projections">/ev/projections</Link>)
        </>
      ),
      oddsjam: 'Yes — Positive EV screen (Platinum plan; also a standalone app subscription)',
      bettingpros: 'Market EV ratings included on Plus/Elite; not a live +EV odds scanner',
    },
    {
      feature: 'Public Graded Model Ledger',
      beezy: beezyLedgerCell,
      oddsjam: 'Not published',
      bettingpros: 'Expert pick records published; no auto-graded model ledger',
    },
    {
      feature: 'Closing Line Value (CLV)',
      beezy: beezyClvCell,
      oddsjam: "Yes — shows CLV (per-bet CLV in its Bet Tracker, tracked against the sharpest book's closing line)",
      bettingpros: 'Not published',
    },
    {
      feature: 'Methodology Transparency',
      beezy: (
        <>
          Walk-forward validation, out-of-sample figures, and negative-ROI systems disclosed on <Link href="/models">/models</Link>
        </>
      ),
      oddsjam: 'Not published',
      bettingpros: "1–5 star Bet Ratings based on their model's edge vs the line; consensus projections",
    },
    {
      feature: 'Platform',
      beezy: 'Web app (no iOS/Android app)',
      oddsjam: 'Web, iOS, Android',
      bettingpros: 'Web, iOS, Android',
    },
    {
      feature: 'Best Fit For',
      beezy: 'Bettors wanting proprietary projections, +EV scanning, and a public paper ledger at $19–$39/mo',
      oddsjam: 'High-volume bettors wanting arbitrage and +EV scanning across many books',
      bettingpros: 'Bettors wanting consensus projections and broad editorial coverage',
    },
  ]

  const bettingProsLedgerP = summary ? (
    <p>
      Public, bounded track record: Every flagged beezy play is timestamped before game time and paper-graded automatically on <Link href="/results">/results</Link> ({summary.totalGraded.toLocaleString()} graded plays, {roiStr ? `${roiStr} ` : ''}{stakingBasis} ROI{startDateLong && endDateLong ? ` from ${startDateLong} through ${endDateLong}` : ''}, wins and losses alike, including negative-ROI systems). We also state plainly what is not yet graded publicly: {gradedLeaguesStr} systems settle in public results, while {unsettledLeaguesStr} projections are currently in research or preview (details on <Link href="/models">/models</Link>).
    </p>
  ) : (
    <p>
      Public, bounded track record: Every flagged beezy play is timestamped before game time and paper-graded automatically on <Link href="/results">/results</Link> (paper-graded plays, wins and losses alike, including negative-ROI systems). We also state plainly what is not yet graded publicly: {gradedLeaguesStr} systems settle in public results, while {unsettledLeaguesStr} projections are currently in research or preview (details on <Link href="/models">/models</Link>).
    </p>
  )

  return (
    <div className="max-w-[860px] mx-auto pt-8 px-5 pb-12">
      <h1 className="text-[24px] font-bold text-text-primary" style={{ margin: '0 0 16px' }}>beezy.fyi vs OddsJam and other sports betting tools</h1>
      <div className="article-body">
        <p>
          Sports betting tools fall into three groups: odds scanners, prop research tools and bet trackers. beezy.fyi is
          a scanner that also publishes its own projections and a public results ledger. Competitor prices below link to each vendor&apos;s
          page and were read from those pages on October 24, 2026. Some vendors price per day or toggle between monthly
          and annual billing, and prices change, so confirm current pricing with the vendor.
        </p>
        <p>
          beezy pricing at a glance: $0/mo Explorer, $19/mo single-sport, $39/mo all-access, or $299/yr annual (includes Closing Line Value data exports). Below is a direct comparison of beezy against OddsJam and BettingPros on pricing, projection method, +EV scanning, and public grading. Competitor details last verified November 23, 2026.
        </p>
        <h2>Price and Feature Comparison Table</h2>
      </div>
      <div className="overflow-x-auto border border-separator-border rounded-control bg-background-primary-default" style={{ margin: '16px 0 8px' }}>
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th scope="col" style={th}>Feature</th>
              <th scope="col" style={th}>beezy (beezy.fyi)</th>
              <th scope="col" style={th}>OddsJam</th>
              <th scope="col" style={th}>BettingPros</th>
            </tr>
          </thead>
          <tbody>
            {comparisonRows.map(r => (
              <tr key={r.feature}>
                <th scope="row" style={{ ...td, fontWeight: 600, color: 'var(--color-text-primary)' }}>{r.feature}</th>
                <td style={td}>{r.beezy}</td>
                <td style={td}>{r.oddsjam}</td>
                <td style={td}>{r.bettingpros}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[12px] text-text-secondary" style={{ margin: '0 0 24px' }}>
        beezy performance figures are paper-tracked model results, not real-money fills. Competitor pricing and features verified November 23, 2026.
      </p>
      <div className="overflow-x-auto border border-separator-border rounded-control bg-background-primary-default" style={{ margin: '16px 0 24px' }}>
        <table className="w-full border-collapse">
          <thead><tr><th style={th}>Tool</th><th style={th}>Price</th><th style={th}>What it is</th></tr></thead>
          <tbody>
            {ROWS.map(r => (
              <tr key={r.tool}>
                <td style={{ ...td, fontWeight: 600 }}>{r.href ? <a href={r.href}>{r.tool}</a> : r.tool}</td>
                <td style={td}>{r.price}</td><td style={td}>{r.what}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="article-body">
        <h2>beezy.fyi vs OddsJam</h2>
        <p>
          Both flag prices that lag the market. beezy.fyi adds its own player projections and checks Kalshi and Novig
          alongside sportsbooks. Its flagged plays are logged to the public <a href="/results">results ledger</a> as paper
          bets, so the record can be checked before you rely on it.
        </p>
        <h2>beezy.fyi vs Props.cash and Outlier</h2>
        <p>
          Those tools center on trends, hit rates and odds comparison. beezy.fyi projects tonight&apos;s outcome from
          minutes, usage and official injury reports instead of recent hit rate.
        </p>
        <h2>beezy.fyi vs Unabated</h2>
        <p>
          Unabated is built for bettors who want to construct their own numbers. beezy.fyi delivers a filtered board of
          +EV prices and projections.
        </p>
        <h2>beezy.fyi vs Pikkit and Betstamp</h2>
        <p>
          Pikkit and Betstamp track bets, ROI and closing line value, and Betstamp also offers a pro odds screen. beezy.fyi differs
          by publishing its own projections and a public ledger of every flagged play.
        </p>
        <h2>beezy vs. BettingPros for Player Prop Projections</h2>
        <p>
          Bettors comparing alternatives to BettingPros for player prop projections are usually choosing between two different approaches: consensus projections with editorial coverage versus proprietary quantitative models tied to a public ledger.
        </p>
        <p>
          Own model vs. consensus projections: BettingPros centers on consensus projections and editorial analysis. beezy builds its own quantitative models&mdash;incorporating minutes, usage, and injury redistribution&mdash;and pairs those outputs with a live positive Expected Value (+EV) scanner to surface where sportsbook lines diverge from model and fair prices.
        </p>
        {bettingProsLedgerP}
        <p>
          Pricing: beezy costs $0/mo (Explorer), $19/mo (single-sport), $39/mo (all-access), or $299/yr (annual, including CLV data exports), compared to BettingPros at Plus $29.99/mo month-to-month or $119.99/yr ($9.99/mo); Elite $49.99/mo or $199.99/yr ($16.67/mo).
        </p>
        <p>
          When BettingPros is the better fit: If you want a mobile app, large-scale daily editorial articles, or consensus expert rankings across every sport, BettingPros fits that workflow better. beezy is web-only and built for bettors who want proprietary model outputs, +EV pricing, and auditable paper grading.
        </p>
        <p>
          <Link href="/results">Inspect the Public Ledger (/results) &rarr;</Link> &middot; <Link href="/pricing">Compare Plans (/pricing) &rarr;</Link>
        </p>
      </div>
    </div>
  )
}
