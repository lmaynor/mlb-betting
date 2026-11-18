# Bankroll Contest (paper betting game) -- scope and plan

Status (2026-11-17): M0 spikes done (section 11). M1 engine + schema + store BUILT on this branch (`contest/`, tested on SQLite and local PostgreSQL 16.2 incl. concurrency, not deployed). Round robins (engine, up to 11 picks) and a sample-data UI at `/contest` (OFF by default, browser-verified at 1280px and 375px) also built. M2+ not started. Written 2026-11-16 (Mon). Working title "BZ Bankroll Challenge"; rename freely.
Proof of concept: college football bowls + CFP (bowls open mid-December, about 4 weeks out). Then March Madness.

## 1. Decisions already made (owner, 2026-11-16)

| Question | Answer |
|---|---|
| Audience | Private, invite-only (the existing contest crowd). Data model keeps `contest_id` so it can open up later. |
| Prize / fee | Bragging rights only. No payments, no Stripe, no entry fee. |
| v1 markets | Main lines only: spread, moneyline, total, as straights, parlays and teasers (parity with the current contest). |
| Halftime | IN v1, but sequenced last and behind a flag (see section 7). Pregame must not wait on it. |

## 2. What we already have (verified 2026-11-16)

The premise "we don't have college odds" is half wrong. For MAIN LINES we already do, free, with zero ParlayAPI credits:

- `ncaaf-bank-bp`, `ncaaf-bank-kalshi`: every 20 min, Aug-Jan, scheduler ENABLED, last ran 2026-11-17 04:40Z. Writes
  `Odds/{bettingpros,kalshi}_live/ncaaf/game_lines_latest.json` (ML / spread / total ladders, start times, team names).
- `ncaab-bank-bp`, `ncaab-bank-kalshi`, `ncaab-board`: hourly, Nov-Apr (covers March Madness). Same file shape.
- `an-daily-ncaaf/ncaab/ncaaw`: Action Network daily harvest (closing-state snapshots, not intraday). Not needed for v1.
- Team crosswalk BettingPros <-> Kalshi for NCAAF: `ev/sports/ncaa_teams.py`. NCAAB has none (joins by name similarity in `ev/ncaab_board.py`).
- Frontend: Clerk auth with `getUserId()` (`beezy-vip/lib/auth.ts`); frontend talks to the Cloud Run API (`lib/betting-api.ts`) which owns Cloud SQL (pg8000).
- The only existing "bankroll" is a single number in Clerk publicMetadata (`app/api/user/bankroll`). No bet ledger. Do not reuse it.
- ESPN public scoreboard is already used for NBA grading (`ev/grade_props.py`); it exposes `STATUS_HALFTIME` / `STATUS_FINAL`.

NOT present: any NCAA wiring in the ParlayAPI client (grep is empty); any NCAA final-score source; any in-game / halftime price feed (bankers drop started games). Props and alts for college are unproven and out of v1 scope. If we ever want them, probe ParlayAPI/AN coverage first.

## 3. Game rules (defaults, mirror your real rulebook where it differs)

OWNER INPUT NEEDED: send the existing annual-contest rulebook. These defaults are placeholders until then.

- Start $5,000. Eliminated when balance < min stake and no pending bets. Eliminated entries stay on the board, greyed.
- Bet kinds: straight; parlay (2-8 legs); teaser (2-4 legs, spreads/totals only, football 6 / 6.5 / 7 pt, basketball 4 / 4.5 / 5 pt, fixed payout table in config). Max one leg per game per slip (no same-game correlation).
- Stake limits in contest config: min, max per bet, max total open risk. Decided 2026-11-17: min $50 (1% of the start), no max beyond the balance (section 15).
- Pricing: ONE reference price per market for everyone (no line shopping). Proposed: DraftKings from the BettingPros feed, fallback to median onshore line; fixed -110 where the book is flat. A bet records line + price + quote timestamp at placement.
- Push: straight refunds; parlay/teaser leg drops out; overtime counts; postponed/cancelled -> void after 48 h.
- Hidden picks: a bet is hidden from everyone but its owner until that leg's game kicks off (per-leg for parlays). Enforced in the API layer, not the UI. Leaderboard shows balance, rank, record, open-risk total only.
- Bets close 15 min before kickoff (late injury / opt-out news buffer). Admin can suspend a market or game.

