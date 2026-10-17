import type { Metadata } from 'next'
import { ContactLine, LegalPage } from '@/components/legal/legal-page'
import { SITE_CONFIG } from '@/lib/site-config'

export const metadata: Metadata = { title: 'Privacy Policy' }

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy Policy">
      <h2>1. Who this covers</h2>
      <p>
        This policy describes what personal information beezy.fyi collects and how it is used. beezy.fyi is operated by an
        independent individual creator and sole proprietor, who is the responsible party for this information.
      </p>

      <h2>2. What we collect</h2>
      <ul>
        <li>
          <strong>Authentication (Clerk):</strong> your email address, name or username, and OAuth identifiers. Sign-in,
          sessions, and password handling are managed by Clerk. We never see your password.
        </li>
        <li>
          <strong>Billing (Stripe):</strong> subscription status, Stripe Customer ID, and plan metadata. beezy.fyi never
          receives or stores raw card numbers; Stripe handles payment details under its own privacy policy.
        </li>
        <li>
          <strong>Discord role sync (beezy-bot):</strong> an optional Discord User ID, stored only if you link your account
          from <a href="/dashboard/settings">account settings</a> or the <code>/link</code> command, so subscriber roles can
          be assigned.
        </li>
        <li>
          <strong>Bankroll:</strong> a bankroll figure you enter is kept in your browser&apos;s local storage. If you save it
          from account settings, it is also stored in your Clerk profile metadata.
        </li>
        <li>
          <strong>Browser local storage:</strong> your selected sportsbooks (<code>beezy_my_books</code>) and any bet slip
          you build stay in your browser and are not sent to us.
        </li>
        <li>
          <strong>Request data:</strong> our host (Vercel) receives your IP address and request headers. Region headers
          derived from your IP are read at request time to apply availability rules; we do not store them against your
          account.
        </li>
        <li>
          <strong>Support messages:</strong> anything you include when you email or message us.
        </li>
      </ul>
      <p>
        We do not collect Social Security or government ID numbers, biometric identifiers, or precise geolocation. We do
        not sell your personal information.
      </p>

      <h2>3. How we use it</h2>
      <ul>
        <li>To provide and secure the Service, including gating paid content and syncing Discord roles.</li>
        <li>To process subscriptions and send receipts and renewal notices.</li>
        <li>To answer support requests and send service announcements.</li>
        <li>To prevent fraud and abuse and to comply with the law.</li>
      </ul>

      <h2>4. Service providers</h2>
      <p>
        We share data only with the providers needed to run the Service: Clerk (authentication), Stripe (billing), Vercel
        (hosting), Discord (role sync, if you link), and our cloud infrastructure. We may also disclose information if
        required by law or to protect the Service and its users.
      </p>

      <h2>5. Cookies</h2>
      <p>
        We use cookies that are necessary for sign-in sessions. You can block cookies in your browser, but signing in may
        stop working.
      </p>

      <h2>6. Retention</h2>
      <p>
        We keep account and billing records while your account is active and for a reasonable period afterwards for tax,
        fraud-prevention, and dispute purposes. Locally stored data stays in your browser until you clear it.
      </p>

      <h2>7. Deleting your data</h2>
      <p>
        You can delete your account from your Clerk account settings (the profile menu in the site header). You can also
        ask us to remove your data, including a linked Discord ID, by emailing{' '}
        <a href={`mailto:${SITE_CONFIG.supportEmail}`}>{SITE_CONFIG.supportEmail}</a>. Billing records we are required to
        keep may be retained.
      </p>

      <h2>8. Security</h2>
      <p>
        Traffic is encrypted in transit and access to account data is restricted. No system is completely secure, so we
        cannot guarantee absolute security.
      </p>

      <h2>9. Children</h2>
      <p>The Service is not directed at anyone under 18, and we do not knowingly collect their information.</p>

      <h2>10. Changes</h2>
      <p>We may update this policy and will change the &quot;Last updated&quot; date above when we do.</p>

      <h2>11. Contact</h2>
      <ContactLine />
    </LegalPage>
  )
}
