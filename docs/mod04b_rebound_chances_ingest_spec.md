# MOD-04 B5: NBA Rebound Chances Banking-Only Ingest Specification

> [!IMPORTANT]
> **Governance / Scope Statement:** This document is a **banking-only specification** (no ingest code is included here). **No model change, no feature-wiring change, and no serving weight or live pipeline change is authorized by this spec.**

---

## 1. Context & Current Data Gap

The MOD-04 B4 NBA rebounds structural decomposition shadow model ([`nba/props/rebounds_decomp.py`](file:///home/hatch/workspace/mlb-betting-mod04b/nba/props/rebounds_decomp.py)) splits total rebounds into `OREB + DREB` using Expected Missed Shots Available (EMSA) and minutes-integrated player rebound shares. Today, without per-game player rebound-chance tracking, player share estimation (`raw_oreb_share`, `raw_dreb_share`) relies on realized box-score rebounds divided by prorated team court-minute rebounds (`t_oreb * min / team_court_min`, `t_dreb * min / team_court_min`). Rebound chances directly isolate positioning and rebounding opportunity from conversion luck (whether a teammate or opponent tipped/secured the board).

Current state of NBA tracking and shot data in this repository (verified as of **Nov 25, 2026**):

1. **`nba/shots/` banks shot charts only:** [`nba/shots/build.py`](file:///home/hatch/workspace/mlb-betting-mod04b/nba/shots/build.py) and [`nba/shots/charts.py`](file:///home/hatch/workspace/mlb-betting-mod04b/nba/shots/charts.py) store per-shot `(x, y, zone, made, three)` parquet artifacts for shot-location/zone EMSA splits. They contain zero rebound-chances or tracking opportunity columns.
2. **`nba/data/tracking.py` has only prior-season aggregates:** [`nba/data/tracking.py`](file:///home/hatch/workspace/mlb-betting-mod04b/nba/data/tracking.py) mirrors seasonal aggregate CSVs from a community repository (`github.com/gabriel1200/site_Data`, `NBA/tracking/{year}/{passing,dreb,oreb,touches}.csv`). It has no per-game granularity, and its `dreb`/`oreb` rebound-chance tables are complete **only through 2022-23** (`year = 2023`, `MIN_FULL_SEASON_GP = 30.0`); the 2024 file stops at `<= 23` games per player, and 2025/2026 files are absent.
3. **GCS `NBA/tracking/` prefix is empty as of Nov 25, 2026:** No automated daily rebound-chance ingest exists yet.

---

## 2. Source Options

Primary candidate source: **Official NBA.com Stats Tracking API** (`stats.nba.com/stats/`):

1. **`boxscoreplayertrackv3` / `boxscoreplayertrackv2` (preferred per-game endpoint):**
   - Keyed by `GameID` (10-digit NBA game ID).
   - Returns per-player-game tracking box rows including offensive rebound chances (`reboundChancesOffensive` / `OREB_CHANCES`), defensive rebound chances (`reboundChancesDefensive` / `DREB_CHANCES`), total rebound chances (`reboundChancesTotal` / `REB_CHANCES`), and contested/uncontested rebounding splits (`reboundsOffensiveContested`, `reboundsDefensiveContested`, `reboundsTotal`, etc., depending on endpoint version).
2. **`leaguedashptstats` (`PtMeasureType=Rebounding`, date-range fallback):**
   - Parameterized by `DateFrom`, `DateTo`, `PerMode=Totals`, `PlayerOrTeam=Player`, `Season`, `SeasonType`.
   - When queried for a single `DateFrom == DateTo` ET calendar day, returns per-player daily rebounding tracking totals including `OREB_CHANCES`, `DREB_CHANCES`, `REB_CHANCES`, `OREB_CONTEST`, `OREB_UNCONTEST`, `DREB_CONTEST`, `DREB_UNCONTEST`, `REB_CONTEST`, `REB_UNCONTEST`, `AVG_REB_DIST`.

**Operational requirements:**
- Standard NBA Stats headers (`Referer: https://www.nba.com/`, `Origin: https://www.nba.com`, `x-nba-stats-origin: stats`, `User-Agent`).
- Polite request throttling (`>= 0.6s` delay with exponential backoff on HTTP 429/5xx) and raw-first persistence before normalization.
- Verify egress from Cloud Run / backfill host before scheduling (office LANs or cloud IP blocks may require proxy/header tuning).

---

## 3. Target Per-(Player, Game) Schema

Normalized rows must be keyed by **`(game_id, player_id)`** (mapped to SportsBlaze `game_id` and `player_id` via canonical crosswalk / fail-closed identity resolution, retaining `nba_person_id` and `nba_game_id` for auditability).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `game_id` | `str` | No | Canonical SportsBlaze game ID (primary key part 1) |
| `player_id` | `str` | No | Canonical SportsBlaze player UUID (primary key part 2) |
| `nba_game_id` | `str` | No | 10-digit NBA.com game ID (`002...` / `004...`) |
| `nba_person_id` | `int64` | No | NBA.com `PLAYER_ID` / `personId` |
| `date` | `str` | No | ET game calendar date (`YYYY-MM-DD`, matching `nba.props.data`) |
| `season_year` | `int32` | No | NBA season start year (e.g., `2026` for 2026-27) |
| `team` | `str` | No | Canonical 3-letter team tricode (`nba.teams.team_code`) |
| `player_name` | `str` | No | Player display name from source feed |
| `minutes` | `float64` | Yes | Played minutes from tracking feed |
| `oreb_chances` | `float64` | No | Offensive rebound chances |
| `dreb_chances` | `float64` | No | Defensive rebound chances |
| `reb_chances` | `float64` | No | Total rebound chances (`oreb_chances + dreb_chances`) |
| `oreb_chances_contested` | `float64` | Yes | Contested OREB chances (if available from endpoint) |
| `oreb_chances_uncontested` | `float64` | Yes | Uncontested OREB chances (if available from endpoint) |
| `dreb_chances_contested` | `float64` | Yes | Contested DREB chances (if available from endpoint) |
| `dreb_chances_uncontested` | `float64` | Yes | Uncontested DREB chances (if available from endpoint) |
| `reb_chances_contested` | `float64` | Yes | Contested total rebound chances (if available) |
| `reb_chances_uncontested` | `float64` | Yes | Uncontested total rebound chances (if available) |
| `oreb` | `float64` | Yes | Actual offensive rebounds recorded in tracking feed (cross-check only) |
| `dreb` | `float64` | Yes | Actual defensive rebounds recorded in tracking feed (cross-check only) |
| `reb` | `float64` | Yes | Actual total rebounds recorded in tracking feed (cross-check only) |
| `source_endpoint` | `str` | No | Source identifier (`boxscoreplayertrackv3` / `leaguedashptstats`) |
| `ingested_at_utc` | `str` | No | ISO-8601 UTC ingestion timestamp |

---

## 4. Banking Target (GCS Layout)

All artifacts land under the `NBA/tracking/` prefix via `mlb_core.storage`:

- **Raw immutable payloads (raw-first):**
  - Per-game: `NBA/tracking/raw/boxscoreplayertrack/{date}/{nba_game_id}.json.gz`
  - Daily fallback (`leaguedashptstats`): `NBA/tracking/raw/rebounding_daily/{date}.json.gz`
- **Normalized daily snapshots:**
  - `NBA/tracking/rebound_chances/daily/{date}.parquet`
- **Consolidated master table:**
  - `NBA/tracking/rebound_chances/master.parquet`
- **Ingest manifest / checkpoint:**
  - `NBA/tracking/rebound_chances/_manifest.json` (tracks last banked ET date, game counts, row counts, and unmatched-player drops).

---

## 5. Cadence

- **Daily incremental banking:** Run once daily post-settlement at **07:00 ET** (after all prior-night West Coast tip-offs are final and NBA Stats tracking reconciliation has published) for `date = yesterday_et`, with a 3-day lookback window (`yesterday - 2d .. yesterday`) to capture late tracking corrections.
- **Historical backfill:** One-time checkpointed backfill across completed games from `2019-20` through `2025-26` + season-to-date `2026-27`, throttled and resumable via `_manifest.json`.

---

## 6. Leakage & Point-in-Time Rules

1. **Banking-only post-game data:** Rebound chances are realized post-game statistics and belong exclusively to the banking/label layer, never same-game inference inputs.
2. **Strictly prior consumption:** Any future feature builder reading `NBA/tracking/rebound_chances/master.parquet` MUST filter `game_date < target_date` (or use `shift(1)` rolling windows ordered by `(tip_sort, game_id)` inside `rebounds_decomp.py` and pass `mod04_features.build_pregame_features`, treating tracking tables as `_BOX_FRAME_KEYS`).
3. **Schema gate:** All feature outputs must pass `nhl.props.mod04_features.assert_feature_schema`. Columns such as `oreb_chances`, `dreb_chances`, `reb_chances`, `oreb`, `dreb`, `reb` from the target game must never appear in feature matrices.
4. **Fail-closed identity:** Ambiguous player-name joins against `player_boxscores_master.csv` must be dropped and logged to `_manifest.json` rather than guessed.

---

## 7. Validation Checks

Every daily and master write must pass these automated checks before updating `master.parquet`:

1. **Primary-key uniqueness:** Zero duplicate `(game_id, player_id)` rows.
2. **Non-negativity & finiteness:** `oreb_chances >= 0`, `dreb_chances >= 0`, `reb_chances >= 0`, and non-null for all played rows (`minutes > 0`).
3. **Accounting identities:**
   - `abs(reb_chances - (oreb_chances + dreb_chances)) <= 1e-6`
   - When contested/uncontested splits are present: `contested + uncontested == total` within `1e-6` for `oreb_chances`, `dreb_chances`, and `reb_chances`.
   - Consistency with box score: `oreb <= oreb_chances + 1e-6` and `dreb <= dreb_chances + 1e-6` (flag rare stat-correction anomalies rather than silently clamping).
4. **Coverage & reconciliation:**
   - Game coverage `>= 98%` vs `NBA/games_master.csv` final non-preseason games on the target date.
   - Player join coverage `>= 95%` of `minutes > 0` rows in `NBA/player_boxscores_master.csv`.
   - Cross-check `sum(reb)` vs `player_boxscores_master.csv` team totals on joined rows.

---

## 8. Authorization

This specification authorizes **documentation and future banking-pipeline planning only**. No ingest script, no `rebounds_decomp.py` feature change, no model retrain, and no serving weight or live pipeline change is authorized by this document.