## 4. Architecture

Shared infra, not a sport pillar: new package `contest/` (Python, Cloud Run API + Cloud SQL), `beezy-vip/app/contest/**` (frontend). Sport adapters are thin.

Tables (new, own schema PR per CLAUDE.md contract rule):
`contest`, `contest_entry` (Clerk user id, balance, status), `contest_game` (ESPN event id, BettingPros key, commence_time, round label, final score, status), `contest_bet`, `contest_leg`, `contest_ledger` (append-only STAKE / PAYOUT / REFUND / ADJUST; balance is derived and reconciled), `contest_invite`.

Flows:
1. Board builder (scheduler, every 20 min in game weeks): read the banked ncaaf files -> map to contest games through the crosswalk -> pick reference price -> write `Contest/{id}/board_latest.json` with `fetched_at`. A game with an unmapped team is dropped, never guessed (repo convention).
2. Placement (one DB transaction): lock entry row, check status/balance/limits, game not started, quote age under gate (NCAAF 30 min, NCAAB 75 min), client's expected price equals the current board price (else "price changed"), idempotency key, insert bet + legs + ledger debit. Time is server time only.
3. Grading job (every 15 min on game days): ESPN scoreboard -> final scores -> grade legs -> settle bets -> ledger credits -> eliminations. Idempotent. Admin override: void a game, correct a score.
4. Frontend/API bridge: Next route handler authenticates with Clerk, calls Cloud Run with the API key plus the user id (server to server; the key never reaches the browser).
5. Admin page: create contest, invites, load slate, suspend, void, adjust, view reconciliation.

## 5. Odds and cost

v1 uses the free banked feeds: no ParlayAPI credits, no new scraped source (no new ToS check). The refresh cadence you wanted (about hourly, not overnight) is already satisfied or exceeded by running jobs; the contest only consumes their files. Credits become relevant only for props / alts / ladders later.

## 6. Milestones (each its own PR; shipped OFF behind `CONTEST_ENABLED`, kill switch accepts 0/false/off/no)

| # | Work | Target |
|---|---|---|
| M0 | Spikes (1-2 days): (a) live/halftime price probe during live NCAAB games and Sat 11/21 CFB: do BettingPros / Kalshi return in-game full-game lines, how fresh; (b) ESPN college scoreboard shape and event-id <-> BettingPros join rate for NCAAF; (c) reference-price choice on real files | Nov 17-21 |
| M1 | Schema PR + pure engine (parlay/teaser math, push rules, ledger invariants, grading) + offline tests | Nov 24 |
| M2 | Board builder (NCAAF adapter) + placement + settlement + admin API + tests | Dec 1 |
| M3 | Frontend: join by invite, board by day, slip (straight/parlay/teaser), my bets, leaderboard, admin | Dec 5 |
| M4 | Dress rehearsal on real games: Sat 11/28 and conference championships Sat 12/5, a few friends, full settle cycle | Dec 5-6 |
| M5 | Halftime window (flag `CONTEST_HALFTIME_ENABLED`, OFF), rehearsed on 12/5 and early bowls; go/no-go from the M0 probe and rehearsal | Dec 11 |
| Launch | Deploy per CLAUDE.md wrap-up sequence (API first, then Vercel), invites out, bowls open | ~Dec 12 |
| MM | March Madness: NCAAB adapter (BettingPros anchor + name join), ESPN men's CBB scoreboard, 4-pt teasers, raise bank cadence to 20 min for tournament days | Feb |

Selection / CFP round dates should be confirmed against the official calendar; later CFP rounds only get lines once matchups are set, so rounds open sequentially and bankrolls carry over.

