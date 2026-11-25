"""MOD-04e U1: outcome labels module (imported only by training/grading, never by features.py).

Joins nflverse player_stats actuals and nfl.data.outcome_matching statuses onto a
pregame feature frame by (player_id, game_id).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from nfl.data import outcome_matching as om

LABEL_FAMILIES = (
    "receptions",
    "rec_yds",
    "rush_attempts",
    "rush_yds",
    "pass_attempts",
    "pass_completions",
    "interceptions",
    "anytime_td",
)


def build_labels(
    features_df: pd.DataFrame,
    *,
    player_stats: pd.DataFrame,
    schedules: pd.DataFrame,
    players: Optional[pd.DataFrame] = None,
    snap_counts: Optional[pd.DataFrame] = None,
    rosters_weekly: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Join actual outcomes + outcome_matching status onto features_df.

    Returns a DataFrame indexed identically to features_df containing keys
    (player_id, game_id, season, week), outcome_status, gradeable, outcome_reason,
    and one actual column per family in LABEL_FAMILIES (plus td_count).
    """
    if features_df.empty:
        cols = ["player_id", "game_id", "season", "week", "outcome_status", "gradeable", "outcome_reason", "td_count", *LABEL_FAMILIES]
        return pd.DataFrame(columns=cols)

    out = pd.DataFrame({
        "player_id": features_df["player_id"].astype(str),
        "game_id": features_df["game_id"].astype(str),
        "season": pd.to_numeric(features_df["season"], errors="coerce").astype("Int64"),
        "week": pd.to_numeric(features_df["week"], errors="coerce").astype("Int64"),
    }, index=features_df.index)

    ps = player_stats.copy() if player_stats is not None else pd.DataFrame()
    snaps = snap_counts.copy() if snap_counts is not None else pd.DataFrame()
    rosters = rosters_weekly.copy() if rosters_weekly is not None else pd.DataFrame()
    pl = players.copy() if players is not None else pd.DataFrame()

    # Index player_stats by (player_id, game_id)
    stats_by_pg: dict[tuple[str, str], dict] = {}
    if not ps.empty and "player_id" in ps.columns and "game_id" in ps.columns:
        for row in ps.to_dict("records"):
            stats_by_pg[(str(row["player_id"]), str(row["game_id"]))] = row

    # Crosswalk gsis_id -> pfr_id and snap counts by (gsis_id, game_id)
    pfr_to_gsis: dict[str, str] = {}
    if not pl.empty and "pfr_id" in pl.columns and "gsis_id" in pl.columns:
        xw = pl[["pfr_id", "gsis_id"]].dropna()
        pfr_to_gsis = dict(zip(xw["pfr_id"].astype(str), xw["gsis_id"].astype(str)))

    snaps_by_pg: dict[tuple[str, str], float] = {}
    if not snaps.empty and "game_id" in snaps.columns:
        id_col = "gsis_id" if "gsis_id" in snaps.columns else ("player_id" if "player_id" in snaps.columns else None)
        tot_col = "total_snaps" if "total_snaps" in snaps.columns else ("offense_snaps" if "offense_snaps" in snaps.columns else None)
        for row in snaps.to_dict("records"):
            gid = str(row.get("game_id") or "")
            pid = str(row.get(id_col) or "") if id_col else pfr_to_gsis.get(str(row.get("pfr_player_id") or ""), "")
            if not pid or not gid:
                continue
            val = row.get(tot_col) if tot_col else None
            if val is not None and pd.notna(val):
                snaps_by_pg[(pid, gid)] = max(snaps_by_pg.get((pid, gid), 0.0), float(val))

    # Weekly roster index by (season, week, gsis_id)
    roster_by_swp: dict[tuple[int, int, str], list[dict]] = {}
    roster_sw_covered: set[tuple[int, int]] = set()
    if not rosters.empty and "season" in rosters.columns and "week" in rosters.columns:
        pid_col = "gsis_id" if "gsis_id" in rosters.columns else ("player_id" if "player_id" in rosters.columns else None)
        for row in rosters.to_dict("records"):
            s_v, w_v = row.get("season"), row.get("week")
            if pd.isna(s_v) or pd.isna(w_v):
                continue
            s_i, w_i = int(s_v), int(w_v)
            roster_sw_covered.add((s_i, w_i))
            if pid_col and row.get(pid_col) is not None and pd.notna(row.get(pid_col)):
                roster_by_swp.setdefault((s_i, w_i, str(row[pid_col])), []).append(row)

    statuses: list[str] = []
    gradeables: list[bool] = []
    reasons: list[str] = []
    fam_vals: dict[str, list[float]] = {f: [] for f in (*LABEL_FAMILIES, "td_count")}

    for r in out.itertuples(index=False):
        pid, gid = r.player_id, r.game_id
        s_i = int(r.season) if pd.notna(r.season) else None
        w_i = int(r.week) if pd.notna(r.week) else None

        srow = stats_by_pg.get((pid, gid))
        if srow is not None:
            statuses.append(om.STATUS_STAT)
            gradeables.append(True)
            reasons.append("")
            for fam in LABEL_FAMILIES:
                val, _ = om._extract_stat_value(fam, srow)
                fam_vals[fam].append(0.0 if val is None else float(val))
            r_td = float(srow.get("rushing_tds") or 0.0) if pd.notna(srow.get("rushing_tds")) else 0.0
            c_td = float(srow.get("receiving_tds") or 0.0) if pd.notna(srow.get("receiving_tds")) else 0.0
            fam_vals["td_count"].append(r_td + c_td)
            continue

        tot_snaps = snaps_by_pg.get((pid, gid))
        if tot_snaps is not None:
            if tot_snaps > 0:
                statuses.append(om.STATUS_PLAYED_ZERO)
                gradeables.append(True)
                reasons.append("played_zero_snaps")
                for fam in (*LABEL_FAMILIES, "td_count"):
                    fam_vals[fam].append(0.0)
            else:
                statuses.append(om.STATUS_DNP)
                gradeables.append(False)
                reasons.append("dnp_zero_snaps")
                for fam in (*LABEL_FAMILIES, "td_count"):
                    fam_vals[fam].append(float("nan"))
            continue

        if s_i is not None and w_i is not None and (s_i, w_i) in roster_sw_covered:
            r_rows = roster_by_swp.get((s_i, w_i, pid), [])
            is_inact, why = om._roster_row_is_inactive(r_rows)
            if is_inact:
                statuses.append(om.STATUS_INACTIVE)
                gradeables.append(False)
                reasons.append(why)
            else:
                statuses.append(om.STATUS_UNRESOLVED)
                gradeables.append(False)
                reasons.append("active_no_snap_row")
        else:
            statuses.append(om.STATUS_UNRESOLVED)
            gradeables.append(False)
            reasons.append("no_stat_or_snap_row")

        for fam in (*LABEL_FAMILIES, "td_count"):
            fam_vals[fam].append(float("nan"))

    out["outcome_status"] = statuses
    out["gradeable"] = pd.Series(gradeables, index=out.index, dtype="bool")
    out["outcome_reason"] = reasons
    for fam in (*LABEL_FAMILIES, "td_count"):
        out[fam] = pd.Series(fam_vals[fam], index=out.index, dtype="float64")
    return out
