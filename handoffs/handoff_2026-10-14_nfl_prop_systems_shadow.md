# Handoff -- 2026-10-14 -- NFL data -> features -> prop models (shadow), branch feat/nfl-prop-systems

Request: "leverage blitzcast + sports-quant for NFL data pipelines and prop systems". Built on the existing nflverse
mirror (PR #36; backfill has run: 129 files in `NFL/`, player_stats 2018-2026).

## What came from the two zips
- **sports-quant: nothing imported.** Its NFL half scrapes PFF Premium (login) and PFR through rotating proxies to get
  past rate limits (ToS/evasion), ships no NFL data, and picks "top 3 of 50 seeds by seasonal accuracy" (selection bias).
- **blitzcast: pattern reference only** (game-level win-prob; its own backtest loses to the closing line). Our nflverse
  mirror already covers its loader role.

## Shipped (all offline-tested; nothing deployed, nothing written to GCS or the bets table)
- `nfl/features/props_features.py`, `nfl/models/{props,walkforward}.py` -- leakage-proof features, 6 stat models, walk-forward CLI.
- `nfl/props/{resolve,grade,lines}.py` -- fail-closed resolver, second grader (snap-aware DNP), banked-lines tables.
- `scripts/nfl_banked_lines.py`, `scripts/nfl_model_vs_market.py`. Tests: 300+ NFL tests (features 165 incl.).

## Results (read these before believing anything)
- Walk-forward 2021-2025: models beat trailing-mean baselines on MAE/deviance/Brier-at-proxy-line for every stat. Weak bar:
  a real book line already contains that information, and closing spread/total are model inputs.
- Banked market (EVSweep): 16 games, 3 ET days, 3,376 props, 69 with >=3 books at open AND close. The sweep was NOT
  running 10-06..10-11 (Thursdays + all of week-5 Sunday lost, unrecoverable). 10-04 payloads are rotating partial views.
- Model vs market on the banked weeks: pooled gap looked significant (-0.012 Brier) but was an artifact (single-book flat
  0.50 prices, alt ladders = 14 QBs x many points). Main line only, >=3 books: -0.007 +/- 0.016 (n=61); any >=3 books -0.005 +/- 0.013 (n=83). **No edge shown.**
- Resolver: 0 wrong players; name+team resolves 100% of 2024-26 offensive player-weeks, name only 94.3%. 531/532 book strings resolve.

## Open items / decisions for the owner
1. **Restart/verify the NFL sweep for full slates** -- the bank is the only way to ever test edge; unbanked days are gone.
   (Cloud Run job + shared ParlayAPI credits; not touched here.)
2. Read the books' and Kalshi's anytime-TD and DNP rules (grader assumes rush+rec TDs; ~6% of Yes outcomes are scope-sensitive).
3. ev/ bugs found, not fixed: `player_touchdown_passes` missing from `ev.props.STAT_KEYS` (5 quotes dropped); Novig's own
   price is inside the consensus (leave-one-out only exists in the executor).
4. Verify `nfl-refresh-data` is actually provisioned (this session only saw the backfill sentinel).
5. Late scratches are invisible to features (inactive at kickoff -> no row); a live scorer must decide who plays itself.
6. Next build, only after (1) has banked several full slates: shadow scorer on live consensus (features support
   `include_upcoming=True`; overwrite market-dependent columns with live lines), then CLV vs close. CLV, not ROI, is the gate.
