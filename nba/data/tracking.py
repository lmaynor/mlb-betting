"""nba.data.tracking -- PRIOR-SEASON player tracking (passing, rebound chances) joined to the board's player keys.

SOURCE. The only tracking data this repo has is the community mirror github.com/gabriel1200/site_Data (stats.nba.com tracking
tables saved per season: `{year}/player_tracking/{passing,dreb,oreb,touches,...}.csv`, year = the season's END year, so
2025 = 2024-25). It carries NO license and no README; using it was the owner's call (2026-11-01). Nothing in this repo
fetched it automatically: `mirror()` copies a local clone into `NBA/tracking/{year}/{table}.csv` (the owner runs it), and
`load()` reads that mirror (or a local directory). The earlier belief that these tables were "already banked in
NBA/stats_nba/raw" was false: that prefix does not exist and the Kaggle ingest never ran.

GRANULARITY IS THE CONSTRAINT. Every table is a SEASON AGGREGATE per player (no per-game rows), so a season's table contains
the games the replay is trying to predict. The only leak-free use is the PRIOR completed season's table: target season S (the
box-score master's `season_year`, 2024 = 2024-25) reads the file of year S (= season S-1..S). The current, partial season is
never read.

DATA GAPS (verified 2026-11-01): passing / touches are complete 2014-2026 (2027 = a 5-game start of 2026-27). The rebound
CHANCE files (dreb / oreb) are complete only through 2023 (2022-23); the 2024 file stops at <= 23 games per player and 2025 / 2026
are missing, so the rebound factor cannot be served until those files exist (`rebound_factor` returns 1.0 when either table is
absent or unusable).

FACTORS (both are "remove the teammates' luck" ratios, shrunk and clipped; neither is validated until nba/props/engine_eval.py
says so -- they are OFF by default in ev.daily_projections.NBA_FEATURES):
  assists : potential assists are the pass-level opportunity; AST / PotentialAST is the conversion, which depends on teammates'
            shooting. ratio = PotentialAST * shrunk_conversion / AST with the conversion shrunk to the league rate (K = 150
            potential assists). A player whose assists ran lucky last season is regressed down, an unlucky one up.
  rebounds: ratio = chances * shrunk_conversion / rebounds, chances = DREBChances + OREBChances (K = 200 chances).
Join key: the board's `norm_name` of PLAYER (passing / touches also carry the NBA PLAYER_ID, kept as `person_id`). A name that
appears twice in a year's table is dropped from that year (fail closed, like the rest of the repo's identity layers).
"""
from __future__ import annotations

import argparse
import io
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from mlb_core.data.cockpit_form import norm_name

log = logging.getLogger("nba.data.tracking")

TRACKING_PREFIX = "NBA/tracking"                       # GCS: NBA/tracking/{year}/{table}.csv
SOURCE_SUBDIR = "player_tracking"                      # inside a season folder of the community repo
TABLES = ("passing", "dreb", "oreb", "touches")
MIN_MINUTES = 300.0                                    # prior-season minutes a player needs for any factor
MIN_POTENTIAL_AST = 40.0
MIN_CHANCES = 100.0
K_AST, K_REB = 150.0, 200.0                            # prior opportunities behind the league conversion rate
LEAGUE_MIN_MINUTES = 200.0                             # players behind the league conversion rate
FACTOR_CLIP = (0.85, 1.15)
MIN_FULL_SEASON_GP = 30.0                              # a rebound file whose max GP is below this is a partial pull


def _name_col(df: pd.DataFrame) -> str:
    return "PLAYER" if "PLAYER" in df.columns else "Player"


