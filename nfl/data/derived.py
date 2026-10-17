"""Derived (gold) tables built from the mirrored nflverse play-by-play: team-game and player-game usage context.

    python -m nfl.data.derived --seasons 2018-2026 [--force] [--dry-run]

The mirror under NFL/nflverse/ is byte-for-byte upstream. This module adds a small, versioned, QUERYABLE layer under
NFL/derived/ so features, graders and the projection board read ~1 MB per season instead of re-aggregating a ~20 MB
play-by-play file (372 columns) each time:

    NFL/derived/team_game_pbp/team_game_pbp_{season}.parquet     one row per (game_id, team): own offense + the
                                                                 defense's allowed rates (columns off_* / def_*)
    NFL/derived/player_game_pbp/player_game_pbp_{season}.parquet one row per (game_id, player_id): targets, air yards,
                                                                 red-zone / goal-line usage, EPA
    NFL/derived/_manifest.json                                   key -> {rows, version, source_sha256, built_at}

The definitions follow nflfastR conventions (xpass / pass_oe are nflfastR's own expected-pass model, shipped in the
pbp; "neutral situation" is the standard Baldwin definition): they exist because the best predictor of a player's
volume is his team's play volume and pass rate, and the existing feature builder only had game-level totals.

Neutral situation = 1st or 2nd down, win probability between 0.20 and 0.80, outside the last two minutes of either
half. Rates over neutral plays are the pass tendency a game script does not explain away.

Everything below the I/O section is pure (DataFrame in, DataFrame out) and unit-tested offline.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from nfl import config

logger = logging.getLogger(__name__)

DERIVED_VERSION = "pbp_ctx_v1"          # bump when a definition below changes: forces a rebuild of every season
DERIVED_PREFIX = f"{config.NFL_PREFIX}/derived"
DERIVED_MANIFEST_KEY = f"{DERIVED_PREFIX}/_manifest.json"

TEAM_TABLE = "team_game_pbp"
PLAYER_TABLE = "player_game_pbp"

NEUTRAL_WP = (0.20, 0.80)
NEUTRAL_MIN_HALF_SECONDS = 120
EXPLOSIVE_PASS_YDS = 20
EXPLOSIVE_RUSH_YDS = 10
DEEP_AIR_YARDS = 20
RZ_YARDLINE = 20                         # "red zone" for targets / play calling (yardline_100 <= 20)
RZ_CARRY_YARDLINE = 10                   # carries inside the 10
GL_CARRY_YARDLINE = 3                    # carries inside the 3
GL_TGT_YARDLINE = 10                     # targets inside the 10
MAX_PLAY_GAP_SEC = 60                    # a gap between consecutive plays of one drive above this is not "pace"

# the only pbp columns the derivations read (parquet column projection keeps the load small)
PBP_COLUMNS = [
    "game_id", "play_id", "season", "week", "season_type", "posteam", "defteam", "drive", "play_type", "pass", "rush",
    "pass_attempt", "rush_attempt", "qb_dropback", "qb_kneel", "qb_spike", "sack", "epa", "success", "yards_gained",
    "air_yards", "yards_after_catch", "cpoe", "xpass", "pass_oe", "down", "wp", "half_seconds_remaining",
    "game_seconds_remaining", "yardline_100", "shotgun", "no_huddle", "receiver_player_id", "rusher_player_id",
    "passer_player_id", "complete_pass",
]

TEAM_METRICS = ["plays", "dropbacks", "pass_rate", "neutral_plays", "neutral_pass_rate", "xpass", "pass_oe",
                "epa_play", "epa_dropback", "epa_rush", "success_rate", "expl_pass_rate", "expl_rush_rate",
                "sack_rate", "adot", "cpoe", "no_huddle_rate", "shotgun_rate", "sec_per_play_neutral",
                "rz_plays", "rz_pass_rate"]
PLAYER_METRICS = ["tgt_pbp", "air_yards", "deep_tgt", "tgt_rz20", "tgt_in10", "rec_epa", "car_pbp", "car_rz10",
                  "car_in3", "rush_epa", "rush_expl10", "dropbacks_pbp", "pass_epa"]


def _flag(p: pd.DataFrame, col: str) -> pd.Series:
    """0/1 float of a possibly-missing flag column."""
    if col not in p.columns:
        return pd.Series(0.0, index=p.index)
    return pd.to_numeric(p[col], errors="coerce").fillna(0.0).clip(0, 1)


def offensive_plays(pbp: pd.DataFrame) -> pd.DataFrame:
    """Scrimmage plays that say something about play calling: a pass or a run by a known offense, no kneels / spikes.

    nflfastR gives penalty-nullified snaps play_type 'no_play', so they are excluded by the play_type filter."""
    p = pbp
    keep = p["posteam"].notna() & p["play_type"].isin(["pass", "run"])
    keep &= _flag(p, "qb_kneel") == 0
    keep &= _flag(p, "qb_spike") == 0
    if "season_type" in p.columns:
        keep &= p["season_type"].isin(["REG", "POST"])
    out = p[keep].copy()
    out["_pass"] = _flag(out, "qb_dropback")          # a dropback is a pass call (incl. sacks and scrambles)
    out["_rush"] = 1.0 - out["_pass"]
    return out


def neutral_mask(p: pd.DataFrame) -> pd.Series:
    down = pd.to_numeric(p["down"], errors="coerce")
    wp = pd.to_numeric(p["wp"], errors="coerce")
    half = pd.to_numeric(p["half_seconds_remaining"], errors="coerce")
    return down.isin([1, 2]) & wp.between(*NEUTRAL_WP) & (half > NEUTRAL_MIN_HALF_SECONDS)


def _add_gap_seconds(p: pd.DataFrame) -> pd.Series:
    """Seconds between a play and the previous scrimmage play of the SAME offense in the SAME drive (NaN for a drive's
    first play and for gaps outside [0, MAX_PLAY_GAP_SEC])."""
    s = p.sort_values(["game_id", "play_id"], kind="mergesort")
    grp = s.groupby(["game_id", "posteam", "drive"], sort=False)["game_seconds_remaining"]
    gap = grp.shift(1) - s["game_seconds_remaining"]
    gap = gap.where((gap >= 0) & (gap <= MAX_PLAY_GAP_SEC))
    return gap.reindex(p.index)


def _mean(x: pd.Series) -> float:
    x = pd.to_numeric(x, errors="coerce")
    return float(x.mean()) if x.notna().any() else np.nan


def _team_agg(plays: pd.DataFrame, team_col: str) -> pd.DataFrame:
    """Per (game_id, team_col) rates over `plays`; the caller prefixes the columns."""
    p = plays
    neutral = neutral_mask(p)
    att = p[_flag(p, "pass_attempt") == 1]
    rush = p[p["_rush"] == 1]
    db = p[p["_pass"] == 1]
    rz = p[pd.to_numeric(p["yardline_100"], errors="coerce") <= RZ_YARDLINE]
    nplays = p[neutral]

    def by(df):
        return df.groupby(["game_id", team_col], sort=False)

    out = pd.DataFrame({
        "plays": by(p).size(),
        "dropbacks": by(p)["_pass"].sum(),
        "neutral_plays": by(nplays).size(),
        "neutral_pass_rate": by(nplays)["_pass"].mean(),
        "xpass": by(p)["xpass"].apply(_mean),
        "pass_oe": by(p)["pass_oe"].apply(_mean),
        "epa_play": by(p)["epa"].apply(_mean),
        "epa_dropback": by(db)["epa"].apply(_mean),
        "epa_rush": by(rush)["epa"].apply(_mean),
        "success_rate": by(p)["success"].apply(_mean),
        "expl_pass_rate": by(att.assign(_x=(pd.to_numeric(att["yards_gained"], errors="coerce")
                                           >= EXPLOSIVE_PASS_YDS).astype(float)))["_x"].mean(),
        "expl_rush_rate": by(rush.assign(_x=(pd.to_numeric(rush["yards_gained"], errors="coerce")
                                            >= EXPLOSIVE_RUSH_YDS).astype(float)))["_x"].mean(),
        "adot": by(att)["air_yards"].apply(_mean),
        "cpoe": by(att)["cpoe"].apply(_mean),
        "no_huddle_rate": by(p)["no_huddle"].apply(_mean),
        "shotgun_rate": by(p)["shotgun"].apply(_mean),
        "rz_plays": by(rz).size(),
        "rz_pass_rate": by(rz)["_pass"].mean(),
    })
    out["pass_rate"] = out["dropbacks"] / out["plays"]
    sack = p[_flag(p, "sack") == 1]
    n_sack = by(sack).size().reindex(out.index).fillna(0.0)
    out["sack_rate"] = n_sack / out["dropbacks"].where(out["dropbacks"] > 0)
    gap = _add_gap_seconds(p)
    out["sec_per_play_neutral"] = by(p.assign(_gap=gap)[neutral])["_gap"].mean()
    out[["plays", "neutral_plays", "rz_plays"]] = out[["plays", "neutral_plays", "rz_plays"]].fillna(0).astype(float)
    return out[TEAM_METRICS]


def team_game_from_pbp(pbp: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, team): the team's OFFENSE rates (off_*) and what its DEFENSE allowed (def_*) that game.

    Keys: game_id, team, season, week, opponent. A game where a team never had a scrimmage play has no row."""
    p = offensive_plays(pbp)
    if p.empty:
        return pd.DataFrame(columns=["game_id", "team", "season", "week", "opponent"]
                            + [f"off_{m}" for m in TEAM_METRICS] + [f"def_{m}" for m in TEAM_METRICS])
    off = _team_agg(p, "posteam").add_prefix("off_")
    dfn = _team_agg(p, "defteam").add_prefix("def_")
    off.index = off.index.set_names(["game_id", "team"])
    dfn.index = dfn.index.set_names(["game_id", "team"])
    out = off.join(dfn, how="outer").reset_index()
    meta = (p.groupby(["game_id", "posteam"], sort=False).agg(season=("season", "first"), week=("week", "first"),
                                                              opponent=("defteam", "first")).reset_index()
            .rename(columns={"posteam": "team"}))
    out = out.merge(meta, on=["game_id", "team"], how="left")
    # a defense row exists for every team that faced an offense; the opponent of a team's defense is the posteam
    miss = out["opponent"].isna()
    if miss.any():
        opp = (p.groupby(["game_id", "defteam"], sort=False).agg(season=("season", "first"), week=("week", "first"),
                                                                 opponent=("posteam", "first")).reset_index()
               .rename(columns={"defteam": "team"}))
        fill = out.loc[miss, ["game_id", "team"]].merge(opp, on=["game_id", "team"], how="left")
        for c in ("season", "week", "opponent"):
            out.loc[miss, c] = fill[c].to_numpy()
    out["season"] = out["season"].astype("int64")
    out["week"] = out["week"].astype("int64")
    front = ["game_id", "team", "season", "week", "opponent"]
    return out[front + [c for c in out.columns if c not in front]].sort_values(front[:2]).reset_index(drop=True)


