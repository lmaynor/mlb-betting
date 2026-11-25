"""MOD-04 Cluster (a) Stage 1 Exposure Backbone v1 (NHL EV + PK TOI distribution).

Produces per skater-game ``(mu_ev_toi, sigma_ev_toi, mu_pk_toi, sigma_pk_toi,
mu_exp_toi, sigma_exp_toi)`` before any Stage 2 per-minute blocked-shot rate
head applies. Reuses ``nhl.props.xgfeat.prior_roll`` so every rolling window
is strictly ``shift(1)``-first (matching ``nhl.props.toi_feat`` conventions).

LEAKAGE CONTRACT:
  * Only skater box-score and TOI-split rows with ``date < target_date`` (ET
    game-date convention matching ``NHL/games_master.csv`` and
    ``NHL/skater_boxscores_master.csv``) enter rolling TOI projections.
  * When a player's TOI split history is missing, falls back to total ``toi_sec``
    split by defenseman/forward priors and sets ``toi_split_fallback_flag = 1``.
  * Rotowire daily lineup/injury files (``NHL/lineups/rotowire/<date>.json``)
    are applied at SCORE TIME ONLY (never in training, which uses masters only),
    and only when ``fetched_at <= cutoff_utc``.
  * A player listed ``OUT`` / ``IR*`` in Rotowire receives zero exposure
    (``mu_ev_toi = mu_pk_toi = mu_exp_toi = 0.0``, ``is_player_out = 1``);
    active teammates in the same position group absorb a bounded vacated-TOI boost.
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from nhl.props.xgfeat import prior_roll

EB_TOI_PRIOR_N = 4.0
LG_PK_SHARE = 0.065
MAX_EV_TOI = 28.0
MAX_PK_TOI = 8.0
MAX_EXP_TOI = 32.0

POS_DEFAULT_TOI: dict[str, dict[str, float]] = {
    "D": {"ev": 16.8, "pk": 1.6, "total": 19.3, "ev_frac": 0.871, "pk_frac": 0.083, "cv": 0.15},
    "F": {"ev": 12.8, "pk": 0.8, "total": 15.2, "ev_frac": 0.844, "pk_frac": 0.053, "cv": 0.18},
}

OUT_STATUSES = frozenset({"OUT", "IR", "IR-LT", "IR-NR"})
DTD_STATUSES = frozenset({"DTD"})

TOI_BACKBONE_COLS = [
    "mu_ev_toi",
    "sigma_ev_toi",
    "mu_pk_toi",
    "sigma_pk_toi",
    "mu_exp_toi",
    "sigma_exp_toi",
    "pk_toi_share",
    "toi_split_fallback_flag",
    "absorbed_toi_delta",
    "teammate_out_count",
    "is_player_out",
    "is_player_dtd",
    "rw_pp_unit",
]


def pos_group_nhl(position: Any) -> str:
    """Map NHL skater position code (C, L, LW, R, RW, D) to 'D' or 'F'."""
    p = str(position or "").strip().upper()
    return "D" if p == "D" else "F"


def compute_prior_toi_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute strictly-prior (``shift(1)``) EV/PK/total TOI rolling stats on sorted skater rows.

    Parameters
    ----------
    df:
        Chronologically sorted per-player frame (``player_id``, ``date``, ``game_id``,
        ``position``, ``toi_sec`` and optional ``toi_ev``, ``toi_pk`` in seconds).
        May contain placeholder query rows at the end of each player's series with
        NaN outcomes.
    """
    if df.empty:
        return pd.DataFrame(index=df.index)

    work = df.copy()
    work["toi_tot_min"] = pd.to_numeric(work["toi_sec"], errors="coerce") / 60.0
    if "toi_ev" in work.columns:
        work["toi_ev_min"] = pd.to_numeric(work["toi_ev"], errors="coerce") / 60.0
    else:
        work["toi_ev_min"] = np.nan
    if "toi_pk" in work.columns:
        work["toi_pk_min"] = pd.to_numeric(work["toi_pk"], errors="coerce") / 60.0
    else:
        work["toi_pk_min"] = np.nan

    work["toi_exp_min"] = work["toi_ev_min"] + work["toi_pk_min"]
    work["has_tot"] = work["toi_tot_min"].notna().astype(float)
    work["has_split"] = (work["toi_ev_min"].notna() & work["toi_pk_min"].notna()).astype(float)

    out = pd.DataFrame(index=work.index)
    out["n_prior_games"] = work.groupby("player_id", sort=False).cumcount().astype(int)
    n_tot_prior = prior_roll(work, "player_id", "has_tot", 1000, "sum", min_periods=1).fillna(0.0)
    n_spl_prior = prior_roll(work, "player_id", "has_split", 1000, "sum", min_periods=1).fillna(0.0)

    ev_l10 = prior_roll(work, "player_id", "toi_ev_min", 10, "mean", min_periods=1)
    ev_l30 = prior_roll(work, "player_id", "toi_ev_min", 30, "mean", min_periods=1)
    pk_l10 = prior_roll(work, "player_id", "toi_pk_min", 10, "mean", min_periods=1)
    pk_l30 = prior_roll(work, "player_id", "toi_pk_min", 30, "mean", min_periods=1)
    tot_l10 = prior_roll(work, "player_id", "toi_tot_min", 10, "mean", min_periods=1)
    tot_l30 = prior_roll(work, "player_id", "toi_tot_min", 30, "mean", min_periods=1)

    ev_std10 = prior_roll(work, "player_id", "toi_ev_min", 10, "std", min_periods=3)
    pk_std10 = prior_roll(work, "player_id", "toi_pk_min", 10, "std", min_periods=3)
    exp_std10 = prior_roll(work, "player_id", "toi_exp_min", 10, "std", min_periods=3)
    tot_std10 = prior_roll(work, "player_id", "toi_tot_min", 10, "std", min_periods=3)

    pos_grp = work["position"].map(pos_group_nhl)
    p_ev = pos_grp.map(lambda g: POS_DEFAULT_TOI[g]["ev"]).astype(float)
    p_pk = pos_grp.map(lambda g: POS_DEFAULT_TOI[g]["pk"]).astype(float)
    p_tot = pos_grp.map(lambda g: POS_DEFAULT_TOI[g]["total"]).astype(float)
    p_ev_f = pos_grp.map(lambda g: POS_DEFAULT_TOI[g]["ev_frac"]).astype(float)
    p_pk_f = pos_grp.map(lambda g: POS_DEFAULT_TOI[g]["pk_frac"]).astype(float)
    p_cv = pos_grp.map(lambda g: POS_DEFAULT_TOI[g]["cv"]).astype(float)

    has_spl_hist = ev_l10.notna() & pk_l10.notna() & (n_spl_prior > 0)
    has_tot_hist = tot_l10.notna() & (n_tot_prior > 0)

    raw_ev = 0.65 * ev_l10 + 0.35 * ev_l30.fillna(ev_l10)
    raw_pk = 0.65 * pk_l10 + 0.35 * pk_l30.fillna(pk_l10)
    w_spl = n_spl_prior / (n_spl_prior + EB_TOI_PRIOR_N)
    ev_from_spl = w_spl * raw_ev + (1.0 - w_spl) * p_ev
    pk_from_spl = w_spl * raw_pk + (1.0 - w_spl) * p_pk

    raw_tot = 0.65 * tot_l10 + 0.35 * tot_l30.fillna(tot_l10)
    w_tot = n_tot_prior / (n_tot_prior + EB_TOI_PRIOR_N)
    tot_est = np.where(has_tot_hist, w_tot * raw_tot + (1.0 - w_tot) * p_tot, p_tot)
    ev_from_tot = tot_est * p_ev_f
    pk_from_tot = tot_est * p_pk_f

    mu_ev = np.where(has_spl_hist, ev_from_spl, ev_from_tot)
    mu_pk = np.where(has_spl_hist, pk_from_spl, pk_from_tot)

    # Team PK-share environment adjustment (if passed on `work`)
    if "team_pk_share_l15" in work.columns:
        t_pk = pd.to_numeric(work["team_pk_share_l15"], errors="coerce").fillna(LG_PK_SHARE).to_numpy(float)
        pk_mult = np.clip(1.0 + 0.5 * (t_pk / LG_PK_SHARE - 1.0), 0.85, 1.20)
        mu_pk = mu_pk * pk_mult

    mu_ev = np.clip(mu_ev, 0.0, MAX_EV_TOI)
    mu_pk = np.clip(mu_pk, 0.0, MAX_PK_TOI)
    mu_exp = np.clip(mu_ev + mu_pk, 0.0, MAX_EXP_TOI)

    sd_ev_prior = np.maximum(mu_ev * p_cv, 0.5)
    sd_ev = np.where(ev_std10.notna(), 0.6 * ev_std10.to_numpy(float) + 0.4 * sd_ev_prior, sd_ev_prior)
    sd_ev = np.clip(sd_ev, 0.4, 8.0)

    sd_pk_prior = np.where(mu_pk > 0.05, np.maximum(mu_pk * 0.55, 0.25), 0.05)
    sd_pk = np.where(pk_std10.notna(), 0.6 * pk_std10.to_numpy(float) + 0.4 * sd_pk_prior, sd_pk_prior)
    sd_pk = np.clip(sd_pk, 0.0, 4.0)

    sd_exp_fallback = np.where(tot_std10.notna(), tot_std10.to_numpy(float), np.maximum(mu_exp * p_cv, 0.6))
    sd_exp = np.where(exp_std10.notna(), 0.6 * exp_std10.to_numpy(float) + 0.4 * np.maximum(mu_exp * p_cv, 0.6), sd_exp_fallback)
    sd_exp = np.clip(sd_exp, 0.5, 9.0)

    out["toi_l5"] = prior_roll(work, "player_id", "toi_tot_min", 5, "mean", min_periods=1)
    out["toi_l15"] = prior_roll(work, "player_id", "toi_tot_min", 15, "mean", min_periods=1)
    out["toi_ev_l10"] = ev_l10
    out["toi_pk_l10"] = pk_l10
    out["mu_ev_toi"] = mu_ev
    out["sigma_ev_toi"] = sd_ev
    out["mu_pk_toi"] = mu_pk
    out["sigma_pk_toi"] = sd_pk
    out["mu_exp_toi"] = mu_exp
    out["sigma_exp_toi"] = sd_exp
    out["pk_toi_share"] = np.where(mu_exp > 1e-6, mu_pk / mu_exp, 0.0)
    out["toi_split_fallback_flag"] = (~has_spl_hist).astype(int)
    return out