class TrackingYear:
    """One season's tables keyed by norm_name; a table that is missing or looks like a partial pull is simply absent."""

    def __init__(self, year: int, frames: dict):
        self.year = int(year)
        self.rows: dict = {}                            # table -> {norm name: row dict}
        self.dropped: dict = {}                         # table -> names dropped as ambiguous
        self.partial: set = set()                       # tables rejected as partial pulls
        for table, df in frames.items():
            if df is None or df.empty:
                continue
            col = _name_col(df)
            df = df.copy()
            df["_nn"] = df[col].map(norm_name)
            if table in ("dreb", "oreb") and float(df["GP"].max()) < MIN_FULL_SEASON_GP:
                self.partial.add(table)
                continue
            dup = df["_nn"].duplicated(keep=False)
            self.dropped[table] = sorted(set(df.loc[dup, "_nn"]))
            self.rows[table] = {r["_nn"]: r for r in df.loc[~dup & (df["_nn"] != "")].to_dict("records")}
        self._league: dict = {}

    def has(self, *tables: str) -> bool:
        return all(t in self.rows for t in tables)

    def person_id(self, pn: str) -> int | None:
        """NBA person id (stats.nba.com PLAYER_ID) from passing / touches; None if absent."""
        for table in ("passing", "touches"):
            r = self.rows.get(table, {}).get(pn)
            if r is not None and r.get("PLAYER_ID") == r.get("PLAYER_ID"):
                return int(r["PLAYER_ID"])
        return None

    def _league_rate(self, key: str, num, den) -> float | None:
        if key not in self._league:
            n = d = 0.0
            tables = {"ast": ("passing",), "reb": ("dreb", "oreb")}[key]
            for t in tables:
                for r in self.rows.get(t, {}).values():
                    if float(r.get("MIN") or 0) >= LEAGUE_MIN_MINUTES:
                        n += float(num(r))
                        d += float(den(r))
            self._league[key] = (n / d) if d > 0 else None
        return self._league[key]

    # -- factors ------------------------------------------------------------------------------------------------
    def assist_ratio(self, pn: str) -> float:
        r = self.rows.get("passing", {}).get(pn)
        if r is None:
            return 1.0
        ast, pot, mins = float(r.get("AST") or 0), float(r.get("PotentialAST") or 0), float(r.get("MIN") or 0)
        lg = self._league_rate("ast", lambda x: x.get("AST") or 0, lambda x: x.get("PotentialAST") or 0)
        if lg is None or mins < MIN_MINUTES or pot < MIN_POTENTIAL_AST or ast <= 0:
            return 1.0
        conv = (ast + K_AST * lg) / (pot + K_AST)
        return float(np.clip(pot * conv / ast, FACTOR_CLIP[0], FACTOR_CLIP[1]))

    def rebound_ratio(self, pn: str) -> float:
        d, o = self.rows.get("dreb", {}).get(pn), self.rows.get("oreb", {}).get(pn)
        if d is None or o is None:
            return 1.0
        reb = float(d.get("DREB") or 0) + float(o.get("OREB") or 0)
        chances = float(d.get("DREBChances") or 0) + float(o.get("OREBChances") or 0)
        mins = float(d.get("MIN") or 0)
        lg = self._league_rate("reb", lambda x: (x.get("DREB") or x.get("OREB") or 0),
                               lambda x: (x.get("DREBChances") or x.get("OREBChances") or 0))
        if lg is None or mins < MIN_MINUTES or chances < MIN_CHANCES or reb <= 0:
            return 1.0
        conv = (reb + K_REB * lg) / (chances + K_REB)
        return float(np.clip(chances * conv / reb, FACTOR_CLIP[0], FACTOR_CLIP[1]))


class Tracking:
    """{year: TrackingYear}. `assist_factor(pn, season_year, gamma)` reads the table of year == season_year (the PRIOR
    completed season of a target season whose master label is season_year); 1.0 when it is not loaded."""

    def __init__(self, years: dict | None = None):
        self.years: dict = dict(years or {})

    def year_for(self, season_year: int) -> TrackingYear | None:
        return self.years.get(int(season_year))

    def assist_factor(self, pn: str, season_year: int, gamma: float = 1.0) -> float:
        ty = self.year_for(season_year)
        return 1.0 if ty is None or not gamma else float(np.clip(ty.assist_ratio(pn) ** gamma, *FACTOR_CLIP))

    def rebound_factor(self, pn: str, season_year: int, gamma: float = 1.0) -> float:
        ty = self.year_for(season_year)
        return 1.0 if ty is None or not gamma else float(np.clip(ty.rebound_ratio(pn) ** gamma, *FACTOR_CLIP))

    def coverage(self) -> dict:
        return {y: {"tables": sorted(ty.rows), "partial": sorted(ty.partial), "players": len(ty.rows.get("passing", {}))}
                for y, ty in sorted(self.years.items())}


