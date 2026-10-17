# NFL data package (nflverse mirror)

Data-ingest-first NFL expansion. Mirrors the **nflverse** open datasets into the shared GCS bucket so
NFL work (prop grading, backtests, ID/schedule joins, later models) has local, versioned, queryable data.
**There is no NFL model or betting runner here.** Live NFL odds/props are the separate shadow sweep in
`ev/` (`ev/sweep_nfl_props.py`); this package is its historical / outcomes / identity backbone.

## Source

nflverse publishes parquet files as assets on GitHub releases of `nflverse/nflverse-data`:

    https://github.com/nflverse/nflverse-data/releases/download/{tag}/{file}.parquet

No auth, no API quota. URL shapes and season floors come from the `nflreadpy` loaders; column contracts from
`nflreadr`'s data dictionaries (`data-raw/dictionary_*.csv`). nflfastR and nflseedR are R model/simulation code
(EPA/WP models, season simulation) -- they are not needed to ingest, and are not run in this stack.

## Datasets (`nfl/config.py` `DATASETS`)

| group | datasets |
|---|---|
| **core** | `schedules` (incl. closing spread/total/moneyline, result, roof/weather, rest, QBs, referee; 1999+), `players` (id crosswalk), `teams`, `officials`, `player_stats` (weekly, 1999+ -- the prop grader), `team_stats`, `rosters`, `rosters_weekly`, `injuries`, `depth_charts`, `snap_counts` |
| **detail** | `pbp`, `participation`, `ftn_charting`, `ngs_{passing,receiving,rushing}`, `pfr_{pass,rush,rec,def}` |
| **extra** (opt-in) | `contracts`, `draft_picks`, `trades`, `combine`, `ff_opportunity` (ffverse expected-opportunity model; lives on the `ffverse/ffopportunity` release, not nflverse-data; columns not yet dictionary-verified) |

Default selection is core + detail. Default backfill window starts at `NFL_BACKFILL_START` (2018).

## GCS layout (shared bucket, `NFL/` prefix)

```
NFL/
  nflverse/{dataset}/{dataset}_{season}.parquet   per-season datasets (verbatim upstream bytes)
  nflverse/{dataset}/{dataset}.parquet            single-file datasets
  _manifest.json                                  key -> {url, sha256, bytes, rows, columns, fetched_at, checked_at}
  last_refresh.json                               sentinel: status ok|partial|error, counts, failures
```

Files are mirrored **byte-for-byte** (no re-encode, no merge): nflverse rewrites full-season snapshots in place, so
replacing the object is the correct update.

## Safety behaviour

- Validates every download (parquet footer, non-empty, dictionary-verified required columns); a bad file is
  **refused** and the previous good copy stays.
- Shrink guard: a file whose row count drops below 80% of the mirrored copy is refused (`--force` overrides).
- Frozen historical seasons are not re-requested; unchanged files (sha256) are not rewritten.
- A 404 on the **newest** season is expected (e.g. participation is published only after the season ends); a 404 on
  a frozen season or single-file dataset is a **failure** (our URL assumption is wrong). Any failure -> non-zero exit.

## Running

```bash
# Plan / size without writing (MLB_BASE_DATA must be set for non-GCS runs; the repo default is a Windows path)
python3 -m nfl.data.ingest --mode backfill --dry-run
python3 -m nfl.data.ingest --mode backfill --probe            # HEAD every URL, print sizes

# One-time backfill, then nightly refresh (what the Cloud Run Job runs)
GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nfl.data.ingest --mode backfill
GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nfl.data.ingest --mode refresh
```

Provision the nightly job with `deploy/setup_nfl_refresh.sh`. The job runs `python -m nfl.data.pipeline`
(mirror -> derived tables -> data contracts); `monitor_ops` alerts on a failed refresh AND on a failed contract.
`nfl-refresh-data` is provisioned and has been running (sentinel 2026-10-15 13:32 UTC, status ok).

## Reading

