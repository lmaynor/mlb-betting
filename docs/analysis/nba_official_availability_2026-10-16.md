# NBA pre-game availability: the official injury report vs "who actually did not dress" (2026-10-16)

Follow-up to `nba_zip_review_2026-10-16.md`. Question: the opt-in availability features were trained on who actually did not dress
(an upper bound the live scorer can never reproduce). Does the OFFICIAL pre-game injury report -- the information the live path will
actually have -- recover that gain? Nothing here is evidence of edge: no NBA prop odds history exists.

## Answer in four lines

1. **Mostly, not entirely.** Features built from the pre-game official list (`Out` players, >= 30 min before tip) recover about 60% of the
   upper-bound gain: points +1.01% vs +1.64%, PRA +1.30% vs +2.23%, rebounds +0.61% vs +1.04%, assists +0.46% vs +0.81%, minutes +2.78%
   vs +4.74% (MAE reduction vs the default features; every 95% date-cluster CI excludes zero).
2. **Using more of the list helps a little.** Adding Doubtful/Questionable missing minutes and the player's own listed status
   (`list_full`) lifts it to +1.18% points / +1.58% PRA / +3.38% minutes, i.e. roughly 70% of the upper bound.
3. **A P(active)-weighted feature adds nothing beyond that.** `list_pactive` (expected missing minutes, P(active | status) fitted on prior
   seasons only) matches `list_full` within noise (+1.09% points, +1.50% PRA, +3.34% minutes). The statuses themselves already carry
   the signal; a separate state-aware minutes mixture (INACTIVE / LIMITED / NORMAL, as in the supplied props repo) was NOT built --
   this study found no evidence it would pay for itself on top of the list features.
4. **The remaining gap is real and is the honest live-vs-train cost**: the report cannot know a late scratch, a coach's decision, or a
   player ruled out after the last report. Treat the "history" numbers as an upper bound and the "list" numbers as the realistic gain.

## Caveats that change how to read it

* **Coverage is 64.3% of team-games** (13,150 of 20,448): a game counts as covered only when its matchup appears in a report stamped
  >= 30 min pre-tip, and the league lists only players, so a game where NEITHER team had an entry has no matchup line and is
  treated as UNKNOWN (NaN), not healthy. Coverage is therefore understated and the list variants are handicapped by NaNs the
  `history` variant does not have. 13 reports parse to zero player rows (empty-list days) and 6 stamps were never published.
* Test seasons are 2024 and 2025 only, trained from 2021 (the first season with banked reports): the baseline here is weaker than the
  2019-trained one in `nba_availability_study_2026-10-16.md`. Variants are compared on identical rows.
* The reports are hourly (to 2025-12) and quarter-hour (from 2026-01) stamps; the study uses the two daily stamps (~1:30 PM and ~5:30 PM
  ET). A game tipping before ~2 PM ET has no covering report and is left uncovered (NaN). A denser feed would help the live path.
* Accuracy of the MEAN model, conditional on the player playing -- exactly what a book line that voids on DNP needs. It does not price
  the probability of playing (see the P(played | status) table: Out 0.2%, Doubtful 2.1%, Questionable 50.1%, Probable 91.6%, Available
  77.1% -- "Available" with an injury reason 90%, with a G League reason 39%).
* Names resolve to ESPN ids for 99.1% of listed players (fail closed).

## Generated report (nba/props/official_availability.py)


Mean model, rolling origin (train <= T-2, early-stop T-1, refit <= T-1), regular-season rows of the test season,
ALL variants on the same rows (season_year >= 2021). ACCURACY, not edge: no NBA prop odds history.

Test seasons (start year): [2024, 2025]. Rows: 137,933. Reports banked: 2,109. Team-games covered by a report stamped >= 30 min pre-tip: 13,150 of 20,448 (64.3%); listed names resolved to ESPN ids: 99.1%.

| variant | what it is |
|---|---|
| v1 | shipped default features |
| history | + availability from who actually did not dress (the existing opt-in; an UPPER BOUND, not available live) |
| list | + the same 3 availability columns computed from the pre-game official list's `Out` players (live parity) |
| list_full | list + Doubtful/Questionable missing minutes + the player's own listed status |
| list_pactive | v1 + expected missing minutes = sum over listed rotation players of (1 - P(active | status)) x minutes; P(active) fitted on prior seasons only |

## MAE by stat (all test seasons, row-weighted); gain vs v1 with date-cluster 95% CI