def season_year_of(day: str) -> int:
    """'YYYY-MM-DD' (tip date) -> the box-score master's season_year: Sep..Dec -> that year, Jan..Aug -> year - 1."""
    y, m = int(day[:4]), int(day[5:7])
    return y if m >= 9 else y - 1


# ---------------------------------------------------------------------------------------------------- I/O
def _read(local_dir: str | None, year: int, table: str, source_layout: bool) -> pd.DataFrame | None:
    rel = f"{year}/{SOURCE_SUBDIR}/{table}.csv" if source_layout else f"{year}/{table}.csv"
    try:
        if local_dir:
            p = Path(local_dir) / rel
            return pd.read_csv(p) if p.exists() else None
        from mlb_core import storage
        key = f"{TRACKING_PREFIX}/{rel}"
        return pd.read_csv(io.BytesIO(storage.read_bytes(key))) if storage.exists(key) else None
    except Exception as exc:                                                               # noqa: BLE001
        log.warning("tracking %s/%s unreadable: %s", year, table, exc)
        return None


def load(years, local_dir: str | None = None, source_layout: bool = False) -> Tracking:
    """Tracking for `years` from a local directory (`source_layout=True` = a clone of the community repo) or from the GCS
    mirror (`NBA/tracking/{year}/{table}.csv`). A missing year / table is just absent; nothing here raises on missing data."""
    out = {}
    for y in years:
        frames = {t: _read(local_dir, y, t, source_layout) for t in TABLES}
        if any(f is not None for f in frames.values()):
            out[int(y)] = TrackingYear(y, frames)
    return Tracking(out)


def mirror(src_dir: str, years, dry_run: bool = True) -> dict:
    """Copy `{src_dir}/{year}/player_tracking/{table}.csv` to `NBA/tracking/{year}/{table}.csv` with a manifest. Dry-run by
    default; it never reads the network. Returns the manifest."""
    manifest = {"source": "github.com/gabriel1200/site_Data (unlicensed community mirror; owner approved use 2026-11-01)",
                "files": []}
    for y in years:
        for t in TABLES:
            p = Path(src_dir) / str(y) / SOURCE_SUBDIR / f"{t}.csv"
            if not p.exists():
                continue
            key = f"{TRACKING_PREFIX}/{y}/{t}.csv"
            manifest["files"].append({"key": key, "bytes": p.stat().st_size})
            if not dry_run:
                from mlb_core import storage
                storage.write_bytes(p.read_bytes(), key)
    if not dry_run:
        from mlb_core import storage
        storage.write_bytes(json.dumps(manifest, indent=1).encode(), f"{TRACKING_PREFIX}/_manifest.json")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mirror community NBA tracking tables to NBA/tracking/ (dry-run by default).")
    ap.add_argument("--src", required=True, help="local clone of the community repo")
    ap.add_argument("--years", default="2014-2026", help="e.g. 2014-2026 or 2023,2024")
    ap.add_argument("--write", action="store_true", help="actually write to storage (default: dry run)")
    a = ap.parse_args(argv)
    if "-" in a.years:
        lo, hi = a.years.split("-")
        years = range(int(lo), int(hi) + 1)
    else:
        years = [int(y) for y in a.years.split(",")]
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    m = mirror(a.src, years, dry_run=not a.write)
    print(json.dumps({"files": len(m["files"]), "written": bool(a.write)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
