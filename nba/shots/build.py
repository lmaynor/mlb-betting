"""Build the per-season shot artifacts the /edge shot chart serves.

    python -m nba.shots.build --csv NBA_2004_2025_Shots.csv --seasons 2022 2023 2024 [--dry-run]
    python -m nba.shots.build --espn-parquet play_by_play_2027.parquet --espn-season 2027      # the live 2026-27 season

Writes (through `mlb_core.storage`, i.e. GCS when MLB_GCS_BUCKET is set, else MLB_BASE_DATA):
    NBA/shots/shots_{start_year}.parquet   one row per field-goal attempt, canonical frame (see court.py)
    NBA/shots/league_{start_year}.json     league zone / hex / shot-group / defence tables

The raw adapter here reads the 24-column `NBA_2004_2025_Shots.csv` layout (stats.nba.com ShotChartDetail lineage: LOC_X is
lateral feet, LOC_Y is feet from the baseline, hoop at (0, 5.25)). A different source only needs its own `normalize_*`
that returns the same frame.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
from collections import Counter
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from mlb_core import storage as _st
from mlb_core.data.player_names import norm_name
from nba.shots.court import DIST_BINS, FRAME_COLS, HALF_Y, HEX_R, HOOP, ZONE_KEYS, ZONE_LABELS, dist_bin, hex_of, season_label, shot_group

logger = logging.getLogger(__name__)

SHOT_PREFIX = "NBA/shots"
MIN_HEX_FGA = 10          # league hexes thinner than this are dropped (a player hex has no league baseline there)
RAW_COLS = ["SEASON_1", "TEAM_ID", "PLAYER_ID", "PLAYER_NAME", "GAME_DATE", "GAME_ID", "HOME_TEAM", "AWAY_TEAM",
            "SHOT_MADE", "ACTION_TYPE", "SHOT_TYPE", "BASIC_ZONE", "ZONE_ABB", "LOC_X", "LOC_Y", "QUARTER",
            "MINS_LEFT", "SECS_LEFT"]
ZONE_INDEX = {k: i for i, k in enumerate(ZONE_KEYS)}


def shots_key(year: int) -> str:
    return f"{SHOT_PREFIX}/shots_{year}.parquet"


def league_key(year: int) -> str:
    return f"{SHOT_PREFIX}/league_{year}.json"


def _truthy(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper().isin(["TRUE", "1", "1.0", "MADE SHOT"])


def team_tricodes(raw: pd.DataFrame) -> dict:
    """TEAM_ID -> tricode: the one tricode that is in every one of the team's games (HOME_TEAM / AWAY_TEAM name both sides)."""
    out = {}
    for tid, g in raw.groupby("TEAM_ID"):
        c = Counter(g["HOME_TEAM"].tolist() + g["AWAY_TEAM"].tolist())
        out[tid] = c.most_common(1)[0][0]
    return out


