import Link from 'next/link'
import { SGP_ARTIFACT_EV_PCT, type OddsScreenMode } from '@/lib/odds-screen'

export function OddsFootnote({ mode }: { mode: OddsScreenMode }) {
  return (
    <p className="times text-text-secondary text-compact mt-6 max-w-[90ch] leading-[1.6]">
      {mode === 'ev' && <>Each cell is a book&apos;s price on the row&apos;s side above its price on the other side; green marks the best bettable price and a tint marks a book
        beating the fair price by 1.5% or more. FV is the fair price from the reference books you pick, averaged with equal weight after each book&apos;s vig is removed;
        a book is never measured against its own line. EV is fair probability times payout minus one at the best price.</>}
      {mode === 'outliers' && <>Market Outliers visualizes single-book price dislocations where one book hangs odds far above the rest of the market.
        MARKET AVG is the leave-self-out consensus across peer books. The green bar shows the outlier book&apos;s payout relative to peer books.
        Verified +EV requires at least two reference books or the player&apos;s multi-line curve confirming positive expected value.</>}
      {mode === 'middles' && <>A middle is the over of a lower line plus the under of a higher line: if the result lands strictly between them both bets win,
        anywhere else one wins and one loses, so it is a small known cost for a chance at a double win, not a risk-free bet. Stakes equalise the payout when only one leg wins.
        BREAK-EVEN is the chance the window must hit for the middle to pay for itself; P(WINDOW) is the reference books&apos; own estimate from the two lines (or the player&apos;s
        curve), so it is only as good as those prices. Each cell shows the over of the lower line above the under of the higher line; green outlines mark the two legs.
        Kalshi&apos;s taker fee is taken off a Kalshi leg. Confirm both lines before placing either, and mind each book&apos;s limits and grading rules.</>}
      {mode === 'arb' && <>Each cell is a book&apos;s over price above its under price; the green outline marks the two legs of the best hedge. Stakes lock the same payout on
        either outcome. Kalshi&apos;s taker fee (0.07 &times; p &times; (1 &minus; p) per contract) is taken off its leg; other exchange commissions are not modelled.
        Prop arbs are often a stale quote or a mismatched line: confirm both legs before placing either, and mind each book&apos;s limits and grading rules.</>}
      {mode === 'longshots' && <>Alt &amp; Tail Ladders groups every book&apos;s rungs for one player and stat into a single card. A VERIFIED DISLOCATION badge requires at least four books quoting the exact rung two-sided, tight agreement between them, fresh quotes, and a net edge after the rung&apos;s own vig above the market&apos;s noise floor (+2.5% yards, +5.0% counts). Cards under the four-book floor show prices only. One-sided milestones never show a fair value. </>}
      {mode === 'sgp' && <>SGP Proxies models same-book two-leg tickets from the single-leg prices. The modelled SGP price runs the book&apos;s own two prices through an assumed copula and hold; the model fair price runs the other books&apos; de-vigged prices through an assumed correlation (&rho;). Every constant is a guess and none is validated, and the modelled price is not a price the book quoted: confirm any combination in the book&apos;s own builder. Rows above {SGP_ARTIFACT_EV_PCT}% model EV are flagged as likely artifacts and hidden unless you include them. The books pricing a ticket are left out of its fair value.</>}
      {' '}These are unvalidated market signals, not part of the public track record. Lines move or limit quickly: check the price at the book. Not financial advice.
      21+. If gambling is a problem, call 1-800-GAMBLER.{' '}
      <Link href="/legal/responsible-gambling" style={{ color: 'var(--link)' }}>Responsible gambling</Link>
    </p>
  )
}
