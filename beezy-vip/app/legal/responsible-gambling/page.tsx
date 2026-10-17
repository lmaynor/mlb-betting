import type { Metadata } from 'next'
import { ContactLine, LegalPage } from '@/components/legal/legal-page'

export const metadata: Metadata = { title: 'Responsible Gambling' }

export default function ResponsibleGamblingPage() {
  return (
    <LegalPage title="Responsible Gambling">
      <div style={{ background: '#11131a', border: '1px solid #1e2230', borderLeft: '3px solid #f87171', borderRadius: '8px', padding: '16px 20px', marginBottom: '28px' }}>
        <p style={{ fontWeight: 700, margin: '0 0 8px' }}>If you or someone you know has a gambling problem, help is available 24/7:</p>
        <ul style={{ margin: 0, paddingLeft: '20px' }}>
          <li>
            <strong>National Problem Gambling Helpline:</strong> <a href="tel:18004262537">1-800-GAMBLER (1-800-426-2537)</a>, call or text
          </li>
          <li>
            <strong>National Council on Problem Gambling:</strong>{' '}
            <a href="https://www.ncpgambling.org" target="_blank" rel="noopener noreferrer">ncpgambling.org</a>
          </li>
          <li>
            <strong>Gamblers Anonymous:</strong>{' '}
            <a href="https://www.gamblersanonymous.org" target="_blank" rel="noopener noreferrer">gamblersanonymous.org</a>
          </li>
          <li>
            <strong>Illinois Self-Exclusion (Illinois Gaming Board):</strong>{' '}
            <a href="https://www.igb.illinois.gov" target="_blank" rel="noopener noreferrer">igb.illinois.gov</a>
          </li>
          <li>
            <strong>Crisis Text Line:</strong> text HOME to 741741
          </li>
        </ul>
      </div>

      <h2>1. beezy.fyi is not a sportsbook</h2>
      <p>
        beezy.fyi publishes statistical analysis for informational and educational purposes. We never accept, hold, or
        facilitate wagers. Any wager you place is your own decision, made independently with a third party.
      </p>

      <h2>2. Age requirement</h2>
      <p>
        You must be 21 or older to wager in most U.S. jurisdictions (18 or older where local law permits). Do not use any
        content on this site to inform wagering if you are under the legal age where you are.
      </p>

      <h2>3. Gambling involves risk</h2>
      <p>
        Every wager can lose. Models, including ours, cannot predict the future, and paper-tracked results are simulations
        that do not guarantee future performance.
      </p>

      <h2>4. Bankroll management</h2>
      <ul>
        <li>Only stake money you can afford to lose. Never use rent, bills, savings, or borrowed money.</li>
        <li>Set a budget and a time limit before you start, and stop when you reach either.</li>
        <li>Size every stake as a small fraction of your bankroll. The Kelly calculators on this site are a sizing aid, not a guarantee; fractional Kelly is safer than full Kelly.</li>
        <li>Never chase losses by raising stakes.</li>
      </ul>

      <h2>5. Warning signs</h2>
      <ul>
        <li>Spending more time or money than you intended.</li>
        <li>Chasing losses.</li>
        <li>Gambling that affects work, school, or relationships.</li>
        <li>Hiding your activity or borrowing to fund it.</li>
        <li>Feeling restless or irritable when you try to stop.</li>
      </ul>
      <p>If any of these sound familiar, use the resources at the top of this page.</p>

      <h2>6. Tools at licensed sportsbooks</h2>
      <p>
        Licensed operators offer deposit limits, wager limits, loss limits, cool-off periods, and self-exclusion. State
        programs, such as the Illinois Gaming Board self-exclusion program, can exclude you from all licensed operators in
        the state.
      </p>

      <h2>7. Our commitments</h2>
      <ul>
        <li>We do not market the Service as a guaranteed path to profit.</li>
        <li>We will cancel a subscription immediately, with a refund of any prepaid period not yet started, if you tell us you are a problem gambler.</li>
      </ul>

      <h2>8. Contact</h2>
      <ContactLine />
    </LegalPage>
  )
}
