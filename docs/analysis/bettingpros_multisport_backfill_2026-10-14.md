# NFL / NHL / NBA prop-odds history: BettingPros slow crawl + ParlayAPI 7-day archive -- 2026-10-14

Goal: get historical per-book prop odds for the three new pillars so CLV / consensus / model backtests have something to
stand on. Two free-or-cheap sources, complementary. Probes were read-only; the ParlayAPI pull spent 460 credits.

## BettingPros (free, unlimited, SLOW) -- the long history

Historical depth, probed per sport with a real market on sampled dates (books = distinct books quoting; DFS included):

| Sport | Depth | Books per prop | Notes |
|---|---|---|---|
| NFL | 2021 -> now (2020: 5 books, 2019: consensus + FanDuel only) | 7-9 in 2021-22, 10-17 in 2023-25 | Novig/ProphetX from 2025 |
| NHL | 2022-11 -> now (2020-22-02: events but ZERO offers) | 5 (2022) -> 11-13 (2025) | no 2026-02-10 slate (Olympic break) |
| NBA | 2021-12 -> now | 5-7 (2021-22) -> 14-17 (2025-26) | Novig 80-95% of offers from 2025-11 |

Two-sided books at the modal line: median 3-7 early, 5-14 in 2025+; >=3 two-sided on 85-100% of offers throughout.
**Pinnacle never quotes** (0% every sport/date) -- confirmed again on history. Novig/ProphetX start 2025.

Crawler: `mlb.runners.backfill_bettingpros_props` (Cloud Run Jobs `bp-backfill-{nfl,nhl,nba}`,
`deploy/setup_backfill_bettingpros_props.sh`) over `mlb_core.odds.bettingpros_props`.
- Long format, ONE parquet per (market, date): `Odds/bettingpros_{sport}/{market_slug}/{date}.parquet`; columns in
  `bettingpros_props.COLUMNS`. One row per (event, player, market, line, side, book) plus one `is_open` row per side from the
  selection's `opening_line` and the BettingPros `Consensus` (book 0). `updated` is the quote's last-change time (the closest
  thing to a close in a daily snapshot).
- Keeps EVERY book. `book_kind` = book | exchange (Novig, ProphetX, Kalshi, Polymarket...) | dfs | sharp | consensus | open.
  The MLB map's trimmed book list dropped Novig/ProphetX/Kalshi/Pinnacle -- do not reuse it for these sports. **Kalshi must be
  excluded from any consensus** (the sweep trades against it); DFS quotes are not two-sided prices.
- Markets are resolved live from `/v3/markets` by slug (`category=player-props`, `period=game`); `most-*` specials skipped.
- Slow on purpose: offers page at 10, so an NHL game day is ~100-150 requests. Paced (0.8s/page, 1.5s/market, 3s/date, all
  jittered), newest first, resume-exact (a 0-row parquet marks an empty day), today never written. Smoke: 2 NHL markets / 4 games
  = 14 s. Estimated full history: NFL ~1-2 h, NHL ~10-15 h, NBA (5 core markets) ~25-35 h.
- Same-day caveat: a partition is only written for days strictly before today. BettingPros keeps the last quoted state of a
  settled day, so these are CLOSE-ish snapshots (one per day), not a time series. For line movement you need the live banking.

## ParlayAPI historical archive (paid credits, rolling window) -- the sharp close

`/v1/historical/sports/{sport}/closing-odds` holds `prop_closing_lines` (15M+ rows) and, unlike BettingPros, **Pinnacle and
Novig prop closes**. Depth is a TIER limit (`/v1/pricing` `historical_hours`): **Starter (our plan) = 168 h = 7 days** --
the window rolls, so the data is lost if not pulled. (The earlier claim "no historical-props API at any tier" was wrong
for the last week; it is right for anything older.)

`nba/odds/parlay_historical.py` (run 2026-10-14, 440 credits + 20 probe): 2026-10-07 .. 10-13, per (sport, date) one props call
(books: Pinnacle, Novig, ProphetX, Kalshi, DK, FD, MGM, Caesars, bet365, BetRivers, Fanatics, Hard Rock, Parx, Bovada, Fliff; DFS
excluded) + one game-lines call (default book Pinnacle). Raw JSON under `OddsAccum/{sport}/historical/{date}/{kind}_pNNN.json`,
a `_done.json` per unit. Rows landed: NHL props 995-4,958/day, NBA 2-3,317/day (preseason), NFL 525-19,854/day (Sunday = 4 pages).
- Row = (book, player, market, line, over_odds, under_odds, game_date, commence_time); **no team or event id** -- join to box
  scores on (game_date, player name). Pinnacle prop coverage is thin (NHL ~30 rows on a 3.5k-row day).
- Game-lines archive is nearly empty for NHL/NBA (0 rows every day) and ~42 rows/NFL day -- ~140 credits went on empty calls.
  Do not re-run `game_lines` for NHL/NBA without a reason; `--no-game-lines` skips it.
- A prop key outside `PROP_MARKETS` silently drops that book family (NFL receiving yards is `player_rec_yds` for US books,
  `player_receiving_yards` for Pinnacle/Fliff, `player_reception_yds` for Novig); the list carries the aliases.
- Cost is 10 credits per CALL (not per market), each `offset=` page billed again; shared pool 15,240 -> 14,780.
- Re-run weekly while the Starter window is the only access (`python3 -m nba.odds.parlay_historical --max-credits 600`):
  ~300-450 credits/week buys the rolling Pinnacle/Novig prop close. Whether that is worth the pool is a budget call (MLB's
  pace guard and the odds-bank ledger do not see this spend).

## Not done
- No BettingPros -> `ev.props.Quote` adapter, no odds_history parquet normalization for these sports, no CLV job.
- NBA only has 5 of 11 configured markets queued by default (widen with `BP_MARKETS`).
- No join to `NHL/` / `NBA/` / `NFL/` results yet (names differ per source; `PlayerResolver` exists for NHL/NBA).
