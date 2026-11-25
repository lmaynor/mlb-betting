"""MOD-04 Cluster (a) Stage 1 Exposure Backbone v1 (NBA minutes distribution).

Produces per player-game ``(mu_min, sigma_min)`` before any Stage 2 per-minute rate
head applies. Reuses ``nba.props.hierarchical.project_minutes_distribution`` and
``nba.props.hierarchical.compute_vacated_shares`` directly.

LEAKAGE CONTRACT:
  * Only box-score rows with ``date < target_date`` (same UTC tip-date system as
    ``player_boxscores_master.csv``) enter base EWM minutes, foul rates, and
    rotation weights.
  * Injury absences (Out/Doubtful) and ``arm_s_index`` minutes-cap signals must come
    exclusively from the latest pre-cutoff snapshot (``ts_utc <= cutoff_utc``).
  * A player flagged ``Out`` receives ``mu_min = 0.0``, ``sigma_min = 0.0`` and
    ``is_player_out = 1``; the row is still emitted for shadow completeness.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from nba.props.hierarchical import (
    MAX_MEAN_MIN,
    ROTATION_MIN_MPG,
    compute_vacated_shares,
    ewm,
    project_minutes_distribution,
)

# Absence weights for pre-game teammate redistribution
ABSENCE_WEIGHTS: dict[str, float] = {
    "Out": 1.0,
    "Doubtful": 0.75,
}
DEFAULT_MIN_BASE = 16.0
EB_PRIOR_MIN = 16.0
EB_PRIOR_GAMES = 4.0
CAP_RATIO_TRIGGER = 0.98
CAP_SIGMA_SCALE = 0.75
MIN_BACKBONE_COLS = [
    "mu_min_base",
    "mu_min",
    "sigma_min",
    "vacated_min_team",
    "absorbed_min_delta",
    "teammate_out_count",
    "is_player_out",
    "is_player_doubtful",
    "is_player_questionable",
    "min_cap_flag",
    "min_cap_ratio",
    "is_injury_return_flag",
]


def _base_minutes_from_history(minutes_hist: np.ndarray, starter_hist: np.ndarray | None = None) -> float:
    """EB-shrunk EWM minutes projection from strictly prior played games."""
    mins = minutes_hist[np.isfinite(minutes_hist) & (minutes_hist > 0)]
    n = len(mins)
    if n == 0:
        return DEFAULT_MIN_BASE
    tail = mins[-20:]
    raw_ewm = ewm(tail, halflife=8.0)
    prior = EB_PRIOR_MIN
    if starter_hist is not None and len(starter_hist):
        st = starter_hist[-10:]
        if np.isfinite(st).any() and float(np.nanmean(st)) >= 0.5:
            prior = 26.0
    return float((n * raw_ewm + EB_PRIOR_GAMES * prior) / (n + EB_PRIOR_GAMES))


def _build_team_rows_map(
    prior_pb: pd.DataFrame,
    teams: set[str],
    target_date: str | None,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Convert strictly-prior boxscores into ``compute_vacated_shares`` team maps."""
    if prior_pb.empty or not teams:
        return {}
    sub = prior_pb[prior_pb["team"].isin(teams)].copy()
    if target_date is not None:
        sub = sub[sub["date"].astype(str).str[:10] < str(target_date)[:10]]
    if sub.empty:
        return {}
    sub = sub.sort_values(["team", "player_id", "date"])
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for team, tg in sub.groupby("team", sort=False):
        tmap: dict[str, list[dict[str, Any]]] = {}
        for pid, pg in tg.groupby("player_id", sort=False):
            recs = [
                {"_date": str(d)[:10], "minutes": float(m)}
                for d, m in zip(pg["date"].tail(60), pg["minutes"].tail(60))
                if pd.notna(m) and float(m) > 0
            ]
            if recs:
                tmap[str(pid)] = recs
        out[str(team)] = tmap
    return out


