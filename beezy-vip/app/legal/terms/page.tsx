export default function TermsPage() {
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

      <h1 style={{ fontSize: '28px', fontWeight: 800, marginBottom: '6px' }}>Terms of Service</h1>
      <p style={{ color: '#6b7280', fontSize: '13px', marginBottom: '36px' }}>Last updated: 2026-05-21</p>

      <div className="article-body">

        <h2>1. Acceptance of Terms</h2>
        <p>
          By accessing or using Beezy.VIP (the "Service"), operated by <strong>[Operator Name]</strong>
          ("we," "us," or "our"), you agree to be bound by these Terms of Service ("Terms").
          If you do not agree, do not use the Service. These Terms constitute a binding legal
          agreement between you and <strong>[Operator Name]</strong> under the laws of the
          State of Illinois.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Confirm governing law and operator legal entity before launch. */}
          We reserve the right to modify these Terms at any time. Continued use of the Service
          after changes are posted constitutes your acceptance of the revised Terms. We will
          provide reasonable notice of material changes via email or prominent notice on the site.
        </p>

        <h2>2. Eligibility</h2>
        <p>
          You must be at least 18 years of age to use the Service. By using the Service, you
          represent and warrant that you are 18 or older and have the legal capacity to enter
          into a binding contract. If you are accessing the Service on behalf of a business entity,
          you represent that you have authority to bind that entity to these Terms.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Review state-level age requirements and geo-blocking obligations. */}
          It is your sole responsibility to ensure that your use of the Service complies with
          all applicable laws in your jurisdiction. The Service does not accept subscriptions
          from jurisdictions where such a service is prohibited by law. We use automated
          geo-restriction tools; circumventing them constitutes a material breach of these Terms.
        </p>

        <h2>3. Description of Service</h2>
        <p>
          Beezy.VIP is a <strong>paid subscription content service</strong>. We publish
          machine-learning-generated statistical analyses and model outputs related to Major
          League Baseball ("MLB") games. All content is provided <strong>for entertainment
          and educational purposes only</strong>. The Service is not a sportsbook, sports
          wagering operator, betting exchange, tipster service, or financial advisory service.
          We do not accept wagers, facilitate gambling transactions, or provide legally regulated
          financial advice.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Confirm this characterization survives scrutiny under the Illinois
              Sports Wagering Act (230 ILCS 45/) and any applicable FTC endorsement guidance. */}
          Past model performance is not indicative of future results. Statistical models carry
          inherent uncertainty. No output from the Service should be construed as a guarantee
          of any outcome.
        </p>

        <h2>4. Subscription Plans and Payment</h2>
        <p>
          Access to premium features requires a paid subscription. Subscription fees are
          charged in advance on a recurring basis (monthly or seasonal, depending on the
          selected plan) via our payment processor, Stripe. You authorize us to charge your
          designated payment method for all applicable fees.
        </p>
        <p>
          All fees are stated in U.S. dollars. We reserve the right to change subscription
          pricing upon at least 30 days' written notice to active subscribers. Price changes
          take effect at the start of your next billing cycle following the notice period.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Confirm auto-renewal disclosure satisfies Illinois Automatic
              Contract Renewal Act (815 ILCS 601/) — requires clear disclosure before purchase
              and reminder before renewal. */}
          Subscriptions renew automatically unless cancelled. You may cancel at any time
          through your account settings or by contacting support; cancellation takes effect
          at the end of the current paid period.
        </p>

        <h2>5. Refund Policy</h2>
        <p>
          New subscribers may request a full refund within <strong>7 days</strong> of their
          initial purchase ("money-back guarantee"). Refunds requested after 7 days from the
          initial purchase date are not available except as required by applicable law. Renewal
          charges are non-refundable. See our <a href="/legal/refunds">Refund Policy</a> for
          full details.
        </p>

        <h2>6. Prohibited Conduct</h2>
        <p>You agree not to:</p>
        <ul>
          <li>Resell, redistribute, or sublicense Service content without prior written consent.</li>
          <li>Scrape, crawl, or programmatically extract content from the Service in bulk.</li>
          <li>Use the Service to develop a competing product or service.</li>
          <li>Circumvent geographic restrictions or authentication mechanisms.</li>
          <li>Use the Service in any manner that violates applicable law.</li>
          <li>Impersonate another person or entity, or misrepresent your affiliation.</li>
        </ul>

        <h2>7. Intellectual Property</h2>
        <p>
          All content on the Service—including model outputs, methodology descriptions,
          text, graphics, and code—is owned by or licensed to <strong>[Operator Name]</strong>
          and is protected by U.S. copyright law (17 U.S.C. § 101 et seq.) and applicable
          intellectual property laws. Your subscription grants you a limited, non-exclusive,
          non-transferable license to access and use content for personal, non-commercial
          purposes only.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — If any MLB statistical data is used, confirm compliance with
              MLB Advanced Media data licensing terms. */}
        </p>

        <h2>8. Disclaimers</h2>
        <p>
          THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTIES OF ANY KIND,
          EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO WARRANTIES OF MERCHANTABILITY,
          FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT. WE DO NOT WARRANT THAT THE
          SERVICE WILL BE UNINTERRUPTED, ERROR-FREE, OR FREE OF HARMFUL COMPONENTS.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Illinois does not allow disclaimer of implied warranty for
              consumer goods in some contexts; confirm scope. */}
          MODEL OUTPUTS ARE STATISTICAL ESTIMATES. WE MAKE NO REPRESENTATION THAT ANY
          PREDICTION WILL BE ACCURATE OR PROFITABLE. ANY FINANCIAL DECISIONS YOU MAKE
          BASED ON SERVICE CONTENT ARE MADE AT YOUR OWN RISK.
        </p>

        <h2>9. Limitation of Liability</h2>
        <p>
          TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT SHALL
          <strong> [OPERATOR NAME]</strong>, ITS OFFICERS, DIRECTORS, EMPLOYEES, OR AGENTS
          BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES
          ARISING OUT OF OR RELATED TO YOUR USE OF THE SERVICE, EVEN IF WE HAVE BEEN ADVISED
          OF THE POSSIBILITY OF SUCH DAMAGES. OUR TOTAL CUMULATIVE LIABILITY TO YOU FOR ALL
          CLAIMS ARISING OUT OF THESE TERMS SHALL NOT EXCEED THE AMOUNT YOU PAID US IN THE
          12 MONTHS PRECEDING THE CLAIM.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Illinois consumer fraud statutes (815 ILCS 505/) may limit the
              enforceability of certain liability caps for consumer-facing services. Review. */}
        </p>

        <h2>10. Indemnification</h2>
        <p>
          You agree to indemnify, defend, and hold harmless <strong>[Operator Name]</strong> and
          its affiliates from and against any claims, liabilities, damages, losses, and expenses
          (including reasonable attorneys' fees) arising out of or in any way connected with
          your use of the Service, your violation of these Terms, or your violation of any
          third-party rights.
        </p>

        <h2>11. Governing Law and Dispute Resolution</h2>
        <p>
          These Terms are governed by and construed in accordance with the laws of the State
          of Illinois, without regard to its conflict-of-law provisions. Any dispute arising
          under these Terms shall be resolved exclusively in the state or federal courts located
          in Illinois, and you consent to personal jurisdiction in those courts.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — If you intend to add an arbitration clause, it must comply with
              Illinois consumer protection standards. AAA or JAMS rules recommended. Evaluate
              class action waiver enforceability under Illinois law before adding one. */}
        </p>

        <h2>12. Termination</h2>
        <p>
          We may suspend or terminate your access to the Service at any time for material
          breach of these Terms, with or without notice. Upon termination, your license to use
          the Service ceases immediately. Provisions that by their nature should survive
          termination (including Sections 7–11) will survive.
        </p>

        <h2>13. Entire Agreement</h2>
        <p>
          These Terms, together with our Privacy Policy, Refund Policy, and Responsible
          Gambling Policy, constitute the entire agreement between you and
          <strong> [Operator Name]</strong> with respect to the Service and supersede all
          prior agreements and understandings.
        </p>

        <h2>14. Contact</h2>
        <p>
          {/* {LAWYER_REVIEW} — Add legal mailing address before launch (required for GDPR,
              CAN-SPAM, and Illinois disclosure requirements). */}
          Questions regarding these Terms may be directed to: <strong>[legal@beezy.vip]</strong>
        </p>

      </div>
    </main>
  );
}
