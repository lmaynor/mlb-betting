# MOD-04 Cluster (c) Playmaking (`mod04c/`)

Shadow-only playmaking prop models and data contracts for NBA and NHL assists.

**NO serving weights change in this build.** All outputs are shadow-only and gated per the November 25, 2026 pre-registration in the program log.

## Binding Note on Merge

`mod04c/` is intentionally isolated from Cluster (a)'s `mod04/` package during parallel branch development. On merge into `main`, `mod04c.feature_store` feature assembly rebinds to Cluster (a)'s shared backbone module.

## Modules & Contracts

| Module | Contract / Scope | Key Exports |
|---|---|---|
| `mod04c/feature_store.py` | **U1 leak-proof contract**: blocks `*_post`, `actual_*`, `outcome_*` columns; `filter_history` enforces `date < target_date`; `filter_snapshots` enforces `ts <= cutoff_utc`; pure `build_pregame_features` never touches `LabelStore`; `join_labels` is training/grading-only. | `validate_feature_frame`, `assert_clean`, `filter_history`, `filter_snapshots`, `build_pregame_features`, `LabelStore`, `join_labels` |
| `mod04c/dist_negbin.py` | **U2/U4 NegBin mechanics**: normalized discrete NegBin/Poisson PMF, `P(Y > line)`, independent count convolution, OOF dispersion grid fit, U4 logit blend clipped to `(1e-6, 1-1e-6)`, bisection rung shift preserving `alpha`. | `nb_pmf`, `pmf_p_over`, `convolve_pmf`, `fit_alpha_grid`, `blend_p`, `shift_mu_to_p_over` |
| `mod04c/prd04_guard.py` | **PRD-04 hard guardrail (NBA assists)**: eligible iff injury snapshot exists, `snapshot_ts_utc <= lock_utc`, age `<= 60m`, and zero unresolved `Questionable`/`Doubtful` statuses on the player or teammates. Excluded rows remain stamped in audit output with `p_model` / `p_served` nulled out. | `GuardResult`, `evaluate_guard`, `apply_guard`, `is_unresolved_status` |
| `mod04c/nhl_assist_split.py` | **C1 NHL assist split**: reconstructs primary (`assist1_id`) and secondary (`assist2_id`) assists from `NHL/shots/shots_{season}.csv` (excluding shootout `period_type == "SO"`) or raw PBP JSON, and verifies exact agreement with `NHL/skater_boxscores_master.csv`. | `split_from_shots`, `split_from_pbp`, `verify_split` |
| `mod04c/nba_assists.py` | **C2 NBA assists creation x conversion head**: single-pass pseudo-potential creation rate GBDT (`count:poisson`) multiplied by shrunk teammate zone-eFG conversion rate + OOF NegBin `alpha`; separates `vacated_handler_minutes` vs `finisher_fga_share_available`; exposes `GAME_ENV_FEATURES` for ablation; compares against EB and unified baselines in `walk_forward`. | `CreationConversionAssistsModel`, `build_nba_assist_features`, `eb_shrink`, `GAME_ENV_FEATURES`, `walk_forward` |
| `mod04c/nhl_assists.py` | **C3 NHL assists heads**: `PrimaryAssistsModel` (XGBoost `count:poisson` per-60 TOI), `SecondaryAssistsModel` (heavily shrunk EB rate with `k=40` games, no GBDT), convolved PMF (`convolve_pmf`), and `UnifiedAssistsModel` comparison via `walk_forward_compare` (`KEEP UNIFIED` if `split_ll >= unified_ll`). | `PrimaryAssistsModel`, `SecondaryAssistsModel`, `UnifiedAssistsModel`, `build_nhl_assist_features`, `walk_forward_compare` |
| `mod04c/passing.py` | **C4 NBA.com Stats Passing API banker & gate**: fetches `leaguedashptstats` (`PtMeasureType=Passing`) with research headers, banks to GCS, evaluates skip condition (`0.85 <= holdout_slope <= 1.15` and `delta_ll <= -0.0030`). | `fetch_passing`, `bank_passing`, `evaluate_skip_condition` |
| `mod04c/shadow.py` | **U3/U6 shadow serialization**: stamps truthful `engine`, market-free `p_model`, blended `p_served` (or `no_market_p` / `excluded_by_guard`), writes shadow JSON to GCS. | `make_shadow_rows`, `write_predictions_json` |

## Engine Labels

- NBA creation x conversion: `mod04_nba_assists_shadow_v1`
- NHL primary assists: `mod04_nhl_assists_primary_shadow_v1`
- NHL secondary assists: `mod04_nhl_assists_secondary_shadow_v1`
- NHL convolved split assists: `mod04_nhl_assists_split_shadow_v1`
- NHL unified baseline: `mod04_nhl_assists_unified_shadow_v1`

## GCS Keys & Verification Summary

- **C1 Assist Split**:
  - Output table: `NHL/assist_split/assist_split.csv`
  - Verification JSON: `NHL/assist_split/verification.json`
  - Coordinator-verified against `NHL/skater_boxscores_master.csv`: 2025 (`1,497` games, `0` player-game mismatches, `15,438 = 15,438`), 2026 (`446` games, `0` mismatches, `4,695 = 4,695`).
- **C4 Passing Banking**:
  - Key pattern: `NBA/passing/passing_{YYYY-MM-DD}.csv`
  - Skip condition (gate-time evaluation only; banking runs regardless): `0.85 <= holdout_slope <= 1.15` AND `delta_ll_vs_market <= -0.0030`.
- **Shadow Predictions**:
  - Key pattern: `EVSweep/mod04c/shadow/{family}/{YYYY-MM-DD}.json` (`family` in `{nba_assists, nhl_assists}`).

## CLI Scripts

- `scripts/mod04c_build_assist_split.py`
- `scripts/mod04c_bank_passing.py`
- `scripts/mod04c_score_shadow.py`
- `scripts/mod04c_walkforward_sanity.py`