## 7. Halftime window (in v1, last milestone, flag OFF)

Why it is defensible: at halftime the ball is dead, so the "bet after a dramatic play before the line moves" exploit largely disappears. Remaining risks are feed lag around the buzzer and stale quotes.

Design:
- Watcher (scheduler tick every minute during contest game windows): poll ESPN scoreboard for contest games; state machine PREGAME -> LIVE -> HALFTIME -> LIVE2 -> FINAL.
- Window opens only when ESPN says halftime AND the score has been unchanged across two polls AND a quote fetched AFTER halftime was detected exists (so the price post-dates the last play).
- Markets: full-game live spread / total / moneyline (graded on the final score, so the first-half score is already priced in). Straights only. Tighter caps (e.g. max 10% of balance). Optional: ignore 2H-only lines (not reliably in the catalog).
- Quote refresh every 30-60 s while open; bets accepted only at the current price with the same "price changed" rejection; close on ESPN leaving halftime, a score change, a stale quote (>90 s) or an 8-minute cap.
- Halftime bets stay hidden until the window closes.
- Pricing (DECIDED by the M0 probe, section 11): BettingPros has no live lines, so halftime prices come from Kalshi only: mid of bid/ask, a fixed contest margin on top, and a liquidity filter (bid-ask width <= 6c, otherwise that market is not offered for that game). Expect moneyline (and football spread) on liquid games only, not a full menu.
- Request rate: Kalshi's public market data is built for polling, and BettingPros is not used at halftime, so the one-polite-request rule is not an issue.
- Ships OFF. If M0 shows no usable live source, halftime becomes a documented null result and the rest of the contest ships unchanged.

## 8. Risks

1. Halftime live prices unverified -- largest risk; M0 probe decides.
2. Bowl opt-outs and late line moves: 15-min close buffer, admin suspend/void.
3. Game mapping: NCAAB has no code crosswalk (fuzzy joins); drop unmapped games.
4. Hourly NCAAB cadence is thin for tournament-day clusters; raise in March.
5. Bowl and CFP slates appear late; only games with banked lines are bettable.
6. Settlement accuracy (vacated results, OT, neutral sites): single ESPN source plus admin override; consider an AN cross-check later.
7. Identity is Clerk-only: one entry per account, invite codes with use limits, rate limiting.
8. Single reference price vs line shopping is a fairness choice; confirm.

## 9. Testing and evidence

Offline only (conftest fixtures, saved ESPN and banked-feed JSON, no GCS or network). Engine unit tests (payout math, push/void, teaser tables), ledger invariants (sum of ledger equals balance), concurrent double-submit placement test, grading from recorded scoreboards, halftime state machine from recorded status sequences. No real-money path exists anywhere in this project.

## 10. Not in v1 (cash out and live game state are in, see section 12)

Player props, alts, ladders, same-game parlays, live in-play betting beyond the halftime window, prizes/fees, public signup, casino games, notifications (a Discord ping at halftime open is a cheap later add).

## 11. M0 spike results (run 2026-11-17 ~04:50 UTC, read-only, public endpoints plus `gcloud storage cat`)

Sample sizes are small and noted. Nothing was written to GCS or deployed.