def player_game_from_pbp(pbp: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, player_id): usage measured on the plays themselves (targets incl. incompletions, air
    yards, deep / red-zone / goal-line volume, EPA). Superset of nfl.features.props_features.aggregate_pbp_redzone."""
    p = offensive_plays(pbp)
    cols = ["game_id", "player_id", "season", "week", "team"] + PLAYER_METRICS
    if p.empty:
        return pd.DataFrame(columns=cols)
    yl = pd.to_numeric(p["yardline_100"], errors="coerce")
    ay = pd.to_numeric(p["air_yards"], errors="coerce")
    yg = pd.to_numeric(p["yards_gained"], errors="coerce")
    epa = pd.to_numeric(p["epa"], errors="coerce")

    tgt = p[(_flag(p, "pass_attempt") == 1) & p["receiver_player_id"].notna()]
    t = pd.DataFrame({
        "game_id": tgt["game_id"], "player_id": tgt["receiver_player_id"], "season": tgt["season"],
        "week": tgt["week"], "team": tgt["posteam"], "tgt_pbp": 1.0,
        "air_yards": ay.loc[tgt.index].fillna(0.0),
        "deep_tgt": (ay.loc[tgt.index] >= DEEP_AIR_YARDS).astype(float),
        "tgt_rz20": (yl.loc[tgt.index] <= RZ_YARDLINE).astype(float),
        "tgt_in10": (yl.loc[tgt.index] <= GL_TGT_YARDLINE).astype(float),
        "rec_epa": epa.loc[tgt.index]})
    run = p[(_flag(p, "rush_attempt") == 1) & p["rusher_player_id"].notna()]
    r = pd.DataFrame({
        "game_id": run["game_id"], "player_id": run["rusher_player_id"], "season": run["season"],
        "week": run["week"], "team": run["posteam"], "car_pbp": 1.0,
        "car_rz10": (yl.loc[run.index] <= RZ_CARRY_YARDLINE).astype(float),
        "car_in3": (yl.loc[run.index] <= GL_CARRY_YARDLINE).astype(float),
        "rush_epa": epa.loc[run.index],
        "rush_expl10": (yg.loc[run.index] >= EXPLOSIVE_RUSH_YDS).astype(float)})
    dbk = p[(p["_pass"] == 1) & p["passer_player_id"].notna()]
    d = pd.DataFrame({
        "game_id": dbk["game_id"], "player_id": dbk["passer_player_id"], "season": dbk["season"],
        "week": dbk["week"], "team": dbk["posteam"], "dropbacks_pbp": 1.0, "pass_epa": epa.loc[dbk.index]})

    keys = ["game_id", "player_id", "season", "week", "team"]
    parts = []
    for df, sums in ((t, ["tgt_pbp", "air_yards", "deep_tgt", "tgt_rz20", "tgt_in10", "rec_epa"]),
                     (r, ["car_pbp", "car_rz10", "car_in3", "rush_epa", "rush_expl10"]),
                     (d, ["dropbacks_pbp", "pass_epa"])):
        parts.append(df.groupby(keys, sort=False)[sums].sum(min_count=1).reset_index())
    out = parts[0]
    for nxt in parts[1:]:
        out = out.merge(nxt, on=keys, how="outer")
    for c in PLAYER_METRICS:
        if c not in out.columns:
            out[c] = np.nan
    count_cols = [c for c in PLAYER_METRICS if not c.endswith("_epa")]
    out[count_cols] = out[count_cols].fillna(0.0)          # no event of that type that game = 0 (EPA stays NaN)
    out["season"] = out["season"].astype("int64")
    out["week"] = out["week"].astype("int64")
    return out[cols].sort_values(["game_id", "player_id"]).reset_index(drop=True)


# --------------------------------------------------------------------------------------------------------------
# I/O: build + manifest
# --------------------------------------------------------------------------------------------------------------

def table_key(table: str, season: int) -> str:
    return f"{DERIVED_PREFIX}/{table}/{table}_{season}.parquet"


def _load_manifest() -> dict:
    from mlb_core import storage
    try:
        if storage.exists(DERIVED_MANIFEST_KEY):
            return json.loads(storage.read_bytes(DERIVED_MANIFEST_KEY).decode())
    except Exception as exc:                    # noqa: BLE001
        logger.warning("derived manifest unreadable (%s); starting empty", exc)
    return {}


def _source_sha(season: int) -> Optional[str]:
    """sha256 of the mirrored pbp file this season's derived tables come from (None if the manifest lacks it)."""
    from mlb_core import storage
    try:
        m = json.loads(storage.read_bytes(config.MANIFEST_KEY).decode())
        return (m.get(config.DATASETS["pbp"].key(season)) or {}).get("sha256")
    except Exception:                           # noqa: BLE001
        return None


