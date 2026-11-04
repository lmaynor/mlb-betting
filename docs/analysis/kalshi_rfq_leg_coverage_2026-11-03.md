# Kalshi RFQ combo legs: why ~85% of legs have no fair value (2026-11-03)

Status: measurement only. Nothing here is a trading result. Companion to `kalshi_rfq_combo_lay_2026-10-25.md`.

## 1. Summary

In a point-in-time replay of 169,186 real settled Kalshi sports combos (1,248,194 leg occurrences, 2026-10-16..11-01), **14.7% of legs
could be priced and 85.3% could not**. The 85% is not one problem. It splits into four unrelated causes, and only the last two are
things the pricer or the feeds can fix quickly:

| # | Cause | Share of all legs | Nature |
|---|---|---|---|
| 1 | Sport / series we have no price source for (NCAAF, soccer, MLB props, period and team totals, specials) | **37.8%** | Missing data source. By design. |
| 2 | No fresh banked snapshot at the combo's time, or the game had started (`NO_SNAPSHOT`, `STARTED`) | **28.5%** | Mostly a **replay artifact**: the history was not archived densely enough. A live daemon reading the latest feed does not hit most of this. |
| 3 | Game-level line missing (`no_game_line`) | 9.3% | NFL game lines were never banked; NBA/NHL only banked since 10-15. Partly fixed live by the 2026-11-03 change. |
| 4 | Player-prop gaps (`no_rung`, `no_stat`, `no_game`, `no_player`) | 9.6% | Books quote fewer rungs / players / games than Kalshi lists. Partly fixable. |

Two framing points matter more than the headline number:

* **Strip out the replay artifact (cause 2) and the base is 892,894 legs, of which 20.6% are priced.** The honest "live-addressable"
  coverage is about one leg in five, and 41% of that base is sports we do not cover at all.
* **Leg coverage is the wrong unit; combos are priced whole.** Only **4.0% of combos have every leg priced** and **41.3% contain at
  least one leg from a sport we do not cover**. Combos average 7.4 legs (median 5; 22% have 10+), so even a 50% per-leg hit rate would
  leave almost every combo with a hole. That is why coverage, not risk, is the binding limit (as the demo tape already showed).

## 2. Method and what the numbers are

* **Combos:** the public-API pull from the earlier replay session (`combos_full.jsonl`): 202,724 combo markets, **169,186 traded with a
  result**, created 2026-10-16..11-01. Every one of the 102 four-hour pull windows hit the page cap, so this is a **sample**, not the
  population. Counts below are leg occurrences (a leg in 1,000 combos counts 1,000 times), so heavily repeated retail legs dominate.
* **Pricing book:** the same one the daemon uses, built point-in-time: NFL sweep cycle rows (256 snapshots), raw all-book NFL / NBA /
  NHL prop payloads, and (new, 2026-11-03) the banked NBA / NHL game-line snapshots. 732 snapshots in total. A combo is priced from the
  newest snapshot stamped **before** its creation time and at most 120 minutes old, and never for a game that had already started.
* **Outcome of one leg** (from `ev.kalshi_rfq_replay.audit_legs`): `HIT_exact` / `HIT_curve` (priced; curve = interpolated or
  extrapolated, `pooled=True`), `NO_SNAPSHOT`, `STARTED`, or `MISS_<why>` from the fair book's own `why_unmapped`.
* **Caveats.** (a) `KXNFLRECYDS`, `KXNFLREC` etc. show some `MISS_unknown_series`: for a registered prop series that means the sport's
  prop index was absent at that moment (it falls through to the game-series check), i.e. it is really cause 2 mislabelled; I did not
  re-label it. (b) "Sport not covered" is judged by the pricer's own table (NFL / NBA / NHL only), not by whether a source exists
  somewhere. (c) The `kind` split by series suffix puts period and team totals (`KXNBA1QTOTAL`, `KXNFL1HTOTAL`) under "total".

## 3. Where the 85% goes

All 1,248,194 leg occurrences:

