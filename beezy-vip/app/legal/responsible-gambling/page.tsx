export default function ResponsibleGamblingPage() {
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

      <h1 style={{ fontSize: '28px', fontWeight: 800, marginBottom: '6px' }}>Responsible Gambling</h1>
      <p style={{ color: '#6b7280', fontSize: '13px', marginBottom: '36px' }}>Last updated: 2026-05-21</p>

      <div className="article-body">

        {/* Crisis resources — front and prominent */}
        <div style={{
          background: '#fef2f2',
          border: '1px solid #fca5a5',
          borderRadius: '6px',
          padding: '16px 20px',
          marginBottom: '28px',
        }}>
          <p style={{ fontWeight: 700, marginBottom: '8px', color: '#991b1b' }}>
            If you or someone you know has a gambling problem, help is available 24/7:
          </p>
          <ul style={{ margin: 0, paddingLeft: '20px', color: '#7f1d1d' }}>
            <li>
              <strong>National Problem Gambling Helpline:</strong>{' '}
              <a href="tel:18004264700">1-800-GAMBLER (1-800-426-2537)</a> — call or text
            </li>
            <li>
              <strong>Illinois Council on Problem Gambling:</strong>{' '}
              <a href="https://www.icpg.org" target="_blank" rel="noopener noreferrer">icpg.org</a>
              {' '}| <a href="tel:18006330025">1-800-GAMBLER</a>
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
              <strong>Crisis Text Line:</strong> Text HOME to <strong>741741</strong>
            </li>
          </ul>
        </div>

        <h2>1. Our Service Is Not a Sportsbook</h2>
        <p>
          Beezy.VIP is a <strong>subscription content service</strong>. We publish
          machine-learning-generated statistical analyses of MLB games for entertainment and
          educational purposes. We do not accept wagers, facilitate gambling transactions, or
          provide legally regulated financial or betting advice. Any decision to place a wager
          with a licensed sportsbook is entirely your own, made independently of this Service.
        </p>
        <p>
          {/* {LAWYER_REVIEW} — Verify this characterization is consistent with the Illinois
              Sports Wagering Act (230 ILCS 45/) and any FTC guidance on sports pick services.
              If the Service is later deemed a "sports pick service" under any state law, separate
              licensing obligations may apply. */}
        </p>

        <h2>2. Gambling Involves Risk</h2>
        <p>
          Sports wagering is a form of gambling. All gambling involves risk of financial loss.
          Statistical models—including ours—cannot predict the future. No pick, analysis, or
          model output on this Service should be treated as a guarantee of any outcome, and
          past model performance does not guarantee future results.
        </p>
        <p>
          Never gamble with money you cannot afford to lose. Never use borrowed funds, rent,
          bill payments, savings, or money earmarked for necessities to place wagers.
        </p>

        <h2>3. Signs of Problem Gambling</h2>
        <p>
          Problem gambling can affect anyone. Warning signs include:
        </p>
        <ul>
          <li>Spending more time or money gambling than intended.</li>
          <li>Chasing losses — increasing bets to recover previous losses.</li>
          <li>Gambling interfering with work, school, relationships, or daily responsibilities.</li>
          <li>Hiding gambling activity from family or friends.</li>
          <li>Feeling restless or irritable when attempting to stop gambling.</li>
          <li>Borrowing money or selling possessions to fund gambling.</li>
          <li>Repeated unsuccessful efforts to cut back or stop gambling.</li>
        </ul>
        <p>
          If any of these signs apply to you, we encourage you to seek help immediately
          using the resources listed at the top of this page.
        </p>

        <h2>4. Tools Available Through Licensed Sportsbooks</h2>
        <p>
          Licensed sports wagering operators in Illinois are required under the Illinois Sports
          Wagering Act (230 ILCS 45/) to provide responsible gambling tools. If you use a
          licensed sportsbook, these tools are available to you:
        </p>
        <ul>
          <li><strong>Deposit limits:</strong> set daily, weekly, or monthly deposit caps.</li>
          <li><strong>Wager limits:</strong> limit the size of individual bets.</li>
          <li><strong>Loss limits:</strong> cap total losses over a defined period.</li>
          <li><strong>Cool-off periods:</strong> temporary suspension of your account.</li>
          <li><strong>Self-exclusion:</strong> permanent or long-term exclusion from a platform.</li>
        </ul>
        <p>
          Illinois also operates a statewide <strong>Self-Exclusion Program</strong> through the
          Illinois Gaming Board, which allows individuals to exclude themselves from all
          licensed gambling facilities and sports wagering platforms in Illinois. Information is
          available at{' '}
          <a href="https://www.igb.illinois.gov" target="_blank" rel="noopener noreferrer">igb.illinois.gov</a>.
        </p>

        <h2>5. Beezy.VIP Commitments</h2>
        <p>
          Although we are not a licensed gambling operator, we voluntarily commit to the
          following:
        </p>
        <ul>
          <li>We do not market to individuals who have self-excluded from gambling platforms.</li>
          <li>We do not market the Service as a guaranteed path to profit.</li>
          <li>We will cancel the subscription of any user who informs us they are a problem
            gambler and requests cancellation, with a pro-rated refund of any prepaid unused
            subscription period.</li>
          <li>We prominently display responsible gambling resources on this page and in our
            onboarding communications.</li>
        </ul>
        <p>
          {/* {LAWYER_REVIEW} — Confirm that the voluntary commitments above are accurate,
              achievable operationally, and do not create unintended legal obligations. */}
        </p>

        <h2>6. Age Restrictions</h2>
        <p>
          You must be 18 or older to use this Service. Sports wagering in Illinois is legal
          only for individuals 21 years of age or older. If you are between 18 and 20, you may
          not legally wager in Illinois and should not use any content on this Service to
          inform wagering decisions.
        </p>

        <h2>7. Contact</h2>
        <p>
          To report a responsible gambling concern or request account cancellation for
          problem gambling reasons, contact us at <strong>[legal@beezy.vip]</strong>.
        </p>

      </div>
    </main>
  );
}