**A. Live / halftime prices**
- BettingPros: still returns offers for games that have started, but they are FROZEN at tip-off. For 6 NCAAB games that started 1.5-3.5 h earlier (most already over), the newest `updated` stamp across all books was 0-8 min after the scheduled start, on spread, total and moneyline. No live lines. The event `status` field read "scheduled" for every event, including finished games, so it is useless for game state.
- Kalshi: markets stay `active` in play and the quotes track the score. With ESPN showing UC San Diego up 9 and about 4 min left, "wins by over 6.5" quoted 0.70/0.73; UCLA up 34, "over 7.5" quoted 0.99/1.00. The market `updated_time` field is NOT a freshness signal (it read Nov 13), so staleness must be judged from our own poll time.
- Kalshi liquidity is thin. NCAAB near-the-money rungs: median bid-ask width 6c (moneyline), 7c (spread), 8c (total); spread and total median volume 0; 35% of moneylines within 3c. NCAAF: moneyline 38% within 3c (median volume 38), spreads median 4c wide with 95% within 6c; the totals series `KXNCAFTOTAL` returned 0 open markets (unexplained, check before relying on football totals).
- (Superseded by section H below: a real halftime was captured later the same day.) NOT tested at the time of this probe: an actual halftime. All 4 games live during the probe were in the 2nd half. The ESPN status name `STATUS_HALFTIME` was not observed (only IN_PROGRESS and FINAL, with `period` and `displayClock` available). Next real captures: tonight's NCAAB slate (Tue 11/17 from about 7 pm ET) and Sat 11/21 football.
- Verdict: halftime is feasible only as Kalshi-priced, liquidity-filtered, and offered on a subset of games and markets. Stays flag OFF; go/no-go after the 11/21 and 12/5 captures.

**B. ESPN scoreboard**
- Works for FBS (`groups=80`), FCS (`groups=81`) and men's college basketball (`groups=50`). Always pass `dates=`: the default call returned 0 football events on a weekday.
- CFP placeholders already exist: 2026-12-19 carries 5 events with TBD teams, headline "College Football Playoff First Round Game", season type 3 (post-season). Contest game rows can be pre-created and filled when matchups are set.

**C. Game mapping (BettingPros -> ESPN, normalized school name, start within 3 h)**
- NCAAF Sat 11/21 (fresh BettingPros crawl, FBS + FCS): 99 of 104 named games joined (95.2%). Misses are aliases: Louisiana-Monroe, Miami (FL), UNLV, Albany, LIU. 12 of 116 BettingPros events had no names yet (no moneyline offers posted).
- NCAAB banked slate: 110 of 118 named games joined (93.2%). 4 of the matches had home and away flipped versus ESPN (neutral sites), so the join must be orientation-agnostic. Misses: Miami (FL), Ole Miss, San Jose St., Cal Baptist and similar aliases.
- Takeaway: a small alias table plus admin approval of the slate gets this to a clean mapping. Never auto-offer an unmapped game (repo convention).

**D. Feed windows**
- The NCAAF banker only looks about 3 days ahead: the live file holds 7 games and none from Saturday. The bowl board builder needs a wider `days` window than the banker's default, or its own crawl.

**E. Changes to the plan**
- Section 7 pricing decided (Kalshi only, liquidity filter).
- Add to M2: alias table and orientation-agnostic join; wider lookahead window; pre-create CFP rows from ESPN placeholders.
- Add to M5: a halftime capture script (ESPN status plus Kalshi quotes every 30 s through real halftimes) run on 11/17 evening, 11/21 and 12/5 before any go decision.

**F. Halftime capture tool (built 2026-11-17)**
- `scripts/contest_halftime_capture.py` (stdlib only, laptop, no keys) polls ESPN every 30 s and, only while a game is in the capture window (last 4 min of the first half, halftime, first 4 min of the second), pulls the open Kalshi moneyline / spread / total series and appends the matched game's quotes to a JSONL file in `~/halftime_captures/`. `--summarize FILE` prints a per-game verdict: ESPN status names and halftime length, whether the score stayed frozen, favourite's moneyline mid before vs after the break, and how many snapshots were within 6c. Tests: `tests/test_contest_halftime_capture.py` (16 pass, offline).
- First run showed ESPN also uses `STATUS_END_PERIOD`, so the window logic keys off period and clock, not a single status name.
- Kalshi coverage of tomorrow's NCAAB slate (pregame quotes, saved Kalshi data vs ESPN 2026-11-17): 52 of 68 ESPN games matched to a Kalshi game (76%); 43 had both moneyline sides within 6c. Kalshi had spread markets for 47 of the 52. Halftime offers will therefore exist for roughly 60% of games at best, never all.
- Matching is exact-name and fails closed; games with no unique match are logged as notes, not guessed.

