/** Explains the three numbers on every projection board: Model edge, Price EV and the Beezy Score. Collapsed by default. */
export function EdgeEvLegend() {
  return (
    <details style={{ marginTop: '14px', border: '1px solid #1e2230', borderRadius: '8px', background: '#11131a', padding: '10px 14px' }}>
      <summary style={{ cursor: 'pointer', fontSize: '12.5px', fontWeight: 600, color: 'var(--silver)' }}>Reading Model edge, Price EV and Score</summary>
      <div style={{ fontSize: '12.5px', color: 'var(--silver)', lineHeight: 1.65, marginTop: '10px', display: 'grid', gap: '8px', maxWidth: '88ch' }}>
        <p style={{ margin: 0 }}>
          <strong>Model edge</strong> compares two opinions: the model&rsquo;s probability for the listed side minus the market&rsquo;s fair
          probability (the books&rsquo; consensus with the vig removed). A high edge means the model disagrees with the market. It does not
          mean you make money.
        </p>
        <p style={{ margin: 0 }}>
          <strong>Price EV</strong> asks a different question: if the market is right, does the best price on the board pay more than a fair
          price? It uses the market&rsquo;s probability, not the model&rsquo;s. Negative means that book charges more than the market&rsquo;s
          fair price (you would be paying the vig); positive means it offers a better number than consensus.
        </p>
        <p style={{ margin: 0 }}>
          A large Model edge beside a negative Price EV reads: <em>only the model likes this</em>. No book is mispriced against the market, and
          the whole case rests on the model being right. Both positive is MODEL + MARKET: the model and the prices agree.
        </p>
        <p style={{ margin: 0 }}>
          <strong>Score</strong> (0-100) blends the edge with how many books back the fair price and how likely the side is to hit. When only
          one of the two edges is positive it counts half of that edge, so STRONG needs both. On the NFL, NHL and NBA boards an edge above 15%
          is treated as a likely model error and scores 0.
        </p>
        <p style={{ margin: 0 }}>
          <strong>Market fair</strong> is that consensus probability, its American price and how many books it rests on. <strong>Form</strong> is
          the share of recent games that landed on the listed side of the line, or the player&rsquo;s own average when there is no line.
        </p>
      </div>
    </details>
  )
}
