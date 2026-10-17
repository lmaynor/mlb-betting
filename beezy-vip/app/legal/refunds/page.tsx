import type { Metadata } from 'next'
import { ContactLine, LegalPage } from '@/components/legal/legal-page'
import { SITE_CONFIG } from '@/lib/site-config'

export const metadata: Metadata = { title: 'Refund Policy' }

export default function RefundsPage() {
  return (
    <LegalPage title="Refund Policy">
      <h2>1. Cancel anytime in one click</h2>
      <p>
        Subscribers to the Single-Sport Pass or All-Access Pro can cancel recurring billing at any time from{' '}
        <a href="/dashboard/settings">account settings</a> (Stripe customer portal) or by emailing{' '}
        <a href={`mailto:${SITE_CONFIG.supportEmail}`}>{SITE_CONFIG.supportEmail}</a>. After you cancel, access stays active
        through the end of the current paid billing period and you are not billed again.
      </p>

      <h2>2. No partial-period refunds</h2>
      <p>
        Real-time +EV alerts, model projections, and Discord signals are digital goods consumed as soon as you have access.
        Payments for the current billing cycle are therefore non-refundable once access has been provided, including for
        unused time after you cancel, except for a verified duplicate or erroneous charge, which we will refund in full.
      </p>

      <h2>3. Problem gambling</h2>
      <p>
        If you tell us you are struggling with gambling and ask us to cancel, we will cancel immediately and refund any
        prepaid period that has not yet started. See <a href="/legal/responsible-gambling">Responsible Gambling</a>.
      </p>

      <h2>4. Billing disputes</h2>
      <p>
        If something looks wrong on a charge, contact us first so we can fix it quickly. Disputing a valid charge with your
        card issuer without contacting us may result in your access being suspended.
      </p>

      <h2>5. Your statutory rights</h2>
      <p>Nothing in this policy limits rights you have under the law that applies to you.</p>

      <h2>6. How to ask</h2>
      <p>
        Include the email on your account, the charge date, and a short description. We aim to reply within two business
        days.
      </p>
      <ContactLine />
    </LegalPage>
  )
}