**G. `WalrusQuant/sports-leader-mcp` and ESPN's own odds (read 2026-11-17, nothing cloned or run)**
- What it is: an MIT-licensed Node / TypeScript MCP server (about 10 stars, one maintainer, pushed daily) that wraps ESPN's undocumented public endpoints (scoreboard, summary, odds, win probability, injuries, news), keyless Kalshi / Polymarket prices matched to ESPN games, and a third-party nightly Parquet export (schedules, results, box scores, injuries) for college football and men's college basketball from 2021 on. Our stack is Python on Cloud Run, so it is a reference for endpoints and contracts, not a runtime dependency.
- Probe (ESPN scoreboard, not the MCP): the event itself carries DraftKings main lines. Football 2026-11-21: 54 of 58 games have odds; men's basketball 2026-11-17: 54 of 68. Spread (line + price, e.g. +5.5 at -112), moneyline (+185) and total (o47.5 at -108) each carry a `close` block, and the same payload carries ESPN team ids and logos. The CFP first-round placeholders (2026-12-19, TBD teams) have no odds yet. Games in play carry NO odds, so ESPN does not replace Kalshi for the halftime window.
- What that changes: the contest board could read its reference price from the ESPN event, which is already keyed by the ESPN event id the contest uses. That removes the BettingPros-to-ESPN name join (93-95% in section 11C) and the alias table for pricing, and it matches the placeholder "reference book = DraftKings". BettingPros / Kalshi stay as the fallback and cross-check. NOT yet checked: how fresh ESPN's lines are against BettingPros' DraftKings lines, and whether the `open` block is present.
- Blockers before building on it (CLAUDE.md: a new scraped source needs a recorded ToS or permission check first): `docs/DATA_LICENSES.md` has no ESPN entry and CONTEXT.md records no ESPN terms check, although ESPN's scoreboard is already used for NBA grading (`ev/grade_props.py`). Showing DraftKings prices in a contest is a second licensing question. ESPN's endpoints are unofficial and can change without notice.
- The third-party Parquet export is useful for rehearsal fixtures and grading tests (real final scores, 2021 on). Its licence and provenance were not checked and nothing was downloaded.

**H. A real halftime, captured 2026-11-17 (Rutgers at Houston, 45-30, `scripts/contest_halftime_capture.py`, 21 cycles over 6.7 min)**
- ESPN: the status name is `STATUS_HALFTIME`, detail "Halftime", period 1, clock 0:00. The score stayed frozen at 30-45 for the whole capture. (Basketball; football halftime not yet seen.)
- Kalshi, this one liquid game: every one of the 294 spread rung quotes was within 6c (most 1c wide) and 310 of 316 total rung quotes were within 6c; mids moved at most 1c in 6.7 minutes. The moneyline was out of the capture's 3c-97c range because Houston led by 15. One game is encouraging evidence, not proof: the earlier probe found thin markets on most NCAAB games. Go/no-go for the halftime window still needs more halftimes (a football one on Sat 11/21, and games with less liquid markets).
- The capture summary printed "Kalshi snapshots: 0" because it keys its favourite-price block on the moneyline series; it still reported the spread and total counts. Worth making the summary say "no moneyline in range" instead.
- ESPN's basketball `situation.lastPlay.probability` carries its own home / away win probability (0.983 / 0.017 at that moment): a model-free in-game win probability that a later in-game cash out could use for moneyline bets. Not built, not validated.

## 12. Cash out, live game state, and the critique pass (2026-11-17)