def compute_toi_backbone(
    prior_skaters: pd.DataFrame,
    prior_splits: pd.DataFrame | None,
    slate: pd.DataFrame,
    rotowire_by_team: Mapping[str, Mapping[str, Any]] | None = None,
    target_date: str | None = None,
    team_pk_share_map: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """Compute Stage 1 EV + PK TOI distributions aligned to ``slate`` (score-time interface).

    Parameters
    ----------
    prior_skaters:
        Strictly prior played skater boxscores (``date < target_date``) with
        ``player_id``, ``game_id``, ``date``, ``team``, ``position``, ``toi_sec``.
    prior_splits:
        Strictly prior TOI splits (``game_id``, ``player_id``, ``toi_ev``, ``toi_pk``).
    slate:
        Target rows with ``player_id``, ``team``, ``position``, ``player_norm`` (optional).
    rotowire_by_team:
        Pre-cutoff Rotowire team dict ``{team: {"pp": {"1": [...], "2": [...]}, "injuries": [...]}}``.
    target_date:
        ET date string ``YYYY-MM-DD``; enforces ``date < target_date`` on history.
    team_pk_share_map:
        Optional ``{team: team_pk_share_l15}`` from strictly prior games.
    """
    if slate.empty:
        return pd.DataFrame(columns=TOI_BACKBONE_COLS, index=slate.index)

    sk = prior_skaters.copy() if prior_skaters is not None else pd.DataFrame()
    if not sk.empty:
        sk["date"] = sk["date"].astype(str).str[:10]
        if target_date is not None:
            sk = sk[sk["date"] < str(target_date)[:10]].copy()
        sk["player_id"] = pd.to_numeric(sk["player_id"], errors="coerce").astype("Int64").astype(str)
        sk["game_id"] = pd.to_numeric(sk["game_id"], errors="coerce").astype("Int64")
        if prior_splits is not None and not prior_splits.empty:
            sp = prior_splits[["game_id", "player_id", "toi_ev", "toi_pk"]].copy()
            sp["game_id"] = pd.to_numeric(sp["game_id"], errors="coerce").astype("Int64")
            sp["player_id"] = pd.to_numeric(sp["player_id"], errors="coerce").astype("Int64").astype(str)
            sp = sp.dropna(subset=["game_id"]).drop_duplicates(["game_id", "player_id"])
            sk = sk.merge(sp, on=["game_id", "player_id"], how="left")
        else:
            sk["toi_ev"] = np.nan
            sk["toi_pk"] = np.nan

        # Append one placeholder query row per player present in prior history or slate
        last_rows = sk.sort_values(["player_id", "date", "game_id"]).groupby("player_id", sort=False).tail(1).copy()
        last_rows["game_id"] = -1
        last_rows["date"] = str(target_date or "2099-01-01")[:10]
        last_rows["toi_sec"] = np.nan
        last_rows["toi_ev"] = np.nan
        last_rows["toi_pk"] = np.nan
        if team_pk_share_map:
            last_rows["team_pk_share_l15"] = last_rows["team"].map(team_pk_share_map)
        comb = pd.concat([sk, last_rows], ignore_index=True).sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
        stats_all = compute_prior_toi_stats(comb)
        q_mask = comb["game_id"] == -1
        p_stats = pd.concat([comb.loc[q_mask, ["player_id"]].reset_index(drop=True), stats_all.loc[q_mask].reset_index(drop=True)], axis=1)
        stats_by_pid = p_stats.set_index("player_id").to_dict(orient="index")
    else:
        stats_by_pid = {}

    # Parse Rotowire team info (score-time only)
    from nhl.data.rotowire_lineups import _norm as rw_norm, _same_player as rw_same_player

    rw_map = rotowire_by_team or {}
    # Precompute vacated TOI by (team, pos_grp) from Rotowire injuries
    vacated_by_tp: dict[tuple[str, str], float] = {}
    out_cnt_by_tp: dict[tuple[str, str], int] = {}
    player_rw: list[tuple[int, int, int]] = []  # (is_out, is_dtd, rw_pp_unit)

    slate_teams = list(slate["team"].astype(str))
    slate_pos = [pos_group_nhl(p) for p in slate["position"]] if "position" in slate.columns else ["F"] * len(slate)
    slate_names = list(slate["name"].astype(str) if "name" in slate.columns else slate.get("player", pd.Series("", index=slate.index)).astype(str))
    slate_pids = list(slate["player_id"].astype(str))

    # Also build roster lookup from prior history for players injured who aren't on `slate`
    for i in range(len(slate)):
        team = slate_teams[i]
        pg = slate_pos[i]
        nm = slate_names[i]
        pid_str = slate_pids[i]
        t_info = rw_map.get(team)
        is_out = 0
        is_dtd = 0
        rw_pp = 0
        if t_info:
            want = rw_norm(nm)
            inj_list = t_info.get("injuries") or []
            for inj in inj_list:
                if rw_same_player(want, rw_norm(str(inj.get("name", "")))):
                    st = str(inj.get("status") or "").upper()
                    if st in OUT_STATUSES:
                        is_out = 1
                    elif st in DTD_STATUSES:
                        is_dtd = 1
            pp_dict = t_info.get("pp") or {}
            hits = {int(u) for u in ("1", "2") for n in (pp_dict.get(u) or []) if rw_same_player(want, rw_norm(str(n)))}
            if len(hits) == 1:
                rw_pp = next(iter(hits))

        if is_out:
            rec = stats_by_pid.get(pid_str)
            vac = float(rec["mu_exp_toi"]) if rec is not None else POS_DEFAULT_TOI[pg]["ev"]
            vacated_by_tp[(team, pg)] = vacated_by_tp.get((team, pg), 0.0) + max(vac, 0.0)
            out_cnt_by_tp[(team, pg)] = out_cnt_by_tp.get((team, pg), 0) + 1
        player_rw.append((is_out, is_dtd, rw_pp))

    # Count active skaters per (team, pos_grp) to share vacated TOI conservatively
    active_cnt_by_tp: dict[tuple[str, str], int] = {}
    for i in range(len(slate)):
        if player_rw[i][0] == 0:
            key = (slate_teams[i], slate_pos[i])
            active_cnt_by_tp[key] = active_cnt_by_tp.get(key, 0) + 1

    records: list[dict[str, float | int]] = []
    for i in range(len(slate)):
        pid_str = slate_pids[i]
        team = slate_teams[i]
        pg = slate_pos[i]
        is_out, is_dtd, rw_pp = player_rw[i]

        rec = stats_by_pid.get(pid_str)
        if rec is not None:
            mu_ev = float(rec["mu_ev_toi"])
            sd_ev = float(rec["sigma_ev_toi"])
            mu_pk = float(rec["mu_pk_toi"])
            sd_pk = float(rec["sigma_pk_toi"])
            sd_exp = float(rec["sigma_exp_toi"])
            fb_flag = int(rec["toi_split_fallback_flag"])
        else:
            p_def = POS_DEFAULT_TOI[pg]
            mu_ev = p_def["ev"]
            mu_pk = p_def["pk"]
            sd_ev = max(mu_ev * p_def["cv"], 0.6)
            sd_pk = max(mu_pk * 0.55, 0.25)
            sd_exp = max((mu_ev + mu_pk) * p_def["cv"], 0.7)
            fb_flag = 1

        if team_pk_share_map and team in team_pk_share_map and rec is None:
            t_pk = float(team_pk_share_map[team])
            mu_pk = float(np.clip(mu_pk * np.clip(1.0 + 0.5 * (t_pk / LG_PK_SHARE - 1.0), 0.85, 1.20), 0.0, MAX_PK_TOI))

        # Bounded teammate-absence TOI absorption (same position group, capped at +2.5 min)
        vac_pool = vacated_by_tp.get((team, pg), 0.0)
        n_act = max(active_cnt_by_tp.get((team, pg), 1), 1)
        out_cnt = out_cnt_by_tp.get((team, pg), 0)
        absorbed = 0.0
        if not is_out and vac_pool > 0.0:
            # 60% of vacated minutes redistribute evenly across active same-pos skaters, capped at 2.5 min
            raw_abs = min(0.60 * vac_pool / n_act, 2.5)
            absorbed = float(raw_abs)
            ev_share = mu_ev / max(mu_ev + mu_pk, 1e-6)
            mu_ev = min(mu_ev + absorbed * ev_share, MAX_EV_TOI)
            mu_pk = min(mu_pk + absorbed * (1.0 - ev_share), MAX_PK_TOI)

        if is_out:
            mu_ev = 0.0
            sd_ev = 0.0
            mu_pk = 0.0
            sd_pk = 0.0
            mu_exp = 0.0
            sd_exp = 0.0
            absorbed = 0.0
        else:
            if is_dtd:
                sd_ev = min(sd_ev * 1.25, 8.0)
                sd_exp = min(sd_exp * 1.25, 9.0)
            mu_exp = min(mu_ev + mu_pk, MAX_EXP_TOI)

        records.append({
            "mu_ev_toi": float(mu_ev),
            "sigma_ev_toi": float(sd_ev),
            "mu_pk_toi": float(mu_pk),
            "sigma_pk_toi": float(sd_pk),
            "mu_exp_toi": float(mu_exp),
            "sigma_exp_toi": float(sd_exp),
            "pk_toi_share": float(mu_pk / mu_exp) if mu_exp > 1e-6 else 0.0,
            "toi_split_fallback_flag": int(fb_flag),
            "absorbed_toi_delta": float(absorbed),
            "teammate_out_count": int(out_cnt),
            "is_player_out": int(is_out),
            "is_player_dtd": int(is_dtd),
            "rw_pp_unit": int(rw_pp),
        })

    return pd.DataFrame(records, index=slate.index)
