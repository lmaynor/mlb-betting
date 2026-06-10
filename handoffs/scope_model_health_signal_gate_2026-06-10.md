# Scope + requirements -- Model-health signal upgrade + rolling suppression gate

_Authored 2026-06-10 by Opus 4.8 for handoff to Sonnet. Read CONTEXT.md (s5, s11) and this file
before writing code. This task DOES touch production -- branch, test, deploy via deploy_service.sh._

---

## 0. Why

Two related defects surfaced while reviewing `/model-health`:

1. The `signal` label in `main.py` (model_health_handler, ~line 1030) is derived ONLY from
   `auc_model`. So HR reads "strong" (AUC 0.61) while bleeding ROI -20%, and 1IOU reads "moderate"
   (AUC 0.55) while massively overconfident (cal_err -0.26). The label hides calibration and P&L
   failures -- the exact failures we care about.

2. Broken systems (F5, NRFI, F1H sitting at AUC ~0.50 with cal_err -0.20 to -0.26) keep placing
   paper Kelly bets into their most-overconfident tail every slate. There is no mechanism to
   automatically stop a system from betting when its live performance has degraded; a human has to
   notice and hand-edit the runner. F5 already has a `LOG_ONLY_SYSTEMS` set (run_f5.py:392) but it
   is hardcoded and not performance-driven.

Two tasks, shared metric definitions. Task A is small (a diagnostics field). Task B is medium (a
closed-loop gate). They can ship in one PR or two -- author's call -- but Task B reuses metrics
that `monitor_performance.py` ALREADY computes.

---

## Task A -- calibration- and ROI-aware health verdict

### Current
`main.py` model_health_handler `_system_stats()` returns a `signal` field with this logic only:
```
strong   if auc >= 0.57
moderate if auc >= 0.53
weak     if auc >= 0.50
inverted if auc <  0.50
unknown  otherwise
```

### Requirement
Keep `signal` AS-IS for backward compatibility (it is consumed only inside main.py, but stay
additive anyway). ADD three fields to each system's stats dict:

- `flags`: list[str] -- any of: `"inverted"` (auc < 0.50), `"miscalibrated"` (|cal_err| > 0.10),
  `"negative_roi"` (roi < -10), `"underpowered"` (n < MIN_HEALTH_N), `"no_edge"`
  (0.50 <= auc < 0.53).
- `health`: str -- single composite verdict with this precedence (first match wins):
  `"underpowered"` (n < MIN_HEALTH_N) -> `"inverted"` (auc < 0.50) ->
  `"miscalibrated"` (|cal_err| > 0.10) -> `"no_edge"` (auc < 0.53) ->
  `"degraded"` (roi < -10 despite ok auc/cal) -> `"moderate"` (auc < 0.57) -> `"healthy"`.
- `recommended_action`: str -- short human string keyed off `health`, e.g.
  inverted -> "retrain (rank-ordering broken; calibrator cannot fix)",
  miscalibrated -> "recalibrate / check edge filter (overconfident point estimates)",
  no_edge -> "market efficient; keep log-only, do not chase",
  degraded -> "investigate pricing/variance; review per-book ROI",
  healthy -> "ok".

Thresholds as module constants (env-overridable like the monitor): `MIN_HEALTH_N = 20`,
`CAL_ERR_TOL = 0.10`, `ROI_FLOOR = -10`. Use the SAME numeric definitions as Task B (shared --
define once, import or duplicate with a comment pointing at the canonical copy).

Note: HR specifically will land as `degraded` (good AUC, slightly-neg cal, -20% ROI) -- that is the
correct, honest label (high-variance market / vig, not a broken model). Do not special-case it.

---

## Task B -- rolling-performance auto-suppression gate

### Goal
When a system's recent LIVE performance is structurally broken, automatically stop it from placing
staked bets (drop to log-only) until it recovers -- without a human edit, and reversibly.

### Reuse what exists
`runners/monitor_performance.py` already computes per-system rolling stats over the last
`ROLLING_WINDOW=30` settled bets: `_rolling_stats()` returns n, roi, hit_rate, avg model prob,
edge, AND auc (`_auc`, Mann-Whitney). It already runs daily 09:30 UTC via `mlb-monitor`. Add the
gate decision + state write here; do NOT build a new monitor.

`run_f5.py` already has the enforcement pattern: a `LOG_ONLY_SYSTEMS` set and a per-row `log_only`
flag that forces `stake = 0` while still logging the prediction (run_f5.py:392, 458, 507). Generalize
this from a hardcoded set to a dynamic, gate-file-driven check, and add the same hook to the other
runners (run_nrfi, run_k, run_hr, run_batter_hits, run_game).

### Gate decision (computed in monitor_performance)
A system is SUPPRESSED when, over >= `MIN_GATE_N` settled bets in the rolling window, ANY of:
- rolling AUC < 0.52, OR
- |cal_err| (rolling hit_rate - rolling avg_model_prob) > 0.12, OR
- rolling ROI < -20%.

Guards (mandatory -- prevent flapping and accidental shutdowns):
- `MIN_GATE_N = 30` settled bets minimum before the gate may suppress AT ALL. A system with < 30
  settled bets is NEVER suppressed (state = "monitoring").
- Hysteresis: a system must meet the suppress condition on 2 consecutive monitor runs before
  flipping to suppressed, and must clear ALL conditions on 2 consecutive runs before un-suppressing.
  (Store a small counter in the state file.)
- Manual override: a registry/env override that force-enables or force-suppresses a system
  regardless of metrics, so a human can always win. Suggested: optional `force_gate` on
  `SystemConfig` (None | "on" | "off"), checked first.