- **Cash out (pregame only, built):** value = full payout x P(every leg still wins on the current board) x (1 - 5%), rounded down, never above the payout. P(leg) is the de-vigged two-sided price, moved from the current line to the line the player holds with a normal model of the final margin / total, so a line that moved in the player's favour can cash out above the stake (closing-line value). Straights and parlays; a round robin cashes out as one group, all or nothing; teasers are not offered. Closes with betting (15 minutes before kickoff) and needs fresh quotes. `store.cash_out` re-prices and refuses with VALUE_CHANGED if the board moved since the quote. The 5% margin and the sigmas (football 13.5 / 13.0, basketball 11 / 15) are placeholders and unvalidated; a normal model ignores key numbers.
- **In-game cash out (not built):** needs live prices. Only the halftime window will have them (Kalshi, section H); BettingPros freezes at tip-off and ESPN carries no odds on a live game. The engine already takes any quote board, so the halftime feed can feed it later.
- **Live game state on the cards (UI built, feed not):** score beside each team name, a dot for who has the ball, and a state line (clock, down and distance, red zone) from `GameState`. The ESPN scoreboard supplies status, score and clock for every sport (seen live). Football `situation` was checked on a LIVE payload (EMU at WMU, 2026-11-17): `downDistanceText` ("1st & 10 at EMU 16") is present on a normal down and absent between plays; ESPN sent no possession team id, so the possession dot appears only when ESPN supplies one.
- **Critique pass:** Impeccable critique 25/40; fixed the color-only selection, undersized slip controls, play-money wording, first-pick warning, board ordering and width, 11px labels, and Pool wording.

## 13. Alt lines, same-game parlays, the live API, and what is still open (2026-11-17)

**Measured, not guessed.** `scripts/contest_sportsbook_research.py` ran on 943 football and 700 basketball closing lines (write-up with every table: `docs/analysis/contest_sportsbook_experiments_2026-11-17.md`). Football sigma 15.0 spread / 15.5 total (placeholders were 13.5 / 13.0); basketball 11.1 / 17.2. Key numbers are real in football (a final margin of exactly 3 is 3.8x likelier than its neighbours, 7 is 3.0x) but weighting the margin model by them did not beat a plain normal out of sample (log-loss gain +0.0012, interval -0.0012 to +0.0035), so that is a null result and the shipped weights (3 -> 2.5, 7 -> 1.9) are a judgment call that stops buy-through-the-3 arbitrage, not an estimate.

- **Alt lines (built):** any half-point number within 14 points (football) or 10 (basketball) of the main line, priced by `contest/altlines.py` from the main two-sided price through `contest/margin_model.py` (a discretised normal refit to the de-vigged probability, key weights on football spreads) plus a 5% margin, price to the nearest 5. A deterministic function of the board, so `validate_slip` checks an alt leg without storing a ladder (`BAD_LINE` when not offered, `PRICE_CHANGED` carrying the right price). Spreads and totals only; never inside a teaser (already moved) or a same-game parlay. The slip shows a half-point stepper per pick. Cash out uses the same model, so buying an alt and cashing it out cannot beat the margin. UNVALIDATED.
- **Same-game parlays (built):** a spread or the moneyline plus the total from one game (spread + moneyline refused: the legs nest). Priced as a pair with a Gaussian copula at the measured event correlation (+/-0.17 for football spread x total: favourite + over and underdog + under positive, the other two negative; 0.05 for moneyline x total; basketball +/-0.02 and 0), then a 7% margin. The ticket price is quoted by the server and stored on the bet (`contest_bet.ticket_price`); a changed ticket is `PRICE_CHANGED`; if one leg pushes or voids the other pays at its own price; not cashed out. Tab "SGP" on the slip. UNVALIDATED.
- **Teasers (checked, not changed):** at the placeholder prices a random teaser loses for the bettor in both sports (leg win rate 61-72% football against 72-76% needed; basketball 51-67%). Only favourites through the 3 (74-79%, n about 80) come near break-even. The tables are the owner's rulebook decision.
- **M2 data plumbing (built, nothing deployed):** `contest/espn.py` parses the scoreboard (checked on a live payload: 54 of 58 games carried spread, total and moneyline); `contest/sync.py` + `refresh.py` upsert games, store quotes (`contest_quote`), keep live state, settle finals and void postponements in one idempotent pass; `contest/service.py` maps requests to engine calls and engine state to the page's feed; `contest/api.py` exposes `GET /api/contest/<slug>/feed`, `POST .../place|join|rename|cashout` (site API key + `X-Contest-User`), and `POST /contest-refresh` (scheduler). `scripts/contest_admin.py` creates the contest and invites and runs a refresh by hand. The site proxies through `/api/contest/[slug]/[action]`; the page goes live when `CONTEST_SLUG` is set and falls back to sample data only when it is not.
- **Clerk participant role (built as a mirror):** the database is the authority (a `contest_entry` row exists only after an invite code was redeemed). A successful join also writes `privateMetadata.contest.roles.<slug> = "participant"` (admins: `"admin"`) through the Backend API, the same place the Stripe webhook keeps `tier`. Options weighed: Clerk Organizations (needs the feature switched on, adds an org switcher, still needs our invite rules), a session-token claim (`{{user.public_metadata.contest}}`; a dashboard setting we cannot set from code), and private metadata (chosen). Nothing here depends on a dashboard change. VERIFIED against the real Clerk API (development instance, Clerk CLI, 2026-11-17, throwaway user deleted afterwards): a BAPI-created user's primary email reads `verification.status = verified`; `PATCH /users/{id}/metadata` DEEP-merges nested private metadata (a nested write keeps other roles and the Stripe `tier`) and deletes a key only when it is set to `null`. That exposed a bug: `revokeContestRole` wrote the roles without the slug, which Clerk ignores; it now writes `null`. The real `grantContestRole` / `revokeContestRole` code was run through the CLI: grant, admin grant, repeat join does not downgrade an admin, revoke really removes, `tier` preserved. Not yet run: the SDK path inside the deployed Next route, and an UNverified email.
- **Verified:** 180 Python contest tests pass on SQLite and on a throwaway local PostgreSQL through pg8000; 170+ frontend contest tests pass; one real ESPN refresh into a scratch SQLite wrote 71 games and 396 quotes. NOT verified: Cloud SQL, the deployed API, Clerk with real keys (the join mirror), anything on Vercel.