def build_season(season: int, force: bool = False, manifest: Optional[dict] = None) -> dict:
    """Rebuild one season's derived tables unless they are current (same DERIVED_VERSION and source sha256).
    Returns {'season', 'status': built|current|no_pbp|error, ...}. Never raises."""
    from mlb_core import storage
    from nfl.data.read import load
    manifest = manifest if manifest is not None else _load_manifest()
    kt, kp = table_key(TEAM_TABLE, season), table_key(PLAYER_TABLE, season)
    sha = _source_sha(season)
    prev = manifest.get(kt) or {}
    if (not force and sha and prev.get("source_sha256") == sha and prev.get("version") == DERIVED_VERSION
            and storage.exists(kt) and storage.exists(kp)):
        return {"season": season, "status": "current"}
    try:
        pbp = load("pbp", seasons=[season], columns=PBP_COLUMNS)
    except FileNotFoundError:
        return {"season": season, "status": "no_pbp"}
    except Exception as exc:                    # noqa: BLE001
        logger.exception("derived %s: pbp load failed", season)
        return {"season": season, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
    try:
        team = team_game_from_pbp(pbp)
        player = player_game_from_pbp(pbp)
        storage.write_parquet(team, kt)
        storage.write_parquet(player, kp)
    except Exception as exc:                    # noqa: BLE001
        logger.exception("derived %s: build failed", season)
        return {"season": season, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
    now = datetime.now(timezone.utc).isoformat()
    for key, df in ((kt, team), (kp, player)):
        manifest[key] = {"rows": len(df), "version": DERIVED_VERSION, "source_sha256": sha, "built_at": now}
    return {"season": season, "status": "built", "team_rows": len(team), "player_rows": len(player)}


def run(seasons: Optional[Iterable[int]] = None, force: bool = False, today: Optional[date] = None) -> dict:
    """Build derived tables for `seasons` (default: the current season only -- the nightly refresh's job)."""
    from mlb_core import storage
    seasons = sorted(set(seasons)) if seasons else [config.current_season(today)]
    manifest = _load_manifest()
    results = [build_season(s, force, manifest) for s in seasons]
    if any(r["status"] == "built" for r in results):
        storage.write_bytes(json.dumps(manifest, indent=1, sort_keys=True).encode(), DERIVED_MANIFEST_KEY)
    bad = [r for r in results if r["status"] == "error"]
    return {"status": "error" if bad else "ok", "version": DERIVED_VERSION, "results": results}


def main(argv=None) -> int:
    from nfl.data.ingest import _parse_seasons
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", help="e.g. 2024 | 2018-2026 (default: current season)")
    ap.add_argument("--force", action="store_true", help="rebuild even when the tables are current")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run(_parse_seasons(args.seasons), args.force)
    print(json.dumps(summary, indent=1))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
