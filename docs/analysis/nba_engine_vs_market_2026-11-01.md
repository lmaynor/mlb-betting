# NBA serving engine vs real book lines (BettingPros history) -- 2026-11-01

**Verdict: UNDETERMINED, leaning NO_EDGE. Do not promote.** CLV, the repo's go/no-go, cannot be computed from this feed (one last snapshot per
day), so nothing here can say more. What the data does say:

1. **The engine alone is worse than the market on every stat and every season** (log loss 0.7114 vs 0.6859 with oracle absences, 0.7164 with none, on
   362,777 props; a coin flip is 0.6931). It is a worse price-setter than the consensus, not a better one.
2. **It carries a small amount of information the market lacks, concentrated in rebounds and threes** (blend c +0.13..+0.14 and +0.19..+0.21, t about 9),
   a little in assists (+0.09..+0.10), almost none in points and PRA (+0.02..+0.04). That is no more than the PRE-#147 engine carries (pooled c +0.08 both,
   oracle absences), so PR #147 did not add market-relevant information; it made the engine a better box-score predictor (see 3) without making it a better
   complement to the market.
3. **New beats old on the same props**, paired: pooled d log loss -0.0051 (SE 0.0003) with oracle absences and -0.0019 (SE 0.0001) with none; better in every
   season. Most of the oracle gain (about 60%) is the absence redistribution being fed the answer; without absence information the gain is small and sits in
   threes (-0.0044), PRA (-0.0045) and points (-0.0018), with rebounds and assists tied.
4. **Profit.** At the median-book price the walk-forward market-anchored blend shows +2% to +8% at edge >= 2-5% (n = 3k-11k, CIs lower bound about +1% at 2-4%,
   straddling 0 at 5%), carried by rebounds (+5.0%, CI [+1.7, +8.5]) and threes (+6.3%, CI [+2.8, +9.6]) and fading to +2.5% / +3.0% (CIs include 0) in 2024 / 2025.
   The engine's own side selection (no blend) loses: -3.4% to -3.7% at the median price against -4.0% for blind Under (a tiny positive "vs both sides" of +3% is the
   vig being slightly less than the full both-sides cost, not a profit). At the BEST price the no-model line-shopping control beats the blend (+15.9% vs +8.6..9.2% at 3%),
   exactly the best-quote artifact the 2026-10-23 study found. One walk-forward threshold read (blend, median price) is +3.4% [-3.2, +10.1] (n = 843): not distinguishable from 0.
5. **Bug found and fixed**: the live loader never gave the engine an opponent column, so the opponent factor and the shipped team-makes assist factor were inert on the
   real board, although every replay had them (section "Bugs"). With them the engine is better by -0.0011 log loss (SE 0.0002, z -6.9; assists -0.0017, PRA -0.0020, points -0.0011).
6. **The live display constants overstate the measured information**: `BLEND_C_BY_SPORT_STAT["nba"]` (threes .25, rebounds .18, assists .18, pra .12, points .10) vs measured
   engine c with no absence info (threes +0.19, rebounds +0.13, assists +0.09, pra +0.02, points +0.02) and with oracle absences (+0.21, +0.14, +0.10, +0.04, +0.03). Display only, not edited.

## What was run

`scripts/nba_engine_vs_market.py` prices every historical consensus prop with the production functions (`ev.daily_projections._nba_projection`, `GameLogs.context`, `p_over`) AS OF
the game: each player's history, the league context (rebuilt monthly from rows before the month, like `nba/props/engine_eval.py`), the team-game table and the absence inputs
are all cut to master rows dated strictly before the game's date. Six variants are priced on every prop, so every comparison is paired on the same rows:

| variant | engine | absence information | opponent column |
|---|---|---|---|
| `old` / `old_noabs` | pre-#147 (independent EWM per stat, flat 1 + 0.04 x absences) | oracle / none | yes |
| `ship` / `ship_noabs` | as shipped (`DP.NBA_FEATURES` read at run time: eta_scale 0.4, steals / blocks excluded, shot_gamma 0.5; DvP, pool, ZINB, tracking OFF) | oracle / none | yes |
| `ship_live` / `ship_live_noabs` | as shipped | oracle / none | NO (what the live loader fed it until the fix) |

**Absence information, stated up front.** `oracle` = every rotation player (L10 >= 12 mpg) the box score of THAT game lists as not playing is Out with probability 1 (a leak of the
game being priced; `old` gets the same oracle count, which favours `old`). `none` = no absence information at all. The market quote is the last snapshot of the day, taken after injury
news, so the market prices the known absences: `none` under-informs the engine relative to a live board that has the injury report (Questionable players are probabilities), and `oracle`
over-informs it (late scratches, DNP-CDs). The live engine sits between the two; the comparison is bracketed rather than resolved. Oracle absences inflate every number that is "new vs old" or "engine vs market".
No spreads (the master has none): the blowout trim and overtime term are not exercised. Props whose player did not play are void at the book and are not scored (same conditioning as the 2026-10-23 test).

**Population and join.** Consensus = `mlb_core/odds/bettingpros_loader.py` (>= 3 two-sided real books, no Kalshi / DFS / BettingPros-consensus rows, de-vigged median, one modal line per (event, player, stat); grace +10 min on
the postgame-quote test, as in the 2026-10-23 study), rebuilt from the local copy of `Odds/bettingpros_nba` (383,285 rows, identical to the earlier count). BettingPros carries only points, rebounds,
assists, threes and PRA in the bucket (pr / pa / ra, steals, blocks, double-double are not there); those five are scored. Identity is fail-closed: names resolve to the master's player id with `PlayerResolver`
over the players who played that UTC date, and the team must be one of the event's two teams. **The join date is the UTC date of `scheduled`, not the ET game day**: the master's `date` is the UTC tip date (exact-name
match rate 95.9% on the UTC date, 32.9% on the ET date). All lines are x.5, so there are no pushes and the engine's integer-line handling is not exercised.
Every one of the 383,285 consensus rows is either scored or counted under exactly one reason (`MVM.coverage` raises otherwise).

**No sampling**: all 1,083 game dates priced (`--sample-every 1`), 362,777 of 363,643 joined props priced by all six variants (866 had < 4 prior games for some stat). Runtime 244 s on 8 workers.

**Selection / fitting.** Nothing is fitted on the scored data. The walk-forward blend `p = sigmoid(a + b logit(mkt) + c (logit(engine) - logit(mkt)))`, clamped to market +/- 8 points, is fitted per stat on EARLIER seasons only (2021 is never scored by it;
the ROI tables score 2022-2025). Thresholds in the fixed grid {2,3,4,5}% are reported descriptively (all of them, no pick); the "walk-forward threshold" lines choose the threshold for season s from seasons < s only (best ROI with >= 300 bets), and always land on
the largest or second-largest grid threshold, which is the artifact of selecting more extreme prices. 2023 (season_year; 2023-24) is the season `engine_eval` tuned the shipped constants on (against the BOX SCORE, not market lines), so the cleanest market reads are 2021-22, 2022-23 (before the tuning) and 2024-25, 2025-26 (its held-out test seasons).

## Coverage and data fingerprints