| Outcome | Legs | Share |
|---|---:|---:|
| HIT_curve (priced, interpolated / extrapolated) | 121,912 | 9.8% |
| HIT_exact (priced, exact rung) | 61,816 | 5.0% |
| **Priced total** | **183,728** | **14.7%** |
| MISS sport_not_covered | 367,436 | 29.4% |
| NO_SNAPSHOT | 328,243 | 26.3% |
| MISS no_game_line | 115,606 | 9.3% |
| MISS unknown_series | 105,240 | 8.4% |
| MISS no_rung | 39,753 | 3.2% |
| MISS no_stat | 37,882 | 3.0% |
| MISS no_game | 37,230 | 3.0% |
| STARTED | 27,057 | 2.2% |
| MISS no_player | 5,474 | 0.4% |
| MISS ambiguous_player / team_mismatch | 545 | <0.1% |

## 4. By league

| League group | Legs | Share of legs | Priced | not covered | unknown series | no snapshot | no game line | no rung | no stat | no game | started |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NFL | 456,883 | 36.6% | 30.3% | 2% | 6% | 24% | 15% | 3% | 7% | 8% | 2% |
| NBA | 281,846 | 22.6% | 14.0% | 6% | 7% | 48% | 10% | 7% | 1% | 0% | 6% |
| NCAA (football, basketball) | 241,729 | 19.4% | 0.0% | 88% | 2% | 10% | 0% | 0% | 0% | 0% | 0% |
| Soccer (UCL, EPL, Serie A, La Liga, Bundesliga) | 183,106 | 14.7% | 0.0% | 65% | 18% | 17% | 0% | 0% | 0% | 0% | 0% |
| NHL | 52,475 | 4.2% | 11.1% | 0% | 1% | 36% | 34% | 7% | 5% | 0% | 4% |
| MLB | 32,155 | 2.6% | 0.0% | 22% | 56% | 22% | 0% | 0% | 0% | 0% | 0% |

Reading it: **34.1% of all legs are NCAA or soccer and have a 0% hit rate by construction.** NFL props are the only family priced well
(38.6% of NFL prop legs). NBA props are 61% `NO_SNAPSHOT`. NFL game winners, spreads and totals are 0% priced because no history exists.

## 5. The four causes in detail

### 5.1 No price source for the sport or series (37.8%: 29.4% not covered + 8.4% unknown series)

`ev.kalshi_game_legs` and the prop index only know NFL, NBA and NHL (`_COVERED_LEAGUES`, `ADAPTERS`). Everything else is dropped on
purpose rather than priced from a wrong source.

* **NCAAF** is the biggest single block: `KXNCAAFGAME` 119k, `KXNCAAFSPREAD` 49k, `KXNCAAFTOTAL` 25k, `KXNCAAFTEAMTOTAL` 8k, `KXNCAAF1HTOTAL`
  7k legs. All NCAA series together are 19.4% of every leg (almost all football), so college football is a large share of retail combo flow.
* **Soccer:** `KXUCLGAME` 21k, `KXEPLGAME` 17k, `KXUCLTOTAL` 15k, `KXUCL1HTOTAL` 13k, `KXSERIEAGAME` 12k, `KXLALIGAGAME` 11k, `KXEPLTOTAL` 7k,
  plus `KXUCL1H` 13k and `KXUCLBTTS` 7k that are `unknown_series` (first-half and both-teams-to-score are not game-winner shapes).
* **MLB** (`KXMLBHRR` 8k etc.): mostly `unknown_series`. MLB is in its offseason from Nov 5 and the RFQ pricer never covered it.
* **Period and special markets on covered leagues:** `KXNBA1QTOTAL` 8k, `KXNBA1QSPREAD` 4k, `KXNBARACE` 9k (race to N points), `KXNBA1Q` 6k.
* Part of the 8.4% `unknown_series` is the sport-index mislabel from the caveat in section 2 (about 7.5k legs on `KXNFLRECYDS` / `KXNFLREC`
  alone); I did not measure the rest of the split.

What would fix it: a price source per sport. BestBook publishes only `nfl`, `nba`, `nhl` (verified by listing `Odds/bestbook_live/`),
so NCAAF and soccer would need ParlayAPI (credits) or another feed. I have **not** verified that any free source carries them. Pricing
them is also a modelling decision (soccer 3-way, college spreads with thin books).

