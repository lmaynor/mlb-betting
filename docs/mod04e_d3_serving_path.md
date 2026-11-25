# MOD-04 Cluster (e) D3 Serving-Path Design (Gate Stays Shut)

Status: **DESIGN ONLY -- D3 GATE REMAINS SHUT.**
This document specifies how the Argon D3 NFL hierarchical-engine gate operates today in `ev/daily_projections.py`, the exact code changes that would be required if a MOD-04e family passes its pre-registered walk-forward gate, and the approvals required before lifting the gate.

---

## (a) How `live_served_rows` / `is_hierarchical_nfl_row` drop hierarchical NFL rows today

In `ev/daily_projections.py`, NFL hierarchical rows are blocked unconditionally at **publication time** and **read time** regardless of `NFL_ENGINE` or `NFL_HIER_STATS`:

1. **Row-level engine stamping (`_row_engine` / `make_row`)**:
   - In `make_row` (`ev/daily_projections.py`), when `sport == "nfl"` and `_hl.stat_enabled(stat)` is true, `_price_hier(...)` computes `hier` via `nfl.models.hierarchical_live.price_row(...)`.
   - `_row_engine(extra, nb, hier)` stamps `"engine": ENGINE_NFL_HIER` (`"hierarchical_v1"`) on any row where `hier is not None`, and `"empirical_ewm_v2"` (`ENGINE`) on legacy EWM rows.
2. **Gate predicate (`is_hierarchical_nfl_row`)**:
   - `is_hierarchical_nfl_row(r)` returns `True` when `str(r.get("sport") or "") == "nfl" and "hierarchical" in str(r.get("engine") or "")`.
   - Because it checks substring `"hierarchical"` on `engine` for `sport == "nfl"`, it catches `"hierarchical_v1"`, `"empirical_ewm_v2+hierarchical_v1"`, and any future hierarchical engine identifier.
3. **Board assembly filter (`run_sport`)**:
   - In `run_sport("nfl", ...)`, after `order_and_cut(line_rows + rot)`, `served = live_served_rows(rows)` filters out every row matching `is_hierarchical_nfl_row(r)`.
   - Dropped count is recorded in `summary["hier_rows_gated"]`, and only `served` enters the published board payload and `ev.model_picks`.
4. **Serving read-path defense-in-depth (`get_daily_projections`)**:
   - `get_daily_projections(sport, ...)` also runs `rows = live_served_rows(payload.get("rows") or [])` before returning any precomputed or freshly computed payload, so stale or externally written payloads cannot leak hierarchical NFL rows.
5. **Default blend weight (`BLEND_C_BY_SPORT_STAT["nfl"]` / `blend_c`)**:
   - Per MOD-04e E0(iii), `BLEND_C_BY_SPORT_STAT["nfl"] = {"default": 0.00}`, so `blend_c("nfl", stat)` resolves to `0.00` for every NFL stat and `blend_p_over(...)` returns `p_mkt_over` unchanged.

---

## (b) Numbered change list for a gate-passed family

If a specific MOD-04e family clears its pre-registered walk-forward gate, **only** that family would be promoted via the following numbered changes (never a blanket lift across all NFL stats):

1. **Pre-registered verdict allowlist (`nfl/mod04e/` + `ev/daily_projections.py`)**:
   - Add a per-family allowlist file (e.g. `nfl/mod04e/promoted_families.json` or `MOD04E_PROMOTED_STATS`) keyed explicitly on the pre-registered gate verdict (season, holdout delta-LL, z-stat, slope, ECE, sample size `n`, verdict commit/date).
   - Add helper `is_mod04e_promoted_nfl_row(r)` / update `is_hierarchical_nfl_row(r)` in `ev/daily_projections.py` so `live_served_rows(rows)` passes rows **only** when `r["stat"]` is in the gate-passed allowlist and `r["engine"]` matches the validated engine id. Legacy `hierarchical_v1` rows for non-promoted stats remain dropped.
2. **Engine pricing and truthful row label (`make_row`, `_row_engine`, `engine_id`)**:
   - Wire `nfl.mod04e.model` scoring into a dedicated helper (e.g. `_price_mod04e(...)` alongside `_price_hier(...)`) inside `make_row` only for allowlisted `(sport="nfl", stat)` pairs behind a kill-switch env flag.
   - Update `_row_engine(...)` so promoted rows carry the truthful engine label `nfl.mod04e.model.ENGINE_ID` (`"mod04_nfl_gamescript_shadow_v1"`, or a promoted versioned tag if frozen at promotion) while unpromoted stats keep `"empirical_ewm_v2"`.
   - Update `engine_id("nfl")` to reflect the active promoted mix only when at least one family is allowlisted and enabled.
3. **Earned blend weight (`BLEND_C_BY_SPORT_STAT["nfl"]`, `blend_c`, `blend_p_over`)**:
   - Replace `"default": 0.00` with explicit per-family entries in `BLEND_C_BY_SPORT_STAT["nfl"]` (keeping `"default": 0.00`), setting `c` ONLY for the gate-passed family to its walk-forward earned value (and any fitted pre-blend calibrator in `mlb_core.risk.calibration` if required).
   - Keep `blend_p_over(...)` unchanged so unpromoted families stay at `c = 0.00` (`p_served == p_market`).
4. **Dual logging (`make_row` / `nfl.mod04e.shadow`)**:
   - Ensure every promoted NFL row logs both `p_over_model` (from the MOD-04e discrete PMF at `line`), `p_over_market` (`cons.p_over`), `p_over_blend` (`blend_p_over` with the earned `c`), `blend_c`, and `engine`, plus `run_kind` / `inactive_scratch` provenance for post-game grading parity.
5. **Summary & grading integration (`run_sport`, `ev.model_picks`)**:
   - Record per-engine counts (`mod04e_rows_served`, `hier_rows_gated`) in `run_sport` summary.
   - Keep `ev.model_picks` Official-tier eligibility gated per family until forward-CLV / paper-track criteria are met.

---

## (c) Required approvals before lifting the gate

The D3 gate stays shut in this build. Promoting any family from shadow to live serving requires **all** of the following on file:

1. **Pre-registered gate verdict on file**: Held-out walk-forward output (from `scripts/mod04e_walkforward.py`) demonstrating positive information beyond the BettingPros close (paired delta-LL < 0 with required z-threshold, recalibration slope within tolerance, adequate sample size `n`, and clean weather/inactive segmentation).
2. **Luc's explicit direction**: Explicit approval in chat/commit directive from Luc authorizing the specific `(family, earned_c)` promotion and env-flag activation.
3. **New Argon ruling if D3 terms change**: Any change to the Argon 2026-11-10 D3 terms themselves (such as altering the engine-gate semantics, changing the benchmark/CLV standard, or promoting outside the pre-registered per-family gate criteria) requires a new recorded Argon ruling before implementation.
