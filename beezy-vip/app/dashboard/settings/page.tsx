import { currentUser } from '@clerk/nextjs/server'
import { getUserTier }  from '@/lib/auth'
import { BankrollInput } from '@/components/ui/bankroll-input'
import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'Settings — Dashboard' }

export default async function DashboardSettingsPage() {
  const [user, tier] = await Promise.all([
    currentUser().catch(() => null),
    getUserTier(),
  ])

  const tierLabel: Record<string, string> = {
    pro:     'Pro · $79/mo',
    season:  'Season · $499',
    starter: 'Starter · $29/mo',
    public:  'Free',
  }

  return (
    <div className="max-w-lg">
      {/* Account */}
      <div className="border border-[var(--border)] p-5 mb-4">
        <p className="mono text-xs uppercase tracking-widest text-muted mb-4">Account</p>
        <div className="space-y-3">
          <div className="flex justify-between items-center">
            <span className="text-xs text-muted">Email</span>
            <span className="mono text-xs text-text">
              {user?.emailAddresses[0]?.emailAddress ?? '—'}
            </span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-xs text-muted">Plan</span>
            <span className="mono text-xs text-accent font-semibold">
              {tierLabel[tier] ?? 'Free'}
            </span>
          </div>
        </div>
      </div>

      {/* Subscription */}
      <div className="border border-[var(--border)] p-5 mb-4">
        <p className="mono text-xs uppercase tracking-widest text-muted mb-4">Subscription</p>
        <p className="text-xs text-muted mb-4 leading-relaxed">
          Manage billing, cancel, or change your plan via the Stripe customer portal.
        </p>
        <a
          href="/api/stripe/portal"
          className="mono text-xs uppercase tracking-widest px-4 py-2.5 border border-[var(--border-bright)] text-text hover:border-accent hover:text-accent transition-colors inline-block"
        >
          Manage Billing →
        </a>
      </div>

      {/* Bankroll setting */}
      <div className="border border-[var(--border)] p-5 mb-4">
        <p className="mono text-xs uppercase tracking-widest text-muted mb-4">Bankroll</p>
        <p className="text-xs text-muted mb-4">
          Set your bankroll to get personalised Kelly stake amounts in dollars on the picks tab.
        </p>
        <BankrollInput defaultValue={(user?.publicMetadata?.bankroll as number) ?? 1000} />
      </div>

      {/* Notifications */}
      <div className="border border-[var(--border)] p-5">
        <p className="mono text-xs uppercase tracking-widest text-muted mb-4">Notifications</p>
        <p className="text-xs text-muted">
          Picks are delivered via Discord. Join the Pro channel for push notifications.
        </p>
        <a
          href="https://discord.gg/beezy"
          target="_blank"
          rel="noopener noreferrer"
          className="mono text-xs uppercase tracking-widest text-accent hover:underline inline-block mt-3"
        >
          Open Discord →
        </a>
      </div>
    </div>
  )
}
