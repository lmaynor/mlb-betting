import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:  'Refund Policy',
  robots: { index: false, follow: false },
}

// LEGAL REVIEW REQUIRED -- all content below is placeholder pending attorney review.

export default function RefundsPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-16">
      {/* LEGAL REVIEW REQUIRED */}
      <div className="border border-yellow-500/40 bg-yellow-500/5 px-4 py-3 mb-10 mono text-xs text-yellow-400">
        PLACEHOLDER -- This page is pending legal review. Replace before charging real money.
      </div>

      <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">Refund Policy</h1>
      <p className="mono text-xs text-muted mb-12">Last updated: {new Date().getFullYear()} -- Placeholder</p>

      <div className="space-y-10 text-sm text-muted leading-relaxed">

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">Monthly Subscriptions</h2>
          {/* LEGAL REVIEW REQUIRED -- confirm refund window */}
          <p>
            Monthly subscriptions may be cancelled at any time. Cancellation takes effect at
            the end of the current billing period -- you retain access through the period you
            paid for. We do not issue prorated refunds for partial months.
          </p>
          <p className="mt-3">
            [PLACEHOLDER -- decide on a money-back window. Common options: 7-day, no questions
            asked, or no refunds. Confirm with attorney.]
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">Season Pass</h2>
          <p>
            The Season Pass is a one-time purchase for the 2026 MLB season. It is non-refundable
            after 7 days from purchase, except as required by applicable law.
            [PLACEHOLDER -- confirm with attorney.]
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">Service Outages</h2>
          <p>
            If the Service is unavailable for an extended period due to our error, we will
            extend your subscription by an equivalent period. We do not issue monetary refunds
            for planned maintenance or brief outages.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">Stripe Disputes</h2>
          <p>
            If you believe you were charged in error, contact us before initiating a dispute
            with your bank. Chargebacks result in immediate account suspension. We will work
            with you to resolve billing issues directly.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">How to Cancel</h2>
          <p>
            Cancel anytime from{' '}
            <a href="/dashboard/settings" className="text-accent hover:underline">
              Dashboard &rarr; Settings &rarr; Billing
            </a>. You will retain access until the end of your current billing period.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">Contact</h2>
          {/* LEGAL REVIEW REQUIRED -- add contact email */}
          <p>Billing questions: [contact email -- add before launch]</p>
        </section>

      </div>
    </div>
  )
}