### 5.2 No fresh snapshot, or the game had started (28.5%: 26.3% + 2.2%) -- mostly a replay artifact

`NO_SNAPSHOT` means nothing was stamped within 120 minutes before the combo. It is driven by **when we archived**, not by what the
feeds carry now:

* **19% of all `NO_SNAPSHOT` legs are combos created before 10-17**, when no NBA / NHL prop or game-line snapshot existed (banking
  started 10-15 / 10-16) and NFL sweep cycles were missing (the sweep was not running 10-06..10-11).
* NBA / NHL **props were banked about twice a day until ~10-29** (10-14x a day after), so a 120-minute window usually missed. NBA is 48%
  `NO_SNAPSHOT` overall and 61% on NBA props.
* Daily leg-level `NO_SNAPSHOT` ranges from about 0-4% (10-24, 10-25, 10-31, 11-01) to 70% (10-23). The days that priced well (10-22 42.6%, 10-25 35.7%, 10-26 34.1%)
  are the days with dense archives.
* `STARTED` (2.2%) is a correctness feature: a pre-game consensus is not a fair value for a live parlay, so those legs are refused.

A live daemon reads the latest feed, so it does not suffer the archive gaps; it suffers the staleness gate instead (section 7).

### 5.3 Game-level lines missing (9.3%)

* **NFL game winner / spread / total: 0% priced** (`no_game_line` 78% / 63% / 44%). The NFL sweep banks player props only and BestBook
  ladders are overwritten hourly, so there is nothing to replay. This is the largest fixable-in-principle block (NFL game legs are
  ~7.9% of legs).
* **NBA / NHL game legs are priced only after the banked game-line snapshots exist** (NBA winner 33.9%, NHL winner 24.4%; the rest
  `no_game_line` because the combo predates banking, or the market is a period / team total we do not price).
* The 2026-11-03 change prices these live from BestBook and the banked ParlayAPI feed: ~75% of Kalshi's open NFL game / spread / total
  markets on 2026-11-03. That is **not visible in this replay**, because history before today does not exist for NFL game lines.

### 5.4 Player-prop gaps (9.6%)

| Why | Legs | Main series | Meaning |
|---|---:|---|---|
| `no_rung` | 39.8k (3.2%) | NBA points (PROT), NFL rec yds, NHL totals | Kalshi lists every N+; books quote a few lines; the curve will not extrapolate past its anchors. |
| `no_stat` | 37.9k (3.0%) | `KXNFLTDPROT` 24k, `KXNFLTD` 8k, `KXNHLGOAL` 2k | Anytime TD / goals were not in the swept markets for most of this window. |
| `no_game` | 37.2k (3.0%) | NFL rec / pass / rush yds, anytime TD | Game outside the sweep horizon (about 40h) or not banked. |
| `no_player` | 5.5k (0.4%) | -- | Player not quoted by any book. |

Anytime TD (`player_anytime_td`) has been in the NFL sweep since 2026-11-01 with a one-sided de-vig, so live coverage there should be
better than this window shows. Unverified until a new replay or tape.

## 6. Combo-level view (why this matters more than the leg rate)

| Measure | Combos | Share of 169,186 |
|---|---:|---:|
| All legs priced | 6,776 | 4.0% |
| At least one leg priced | 39,360 | 23.3% |
| No snapshot at combo time | 29,900 | 17.7% |
| Contains a leg from an uncovered sport | 69,895 | 41.3% |
| Same-game (all legs one game) | -- | 34% |

Leg-count distribution: 2 legs 11.7%, 3 legs 14.0%, 4 legs 14.5%, 5 legs 10.9%, 6 legs 10.2%, 7 legs 6.7%, 8 legs 5.9%, 9 legs 4.4%,
**10+ legs 21.7%**. Mean 7.4, median 5.

