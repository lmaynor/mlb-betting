export default function PrivacyPage() {
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

      <h1 style={{ fontSize: '28px', fontWeight: 800, marginBottom: '6px' }}>Privacy Policy</h1>
      <p style={{ color: '#6b7280', fontSize: '13px', marginBottom: '36px' }}>Last updated: 2026-05-21</p>

      <div className="article-body">

        <h2>1. Scope and Controller Identity</h2>
        <p>
          This Privacy Policy describes how <strong>[Operator Name]</strong> ("we," "us," or "our"),
          the operator of Beezy.VIP, collects, uses, and discloses personal information when you
          use the Service. For purposes of applicable privacy law, <strong>[Operator Name]</strong>
          is the data controller.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Confirm whether Illinois BIPA (740 ILCS 14/) or the Illinois
              Personal Information Protection Act (815 ILCS 530/) impose additional obligations
              beyond what is stated here. BIPA is triggered by biometric identifiers — confirm
              the Service collects none. */}
        </p>

        <h2>2. Information We Collect</h2>

        <h3>2a. Information You Provide</h3>
        <ul>
          <li><strong>Account registration:</strong> email address, username, password (hashed).</li>
          <li><strong>Payment:</strong> billing name, billing address. We do <em>not</em> store
            full payment card numbers — all payment data is handled directly by Stripe, Inc.
            under Stripe's own privacy policy and PCI DSS compliance program.</li>
          <li><strong>Support communications:</strong> any information you include in emails or
            support requests.</li>
        </ul>

        <h3>2b. Information Collected Automatically</h3>
        <ul>
          <li><strong>Usage data:</strong> pages visited, features used, timestamps, referring URL.</li>
          <li><strong>Device and browser data:</strong> IP address, browser type, operating system,
            device identifiers.</li>
          <li><strong>Cookies and similar technologies:</strong> session cookies required for
            authentication; analytics cookies (see Section 5).</li>
        </ul>

        <h3>2c. Information We Do Not Collect</h3>
        <p>
          We do not collect Social Security numbers, government-issued ID numbers, biometric
          identifiers, precise geolocation, or health information.
        </p>

        <h2>3. How We Use Personal Information</h2>
        <p>We use personal information to:</p>
        <ul>
          <li>Provide, maintain, and improve the Service.</li>
          <li>Process payments and subscriptions.</li>
          <li>Send transactional communications (account confirmations, receipts, renewal notices).</li>
          <li>Send service-related announcements (e.g., material changes to Terms or pricing).</li>
          <li>Enforce geographic restrictions required by applicable law.</li>
          <li>Detect and prevent fraud, abuse, and security incidents.</li>
          <li>Comply with legal obligations.</li>
        </ul>
        <p>
          {/* {LAWYER_REVIEW} — If you intend to send marketing emails, confirm CAN-SPAM Act
              (15 U.S.C. § 7701) compliance: clear identification, opt-out mechanism, physical
              postal address required. Illinois does not impose separate email marketing law
              beyond federal CAN-SPAM as of this writing. */}
          We will not sell your personal information to third parties.
        </p>

        <h2>4. Legal Basis for Processing</h2>
        <p>
          {/* {LAWYER_REVIEW} — Full GDPR/CCPA analysis required if EU or California residents
              are accepted as subscribers. As of this draft the Service targets U.S. users but
              this section should be completed before any international marketing. */}
          We process personal information on the following bases: (a) performance of a contract
          (your subscription agreement with us); (b) compliance with legal obligations; and
          (c) our legitimate interests in operating and improving the Service, where those
          interests are not overridden by your rights.
        </p>

        <h2>5. Cookies</h2>
        <p>
          We use strictly necessary session cookies to authenticate logged-in users. We may
          use analytics cookies (e.g., Vercel Analytics, Google Analytics) to understand
          aggregate usage patterns. Analytics cookies are not required for the Service to
          function. You may disable cookies in your browser settings; doing so may impair
          authentication functionality.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Verify whether a cookie consent banner is required under
              applicable law for your user base. Illinois does not currently have a comprehensive
              consumer privacy statute imposing cookie consent requirements, but this may change. */}
        </p>

        <h2>6. Disclosure of Personal Information</h2>
        <p>We share personal information only in the following circumstances:</p>
        <ul>
          <li><strong>Service providers:</strong> Stripe (payment processing), Vercel (hosting
            and analytics), Clerk (authentication — if enabled), and similar vendors who
            process data on our behalf under written data processing agreements.</li>
          <li><strong>Legal process:</strong> in response to a valid subpoena, court order,
            or other legal demand, or to protect the rights, property, or safety of
            [Operator Name], our users, or the public.</li>
          <li><strong>Business transfers:</strong> in connection with a merger, acquisition,
            or sale of all or substantially all of our assets, provided the successor agrees
            to honor this Privacy Policy.</li>
          <li><strong>With your consent.</strong></li>
        </ul>

        <h2>7. Data Retention</h2>
        <p>
          We retain account data for as long as your subscription is active and for a reasonable
          period thereafter for legal, tax, and dispute-resolution purposes (generally no more
          than 3 years post-cancellation, unless a longer period is required by law). Aggregate,
          de-identified analytics data may be retained indefinitely.
        </p>

        <h2>8. Security</h2>
        <p>
          We implement industry-standard technical and organizational measures to protect
          personal information, including TLS encryption in transit, hashed password storage,
          and access controls. No system is completely secure; we cannot guarantee absolute
          security of your information.
        </p>

        <h2>9. Children</h2>
        <p>
          The Service is not directed at children under 18. We do not knowingly collect
          personal information from anyone under 18. If we learn we have collected such
          information, we will delete it promptly.
        </p>

        <h2>10. Your Rights (Illinois and U.S. Federal)</h2>
        <p>
          Under the Illinois Personal Information Protection Act (815 ILCS 530/) and applicable
          federal law, you have the right to:
        </p>
        <ul>
          <li>Request access to personal information we hold about you.</li>
          <li>Request correction of inaccurate personal information.</li>
          <li>Request deletion of your account and associated personal information, subject to
            legal retention obligations.</li>
          <li>Opt out of marketing communications at any time via the unsubscribe link in any
            marketing email or by contacting us directly.</li>
        </ul>
        <p>
          To exercise any of these rights, contact us at <strong>[legal@beezy.vip]</strong>.
          We will respond within a reasonable timeframe consistent with applicable law.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — If California residents subscribe, a CCPA/CPRA "Notice at
              Collection," "Do Not Sell or Share My Personal Information" link, and a separate
              California Privacy Notice are required. Add before accepting California subscribers. */}
        </p>

        <h2>11. Data Breach Notification</h2>
        <p>
          In the event of a security breach affecting your personal information, we will
          notify affected users in accordance with the Illinois Personal Information Protection
          Act (815 ILCS 530/10), which requires notification in the most expedient time possible
          and without unreasonable delay.
        </p>

        <h2>12. Third-Party Links</h2>
        <p>
          The Service may contain links to third-party websites. We are not responsible for the
          privacy practices of those sites and encourage you to review their privacy policies.
        </p>

        <h2>13. Changes to This Policy</h2>
        <p>
          We may update this Privacy Policy from time to time. We will notify you of material
          changes via email or prominent notice on the Service at least 14 days before the
          changes take effect.
        </p>

        <h2>14. Contact</h2>
        <p>
          {/* {LAWYER_REVIEW} — A physical mailing address is required under CAN-SPAM and
              is best practice for any privacy policy. Add before launch. */}
          Privacy inquiries: <strong>[legal@beezy.vip]</strong>
        </p>

      </div>
    </main>
  );
}
