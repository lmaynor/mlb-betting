import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:  'Responsible Gambling',
  robots: { index: true, follow: true },
}

export default function ResponsibleGamblingPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-16">
      <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">Responsible Gambling</h1>
      <p className="mono text-xs text-muted mb-12">
        Beezy.VIP is a statistical information service. We care about your wellbeing.
      </p>

      <div className="space-y-10 text-sm text-muted leading-relaxed">

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">Age Requirement</h2>
          <p>
            You must be <strong className="text-text">21 or older</strong> (or the legal age in
            your jurisdiction) to use this service and to place sports wagers. Do not use this
            service if you are underage.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">Gambling Is Not Investing</h2>
          <p>
            Statistical models can identify edges over large sample sizes, but no model wins
            every bet. Never wager money you cannot afford to lose. Sports betting should be
            treated as entertainment, not as a source of income.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">Warning Signs of Problem Gambling</h2>
          <ul className="space-y-2 ml-4">
            <li>Betting more than you intended or can afford</li>
            <li>Chasing losses with larger bets</li>
            <li>Lying to family or friends about gambling activity</li>
            <li>Gambling to escape stress, anxiety, or depression</li>
            <li>Borrowing money to gamble</li>
            <li>Feeling unable to stop or cut back</li>
          </ul>
          <p className="mt-4">
            If any of these describe you, please reach out for help immediately.
          </p>
        </section>

        <section className="border border-accent/30 bg-accent/5 p-6">
          <h2 className="text-accent font-bold uppercase tracking-wide text-xs mb-4">Get Help Now</h2>
          <div className="space-y-4">
            <div>
              <p className="text-text font-semibold mono text-sm">National Problem Gambling Helpline</p>
              <a
                href="tel:18004264700"
                className="mono text-xl font-bold text-accent hover:underline"
              >
                1-800-522-4700
              </a>
              <p className="text-xs mt-1">24/7 -- confidential -- free</p>
            </div>
            <div>
              <p className="text-text font-semibold mono text-sm">Crisis Text Line</p>
              <p className="mono text-sm">Text <strong className="text-text">HOME</strong> to <strong className="text-text">741741</strong></p>
            </div>
            <div>
              <p className="text-text font-semibold mono text-sm">National Council on Problem Gambling</p>
              <a
                href="https://www.ncpgambling.org"
                className="text-accent hover:underline text-xs"
                target="_blank"
                rel="noopener noreferrer"
              >
                ncpgambling.org
              </a>
            </div>
            <div>
              <p className="text-text font-semibold mono text-sm">Gamblers Anonymous</p>
              <a
                href="https://www.gamblersanonymous.org"
                className="text-accent hover:underline text-xs"
                target="_blank"
                rel="noopener noreferrer"
              >
                gamblersanonymous.org
              </a>
            </div>
          </div>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">Self-Exclusion</h2>
          <p>
            Most licensed sportsbooks offer self-exclusion programs. Contact your sportsbook
            directly to enroll. You can also use the National Self-Exclusion Program at{' '}
            <a
              href="https://www.ncpgambling.org/programs-resources/programs/national-self-exclusion-program/"
              className="text-accent hover:underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              ncpgambling.org
            </a>.
          </p>
        </section>

        <section>
          <h2 className="text-text font-bold uppercase tracking-wide text-xs mb-3">Opt Out of This Service</h2>
          <p>
            To cancel your Beezy.VIP subscription and stop receiving picks, visit{' '}
            <a href="/dashboard/settings" className="text-accent hover:underline">
              Dashboard &rarr; Settings &rarr; Billing
            </a>{' '}
            or contact us directly. Cancellation is immediate and no further charges will be made.
          </p>
        </section>

      </div>
    </div>
  )
}
