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
| **extra** (opt-in) | `contracts`, `draft_picks`, `trades`, `combine` |

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

Provision the nightly job with `deploy/setup_nfl_refresh.sh` (not wired into `monitor_ops`, same as NBA).

## Reading

```python
from nfl.data.read import load
stats = load("player_stats", seasons=[2024, 2025], columns=["player_id", "season", "week", "passing_yards"])
games = load("schedules")          # strict=True: a requested-but-unmirrored season raises, never returns a hole
```

`nfl.data.keys.add_game_keys(schedules)` adds the `game_key` (`nfl:{date}:{AWAY}@{HOME}`) that `ev/` uses, translating
nflverse team codes to the Kalshi/ParlayAPI codes (`JAX->JAC`, `LA->LAR`; `OAK/SD/STL` relocations).

## Not built yet (natural next steps)

- Player-name -> `gsis_id` resolver (from `players`) for the props sweep; fail closed on ambiguity.
- A second grader for settled NFL props from `player_stats` (the open item in `docs/designs/multisport_ev_sweep.md`).
- Freshness check in `monitor_ops`.
