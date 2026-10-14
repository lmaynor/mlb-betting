"""Mirror nflverse parquet datasets into the shared bucket under NFL/nflverse/.

Two modes (same code, different season window):

  backfill   every season from NFL_BACKFILL_START (default 2018) to the current season, per dataset.
             Historical seasons already mirrored are skipped (they are frozen upstream); --force re-pulls.
  refresh    the current season only (+ every single-file dataset). This is what the nightly Cloud Run Job
             runs. In the offseason the files are byte-identical, so it degrades to a no-op by sha256.

Per file: download -> validate (parquet footer, non-empty, required columns) -> compare sha256 with the
manifest -> write. A file that fails validation or has shrunk past SHRINK_GUARD is REFUSED and the existing
good copy is left alone. Failures never stop the other files; they are recorded in the sentinel and the process
exits non-zero so the Cloud Run execution shows as failed.

Examples (local dry run touches nothing; MLB_BASE_DATA MUST be set for non-GCS runs -- the repo default is a
Windows path):

    python3 -m nfl.data.ingest --mode backfill --dry-run
    python3 -m nfl.data.ingest --mode backfill --probe --datasets schedules,player_stats
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nfl.data.ingest --mode refresh
"""
import argparse
import hashlib
import io
import json
import logging
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Optional

from mlb_core import storage
from nfl import config
from nfl.config import DATASETS, DEFAULT_GROUPS, GROUPS, Dataset
from nfl.data.nflverse import NflverseClient

logger = logging.getLogger(__name__)

# result statuses
NEW, UPDATED, UNCHANGED = "new", "updated", "unchanged"
SKIPPED, MISSING, EMPTY = "skipped_present", "missing", "empty"
INVALID, SHRUNK, FAILED = "invalid", "refused_shrink", "failed"
_BAD = {INVALID, SHRUNK, FAILED}


class InvalidAsset(ValueError):
    """Downloaded bytes are not a usable parquet file for this dataset."""


@dataclass(frozen=True)
class Item:
    dataset: Dataset
    season: Optional[int]       # None for single-file datasets
    latest: bool                # newest season for this dataset (or a single file): may legitimately 404 / change

    @property
    def url(self) -> str:
        return self.dataset.url(self.season)

    @property
    def key(self) -> str:
        return self.dataset.key(self.season)

    @property
    def label(self) -> str:
        return f"{self.dataset.name}" + (f"/{self.season}" if self.season else "")


# --------------------------------------------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------------------------------------------

def select_datasets(names: Optional[Iterable[str]] = None, groups: Iterable[str] = DEFAULT_GROUPS) -> list:
    if names:
        unknown = [n for n in names if n not in DATASETS]
        if unknown:
            raise ValueError(f"unknown dataset(s) {unknown}; known: {sorted(DATASETS)}")
        return [DATASETS[n] for n in names]
    bad = [g for g in groups if g not in GROUPS]
    if bad:
        raise ValueError(f"unknown group(s) {bad}; known: {list(GROUPS)}")
    return [d for d in DATASETS.values() if d.group in set(groups)]


def plan(datasets: Iterable[Dataset], mode: str = "refresh", seasons: Optional[Iterable[int]] = None,
         today: Optional[date] = None) -> list:
    """Expand datasets into concrete (dataset, season) items, newest season first."""
    if mode not in ("backfill", "refresh"):
        raise ValueError("mode must be 'backfill' or 'refresh'")
    wanted = sorted(set(seasons)) if seasons else None
    items = []
    for ds in datasets:
        if not ds.per_season:
            items.append(Item(ds, None, True))
            continue
        top = config.current_season(today, roster=ds.roster_year)
        if mode == "refresh" and not wanted:
            span = [top]
        else:
            lo = max(ds.first, wanted[0] if wanted else config.BACKFILL_START)
            hi = min(top, wanted[-1]) if wanted else top
            span = [s for s in range(lo, hi + 1) if not wanted or s in wanted]
        items.extend(Item(ds, s, s == top) for s in sorted(span, reverse=True))
    return items


# --------------------------------------------------------------------------------------------------------------
# validation / manifest
# --------------------------------------------------------------------------------------------------------------

def validate(data: bytes, ds: Dataset) -> dict:
    """Return {'rows': int, 'columns': int} or raise InvalidAsset. Reads only the parquet footer."""
    if len(data) < 12 or data[:4] != b"PAR1" or data[-4:] != b"PAR1":
        raise InvalidAsset(f"not a parquet file ({len(data)} bytes, head={data[:16]!r})")
    import pyarrow.parquet as pq
    try:
        pf = pq.ParquetFile(io.BytesIO(data))
    except Exception as exc:
        raise InvalidAsset(f"unreadable parquet footer: {exc}") from exc
    names = set(pf.schema_arrow.names)
    missing = [c for c in ds.required if c not in names]
    if missing:
        raise InvalidAsset(f"missing required columns {missing} (have {len(names)} columns)")
    return {"rows": pf.metadata.num_rows, "columns": len(names)}


def load_manifest() -> dict:
    try:
        if storage.exists(config.MANIFEST_KEY):
            return json.loads(storage.read_bytes(config.MANIFEST_KEY).decode())
    except Exception as exc:
        logger.warning("manifest unreadable (%s); starting empty", exc)
    return {}


def _save_manifest(manifest: dict) -> None:
    storage.write_bytes(json.dumps(manifest, indent=1, sort_keys=True).encode(), config.MANIFEST_KEY)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------------------------------------------
# one file
# --------------------------------------------------------------------------------------------------------------

