# Window 2 Build Notes — RES-12 RFQ Taker Window 2 Machinery (RUL-20261119)

## 1. Governing Documents
- Pre-registration (LOCKED): `~/workspace/beezy/rfq-window2-preregistration-2026-11-19.md`
- Ruling: `~/workspace/beezy/argon-rulings/RUL-20261119-rfq-taker-window2-verbatim.md` (RUL-20261119, Neon web lane)

## 2. Files Created and Modified
- **Modified**: `ev/combo_taker_scanner.py`
  - Fixed `roi_no_taker` denominator to be collateral-inclusive `(ask_no + fee_taker)` per RUL-20261119 Q5 and pre-registration §8 audit finding. Added citation comment.
- **Created**: `ev/combo_taker_window2.py`
  - Implements the complete pre-registered Window 2 Portfolio Architecture Package:
    - `Window2Config`: frozen dataclass containing all locked default parameters.
    - Family classification and `FamilyTrustBook`: tracking settled fires, distinct days, pnl, evaluated counts, regimes (`CIRCUIT_BROKEN`, `ACCRUING`, `TRUSTED`), and `tau` multipliers; `combo_family` weakest-link resolution.
    - `RoiCalibrator` and `fit_calibrator`: piecewise-linear ridge weighted least-squares with hinge at 0.030, Empirical-Bayes shrinkage across families, non-monotonicity quartile clamp at Q3, +50% output clamp, and cold-start sizing haircut.
    - `rotten_leg_v2`: shadow-only worst-leg stress test evaluating single-leg edge collapse (`EDGE_COLLAPSE`) and tail-ratio anomalies (`TAIL_RATIO_ANOMALY`).
    - `Window2Admission`: 60-second batching, greedy composite $S_i$ scoring, online Tier B 40% reservation prior to T-60m, 4-tier cap table (combo, shared-leg $35, player $50, game $150 & $\le 15$ combo-legs, daily pool $600), and Ledger B-shadow decision logging alongside Window 1 shadow evaluation.
- **Created**: `tests/test_combo_taker_window2.py`
  - All 13 pre-registered tests implemented and verified.
- **Created**: `WINDOW2_BUILD_NOTES.md`
  - Build notes and test execution verification.

## 3. Existing Test Fallout from Item 1 Denominator Fix
- **Fallout check**: 0 existing test expectations in `tests/test_tail_and_combo_taker.py` or `tests/test_kalshi_rfq.py` were broken or required numeric expectation updates.
  - In `tests/test_tail_and_combo_taker.py`, existing tests check boundary inequalities (`assert sig.roi_no_taker >= 0.030` in `test_blade3_fires_when_gates_clear` and `assert sig.roi_no_taker < 0.030` in `test_blade3_roi_and_edge_gates_block_bad_price`). Both remain valid under the collateral-inclusive denominator `(ask_no + fee_taker)`.
  - `tests/test_kalshi_rfq.py` does not import or test `combo_taker_scanner.py`.

## 4. Test Execution and Results
Command executed from worktree root:
```bash
~/workspace/.minutes-venv/bin/python -m pytest tests/test_combo_taker_window2.py tests/test_tail_and_combo_taker.py tests/test_kalshi_rfq.py -q
```

Results:
```
153 passed in 16.99s
```
Breakdown:
- `tests/test_combo_taker_window2.py`: 13 passed
  1. `test_tier_b_reservation_caps_at_40_percent_before_release`
  2. `test_shared_leg_cap_stops_pileup_at_35`
  3. `test_rotten_leg_v2_shadow_never_blocks`
  4. `test_rotten_leg_v2_condition_b`
  5. `test_calibration_clamps_at_plus_50_percent`
  6. `test_calibration_shrinks_explosion_rows`
  7. `test_circuit_breaker_excludes_family`
  8. `test_below_floor_family_deranked_and_tier_b_only`
  9. `test_composite_discount_second_identical_leg`
  10. `test_game_leg_count_cap`
  11. `test_daily_pool_cap`
  12. `test_window1_shadow_logged_and_legacy_defaults_untouched`
  13. `test_roi_denominator_includes_fee`
- `tests/test_tail_and_combo_taker.py`: 13 passed
- `tests/test_kalshi_rfq.py`: 127 passed

## 5. Fences and Invariants
- **Signal-only**: pure signals/decisions, no venue clients, no order placement, no network.
- **Zero execution imports**: no imports from `mlb.execution` (taker fee inline formula used).
- **Window 1 untouched**: `scan_combos` and `ComboLiabilityGuard` defaults ($75 player, $150 game, $15 combo) remain identical and frozen.
- **No commits**: worktree left dirty for review on branch `feat/rfq-taker-window2`.
