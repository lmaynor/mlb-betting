import type { Metadata } from 'next'
import { ContactLine, LegalPage } from '@/components/legal/legal-page'
import { SITE_CONFIG } from '@/lib/site-config'

export const metadata: Metadata = { title: 'Terms of Service' }

export default function TermsPage() {
  return (
    <LegalPage title="Terms of Service">
      <h2>1. Who operates beezy.fyi</h2>
      <p>
        beezy.fyi (&quot;beezy.fyi,&quot; &quot;Beezy,&quot; &quot;we,&quot; &quot;us,&quot; or &quot;our&quot;) is operated by an
        independent individual creator and sole proprietor. It is not a registered limited liability company,
        corporation, sportsbook, or financial institution. By accessing or using beezy.fyi, its API, or its Discord
        server (together, the &quot;Service&quot;) you agree to these Terms. If you do not agree, do not use the Service.
      </p>
      <p>
        We may revise these Terms at any time by posting the revised version with a new &quot;Last updated&quot; date.
        Continued use after a change is acceptance of the revised Terms.
      </p>

      <h2>2. Eligibility</h2>
      <p>
        You must be at least 18 years old, and you must be old enough to wager legally where you are (21 or older in most
        U.S. jurisdictions). You are solely responsible for knowing and following the laws that apply to you. Some content
        is unavailable in regions where local rules restrict it, and attempting to circumvent those restrictions is a
        breach of these Terms.
      </p>

      <h2>3. Informational and educational purpose only</h2>
      <p>
        beezy.fyi publishes statistical model projections, market-consensus odds comparisons, and hypothetical
        paper-tracked performance records (1/4 Kelly and flat-stake simulations). All of it is for informational and
        educational purposes. We do not book, accept, hold, or transmit bets or wagers of any kind, and we are not a
        tipster, broker, or exchange.
      </p>
      <p>
        Paper-tracked results are simulations. They do not reflect real stakes, fills, limits, or line movement, and past
        model or paper-ledger performance does not guarantee future results.
      </p>

      <h2>4. No financial, investment, tax, or wagering advice</h2>
      <p>
        Nothing on beezy.fyi, in our API, or in our Discord server is financial, investment, legal, tax, or wagering
        advice. Model outputs are statistical estimates and may be wrong.
      </p>

      <h2>5. Assumption of risk</h2>
      <p>
        Sports wagering and prediction-market trading involve a high degree of risk, including the loss of everything you
        stake. You are solely responsible for every decision you make at a third-party sportsbook or exchange, including
        DraftKings, FanDuel, BetMGM, Caesars, Kalshi, and Novig. The operator of beezy.fyi has no liability for wagering
        losses, account limits or restrictions, missed lines, or delayed or stale odds data.
      </p>

      <h2>6. Subscriptions and billing</h2>
      <p>
        Paid plans (Single-Sport Pass and All-Access Pro) are billed in advance on a recurring basis through Stripe, in U.S.
        dollars. We will give at least 30 days&apos; notice of a price change to active subscribers, effective at your next
        billing cycle. Subscriptions renew automatically until cancelled.
      </p>
      <p>
        You can cancel at any time from <a href="/dashboard/settings">account settings</a> (Stripe customer portal) or by
        emailing <a href={`mailto:${SITE_CONFIG.supportEmail}`}>{SITE_CONFIG.supportEmail}</a>. Access continues through the
        end of the current paid period. See the <a href="/legal/refunds">Refund Policy</a>.
      </p>

      <h2>7. Acceptable use</h2>
      <p>You agree not to:</p>
      <ul>
        <li>Resell, redistribute, or sublicense Service content without written consent.</li>
        <li>Scrape, crawl, or bulk-extract content from the Service.</li>
        <li>Share a paid login or Discord role with other people.</li>
        <li>Use the Service to build a competing product.</li>
        <li>Circumvent geographic restrictions or authentication.</li>
        <li>Use the Service in violation of applicable law.</li>
      </ul>

      <h2>8. Intellectual property</h2>
      <p>
        The Service&apos;s models, methodology descriptions, text, graphics, and code are owned by or licensed to the
        operator. Your subscription grants a limited, non-exclusive, non-transferable license to use the content for
        personal, non-commercial purposes. Third-party team names, logos, and data remain the property of their owners and
        are used for identification only.
      </p>

      <h2>9. Disclaimer of warranties</h2>
      <p>
        THE SERVICE IS PROVIDED &quot;AS IS&quot; AND &quot;AS AVAILABLE,&quot; WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED,
        INCLUDING MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT, TO THE MAXIMUM EXTENT PERMITTED BY
        LAW. WE DO NOT WARRANT THAT THE SERVICE OR ITS DATA WILL BE UNINTERRUPTED, TIMELY, ACCURATE, OR ERROR-FREE.
      </p>

      <h2>10. Limitation of liability</h2>
      <p>
        TO THE MAXIMUM EXTENT PERMITTED BY LAW, THE OPERATOR OF BEEZY.FYI IS NOT LIABLE FOR INDIRECT, INCIDENTAL, SPECIAL,
        CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR FOR LOST WAGERS OR PROFITS. THE OPERATOR&apos;S TOTAL CUMULATIVE LIABILITY FOR ANY
        CLAIM ARISING OUT OF OR RELATING TO THE SERVICE IS CAPPED AT THE GREATER OF (A) $0.00 FOR FREE-TIER USERS, OR (B) THE
        SUBSCRIPTION FEES YOU ACTUALLY PAID TO BEEZY.FYI IN THE THIRTY (30) DAYS BEFORE THE EVENT GIVING RISE TO THE CLAIM.
        Some jurisdictions do not allow certain limitations, so parts of this section may not apply to you.
      </p>

      <h2>11. Indemnification</h2>
      <p>
        You agree to indemnify and hold harmless the operator of beezy.fyi from claims, losses, and expenses (including
        reasonable attorneys&apos; fees) arising from your use of the Service, your breach of these Terms, or your violation of
        any law or third-party right.
      </p>

      <h2>12. Governing law and venue</h2>
      <p>
        These Terms are governed by the laws of the {SITE_CONFIG.governingState}, without regard to conflict-of-law rules.
        Disputes will be brought exclusively in the state or federal courts located in {SITE_CONFIG.governingVenue}, and you
        consent to personal jurisdiction there.
      </p>

      <h2>13. Termination</h2>
      <p>
        We may suspend or end your access for breach of these Terms. Sections that by their nature should survive
        termination (including 3 through 5 and 8 through 12) will survive.
      </p>

      <h2>14. Entire agreement</h2>
      <p>
        These Terms, with the <a href="/legal/privacy">Privacy Policy</a>, <a href="/legal/refunds">Refund Policy</a>, and{' '}
        <a href="/legal/responsible-gambling">Responsible Gambling</a> page, are the entire agreement between you and the
        operator regarding the Service.
      </p>

      <h2>15. Contact</h2>
      <p>Send notices and support requests through any of these routes.</p>
      <ContactLine />
    </LegalPage>
  )
}
