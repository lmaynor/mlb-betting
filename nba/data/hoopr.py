"""Mirror the sportsdataverse (hoopR) ESPN NBA parquet releases into the shared bucket under NBA/hoopr/.

WHY (see docs/analysis/nba_zip_review_2026-10-16.md):
  * the SportsBlaze feed has no 2026-27 season, so the NBA props scorer refuses every slate (STALE_HISTORY);
  * SportsBlaze player ids are UUIDs that join to neither ESPN nor the odds layer (nba/props/identity.py bridges
    505 of 629 players); ESPN athlete ids ARE the id space of NBA/players_xwalk.csv and of ev/grade_props.nba_actuals;
  * ESPN box scores list every inactive / DNP player WITH a reason (COACH'S DECISION, LEFT ANKLE SPRAIN, REST,
    SUSPENDED...) and carry the real tip timestamp in America/New_York -- the two things nba/props has to
    reconstruct today (inactives from absence, ET game day from schedule dumps).

SOURCE: parquet assets on GitHub releases of sportsdataverse/sportsdataverse-data (no auth, no quota), the same URLs
hoopR's load_nba_* loaders read (R/load_nba_stats.R / load_nba.R). A hoopR "season" is the year the season ENDS
(2026 = 2025-26). Files are full-season snapshots rewritten in place upstream, so the correct update is "replace the
object"; we mirror them byte-for-byte (no re-encoding) with a sha256 manifest, same contract as nfl.data.ingest.

    python3 -m nba.data.hoopr --mode backfill --dry-run
    python3 -m nba.data.hoopr --mode backfill --probe --datasets player_boxscores
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nba.data.hoopr --mode refresh

GCS layout: NBA/hoopr/{dataset}/{stem}_{season}.parquet, NBA/hoopr/_manifest.json, NBA/hoopr/last_refresh.json.
Local mode (no GCS_BUCKET) writes under MLB_BASE_DATA -- the repo default is a Windows path, so set it explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Optional

import requests

from mlb_core import storage

logger = logging.getLogger(__name__)

# -- release layout ----------------------------------------------------------------------------------------------
HOOPR_BASE = os.environ.get(
    "NBA_HOOPR_BASE", "https://github.com/sportsdataverse/sportsdataverse-data/releases/download")
USER_AGENT = os.environ.get("NBA_HOOPR_USER_AGENT", "beezy.fyi-nba-ingest/1.0 (+https://beezy.fyi)")
HOOPR_PREFIX = "NBA/hoopr"
MANIFEST_KEY = f"{HOOPR_PREFIX}/_manifest.json"
LAST_REFRESH = f"{HOOPR_PREFIX}/last_refresh.json"

# Default backfill floor: the model window starts 2019-20 (nba.config.BACKFILL_SEASONS); ESPN goes back to 2002.
BACKFILL_START = int(os.environ.get("NBA_HOOPR_BACKFILL_START", "2019"))
REQUEST_DELAY_SEC = float(os.environ.get("NBA_HOOPR_REQUEST_DELAY", "0.25"))
MAX_ASSET_BYTES = int(os.environ.get("NBA_HOOPR_MAX_ASSET_BYTES", str(400 * 1024 * 1024)))
# Refuse to overwrite a mirrored file whose row count fell below this fraction of the previous copy (bad publish).
SHRINK_GUARD = float(os.environ.get("NBA_HOOPR_SHRINK_GUARD", "0.8"))

_TIMEOUT = 120
_MAX_RETRIES = 4
_RETRY_STATUS = {403, 429, 500, 502, 503, 504}     # 403/429 = GitHub secondary rate limiting


@dataclass(frozen=True)
class Dataset:
    """One mirrored dataset.

    tag       GitHub release tag (path segment after /download/).
    stem      asset filename stem; the file is {stem}_{season}.parquet
    first     first season upstream publishes (clips the window from below)
    group     core | extra -- the default CLI selection is core only (pbp is ~20 MB per season)
    required  columns that must exist or the file is rejected. ONLY columns confirmed in the 2026 files (2026-10-16).
    """
    name: str
    tag: str
    stem: str
    first: int
    group: str
    required: tuple

    def url(self, season: int) -> str:
        return f"{HOOPR_BASE}/{self.tag}/{self.stem}_{season}.parquet"

    def key(self, season: int) -> str:
        return f"{HOOPR_PREFIX}/{self.name}/{self.stem}_{season}.parquet"


DATASETS = {d.name: d for d in (
    Dataset("player_boxscores", "espn_nba_player_boxscores", "player_box", 2002, "core",
            ("game_id", "season", "season_type", "game_date", "game_date_time", "athlete_id", "team_id",
             "team_abbreviation", "minutes", "points", "rebounds", "assists", "starter", "did_not_play", "reason",
             "home_away", "opponent_team_id")),
    Dataset("team_boxscores", "espn_nba_team_boxscores", "team_box", 2002, "core",
            ("game_id", "season", "season_type", "game_date_time", "team_id", "team_abbreviation",
             "team_home_away", "team_score", "opponent_team_id")),
    Dataset("schedules", "espn_nba_schedules", "nba_schedule", 2002, "core",
            ("game_id", "season", "season_type", "game_date_time", "home_id", "away_id", "home_abbreviation",
             "away_abbreviation", "status_type_completed")),
    Dataset("pbp", "espn_nba_pbp", "play_by_play", 2002, "extra", ("game_id", "season")),
)}
GROUPS = ("core", "extra")
DEFAULT_GROUPS = ("core",)

# ESPN season_type codes in these files (verified on 2026: 2 regular, 3 postseason, 5 play-in; 1 = preseason upstream)
SEASON_TYPE_PRESEASON, SEASON_TYPE_REGULAR, SEASON_TYPE_PLAYOFFS, SEASON_TYPE_PLAYIN = 1, 2, 3, 5

NEW, UPDATED, UNCHANGED = "new", "updated", "unchanged"
SKIPPED, MISSING, EMPTY = "skipped_present", "missing", "empty"
INVALID, SHRUNK, FAILED = "invalid", "refused_shrink", "failed"
_BAD = {INVALID, SHRUNK, FAILED}


def current_season(today: Optional[date] = None) -> int:
    """hoopR season = the year the season ends. The 2026-27 season (preseason from October) is 2027.
    August-December -> next calendar year; January-July -> this calendar year."""
    t = today or datetime.now(timezone.utc).date()
    return t.year + 1 if t.month >= 8 else t.year


class InvalidAsset(ValueError):
    """Downloaded bytes are not a usable parquet file for this dataset."""


class HooprError(RuntimeError):
    """A download failed after retries (distinct from 'not published', which is None)."""


class HooprClient:
    def __init__(self, delay: float = REQUEST_DELAY_SEC, session=None, max_bytes: int = MAX_ASSET_BYTES):
        self.delay = delay
        self.max_bytes = max_bytes
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/octet-stream, */*"})

    def head_size(self, url: str) -> Optional[int]:
        resp = self.session.head(url, timeout=_TIMEOUT, allow_redirects=True)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        n = resp.headers.get("Content-Length")
        return int(n) if n and n.isdigit() else None

    def get_bytes(self, url: str) -> Optional[bytes]:
        """Asset bytes, or None when not published (HTTP 404). Transient failures retry, then raise."""
        last_exc: Optional[BaseException] = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.session.get(url, timeout=_TIMEOUT)
                if resp.status_code == 404:
                    logger.info("hoopR 404 (not published): %s", url)
                    return None
                if resp.status_code in _RETRY_STATUS:
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()
                data = resp.content
                if len(data) > self.max_bytes:
                    raise HooprError(f"asset is {len(data)} bytes (> cap {self.max_bytes}): {url}")
                if self.delay:
                    time.sleep(self.delay)
                return data
            except HooprError:
                raise
            except requests.RequestException as exc:
                last_exc = exc
                backoff = 2 ** attempt
                logger.warning("hoopR GET failed (%s) attempt %d/%d; retry in %ds",
                               exc, attempt + 1, _MAX_RETRIES, backoff)
                time.sleep(backoff)
        raise HooprError(f"hoopR GET failed after {_MAX_RETRIES} tries: {url}") from last_exc


@dataclass(frozen=True)
class Item:
    dataset: Dataset
    season: int
    latest: bool            # newest season: may legitimately 404 early in the year / change nightly

    @property
    def url(self) -> str:
        return self.dataset.url(self.season)

    @property
    def key(self) -> str:
        return self.dataset.key(self.season)

    @property
    def label(self) -> str:
        return f"{self.dataset.name}/{self.season}"


# ------------------------------------------------------------------------------------------------ planning
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
    """Expand datasets into (dataset, season) items, newest season first.

    refresh  : the current season, plus the previous season while the current one has not started (August-
               October: a late correction to last season's final games still lands, and a 404 on the new
               season is expected rather than a failure).
    backfill : BACKFILL_START .. current season (or the explicit --seasons window)."""
    if mode not in ("backfill", "refresh"):
        raise ValueError("mode must be 'backfill' or 'refresh'")
    wanted = sorted(set(seasons)) if seasons else None
    top = current_season(today)
    t = today or datetime.now(timezone.utc).date()
    items = []
    for ds in datasets:
        if mode == "refresh" and not wanted:
            span = [top] + ([top - 1] if t.month >= 8 else [])
        else:
            lo = max(ds.first, wanted[0] if wanted else BACKFILL_START)
            hi = min(top, wanted[-1]) if wanted else top
            span = [s for s in range(lo, hi + 1) if not wanted or s in wanted]
        items.extend(Item(ds, s, s >= top - (1 if mode == "refresh" else 0)) for s in sorted(span, reverse=True))
    return items


# ------------------------------------------------------------------------------------------------ validation
def validate(data: bytes, ds: Dataset) -> dict:
    """{'rows', 'columns'} or raise InvalidAsset. Reads only the parquet footer."""
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
        if storage.exists(MANIFEST_KEY):
            return json.loads(storage.read_bytes(MANIFEST_KEY).decode())
    except Exception as exc:
        logger.warning("manifest unreadable (%s); starting empty", exc)
    return {}


def _save_manifest(manifest: dict) -> None:
    storage.write_bytes(json.dumps(manifest, indent=1, sort_keys=True).encode(), MANIFEST_KEY)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------------------------------------ one file
def process(item: Item, client: HooprClient, manifest: dict, force: bool = False) -> dict:
    """Fetch + validate + (maybe) write one item. Mutates manifest on a write. Never raises."""
    res = {"item": item.label, "key": item.key, "url": item.url}
    prev = manifest.get(item.key)
    try:
        if not item.latest and not force and (prev or storage.exists(item.key)):
            return {**res, "status": SKIPPED}
        data = client.get_bytes(item.url)
        if data is None:
            return {**res, "status": MISSING, "expected": item.latest}
        info = validate(data, item.dataset)
        if info["rows"] == 0:
            return {**res, "status": EMPTY}
        sha = hashlib.sha256(data).hexdigest()
        if prev and prev.get("sha256") == sha:
            prev["checked_at"] = _now()
            return {**res, "status": UNCHANGED, **info}
        if prev and not force and prev.get("rows") and info["rows"] < prev["rows"] * SHRINK_GUARD:
            return {**res, "status": SHRUNK, "rows": info["rows"], "prev_rows": prev["rows"],
                    "error": f"rows {prev['rows']} -> {info['rows']} (< {SHRINK_GUARD:.0%}); kept old copy"}
        storage.write_bytes(data, item.key)
        manifest[item.key] = {"url": item.url, "sha256": sha, "bytes": len(data), "rows": info["rows"],
                              "columns": info["columns"], "fetched_at": _now(), "checked_at": _now()}
        return {**res, "status": UPDATED if prev else NEW, "bytes": len(data), **info}
    except InvalidAsset as exc:
        return {**res, "status": INVALID, "error": str(exc)}
    except Exception as exc:                 # one bad file must not stop the rest
        logger.exception("failed %s", item.label)
        return {**res, "status": FAILED, "error": f"{type(exc).__name__}: {exc}"}


def is_failure(r: dict) -> bool:
    return r["status"] in _BAD or (r["status"] == MISSING and not r.get("expected"))


def run(mode: str = "refresh", datasets: Optional[Iterable[str]] = None, groups: Iterable[str] = DEFAULT_GROUPS,
        seasons: Optional[Iterable[int]] = None, force: bool = False, client: Optional[HooprClient] = None,
        today: Optional[date] = None, write_sentinel: bool = True) -> dict:
    items = plan(select_datasets(datasets, groups), mode, seasons, today)
    client = client or HooprClient()
    manifest = load_manifest()
    results = []
    try:
        for item in items:
            r = process(item, client, manifest, force)
            results.append(r)
            if r["status"] not in (SKIPPED, UNCHANGED):
                logger.info("%-26s %-15s %s", item.label, r["status"], r.get("error") or f"{r.get('rows', '')} rows")
    finally:
        if any(r["status"] in (NEW, UPDATED, UNCHANGED) for r in results):
            _save_manifest(manifest)
    failures = [r for r in results if is_failure(r)]
    ok_any = any(r["status"] in (NEW, UPDATED, UNCHANGED, SKIPPED) for r in results)
    summary = {
        "status": "ok" if not failures else ("partial" if ok_any else "error"),
        "mode": mode, "files": len(results), "counts": dict(Counter(r["status"] for r in results)),
        "written": [r["item"] for r in results if r["status"] in (NEW, UPDATED)],
        "failures": [{k: r.get(k) for k in ("item", "status", "url", "error")} for r in failures],
        "timestamp": _now(),
    }
    if write_sentinel:
        try:
            storage.write_bytes(json.dumps(summary, indent=1).encode(), LAST_REFRESH)
        except Exception as exc:
            logger.warning("could not write hoopR sentinel: %s", exc)
    logger.info("hoopR %s: %s", mode, json.dumps({k: summary[k] for k in ("status", "files", "counts")}))
    return summary


# ------------------------------------------------------------------------------------------------ readers
def read_dataset(name: str, seasons: Iterable[int], local_dir: Optional[str] = None):
    """Concatenate the mirrored season files of `name` into one DataFrame (missing seasons are skipped with a log
    line, never invented). `local_dir` reads <dir>/{name}/{stem}_{season}.parquet (offline work / tests)."""
    import pandas as pd
    ds = DATASETS[name]
    frames = []
    for s in sorted(set(seasons)):
        try:
            if local_dir:
                f = os.path.join(local_dir, name, f"{ds.stem}_{s}.parquet")
                if not os.path.exists(f):
                    logger.info("hoopR %s/%s not mirrored locally", name, s)
                    continue
                frames.append(pd.read_parquet(f))
            else:
                k = ds.key(s)
                if not storage.exists(k):
                    logger.info("hoopR %s/%s not mirrored", name, s)
                    continue
                frames.append(pd.read_parquet(io.BytesIO(storage.read_bytes(k))))
        except Exception as exc:             # noqa: BLE001
            logger.warning("hoopR %s/%s unreadable: %r", name, s, exc)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ------------------------------------------------------------------------------------------------ CLI
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
    ap.add_argument("--seasons", help="hoopR seasons (year the season ends), e.g. 2026 | 2019-2026")
    ap.add_argument("--force", action="store_true", help="re-pull frozen seasons; bypass the shrink guard")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no network, no writes")
    ap.add_argument("--probe", action="store_true", help="HEAD each URL and print its size; no writes")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    names = [n for n in (args.datasets or "").split(",") if n]
    groups = [g for g in args.groups.split(",") if g]
    items = plan(select_datasets(names, groups), args.mode, _parse_seasons(args.seasons))
    if args.dry_run or args.probe:
        client = HooprClient() if args.probe else None
        total = 0
        for it in items:
            size = ""
            if client is not None:
                n = client.head_size(it.url)
                total += n or 0
                size = "  (not published)" if n is None else f"  {n / 1e6:8.1f} MB"
            print(f"{it.label:26s} -> {it.key}{size}")
        print(f"{len(items)} files" + (f", {total / 1e6:.0f} MB total" if client is not None else ""))
        return 0
    summary = run(args.mode, names or None, groups, _parse_seasons(args.seasons), args.force)
    print(json.dumps(summary, indent=1))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