## 14. Sportsbook extras, organiser tools and the halftime window (2026-11-17, second pass)

- **Odds format toggle:** American, decimal or fractional, a display setting remembered in the browser (`lib/contest/format.ts`); the engine and the server stay American. Fractions are exact (+150 is 3/2, -110 is 10/11).
- **My Bets:** All / Open / Settled filter with counts; **Bet Again** puts a settled bet's picks back on the slip at TODAY's prices (never the old ones) and says "Games closed" when it cannot. A bankroll chart sits above the table: one flat line, the start as a dashed baseline, and a text summary (start, peak, low, now). Points are the moments the bankroll changed (a bet settling, a cash out, an adjustment, a blackjack result); placing a bet moves money from balance to risk and is not a point.
- **Organiser:** `/contest/admin` (404 unless the user is an organiser of that contest in Clerk private metadata) and `GET /api/contest/<slug>/admin`, `POST .../admin/<op>`: rename, adjust (a reason is required), void-game, settle-game by hand, invite, open / close to new bets, and feature switches (halftime, cash out, alt lines, SGP, blackjack). Every action is written to `contest_admin_log` with the actor. The service trusts `X-Contest-Admin` only because the site API key makes the caller the Next proxy, which checks the Clerk role first.
- **Rate limits and accounts:** per-user sliding windows (join 6 per 10 minutes, place and cash out 20 per minute, reads 60 per minute), 429 with `Retry-After`; in memory per process, so the real ceiling is the limit times the instances. Joining needs a verified primary email in Clerk. That is the cheapest check that one inbox is one account; it does not stop one person using two inboxes, which the invite code and the organiser's eyes have to cover.
- **Announcements (OFF):** `contest/notify.py` posts kickoff, final (with the top of the board), postponement and elimination to a Discord webhook when `CONTEST_NOTIFY` and `CONTEST_DISCORD_WEBHOOK` are set. Each message is claimed in `contest_notice` before sending (no double posts), a dead webhook releases the claim, the first pass records history quietly, and messages carry display names only.
- **Halftime window (OFF, built, unrehearsed):** `contest/halftime.py`. ESPN halftime plus an unchanged score across two polls plus a Kalshi quote fetched after halftime was first seen opens the window; it closes on a score change, a quote older than 90 seconds, 8 minutes, or ESPN leaving halftime, and never reopens for that game. Straights only, at most 10% of balance, at the current Kalshi-derived price (mid plus 7%; a market wider than 6 cents is not offered), and the bet is hidden from other players until the window closes (`phase = 'halftime'`). Needs BOTH the contest rule (an organiser switch) and `CONTEST_HALFTIME_ENABLED` in the service. All three Kalshi title shapes were read from live payloads on 2026-11-17 (football total series is `KXNCAAFTOTAL`, titled "Full Game: Over X points scored"; `KXNCAFTOTAL` is empty today but is also read). A series lists every game, so a game's markets are chosen by event ticker (found from its team names); the first version took the totals of ALL games, which the live check exposed and a test now guards. Only one liquid basketball halftime has been recorded. This is a build, not a go decision: rehearse on 11/21 and 12/5 first.
- **March Madness:** `sport = ncaab` uses basketball defaults (quote age 75 minutes for an hourly board, alt reach 10, no key weights, 4/4.5/5-point teaser placeholders, sigmas 11.1 / 17.2 from two January weeks). The ESPN parser was run on a real men's basketball scoreboard (68 events, 48 with all three lines, round labels from the event notes). Refit sigmas on a full March sample before the tournament.
- **Scheduler (script written, NOT run):** `deploy/setup_contest_scheduler.sh` creates `contest-refresh` (every 20 minutes) and `contest-halftime` (every minute, a no-op unless a game is at halftime and the rules allow it), both PAUSED unless `START=1`, binds `roles/run.invoker` for the scheduler account on the service, and supports `PAUSE=1`.
- **ESPN and Kalshi terms (recorded, owner review needed):** ESPN's scoreboard is an unofficial, undocumented endpoint with no published terms that grant this use; Kalshi's market data endpoint is public. The contest is private, invite-only and play-money, requests carry a descriptive User-Agent, and one request per day of the window per pass is made. Whether that is acceptable is the owner's call, the same as for the other scraped sources in CONTEXT.md.