In the replay (quote config, 76,246 combos with at least one banked leg): 55% reject as `REJ_COMBO_LEG_UNVALIDATED` (too many
unpriced legs), 19.6% as `REJ_COMBO_NOT_LONGSHOT`, 7.4% as `REJ_SGP_SAME_PLAYER`, and 17.7% produce a quote. Bounded quotes (some legs
dropped as an upper bound) are the majority of what does price.

## 7. What a live daemon would see versus this replay

* Cause 2 largely disappears live (no archive gaps), but the **leg gate's 1500 s staleness limit** replaces it: BestBook refreshes
  hourly, so banked game rows pass for roughly the first 25 minutes of each hour. Props from BettingPros / BestBook are hourly too.
* Cause 3 for NFL is fixed live (BestBook ladders), unreplayable.
* Causes 1 and 4 are unchanged live. The 41% of combos with an uncovered-sport leg stay unpriceable (or bounded) until a source exists.

## 8. What would move the number, ranked

| Option | Legs it could touch | Effort | Caveat |
|---|---|---|---|
| Price NCAAF game / spread / total | up to ~18% | New feed (credits) + sd tables | No verified free source; college lines are thin. |
| Price soccer game / total | up to ~12% | New feed + 3-way handling | Same; 1H and BTTS are further special shapes. |
| Archive point-in-time BestBook + game lines | replay only (cause 2 and 3) | Small | Needed before any NFL game-leg replay. Forward only. |
| Widen game-line staleness gate | live only | One line | A risk gate: the owner's call. |
| Anytime TD / goals into sweeps and bank | ~2.6% | Done for NFL TD 2026-11-01 | Verify with a fresh replay. |
| Extend rung coverage (ladder fit beyond anchors) | ~3% | Modelling | Already partly done (`ev/prop_ladder.py`). |

## 9. What this means for the edge question

Coverage caps what can be tested, and what is testable is not encouraging yet. Replay of the sample with the new NBA / NHL game-line
source (quote config, no look-ahead; confidence intervals are 95% day-cluster bootstraps over 12-14 days):

| Population | n | Mean (c per contract) | 95% CI |
|---|---:|---:|---|
| Fully priced, lay at the traded price | 2,822 | +9.5 | [-3.2, +17.0] |
| ...same-game | 2,356 | +12.5 | [-2.4, +17.7] |
| ...cross-game | 466 | -5.4 | [-24.6, +7.7] |
| Bounded (some legs unpriced), lay at traded price | 27,688 | +0.6 | [-5.1, +6.6] |
| **Would-fill at OUR bid** | **450** | **-21.5** | **[-31.4, +4.5]** |
| ...excluding the 2 heaviest fill days | 119 | -4.5 | [-35.6, +15.4] |

* The headline lay result is positive but its interval includes zero, and it rests on same-game combos from a handful of days.
* **Where our bid would actually fill, the combos traded at a mean yes price of 0.535 against our p_true of 0.197, and 46.9% hit.** That is
  adverse selection: we fill only where we disagree with the market most, and the market was right. Volume-weighted it is -34.7c.
* 74% of fills fall on two days (10-18: 191, 10-25: 140), so the -21.5c is one or two slates, not a stable estimate.
* Adding NBA / NHL game legs raised fully-priced combos only from 2,653 to 2,822. The missing coverage is NCAAF, soccer and NFL game lines,
  not NBA / NHL.

Verdict: no edge established, and not refuted either. A trustworthy answer needs the production recorder's real RFQ flow, point-in-time
archives of every feed (so NFL game legs are replayable), and a held-out set of games.

## 10. Reproduce

```
# needs GCS read access (ADC) and the combos pull from the earlier session
GCS_BUCKET=<data bucket> python scripts/kalshi_rfq_replay.py audit  --combos combos_full.jsonl --cache-dir cache --top 60
GCS_BUCKET=<data bucket> python scripts/kalshi_rfq_replay.py replay --combos combos_full.jsonl --cache-dir cache --csv out.csv
```
The league / day / per-combo breakdowns in sections 4-6 came from a one-off script over `audit_legs`' own logic (per-combo and per-day
bookkeeping); it is not committed. Numbers are a sample of the settled flow, so treat them as order-of-magnitude.
