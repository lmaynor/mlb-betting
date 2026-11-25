"""C1 NHL primary/secondary assist split from flattened shots and raw PBP.

NHL/skater_boxscores_master.csv carries total `assists` and `power_play_assists`
only, with no primary/secondary split. Flattened shot tables
(`NHL/shots/shots_{season}.csv`, produced by `nhl.data.flatten.flatten_shots`)
and raw NHL play-by-play payloads record `assist1_id`/`assist2_id` on every
non-shootout goal event, enabling an exact split reconstruction.
"""
from __future__ import annotations

import pandas as pd

SPLIT_COLUMNS = [
    "game_id",
    "player_id",
    "primary_assists",
    "secondary_assists",
    "assists_split_total",
]


def _empty_split() -> pd.DataFrame:
    return pd.DataFrame(columns=SPLIT_COLUMNS)


def split_from_shots(shots_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate goal events (excluding shootout `period_type == 'SO'`) into
    per `(game_id, player_id)` primary and secondary assist counts.
    """
    if shots_df is None or shots_df.empty:
        return _empty_split()

    ptype = shots_df["period_type"].fillna("").astype(str) if "period_type" in shots_df.columns else ""
    goals = shots_df[(shots_df["event"] == "goal") & (ptype != "SO")]
    if goals.empty:
        return _empty_split()

    def _count_role(col: str, out_col: str) -> pd.DataFrame:
        if col not in goals.columns:
            return pd.DataFrame(columns=["game_id", "player_id", out_col])
        sub = goals[["game_id", col]].dropna(subset=["game_id", col]).copy()
        if sub.empty:
            return pd.DataFrame(columns=["game_id", "player_id", out_col])
        sub["game_id"] = pd.to_numeric(sub["game_id"], errors="coerce")
        sub["player_id"] = pd.to_numeric(sub[col], errors="coerce")
        sub = sub.dropna(subset=["game_id", "player_id"])
        if sub.empty:
            return pd.DataFrame(columns=["game_id", "player_id", out_col])
        sub["game_id"] = sub["game_id"].astype("int64")
        sub["player_id"] = sub["player_id"].astype("int64")
        return sub.groupby(["game_id", "player_id"], as_index=False).size().rename(columns={"size": out_col})

    prim = _count_role("assist1_id", "primary_assists")
    sec = _count_role("assist2_id", "secondary_assists")
    if prim.empty and sec.empty:
        return _empty_split()

    merged = prim.merge(sec, on=["game_id", "player_id"], how="outer").fillna(0)
    merged["primary_assists"] = merged["primary_assists"].astype("int64")
    merged["secondary_assists"] = merged["secondary_assists"].astype("int64")
    merged["assists_split_total"] = (merged["primary_assists"] + merged["secondary_assists"]).astype("int64")
    return merged[SPLIT_COLUMNS].sort_values(["game_id", "player_id"]).reset_index(drop=True)


def split_from_pbp(pbp: dict, game_id: int | None = None) -> pd.DataFrame:
    """Aggregate primary/secondary assists directly from a raw NHL PBP JSON payload."""
    if not pbp:
        return _empty_split()
    gid = game_id if game_id is not None else pbp.get("id")
    if gid is None:
        return _empty_split()
    rows = []
    for play in pbp.get("plays", []) or []:
        if play.get("typeDescKey") != "goal":
            continue
        pd_ = play.get("periodDescriptor") or {}
        if pd_.get("periodType") == "SO":
            continue
        details = play.get("details") or {}
        rows.append({
            "game_id": gid,
            "event": "goal",
            "period_type": pd_.get("periodType"),
            "assist1_id": details.get("assist1PlayerId"),
            "assist2_id": details.get("assist2PlayerId"),
        })
    return split_from_shots(pd.DataFrame(rows))


def verify_split(split_df: pd.DataFrame, master_df: pd.DataFrame) -> dict:
    """Verify aggregated split totals against `NHL/skater_boxscores_master.csv`.

    Missing split rows are treated as 0 assists. Returns exact verification counts.
    """
    m = master_df[["game_id", "player_id", "assists"]].dropna(subset=["game_id", "player_id"]).copy()
    m["game_id"] = pd.to_numeric(m["game_id"], errors="coerce").astype("int64")
    m["player_id"] = pd.to_numeric(m["player_id"], errors="coerce").astype("int64")
    m["assists"] = pd.to_numeric(m["assists"], errors="coerce").fillna(0).astype("int64")
    m = m.groupby(["game_id", "player_id"], as_index=False)["assists"].sum()

    if split_df is None or split_df.empty:
        s = pd.DataFrame(columns=["game_id", "player_id", "assists_split_total"])
    else:
        s = split_df[["game_id", "player_id", "assists_split_total"]].copy()
        s["game_id"] = pd.to_numeric(s["game_id"], errors="coerce").astype("int64")
        s["player_id"] = pd.to_numeric(s["player_id"], errors="coerce").astype("int64")
        s["assists_split_total"] = pd.to_numeric(s["assists_split_total"], errors="coerce").fillna(0).astype("int64")
        s = s.groupby(["game_id", "player_id"], as_index=False)["assists_split_total"].sum()

    merged = m.merge(s, on=["game_id", "player_id"], how="outer", indicator=True)
    merged["assists"] = merged["assists"].fillna(0).astype("int64")
    merged["assists_split_total"] = merged["assists_split_total"].fillna(0).astype("int64")

    mismatches = int((merged["assists_split_total"] != merged["assists"]).sum())
    split_total = int(merged["assists_split_total"].sum())
    master_total = int(merged["assists"].sum())

    # Directional breakdown (coordinator amendment, Nov 25, 2026): the outer-join
    # mismatch count conflates two different things. Master-side mismatches are
    # real disagreements on player-games the boxscore master carries. Orphan
    # split rows are assists the PBP credits to a player-game with NO master
    # boxscore row at all (observed: 399 rows / 405 assists over 2019-2026,
    # ~0.4% of split assists, spread across all seasons and game types); they
    # drop out of any training join onto the master by construction.
    master_side = merged[merged["_merge"] != "right_only"]
    orphan = merged[merged["_merge"] == "right_only"]
    master_side_mismatches = int((master_side["assists_split_total"] != master_side["assists"]).sum())
    return {
        "games": int(merged["game_id"].nunique()),
        "player_game_rows_checked": int(len(merged)),
        "mismatches": mismatches,
        "master_side_rows_checked": int(len(master_side)),
        "master_side_mismatches": master_side_mismatches,
        "orphan_split_rows": int(len(orphan)),
        "orphan_split_assists": int(orphan["assists_split_total"].sum()),
        "split_total": split_total,
        "master_total": master_total,
        "exact_match": bool(mismatches == 0 and split_total == master_total),
        "exact_match_on_master_population": bool(master_side_mismatches == 0),
    }