def process(item: Item, client: NflverseClient, manifest: dict, force: bool = False) -> dict:
    """Fetch + validate + (maybe) write one item. Mutates manifest on a write. Never raises."""
    res = {"item": item.label, "key": item.key, "url": item.url}
    prev = manifest.get(item.key)
    try:
        # A frozen historical season that is already mirrored needs no request at all.
        if not item.latest and not force and (prev or storage.exists(item.key)):
            return {**res, "status": SKIPPED}

        data = client.get_bytes(item.url)
        if data is None:
            # Not published: normal for the newest season early in the year; suspicious for anything else.
            ok = item.latest and item.dataset.per_season
            return {**res, "status": MISSING, "expected": ok}

        info = validate(data, item.dataset)
        if info["rows"] == 0:
            return {**res, "status": EMPTY}

        sha = hashlib.sha256(data).hexdigest()
        if prev and prev.get("sha256") == sha:
            prev["checked_at"] = _now()
            return {**res, "status": UNCHANGED, **info}
        if prev and not force and prev.get("rows") and info["rows"] < prev["rows"] * config.SHRINK_GUARD:
            return {**res, "status": SHRUNK, "rows": info["rows"], "prev_rows": prev["rows"],
                    "error": f"rows {prev['rows']} -> {info['rows']} (< {config.SHRINK_GUARD:.0%}); kept old copy"}

        storage.write_bytes(data, item.key)
        manifest[item.key] = {"url": item.url, "sha256": sha, "bytes": len(data), "rows": info["rows"],
                              "columns": info["columns"], "fetched_at": _now(), "checked_at": _now()}
        return {**res, "status": UPDATED if prev else NEW, "bytes": len(data), **info}
    except InvalidAsset as exc:
        return {**res, "status": INVALID, "error": str(exc)}
    except Exception as exc:      # one bad file must not stop the rest
        logger.exception("failed %s", item.label)
        return {**res, "status": FAILED, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------------------------------------------

def is_failure(r: dict) -> bool:
    return r["status"] in _BAD or (r["status"] == MISSING and not r.get("expected"))


def run(mode: str = "refresh", datasets: Optional[Iterable[str]] = None, groups: Iterable[str] = DEFAULT_GROUPS,
        seasons: Optional[Iterable[int]] = None, force: bool = False, client: Optional[NflverseClient] = None,
        today: Optional[date] = None, write_sentinel: bool = True) -> dict:
    items = plan(select_datasets(datasets, groups), mode, seasons, today)
    client = client or NflverseClient()
    manifest = load_manifest()
    results = []
    try:
        for item in items:
            r = process(item, client, manifest, force)
            results.append(r)
            if r["status"] not in (SKIPPED, UNCHANGED):
                logger.info("%-28s %-15s %s", item.label, r["status"],
                            r.get("error") or f"{r.get('rows', '')} rows")
    finally:
        # Persist whatever was written even if something above blew up mid-run.
        if any(r["status"] in (NEW, UPDATED, UNCHANGED) for r in results):
            _save_manifest(manifest)

    counts = dict(Counter(r["status"] for r in results))
    failures = [r for r in results if is_failure(r)]
    summary = {
        "status": "ok" if not failures else ("partial" if any(r["status"] in (NEW, UPDATED, UNCHANGED, SKIPPED)
                                                              for r in results) else "error"),
        "mode": mode, "files": len(results), "counts": counts,
        "written": [r["item"] for r in results if r["status"] in (NEW, UPDATED)],
        "failures": [{k: r.get(k) for k in ("item", "status", "url", "error")} for r in failures],
        "timestamp": _now(),
    }
    if write_sentinel:
        try:
            storage.write_bytes(json.dumps(summary, indent=1).encode(), config.LAST_REFRESH)
        except Exception as exc:
            logger.warning("could not write NFL sentinel: %s", exc)
    logger.info("NFL %s: %s", mode, json.dumps({k: summary[k] for k in ("status", "files", "counts")}))
    return summary


# --------------------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------------------

def _parse_seasons(text: Optional[str]) -> Optional[list]:
    if not text:
        return None
    out = []
    for part in text.split(","):
        lo, _, hi = part.strip().partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=("backfill", "refresh"), default="refresh")
    ap.add_argument("--datasets", help="comma list of dataset names (overrides --groups)")
    ap.add_argument("--groups", default=",".join(DEFAULT_GROUPS), help=f"comma list of {list(GROUPS)}")
    ap.add_argument("--seasons", help="e.g. 2024 | 2018-2026 | 2019,2022-2024 (clipped to each dataset's range)")
    ap.add_argument("--force", action="store_true", help="re-pull frozen seasons; bypass the shrink guard")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no network, no writes")
    ap.add_argument("--probe", action="store_true", help="HEAD each URL and print its size; no writes")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    names = [n for n in (args.datasets or "").split(",") if n]
    groups = [g for g in args.groups.split(",") if g]
    seasons = _parse_seasons(args.seasons)
    items = plan(select_datasets(names, groups), args.mode, seasons)

    if args.dry_run or args.probe:
        client = NflverseClient() if args.probe else None
        total = 0
        for it in items:
            size = ""
            if client is not None:
                n = client.head_size(it.url)
                total += n or 0
                size = "  (not published)" if n is None else f"  {n / 1e6:8.1f} MB"
            print(f"{it.label:28s} -> {it.key}{size}")
        print(f"{len(items)} files" + (f", {total / 1e6:.0f} MB total" if client is not None else ""))
        return 0

    summary = run(args.mode, names or None, groups, seasons, args.force)
    print(json.dumps(summary, indent=1))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
