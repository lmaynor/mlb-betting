"""B1 NHL volume feature assembly (shadow-only, pre-game only, leak-free).

Assembles per-player-game pre-game features for NHL skater SOG and goalie saves from the masters
using `nhl.props.xgfeat` primitives (`prior_roll`, `prior_ratio`, `team_table`, `goalie_table`,
`skater_table`, `expected_starter_features`) with strict `shift(1)` discipline.

Status of optional external inputs (do NOT write any ingest here):
- Situation-split TOI (`nhl.data.toi_splits` -> GCS `NHL/toi_splits/`) does NOT exist yet
  (owned by another cluster). When `toi_splits` is `None`, skater deployment falls back to total
  TOI only, `pp_toi_share_prior` is `NaN`, `is_pp1_proxy` is `0`, and `deployment_source` is
  set to `"total_toi_proxy"`.
- RotoWire archive (`NHL/lineups/rotowire/`) history only spans `ROTOWIRE_SPAN`
  (`2026-11-16`..`2026-11-24`). Any rotowire-derived column is strictly `NaN` outside that span.

No prop lines, odds, or market prices are ever included as features. All output frames are gated by
`assert_feature_schema` and contain no outcome or `*_post` columns.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Mapping

import numpy as np
import pandas as pd

from nhl.props import xgfeat as X
from nhl.props.mod04_features import assert_feature_schema

ROTOWIRE_SPAN: tuple[str, str] = ("2026-11-16", "2026-11-24")

ON_TARGET_PRIOR_RATE = 0.55
ON_TARGET_PRIOR_ATT = 30.0
PP_MIN_TEAM_S = 30.0

_OUTCOME_DROP_COLS = frozenset({
    "saves", "shots_against", "goals_against", "save_pct", "decision", "starter", "is_start",
    "sog", "goals", "assists", "points", "toi_sec", "toi_min", "played", "shots_faced",
    "xga", "ga_pop", "gsax", "sa_pk", "hd_faced", "short",
    "sv_l60_post", "gsax100_l60_post", "xga_shot_l30_post",
})


def _norm_name(name: str) -> str:
    txt = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z ]", "", txt.replace("-", " ")).split())


def _get_box(frames: Mapping[str, pd.DataFrame], primary: str, alt: str) -> pd.DataFrame:
    if primary in frames:
        return frames[primary].copy()
    if alt in frames:
        return frames[alt].copy()
    raise KeyError(f"frames must contain {primary!r} (or {alt!r})")


def _fallback_xg_team(games: pd.DataFrame, skaters: pd.DataFrame, goalies: pd.DataFrame) -> pd.DataFrame:
    g = X.game_table(games)
    if g.empty:
        return pd.DataFrame(columns=["game_id", "team_id", "att_for", "xgf", "sog_for", "xgf_sog",
                                     "goals_for_pop", "hd_for", "att_pp_for", "att_against", "xga",
                                     "sog_against", "xga_sog", "goals_against_pop", "hd_against", "att_pp_against"])
    sk_agg = pd.DataFrame(columns=["game_id", "team_id", "sog_sk", "att_sk", "goals_sk"])
    if len(skaters):
        sk = skaters.copy()
        if "sog" not in sk.columns:
            sk["sog"] = np.nan
        if "attempts" not in sk.columns:
            sk["attempts"] = sk["sog"]
        if "goals" not in sk.columns:
            sk["goals"] = 0.0
        sk_agg = sk.groupby(["game_id", "team_id"], as_index=False).agg(
            sog_sk=("sog", "sum"), att_sk=("attempts", "sum"), goals_sk=("goals", "sum")
        )
    gl_agg = pd.DataFrame(columns=["game_id", "team_id", "sa_gl", "ga_gl"])
    if len(goalies):
        gl = goalies.copy()
        if "shots_against" not in gl.columns:
            gl["shots_against"] = np.nan
        if "goals_against" not in gl.columns:
            gl["goals_against"] = 0.0
        gl_agg = gl.groupby(["game_id", "team_id"], as_index=False).agg(
            sa_gl=("shots_against", "sum"), ga_gl=("goals_against", "sum")
        )

    rows = []
    for side, other in (("home", "away"), ("away", "home")):
        part = pd.DataFrame({
            "game_id": g["game_id"],
            "team_id": g[f"{side}_id"],
            "opp_id": g[f"{other}_id"],
            "sog_g": games.loc[g.index, f"{side}_sog"] if f"{side}_sog" in games.columns else np.nan,
            "goals_g": games.loc[g.index, f"{side}_score"] if f"{side}_score" in games.columns else np.nan,
        })
        rows.append(part)
    t = pd.concat(rows, ignore_index=True)
    t = t.merge(sk_agg, on=["game_id", "team_id"], how="left")
    t = t.merge(gl_agg, on=["game_id", "team_id"], how="left")
    t["sog_for"] = t["sog_g"].fillna(t["sog_sk"]).fillna(0.0)
    t["att_for"] = t["att_sk"].fillna(t["sog_for"]).fillna(0.0)
    t["goals_for_pop"] = t["goals_g"].fillna(t["goals_sk"]).fillna(0.0)
    opp = t[["game_id", "team_id", "sog_for", "att_for", "goals_for_pop"]].rename(
        columns={"team_id": "opp_id", "sog_for": "opp_sog", "att_for": "opp_att", "goals_for_pop": "opp_goals"}
    )
    t = t.merge(opp, on=["game_id", "opp_id"], how="left")
    t["sog_against"] = t["sa_gl"].fillna(t["opp_sog"]).fillna(0.0)
    t["att_against"] = t["opp_att"].fillna(t["sog_against"]).fillna(0.0)
    t["goals_against_pop"] = t["ga_gl"].fillna(t["opp_goals"]).fillna(0.0)
    for col in ("xgf", "xgf_sog", "hd_for", "att_pp_for", "xga", "xga_sog", "hd_against", "att_pp_against"):
        t[col] = 0.0
    return t[["game_id", "team_id", "att_for", "xgf", "sog_for", "xgf_sog", "goals_for_pop", "hd_for", "att_pp_for",
              "att_against", "xga", "sog_against", "xga_sog", "goals_against_pop", "hd_against", "att_pp_against"]]


def _fallback_xg_goalie() -> pd.DataFrame:
    return pd.DataFrame(columns=["game_id", "player_id", "shots_faced", "xga", "ga_pop", "gsax", "sa_pk", "hd_faced"])


def _fallback_xg_skater(skaters: pd.DataFrame) -> pd.DataFrame:
    if skaters.empty:
        return pd.DataFrame(columns=["game_id", "player_id", "attempts", "ixg", "ixg_ev", "ixg_pp",
                                     "att_ev", "att_pp", "hd_att", "sog_pop", "ixg_sog", "goals_pop"])
    s = skaters[["game_id", "player_id"]].drop_duplicates().copy()
    s["attempts"] = skaters["attempts"].to_numpy() if "attempts" in skaters.columns else (
        skaters["sog"].to_numpy() if "sog" in skaters.columns else 0.0
    )
    s["sog_pop"] = skaters["sog"].to_numpy() if "sog" in skaters.columns else 0.0
    s["goals_pop"] = skaters["goals"].to_numpy() if "goals" in skaters.columns else 0.0
    for col in ("ixg", "ixg_ev", "ixg_pp", "att_ev", "att_pp", "hd_att", "ixg_sog"):
        s[col] = 0.0
    return s


def _expected_starter_ids(gt: pd.DataFrame, tt: pd.DataFrame, last_n: int = 10) -> pd.DataFrame:
    """Return (game_id, team_id, expected_starter_id) using strictly prior starts (< date)."""
    starts = gt[gt["is_start"] == 1][["game_id", "team_id", "player_id", "date"]].sort_values(
        ["team_id", "date", "game_id"]
    )
    hist: dict = {}
    for tid, grp in starts.groupby("team_id", sort=False):
        hist[tid] = list(zip(grp["date"], grp["player_id"]))
    rows = []
    for gid, tid, d, rest in tt[["game_id", "team_id", "date", "rest_days"]].itertuples(index=False):
        prev = [x for x in hist.get(tid, []) if x[0] < d][-last_n:]
        if not prev:
            rows.append((gid, tid, np.nan))
            continue
        counts: dict = {}
        for _, pid in prev:
            counts[pid] = counts.get(pid, 0) + 1
        order = sorted(counts, key=lambda k: (-counts[k], -max(i for i, x in enumerate(prev) if x[1] == k)))
        pick = order[0]
        if rest <= 1 and prev[-1][1] == pick and len(order) > 1:
            pick = order[1]
        rows.append((gid, tid, pick))
    return pd.DataFrame(rows, columns=["game_id", "team_id", "expected_starter_id"])


def _in_rotowire_span(dates: pd.Series) -> pd.Series:
    d = dates.astype(str).str[:10]
    return (d >= ROTOWIRE_SPAN[0]) & (d <= ROTOWIRE_SPAN[1])


def _apply_rotowire_goalies(
    saves_df: pd.DataFrame,
    rotowire: pd.DataFrame | None,
) -> pd.DataFrame:
    out = saves_df.copy()
    out["rotowire_confirmed"] = np.nan
    out["starter_source"] = "expected_proxy"
    out["starter_confirmed"] = out["is_expected_starter"].astype(float)

    if rotowire is None or rotowire.empty or out.empty:
        return out

    rw = rotowire.copy()
    if "date" not in rw.columns:
        return out
    rw["_d"] = rw["date"].astype(str).str[:10]
    rw = rw[_in_rotowire_span(rw["_d"])].copy()
    if rw.empty:
        return out

    team_col = "team" if "team" in rw.columns else ("team_abbr" if "team_abbr" in rw.columns else None)
    if team_col is None:
        return out
    rw["_team"] = rw[team_col].astype(str)

    if "confirmed" in rw.columns:
        conf_mask = rw["confirmed"].astype(bool)
    elif "status" in rw.columns:
        conf_mask = rw["status"].astype(str).str.lower() == "confirmed"
    else:
        conf_mask = pd.Series(True, index=rw.index)
    rw = rw[conf_mask].copy()
    if rw.empty:
        return out

    span_mask = _in_rotowire_span(out["date"])
    if not span_mask.any():
        return out

    if "player_id" in rw.columns and rw["player_id"].notna().any():
        rw_map = rw.dropna(subset=["player_id"]).drop_duplicates(["_d", "_team"], keep="last").set_index(["_d", "_team"])["player_id"]
        for idx in out.index[span_mask]:
            key = (str(out.at[idx, "date"])[:10], str(out.at[idx, "team"]))
            if key in rw_map.index:
                matched = float(int(out.at[idx, "player_id"]) == int(rw_map.loc[key]))
                out.at[idx, "rotowire_confirmed"] = matched
                out.at[idx, "starter_confirmed"] = matched
                out.at[idx, "starter_source"] = "rotowire"
    else:
        name_col = "goalie" if "goalie" in rw.columns else ("name" if "name" in rw.columns else None)
        if name_col is not None:
            rw["_gnorm"] = rw[name_col].map(_norm_name)
            rw_map = rw.drop_duplicates(["_d", "_team"], keep="last").set_index(["_d", "_team"])["_gnorm"]
            for idx in out.index[span_mask]:
                key = (str(out.at[idx, "date"])[:10], str(out.at[idx, "team"]))
                if key in rw_map.index:
                    pname = _norm_name(out.at[idx, "name"]) if "name" in out.columns else ""
                    matched = float(bool(pname) and pname == rw_map.loc[key])
                    out.at[idx, "rotowire_confirmed"] = matched
                    out.at[idx, "starter_confirmed"] = matched
                    out.at[idx, "starter_source"] = "rotowire"
    return out


def _apply_rotowire_skaters(
    skater_df: pd.DataFrame,
    rotowire: pd.DataFrame | None,
) -> pd.DataFrame:
    out = skater_df.copy()
    out["rotowire_pp_unit"] = np.nan
    if rotowire is None or rotowire.empty or out.empty or "pp_unit" not in rotowire.columns:
        return out
    if "date" not in rotowire.columns:
        return out

    rw = rotowire.copy()
    rw["_d"] = rw["date"].astype(str).str[:10]
    rw = rw[_in_rotowire_span(rw["_d"])].copy()
    if rw.empty:
        return out

    span_mask = _in_rotowire_span(out["date"])
    if not span_mask.any():
        return out

    if "player_id" in rw.columns and rw["player_id"].notna().any():
        rw_map = rw.dropna(subset=["player_id"]).drop_duplicates(["_d", "player_id"], keep="last").set_index(["_d", "player_id"])["pp_unit"]
        for idx in out.index[span_mask]:
            key = (str(out.at[idx, "date"])[:10], out.at[idx, "player_id"])
            if key in rw_map.index:
                out.at[idx, "rotowire_pp_unit"] = pd.to_numeric(rw_map.loc[key], errors="coerce")
    return out


def _build_skater_volume(
    st: pd.DataFrame,
    tt: pd.DataFrame,
    gt: pd.DataFrame,
    toi_splits: pd.DataFrame | None,
    rotowire: pd.DataFrame | None,
    has_xg_team: bool,
    has_xg_skater: bool,
) -> pd.DataFrame:
    if st.empty:
        cols = [
            "game_id", "player_id", "date", "team", "opp", "team_id", "opp_id", "season_year", "game_type",
            "name", "position", "toi_l10", "toi_ewm", "toi_sd_l10", "att_p60_l20", "att_p60_l60",
            "sog_p60_l30", "sog_l10", "sog_ewm", "on_target_rate_l30", "pp_toi_share_prior", "is_pp1_proxy",
            "deployment_source", "n_prior", "is_d", "is_c", "is_home", "rest_days",
            "t_sf_l10", "t_sf_l30", "t_attf_l30", "t_ppf_l30", "o_sa_l10", "o_sa_l30", "o_atta_l30", "o_ppa_l30",
            "lg_sf_l30", "opp_g_sv_l60", "opp_g_gsax100_l60", "opp_g_xga_shot_l30", "rotowire_pp_unit",
        ]
        return pd.DataFrame(columns=cols)

    s = st.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True).copy()
    s["toi_sd_l10"] = X.prior_roll(s, "player_id", "toi_min", 10, how="std", min_periods=2)
    s["sog_l10"] = X.prior_roll(s, "player_id", "sog", 10)
    s["sog_ewm"] = X.prior_ewm(s, "player_id", "sog", 8)
    s["on_target_rate_l30"] = X.prior_ratio(
        s,
        "player_id",
        "sog",
        "attempts",
        30,
        prior_num=ON_TARGET_PRIOR_RATE * ON_TARGET_PRIOR_ATT,
        prior_den=ON_TARGET_PRIOR_ATT,
    )

    if not has_xg_skater:
        for col in ("ixg_p60_l20", "ixg_p60_l60", "ixg_ev_p60_l30", "ixg_pp_g_l30", "hd_p60_l30", "fin_ratio_l120"):
            if col in s.columns:
                s[col] = np.nan

    if toi_splits is not None and len(toi_splits):
        sp = toi_splits.copy()
        sp["game_id"] = sp["game_id"].astype(s["game_id"].dtype)
        sp["player_id"] = sp["player_id"].astype(s["player_id"].dtype)
        if "pp_share" not in sp.columns:
            if "toi_pp" in sp.columns and "team_pp_s" in sp.columns:
                sp["pp_share"] = sp["toi_pp"] / sp["team_pp_s"].where(sp["team_pp_s"] > 0)
            elif "toi_pp" in sp.columns and "toi_total" in sp.columns:
                sp["pp_share"] = sp["toi_pp"] / sp["toi_total"].where(sp["toi_total"] > 0)
            else:
                sp["pp_share"] = np.nan
        if "team_pp_s" in sp.columns:
            sp["_pp_share_valid"] = sp["pp_share"].where(sp["team_pp_s"] >= PP_MIN_TEAM_S)
        else:
            sp["_pp_share_valid"] = sp["pp_share"]
        sp = sp[["game_id", "player_id", "_pp_share_valid"]].drop_duplicates(["game_id", "player_id"])
        s = s.merge(sp, on=["game_id", "player_id"], how="left")
        s = s.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
        s["pp_toi_share_prior"] = X.prior_roll(s, "player_id", "_pp_share_valid", 10)
        s = s.drop(columns=["_pp_share_valid"])

        has_prior = s["pp_toi_share_prior"].notna() & (s["pp_toi_share_prior"] > 0)
        s["_rank_score"] = s["pp_toi_share_prior"].where(has_prior, -1.0)
        s = s.sort_values(
            ["game_id", "team_id", "_rank_score", "player_id"],
            ascending=[True, True, False, True],
        ).reset_index(drop=True)
        s["_team_rk"] = s.groupby(["game_id", "team_id"], sort=False).cumcount() + 1
        s["is_pp1_proxy"] = np.where(
            s["pp_toi_share_prior"].notna() & (s["pp_toi_share_prior"] > 0) & (s["_team_rk"] <= 3),
            1,
            0,
        )
        s["deployment_source"] = np.where(s["pp_toi_share_prior"].notna(), "toi_splits", "total_toi_proxy")
        s = s.drop(columns=["_rank_score", "_team_rk"]).sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    else:
        s["pp_toi_share_prior"] = np.nan
        s["is_pp1_proxy"] = 0
        s["deployment_source"] = "total_toi_proxy"

    own_cols = ["sf_l10", "sf_l30", "attf_l30", "ppf_l30"]
    own = tt[["game_id", "team_id"] + [c for c in own_cols if c in tt.columns]].rename(
        columns={"team_id": "tid", "sf_l10": "t_sf_l10", "sf_l30": "t_sf_l30",
                 "attf_l30": "t_attf_l30", "ppf_l30": "t_ppf_l30"}
    )
    opp_cols = ["sa_l10", "sa_l30", "atta_l30", "ppa_l30", "lg_sf_l30"]
    opp = tt[["game_id", "team_id"] + [c for c in opp_cols if c in tt.columns]].rename(
        columns={"team_id": "opp_id", "sa_l10": "o_sa_l10", "sa_l30": "o_sa_l30",
                 "atta_l30": "o_atta_l30", "ppa_l30": "o_ppa_l30"}
    )
    f = s.merge(own, left_on=["game_id", "team_id"], right_on=["game_id", "tid"], how="left").drop(columns=["tid"])
    f = f.merge(opp, on=["game_id", "opp_id"], how="left")

    if gt["is_start"].any():
        starters = X.expected_starter_features(gt, tt).drop(columns=["exp_is_actual"]).drop_duplicates(["game_id", "opp_id"])
        f = f.merge(starters, on=["game_id", "opp_id"], how="left")
    else:
        f["opp_g_sv_l60"] = np.nan
        f["opp_g_gsax100_l60"] = np.nan
        f["opp_g_xga_shot_l30"] = np.nan

    if not has_xg_team:
        for col in ("opp_g_gsax100_l60", "opp_g_xga_shot_l30"):
            if col in f.columns:
                f[col] = np.nan

    f = _apply_rotowire_skaters(f, rotowire)

    keep_cols = [
        "game_id", "player_id", "date", "team", "opp", "team_id", "opp_id", "season_year", "game_type",
        "name", "position", "toi_l10", "toi_ewm", "toi_sd_l10", "att_p60_l20", "att_p60_l60",
        "sog_p60_l30", "sog_l10", "sog_ewm", "on_target_rate_l30", "pp_toi_share_prior", "is_pp1_proxy",
        "deployment_source", "n_prior", "is_d", "is_c", "is_home", "rest_days",
        "t_sf_l10", "t_sf_l30", "t_attf_l30", "t_ppf_l30", "o_sa_l10", "o_sa_l30", "o_atta_l30", "o_ppa_l30",
        "lg_sf_l30", "opp_g_sv_l60", "opp_g_gsax100_l60", "opp_g_xga_shot_l30", "rotowire_pp_unit",
    ]
    for c in keep_cols:
        if c not in f.columns:
            f[c] = np.nan
    drop_leak = [c for c in f.columns if c in _OUTCOME_DROP_COLS]
    if drop_leak:
        f = f.drop(columns=drop_leak)
    return f[keep_cols].sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)


def _build_saves_volume(
    gt: pd.DataFrame,
    tt: pd.DataFrame,
    rotowire: pd.DataFrame | None,
    has_xg_team: bool,
    has_xg_goalie: bool,
) -> pd.DataFrame:
    if gt.empty:
        cols = (
            ["game_id", "player_id", "date", "team", "opp", "team_id", "opp_id", "season_year", "game_type", "name"]
            + list(X.SAVES_FEATURES)
            + ["is_expected_starter", "starter_confirmed", "starter_source", "rotowire_confirmed"]
        )
        return pd.DataFrame(columns=cols)

    sf = X.saves_features(gt, tt, starters_only=False)
    drop_out = [c for c in ("saves", "shots_against", "toi_sec") if c in sf.columns]
    if drop_out:
        sf = sf.drop(columns=drop_out)

    if not has_xg_goalie:
        for col in ("g_gsax100_l20", "g_gsax100_l60", "g_xga_shot_l30", "g_hd_share_l30"):
            if col in sf.columns:
                sf[col] = np.nan
    if not has_xg_team:
        for col in ("t_xga_l30", "t_hda_l30", "o_xgf_l30", "o_hdf_l30"):
            if col in sf.columns:
                sf[col] = np.nan

    exp = _expected_starter_ids(gt, tt)
    sf = sf.merge(exp, on=["game_id", "team_id"], how="left")
    sf["is_expected_starter"] = (
        sf["expected_starter_id"].notna() & (sf["player_id"] == sf["expected_starter_id"])
    ).astype(int)
    sf = sf.drop(columns=["expected_starter_id"])
    sf = _apply_rotowire_goalies(sf, rotowire)
    return sf.sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)


def build_nhl_volume_features(
    frames: Mapping[str, pd.DataFrame],
    toi_splits: pd.DataFrame | None = None,
    rotowire: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """Assemble pre-game NHL volume feature matrices for skater SOG and goalie saves.

    Parameters
    ----------
    frames:
        Mapping containing `"games"`, `"skater_box"` (or `"skaters"`), `"goalie_box"` (or `"goalies"`),
        and optionally `"xg_team"`, `"xg_goalie"`, `"xg_skater"`, `"rotowire"`, `"toi_splits"`.
    toi_splits:
        Optional skater situation-split TOI DataFrame (`nhl.data.toi_splits` schema). When None,
        builds on total TOI only and sets `deployment_source = "total_toi_proxy"`.
    rotowire:
        Optional RotoWire lineup DataFrame. Any rotowire-derived column is strictly `NaN` outside
        `ROTOWIRE_SPAN` (`2026-11-16`..`2026-11-24`).

    Returns
    -------
    dict with `"skater"` and `"saves"` DataFrames, each validated by `assert_feature_schema`.
    """
    games = frames["games"].copy()
    skaters = _get_box(frames, "skater_box", "skaters")
    goalies = _get_box(frames, "goalie_box", "goalies")

    splits = toi_splits if toi_splits is not None else frames.get("toi_splits")
    rw = rotowire if rotowire is not None else frames.get("rotowire")

    has_xg_team = "xg_team" in frames and frames["xg_team"] is not None
    has_xg_goalie = "xg_goalie" in frames and frames["xg_goalie"] is not None
    has_xg_skater = "xg_skater" in frames and frames["xg_skater"] is not None

    xg_team = frames["xg_team"].copy() if has_xg_team else _fallback_xg_team(games, skaters, goalies)
    xg_goalie = frames["xg_goalie"].copy() if has_xg_goalie else _fallback_xg_goalie()
    xg_skater = frames["xg_skater"].copy() if has_xg_skater else _fallback_xg_skater(skaters)

    # Prevent _x/_y column collisions inside xgfeat.skater_table / goalie_table when box frames carry xG/attempt columns
    sk_overlap = [c for c in xg_skater.columns if c not in ("game_id", "player_id") and c in skaters.columns]
    if sk_overlap:
        skaters = skaters.drop(columns=sk_overlap)
    gl_overlap = [c for c in xg_goalie.columns if c not in ("game_id", "player_id") and c in goalies.columns]
    if gl_overlap:
        goalies = goalies.drop(columns=gl_overlap)

    tt = X.team_table(games, xg_team)
    gt = X.goalie_table(games, goalies, xg_goalie)
    st = X.skater_table(games, skaters, xg_skater)

    skater_df = _build_skater_volume(st, tt, gt, splits, rw, has_xg_team, has_xg_skater)
    saves_df = _build_saves_volume(gt, tt, rw, has_xg_team, has_xg_goalie)

    assert_feature_schema(skater_df)
    assert_feature_schema(saves_df)
    return {"skater": skater_df, "saves": saves_df}