| stat | variant | MAE | gain vs v1 | 95% CI |
|---|---|---|---|---|
| assists | v1 | 1.3339 | - | - |
| assists | history | 1.3230 | +0.81% | [+0.55%, +1.12%] |
| assists | list | 1.3277 | +0.46% | [+0.25%, +0.72%] |
| assists | list_full | 1.3260 | +0.59% | [+0.36%, +0.84%] |
| assists | list_pactive | 1.3263 | +0.57% | [+0.36%, +0.81%] |
| min | v1 | 4.8147 | - | - |
| min | history | 4.5865 | +4.74% | [+3.75%, +6.11%] |
| min | list | 4.6807 | +2.78% | [+2.07%, +3.75%] |
| min | list_full | 4.6519 | +3.38% | [+2.54%, +4.43%] |
| min | list_pactive | 4.6540 | +3.34% | [+2.48%, +4.45%] |
| points | v1 | 4.5568 | - | - |
| points | history | 4.4821 | +1.64% | [+1.26%, +2.11%] |
| points | list | 4.5110 | +1.01% | [+0.72%, +1.36%] |
| points | list_full | 4.5032 | +1.18% | [+0.85%, +1.57%] |
| points | list_pactive | 4.5072 | +1.09% | [+0.76%, +1.51%] |
| pra | v1 | 5.9756 | - | - |
| pra | history | 5.8427 | +2.23% | [+1.72%, +2.94%] |
| pra | list | 5.8977 | +1.30% | [+0.92%, +1.85%] |
| pra | list_full | 5.8810 | +1.58% | [+1.16%, +2.17%] |
| pra | list_pactive | 5.8861 | +1.50% | [+1.02%, +2.10%] |
| rebounds | v1 | 1.8865 | - | - |
| rebounds | history | 1.8668 | +1.04% | [+0.71%, +1.49%] |
| rebounds | list | 1.8751 | +0.61% | [+0.36%, +0.91%] |
| rebounds | list_full | 1.8715 | +0.79% | [+0.54%, +1.10%] |
| rebounds | list_pactive | 1.8735 | +0.69% | [+0.41%, +1.01%] |

## P(played | listed status), all banked seasons

| status | n | p_played |
|---|---|---|
| Available | 3775 | 0.7711 |
| Probable | 3240 | 0.9157 |
| Questionable | 9271 | 0.5012 |
| Doubtful | 1263 | 0.0206 |
| Out | 47279 | 0.0017 |

### by reason class

| status | reason_cat | n | p_played |
|---|---|---|---|
| Available | g_league | 623 | 0.3917 |
| Available | injury_illness | 2437 | 0.9015 |
| Available | injury_management | 1 | 1.0 |
| Available | not_with_team | 3 | 0.3333 |
| Available | other | 630 | 0.6365 |
| Available | personal | 24 | 0.7083 |
| Available | rest | 8 | 0.875 |
| Available | return_to_competition | 49 | 0.8776 |
| Doubtful | g_league | 150 | 0.0267 |
| Doubtful | injury_illness | 1066 | 0.0159 |
| Doubtful | injury_management | 1 | 0.0 |
| Doubtful | other | 21 | 0.2381 |
| Doubtful | personal | 6 | 0.0 |
| Doubtful | rest | 11 | 0.0 |
| Doubtful | return_to_competition | 8 | 0.0 |
| Out | g_league | 13927 | 0.005 |
| Out | injury_illness | 29231 | 0.0002 |
| Out | injury_management | 4 | 0.0 |
| Out | not_with_team | 787 | 0.0 |
| Out | other | 2210 | 0.0018 |
| Out | personal | 538 | 0.0 |
| Out | rest | 297 | 0.0 |
| Out | return_to_competition | 285 | 0.0035 |
| Probable | g_league | 102 | 0.6569 |
| Probable | injury_illness | 3079 | 0.925 |
| Probable | other | 37 | 0.8649 |
| Probable | personal | 10 | 0.8 |
| Probable | return_to_competition | 12 | 1.0 |
| Questionable | g_league | 869 | 0.191 |
| Questionable | injury_illness | 8122 | 0.534 |
| Questionable | injury_management | 3 | 1.0 |
| Questionable | not_with_team | 3 | 0.3333 |
| Questionable | other | 124 | 0.4919 |
| Questionable | personal | 53 | 0.4717 |
| Questionable | rest | 17 | 0.1765 |
| Questionable | return_to_competition | 80 | 0.6375 |

## Per season

