import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:  'Privacy Policy',
  robots: { index: false, follow: false },
}

// LEGAL REVIEW REQUIRED -- all content below is placeholder pending attorney review.

export default function PrivacyPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-16">
      {/* LEGAL REVIEW REQUIRED */}
      <div className="border border-yellow-500/40 bg-yellow-500/5 px-4 py-3 mb-10 mono text-xs text-yellow-400">
        PLACEHOLDER -- This page is pending legal review. Replace before charging real money.
      </div>

      <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">Privacy Policy</h1>
      <p className="mono text-xs text-muted mb-12">Last updated: {new Date().getFullYear()} -- Placeholder</p>

      <div className="space-y-10 text-sm text-muted leading-relaxed">

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">1. What We Collect</h2>
          <p>We collect the following categories of information:</p>
          <ul className="mt-3 space-y-2 ml-4">
            <li><strong className="text-text">Account data</strong> -- email address, name, and authentication data managed by Clerk.</li>
            <li><strong className="text-text">Payment data</strong> -- billing information managed by Stripe. We do not store card numbers.</li>
            <li><strong className="text-text">Usage data</strong> -- pages visited, features used, and interaction patterns.</li>
            <li><strong className="text-text">Preferences</strong> -- bankroll settings and notification preferences stored in your account.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">2. How We Use Your Data</h2>
          <ul className="space-y-2 ml-4">
            <li>To provide and maintain the Service</li>
            <li>To process subscription payments via Stripe</li>
            <li>To send transactional emails (receipts, account changes)</li>
            <li>To improve the Service through aggregate analytics</li>
          </ul>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">3. Third-Party Services</h2>
          <p>We use the following third parties that may process your data:</p>
          <ul className="mt-3 space-y-2 ml-4">
            <li><strong className="text-text">Clerk</strong> -- authentication and identity management.</li>
            <li><strong className="text-text">Stripe</strong> -- payment processing. Subject to <a href="https://stripe.com/privacy" className="text-accent hover:underline" target="_blank" rel="noopener noreferrer">Stripe&apos;s Privacy Policy</a>.</li>
            <li><strong className="text-text">Vercel</strong> -- hosting and edge infrastructure.</li>
            <li><strong className="text-text">Google Cloud</strong> -- model computation infrastructure (does not process personal data directly).</li>
          </ul>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">4. We Do Not Sell Your Data</h2>
          <p>
            We do not sell, rent, or trade your personal information to third parties for their
            marketing purposes.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">5. Cookies</h2>
          {/* LEGAL REVIEW REQUIRED -- enumerate cookies */}
          <p>
            We use cookies for authentication (Clerk session) and analytics. [PLACEHOLDER --
            enumerate specific cookies and add consent mechanism if required by jurisdiction.]
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">6. California Residents (CCPA)</h2>
          {/* LEGAL REVIEW REQUIRED -- CCPA compliance */}
          <p>
            California residents have the right to know what personal information we collect,
            request deletion of your data, and opt out of sale (we do not sell data).
            To exercise these rights, contact us at [contact email -- add before launch].
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">7. Data Retention</h2>
          <p>
            We retain account data for as long as your account is active. Payment records are
            retained as required by law. You may request deletion of your account at any time.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">8. Contact</h2>
          {/* LEGAL REVIEW REQUIRED -- add DPO contact if required */}
          <p>Privacy questions: [contact email -- add before launch]</p>
        </section>

      </div>
    </div>
  )
}