| item | value |
|---|---|
| git sha (HEAD) | `fafea3a2ba66ca31a594c5b3f776cfb7ff0cbca6` (working tree has other sessions' uncommitted changes; this study adds the 3 files below plus the loader fix) |
| consensus rows | 383,285 (2021-10-19 .. 2026-06-13 ET game dates); BP partitions 1,380 per market x 5 markets |
| consensus rows by stat | pts 81,903; reb 86,566; ast 80,317; threes 70,224; pra 64,275 |
| master (`player_boxscores_master.csv`) | sha256[:16] `88ad7b4fae634a6f`; 188,390 played non-preseason rows with minutes > 0; dates 2019-10-23 .. 2026-11-01; `last_refresh.json` 2026-10-31 (7 games) |
| joined and scored | 363,643 joined (94.9%); 362,777 priced by all variants; per season (2021..2025) 39,218 / 66,047 / 83,292 / 88,281 / 86,805 joined |
| dropped | postgame_quote 843; void_dnp (player did not play) 15,509; unresolved name 34; duplicate (same player, same UTC date, same stat: two events, back-to-back across the UTC midnight) 3,256; ambiguous 0; team mismatch 0; namesake 0; low_books 0 |
| engine switches | `{dvp_k: None, pool_gamma: 0, shot_gamma: 0.5, zinb: False, eta_scale: 0.4, eta_off: (steals, blocks), trk_ast: 0, trk_reb: 0}` |
| runtime | python 3.14.7, pandas 3.0.6 |

## Bugs found

1. **Live NBA loader never supplies an opponent (mlb_core/data/cockpit_form.py `_load_nba`, was line 392: `df["_opp"] = df["opp_abbr"] if "opp_abbr" in df.columns else None`).** The player box master has no `opp_abbr`
   (columns: game_id .. fouls), so `_opp` was None on all 30,661 live-loaded rows. Consequences measured on the real master through the real loader: `GameLogs.context("points").opp_factor` has 0 entries (opponent factor = 1.0 for everyone),
   and `TeamGames.expected_makes` returns None (no opponent-allowed series), so `shotmaking_factor` is 1.0: the shipped `shot_gamma = 0.5` was dead on the real board. `engine_eval.load_master` derives `_opp` from the two teams of each game_id, so
   every replay behind PR #147 / the walk-forward doc had both factors. `cockpit_form._hoopr_nba_rows` documents "`_opp` is left unset exactly as in the master ... the engine's opponent factor stays what it was" -- it was never on.
2. **Team-code mismatch, same area.** The master spells GSW / NOP / NYK / SAS / PHX as GS / NO / NY / SA / PHO; the context is keyed by `canon_team(opp)` (tricodes) and the live board passes tricodes, but `TeamGames` is keyed by the rows' raw
   `team_abbr`, so `expected_makes("GSW", ...)` is None while `("GS", ...)` works (verified), and the hoopR top-up rows already carry tricodes while master rows carry raw codes (one team split across two keys). `engine_eval` has the mirror problem: it passes raw codes
   to a context keyed by tricodes, so its opponent factor is silently 1.0 for those five opponents (this script folds codes before replaying).
   **Fix (minimal, tested):** `_load_nba` now folds `team_abbr` to the tricode (idempotent for rows that already have one) and, when no `opp_abbr` column exists, derives `_opp` from the two teams sharing a `game_id`; defensive when the columns are absent
   (`tests/test_edge_cockpit.py` fixtures). Test: `test_live_loader_derives_opponent_and_folds_team_codes`. **Effect on the live board: the opponent factor and the team-makes assist factor turn ON** (measured gain above: -0.0011 log loss, z -6.9). This changes live NBA projections; undeployed.
   `engine_eval.py` was NOT edited (its raw-code opponent miss for five teams remains; it only affects replays).
3. **Namesake / UTC-date collisions (not a bug, a known limit):** a player in a back-to-back whose first game tips after 8 pm ET and whose second tips before 7:30 pm ET has two master rows with the same UTC `date`; `TeamGames` merges the two games of that team-date into one and the as-of cut cannot separate them (no leak, slightly distorted team-game inputs). Those props are dropped (3,256).

## Tables

All tables below are generated by the script (`--out-md`), unedited. "d" columns are per-prop log-loss differences (negative = first-named is better).

All variants priced 362,777 props (of 363,643 joined; 1083 of 1083 game dates, every 1th); seasons 2021, 2022, 2023, 2024, 2025 (season = start year; 2023 is the season engine_eval tuned the shipped constants on).

### 1. Log loss per stat and season: engine vs old engine vs market

`d new-old` and `d new-mkt` are mean per-prop log-loss differences (NEGATIVE = the shipped engine is better), SE clustered by game date. `c` = blend coefficient of the engine on top of the market (y ~ a + b logit(mkt) + c (logit(engine) - logit(mkt)), date-clustered t); c > 0 means information beyond the market. Both engines are priced on the SAME rows.


#### ORACLE absences (leaks the game being priced): `ship` vs `old`

| stat | season | n | LL market | LL new | LL old | d new-old (SE) | d new-mkt (SE) | c new (t) | c old (t) |
|---|---|---|---|---|---|---|---|---|---|
| points | all | 77,513 | 0.6921 | 0.7346 | 0.7403 | -0.0057 (0.0005) | +0.0425 (0.0014) | +0.03 (+2.8) | +0.04 (+3.7) |
| points | 2021 | 9,156 | 0.6915 | 0.7168 | 0.7231 | -0.0062 (0.0014) | +0.0254 (0.0030) | +0.00 (+0.1) | +0.03 (+0.6) |
| points | 2022 | 13,851 | 0.6928 | 0.7209 | 0.7290 | -0.0081 (0.0011) | +0.0281 (0.0028) | +0.05 (+1.5) | +0.06 (+1.8) |
| points | 2023 | 17,055 | 0.6920 | 0.7345 | 0.7403 | -0.0057 (0.0010) | +0.0425 (0.0028) | +0.05 (+2.3) | +0.06 (+2.8) |
| points | 2024 | 17,966 | 0.6917 | 0.7426 | 0.7469 | -0.0044 (0.0010) | +0.0509 (0.0035) | +0.01 (+0.5) | +0.02 (+0.8) |
| points | 2025 | 19,485 | 0.6923 | 0.7454 | 0.7502 | -0.0048 (0.0010) | +0.0531 (0.0030) | +0.02 (+1.4) | +0.03 (+1.8) |
| rebounds | all | 81,987 | 0.6876 | 0.7045 | 0.7066 | -0.0021 (0.0004) | +0.0169 (0.0009) | +0.14 (+9.6) | +0.15 (+10.0) |
| rebounds | 2021 | 13,181 | 0.6886 | 0.6977 | 0.7014 | -0.0036 (0.0009) | +0.0091 (0.0021) | +0.25 (+5.7) | +0.23 (+5.7) |
| rebounds | 2022 | 14,386 | 0.6872 | 0.6993 | 0.7007 | -0.0014 (0.0009) | +0.0120 (0.0018) | +0.17 (+4.4) | +0.20 (+5.0) |
| rebounds | 2023 | 17,197 | 0.6875 | 0.7044 | 0.7060 | -0.0016 (0.0009) | +0.0168 (0.0018) | +0.14 (+4.6) | +0.14 (+4.5) |
| rebounds | 2024 | 18,172 | 0.6858 | 0.7084 | 0.7102 | -0.0018 (0.0010) | +0.0225 (0.0022) | +0.08 (+2.6) | +0.09 (+3.2) |
| rebounds | 2025 | 19,051 | 0.6887 | 0.7096 | 0.7119 | -0.0024 (0.0008) | +0.0208 (0.0019) | +0.14 (+5.0) | +0.13 (+4.6) |
| assists | all | 76,077 | 0.6833 | 0.7029 | 0.7060 | -0.0031 (0.0004) | +0.0195 (0.0009) | +0.10 (+6.3) | +0.10 (+6.6) |
| assists | 2021 | 11,896 | 0.6839 | 0.6996 | 0.7036 | -0.0040 (0.0010) | +0.0157 (0.0021) | +0.15 (+3.9) | +0.13 (+3.3) |
| assists | 2022 | 13,167 | 0.6826 | 0.6985 | 0.7031 | -0.0047 (0.0010) | +0.0159 (0.0021) | +0.14 (+3.3) | +0.14 (+3.6) |
| assists | 2023 | 16,720 | 0.6829 | 0.7044 | 0.7054 | -0.0010 (0.0008) | +0.0215 (0.0019) | +0.07 (+2.0) | +0.09 (+2.6) |
| assists | 2024 | 18,108 | 0.6822 | 0.7062 | 0.7094 | -0.0032 (0.0008) | +0.0240 (0.0020) | +0.04 (+1.2) | +0.04 (+1.4) |
| assists | 2025 | 16,186 | 0.6852 | 0.7034 | 0.7067 | -0.0032 (0.0008) | +0.0182 (0.0021) | +0.14 (+4.1) | +0.13 (+4.0) |
| threes | all | 66,481 | 0.6735 | 0.6798 | 0.6859 | -0.0061 (0.0005) | +0.0063 (0.0006) | +0.21 (+8.7) | +0.15 (+7.1) |
| threes | 2021 | 4,920 | 0.6823 | 0.6821 | 0.6861 | -0.0039 (0.0018) | -0.0001 (0.0019) | +0.39 (+3.8) | +0.34 (+3.8) |
| threes | 2022 | 13,372 | 0.6680 | 0.6735 | 0.6797 | -0.0062 (0.0013) | +0.0055 (0.0015) | +0.21 (+3.6) | +0.13 (+2.5) |
| threes | 2023 | 15,643 | 0.6726 | 0.6790 | 0.6844 | -0.0054 (0.0010) | +0.0064 (0.0013) | +0.22 (+4.6) | +0.13 (+3.2) |
| threes | 2024 | 16,734 | 0.6732 | 0.6828 | 0.6898 | -0.0070 (0.0011) | +0.0095 (0.0013) | +0.11 (+2.6) | +0.07 (+2.1) |
| threes | 2025 | 15,812 | 0.6764 | 0.6820 | 0.6884 | -0.0064 (0.0010) | +0.0056 (0.0014) | +0.25 (+5.2) | +0.20 (+4.9) |
| pra | all | 60,719 | 0.6924 | 0.7366 | 0.7463 | -0.0097 (0.0006) | +0.0442 (0.0016) | +0.04 (+3.2) | +0.04 (+3.7) |
| pra | 2022 | 11,126 | 0.6923 | 0.7229 | 0.7337 | -0.0108 (0.0014) | +0.0306 (0.0030) | +0.05 (+1.6) | +0.07 (+2.1) |
| pra | 2023 | 16,493 | 0.6923 | 0.7370 | 0.7452 | -0.0082 (0.0010) | +0.0447 (0.0030) | +0.04 (+2.0) | +0.05 (+2.5) |
| pra | 2024 | 17,130 | 0.6925 | 0.7451 | 0.7560 | -0.0109 (0.0011) | +0.0526 (0.0034) | +0.02 (+0.9) | +0.01 (+0.6) |
| pra | 2025 | 15,970 | 0.6925 | 0.7367 | 0.7458 | -0.0091 (0.0012) | +0.0442 (0.0031) | +0.04 (+2.0) | +0.05 (+2.4) |
| ALL | all | 362,777 | 0.6859 | 0.7114 | 0.7165 | -0.0051 (0.0003) | +0.0256 (0.0008) | +0.08 (+9.0) | +0.08 (+9.7) |
| ALL | 2021 | 39,153 | 0.6871 | 0.7008 | 0.7052 | -0.0044 (0.0007) | +0.0138 (0.0014) | +0.15 (+5.7) | +0.15 (+5.9) |
| ALL | 2022 | 65,902 | 0.6844 | 0.7024 | 0.7084 | -0.0060 (0.0006) | +0.0180 (0.0015) | +0.10 (+4.3) | +0.11 (+4.6) |
| ALL | 2023 | 83,108 | 0.6857 | 0.7123 | 0.7166 | -0.0044 (0.0006) | +0.0266 (0.0015) | +0.08 (+4.5) | +0.08 (+4.8) |
| ALL | 2024 | 88,110 | 0.6852 | 0.7172 | 0.7226 | -0.0054 (0.0006) | +0.0320 (0.0018) | +0.04 (+2.2) | +0.04 (+2.5) |
| ALL | 2025 | 86,504 | 0.6873 | 0.7165 | 0.7215 | -0.0051 (0.0006) | +0.0291 (0.0016) | +0.08 (+5.1) | +0.08 (+5.6) |

#### NO absence information (pre-news pricer): `ship_noabs` vs `old_noabs`

| stat | season | n | LL market | LL new | LL old | d new-old (SE) | d new-mkt (SE) | c new (t) | c old (t) |
|---|---|---|---|---|---|---|---|---|---|
| points | all | 77,513 | 0.6921 | 0.7407 | 0.7425 | -0.0018 (0.0001) | +0.0486 (0.0016) | +0.02 (+1.7) | +0.02 (+1.6) |
| points | 2021 | 9,156 | 0.6915 | 0.7236 | 0.7240 | -0.0004 (0.0003) | +0.0321 (0.0036) | -0.03 (-0.9) | -0.03 (-0.8) |
| points | 2022 | 13,851 | 0.6928 | 0.7279 | 0.7288 | -0.0008 (0.0002) | +0.0351 (0.0033) | +0.02 (+0.8) | +0.02 (+0.8) |
| points | 2023 | 17,055 | 0.6920 | 0.7393 | 0.7415 | -0.0022 (0.0002) | +0.0473 (0.0030) | +0.04 (+2.1) | +0.04 (+1.9) |
| points | 2024 | 17,966 | 0.6917 | 0.7494 | 0.7518 | -0.0024 (0.0002) | +0.0577 (0.0038) | -0.00 (-0.2) | -0.00 (-0.2) |
| points | 2025 | 19,485 | 0.6923 | 0.7510 | 0.7534 | -0.0024 (0.0002) | +0.0587 (0.0034) | +0.02 (+1.2) | +0.02 (+1.1) |
| rebounds | all | 81,987 | 0.6876 | 0.7079 | 0.7077 | +0.0002 (0.0000) | +0.0203 (0.0010) | +0.13 (+9.4) | +0.13 (+9.4) |
| rebounds | 2021 | 13,181 | 0.6886 | 0.7018 | 0.7017 | +0.0001 (0.0001) | +0.0132 (0.0024) | +0.22 (+5.7) | +0.22 (+5.7) |
| rebounds | 2022 | 14,386 | 0.6872 | 0.7017 | 0.7015 | +0.0002 (0.0001) | +0.0145 (0.0019) | +0.17 (+4.6) | +0.17 (+4.6) |
| rebounds | 2023 | 17,197 | 0.6875 | 0.7075 | 0.7072 | +0.0003 (0.0001) | +0.0199 (0.0020) | +0.13 (+4.4) | +0.13 (+4.4) |
| rebounds | 2024 | 18,172 | 0.6858 | 0.7123 | 0.7121 | +0.0002 (0.0001) | +0.0265 (0.0023) | +0.06 (+2.2) | +0.06 (+2.2) |
| rebounds | 2025 | 19,051 | 0.6887 | 0.7130 | 0.7128 | +0.0001 (0.0001) | +0.0242 (0.0021) | +0.13 (+4.9) | +0.13 (+4.8) |
| assists | all | 76,077 | 0.6833 | 0.7073 | 0.7073 | -0.0000 (0.0001) | +0.0239 (0.0010) | +0.09 (+5.7) | +0.08 (+5.3) |
| assists | 2021 | 11,896 | 0.6839 | 0.7066 | 0.7065 | +0.0001 (0.0003) | +0.0227 (0.0026) | +0.09 (+2.4) | +0.09 (+2.4) |
| assists | 2022 | 13,167 | 0.6826 | 0.7039 | 0.7039 | -0.0000 (0.0003) | +0.0213 (0.0024) | +0.12 (+3.1) | +0.11 (+2.9) |
| assists | 2023 | 16,720 | 0.6829 | 0.7078 | 0.7076 | +0.0001 (0.0003) | +0.0248 (0.0021) | +0.07 (+2.2) | +0.07 (+2.2) |
| assists | 2024 | 18,108 | 0.6822 | 0.7095 | 0.7097 | -0.0003 (0.0002) | +0.0273 (0.0023) | +0.04 (+1.2) | +0.03 (+0.9) |
| assists | 2025 | 16,186 | 0.6852 | 0.7074 | 0.7076 | -0.0001 (0.0002) | +0.0222 (0.0022) | +0.12 (+3.9) | +0.11 (+3.6) |
| threes | all | 66,481 | 0.6735 | 0.6812 | 0.6856 | -0.0044 (0.0005) | +0.0077 (0.0007) | +0.19 (+8.4) | +0.13 (+6.4) |
| threes | 2021 | 4,920 | 0.6823 | 0.6827 | 0.6852 | -0.0025 (0.0015) | +0.0004 (0.0023) | +0.37 (+3.8) | +0.29 (+3.0) |
| threes | 2022 | 13,372 | 0.6680 | 0.6747 | 0.6787 | -0.0040 (0.0012) | +0.0066 (0.0016) | +0.19 (+3.6) | +0.11 (+2.0) |
| threes | 2023 | 15,643 | 0.6726 | 0.6807 | 0.6850 | -0.0043 (0.0009) | +0.0081 (0.0014) | +0.20 (+4.4) | +0.12 (+2.8) |
| threes | 2024 | 16,734 | 0.6732 | 0.6846 | 0.6900 | -0.0054 (0.0010) | +0.0114 (0.0014) | +0.10 (+2.4) | +0.07 (+1.8) |
| threes | 2025 | 15,812 | 0.6764 | 0.6831 | 0.6874 | -0.0043 (0.0009) | +0.0067 (0.0015) | +0.23 (+5.0) | +0.20 (+4.9) |
| pra | all | 60,719 | 0.6924 | 0.7466 | 0.7511 | -0.0045 (0.0003) | +0.0542 (0.0019) | +0.02 (+1.6) | +0.01 (+1.4) |
| pra | 2022 | 11,126 | 0.6923 | 0.7338 | 0.7351 | -0.0013 (0.0006) | +0.0416 (0.0037) | +0.01 (+0.5) | +0.02 (+0.6) |
| pra | 2023 | 16,493 | 0.6923 | 0.7447 | 0.7490 | -0.0044 (0.0005) | +0.0523 (0.0034) | +0.03 (+1.7) | +0.03 (+1.4) |
| pra | 2024 | 17,130 | 0.6925 | 0.7556 | 0.7633 | -0.0077 (0.0007) | +0.0631 (0.0039) | -0.00 (-0.2) | -0.01 (-0.5) |
| pra | 2025 | 15,970 | 0.6925 | 0.7479 | 0.7513 | -0.0034 (0.0005) | +0.0554 (0.0038) | +0.02 (+1.2) | +0.03 (+1.4) |
| ALL | all | 362,777 | 0.6859 | 0.7164 | 0.7183 | -0.0019 (0.0001) | +0.0305 (0.0009) | +0.06 (+7.4) | +0.05 (+6.9) |
| ALL | 2021 | 39,153 | 0.6871 | 0.7060 | 0.7063 | -0.0003 (0.0002) | +0.0189 (0.0017) | +0.10 (+4.5) | +0.10 (+4.5) |
| ALL | 2022 | 65,902 | 0.6844 | 0.7076 | 0.7088 | -0.0012 (0.0003) | +0.0232 (0.0019) | +0.07 (+3.4) | +0.07 (+3.0) |
| ALL | 2023 | 83,108 | 0.6857 | 0.7164 | 0.7184 | -0.0020 (0.0002) | +0.0307 (0.0017) | +0.07 (+4.2) | +0.06 (+3.8) |
| ALL | 2024 | 88,110 | 0.6852 | 0.7224 | 0.7255 | -0.0030 (0.0003) | +0.0373 (0.0021) | +0.02 (+1.3) | +0.01 (+1.0) |
| ALL | 2025 | 86,504 | 0.6873 | 0.7215 | 0.7234 | -0.0019 (0.0002) | +0.0342 (0.0018) | +0.06 (+4.4) | +0.06 (+4.4) |

#### As production runs today: no opponent column, ORACLE absences: `ship_live` vs `old`

| stat | season | n | LL market | LL new | LL old | d new-old (SE) | d new-mkt (SE) | c new (t) | c old (t) |
|---|---|---|---|---|---|---|---|---|---|
| points | all | 77,513 | 0.6921 | 0.7357 | 0.7403 | -0.0046 (0.0005) | +0.0436 (0.0015) | +0.03 (+2.6) | +0.04 (+3.7) |
| points | 2021 | 9,156 | 0.6915 | 0.7169 | 0.7231 | -0.0061 (0.0015) | +0.0255 (0.0030) | +0.00 (+0.0) | +0.03 (+0.6) |
| points | 2022 | 13,851 | 0.6928 | 0.7215 | 0.7290 | -0.0075 (0.0011) | +0.0287 (0.0028) | +0.05 (+1.6) | +0.06 (+1.8) |
| points | 2023 | 17,055 | 0.6920 | 0.7356 | 0.7403 | -0.0046 (0.0010) | +0.0436 (0.0029) | +0.05 (+2.2) | +0.06 (+2.8) |
| points | 2024 | 17,966 | 0.6917 | 0.7433 | 0.7469 | -0.0036 (0.0011) | +0.0517 (0.0034) | +0.01 (+0.4) | +0.02 (+0.8) |
| points | 2025 | 19,485 | 0.6923 | 0.7475 | 0.7502 | -0.0027 (0.0011) | +0.0552 (0.0031) | +0.02 (+1.1) | +0.03 (+1.8) |
| rebounds | all | 81,987 | 0.6876 | 0.7049 | 0.7066 | -0.0018 (0.0004) | +0.0173 (0.0009) | +0.14 (+9.3) | +0.15 (+10.0) |
| rebounds | 2021 | 13,181 | 0.6886 | 0.6979 | 0.7014 | -0.0035 (0.0010) | +0.0093 (0.0021) | +0.24 (+5.5) | +0.23 (+5.7) |
| rebounds | 2022 | 14,386 | 0.6872 | 0.6995 | 0.7007 | -0.0012 (0.0010) | +0.0123 (0.0017) | +0.17 (+4.4) | +0.20 (+5.0) |
| rebounds | 2023 | 17,197 | 0.6875 | 0.7046 | 0.7060 | -0.0014 (0.0009) | +0.0170 (0.0018) | +0.14 (+4.8) | +0.14 (+4.5) |
| rebounds | 2024 | 18,172 | 0.6858 | 0.7079 | 0.7102 | -0.0022 (0.0010) | +0.0221 (0.0021) | +0.09 (+2.8) | +0.09 (+3.2) |
| rebounds | 2025 | 19,051 | 0.6887 | 0.7111 | 0.7119 | -0.0009 (0.0010) | +0.0223 (0.0020) | +0.11 (+4.1) | +0.13 (+4.6) |
| assists | all | 76,077 | 0.6833 | 0.7046 | 0.7060 | -0.0014 (0.0004) | +0.0213 (0.0009) | +0.07 (+4.1) | +0.10 (+6.6) |
| assists | 2021 | 11,896 | 0.6839 | 0.7002 | 0.7036 | -0.0034 (0.0012) | +0.0163 (0.0020) | +0.14 (+3.7) | +0.13 (+3.3) |
| assists | 2022 | 13,167 | 0.6826 | 0.6991 | 0.7031 | -0.0041 (0.0010) | +0.0165 (0.0022) | +0.13 (+3.0) | +0.14 (+3.6) |
| assists | 2023 | 16,720 | 0.6829 | 0.7058 | 0.7054 | +0.0004 (0.0009) | +0.0229 (0.0020) | +0.04 (+1.0) | +0.09 (+2.6) |
| assists | 2024 | 18,108 | 0.6822 | 0.7091 | 0.7094 | -0.0003 (0.0009) | +0.0269 (0.0020) | -0.02 (-0.6) | +0.04 (+1.4) |
| assists | 2025 | 16,186 | 0.6852 | 0.7061 | 0.7067 | -0.0006 (0.0009) | +0.0208 (0.0021) | +0.09 (+2.6) | +0.13 (+4.0) |
| threes | all | 66,481 | 0.6735 | 0.6804 | 0.6859 | -0.0055 (0.0006) | +0.0069 (0.0007) | +0.17 (+7.0) | +0.15 (+7.1) |
| threes | 2021 | 4,920 | 0.6823 | 0.6833 | 0.6861 | -0.0028 (0.0019) | +0.0011 (0.0020) | +0.33 (+3.2) | +0.34 (+3.8) |
| threes | 2022 | 13,372 | 0.6680 | 0.6734 | 0.6797 | -0.0063 (0.0014) | +0.0054 (0.0015) | +0.21 (+3.6) | +0.13 (+2.5) |
| threes | 2023 | 15,643 | 0.6726 | 0.6799 | 0.6844 | -0.0045 (0.0011) | +0.0073 (0.0014) | +0.16 (+2.9) | +0.13 (+3.2) |
| threes | 2024 | 16,734 | 0.6732 | 0.6832 | 0.6898 | -0.0066 (0.0012) | +0.0099 (0.0013) | +0.09 (+2.1) | +0.07 (+2.1) |
| threes | 2025 | 15,812 | 0.6764 | 0.6829 | 0.6884 | -0.0055 (0.0011) | +0.0065 (0.0014) | +0.21 (+4.3) | +0.20 (+4.9) |
| pra | all | 60,719 | 0.6924 | 0.7387 | 0.7463 | -0.0076 (0.0006) | +0.0462 (0.0017) | +0.03 (+2.5) | +0.04 (+3.7) |
| pra | 2022 | 11,126 | 0.6923 | 0.7240 | 0.7337 | -0.0097 (0.0015) | +0.0317 (0.0030) | +0.05 (+1.5) | +0.07 (+2.1) |
| pra | 2023 | 16,493 | 0.6923 | 0.7391 | 0.7452 | -0.0061 (0.0011) | +0.0467 (0.0031) | +0.04 (+1.7) | +0.05 (+2.5) |
| pra | 2024 | 17,130 | 0.6925 | 0.7465 | 0.7560 | -0.0095 (0.0012) | +0.0540 (0.0034) | +0.01 (+0.7) | +0.01 (+0.6) |
| pra | 2025 | 15,970 | 0.6925 | 0.7401 | 0.7458 | -0.0058 (0.0012) | +0.0475 (0.0032) | +0.03 (+1.3) | +0.05 (+2.4) |
| ALL | all | 362,777 | 0.6859 | 0.7126 | 0.7165 | -0.0040 (0.0003) | +0.0267 (0.0008) | +0.06 (+7.5) | +0.08 (+9.7) |
| ALL | 2021 | 39,153 | 0.6871 | 0.7012 | 0.7052 | -0.0040 (0.0008) | +0.0142 (0.0013) | +0.14 (+5.5) | +0.15 (+5.9) |
| ALL | 2022 | 65,902 | 0.6844 | 0.7029 | 0.7084 | -0.0056 (0.0007) | +0.0184 (0.0015) | +0.10 (+4.1) | +0.11 (+4.6) |
| ALL | 2023 | 83,108 | 0.6857 | 0.7134 | 0.7166 | -0.0032 (0.0006) | +0.0277 (0.0016) | +0.07 (+3.7) | +0.08 (+4.8) |
| ALL | 2024 | 88,110 | 0.6852 | 0.7182 | 0.7226 | -0.0044 (0.0007) | +0.0330 (0.0018) | +0.03 (+1.7) | +0.04 (+2.5) |
| ALL | 2025 | 86,504 | 0.6873 | 0.7185 | 0.7215 | -0.0030 (0.0006) | +0.0312 (0.0016) | +0.06 (+3.8) | +0.08 (+5.6) |

### 1b. What the missing opponent column cost (paired, same props)

`ship` (opponent codes present, what every replay assumed) minus `ship_live` (no opponent column, what the live loader fed the engine before the 2026-11-02 loader fix). Negative d = having the opponent information is better.

| stat | absences | n | d LL ship - ship_live (SE) | z |
|---|---|---|---|---|
| points | oracle | 77,513 | -0.00107 (0.00023) | -4.8 |
| points | none | 77,513 | -0.00109 (0.00024) | -4.5 |
| rebounds | oracle | 81,987 | -0.00036 (0.00019) | -1.9 |
| rebounds | none | 81,987 | -0.00036 (0.00020) | -1.9 |
| assists | oracle | 76,077 | -0.00174 (0.00031) | -5.5 |
| assists | none | 76,077 | -0.00172 (0.00032) | -5.4 |
| threes | oracle | 66,481 | -0.00058 (0.00022) | -2.6 |
| threes | none | 66,481 | -0.00056 (0.00022) | -2.5 |
| pra | oracle | 60,719 | -0.00204 (0.00034) | -6.0 |
| pra | none | 60,719 | -0.00212 (0.00037) | -5.7 |
| ALL | oracle | 362,777 | -0.00112 (0.00016) | -6.9 |
| ALL | none | 362,777 | -0.00113 (0.00017) | -6.5 |

### 1c. Calibration at the market line (engine `ship_noabs`; all scored props)

`bias` = mean engine mu - realised stat; `mu - line` = mean engine mu - market line; P(over) columns are means over the same props (realised over rate is the target; the market is the benchmark).

| stat | n | mean line | mean mu | mean y | bias | P(over) engine | P(over) market | realised over rate |
|---|---|---|---|---|---|---|---|---|
| points | 77,513 | 14.01 | 13.97 | 14.19 | -0.22 | 0.492 | 0.499 | 0.484 |
| rebounds | 81,987 | 5.08 | 5.07 | 5.16 | -0.09 | 0.449 | 0.497 | 0.477 |
| assists | 76,077 | 3.22 | 3.29 | 3.37 | -0.08 | 0.464 | 0.494 | 0.489 |
| threes | 66,481 | 1.65 | 1.73 | 1.75 | -0.02 | 0.454 | 0.485 | 0.464 |
| pra | 60,719 | 22.80 | 22.48 | 22.88 | -0.40 | 0.482 | 0.499 | 0.487 |

### 2. Flat-stake ROI (walk-forward blend; engine side when edge >= threshold)

`engine` = the engine's own P(over) (clipped 0.5%-99.5%); `walk-forward blend` = market-anchored blend with (a, b, c) fitted per stat on EARLIER seasons only (display rule of the live board); `line shopping` = the CONSENSUS probability alone with the same threshold and price (no model: pure best-quote selection); `vs both sides` = ROI minus betting both sides of the same props at the same prices. CIs: date-clustered bootstrap.


#### ship_noabs (no absence info): flat 1u at the **median-book** price, scored seasons 2022, 2023, 2024, 2025

| edge >= | engine ROI [95% CI] (n) | walk-forward blend ROI [CI] (n) | line shopping, NO model (n) | engine vs betting both sides |
|---|---|---|---|---|
| 2% | -3.7% [-4.3%, -3.2%] (n=174,944) | +3.0% [+1.1%, +4.9%] (n=9,824) | -16.1% (n=15) | +2.9% |
| 3% | -3.7% [-4.3%, -3.1%] (n=154,613) | +5.4% [+2.3%, +8.0%] (n=5,312) | -33.2% (n=6) | +2.9% |
| 4% | -3.5% [-4.1%, -2.9%] (n=136,214) | +4.6% [+1.1%, +8.1%] (n=2,855) | -100.0% (n=3) | +3.1% |
| 5% | -3.3% [-4.0%, -2.6%] (n=119,639) | +7.3% [-2.2%, +16.6%] (n=479) | -100.0% (n=3) | +3.2% |

Blind controls at this price: always Over -9.2% [-9.7%, -8.8%] (n=312,498); always Under -4.0% [-4.4%, -3.5%] (n=312,498).

#### ship_noabs (no absence info): flat 1u at the **best-book** price, scored seasons 2022, 2023, 2024, 2025

| edge >= | engine ROI [95% CI] (n) | walk-forward blend ROI [CI] (n) | line shopping, NO model (n) | engine vs betting both sides |
|---|---|---|---|---|
| 2% | +0.1% [-0.5%, +0.6%] (n=216,678) | +5.8% [+4.6%, +7.0%] (n=59,392) | +9.7% (n=19,521) | +2.8% |
| 3% | +0.1% [-0.5%, +0.7%] (n=193,402) | +9.2% [+7.4%, +10.8%] (n=34,478) | +15.9% (n=10,606) | +2.8% |
| 4% | +0.3% [-0.4%, +0.9%] (n=171,883) | +12.5% [+10.1%, +14.9%] (n=20,112) | +23.8% (n=6,190) | +2.9% |
| 5% | +0.6% [-0.2%, +1.3%] (n=151,901) | +17.8% [+14.1%, +21.7%] (n=11,583) | +33.9% (n=4,016) | +3.2% |

Blind controls at this price: always Over -5.6% [-6.1%, -5.2%] (n=312,498); always Under -0.5% [-1.0%, -0.0%] (n=312,498).

Fitted blend c (earlier seasons only) per stat and season: assists 2022: +0.09; assists 2023: +0.10; assists 2024: +0.09; assists 2025: +0.07; points 2022: -0.03; points 2023: +0.01; points 2024: +0.03; points 2025: +0.02; pra 2023: +0.01; pra 2024: +0.03; pra 2025: +0.01; rebounds 2022: +0.22; rebounds 2023: +0.19; rebounds 2024: +0.17; rebounds 2025: +0.13; threes 2022: +0.37; threes 2023: +0.23; threes 2024: +0.22; threes 2025: +0.18

- walk-forward threshold (median price, rule=engine): chosen per season {2023: 8%, 2024: 8%, 2025: 8%} -> out-of-sample ROI -3.5% [-4.4%, -2.6%] (n=68,859), vs both sides +3.1%
- walk-forward threshold (median price, rule=blend): chosen per season {2023: 4%, 2024: 5%, 2025: 5%} -> out-of-sample ROI +3.4% [-3.2%, +10.1%] (n=843), vs both sides +9.5%
- walk-forward threshold (best price, rule=engine): chosen per season {2023: 8%, 2024: 8%, 2025: 8%} -> out-of-sample ROI +1.3% [+0.4%, +2.3%] (n=88,806), vs both sides +3.5%
- walk-forward threshold (best price, rule=blend): chosen per season {2023: 5%, 2024: 8%, 2025: 8%} -> out-of-sample ROI +36.5% [+26.2%, +46.8%] (n=3,965), vs both sides +20.7%
- walk-forward threshold (best price, rule=line shopping): chosen per season {2023: 4%, 2024: 6%, 2025: 8%} -> out-of-sample ROI +53.8% [+37.3%, +70.2%] (n=2,348), vs both sides +25.8%

ship_noabs (no absence info): per stat / per season, edge >= 3%, median price (engine | blend | line shopping):

| slice | engine ROI (n) | blend ROI (n) | line shopping ROI (n) | always Over / Under (median price) |
|---|---|---|---|---|
| points | -5.3% [-6.3%, -4.4%] (n=36,300) | +1.2% [-15.1%, +17.2%] (n=85) | n=0 | -8.9% / -4.0% |
| rebounds | -1.6% [-2.6%, -0.7%] (n=35,095) | +5.0% [+1.7%, +8.5%] (n=2,604) | +100.5% (n=2) | -10.2% / -3.0% |
| assists | -4.3% [-5.4%, -3.3%] (n=31,224) | -1.8% [-19.0%, +13.4%] (n=125) | -100.0% (n=4) | -7.6% / -5.6% |
| threes | -1.7% [-2.7%, -0.5%] (n=24,194) | +6.3% [+2.8%, +9.6%] (n=2,495) | n=0 | -10.6% / -2.9% |
| pra | -5.2% [-6.3%, -4.2%] (n=27,800) | +11.1% [-100.0%, +66.7%] (n=3) | n=0 | -8.8% / -4.4% |
| season 2022 | -2.3% [-3.5%, -0.7%] (n=24,697) | +6.1% [+1.2%, +10.5%] (n=2,353) | n=0 | -10.0% / -3.0% |
| season 2023 | -3.3% [-4.6%, -2.0%] (n=42,146) | +7.4% [+1.4%, +12.4%] (n=1,362) | n=0 | -9.3% / -3.8% |
| season 2024 | -4.9% [-6.1%, -3.8%] (n=43,524) | +2.5% [-3.2%, +8.4%] (n=1,013) | n=0 | -8.7% / -4.7% |
| season 2025 | -3.6% [-4.6%, -2.5%] (n=44,246) | +3.0% [-5.1%, +11.4%] (n=584) | -33.2% (n=6) | -9.2% / -4.0% |

#### ship (oracle absences): flat 1u at the **median-book** price, scored seasons 2022, 2023, 2024, 2025

| edge >= | engine ROI [95% CI] (n) | walk-forward blend ROI [CI] (n) | line shopping, NO model (n) | engine vs betting both sides |
|---|---|---|---|---|
| 2% | -3.6% [-4.2%, -3.1%] (n=169,018) | +2.3% [+0.6%, +4.2%] (n=11,130) | -16.1% (n=15) | +3.0% |
| 3% | -3.6% [-4.1%, -3.0%] (n=147,970) | +4.0% [+1.3%, +6.7%] (n=6,021) | -33.2% (n=6) | +3.0% |
| 4% | -3.5% [-4.1%, -2.8%] (n=129,017) | +4.2% [+0.6%, +7.7%] (n=3,147) | -100.0% (n=3) | +3.1% |
| 5% | -3.4% [-4.0%, -2.7%] (n=112,491) | +7.8% [-1.0%, +15.8%] (n=563) | -100.0% (n=3) | +3.2% |

Blind controls at this price: always Over -9.2% [-9.7%, -8.8%] (n=312,498); always Under -4.0% [-4.4%, -3.5%] (n=312,498).

#### ship (oracle absences): flat 1u at the **best-book** price, scored seasons 2022, 2023, 2024, 2025

| edge >= | engine ROI [95% CI] (n) | walk-forward blend ROI [CI] (n) | line shopping, NO model (n) | engine vs betting both sides |
|---|---|---|---|---|
| 2% | +0.1% [-0.5%, +0.5%] (n=213,011) | +5.8% [+4.6%, +6.9%] (n=62,405) | +9.7% (n=19,521) | +2.8% |
| 3% | +0.3% [-0.3%, +0.9%] (n=188,737) | +8.6% [+7.0%, +10.3%] (n=36,225) | +15.9% (n=10,606) | +3.0% |
| 4% | +0.5% [-0.2%, +1.1%] (n=166,111) | +11.9% [+9.7%, +14.4%] (n=21,128) | +23.8% (n=6,190) | +3.1% |
| 5% | +0.7% [-0.1%, +1.3%] (n=145,606) | +17.3% [+14.1%, +21.1%] (n=12,033) | +33.9% (n=4,016) | +3.2% |

Blind controls at this price: always Over -5.6% [-6.1%, -5.2%] (n=312,498); always Under -0.5% [-1.0%, -0.0%] (n=312,498).

Fitted blend c (earlier seasons only) per stat and season: assists 2022: +0.15; assists 2023: +0.15; assists 2024: +0.11; assists 2025: +0.09; points 2022: +0.00; points 2023: +0.04; points 2024: +0.05; points 2025: +0.03; pra 2023: +0.05; pra 2024: +0.05; pra 2025: +0.03; rebounds 2022: +0.25; rebounds 2023: +0.21; rebounds 2024: +0.18; rebounds 2025: +0.15; threes 2022: +0.39; threes 2023: +0.25; threes 2024: +0.23; threes 2025: +0.19

- walk-forward threshold (median price, rule=engine): chosen per season {2023: 8%, 2024: 8%, 2025: 8%} -> out-of-sample ROI -3.4% [-4.4%, -2.5%] (n=63,474), vs both sides +3.2%
- walk-forward threshold (median price, rule=blend): chosen per season {2023: 5%, 2024: 5%, 2025: 5%} -> out-of-sample ROI +3.7% [-10.3%, +18.1%] (n=206), vs both sides +7.6%
- walk-forward threshold (best price, rule=engine): chosen per season {2023: 8%, 2024: 8%, 2025: 8%} -> out-of-sample ROI +1.3% [+0.4%, +2.4%] (n=83,203), vs both sides +3.5%
- walk-forward threshold (best price, rule=blend): chosen per season {2023: 6%, 2024: 8%, 2025: 8%} -> out-of-sample ROI +45.1% [+33.1%, +58.5%] (n=2,994), vs both sides +23.8%
- walk-forward threshold (best price, rule=line shopping): chosen per season {2023: 4%, 2024: 6%, 2025: 8%} -> out-of-sample ROI +53.8% [+37.3%, +70.2%] (n=2,348), vs both sides +25.8%

ship (oracle absences): per stat / per season, edge >= 3%, median price (engine | blend | line shopping):

| slice | engine ROI (n) | blend ROI (n) | line shopping ROI (n) | always Over / Under (median price) |
|---|---|---|---|---|
| points | -5.3% [-6.4%, -4.4%] (n=35,983) | -6.6% [-16.6%, +3.8%] (n=231) | n=0 | -8.9% / -4.0% |
| rebounds | -1.6% [-2.6%, -0.8%] (n=32,873) | +5.4% [+1.8%, +8.6%] (n=2,747) | +100.5% (n=2) | -10.2% / -3.0% |
| assists | -4.1% [-5.1%, -2.9%] (n=29,800) | +3.4% [-6.7%, +15.4%] (n=293) | -100.0% (n=4) | -7.6% / -5.6% |
| threes | -1.8% [-3.1%, -0.7%] (n=22,078) | +4.3% [+0.8%, +8.0%] (n=2,594) | n=0 | -10.6% / -2.9% |
| pra | -4.5% [-5.7%, -3.5%] (n=27,236) | -8.2% [-21.1%, +4.8%] (n=156) | n=0 | -8.8% / -4.4% |
| season 2022 | -2.8% [-4.0%, -1.3%] (n=23,206) | +4.0% [-0.2%, +8.3%] (n=2,643) | n=0 | -10.0% / -3.0% |
| season 2023 | -3.3% [-4.5%, -2.1%] (n=39,937) | +6.6% [+2.4%, +10.4%] (n=1,590) | n=0 | -9.3% / -3.8% |
| season 2024 | -4.4% [-5.7%, -3.1%] (n=42,298) | +1.7% [-3.3%, +7.9%] (n=1,153) | n=0 | -8.7% / -4.7% |
| season 2025 | -3.4% [-4.4%, -2.4%] (n=42,529) | +2.1% [-5.4%, +9.2%] (n=635) | -33.2% (n=6) | -9.2% / -4.0% |

#### ship_live_noabs (production inputs, no absence info): flat 1u at the **median-book** price, scored seasons 2022, 2023, 2024, 2025

| edge >= | engine ROI [95% CI] (n) | walk-forward blend ROI [CI] (n) | line shopping, NO model (n) | engine vs betting both sides |
|---|---|---|---|---|
| 2% | -4.1% [-4.7%, -3.5%] (n=173,511) | +2.8% [+0.8%, +4.8%] (n=8,972) | -16.1% (n=15) | +2.5% |
| 3% | -4.0% [-4.7%, -3.4%] (n=153,014) | +4.3% [+1.3%, +6.8%] (n=4,727) | -33.2% (n=6) | +2.6% |
| 4% | -4.0% [-4.7%, -3.3%] (n=134,808) | +4.7% [+1.2%, +8.2%] (n=2,479) | -100.0% (n=3) | +2.6% |
| 5% | -3.8% [-4.5%, -3.1%] (n=118,553) | +6.3% [-4.7%, +16.6%] (n=399) | -100.0% (n=3) | +2.8% |

Blind controls at this price: always Over -9.2% [-9.7%, -8.8%] (n=312,498); always Under -4.0% [-4.4%, -3.5%] (n=312,498).

#### ship_live_noabs (production inputs, no absence info): flat 1u at the **best-book** price, scored seasons 2022, 2023, 2024, 2025

| edge >= | engine ROI [95% CI] (n) | walk-forward blend ROI [CI] (n) | line shopping, NO model (n) | engine vs betting both sides |
|---|---|---|---|---|
| 2% | -0.4% [-1.0%, +0.1%] (n=215,287) | +5.9% [+4.8%, +7.1%] (n=58,032) | +9.7% (n=19,521) | +2.3% |
| 3% | -0.2% [-0.9%, +0.4%] (n=192,064) | +8.8% [+7.1%, +10.4%] (n=33,238) | +15.9% (n=10,606) | +2.4% |
| 4% | +0.0% [-0.7%, +0.7%] (n=170,393) | +12.3% [+9.7%, +14.9%] (n=19,204) | +23.8% (n=6,190) | +2.6% |
| 5% | +0.1% [-0.7%, +0.8%] (n=150,590) | +17.6% [+13.9%, +21.5%] (n=10,936) | +33.9% (n=4,016) | +2.6% |

Blind controls at this price: always Over -5.6% [-6.1%, -5.2%] (n=312,498); always Under -0.5% [-1.0%, -0.0%] (n=312,498).

Fitted blend c (earlier seasons only) per stat and season: assists 2022: +0.08; assists 2023: +0.09; assists 2024: +0.07; assists 2025: +0.05; points 2022: -0.03; points 2023: +0.01; points 2024: +0.03; points 2025: +0.01; pra 2023: +0.01; pra 2024: +0.02; pra 2025: +0.01; rebounds 2022: +0.21; rebounds 2023: +0.19; rebounds 2024: +0.17; rebounds 2025: +0.13; threes 2022: +0.32; threes 2023: +0.23; threes 2024: +0.19; threes 2025: +0.15

- walk-forward threshold (median price, rule=engine): chosen per season {2023: 8%, 2024: 6%, 2025: 6%} -> out-of-sample ROI -4.4% [-5.2%, -3.7%] (n=81,680), vs both sides +2.2%
- walk-forward threshold (median price, rule=blend): chosen per season {2023: 4%, 2024: 5%, 2025: 5%} -> out-of-sample ROI +3.2% [-4.0%, +10.8%] (n=741), vs both sides +9.3%
- walk-forward threshold (best price, rule=engine): chosen per season {2023: 8%, 2024: 8%, 2025: 8%} -> out-of-sample ROI +0.5% [-0.5%, +1.5%] (n=88,013), vs both sides +2.7%
- walk-forward threshold (best price, rule=blend): chosen per season {2023: 6%, 2024: 8%, 2025: 8%} -> out-of-sample ROI +46.9% [+34.3%, +60.7%] (n=2,871), vs both sides +24.6%
- walk-forward threshold (best price, rule=line shopping): chosen per season {2023: 4%, 2024: 6%, 2025: 8%} -> out-of-sample ROI +53.8% [+37.3%, +70.2%] (n=2,348), vs both sides +25.8%

ship_live_noabs (production inputs, no absence info): per stat / per season, edge >= 3%, median price (engine | blend | line shopping):

| slice | engine ROI (n) | blend ROI (n) | line shopping ROI (n) | always Over / Under (median price) |
|---|---|---|---|---|
| points | -5.3% [-6.3%, -4.3%] (n=36,453) | -6.2% [-22.0%, +11.1%] (n=88) | n=0 | -8.9% / -4.0% |
| rebounds | -1.9% [-2.8%, -1.0%] (n=34,865) | +4.2% [+0.4%, +8.2%] (n=2,572) | +100.5% (n=2) | -10.2% / -3.0% |
| assists | -5.1% [-6.3%, -4.0%] (n=30,382) | +2.7% [-17.4%, +27.5%] (n=76) | -100.0% (n=4) | -7.6% / -5.6% |
| threes | -2.5% [-3.7%, -1.5%] (n=23,375) | +4.8% [+0.8%, +9.4%] (n=1,988) | n=0 | -10.6% / -2.9% |
| pra | -5.2% [-6.5%, -4.0%] (n=27,939) | +11.1% [-100.0%, +66.7%] (n=3) | n=0 | -8.8% / -4.4% |
| season 2022 | -2.5% [-3.9%, -1.1%] (n=24,584) | +6.0% [+1.5%, +10.9%] (n=2,082) | n=0 | -10.0% / -3.0% |
| season 2023 | -3.6% [-4.8%, -2.3%] (n=41,751) | +5.6% [+0.8%, +10.5%] (n=1,219) | n=0 | -9.3% / -3.8% |
| season 2024 | -5.2% [-6.5%, -4.0%] (n=43,141) | +1.1% [-5.2%, +6.4%] (n=898) | n=0 | -8.7% / -4.7% |
| season 2025 | -4.2% [-5.2%, -3.2%] (n=43,538) | -0.5% [-7.7%, +8.2%] (n=528) | -33.2% (n=6) | -9.2% / -4.0% |

## Reading the tables

- **Section 1** (log loss): the engine's log loss is above the market in every cell; the order is market < ship < old. `c new` vs `c old` are equal within noise pooled (oracle +0.08 / +0.08; none +0.06 / +0.05) and per stat except threes (new higher: +0.21 vs +0.15 oracle; +0.19 vs +0.13 none).
  In 2024 c is lower than in other seasons for assists and points (market sharper there, or the engine's constants tuned on 2023 not transferring; not resolved here).
- **Section 1c**: the engine's mean mu is close to the market line and to the realised mean (bias -0.02 to -0.40), so the log-loss deficit is not a level bias. Its mean P(over) is below 0.5
  for rebounds / threes / assists (0.449 / 0.454 / 0.464) against a realised over rate of 0.477 / 0.464 / 0.489. That reads as calibration (NegBin shape / league-wide dispersion; the engine's P(over) spread vs the market's was not measured here), not information; the blend's fitted `b` is what absorbs it.
- **Section 2** (ROI): `engine` bets whenever its own probability beats the price by the threshold; because it disagrees with the market by far more than 2-5 points on most props the bet count barely moves with the threshold (175k -> 120k): the threshold is not selecting anything for the raw engine.
  The median-book price is about the consensus price, so the line-shopping control has almost no bets there (n <= 15): by construction there is nothing to shop. At the best price the control earns +9.7% / +15.9% / +23.8% / +33.9% at 2 / 3 / 4 / 5% edge (n = 19.5k .. 4.0k), more than the blend at every threshold; blind Under is -0.5% at the best price and -4.0% at the median price, blind Over -5.6% / -9.2%.
- The walk-forward blend at the best price shows +36% .. +47% (n about 3k) and the control +54%: this is the artifact, not evidence.

## What would change the verdict

Forward CLV from the banked ParlayAPI / BestBook snapshots with open and close prices per book and quote timestamps (pre-registered, restricted to the median-or-better price), on rebounds and threes where the information signal lives, with the line-shopping control subtracted. Until then
the blend is a display-only prior (and its constants should be cut to the measured c), the engine alone should not drive a stake, and "better than the old engine" means "better box-score predictor", not "better than the market".

## Reproduce

```
cd <worktree>; PY=/Users/lmaynor/mlb-betting/.venv/bin/python; S=<scratch dir>; B=gs://concrete-crow-445205-m4-mlb-data
mkdir -p $S/bp $S/nba $S/work && (cd $S/bp && gcloud storage cp -r "$B/Odds/bettingpros_nba/*" .) && (cd $S/nba && gcloud storage cp $B/NBA/player_boxscores_master.csv $B/NBA/games_master.csv .)
PYTHONPATH=. $PY -m scripts.nba_engine_vs_market --bp-dir $S/bp --master $S/nba/player_boxscores_master.csv --work-dir $S/work --workers 8 --out-md $S/tables.md --out-json $S/res.json   # ~6 min incl. consensus + extras (first run), 244 s replay
$PY -m pytest tests/test_nba_engine_vs_market.py tests/test_nba_props_hierarchical.py tests/test_daily_projections_live.py -q
```
`--sample-every N` prices every Nth game date (default 1 = all); `--reuse-rows` re-runs only the analysis from `$S/work/priced.parquet`. The master is a moving file (nightly refresh): the sha256 above identifies the one used.