### Gate state file (GCS, idempotent)
Write `Gates/model_gates.json` to the data bucket:
```json
{
  "as_of": "2026-06-10T09:30:00Z",
  "systems": {
    "F5":   {"suppressed": true,  "reason": "auc 0.499 < 0.52; cal_err -0.20",
             "metrics": {"auc": 0.499, "cal_err": -0.20, "roi": -9.9, "n": 227},
             "suppress_streak": 2, "clear_streak": 0},
    "K":    {"suppressed": false, "reason": "healthy", "...": "..."}
  }
}
```
Use `mlb_core.storage.write_csv`/`write_bytes` (JSON via write_bytes) -- never the GCS client
directly (CONTEXT.md s6). One file, overwritten each monitor run.

### Runner enforcement
At the top of each runner's scoring loop, load the gate file ONCE:
- helper in a shared module (suggest `mlb_core/risk/gates.py`): `is_suppressed(system) -> bool`
  that reads `Gates/model_gates.json`, returns the `suppressed` bool for that system.
- FAIL OPEN: if the file is missing, unreadable, or the system is absent -> return False (NOT
  suppressed). A gate error must never silently halt all betting. Log a warning on read failure.
- When suppressed: the runner still scores and LOGS every prediction (kelly_triggered=False,
  stake=0) -- this preserves the "log every prediction" contract (CONTEXT.md s5) and keeps the
  rolling window filling so the system can later recover. It simply places no staked bet. This is
  exactly the existing F5 `log_only` behavior -- reuse it.

### Alerting
When a system flips suppressed<->unsuppressed, post to #ops-alerts (DISCORD_WEBHOOK_OPS, the same
channel monitor_performance already uses). Include the reason and metrics. One message per flip,
not per run.

### Interaction with existing log_only / 200-bet gate
`registry.log_only` (static, for new systems pre-200-bets) and this dynamic suppression are
independent -- a system is log-only if EITHER is true. Do not conflate them. The static flag means
"not yet graduated"; the gate means "was live, now degraded".

---

## Version control + safety

- Branch off `main`: `git checkout -b feat/model-health-gate`. ASCII-only source (CONTEXT.md s6).
  Co-author trailer on commits.
- Additive only: do not remove the existing `signal` field or change `monitor_performance`'s
  existing alert behavior. New fields, new state file, new helper module, new runner hook.
- `./deploy/deploy_service.sh` runs compileall + pytest before building and preserves
  `--add-cloudsql-instances` -- always deploy via it, never `gcloud builds submit` directly
  (CONTEXT.md s6). One deploy at the end, after grep-verifying all edits.
- Tests: add a unit test for the gate decision logic (suppress/clear/hysteresis/min-n/fail-open)
  and for the Task A health verdict precedence, in `tests/`. The fail-open path is the most
  important to test -- assert a missing/garbage gate file yields `is_suppressed == False`.
- Idempotency: re-running the monitor must produce a consistent gate file; the hysteresis counters
  must persist across runs (read previous state file, increment, write).
- Rollout caution: on first deploy the gate file will not exist -> all systems fail open (keep
  betting) -> safe. The first monitor run after deploy creates the file. Verify the file appears in
  GCS after one manual `/monitor` invocation before trusting it.

---

## Step-by-step

Phase 0 -- recon (read-only): CONTEXT.md s5/s11; `main.py` model_health_handler;
`runners/monitor_performance.py` (`_rolling_stats`, `_check_alerts`, `run`); `runners/run_f5.py`
(`LOG_ONLY_SYSTEMS`, `log_only` flow); `mlb_core/registry.py` (SystemConfig); `mlb_core/storage.py`.

Phase 1 -- Task A: add `flags`/`health`/`recommended_action` to `_system_stats()` in main.py.
Pure function change; no I/O. Easy to unit-test in isolation.

Phase 2 -- Task B metrics+state: in monitor_performance, compute the gate decision per system,
read previous `Gates/model_gates.json` for hysteresis counters, write the updated file, post flip
alerts. Add `force_gate` to SystemConfig (default None) and honor it first.

Phase 3 -- Task B enforcement: add `mlb_core/risk/gates.py::is_suppressed()` (fail-open). Wire it
into every runner's scoring loop, generalizing the F5 `log_only` pattern. Remove F5's hardcoded
`LOG_ONLY_SYSTEMS` only if it becomes redundant -- otherwise leave it (OR semantics).

Phase 4 -- tests + deploy: unit tests (gate logic + health verdict + fail-open), compileall,
pytest, deploy via deploy_service.sh, manual `/monitor` to materialize the gate file, verify in
GCS, then `/model-health` to confirm new fields. Update CONTEXT.md s11 + s12 (new gate file,
new health fields) and write a handoff.

---

## Out of scope
- The first-inning run-distribution spike (separate doc: scope_first_inning_runsim_2026-06-10.md).
- Retraining/rebuilding any model. This task is observability + bet-gating only.
- Changing min_edge thresholds or Kelly fractions (the gate suppresses wholesale; tuning edge is a
  separate modeling decision).
- Any frontend/beezy-vip change. `/model-health` is an ops endpoint; the public site does not read it.

## Reference index (confirmed-present)
- Health handler + `signal` logic: `main.py` model_health_handler (~lines 938-1037).
- Rolling metrics + alert thresholds (reuse): `runners/monitor_performance.py`
  (`_rolling_stats` ~91, `_auc` ~32, `_check_alerts` ~188, `run` ~333).
- Enforcement pattern to generalize: `runners/run_f5.py` (`LOG_ONLY_SYSTEMS` 392, `log_only` 458-525).
- System config / static log_only / expected_hit_rate: `mlb_core/registry.py` (SystemConfig ~25).
- Storage abstraction (use for the gate file): `mlb_core/storage.py`.
- Monitor docs to update: CONTEXT.md s11 (performance monitor) + s12 (ops monitor).
