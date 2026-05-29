export default function RefundsPage() {
  return (
    <main style={{ maxWidth: '760px', margin: '0 auto', padding: '40px 20px' }}>

      {/* Legal review banner */}
      <div style={{
        background: '#1c1207',
        color: '#f59e0b',
        fontFamily: 'monospace',
        fontSize: '11px',
        letterSpacing: '0.1em',
        padding: '10px 14px',
        marginBottom: '28px',
        borderRadius: '4px',
      }}>
        ⚠ THIS DOCUMENT IS CURRENTLY IN LEGAL REVIEW AND MAY BE REVISED. USE IS PROVISIONAL.
      </div>

      <div style={{ marginBottom: '8px' }}>
        <span style={{
          fontSize: '11px',
          fontWeight: 700,
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
          color: '#6b7280',
          background: '#f3f4f6',
          padding: '3px 10px',
          borderRadius: '99px',
        }}>LEGAL</span>
      </div>

      <h1 style={{ fontSize: '28px', fontWeight: 800, marginBottom: '6px' }}>Refund Policy</h1>
      <p style={{ color: '#6b7280', fontSize: '13px', marginBottom: '36px' }}>Last updated: 2026-05-21</p>

      <div className="article-body">

        <h2>1. 7-Day Money-Back Guarantee</h2>
        <p>
          New subscribers may request a full refund of their initial subscription payment
          within <strong>7 calendar days</strong> of the original purchase date
          ("Guarantee Period"). No reason is required. To request a refund under this
          guarantee, contact us at <strong>[support@beezy.fyi]</strong> before the
          Guarantee Period expires. Refunds are issued to the original payment method
          within 5–10 business days, subject to your card issuer's processing time.
        </p>
        <p>
          The 7-day money-back guarantee applies to the initial subscription charge only.
          It does not apply to:
        </p>
        <ul>
          <li>Renewal charges for any subscription period.</li>
          <li>Accounts where the guarantee was previously applied in a prior subscription term.</li>
          <li>Accounts terminated for violation of our Terms of Service.</li>
        </ul>

        <h2>2. Renewals</h2>
        <p>
          Subscription renewals are non-refundable. If you wish to avoid a renewal charge,
          you must cancel your subscription before the renewal date. You can cancel at any
          time through your account settings or by contacting support; cancellation prevents
          future billing and takes effect at the end of the current paid period. You retain
          access to the Service through the end of the period for which you have paid.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Illinois Automatic Contract Renewal Act (815 ILCS 601/) requires
              clear and conspicuous disclosure of automatic renewal terms before purchase and a
              reminder notice before the renewal. Confirm your checkout and email flows satisfy
              this. Failure to comply can render the renewal charge void and entitle the subscriber
              to a full refund of the renewal amount. */}
        </p>

        <h2>3. Service Outages and Disruptions</h2>
        <p>
          If the Service experiences a material, extended outage (generally defined as
          unavailability of core subscription features for more than 72 consecutive hours
          within a billing period through no fault of the subscriber), we will, at our sole
          discretion, offer a pro-rated credit toward a future billing period. We do not
          guarantee uptime and are not obligated to issue refunds for brief interruptions,
          scheduled maintenance, or outages caused by third-party infrastructure providers.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Confirm that the outage credit standard is reasonable and does
              not conflict with any implied service warranty under Illinois law. */}
        </p>

        <h2>4. Problem Gambling Cancellations</h2>
        <p>
          As stated in our <a href="/legal/responsible-gambling">Responsible Gambling Policy</a>,
          any user who notifies us they are a problem gambler and requests cancellation will
          receive a pro-rated refund of any prepaid unused subscription period, calculated
          from the date the request is received.
        </p>

        <h2>5. Chargebacks</h2>
        <p>
          Filing a chargeback with your payment provider without first contacting us to
          resolve the issue constitutes a breach of these Terms. We reserve the right to
          permanently suspend accounts associated with fraudulent chargebacks. If you have
          a billing dispute, please contact us first at <strong>[support@beezy.fyi]</strong>
          and we will work to resolve it promptly.
        </p>

        <h2>6. Statutory Rights</h2>
        <p>
          Nothing in this Refund Policy limits any statutory rights you may have under
          applicable law. Illinois residents retain all rights afforded by the Illinois
          Consumer Fraud and Deceptive Business Practices Act (815 ILCS 505/) and other
          applicable statutes.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Confirm whether Illinois law affords any mandatory refund
              rights for digital subscription services that must be disclosed here. Review
              FTC's Negative Option Rule (16 C.F.R. Part 425) as updated, which imposes
              clear disclosure and cancellation requirements for subscription services. */}
        </p>

        <h2>7. How to Request a Refund</h2>
        <p>
          Email <strong>[support@beezy.fyi]</strong> with the subject line
          "Refund Request" and include:
        </p>
        <ul>
          <li>The email address associated with your account.</li>
          <li>The date of the charge you are disputing.</li>
          <li>A brief description of your request.</li>
        </ul>
        <p>
          We will acknowledge your request within 2 business days and process eligible
          refunds within 5 business days of acknowledgment.
        </p>

      </div>
    </main>
  );
}