def normalize_csv_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Raw 24-column layout -> canonical shot frame for ONE or more seasons (regular season only, as the file is)."""
    df = raw.copy()
    tri = team_tricodes(df)
    df["team"] = df["TEAM_ID"].map(tri)
    home = df["HOME_TEAM"].astype(str)
    df["home"] = df["team"] == home
    df["opp"] = df["AWAY_TEAM"].where(df["home"], df["HOME_TEAM"])
    df["date"] = pd.to_datetime(df["GAME_DATE"], format="%m-%d-%Y", errors="coerce").dt.strftime("%Y-%m-%d")
    df["x"] = pd.to_numeric(df["LOC_X"], errors="coerce")
    df["y"] = pd.to_numeric(df["LOC_Y"], errors="coerce")
    df["made"] = _truthy(df["SHOT_MADE"])
    df["three"] = df["SHOT_TYPE"].astype(str).str.startswith("3")
    df["group"] = df["ACTION_TYPE"].map(shot_group)
    key = df["BASIC_ZONE"].astype(str) + "|" + df["ZONE_ABB"].astype(str)
    df["zone"] = key.map(ZONE_INDEX).fillna(-1).astype("int16")
    df["season"] = pd.to_numeric(df["SEASON_1"], errors="coerce") - 1          # start year (SEASON_1 = the year it ends)
    df["player_id"] = pd.to_numeric(df["PLAYER_ID"], errors="coerce")
    df["name"] = df["PLAYER_NAME"].astype(str)
    df["name_norm"] = df["name"].map(norm_name)
    df["game_id"] = df["GAME_ID"].astype(str)
    df = df.dropna(subset=["x", "y", "date", "team", "player_id", "season"])
    # one row per attempt: the file repeats ~0.01% of attempts (same player, game, clock and spot)
    df = df.drop_duplicates(["game_id", "player_id", "QUARTER", "MINS_LEFT", "SECS_LEFT", "x", "y"])
    df = df[df["y"].between(0, HALF_Y) & df["x"].between(-25, 25)]
    q = df["y"].to_numpy()
    hq = [hex_of(a, b) for a, b in zip(df["x"].to_numpy(), q)]
    df["hex_q"] = [h[0] for h in hq]
    df["hex_r"] = [h[1] for h in hq]
    df["player_id"] = df["player_id"].astype("int64")
    df["season"] = df["season"].astype("int16")
    df["period"] = pd.to_numeric(df["QUARTER"], errors="coerce").fillna(0).astype("int8")
    df["assisted"] = False                                    # the file has no passer; ESPN seasons do (nba/shots/espn.py)
    df["ast_id"] = pd.array([pd.NA] * len(df), dtype="Int64")
    df["ast_norm"] = ""
    return df[FRAME_COLS].reset_index(drop=True)


def _pct(m: int, a: int):
    return round(m / a, 4) if a else None


def league_artifact(df: pd.DataFrame, year: int, source: str) -> dict:
    """League tables for one season's canonical frame."""
    z = df[df["zone"] >= 0]
    zones = []
    for i, key in enumerate(ZONE_KEYS):
        g = z[z["zone"] == i]
        a, m = int(len(g)), int(g["made"].sum())
        zones.append({"key": key, "label": ZONE_LABELS[key], "fga": a, "fgm": m,
                      "cx": round(float(g["x"].mean()), 2) if a else 0.0, "cy": round(float(g["y"].mean()), 2) if a else 0.0})
    df = df.assign(_pts=np.where(df["made"], np.where(df["three"], 3, 2), 0))
    hx = df.groupby(["hex_q", "hex_r"]).agg(fga=("made", "size"), fgm=("made", "sum"), pts=("_pts", "sum"))
    hx = hx[hx["fga"] >= MIN_HEX_FGA]
    # a cell's zone = its modal zone, so the zone view can colour whole cells
    mode = z.groupby(["hex_q", "hex_r"])["zone"].agg(lambda s: int(s.mode().iloc[0]))
    hexes = [[int(q), int(r), int(row.fga), int(row.fgm), int(mode.get((q, r), -1)), int(row.pts)] for (q, r), row in hx.iterrows()]
    quarters = [[int((df["period"] == p).sum()), int(df[df["period"] == p]["made"].sum())] for p in range(1, 6)]
    has_assist = bool(df["assisted"].any())
    assist = None
    if has_assist:       # league makes and assisted makes by zone: the base rate for "how much of his scoring is created by others"
        assist = [[int(((z["zone"] == i) & z["made"]).sum()), int(((z["zone"] == i) & z["made"] & z["assisted"]).sum())] for i in range(len(ZONE_KEYS))]
    groups = {k: [int(len(g)), int(g["made"].sum())] for k, g in df.groupby("group")}
    db = dist_bin(df["x"].to_numpy(), df["y"].to_numpy())
    dist = [[int((db == i).sum()), int(df["made"].to_numpy()[db == i].sum())] for i in range(DIST_BINS)]
    defense = {}
    for team, g in z.groupby("opp"):          # `opp` is the DEFENDING team for these shooters
        arr = [[0, 0] for _ in ZONE_KEYS]
        for i, gg in g.groupby("zone"):
            arr[int(i)] = [int(len(gg)), int(gg["made"].sum())]
        defense[str(team)] = {"zones": arr, "fga": int(len(g)), "fg3a": int(g["three"].sum())}
    return {
        "season": year, "label": season_label(year), "source": source, "built_at": datetime.now(timezone.utc).isoformat(),
        "n_shots": int(len(df)), "fga": int(len(df)), "fgm": int(df["made"].sum()), "fg3a": int(df["three"].sum()),
        "fg3m": int((df["three"] & df["made"]).sum()), "n_games": int(df["game_id"].nunique()),
        "last_game": str(df["date"].max()), "hex_r": HEX_R, "hoop": list(HOOP),
        "zones": zones, "hexes": hexes, "groups": groups, "dist": dist, "quarters": quarters, "has_assist": has_assist,
        "assist": assist, "defense": defense,
    }


