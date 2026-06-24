## Section C -- Live-odds guard + ParlayAPI odds-accumulation fleet (2026-06-24)

This chat's contributions, for the combined 3-chat handoff. Scope: the in-play
odds overconfidence fix (shipped + deployed) and the ParlayAPI accumulator ramp.

### C1. Live/in-play odds guard -- SHIPPED + DEPLOYED (PR #15, main 63936ac)
**Bug:** snapshots sometimes capture in-play odds (game already started). The
pre-game count models score them assuming a full game of PAs/outs remain ->
dramatically inflated P(over) -> bad bets.

**Fix (deployed):**
- `mlb_core/odds/sgo.py`: new `is_live_event(commence_time, now=None, grace_min=0)`
  -- fail-OPEN on missing/unparseable input (never suppresses the whole slate).
  Added `commence_time` (= `status.startsAt`) to the player-OU extractor
  (`_extract_player_ou_props` -> HITS/TB/K/OUTS/ER) and the HR row builder
  (`_hr_row_from_entry`). Game-level extractors already carried it.
- Runners gate `kelly_triggered` on `not is_live_event(...)`:
  `run_batter_hits`, `run_hr`, `run_k` (K + OUTS + ER), `run_batter_tb`.
  Live picks still LOG as predictions (`kelly_triggered=False, stake=0`) for
  measurement; they are not bet. run_batter_hits/run_hr also emit a warning log.
- `tests/test_live_event.py`: 6 tests, green.

**Verify next:** on the 22:00 UTC run, `gcloud run services logs read mlb-betting
... | grep -i "LIVE/in-play"` should show suppressions on afternoon games; no
`stake>0` rows on already-started games.

**NOT done (optional follow-up):** the game-level runners NRFI/F5/GAME don't have
the "PAs left" symptom but could take the same one-line guard --
`commence_time` is now on every extractor so it's trivial.

### C2. ParlayAPI accumulator fleet ramp -- MERGED (PR #14)
User upgraded ParlayAPI to $5 / 20,000 credits/mo. Billing (measured live): props
= 1 credit per (event x market); game lines = 1 credit per market (whole slate);
slate discovery = 1.
- `nba/config.py`: MLB prop markets = the 6 we model (hits, total_bases,
  home_runs, strikeouts, earned_runs, pitcher_outs). Dropped `player_pitching_outs`
  (NOT a live key -- wasted a credit; live key is `player_pitcher_outs`).
- `deploy/setup_parlay_accumulator.sh`: sanitize job-name kind
  (`game_lines` -> `game-lines`) for valid Cloud Run names.
- `deploy/setup_parlay_schedules.sh` (new): provisions the fleet + crons.
  Active: `parlay-accum-mlb-props` 5x/day, `parlay-accum-mlb-game-lines` every 3h
  (~14.3k/mo). Staged (no scheduler until ~Oct): NBA props + game-lines jobs.

**Pending deploy step (Cloud Shell):**
```
cd ~/mlb-betting && git checkout main && git pull && ./deploy/deploy_service.sh
PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_parlay_schedules.sh
```
To push toward ~19k/mo, bump MLB props to 7x/day:
```
PROJECT_ID=concrete-crow-445205-m4 SPORT=baseball_mlb KIND=props \
  SCHEDULE="0 13,15,17,19,21,23,1 * * *" bash ./deploy/setup_parlay_accumulator.sh
```
This forward-accumulates the historical prop archive (`OddsAccum/{sport}/`) the
NBA (and MLB) props backtest needs -- ParlayAPI has NO historical props at any tier.

### Files this chat touched (for merge-conflict awareness)
Merged to main: `mlb_core/odds/sgo.py`, `runners/run_batter_hits.py`,
`runners/run_hr.py`, `runners/run_k.py`, `runners/run_batter_tb.py`,
`tests/test_live_event.py`, `nba/config.py`,
`deploy/setup_parlay_accumulator.sh`, `deploy/setup_parlay_schedules.sh`.
- Did NOT touch `mlb_core/registry.py` or `runners/run_f5.py` (those carry the
  parallel `retire-no-edge-systems` chat's WIP).
- OVERLAP RISK: any parallel chat refactoring `run_k` / `run_hr` /
  `run_batter_*` must rebase on main 63936ac to pick up the live-odds gate.

### Open / next (this chat's domain)
- Verify the live-odds guard fired (above); optionally extend to NRFI/F5/GAME.
- Deploy + provision the ParlayAPI fleet; optionally bump to 19k cadence; flip on
  NBA jobs in October.
- (Cross-section) Edge enrichment job: validate Statcast-derived fields on a live
  run; wire nightly scheduler (per memory: 16:20/22:20 UTC, not after refresh-data).
