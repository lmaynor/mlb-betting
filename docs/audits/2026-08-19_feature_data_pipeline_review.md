# Feature Data Pipeline Review — 2026-08-19

_Reviewer: Claude Code session, real GCS data + gcloud execution history + live MLB Stats API
cross-checks (not a code-only read). Scope: are the underlying features flowing/joining
correctly, is the data "normal" on macro statistics, and where are the rigor gaps._

## TL;DR

Pulled the real GCS masters and every system's `model_features.csv`, cross-checked them against
the live MLB Stats API and against known real-world baselines, and traced one team (Cleveland)
and one player (José Ramírez) end-to-end through the pipeline. The **core play-by-play data
(scoring, runs, wins) is sound and validates almost exactly against real MLB history** — that
part of the worry is not borne out. But the review surfaced **one severe, currently-live bug that
the 2026-08-16 audit's own fix did not actually resolve** (GAME's home/away starter attribution is
still backwards for 89.6% of its training data), plus several previously-undocumented staleness
and coverage gaps in the auxiliary-join and weather layers, and a concrete, data-backed answer on
the "Meatball rate" idea.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | GAME Pro v1: home/away starter-pitcher attribution still backwards for **89.6%** of training rows — the Aug 17 fix only reached the trailing ~90-day window | 🔴 Critical | **New, currently live** |
| 2 | Athletics home games are fed Oakland's weather; the team has played 2025-2026 at Sutter Health Park, Sacramento | 🔴 High | **New, currently live** |
| 3 | Auxiliary join layer (bref/FanGraphs pitching, Savant swing-take, manager hooks) has **no scheduled refresh job** — 3 of 4 sources frozen since ~June 2, 2026 | 🔴 High | **New, currently live** |
| 4 | Weekly model calibration has been silently failing to fire on its automatic schedule for a month; NRFI and F5 calibrators are stale right now | 🟠 High | **New, currently live** |
| 5 | GAME Pro v1 has **zero real weather signal** — `temperature_f`/`wind_speed_mph` are hardcoded constants for all 11,300 training rows | 🟠 Medium | **New** |
| 6 | 2025 Rays season weather is 100% wrong (treated as dome; team played outdoors all year at a hurricane-relocation venue) | 🟡 Medium | **New**, self-resolving for 2026+ |
| 7 | CONTEXT.md documents a GCS filename that doesn't exist (`bref_pitching_master.csv` vs. real `fangraphs_pitching_master.csv`) | ⚪ Low | Doc drift |
| 8 | `BATTER_TB` has no entry in `mlb_core/schemas.py` | ⚪ Low | Coverage gap |
| 9 | "Meatball rate" (pitcher heart-of-zone location rate) does not exist anywhere in the pipeline. Prototyped it from real Statcast data: real signal exists (r≈0.07–0.11 vs. HR rate), weak but genuine, cheap to add | — | Gap / opportunity |
| 10 | Retractable-roof-park weather rows (~23% of parks): the already-known A13 code fix has only repaired 5 of 3,276 historical rows | 🟠 Medium | Known bug, **fix hasn't propagated to data** |

Section 3 covers what's working well — that matters as much as the bugs, given the specific worry
about home/away flips and loose joins.

---

## 1. Scope and method

Per `CLAUDE.md`, `CONTEXT.md` (all ~3,350 lines) and the latest handoff
(`handoffs/handoff_2026-08-17_audit_remediation_complete.md`) were read in full before touching
anything, along with the existing `docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md`
(113-finding prior audit) and its `2026-08-16_fix_checklist.md` — that prior audit already covers a lot
of the same territory (including a home/away pitcher-attribution bug in GAME, finding A4), so this
review's job was to (a) verify those fixes actually reached the live *data*, not just the code, and
(b) go looking for anything the prior, code-focused audit couldn't have caught.

