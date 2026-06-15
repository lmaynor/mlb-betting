# NBA prop/game pipeline -- blueprint

The target NBA betting pipeline, refit onto our stack (Flask/Cloud Run, sync,
`mlb_core.storage`, GCS, Secret Manager, SGO-style multi-book), distilled from two
reference implementations and the data assets in hand. This is the plan; sections
marked DEFERRED are not built yet (they need a trained model / live odds).

## Sources distilled

| Source | What we took | Status |
|---|---|---|
| SportsBlaze | nightly outcomes + box backbone | built (`nba/data/`) |
| Kaggle eoinamoore (stats.nba.com) | deep history + advanced stats | built (`nba/data/kaggle_ingest.py`) |
| training parquet (17 seasons) | game-winner training set (target `home_team_won`) | in hand; spike AUC ~0.72 |
| nba-parlay-generator | The Odds API client + projection->prob->edge->parlay design | odds layer ported; rest DEFERRED |
| nba_gambling | confirms Odds API approach + multi-book props schema | reference |
| BetExplorer / OddsPortal | historical ML odds for backtest seed | reference/seed |

## Layers

### 1. Odds (BUILT -- `nba/odds/`)
Ported from nba-parlay-generator `odds_ingestion.py`, refit sync + GCS.
- `theoddsapi.py` -- `OddsApiClient` (requests, credit-header tracking, 401/422/429
  handling). Methods: `get_events`, `get_game_lines` (1 credit/slate),
  `get_event_player_props` (1 credit/event).
- `extract.py` -- `flatten_player_props`, `best_book_props` (multi-book best price,
  like MLB `_best_book_odds_int`), `flatten_game_lines`.
- `snapshot.py` -- fetch -> GCS raw + flattened CSV + `latest.json`. CLI, **no
  scheduler** (in-season + model-gated; free tier 500/mo).
- Edge/Kelly/de-vig: **reuse `mlb_core.odds.utils`** (already has
  `american_to_implied_prob`, `remove_vig`, `kelly_pct`, `devig_two_way`) -- did
  NOT re-port `edge_calculator`.
- **Provider: ParlayAPI** (`nba/odds/parlayapi.py` + `parlay_extract.py` +
  `accumulator.py`). Chosen over The Odds API: 1000 cr/mo free (vs 500), 60 req/sec,
  includes Pinnacle, 32 books. Billing: props 1 cr per (event x market); whole-slate
  game lines 1 cr per market. Key env `PARLAY_API_KEY` <- Secret Manager `parlay-api-key`.
  The Odds API client (`theoddsapi.py`) kept as an alternate.
- **Accumulator** (`nba/odds/accumulator.py`, Job via `deploy/setup_parlay_accumulator.sh`):
  sport-agnostic; snapshots live props/game-lines to `OddsAccum/{sport}/{date}/`.
  This is how prop history is built -- **no historical-props API exists at any tier**
  (confirmed 2026-06-15), so we bank it forward (MLB now; NBA from Oct).
- NOTE: gambling-category APIs are blocked on the office LAN -- live calls only from Cloud Run.

### 2. Features (DEFERRED)
Two feature surfaces:
- **Game model:** the parquet's engineered set (ELO multi-decay, four-factors,
  rest/travel/B2B, rolling off/def/net rating, pace, TS%). Reproduce live from the
  Kaggle/stats.nba.com box data so new games can be scored.
- **Player props:** the nba-parlay-generator projection feature spec (port to
  `nba/features/`):
  `last5/10/20 {points,rebounds,assists,minutes}`, `season_avg_*`,
  `home/away_avg_*`, `opp_{pts,reb,ast}_rank`, `days_rest`, `back_to_back`,
  `games_last_7`, `is_home`, `is_playoff`, `playoff_avg_*`, `cv_*_l10`,
  `trend_{points,assists}`.

### 3. Models
- **Game winner: DROPPED (backtested NOT viable, 2026-06-15).** Baseline HistGBM
  AUC 0.703 < market 0.739; flat-stake backtest on 3,648 games (2022-25) loses
  -6% to -8% (worse than bet-favorite); model+market blend weights the model at
  ~0.06 (no incremental signal -- market already prices the injury/lineup/rest news
  the stat features miss). Do not productionize NBA moneyline. Full writeup:
  `handoffs/handoff_nba_backtest_2026-06-15.md`.
- **Player props: THE path forward (DEFERRED).** LightGBM/NegBin projection (mean) +
  `compute_historical_std` -> P(over line) via **NegBin CDF** (as MLB BATTER_HITS/TB).
  Softer markets than ML; transfers our MLB prop experience. MUST clear its own
  backtest before any live betting. Blocker: need deeper historical prop odds
  (nba_gambling's are ~Jan-Jun 2026 only) -- accumulate via The Odds API, buy, or scrape.

### 4. Edge + sizing (DEFERRED wiring; math READY)
`model_prob` (calibrated) vs de-vigged market -> edge -> fractional Kelly via
`mlb_core.odds.utils`. Apply the same calibration-before-edge + edge-cap discipline
as MLB (see CONTEXT s11).

### 5. Parlay (DEFERRED -- new capability)
Port `parlay_optimizer.py` (leg combination, joint EV, correlation haircut). We
have no MLB analog; this is net-new.

### 6. Backtest (DEFERRED)
Port `backtesting.py` season harness. Validate edge vs historical closing lines
(BetExplorer/OddsPortal seed) BEFORE any live betting -- the real go/no-go gate,
same philosophy as MLB's CLV/200-bet gates.

## Graduation
When a calibrated NBA model beats historical closing lines in backtest AND live
odds flow from Cloud Run, NBA graduates to the full "adding a new system"
checklist (CONTEXT s6): registry entry, runner, settle, monitors, frontend.
Until then NBA stays out of the MLB registry/monitors.

## Licensing note
nba-parlay-generator has no LICENSE file; the user authorized lifting its code
directly (2026-06-14) provided it is refit to our context. nba_gambling /
NbaBetExplorer likewise authorized. SportsBlaze (no-auth) and Kaggle (per dataset
terms) are data sources.