**Still open before invites go out:** the real teaser table and stake limits and the prize / tiebreak rules (owner), responsible-play copy (owner), the deploy itself (service first, then Vercel, per CLAUDE.md, only on the owner's go), env on Vercel (`CONTEST_ENABLED`, `CONTEST_SLUG`, `BETTING_API_URL`, `BETTING_API_KEY`) and Cloud Run (`CONTEST_ENABLED`, and optionally the notify and halftime switches), running the scheduler script, the Clerk role write inside the deployed route and the unverified-email refusal, and the halftime rehearsals.

## 15. Owner decisions, 2026-11-17

- **Stakes:** no maximum bet beyond the balance (`max_stake_bp` 10,000, `halftime_max_stake_bp` 10,000); the minimum is 1% of the $5,000 start, $50, per bet and for a round robin's total (each round-robin parlay still needs $5). The elimination floor is unchanged: a player is out when the balance cannot cover the minimum bet (the $1 blackjack chip while the lifeline is on) and nothing is pending.
- **Teasers:** the placeholder table is kept (section 5 of the analysis doc has the alternatives).
- **Feeds:** using the ESPN scoreboard and Kalshi public market data is approved.
- **Responsible gambling:** the help line and link on the contest page are approved as written.
- **Halftime:** acknowledged; stays OFF until the 11/21 and 12/5 rehearsals.
- **Turn-on:** `deploy/setup_contest_scheduler.sh START=1` does it all in one go (see its header); `PAUSE=1` reverses it. A new idempotent `POST /contest-setup` (scheduler auth) creates the contest and invite code on first run and never edits an existing contest.
- **Deploy:** all of main goes out with the normal service deploy; the order is service deploy, then Vercel is already live, then the script with START=1.

