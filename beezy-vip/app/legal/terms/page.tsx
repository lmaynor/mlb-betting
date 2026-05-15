import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:  'Terms of Service',
  robots: { index: false, follow: false },
}

// LEGAL REVIEW REQUIRED -- all content below is placeholder pending attorney review.

export default function TermsPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-16">
      {/* LEGAL REVIEW REQUIRED */}
      <div className="border border-yellow-500/40 bg-yellow-500/5 px-4 py-3 mb-10 mono text-xs text-yellow-400">
        PLACEHOLDER -- This page is pending legal review. Do not treat this as legal advice or
        a final terms agreement. Replace before charging real money.
      </div>

      <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">Terms of Service</h1>
      <p className="mono text-xs text-muted mb-12">Last updated: {new Date().getFullYear()} -- Placeholder</p>

      <div className="space-y-10 text-sm text-muted leading-relaxed">

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">1. Acceptance of Terms</h2>
          <p>
            By accessing or using Beezy.VIP (&quot;the Service&quot;), you agree to be bound by these Terms
            of Service. If you do not agree, do not use the Service.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">2. Information Service Only</h2>
          <p>
            Beezy.VIP provides statistical analysis and model-based pick recommendations for
            informational purposes only. We are <strong className="text-text">not a licensed sports
            betting advisory service, a bookmaker, or a financial advisor.</strong> Nothing on this site
            constitutes financial advice, legal advice, or a solicitation to place wagers.
          </p>
          <p className="mt-3">
            All model outputs are based on historical data and statistical modeling. Past model
            performance is not indicative of future results. You assume all risk associated with
            any betting decisions you make based on information from this service.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">3. Eligibility</h2>
          <p>
            You must be at least 21 years of age (or the legal gambling age in your jurisdiction,
            whichever is higher) to use this Service. By using the Service, you represent and
            warrant that you meet this age requirement.
          </p>
          <p className="mt-3">
            The Service is intended for use only where access to sports betting information is
            legal. It is your responsibility to determine whether use of this Service is lawful
            in your jurisdiction.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">4. No Guarantees</h2>
          <p>
            The Service is provided &quot;as is&quot; without warranty of any kind. We make no guarantee
            of accuracy, completeness, or fitness for any particular purpose. Models are
            statistical tools, not oracles. All picks are in paper mode during the current
            validation period.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">5. Subscriptions and Billing</h2>
          {/* LEGAL REVIEW REQUIRED -- refund policy, cancellation terms */}
          <p>
            Paid subscriptions are billed according to the plan selected at checkout. You may
            cancel at any time. Cancellation takes effect at the end of the current billing period.
            Refund policy is described in our <a href="/legal/refunds" className="text-accent hover:underline">Refund Policy</a>.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">6. Indemnification</h2>
          {/* LEGAL REVIEW REQUIRED -- indemnification clause */}
          <p>
            You agree to indemnify, defend, and hold harmless Beezy.VIP and its operators from
            and against any claims, liabilities, damages, losses, and expenses arising out of or
            related to your use of the Service or violation of these Terms.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">7. Limitation of Liability</h2>
          <p>
            To the maximum extent permitted by law, Beezy.VIP shall not be liable for any
            indirect, incidental, special, consequential, or punitive damages, including loss of
            profits or gambling losses, arising out of your use of the Service.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">8. Governing Law</h2>
          {/* LEGAL REVIEW REQUIRED -- choose governing state after legal review */}
          <p>
            These Terms shall be governed by the laws of the State of Delaware, without regard
            to conflict of law principles. [PLACEHOLDER -- confirm jurisdiction with attorney.]
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">9. Changes to Terms</h2>
          <p>
            We reserve the right to modify these Terms at any time. Continued use of the Service
            after changes constitutes acceptance of the revised Terms.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">10. Contact</h2>
          {/* LEGAL REVIEW REQUIRED -- add contact email */}
          <p>Questions about these Terms: [contact email -- add before launch]</p>
        </section>

      </div>
    </div>
  )
}