| stat | season | variant | n | mae | rmse | bias | r2 | mae_gain_vs_v1 | ci_lo | ci_hi | gain_pct |
|---|---|---|---|---|---|---|---|---|---|---|---|
| assists | 2024 | v1 | 25723 | 1.3314 | 1.809 | -0.0114 | 0.5255 |  |  |  |  |
| assists | 2024 | history | 25723 | 1.3206 | 1.7896 | 0.0222 | 0.5356 | 0.0108 | 0.0074 | 0.0146 | 0.8122 |
| assists | 2024 | list | 25723 | 1.3258 | 1.7988 | 0.0151 | 0.5308 | 0.0056 | 0.0027 | 0.0086 | 0.4208 |
| assists | 2024 | list_full | 25723 | 1.3246 | 1.7967 | 0.0172 | 0.5319 | 0.0068 | 0.0038 | 0.0098 | 0.5112 |
| assists | 2024 | list_pactive | 25723 | 1.325 | 1.7979 | 0.0145 | 0.5313 | 0.0064 | 0.0037 | 0.0091 | 0.4812 |
| assists | 2025 | v1 | 26050 | 1.3363 | 1.811 | -0.0124 | 0.5036 |  |  |  |  |
| assists | 2025 | history | 26050 | 1.3254 | 1.7933 | 0.0079 | 0.5132 | 0.0109 | 0.0074 | 0.0153 | 0.8176 |
| assists | 2025 | list | 26050 | 1.3296 | 1.7999 | 0.0036 | 0.5097 | 0.0067 | 0.0038 | 0.0107 | 0.5033 |
| assists | 2025 | list_full | 26050 | 1.3274 | 1.7963 | 0.0091 | 0.5116 | 0.0089 | 0.0057 | 0.0127 | 0.6675 |
| assists | 2025 | list_pactive | 26050 | 1.3276 | 1.7971 | 0.0042 | 0.5111 | 0.0087 | 0.0058 | 0.0125 | 0.6538 |
| min | 2024 | v1 | 25723 | 4.8397 | 6.3269 | 0.0302 | 0.648 |  |  |  |  |
| min | 2024 | history | 25723 | 4.5945 | 5.9864 | 0.2047 | 0.6849 | 0.2452 | 0.1909 | 0.3073 | 5.0669 |
| min | 2024 | list | 25723 | 4.6997 | 6.1486 | 0.1777 | 0.6676 | 0.14 | 0.1054 | 0.1774 | 2.8926 |
| min | 2024 | list_full | 25723 | 4.6672 | 6.1038 | 0.2092 | 0.6724 | 0.1726 | 0.1317 | 0.2162 | 3.5653 |
| min | 2024 | list_pactive | 25723 | 4.6667 | 6.1001 | 0.2083 | 0.6728 | 0.173 | 0.1315 | 0.2201 | 3.5738 |
| min | 2025 | v1 | 26050 | 4.79 | 6.2394 | 0.1302 | 0.6216 |  |  |  |  |
| min | 2025 | history | 26050 | 4.5787 | 5.9571 | 0.2914 | 0.6551 | 0.2113 | 0.1701 | 0.2813 | 4.4114 |
| min | 2025 | list | 26050 | 4.6619 | 6.0626 | 0.2679 | 0.6428 | 0.1281 | 0.0944 | 0.1841 | 2.6736 |
| min | 2025 | list_full | 26050 | 4.6368 | 6.0272 | 0.2742 | 0.6469 | 0.1532 | 0.1128 | 0.2099 | 3.1982 |
| min | 2025 | list_pactive | 26050 | 4.6414 | 6.0294 | 0.2829 | 0.6466 | 0.1486 | 0.1073 | 0.2081 | 3.1021 |
| points | 2024 | v1 | 25723 | 4.576 | 5.9804 | -0.1412 | 0.5377 |  |  |  |  |
| points | 2024 | history | 25723 | 4.4999 | 5.8817 | -0.045 | 0.5528 | 0.0761 | 0.0602 | 0.0939 | 1.6641 |
| points | 2024 | list | 25723 | 4.5333 | 5.9296 | -0.0618 | 0.5455 | 0.0427 | 0.0313 | 0.0544 | 0.9336 |
| points | 2024 | list_full | 25723 | 4.5271 | 5.9192 | -0.0586 | 0.5471 | 0.0489 | 0.0375 | 0.0612 | 1.0689 |
| points | 2024 | list_pactive | 25723 | 4.5309 | 5.9186 | -0.0461 | 0.5472 | 0.0451 | 0.0329 | 0.0596 | 0.9858 |
| points | 2025 | v1 | 26050 | 4.5378 | 5.9375 | -0.1212 | 0.5289 |  |  |  |  |
| points | 2025 | history | 26050 | 4.4646 | 5.8387 | -0.0255 | 0.5444 | 0.0732 | 0.0548 | 0.0986 | 1.6133 |
| points | 2025 | list | 26050 | 4.489 | 5.8718 | -0.0448 | 0.5392 | 0.0488 | 0.0346 | 0.0692 | 1.0763 |
| points | 2025 | list_full | 26050 | 4.4795 | 5.8593 | -0.033 | 0.5412 | 0.0583 | 0.0403 | 0.0818 | 1.285 |
| points | 2025 | list_pactive | 26050 | 4.4838 | 5.8638 | -0.022 | 0.5405 | 0.0541 | 0.0367 | 0.0778 | 1.1912 |
| pra | 2024 | v1 | 25723 | 6.0081 | 7.7436 | -0.2001 | 0.6005 |  |  |  |  |
| pra | 2024 | history | 25723 | 5.8707 | 7.5614 | -0.0177 | 0.6191 | 0.1374 | 0.1074 | 0.1753 | 2.2871 |
| pra | 2024 | list | 25723 | 5.9326 | 7.6522 | -0.0543 | 0.6099 | 0.0755 | 0.0564 | 0.0973 | 1.2567 |
| pra | 2024 | list_full | 25723 | 5.915 | 7.6242 | -0.0422 | 0.6127 | 0.093 | 0.0722 | 0.1172 | 1.5484 |
| pra | 2024 | list_pactive | 25723 | 5.9222 | 7.6259 | -0.0326 | 0.6125 | 0.0859 | 0.0619 | 0.1133 | 1.429 |
| pra | 2025 | v1 | 26050 | 5.9436 | 7.6681 | -0.0923 | 0.5866 |  |  |  |  |
| pra | 2025 | history | 26050 | 5.8151 | 7.4951 | 0.0624 | 0.605 | 0.1286 | 0.0977 | 0.1757 | 2.1635 |
| pra | 2025 | list | 26050 | 5.8633 | 7.5551 | 0.0433 | 0.5987 | 0.0804 | 0.0536 | 0.1236 | 1.3525 |
| pra | 2025 | list_full | 26050 | 5.8473 | 7.5318 | 0.0773 | 0.6011 | 0.0963 | 0.0664 | 0.1416 | 1.6204 |
| pra | 2025 | list_pactive | 26050 | 5.8505 | 7.5373 | 0.0623 | 0.6006 | 0.0931 | 0.0605 | 0.1371 | 1.5669 |
| rebounds | 2024 | v1 | 25723 | 1.9041 | 2.5104 | -0.049 | 0.4765 |  |  |  |  |
| rebounds | 2024 | history | 25723 | 1.8784 | 2.4802 | -0.0239 | 0.4891 | 0.0257 | 0.0187 | 0.0344 | 1.3493 |
| rebounds | 2024 | list | 25723 | 1.8898 | 2.4934 | -0.0278 | 0.4836 | 0.0142 | 0.0094 | 0.0188 | 0.7466 |
| rebounds | 2024 | list_full | 25723 | 1.885 | 2.4882 | -0.0301 | 0.4858 | 0.0191 | 0.0141 | 0.0239 | 1.0025 |
| rebounds | 2024 | list_pactive | 25723 | 1.8872 | 2.4908 | -0.0221 | 0.4847 | 0.0169 | 0.0115 | 0.0224 | 0.8865 |
| rebounds | 2025 | v1 | 26050 | 1.8692 | 2.4555 | 0.0165 | 0.452 |  |  |  |  |
| rebounds | 2025 | history | 26050 | 1.8554 | 2.434 | 0.0446 | 0.4616 | 0.0138 | 0.0083 | 0.0218 | 0.7371 |
| rebounds | 2025 | list | 26050 | 1.8605 | 2.4414 | 0.0435 | 0.4583 | 0.0087 | 0.0042 | 0.0154 | 0.4658 |
| rebounds | 2025 | list_full | 26050 | 1.8583 | 2.438 | 0.0413 | 0.4598 | 0.0109 | 0.0061 | 0.0176 | 0.5843 |
| rebounds | 2025 | list_pactive | 26050 | 1.86 | 2.4388 | 0.0458 | 0.4595 | 0.0092 | 0.0039 | 0.0157 | 0.4917 |