def read_csv_seasons(path: str, years: list[int], chunksize: int = 500_000) -> pd.DataFrame:
    want = {y + 1 for y in years}
    parts = []
    for chunk in pd.read_csv(path, usecols=RAW_COLS, chunksize=chunksize):
        c = chunk[pd.to_numeric(chunk["SEASON_1"], errors="coerce").isin(want)]
        if len(c):
            parts.append(c)
    if not parts:
        raise SystemExit(f"no rows for seasons {years} in {path}")
    return pd.concat(parts, ignore_index=True)


def write_season(df: pd.DataFrame, year: int, source: str, dry_run: bool = False) -> dict:
    """One season's canonical frame -> `NBA/shots/shots_{year}.parquet` + `league_{year}.json` (nothing written on dry_run)."""
    art = league_artifact(df, year, source)
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, compression="zstd")
    info = {"rows": len(df), "parquet_bytes": buf.tell(), "json_bytes": len(json.dumps(art)), "fg_pct": _pct(art["fgm"], art["fga"]),
            "last_game": art["last_game"], "n_games": art["n_games"]}
    if not dry_run:
        _st.write_bytes(buf.getvalue(), shots_key(year))
        _st.write_bytes(json.dumps(art, separators=(",", ":")).encode(), league_key(year))
    return info


def build(csv_path: str, years: list[int], source: str, dry_run: bool = False) -> dict:
    raw = read_csv_seasons(csv_path, years)
    out = {}
    for y in years:
        part = raw[pd.to_numeric(raw["SEASON_1"], errors="coerce") == y + 1]
        if part.empty:
            logger.warning("season %s: no rows", y)
            continue
        out[y] = write_season(normalize_csv_frame(part), y, source, dry_run)
    return out


def build_espn(parquet_path: str, release_season: int, dry_run: bool = False) -> dict:
    """One hoopR `play_by_play_{release_season}.parquet` (release_season = the year the season ENDS) -> artifacts for START year
    release_season - 1. Re-run it as the live season's file grows."""
    from nba.shots.espn import normalize_espn_frame
    df = normalize_espn_frame(pd.read_parquet(parquet_path), release_season)
    year = release_season - 1
    return {year: write_season(df, year, f"ESPN play-by-play via sportsdataverse/hoopR (play_by_play_{release_season}.parquet)", dry_run)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", help="the 24-column NBA shot file")
    ap.add_argument("--seasons", nargs="+", type=int, help="with --csv: season START years, e.g. 2024 = 2024-25")
    ap.add_argument("--espn-parquet", help="a hoopR play_by_play_{N}.parquet")
    ap.add_argument("--espn-season", type=int, help="with --espn-parquet: N, the year the season ENDS (2027 = 2026-27)")
    ap.add_argument("--source", default="NBA_2004_2025_Shots.csv (user-supplied; stats.nba.com ShotChartDetail lineage)")
    ap.add_argument("--dry-run", action="store_true", help="compute and size the artifacts, write nothing")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    out = {}
    if a.csv:
        if not a.seasons:
            ap.error("--csv needs --seasons")
        out.update(build(a.csv, a.seasons, a.source, a.dry_run))
    if a.espn_parquet:
        if not a.espn_season:
            ap.error("--espn-parquet needs --espn-season")
        out.update(build_espn(a.espn_parquet, a.espn_season, a.dry_run))
    if not out:
        ap.error("give --csv/--seasons and/or --espn-parquet/--espn-season")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
