# NHL game engine vs the closing line, on 2021-2026 BettingPros game lines (2026-11-05)

Branch `feat/nhl-bp-game-line-backfill`. Backfill WRITTEN to GCS (owner-authorised); nothing deployed, no scheduler or Cloud Run Job created, no
bets-table writes. Everything modelled here is UNVALIDATED research output.

## Verdict
**The NHL game engine loses to the closing line on all three markets, in every season, and shows no blend weight above zero.** Pooled over
7,400-7,900 games per market (2020-21 to 2026-27, scored as the engine is deployed):

| market | n | log-loss gain vs close (z) | blend weight c on (model - market) (z) | ECE model / market | edge buckets |
|---|---|---|---|---|---|
| moneyline (home wins) | 7,866 | -0.0288 (-9.3) | +0.058 (+1.2) | 0.083 / 0.011 | gap rho +0.30, not strictly rising |
| puck line (home covers) | 7,862 | -0.0310 (-10.5) | +0.037 (+0.9) | 0.054 / 0.012 | gap rho -0.50 |
| total (over, shootout excluded) | 7,426 | -0.0199 (-8.3) | +0.004 (+0.1) | 0.076 / 0.012 | gap rho 0.00 |

Gain = market log loss minus model log loss, positive = model better, standard errors clustered by game day (1,300+ days). All 21 season x
market cells are negative: 20 of them between -0.014 and -0.040 (z -1.1 to -4.7), and the 242-game 2026-27 moneyline stub at -0.0006 (z -0.06).
Restricting to the five full seasons 2021-22 to 2025-26 gives moneyline -0.0284 (z -8.6, n 6,740), puck line -0.0315 (z -10.0, n 6,739), total
-0.0178 (z -6.9, n 6,394). The pooled blend weight c is within 1.3 se of zero on every market (0.02 to 0.06, z 0.1 to 1.2), so there is no sign
the engine knows something the line does not; the engine is also badly over-confident (ECE 5-8 points against the market's 1). The line-shopping
control (bet both sides of everything) pays -4.7% / -4.3% / -4.6% at the median price and -0.4% / -1.9% / -2.4% at the best price, so any best-price
ROI quoted for these buckets must be read against that: the model's moneyline 8%+ bucket (4,176 games) returns +1.0% at the best price against
-0.4% for betting both sides blind across all games, a gap of about one standard error.

Robustness (same verdict each time, gains -0.020 to -0.030, z -8.0 to -10.4): replaying the Coyotes as Utah (`--ari-as-uta`); only quotes last
changed within 6 h of the drop (`--fresh-hours 6`, median 6 books); >= 5 books and Fliff dropped (`--min-books 5 --exclude-book-ids 39`).

### What the opening line adds (a lead, not an edge)
BettingPros stores the opening quote, so open-to-close can be examined, with the caveats below.
- The model also loses to the OPENING line (gain -0.023 / -0.028 / -0.020, z -7.1 / -8.6 / -6.8), and by the same or slightly more to the close
  on those events (-0.028 / -0.030 / -0.020).
- Taking the opening price (opening book, identical line, flat 1u, realised): the model's side returns -2.6% (moneyline), -1.6% (puck line),
  -3.5% (total); betting both sides blind at the same prices returns -4.8% / -3.8% / -4.5%. Model minus blind: +2.2% (z +2.0), +2.3% (z +1.9),
  +1.0% (z +0.8).
- Open-to-close price CLV against the SAME book's close on the same line: model side +0.66% / +0.90% / +0.67%, blind control -0.22% /
  +0.39% / +0.11%; model minus control +0.88% / +0.51% / +0.56% (z +10.7 / +6.6 / +7.8). The lean predicts which way a posted price moves
  by close, which is a real statistical regularity here, but it does not become money: the realised ROI is still negative, no ROI-vs-blind
  difference exceeds 2.0 se (the total's is 0.8), and the entry is one book's first post, stamped before the market had traded, at a price and
  limit nobody has shown can be taken. Read it as "the engine's lean is weakly aligned with where the market is going, not with where the market
  ends", which is the shape of an over-confident model that shares the market's priors. Not an adoption signal.

## What was built
- `mlb_core/odds/bettingpros_games.py`: the game-line crawler and reader (see "Backfill"). Generic over sport (NHL configured; NBA slugs listed,
  its data already banked by `nba/games/bp_games.py` on `feat/nba-game-models`, same schema).
- `nhl/game/market_eval.py`: reads the banked quotes, builds the closing consensus, joins to the NHL schedule, replays the engine day by day,
  and scores it with `mlb_core/eval/market_scoring.py` (date-clustered paired log loss, Brier skill, ECE, blend c, edge buckets, shopping
  control) plus per-season tables, same-book CLV, realised open-price ROI, vs-open scoring and a validation section.
- `tests/test_nhl_market_eval.py` (15 offline tests, including the leakage test below); `tests/test_game_market_eval.py`'s NHL test updated
  (the module no longer refuses without events).
- `docs/solutions/integration-issues/bettingpros-nhl-game-lines-history.md`.

## Backfill
- **Provider:** BettingPros public JSON API (the key and client the repo already uses). Game markets: `moneyline` 193, `puck-line` 195,
  `total` 194 (category `game-odds`, period `game`; ids resolved from the live catalog). `/offers` returns quotes **from 2021-01** (probed
  2019-11, 2020-02, 2020-03: events but zero offers; 2020-08: no events). So the usable span is **2021-01-13 (2020-21) to 2026-11-04**; the
  owner's 2021-22 target is fully covered and 2020-21 comes free.
- **Written:** `gs://concrete-crow-445205-m4-mlb-data/Odds/bettingpros_nhl_games/{moneyline,spread,total}/{YYYY-MM-DD}.parquet`
  (`spread` = the puck line; same naming and schema as `Odds/bettingpros_nba_games`). 1,590 partitions per market (2021-01-01 .. 2026-11-04,
  Oct-Jun plus 1-8 July 2021; 251 of the crawled days had no games and hold a 0-row "nothing there" marker), 543,371 rows, 54 MB.
- **Shape:** long format, one row per event x market x side x book x quoted line, plus the selection's opening line as `is_open` rows.
  A partition holds each book's LAST quote (BettingPros keeps one snapshot per book per event) with its last-change stamp, so it is a
  closing-line PROXY, not a time series.
- **Resumable / idempotent / polite:** a (market, date) partition that exists is skipped, so a re-run costs only the catalog call (verified
  against the bucket: 0 dates re-crawled). Newest first; 4 requests per day; jittered sleeps (0.8 s between pages, 0.8 s between markets,
  1.5 s between days; ~6 s a day, 2 h 40 min for the whole history); today is never written; a circuit breaker stops the crawl on 401 / 403 /
  CAPTCHA-looking responses (exit 3) or 5 failed days in a row, and never retries around a block. This run: 1,585 days, 0 errors, never blocked.
  The first launch was stopped after 4 days and relaunched because `storage.write_parquet` builds a GCS client per object (~8 s each); the
  crawler now reuses one client.
- `python -m mlb_core.odds.bettingpros_games --sport NHL --start 2021-01-01` (needs `GCS_BUCKET`); run it again to top up new days.

## Validation (counts are over the whole span; full tables in the saved report)
- **Rows / events:** 543,371 rows read, 543,349 after de-duplication (22 duplicates: an event sitting under two adjacent date partitions, the
  later `updated` wins); 0 duplicate quote keys afterwards; 8,296 events (REG 7,661, playoffs `PST` 514, preseason `PRE` 121 which are never
  scored). 27 (event, market, side, book) combinations carry more than one quoted line (alt lines); only `is_main` rows are used.
- **Coverage vs the NHL schedule (regular season + playoffs):** 2020-21 941 of 952 games, 2021-22 1,401 of 1,401, 2022-23 1,400 of 1,400,
  2023-24 1,399 of 1,400, 2024-25 1,398 of 1,398, 2025-26 1,394 of 1,394, 2026-27 242 of 250 (the rest are the last 1-2 days). Events with
  >= 3 books on the modal line, moneyline / puck line / total: 2021-22 1,319 / 1,317 / 1,377, 2022-23 1,317 / 1,317 / 1,399, 2023-24 1,315 /
  1,315 / 1,397, 2024-25 1,397 / 1,398 / 1,397, 2025-26 1,392 / 1,392 / 1,390. Median 8 books on the line for ML and puck line, 7 for totals
  (min 3, max 12).
- **The Arizona gap (provider data hole):** in 2020-21 to 2023-24 BettingPros' offers for Coyotes games carry ONE team selection (the
  opponent's); the Arizona side is missing, so all ~300 ARI games have no moneyline or puck-line pair (totals are fine). That is the whole
  moneyline / puck-line shortfall above. It overlaps the engine's own ARI gap (below), so scored n is unchanged by it.
- **Per-book coverage** (share of matched events with a pre-drop close quote) is in the saved report. The books present from the start are
  DraftKings, FanDuel, Caesars, BetMGM, BetRivers, SugarHouse, PartyCasino (mostly 95-100%, with holes: BetMGM moneyline 44% in 2022-23, Caesars
  total 1% in 2021-22, Caesars 72-73% in 2024-25, DraftKings/FanDuel puck line 56% / 65% in 2021-22); Fanatics is patchy (56-67% in 2022-24,
  absent 2024-25), bet365 and theScore Bet start in 2023-24, Hard Rock in 2024-25, Fliff in 2025-26. Pinnacle never quotes here. Dropping
  Fliff and requiring 5 books does not move any result.
- **Tricodes:** 33 BettingPros codes seen; `LA / NJ / SJ / TB` renamed to `LAK / NJD / SJS / TBL` (the schedule's), `ARI` / `UTA` split by date
  (`nhl.props.history.canon_team`); 0 codes outside the schedule after mapping; 8,175 of 8,175 scored-type events matched to a schedule game
  (the match is on (home, away) and the nearest start, 0 schedule games matched twice). BettingPros `scheduled` equals the schedule's
  `start_utc` to the minute (median 0, p1 0, p99 0, max 30 min on a rescheduled start), so the join could be exact.
- **No future leakage in "close":** a real-book quote is a close only if its last-change stamp is no later than 10 minutes after the scheduled
  puck drop (maximum used: +10.0 min); 336 of 405,481 real-book quotes (0.08%) were later than that, in-play re-posts, and were dropped, never
  used. Opening quotes stamped after the drop (10 of 48,482) are not treated as opens. Both stamps are UTC. NHL last-change stamps are old:
  median 42 minutes before the drop, 73% within 6 h, 7.1% more than a day before (a quiet line keeps its stamp); the `--fresh-hours 6`
  run shows this does not matter for the verdict. Consensus / DFS / exchange books are excluded (`book_kind == "book"` only).
- **Replay leakage test** (`test_replay_day_t_predictions_do_not_depend_on_results_on_or_after_t`): scrambling goals, shots and goalie lines on
  and after day t leaves every prediction on days < t AND on day t bit-identical, while later days do change. The replay predicts a whole
  game day before any of that day's results reach the ratings (the old per-game evaluator updated between same-day games).

## How the market and outcomes are defined
- Market per game = the median across books (same main line, both sides) of each book's proportionally de-vigged probability; modal line
  (puck line: smallest |line|; total: nearest the median); >= 3 books; median and best decimal prices from the same books.
- Outcomes: moneyline = final score including OT / shootout; puck line = final-score margin (a shootout win is a 1-goal margin, so it never
  covers -1.5); total = goals EXCLUDING the shootout (`home_goals_ex_so + away_goals_ex_so`); integer-line totals that land on the line are
  dropped as pushes (the engine's `total()` is push-adjusted the same way).
- Engine replay as deployed: every game in the team box master (preseason included) feeds the Kalman ratings and team profiles; goalie GSAx
  from the goalie master strictly before the game; unconfirmed starters (no lineup feed historically); the engine prices the market's own
  puck line and total, not a fixed -1.5 / 6.0.

## A bug in the deployed engine found on the way (NOT fixed here)
`ARI` is not in `nhl.config.TEAMS`, so `KalmanTeamTracker.predict_game` raises `Unknown team code` for any Coyotes game (2019-2024-04) and
`update_game` silently skips it. The daily runner would have failed on a Coyotes slate; for history, ~82 games a season of league-wide rating
information are dropped (301 Coyotes games had a market and went unpriced here). `--ari-as-uta` replays them as UTA (no change to the verdict). Fixing it changes production
pricing, so it needs its own PR.

## Limits (read before quoting any number)
- BettingPros closes are each book's last quote, not Pinnacle and not a time series; the NHL median last change is 42 minutes pre-drop. The
  NFL / NBA harness numbers use different closes, so the three sports' gains are not directly comparable.
- Single replay, parameters as shipped; nothing tuned on these seasons. Whether the engine's hand-set constants were chosen on any of
  2021-2026 was not checked; if they were, these numbers are generous to it.
- 2020-21 is a COVID-shortened, divisional season (884 games scored); 2026-27 is a 242-game stub. Excluding both (the 2021-22 to 2025-26
  figures above) changes nothing, so they do not drive the pooled result.
- The opening quote is one book's first post. The CLV / open-ROI paragraph is hypothetical-entry research, three correlated tests, and the
  edge-bucket and CLV figures are all conditional on "same line at open and close".
- Coverage after 2026-11-04 is not banked; this is a one-off crawl, not a job.

## Reproduce
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data python -m mlb_core.odds.bettingpros_games --sport NHL --start 2021-01-01
    gcloud storage cp -r gs://concrete-crow-445205-m4-mlb-data/Odds/bettingpros_nhl_games/{moneyline,spread,total} <dir>
    gcloud storage cp gs://concrete-crow-445205-m4-mlb-data/NHL/{games,team_boxscores,goalie_boxscores}_master.csv <nhldir>
    PYTHONPATH=. python -m nhl.game.market_eval --local-dir <dir> --nhl-dir <nhldir> --validate --out report.md      # ~7 min
    # sensitivities: --ari-as-uta | --fresh-hours 6 | --min-books 5 --exclude-book-ids 39 | --save-events <dir>

## Not done / next
1. Fix the `ARI` hole in `nhl.config.TEAMS` (own PR; changes pricing) and re-run.
2. Confirmed-goalie replay: the biggest known input the engine lacks historically is the starter; BettingPros' goalie-saves props
   (`Odds/bettingpros_nhl/saves`) carry the starter's identity per game and could condition the replay.
3. Forward CLV on engine picks (none are logged for game lines) and a schedule to top up the crawl, if the owner wants a standing series.