This was not a code-only re-read. For each thread below, real data was pulled from
`gs://concrete-crow-445205-m4-mlb-data/` (weather, scoring, team_schedule, manager_hooks,
swing_take, fangraphs_pitching, statcast, and every system's `model_features.csv`) and analyzed
directly with pandas, cross-checked in three independent ways:

1. **Against a live, authoritative external source** — the MLB Stats API (the same no-auth API
   `mlb_core.data.lineups`/`game_result` already depend on) for real venue and schedule ground truth.
2. **Against a known real-world baseline** — MLB's historical home-field-advantage rate (~53–54%),
   real runs-per-game by season, and Cleveland's actual 2021–2023 win-loss records.
3. **Against the codebase's own git history and GCP execution history** — `git log`, GCS object
   timestamps, and `gcloud run jobs executions list` for every retrain/calibrate job, to establish
   not just what the code says today but what has actually *run* and *landed in data*.

Sample team/player: **Cleveland Guardians / José Ramírez (MLBAM 608070)** — chosen because he's
already a verified fixture elsewhere in this repo's own test suite (per the 2026-08-17 handoff),
and because Cleveland's win totals are well-documented enough to sanity-check by hand.

**Coverage note:** HR_Pro and BATTER_HITS/BATTER_TB's `model_features.csv` (138–285MB each) were
downloaded and spot-checked for the specific things noted below, but did not get the same
row-by-row systematic trace GAME received — GAME was chosen for the deepest trace because it's the
newest, least-hardened system and the prior audit's own A4 finding was specifically about it.

---

## 2. Findings, in detail

### 2.1 🔴 CRITICAL — GAME Pro v1's home/away starter attribution is still backwards for 89.6% of training data

This is the single most important finding, and it's a direct, literal answer to the "home/away
flip" worry. It is **not** a re-discovery of the prior audit's finding A4 — A4's fix is real and is
correctly written into the code. The problem is that **the fix cannot reach most of the training
data by construction**, and nobody had checked that until now.

**What A4 was and what "fixed" meant.** The 2026-08-16 audit found `build_game_features.py`
computing `agg["side"] = inning_topbot.map({"Top": "away", "Bot": "home"})` — backwards (Top of the
inning is the *away* team batting, which means the *home* team's pitcher is on the mound). It was
fixed in commit `431a5c7` (2026-08-17) to `{"Top": "home", "Bot": "away"}`, and the fix checklist
marked it `[x]` with the caveat "rebuild+retrain still needed." That rebuild+retrain sequence *did*
run, same day — confirmed directly via `gcloud run jobs executions list`:

```
mlb-retrain-game-v1    started 2026-08-17T20:17:17Z, completed successfully
mlb-calibrate-game     started 2026-08-17T20:36:37Z, completed successfully
```

So by every check the prior remediation session could reasonably have run, this was done: code
fixed, model retrained, calibrator refit, all same day.

**What actually happened to the data.** `build_starter_features()` in `build_game_features.py` is
an *incremental* builder:

```python
keep_home = existing_home[existing_home["game_date"] < cutoff].copy()   # cutoff = today - 90 days
...
home_df = pd.concat([keep_home, home_df], ignore_index=True)
home_df = home_df.drop_duplicates(subset=["game_pk", "pitcher"]).reset_index(drop=True)
```

Every row older than the trailing 90-day window is carried forward **byte-for-byte unchanged**
from whatever `starter_home_features.csv`/`starter_away_features.csv` already contained. A logic
fix inside the function only ever touches the newest ~90 days on any given run; the other several
years of history are copied forward forever, regardless of how many times the "rebuild" is re-run,
unless someone deliberately reprocesses the full history.

**Verified directly against real Statcast data.** For every game in `GAME_Pro_System/data/model_features.csv`
(11,300 rows, 2022-04 to 2026-08-18), I independently reconstructed the true home-team starting
pitcher from raw `statcast_master.csv` (first pitcher of a Top-half plate appearance in inning 1 —
the same rule the code itself uses) and compared it to the file's own `pitcher` column:

| Window | Rows | `pitcher` column == true HOME starter | == true AWAY starter (wrong) |
|---|---|---|---|
| All rows (2022-04 .. 2026-08) | 11,299 | **10.3%** | **89.6%** |
| Older than ~95 days (before ~2026-05-16) | 10,104 | **0.0%** | **100.0%** |
| Within the last ~95 days | 1,195 | **97.6%** | 2.4% |

Breaking it down by month makes the mechanism unmistakable — every single month from 2022-04
through 2026-04 is **exactly 0% correct**, May 2026 is a mid-month crossover (43% correct, the
90-day window's leading edge sweeping through), and June 2026 onward is **100% correct**:

```
2026-04   n=225   pct_home_correct=0.000
2026-05   n=404   pct_home_correct=0.431   <- the window boundary
2026-06   n=393   pct_home_correct=1.000
2026-07   n=356   pct_home_correct=1.000
2026-08   n=243   pct_home_correct=1.000
```

**Concrete example:** game_pk `824452` (2026-04-19, Cleveland home vs. Baltimore — Ramírez hit a
home run). Reconstructing from raw pitch data: Cleveland's actual starter that day was pitcher
`676282`; Baltimore's was `669432`. GAME's `model_features.csv` lists `pitcher = 669432` for that
row — Baltimore's starter, mislabeled as Cleveland's, exactly the swapped pattern the audit fixed
in the formula but which never reached this row.

**Why this matters more than the checklist entry suggests:** the Aug 17 retrain that was supposed
to correct this trained on a dataset where **only about 10% of the starter-feature rows had
actually changed**. The retrained model is, for the starter-feature half of its 42 features, barely
distinguishable from the pre-fix model. `[x] A4 ... code fixed` was true and not misleading, but
"rebuild+retrain still needed" undersold what "rebuild" actually needed to mean here — a *full
historical* reprocess, not the routine incremental one.

**The same architectural pattern (and therefore the same risk for *any future* fix) also exists
in:**
- `build_hr_features.py` — `build_batter_rolling()` and `build_pitcher_features()` (lines 302, 488)
- `build_batter_hits_features.py` (lines 142, 355)
- `build_batter_tb_features.py` (lines 86, 218)

I did not find evidence that these three currently carry a live bug of this kind — the point is
structural: **any future correctness fix inside one of these four builders' per-row computation
will silently fail to reach existing history the same way**, and that will not show up unless
someone specifically checks the data (as here), because the file's timestamp, row count, and
`last_build.json` sentinel all look completely normal. `build_nrfi_features.py`, `build_f5_features.py`,
and `build_k_features.py` do not use this incremental-preserve pattern.

**Recommended fix, in order:**
1. Force a one-off full-history reprocess of `starter_home_features.csv`/`starter_away_features.csv`
   (and check `build_bullpen_features` — see 2.5 below, it has a related but distinct problem) by
   temporarily running with `lookback_days` covering the full history, or by deleting the two
   intermediate CSVs and letting the incremental builder repopulate from a first-build branch
   (the codebase already has this pattern — see the "Auto-detect first build for long lookback" gotcha
   in `CONTEXT.md` §15.4 — it just wasn't invoked here since this wasn't recognized as a first-build
   situation).
2. Retrain + recalibrate GAME again afterward — the Aug 17 retrain needs to be considered
   provisional until this runs.
3. Add a generic guard: when a fix changes a builder's per-row computation logic (not just a
   join/read), the fix's own commit or PR should say explicitly whether a full historical
   reprocess is required, the same way "run the calibrate job after any retrain" is already a
   house rule in `CONTEXT.md` §15.5.

### 2.2 🔴 HIGH — Athletics home games get Oakland's weather; the team hasn't played there since 2024

`mlb_core/data/weather.py`'s `STADIUMS` dict hardcodes one lat/lon per team, with a comment
acknowledging the 2025+ Statcast team-code rename (`OAK`→`ATH`) but pointing both codes at the same
coordinates — Oakland Coliseum (37.7516, -122.2005).

Verified directly against the live MLB Stats API (the same API this codebase's own
`mlb_core.data.lineups` already depends on):

```
Athletics home games, 2025-04-01..2025-04-09: VENUE = Sutter Health Park  (every game)
Athletics home games, 2026-08-10..2026-08-16: VENUE = Sutter Health Park  (every game, i.e. still true today)
```

Sutter Health Park is in **West Sacramento, CA** (38.57994, -121.51246, per the Stats API's own
`hydrate=location`), a Central Valley climate that runs meaningfully hotter and drier than the Bay
Area marine climate around Oakland Coliseum, especially April–September. `weather_master.csv`'s
`OAK` rows show a flat, cool, low-variance profile (2025 mean 64.6°F, essentially identical to
every other season back to 2021) that is Oakland's climate signature, not Sacramento's — i.e. the
feed is self-consistently and continuously wrong, not just theoretically wrong.

This is **live today**, not just historical: every Athletics home game, including the ones in this
week's slate, gets a real Open-Meteo forecast for the wrong city. It directly affects HR and F5's
weather features (temperature/wind correlate with HR distance and are wired into both).

**Fix:** the MLB Stats API's own schedule response already returns the correct venue and its
coordinates for every single game (`venue.id`, `venue.name`, and via `hydrate=location`,
`defaultCoordinates`) — this is strictly better than a static per-team dict, and would have made
both this bug and the Rays one below (2.6) impossible, because the ground truth is fetched
per-game instead of assumed per-team-forever. Recommend replacing `STADIUMS`' static lookup with a
per-game venue fetch (cache aggressively — a season's worth of venues per team almost never changes
mid-season) rather than patching in one more hardcoded coordinate override.

### 2.3 🔴 HIGH — The auxiliary-join layer has no scheduled refresh job; 3 of 4 sources are frozen since ~June 2, 2026

`mlb_core/data/aux_joins.py` wires four auxiliary datasets into NRFI, K, F5, GAME, HR,
BATTER_HITS, and BATTER_TB: FanGraphs/B-Ref pitching (FIP/WHIP/SO9/BB9), Savant swing-take
(heart/shadow/chase/waste run values), team schedule (travel, rest, series), and manager hooks
(hook tendencies, quality starts). All four are built by one module, `mlb_core/data/auxiliary_features.py`,
which is CLI-only:

> `PYTHONPATH=. python3 -m mlb_core.data.auxiliary_features <dataset> [--force]` — CONTEXT.md §2

I checked whether anything actually calls this on a schedule. **Nothing does.** `main.py` has zero
references to `auxiliary_features`, `manager_hooks`, `team_schedule`, `fangraphs_pitching`, or
`swing_take`; the live Cloud Scheduler job list has no job matching any of those names. It is a
manual-only process, and checking the actual GCS timestamps and data content shows it has not been
run in a long time for three of the four sources:

| Source | Last touched | Coverage as of today (2026-08-19) | Corroborating evidence |
|---|---|---|---|
| `fangraphs_pitching_master.csv` (bref FIP/WHIP/SO9/BB9) | **2026-06-02** | 2026 season frozen ~11 weeks ago | Top-IP 2026 starter shows only 79.1 IP — matches ~13 starts, i.e. an early-June cutoff, not August |
| `swing_take_master.csv` (heart/shadow/chase/waste) | **2026-06-02** | same | Ramírez's 2026 row: 261 PA — a full 2026 season for an everyday player is ~650 PA; 261 is almost exactly the ~40% of a season that had been played by early June |
| `manager_hooks_master.csv` | **2026-06-02** (data content: last row 2026-05-31) | **Zero rows for June, July, or August 2026** | Direct query: `manager_hooks[game_date >= '2026-08-01']` → 0 rows |
| `team_schedule_master.csv` | fresh (into Sept 2026) | OK | Forward-looking MLB schedule data captured once for the season; not evidence of an active refresh, just of not needing one as urgently |

Two of these joins fail differently, which matters for how dangerous each is:
- **`manager_hooks` joins on exact `game_pk`.** Since no June+ game_pks exist in the stale master,
  the join correctly returns NaN for every game since June — safe but silent: this feature group has
  had zero signal for roughly a third of the season, for GAME, F5, NRFI, and K, with no error or
  alert anywhere. (This directly explains a NaN I hit while tracing a specific August Cleveland
  game in step 2.4.)
- **`fangraphs_pitching` and `swing_take` join on `(name, year)` — year granularity, not date.** A
  2026 game in August still matches the pitcher's/batter's 2026 row and returns a **non-null value**
  — it just silently reflects that player's performance only through early June. This is the
  more dangerous failure mode: not missing, just quietly wrong, for the last 11+ weeks of the
  season, on every NRFI/K bref-derived feature and every HR/BATTER_HITS/BATTER_TB swing-take
  feature, system-wide.

**Recommended fix:** add a scheduled Cloud Scheduler job (weekly is probably sufficient, given
these are themselves rolling-30/season aggregates that don't move fast day-to-day) that invokes
`auxiliary_features` for all four datasets with `--force`, the same way `mlb-retrain-weekly` already
exists for models. This is a real gap in the daily/weekly loop inventory in `CONTEXT.md` §4 — none
of the four loops described there cover this.

### 2.4 🟠 HIGH — Weekly calibration has been silently failing to fire on schedule for a month

While confirming that the Aug 17 fixes' retrain+calibrate sequence landed (finding 2.1's context),
I checked the actual execution history of every retrain and calibrate job, not just the most recent
one, and found a consistent gap:

```
mlb-retrain-k-v1     : 2026-07-06, 07-13, 07-20, 07-27, 08-03, 08-10, 08-17(06:00, scheduled)
                       + 08-17(20:10, manual remediation run)     <- fires every Monday, on time
mlb-calibrate-k      : 2026-07-20(06:31)  ...  2026-08-17(20:34, manual remediation run)
                       ^ NOTHING between these two — 4 straight Mondays where K retrained
                         but the automatic calibrate leg never fired
```

The same 4-week gap appears for `mlb-calibrate-hr`, `mlb-calibrate-game`. For `mlb-calibrate-nrfi`
and `mlb-calibrate-f5`, it's worse: their last successful run is **still** 2026-07-20 — those two
were not touched by the manual Aug 17 remediation (NRFI/F5 weren't in scope for that day's code
fixes), so **as of right now, both NRFI's and F5's calibrators are stale relative to 4 subsequent
weekly retrains** (Jul 27, Aug 3, Aug 10, Aug 17), each of which changed the underlying booster's
output distribution without a matching recalibration.

`CONTEXT.md` §15.5 already documents why this matters: *"Calibrators must be refit after any model
output range change... sklearn clips out-of-bounds inputs to the nearest boundary value, mapping
everything to 0 or 1."* This is exactly the failure mode the repo's own gotcha warns about, live,
for two systems, right now.

**Root cause (well-evidenced, not just theorized).** `/retrain-weekly` in `main.py` fires all 8
retrain jobs synchronously within the request, then spawns a **daemon thread that sleeps 1800
seconds before firing the 7 calibrate jobs**, after the HTTP response has already been returned:

```python
def _run_calibrate_after_delay():
    time.sleep(CALIBRATE_DELAY_S)          # 1800s
    ...
t = threading.Thread(target=_run_calibrate_after_delay, daemon=True)
t.start()
return jsonify({...})                       # response sent; request "ends" here
```

The Cloud Run service has no `run.googleapis.com/cpu-throttling: false` (always-allocated-CPU)
annotation — confirmed via `gcloud run services describe` — meaning it runs Cloud Run's **default**
CPU-throttling behavior, where CPU is only reliably allocated while a request is actively being
served. A background thread that needs to keep running *after* the response that spawned it has
already completed is exactly the pattern Cloud Run's docs warn will be unreliable without that
annotation. The data matches this precisely: retrains (which run synchronously, inside the request)
fire every single Monday without fail; calibrates (which depend on the thread surviving 30 minutes
past the response) fire unpredictably — and for 4 straight weeks, not at all.

**Fix:** don't rely on a sleeping thread across the HTTP response boundary. Split into two Cloud
Scheduler jobs — `mlb-retrain-weekly` at 06:00 UTC (as today) and a new `mlb-calibrate-weekly` at
06:35 UTC hitting a separate, equally simple endpoint that just fires the 7 calibrate jobs
synchronously within its own request. This is the same pattern the codebase already uses correctly
for the twice-daily `/run` scheduling relative to `/snapshot-odds`.

**Immediate action recommended independent of the code fix:** manually run `mlb-calibrate-nrfi` and
`mlb-calibrate-f5` now — both are overdue.

### 2.5 🟠 MEDIUM — GAME Pro v1 has zero real weather signal in training

`GAME_Pro_System/data/model_features.csv`'s `temperature_f` column is a hard constant — `70.0`,
every single row, `std = 0.0`, across all 11,300 games. `wind_speed_mph` is likewise constant at
`0.0`. Both are populated by the builder's own fallback:

```python
if "temperature_f"  not in mf.columns: mf["temperature_f"]  = 70.0
if "wind_speed_mph" not in mf.columns: mf["wind_speed_mph"] = 0.0
```

Reading `build_model_features()`'s weather step confirms why the fallback always fires for
historical builds: the real weather overlay only happens if a `wx` dict is passed in, and `wx` is
only ever populated by `fetch_live_weather_for_slate()` for the live/daily scoring path — the
historical feature-rebuild path never loads `weather_master.csv` at all (unlike HR and F5, which
both do). `is_outdoor`, `roof`, `wind_dir_degrees`, `high_wind`, `is_cold`, and `is_hot` are all
**100% NaN** for the same reason.

Two consequences: (1) two of GAME's documented "42 features... park/weather" are structurally
inert — zero variance, zero information, contrary to the system's own stated design; (2) at *live*
scoring time GAME does fetch a real forecast, so the model can see a genuine 45°F or 95°F value it
was never trained on any variation of — a real, if likely low-impact (XGBoost rarely splits on a
zero-variance training column at all) train/serve skew, distinct from the already-known A13
weather-convention bug in HR/F5.

This is a different, previously-undocumented bug from A13 — A13 is about a wrong *convention* on a
weather join that does happen; this is about a join that historically **never happens at all** for
this one system. `park_factor` and `is_dome` are fine — both are populated by a legitimate static
per-team lookup, not this fallback path, and their values look correct (e.g. COL 1.12, BOS 1.08 —
consistent with well-known real park factors).

**Fix:** wire the historical builder to load `weather_master.csv` the same way `build_hr_features.py`
already does (`wx = _load_or_empty(cfg.get("gcs_weather_master", ...))`), then retrain.

### 2.6 🟡 MEDIUM — 2025 Rays season weather is 100% wrong; resolved going forward but poisons a full season of training data

`weather_master.csv` shows `TB` as `roof=dome`, `temperature_f=NaN`, **every single game, every
season, 2021 through today** — no exceptions, ever. That's `weather.py`'s hardcoded convention
(`STADIUMS["TB"] = (..., "dome", ...)`).

Verified against the live MLB Stats API: **Tampa Bay's actual 2025 home games were played at
George M. Steinbrenner Field** (27.97997, -82.50702), an open-air park, not Tropicana Field —
consistent with the well-known fact that Hurricane Milton damaged Tropicana Field's roof in October
2024. The Rays are back at Tropicana Field for 2026 (confirmed via the same API — venue reverts to
"Tropicana Field" for 2026-04 games), so this is **not currently live** the way the Athletics issue
is, but it means **all 81 of the Rays' 2025 home games carry entirely fabricated weather features**
(null where real conditions applied, `is_outdoor=0` where it should be 1) permanently baked into
whatever training data spans 2025 — a full season, for one team, silently wrong in the training set
for HR, F5, and GAME (were it wired — see 2.5).

Same fix as 2.2: a per-game venue lookup instead of a static per-team dict would have prevented
this too, and will prevent whatever the next such relocation is (this is now the second one in two
years — Athletics 2025, Rays 2025 — this is not a one-off).

### 2.7 A13's fix (retractable-roof weather convention) has landed in code but has repaired only 5 of 3,276 affected historical rows

This is the known A13 finding, already `[x]` in the fix checklist as "code fixed... rebuild+retrain
still needed." I checked what "rebuild" actually did to the data. `weather_master.csv` is
append-only (nightly job fetches only "yesterday"; `CONTEXT.md` §3 itself notes "no dedicated
backfill function"), so the code fix can only ever affect *new* rows going forward — it doesn't and
structurally can't retroactively repair rows already written. As of today:

- **3,271 of 3,276** retractable-roof-park rows (Arizona, Houston, Miami, Milwaukee, Seattle,
  Texas, Toronto — 7 of 30 parks) still show `temperature_f=NaN`, `is_outdoor=0` — the pre-fix
  convention, spanning 2021 through mid-August 2026.
- Only **5** rows (Toronto/Houston on 2026-08-16, Milwaukee/Texas/Houston on 2026-08-18) show the
  corrected convention — these look like a manual spot-check of the fix rather than a systematic
  backfill, since the dates aren't contiguous (2026-08-17 for the same parks is still unfixed).

Net effect: HR and F5 are still being trained on a dataset where ~23% of parks have had essentially
no real weather signal for their entire history. Rebuilding the model without first running
`/backfill-data {"systems":["weather"]}` over full history (as `CONTEXT.md` §15.4 already
prescribes for weather gaps generally) leaves this exactly where it was before the code fix.

### 2.8 ⚪ Small findings

- **`CONTEXT.md` §3 documents a GCS object that doesn't exist.** It lists
  `AuxData/bref_pitching_master.csv`; the real object (confirmed via `gsutil ls`) is
  `AuxData/fangraphs_pitching_master.csv`, which is what `load_fangraphs_pitching()` actually reads
  (`_FG_PREFIX = "AuxData/fangraphs_pitching"`). The code is fine; the doc sent me looking for a
  file that isn't there. One-line fix.
- **`mlb_core/schemas.py`'s `SCHEMAS` dict has no `batter_tb_model_features` entry.** Every other
  live-Kelly or log-only trained system (NRFI, F5, HR, K, BATTER_HITS, GAME) has one;
  BATTER_TB — a fully trained NegBin model — does not, so a malformed BATTER_TB feature build would
  pass `validate_df` silently where the same defect in BATTER_HITS would be caught.
- **Bullpen rolling windows have no minimum-innings floor.** `home_bullpen_xwoba_L14` ranges up to
  0.719 in the live GAME features (league-average is ~0.300–0.320; even a historically bad bullpen
  stretch doesn't approach 0.719 over 14 real days). The one row I traced back (`game_pk 823300`,
  2026-05-23, San Diego) has `home_bullpen_ip_L7 = 5.0` — five innings of bullpen work in seven
  days is a tiny, noise-dominated sample driving an extreme value. Only 1 of 1,116 non-null rows is
  this extreme, so it's a minor rigor gap, not a systemic bug — but a `min_periods` floor (the
  rolling calls elsewhere in this codebase already use `min_periods=5` or `10`) would cheaply
  quiet it.
- **HR's per-batter-game grain attributes the whole game's opponent-pitcher features to whichever
  pitcher the batter faced *first*.** If a batter homers off a reliever later in the game, the
  model still sees the *starter's* rolling stats as "the opposing pitcher" for that row. This is a
  real limitation, not obviously a bug: the market being modeled (HR yes/no, any pitcher, that
  game) is itself game-grained, and a fully pitcher-specific model would be a materially bigger
  redesign (per-PA rather than per-batter-game). Worth documenting as a known simplification in
  `CONTEXT.md` rather than silently living only in the code.

---

## 3. What's working well

Worth stating plainly, because the review was specifically motivated by a worry about flips and
loose joins: **the core scoring/outcome data checked out cleanly, repeatedly, against independent
ground truth.**

- **League-wide home-field advantage, reconstructed purely from `scoring_master.csv`'s raw
  inning-by-inning runs** (joined to team identity via `weather_master.csv`, since
  `scoring_master.csv` itself carries no team columns — a real but minor coupling worth knowing
  about): **53.1% home win rate across 14,023 decided games, 2021–2026.** Real-world modern MLB
  home-field advantage is well-established at ~53–54%. This is a strong, independent validation
  that the `top=away-batting / bot=home-batting` convention — the exact convention several of the
  above bugs got backwards elsewhere — is correctly applied in the two data sources most people
  will actually query (scoring and weather masters).
- **Runs-per-game by season tracks real MLB scoring history precisely**, including the well-known
  2022 offense dip (reconstructed: 8.57 R/G combined) and the 2023 rebound after the pitch-clock/
  larger-bases rule changes (reconstructed: 9.23 R/G combined) — these aren't invented numbers, they
  match the actual, reported league environment those years.
- **Cleveland's reconstructed win-loss record matches the real standings exactly** for three
  straight seasons purely from `scoring_master.csv` + `weather_master.csv`: 2021 80-82, 2022 92-70
  (AL Central champions), 2023 76-86 — an exact match down to the single win/loss for all three.
  2024 comes out 91-69 over 160 games against a real record of 92-69/162 — 2 games short, an
  explainable small coverage gap, not a computation error.
- **Doubleheader handling looks correct.** 52 `(home_team, game_date)` pairs have exactly 2 games —
  all legitimate scheduling artifacts (postponement makeups), not duplicate-row corruption.
- **Team-schedule's own internal home/away bookkeeping is self-consistent**: for every sampled
  `game_pk`, exactly one side shows `is_home=1` and the other `is_home=0`, and each side's
  `opp_team` correctly cross-references the other row.
- **A4, A12, A13 (formula), A14, and C2.3's code fixes are genuinely present and correct** on
  inspection — this review isn't contradicting the prior audit's diagnosis, only its assumption
  that "rebuild+retrain ran" meant "the fix reached the data." Specifically, the A4 fix's own
  `{"Top": "home", "Bot": "away"}` mapping was re-verified directly in the current
  `build_game_features.py` (both the starter-side and bullpen-side call sites), separately from
  the incremental-window problem documented in 2.1.
- **The full retrain→calibrate→fit-calibrators sequence for GAME, K, OUTS, HR, BATTER_HITS, and
  BATTER_TB did execute, same day, following the code fixes** (2026-08-17, 20:06–20:47 UTC) —
  confirmed via `gcloud run jobs executions list`, closing the exact "was retrain/recalibrate
  actually run?" open question the 2026-08-17 handoff left unresolved.

---

## 4. Sample trace: José Ramírez / Cleveland Guardians

Methodology and result are folded into 2.1 and 3 above (that's where the trace's most important
discovery — the 90-day incremental-window gap — came from), but summarizing the trace itself for
completeness:

1. **`swing_take_master.csv`** — Ramírez (MLBAM `608070`) has clean rows for 2024/2025/2026 keyed
   correctly on his own ID, with `runs_heart`/`runs_shadow`/`runs_chase`/`runs_waste` all present —
   this join (batter-only, explicitly *not* joined on pitcher IDs per the documented
   `savant-swing-take-batter-only.md` gotcha) works as designed. His 2026 row's `pa=261` is the
   evidence that corroborates the auxiliary-staleness finding (2.3) independently of file
   timestamps.
2. **`team_schedule_master.csv`** — CLE rows correctly flip `is_home`/`opp_team` per game, both
   sides of any given `game_pk` cross-reference correctly.
3. **`manager_hooks_master.csv`** — 858 CLE rows, correctly `(team, game_pk)`-keyed for anything
   through 2026-05-31; zero rows past that (the 2.3 finding).
4. **Statcast → GAME model_features, one real game (`824452`, CLE home vs. BAL, 2026-04-19,
   Ramírez HR)** — this is where independently reconstructing "who actually pitched for the home
   team" and diffing it against the feature file's own `pitcher` column surfaced finding 2.1.
5. **HR's opponent-pitcher attribution** — for this same game, Ramírez faced 2 distinct pitchers;
   the HR builder's `opp_pitcher_id = pitcher of the batter's first PA` rule happened to match the
   actual HR-allowing pitcher in this instance, but that's coincidence, not guarantee — see the
   small finding in 2.8 about this being a known per-game-grain simplification.

---

## 5. The "Meatball" idea: does a pitcher heart-of-zone rate belong in this pipeline?

Checked what already exists before prototyping anything. The pipeline has real, if narrower,
zone-adjacent features, but nothing that measures a **pitcher's own tendency to locate in the heart
of the strike zone**, independent of pitch type or outcome:

- `build_hr_features.py` computes `in_zone`/`chase`/`zone_swing`/`zone_contact_pct` — but these are
  the **batter's** swing-decision quality (chase rate, zone-contact rate), a binary in/out-of-zone
  flag, not a heart-vs-edge subdivision, and not attributed to the pitcher.
- `hr_zone_contact` / `batter_hr_zone_rate_L20` is the batter's own **post-contact** quality
  (exit velo ≥95 & launch angle 20–35°) — describes how well the batter squared a ball up, not
  where the pitcher located it.
- `pitcher_high_fb_pct_L50` is the closest thing to a pitcher-side location feature that exists —
  fastballs above 3.0 ft — a single-dimension, fastball-only proxy.
- `batter_runs_heart`/`_shadow`/`_chase`/`_waste` (from Savant's own public Attack Zones, already
  correctly wired via `join_batter_aux`) are the batter's run-value **performance** when pitches
  land in each zone — a real, already-used version of the "heart of zone" concept, but from the
  batter's side, not a rate describing the pitcher's own location tendency.

So the gap is real: nothing here answers "does this pitcher throw it down the middle more or less
often than average" as a standalone pitcher attribute.

**Prototyped it directly from real Statcast data** (this is exactly what a Baseball Savant /
Pitcher List "Meatball%" metric measures) using the same `plate_x`/`plate_z`/`sz_top`/`sz_bot`
columns `build_hr_features.py` already pulls — so this would be close to free to add:

- Defined "heart" as the inner ~55% of the rulebook zone box, scaled to each pitch's own batter's
  actual `sz_top`/`sz_bot` (so it's fair across batter heights, the same way Statcast's real Attack
  Zones are). Cross-checked against Statcast's own numeric `zone` column (`zone==5` = its dead-center
  cell): every `zone==5` pitch is inside my "heart" flag, plus a reasonable ring around it — 25.6%
  heart-rate overall vs. 11.5% for the narrower `zone==5` alone. Sane, defensible construction.
- Computed each pitcher's heart-rate (≥500 tracked pitches) and their realized HR-allowed rate
  (≥200 batters faced), 610 qualifying pitchers:

  | Heart-rate quartile | Mean heart rate | Mean HR / PA allowed | Mean HR / FB allowed |
  |---|---|---|---|
  | Q1 (fewest meatballs) | 22.3% | 2.92% | 18.71% |
  | Q2 | 24.7% | 3.00% | 18.86% |
  | Q3 | 26.5% | 3.13% | 19.43% |
  | Q4 (most meatballs) | 28.9% | 3.07% | 19.40% |

  Pearson r = **0.111** (heart-rate vs. HR/PA) and **0.074** (heart-rate vs. HR/FB) — positive,
  directionally correct, and real, but **weak**. This is an honest result, not a sales pitch: a
  pitcher's heart-of-zone rate is a small, real contributor to their home-run rate, not a dominant
  one — command quality, velocity, and pitch mix almost certainly matter more, and this alone
  wouldn't have rescued HR's documented calibration issues.

**Recommendation:** worth adding as one more feature (cheap — the raw columns are already loaded),
not worth expecting a large uplift from. Any claim about how much it actually helps should go
through this repo's own walk-forward backtest before being trusted, the same way every other
feature-addition claim here already has to (per `CONTEXT.md`'s CLV-not-ROI go/no-go rule) — a
r≈0.07-0.11 raw correlation is nowhere near sufficient on its own to promote a bet-sizing change.

---

## 6. Recommended action plan

**Do now (data is actively wrong or missing, not just a code fix pending):**
1. Force a full-history reprocess of GAME's `starter_home_features.csv`/`starter_away_features.csv`
   (finding 2.1), then retrain + recalibrate GAME again.
2. Manually run `mlb-calibrate-nrfi` and `mlb-calibrate-f5` (finding 2.4) — both overdue by 4 weeks.
3. Manually re-run `auxiliary_features` for all four datasets with `--force` (finding 2.3) to close
   the ~11-week gap before doing anything else that depends on them.
4. Run `/backfill-data {"systems":["weather"]}` over full history for the 7 retractable-roof parks
   (finding 2.7), then retrain HR and F5 again.

**Fix the mechanism, not just this instance:**
5. Replace the static per-team `STADIUMS` lat/lon dict with a per-game venue lookup from the MLB
   Stats API (findings 2.2, 2.6) — this exact class of bug will recur (it already has, twice, in
   two years) as long as venues are assumed static.
6. Move `/retrain-weekly`'s calibrate phase off a post-response sleeping thread onto its own
   Scheduler-triggered job (finding 2.4).
7. Add a scheduled refresh job for `auxiliary_features` (finding 2.3) — currently absent from every
   documented daily/weekly loop.
8. Wire real historical weather into `build_game_features.py` (finding 2.5).
9. Add a `batter_tb_model_features` schema entry (finding 2.8); fix the `bref_pitching_master.csv`
   filename in `CONTEXT.md` §3 (finding 2.8).
10. When a future fix touches per-row computation logic inside an incremental builder (HR, GAME,
    BATTER_HITS, BATTER_TB all share this pattern), require the fix's own writeup to state whether
    a full historical reprocess is needed — don't rely on "a rebuild ran" as proof a fix landed in
    the data.

**Worth doing, lower urgency:**
11. Add a `min_periods` floor to GAME's bullpen rolling windows (finding 2.8).
12. Prototype the heart-of-zone ("Meatball") rate as a real HR feature and walk-forward-test it
    before trusting it for sizing (Section 5).
13. Document HR's per-batter-game opponent-pitcher simplification explicitly in `CONTEXT.md`
    (finding 2.8) so it's a documented, revisit-able choice rather than a hidden assumption.

---

## Appendix — reproducing the key checks

```bash
# Fix-propagation check (2.1, 2.4): commit dates vs GCS artifact timestamps vs execution history
git log -1 --format="%H %ci %s" 431a5c7   # A4 fix
gcloud run jobs executions list --job=mlb-retrain-game-v1 --region=us-central1 \
  --project=concrete-crow-445205-m4 --limit=5 --format=json
gcloud run jobs executions list --job=mlb-calibrate-nrfi --region=us-central1 \
  --project=concrete-crow-445205-m4 --limit=8 --format=json

# Weather venue ground truth (2.2, 2.6) -- no auth needed
curl -s "https://statsapi.mlb.com/api/v1/schedule?teamId=133&startDate=2025-04-01&endDate=2025-04-10&sportId=1"
curl -s "https://statsapi.mlb.com/api/v1/schedule?teamId=139&startDate=2025-04-01&endDate=2025-04-10&sportId=1"
curl -s "https://statsapi.mlb.com/api/v1/venues/2529?hydrate=location"   # Sutter Health Park

# Auxiliary staleness (2.3)
gsutil ls -la gs://concrete-crow-445205-m4-mlb-data/AuxData/

# GAME home/away trace (2.1) -- reconstructed independently from statcast_master.csv,
# compared to GAME_Pro_System/data/model_features.csv's own `pitcher` column, grouped by month.
```

_Data pulled and analyzed 2026-08-19. All GCS timestamps and gcloud execution histories reflect
that date; if this document is read later, re-run the appendix commands rather than trusting the
numbers above as still current._