```python
from nfl.data.read import load
stats = load("player_stats", seasons=[2024, 2025], columns=["player_id", "season", "week", "passing_yards"])
games = load("schedules")          # strict=True: a requested-but-unmirrored season raises, never returns a hole
```

`nfl.data.keys.add_game_keys(schedules)` adds the `game_key` (`nfl:{date}:{AWAY}@{HOME}`) that `ev/` uses, translating
nflverse team codes to the Kalshi/ParlayAPI codes (`JAX->JAC`, `LA->LAR`; `OAK/SD/STL` relocations).

## Derived tables (`nfl/data/derived.py`)

Small versioned tables built from the mirrored play-by-play, so consumers read ~1 MB per season instead of a 20 MB,
372-column pbp file. nflfastR conventions (`xpass`/`pass_oe` are nflfastR's expected-pass model; neutral situation =
1st/2nd down, win probability 0.20-0.80, outside the last two minutes of a half).

```
NFL/derived/team_game_pbp/team_game_pbp_{season}.parquet      (game_id, team): off_* rates + the defense's allowed def_* rates
NFL/derived/player_game_pbp/player_game_pbp_{season}.parquet  (game_id, player_id): targets, air yards, deep / red-zone / goal-line usage, EPA
NFL/derived/_manifest.json                                    rows, version, source_sha256 -> rebuilt only when pbp or the definitions change
```

```bash
GCS_BUCKET=... python3 -m nfl.data.derived --seasons 2018-2026      # all seasons ~8 s on a laptop
python -c "from nfl.data.read import load_derived; load_derived('team_game_pbp', seasons=[2025])"
```

## Data contracts (`nfl/data/contracts.py`)

Table-level guarantees the ingest's per-file checks cannot give: grain, sign, every final game present in
`player_stats` / `team_stats` / `pbp`, per-team-game reconciliation of player vs team stats (exact), the PPR formula
(semantic drift), schedule results and upcoming lines. `python -m nfl.data.contracts --today 2026-10-16` prints
failures; the pipeline writes `NFL/contracts_latest.json`. Real finding: nflverse writes one null-`player_id`
team-penalty row per week -- see `docs/solutions/integration-issues/nflverse-player-stats-null-id-team-row.md`.

## Prop systems (shadow, no betting)

`nfl/features/`, `nfl/models/` and `nfl/props/` hold the feature builder, per-stat models, walk-forward evaluator, fail-closed
player resolver, second prop grader and the banked market-lines tables. See CONTEXT.md s20 for the design notes, the
UNVERIFIED book-rule assumptions and the (currently null) evidence status. Nothing here writes to GCS or the bets table.

```bash
GCS_BUCKET=... python -m nfl.models.walkforward --stats rec_yds --seasons 2021-2026 --features-cache f.parquet
GCS_BUCKET=... python scripts/nfl_banked_lines.py --out ./.nfl_lines_out
GCS_BUCKET=... python scripts/nfl_model_vs_market.py --lines ./.nfl_lines_out/props_open_close.parquet --features-cache f.parquet
```

### Evidence tools (accuracy only -- there are NO historical NFL prop lines in this repo)

```bash
# paired feature-group ablation (same held-out rows, standard errors, season agreement)
python -m nfl.models.ablation --group pbp_ctx --features-cache f.parquet --seasons 2021-2025
# replay of the SERVING projection engine (ev/daily_projections.py) + the game-environment adjustment
python -m nfl.props.engine_eval --seasons 2021-2025 --tune-through 2023 --env --cache rows.parquet
```

Findings and the adoption rule: `docs/solutions/conventions/nfl-projection-engine-changes-need-walkforward-evidence.md`.

## Not built yet (natural next steps)

- A shadow scorer/runner on live consensus lines (features for upcoming games exist: `include_upcoming=True`; market-
  dependent columns must be overwritten with live lines) and a scheduled retrain.
- Reading the books' and Kalshi's anytime-TD and DNP rule text to settle the grader's UNVERIFIED assumptions.
