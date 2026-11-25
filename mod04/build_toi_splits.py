"""Resumable NHL situation-TOI splits builder for MOD-04 Cluster (a).

Pulls the NHL stats REST API published per-game skater TOI report
(``skater/timeonice?isGame=true`` via ``nhl.data.toi_splits.StatsClient``)
from 2022-10-01 through today, caches raw paginated window payloads under
``data/mod04/NHL/toi_splits/cache/``, writes ``official_toi.csv``, converts via
``official_as_splits`` into ``data/mod04/NHL/toi_splits/toi_splits.csv``,
prints a coverage + PK-sanity report against ``NHL/skater_boxscores_master.csv``,
and optionally uploads to GCS via ``gcloud storage cp``.

Note on NHL stats API pagination: ``api.nhle.com/stats/rest/en/skater/timeonice``
caps every response at 100 rows even when ``limit=-1`` is passed, returning
``total`` and requiring ``start=0, 100, 200, ...`` with deterministic ``sort``.
This runner paginates all pages per 14-day window through ``StatsClient._get``
and caches every page in ``RawCache``.

If the official endpoint fails (API change or refusal), falls back to the
shift-derived builder (``nhl.data.toi_splits.run``) and reports the exact
shortfall in the coverage report.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import subprocess
from typing import Any
import urllib.parse

import numpy as np
import pandas as pd

from nhl.data.toi_splits import (
    GCS_KEY,
    RawCache,
    StatsClient,
    official_as_splits,
    official_frame,
    run as run_shift_builder,
    select_games,
)

log = logging.getLogger("mod04.build_toi_splits")

USER_AGENT = "Beezy Research (beezy.fyi; research@beezy.fyi)"
DEFAULT_DATA_DIR = Path("data/mod04")
DEFAULT_OUT_DIR = DEFAULT_DATA_DIR / "NHL" / "toi_splits"
DEFAULT_CACHE_DIR = DEFAULT_OUT_DIR / "cache"
DEFAULT_START_DATE = "2022-10-01"
DEFAULT_WINDOW_DAYS = 14
PAGE_SIZE = 100
FALLBACK_MAX_GAMES = 250
GCS_BUCKET = "concrete-crow-445205-m4-mlb-data"
GCLOUD_BIN = Path.home() / "google-cloud-sdk/bin/gcloud"

_SORT_PARAM = urllib.parse.quote(
    json.dumps([
        {"property": "gameId", "direction": "ASC"},
        {"property": "playerId", "direction": "ASC"},
    ])
)

SPLITS_COLS = [
    "game_id",
    "player_id",
    "team",
    "date",
    "toi_total",
    "toi_ev",
    "toi_pp",
    "toi_pk",
    "team_pp_s",
    "team_pk_s",
    "pp_share",
    "pp_unit_guess",
]


def _make_client(delay: float = 0.1) -> StatsClient:
    client = StatsClient(delay=delay)
    client.session.headers["User-Agent"] = USER_AGENT
    return client


def _fetch_official_page(client: StatsClient, day_from: str, day_to: str, start: int = 0) -> dict | None:
    expr = urllib.parse.quote(f'gameDate>="{day_from}" and gameDate<="{day_to}"')
    path = f"skater/timeonice?isGame=true&limit={PAGE_SIZE}&start={int(start)}&sort={_SORT_PARAM}&cayenneExp={expr}"
    return client._get(path)


def _fetch_window_paginated(
    day_from: str,
    day_to: str,
    cache: RawCache,
    client: StatsClient,
    refresh: bool = False,
) -> pd.DataFrame:
    """Fetch all pages for ``day_from..day_to`` via ``StatsClient`` + ``RawCache``."""
    parts: list[pd.DataFrame] = []
    start = 0
    total: int | None = None
    while True:
        key = f"{day_from}_{day_to}_s{start}"
        if refresh:
            p_old = cache._path("official_pg", key)
            if p_old.exists():
                p_old.unlink()
        payload = cache.fetch(
            "official_pg",
            key,
            lambda a=day_from, b=day_to, s=start: _fetch_official_page(client, a, b, s),
        )
        if not payload:
            break
        rows = payload.get("data") or []
        if total is None:
            raw_tot = payload.get("total")
            total = int(raw_tot) if raw_tot is not None else len(rows)
        if not rows:
            break
        parts.append(official_frame(payload))
        start += len(rows)
        if start >= total or len(rows) < PAGE_SIZE:
            break
    if not parts:
        return official_frame(None)
    return pd.concat(parts, ignore_index=True)


def _date_windows(first: str, last: str, days: int = DEFAULT_WINDOW_DAYS) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    a = pd.Timestamp(first)
    end = pd.Timestamp(last)
    while a <= end:
        b = min(a + pd.Timedelta(days=days - 1), end)
        out.append((a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")))
        a = b + pd.Timedelta(days=1)
    return out


def fetch_official_resumable(
    first: str,
    last: str,
    out_dir: Path,
    cache_dir: Path,
    delay: float = 0.1,
    days: int = DEFAULT_WINDOW_DAYS,
    flush_every: int = 5,
    client: StatsClient | None = None,
) -> pd.DataFrame:
    """Pull paginated official TOI splits across ``first..last`` with disk cache + incremental CSV checkpoints."""
    out_dir.mkdir(parents=True, exist_ok=True)
    off_path = out_dir / "official_toi.csv"
    cache = RawCache(cache_dir)
    cl = client or _make_client(delay=delay)

    parts: list[pd.DataFrame] = []
    windows = _date_windows(first, last, days=days)
    log.info("Fetching official TOI splits for %s -> %s (%d windows of %d days)", first, last, len(windows), days)
    for idx, (a, b) in enumerate(windows, 1):
        # Always refresh the trailing window ending at `last` so newly-completed games are captured
        df_w = _fetch_window_paginated(a, b, cache, cl, refresh=(b == last))
        if not df_w.empty:
            parts.append(df_w)
        if idx % flush_every == 0 or idx == len(windows):
            combined = pd.concat(parts, ignore_index=True) if parts else official_frame(None)
            combined = combined.drop_duplicates(["game_id", "player_id"]).sort_values(["date", "game_id", "player_id"])
            tmp = off_path.with_suffix(".csv.tmp")
            combined.to_csv(tmp, index=False)
            tmp.replace(off_path)
            log.info(
                "Window %d/%d (%s..%s): %d skater-games (%d games)",
                idx,
                len(windows),
                a,
                b,
                len(combined),
                combined["game_id"].nunique() if not combined.empty else 0,
            )

    if off_path.exists():
        return pd.read_csv(off_path, low_memory=False)
    return official_frame(None)


def coverage_report(
    splits: pd.DataFrame,
    data_dir: Path,
    *,
    min_date: str = DEFAULT_START_DATE,
    source: str = "official",
    fallback_note: str | None = None,
) -> dict[str, Any]:
    """Compute season coverage, master-match rate, and PK TOI sanity metrics."""
    sk_path = data_dir / "NHL" / "skater_boxscores_master.csv"
    gm_path = data_dir / "NHL" / "games_master.csv"

    sp = splits.copy()
    if not sp.empty:
        sp["game_id"] = pd.to_numeric(sp["game_id"], errors="coerce").astype("Int64")
        sp["player_id"] = pd.to_numeric(sp["player_id"], errors="coerce").astype("Int64")
        sp = sp.dropna(subset=["game_id", "player_id"]).copy()
        sp["game_id"] = sp["game_id"].astype("int64")
        sp["player_id"] = sp["player_id"].astype("int64")
        sp["season_year"] = sp["game_id"].astype(str).str[:4].astype(int)

    rep: dict[str, Any] = {
        "source": source,
        "fallback_note": fallback_note,
        "splits_rows": int(len(sp)),
        "splits_games": int(sp["game_id"].nunique()) if not sp.empty else 0,
        "min_date": str(sp["date"].min()) if ("date" in sp.columns and not sp.empty) else None,
        "max_date": str(sp["date"].max()) if ("date" in sp.columns and not sp.empty) else None,
        "by_season": {},
    }

    if not sp.empty:
        tot_s = float(pd.to_numeric(sp["toi_total"], errors="coerce").fillna(0.0).sum())
        ev_s = float(pd.to_numeric(sp["toi_ev"], errors="coerce").fillna(0.0).sum())
        pp_s = float(pd.to_numeric(sp["toi_pp"], errors="coerce").fillna(0.0).sum())
        pk_s = float(pd.to_numeric(sp["toi_pk"], errors="coerce").fillna(0.0).sum())
        rep["toi_shares"] = {
            "ev_share": round(ev_s / tot_s, 4) if tot_s > 0 else None,
            "pp_share": round(pp_s / tot_s, 4) if tot_s > 0 else None,
            "pk_share": round(pk_s / tot_s, 4) if tot_s > 0 else None,
            "pk_share_in_expected_band_7_to_9pct": bool(tot_s > 0 and 0.065 <= (pk_s / tot_s) <= 0.095),
        }
        for sy, grp in sp.groupby("season_year"):
            rep["by_season"][str(int(sy))] = {
                "splits_games": int(grp["game_id"].nunique()),
                "splits_rows": int(len(grp)),
            }

    if sk_path.exists():
        sk = pd.read_csv(
            sk_path,
            usecols=["game_id", "season_year", "game_type", "date", "player_id", "played", "toi_sec"],
            low_memory=False,
        )
        sk["date"] = sk["date"].astype(str).str[:10]
        sk = sk[
            (sk["date"] >= min_date)
            & sk["game_type"].isin([2, 3])
            & sk["played"].astype(str).str.lower().isin({"true", "1", "1.0"})
            & (pd.to_numeric(sk["toi_sec"], errors="coerce").fillna(0) > 0)
        ].copy()
        sk["game_id"] = sk["game_id"].astype("int64")
        sk["player_id"] = sk["player_id"].astype("int64")

        if not sp.empty:
            sp_keys = sp[["game_id", "player_id"]].drop_duplicates().assign(_matched=1)
            m = sk.merge(sp_keys, on=["game_id", "player_id"], how="left")
            matched_rows = int(m["_matched"].notna().sum())
            matched_games = int(m.loc[m["_matched"].notna(), "game_id"].nunique())
        else:
            m = sk.assign(_matched=np.nan)
            matched_rows = 0
            matched_games = 0

        rep["master_rows_in_window"] = int(len(sk))
        rep["master_games_in_window"] = int(sk["game_id"].nunique())
        rep["matched_rows"] = matched_rows
        rep["matched_games"] = matched_games
        rep["row_match_share"] = round(matched_rows / len(sk), 4) if len(sk) else None
        rep["game_match_share"] = round(matched_games / sk["game_id"].nunique(), 4) if len(sk) else None

        for sy, grp in m.groupby("season_year"):
            k = str(int(sy))
            entry = rep["by_season"].setdefault(k, {"splits_games": 0, "splits_rows": 0})
            entry["master_games"] = int(grp["game_id"].nunique())
            entry["master_rows"] = int(len(grp))
            entry["matched_rows"] = int(grp["_matched"].notna().sum())
            entry["row_match_share"] = round(entry["matched_rows"] / len(grp), 4) if len(grp) else None
    elif gm_path.exists():
        gm = pd.read_csv(gm_path, usecols=["game_id", "season_year", "game_type", "date"], low_memory=False)
        gm = gm[(gm["date"].astype(str).str[:10] >= min_date) & gm["game_type"].isin([2, 3])]
        rep["master_games_in_window"] = int(gm["game_id"].nunique())

    return rep


def upload_toi_splits_gcs(local_csv: Path, gcs_key: str = GCS_KEY) -> str:
    """Upload toi_splits.csv to GCS using ``gcloud storage cp`` (never gsutil)."""
    dst = f"gs://{GCS_BUCKET}/{gcs_key}"
    subprocess.run([str(GCLOUD_BIN), "storage", "cp", str(local_csv), dst], check=True)
    return dst


def build_and_publish(
    data_dir: Path = DEFAULT_DATA_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    start_date: str = DEFAULT_START_DATE,
    end_date: str | None = None,
    delay: float = 0.1,
    write_gcs: bool = False,
    fallback_max_games: int = FALLBACK_MAX_GAMES,
) -> dict[str, Any]:
    """Run the official TOI pull (with shift-chart fallback on failure) and emit coverage report."""
    end_dt = end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    splits_path = out_dir / "toi_splits.csv"
    report_path = out_dir / "coverage_report.json"

    source = "official"
    fallback_note: str | None = None
    try:
        off = fetch_official_resumable(
            start_date,
            end_dt,
            out_dir=out_dir,
            cache_dir=cache_dir,
            delay=delay,
        )
        if off.empty:
            raise RuntimeError("Official TOI endpoint returned 0 rows across entire window")
        splits = official_as_splits(off)
        keep = [c for c in SPLITS_COLS if c in splits.columns]
        splits = splits[keep].drop_duplicates(["game_id", "player_id"]).sort_values(["game_id", "player_id"]).reset_index(drop=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("Official TOI route failed (%s); falling back to shift-derived builder", exc)
        source = "shift_fallback"
        gm_path = data_dir / "NHL" / "games_master.csv"
        if not gm_path.exists():
            raise RuntimeError(f"Official route failed ({exc}) and {gm_path} missing for fallback") from exc
        games = pd.read_csv(gm_path, low_memory=False)
        all_ids = select_games(games, day_from=start_date, day_to=end_dt)
        sel_ids = all_ids[-int(fallback_max_games):] if len(all_ids) > int(fallback_max_games) else all_ids
        fallback_note = (
            f"Official route failed ({exc}); built shift-derived splits for {len(sel_ids)} of "
            f"{len(all_ids)} games ({start_date}..{end_dt}). Shortfall: {len(all_ids) - len(sel_ids)} games."
        )
        run_shift_builder(sel_ids, cache_dir=str(cache_dir / "shifts"), out_dir=str(out_dir), delay=delay, pbp_from_gcs=True)
        splits = pd.read_csv(splits_path, low_memory=False)
        if "date" not in splits.columns:
            g_dates = games[["game_id", "date"]].drop_duplicates("game_id")
            splits = splits.merge(g_dates, on="game_id", how="left")

    tmp = splits_path.with_suffix(".csv.tmp")
    splits.to_csv(tmp, index=False)
    tmp.replace(splits_path)

    rep = coverage_report(splits, data_dir, min_date=start_date, source=source, fallback_note=fallback_note)
    if write_gcs:
        rep["gcs_uri"] = upload_toi_splits_gcs(splits_path)
    report_path.write_text(json.dumps(rep, indent=2))
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build NHL situation TOI splits table for MOD-04.")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    ap.add_argument("--start-date", default=DEFAULT_START_DATE)
    ap.add_argument("--end-date", default=None)
    ap.add_argument("--delay", type=float, default=0.1, help="Seconds between NHL API requests")
    ap.add_argument("--fallback-max-games", type=int, default=FALLBACK_MAX_GAMES)
    ap.add_argument("--write-gcs", action="store_true", help=f"Upload to gs://{GCS_BUCKET}/{GCS_KEY}")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rep = build_and_publish(
        data_dir=Path(args.data_dir),
        out_dir=Path(args.out_dir),
        cache_dir=Path(args.cache_dir),
        start_date=args.start_date,
        end_date=args.end_date,
        delay=args.delay,
        write_gcs=args.write_gcs,
        fallback_max_games=args.fallback_max_games,
    )
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