def compute_minutes_backbone(
    prior_pb: pd.DataFrame,
    slate: pd.DataFrame,
    absences_by_team: Mapping[str, Mapping[str, float | str]] | None = None,
    status_by_player: Mapping[tuple[str, str], str] | None = None,
    arm_s_caps: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
    target_date: str | None = None,
) -> pd.DataFrame:
    """Compute Stage 1 ``(mu_min, sigma_min)`` and redistribution columns aligned to ``slate``.

    Parameters
    ----------
    prior_pb:
        Strictly prior played player boxscores (``date < target_date``) with columns
        ``player_id``, ``team``, ``date``, ``minutes``, and optionally ``starter``.
    slate:
        Target rows with ``player_id``, ``team``, and optional ``spread``, ``rest_days``,
        ``pf_per36``, ``player_norm``.
    absences_by_team:
        ``{team: {player_id_str: weight_or_status}}`` from the latest pre-cutoff snapshot.
    status_by_player:
        ``{(team, player_id_str): status}`` (``Out``, ``Doubtful``, ``Questionable``, ...).
    arm_s_caps:
        ``{(team, player_key): {"cap_ratio": float, "is_injury_return": bool, "cap_minutes": float|None}}``.
    target_date:
        UTC date string ``YYYY-MM-DD``; enforces ``date < target_date`` inside ``compute_vacated_shares``.
    """
    if slate.empty:
        return pd.DataFrame(columns=MIN_BACKBONE_COLS, index=slate.index)

    pb = prior_pb.copy()
    if not pb.empty:
        pb["date"] = pb["date"].astype(str).str[:10]
        if target_date is not None:
            pb = pb[pb["date"] < str(target_date)[:10]]
        pb["player_id_str"] = pb["player_id"].astype(str)
    else:
        pb = pd.DataFrame(columns=["player_id", "player_id_str", "team", "date", "minutes", "starter"])

    # Precompute base EWM minutes per player_id from strictly prior history
    base_min_map: dict[str, float] = {}
    if not pb.empty:
        pb_sorted = pb.sort_values(["player_id_str", "date"])
        has_starter = "starter" in pb_sorted.columns
        for pid_str, grp in pb_sorted.groupby("player_id_str", sort=False):
            mins = pd.to_numeric(grp["minutes"], errors="coerce").to_numpy(dtype=float)
            st = grp["starter"].astype(float).to_numpy() if has_starter else None
            base_min_map[pid_str] = _base_minutes_from_history(mins, st)

    # Injury redistribution via nba.props.hierarchical.compute_vacated_shares
    abs_teams = {str(t) for t, m in (absences_by_team or {}).items() if m}
    vac_by_team: dict[str, dict[str, Any]] = {}
    if abs_teams and not pb.empty:
        team_rows_map = _build_team_rows_map(pb, abs_teams, target_date)
        for team in abs_teams:
            raw_abs = absences_by_team.get(team, {}) if absences_by_team else {}
            weights: dict[str, float] = {}
            for k, val in raw_abs.items():
                if isinstance(val, str):
                    w = ABSENCE_WEIGHTS.get(val, 0.0)
                else:
                    w = float(val)
                if w > 0:
                    weights[str(k)] = min(w, 1.0)
            if weights and team in team_rows_map:
                vac_by_team[team] = compute_vacated_shares(
                    team_rows_map[team],
                    weights,
                    as_of=str(target_date)[:10] if target_date is not None else None,
                    min_mpg=ROTATION_MIN_MPG,
                )

    status_map = status_by_player or {}
    caps_map = arm_s_caps or {}

    records: list[dict[str, float | int]] = []
    for row in slate.itertuples(index=False):
        pid_str = str(getattr(row, "player_id"))
        team = str(getattr(row, "team", "") or "")
        pnorm = str(getattr(row, "player_norm", "") or "")

        # Base minutes: use caller's precomputed mu_min_base if already provided & finite, else history
        caller_base = getattr(row, "mu_min_base", np.nan)
        if caller_base is not None and np.isfinite(caller_base):
            mu0 = float(caller_base)
        else:
            mu0 = base_min_map.get(pid_str, DEFAULT_MIN_BASE)

        status = status_map.get((team, pid_str)) or status_map.get((team, pnorm), "")
        is_out = int(status == "Out")
        is_doubtful = int(status == "Doubtful")
        is_questionable = int(status == "Questionable")

        # Teammate absence redistribution (strictly excluding self if listed Out)
        vac_info = vac_by_team.get(team)
        vac_share = float(vac_info["vacated"].get("minutes", 0.0)) if vac_info else 0.0
        team_abs = absences_by_team.get(team, {}) if absences_by_team else {}
        teammate_out_cnt = sum(
            1
            for k, v in team_abs.items()
            if str(k) != pid_str and (ABSENCE_WEIGHTS.get(v, 0.0) if isinstance(v, str) else float(v)) >= 0.75
        )
        boost = 1.0
        if vac_info and not is_out:
            boost = float(vac_info["boost"].get(pid_str, {}).get("minutes", 1.0))
        mu_vac = min(mu0 * max(boost, 1.0), MAX_MEAN_MIN)
        absorbed_delta = max(mu_vac - mu0, 0.0)

        # Spread, rest, foul-rate regime distribution from nba.props.hierarchical
        spread_val = getattr(row, "spread", None)
        spread = float(spread_val) if spread_val is not None and np.isfinite(spread_val) else None
        rest_val = getattr(row, "rest_days", None)
        rest = int(rest_val) if rest_val is not None and np.isfinite(rest_val) else None
        pf36_val = getattr(row, "pf_per36", None)
        pf36 = float(pf36_val) if pf36_val is not None and np.isfinite(pf36_val) else None

        dist = project_minutes_distribution(mu_vac, spread=spread, rest=rest, pf36=pf36)
        mu_min = float(dist["mean"])
        sigma_min = float(dist["sd"])

        # Minutes restriction / cap from arm_s_index
        cap_info = caps_map.get((team, pid_str)) or caps_map.get((team, pnorm)) or caps_map.get(("", pid_str))
        cap_flag = 0
        cap_ratio = 1.0
        inj_ret_flag = 0
        if cap_info is not None:
            raw_ratio = float(cap_info.get("cap_ratio", 1.0))
            inj_ret_flag = int(bool(cap_info.get("is_injury_return", False)))
            cap_minutes = cap_info.get("cap_minutes")
            if np.isfinite(raw_ratio) and raw_ratio < CAP_RATIO_TRIGGER:
                cap_flag = 1
                cap_ratio = max(0.2, min(raw_ratio, 1.0))
                cap_target = float(cap_minutes) if cap_minutes is not None and np.isfinite(cap_minutes) else mu0 * cap_ratio
                mu_min = min(mu_min, max(cap_target, 0.0))
                sigma_min = sigma_min * CAP_SIGMA_SCALE
            elif cap_minutes is not None and np.isfinite(cap_minutes) and float(cap_minutes) < mu_min:
                cap_flag = 1
                cap_ratio = max(0.2, min(float(cap_minutes) / max(mu0, 1e-6), 1.0))
                mu_min = max(float(cap_minutes), 0.0)
                sigma_min = sigma_min * CAP_SIGMA_SCALE

        if is_out:
            mu_min = 0.0
            sigma_min = 0.0
            absorbed_delta = 0.0
        elif is_doubtful:
            # Doubtful players keep non-zero fallback uncertainty unless ruled Out
            sigma_min = max(sigma_min, 0.25 * mu_min)

        records.append({
            "mu_min_base": float(mu0),
            "mu_min": float(max(mu_min, 0.0)),
            "sigma_min": float(max(sigma_min, 0.0)),
            "vacated_min_team": float(vac_share),
            "absorbed_min_delta": float(absorbed_delta),
            "teammate_out_count": int(teammate_out_cnt),
            "is_player_out": is_out,
            "is_player_doubtful": is_doubtful,
            "is_player_questionable": is_questionable,
            "min_cap_flag": cap_flag,
            "min_cap_ratio": float(cap_ratio),
            "is_injury_return_flag": inj_ret_flag,
        })

    return pd.DataFrame(records, index=slate.index)
