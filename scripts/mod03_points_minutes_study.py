#!/usr/bin/env python3
"""Reproducible study harness for NBA points projected-minutes x trailing-rate model.

PRE-REGISTERED CANDIDATE — mod03_points_mu_v1 (registered Nov 25, 2026 in
~/workspace/beezy/model-operationalization-log-2026-11-24.md BEFORE any run;
candidate and bar unaltered).
mu_cand = usage_proj (the archived row's own projected minutes) x the player's
trailing per-minute scoring rate from box-score games strictly before the row's
date (expanding window, minimum 5 prior games; below minimum -> fit-window
league-average per-minute rate). Distribution Normal(mu_cand, sd_row),
sd_row = (proj_p90 - proj_p10)/2.5632 from the archived row;
p_over_cand = 1 - Phi((line - mu_cand)/sd_row) (plain Normal CDF, no continuity
correction).
Population: all priced NBA points board rows, Nov 2-24 EVSweep archives.
Fit window Nov 2-15 (fallback constants only), grade untouched Nov 16-24
(Nov 25 unplayed).
PASS bar (all three): holdout slope of candidate >= 0.30; paired log-loss vs
market no longer significantly worse (one-sided z > -1.65, z of
candidate-minus-market per-row LL diffs); candidate log-loss better than the
served model's raw log-loss on identical rows. PASS -> also fit blend weight c
on the fit window (logit blend with market, grid [0,1] step 0.01, min fit LL)
reported as PROPOSAL only. FAIL -> NBA points stays LEVEL-ONLY permanently.
No serving changes in any outcome.
"""
from __future__ import annotations

import collections
import glob
import json
import math
import os
import unicodedata

import numpy as np
import pandas as pd
from scipy.stats import norm as scipy_norm

ARCH_DIR = os.environ.get("MOD03_ARCH") or os.path.expanduser("~/workspace/mod03-combo-work/archive")
BOX_CSV = os.environ.get("MOD03_BOX") or os.path.expanduser("~/workspace/mod03-combo-work/player_boxscores_master.csv")
AUDIT_JSON = os.path.expanduser("~/workspace/beezy/probability-audit-work/audit_results.json")
OUT_JSON = os.path.expanduser("~/workspace/beezy/mod03-points-minutes-results.json")

STAT_NAME = "points"
DAY_MIN = "2026-11-02"
DAY_MAX = "2026-11-24"
FIT_MAX_DAY = "2026-11-15"
HOLDOUT_MIN_DAY = "2026-11-16"
BLEND_MAX_SHIFT = 0.08
MIN_PRIOR_GAMES = 5
NORM_P90_P10_DIVISOR = 2.5632
SLOPE_BAR = 0.30
Z_BAR = -1.65


def norm(name) -> str:
    """Normalize player name identically to audit.py."""
    if name is None:
        return ""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = s.lower().replace(".", " ").replace("'", " ").replace("-", " ")
    toks = [t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv")]
    return " ".join(toks)


def clip(p):
    """Clip probabilities to [1e-6, 1 - 1e-6] identically to audit.py."""
    return np.clip(p, 1e-6, 1.0 - 1e-6)


def fit_irls(X: np.ndarray, y: np.ndarray, offset: np.ndarray | None = None, max_iter: int = 50):
    """Logistic regression IRLS matching mod02_refit_grade.py / audit.py."""
    n, k = X.shape
    beta = np.zeros(k, dtype=float)
    off = np.zeros(n, dtype=float) if offset is None else np.asarray(offset, dtype=float)
    XtWX = np.eye(k)
    for _ in range(max_iter):
        eta = X @ beta + off
        mu = 1.0 / (1.0 + np.exp(-np.clip(eta, -40.0, 40.0)))
        w = np.maximum(mu * (1.0 - mu), 1e-9)
        z = (eta - off) + (y - mu) / w
        XtWX = X.T @ (X * w[:, None])
        XtWz = X.T @ (w * z)
        try:
            nb = np.linalg.solve(XtWX, XtWz)
        except np.linalg.LinAlgError:
            nb = beta
        if np.max(np.abs(nb - beta)) < 1e-8:
            beta = nb
            break
        beta = nb
    try:
        cov = np.linalg.inv(XtWX)
        se = np.sqrt(np.diag(cov))
    except Exception:
        se = np.full(k, np.nan)
    return beta, se


def metrics(p, y) -> dict:
    """Compute Brier, log-loss, 10 equal-width-bin ECE, bias_pp, and IRLS slope/intercept."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    n = len(y)
    if n == 0:
        return {}
    pc = clip(p)
    brier = float(np.mean((p - y) ** 2))
    ll = float(-np.mean(y * np.log(pc) + (1.0 - y) * np.log(1.0 - pc)))
    ece = 0.0
    for lo in np.arange(0, 1.0, 0.1):
        m = (p >= lo) & (p < lo + 0.1 + (1e-9 if lo >= 0.9 else 0))
        if m.sum() > 0:
            ece += (m.sum() / n) * abs(y[m].mean() - p[m].mean())
    bias = float((p.mean() - y.mean()) * 100.0)
    x = np.log(pc / (1.0 - pc))
    X = np.column_stack([np.ones(n), x])
    beta, se = fit_irls(X, y)
    return dict(
        n=int(n), brier=brier, logloss=ll, ece=float(ece), bias_pp=bias,
        slope=float(beta[1]), slope_se=float(se[1]), intercept=float(beta[0]),
    )


def paired_diff(pa, pb, y, kind: str) -> tuple[float, float, float]:
    """Paired loss difference loss(pa) - loss(pb): (mean, se, z)."""
    pa = clip(np.asarray(pa, float))
    pb = clip(np.asarray(pb, float))
    y = np.asarray(y, float)
    if kind == "brier":
        d = (pa - y) ** 2 - (pb - y) ** 2
    else:
        d = -(y * np.log(pa) + (1.0 - y) * np.log(1.0 - pa)) + (y * np.log(pb) + (1.0 - y) * np.log(1.0 - pb))
    n = len(y)
    m = float(d.mean())
    se = float(d.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    z = float(m / se) if se and not math.isnan(se) and se > 0 else float("nan")
    return m, se, z


def blend_vec(p_model, p_mkt, c, a: float = 0.0, b: float = 1.0) -> np.ndarray:
    """Vectorized replica of ev.daily_projections.blend_p_over."""
    pm = np.clip(np.asarray(p_model, dtype=float), 0.01, 0.99)
    pk = np.clip(np.asarray(p_mkt, dtype=float), 0.01, 0.99)
    c_arr = np.asarray(c, dtype=float)
    lk = np.log(pk / (1.0 - pk))
    lm = np.log(pm / (1.0 - pm))
    z = float(a) + float(b) * lk + c_arr * (lm - lk)
    p = 1.0 / (1.0 + np.exp(-z))
    if (float(a), float(b)) != (0.0, 1.0):
        p = np.clip(p, pk - BLEND_MAX_SHIFT, pk + BLEND_MAX_SHIFT)
    return p


def opp_map(df: pd.DataFrame) -> dict:
    """Map (game_id, team_abbr) -> opponent team_abbr from two-team boxscores."""
    g = df.groupby("game_id")["team_abbr"].unique()
    m = {}
    for gid, teams in g.items():
        if len(teams) == 2:
            m[(gid, teams[0])] = teams[1]
            m[(gid, teams[1])] = teams[0]
    return m


def grade_box(Bsub: pd.DataFrame, master: pd.DataFrame, statcol: str):
    """Grade NBA board rows against boxscore master with +/-1 day fallback and actual return."""
    idx = {}
    for rec in master.itertuples(index=False):
        idx.setdefault((rec.pnorm, rec.team_abbr, rec.date), rec)
    out = []
    offs = collections.Counter()
    for r in Bsub.itertuples(index=False):
        cands = []
        for off in (0, 1, -1):
            dt = (pd.Timestamp(r.day) + pd.Timedelta(days=off)).strftime("%Y-%m-%d")
            rec = idx.get((r.pnorm, r.team, dt))
            if rec is not None:
                cands.append((off, rec))
        if not cands:
            out.append((np.nan, "nomatch", np.nan))
            continue
        pick = cands[0]
        if len(cands) > 1:
            for off, rec in cands:
                if getattr(rec, "opp_abbr", None) == r.opp:
                    pick = (off, rec)
                    break
        off, rec = pick
        offs[off] += 1
        if not rec.played_ok:
            out.append((np.nan, "dnp", np.nan))
            continue
        actual = getattr(rec, statcol)
        if actual is None or (isinstance(actual, float) and math.isnan(actual)):
            out.append((np.nan, "noact", np.nan))
            continue
        actual_f = float(actual)
        if actual_f > r.line:
            out.append((1.0, "ok", actual_f))
        elif actual_f < r.line:
            out.append((0.0, "ok", actual_f))
        else:
            out.append((np.nan, "push", actual_f))
    return out, offs


def load_and_grade_points() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load Nov 2-24 NBA points board archives, dedupe, grade vs boxscores, return (ok_df, full_box)."""
    boards = {}
    for f in sorted(glob.glob(os.path.join(ARCH_DIR, "2026-11-*.json"))):
        with open(f) as fh:
            d = json.load(fh)
        day = d["day"]
        if not (DAY_MIN <= day <= DAY_MAX):
            continue
        sp = d.get("sport", "nba")
        rows_iter = d["rows"].items() if isinstance(d["rows"], dict) else enumerate(d["rows"])
        for key, r in rows_iter:
            stat = r.get("stat")
            if stat != STAT_NAME:
                continue
            if not r.get("has_line") or r.get("line") is None or r.get("p_over_model") is None:
                continue
            boards[(sp, day, key)] = dict(
                sport=sp,
                day=day,
                row_key=key,
                stat=stat,
                player=r["player"],
                pnorm=norm(r["player"]),
                team=r.get("team"),
                opp=r.get("opp"),
                line=float(r["line"]),
                side=r.get("side"),
                p_model=r["p_over_model"],
                p_blend=r.get("p_over_blend"),
                p_market=r.get("p_over_market"),
                engine=r.get("engine"),
                usage_proj=r.get("usage_proj"),
                proj_mean=r.get("proj_mean"),
                proj_p10=r.get("proj_p10"),
                proj_p90=r.get("proj_p90"),
            )

    B = pd.DataFrame(boards.values())
    B = B.sort_values("side").drop_duplicates(
        subset=["sport", "day", "pnorm", "stat", "line"], keep="first"
    )

    nba = pd.read_csv(
        BOX_CSV,
        usecols=["game_id", "date", "team_abbr", "name", "played", "minutes", "points"],
        low_memory=False,
    )
    nba["pnorm"] = nba["name"].map(norm)
    parts = nba["minutes"].astype(str).str.split(":", n=1, expand=True)
    mm = pd.to_numeric(parts[0], errors="coerce")
    ss = pd.to_numeric(parts[1], errors="coerce").fillna(0.0)
    nba["mins_num"] = mm
    nba["mins_full"] = mm + ss / 60.0
    nba["played_ok"] = (nba["played"] == True) & (nba["mins_num"] > 0)  # noqa: E712

    nba_grade = nba[nba["date"] >= "2026-10-28"].copy()
    om = opp_map(nba_grade)
    nba_grade["opp_abbr"] = [om.get((g, t)) for g, t in zip(nba_grade["game_id"], nba_grade["team_abbr"])]

    sub = B[(B["sport"] == "nba") & (B["stat"] == STAT_NAME)].copy()
    graded, _ = grade_box(sub, nba_grade, STAT_NAME)
    sub["y"] = [g[0] for g in graded]
    sub["gstat"] = [g[1] for g in graded]
    sub["actual"] = [g[2] for g in graded]
    ok_df = sub[sub["gstat"] == "ok"].copy()
    return ok_df, nba


def run_sanity_replication(ok_df: pd.DataFrame) -> dict:
    """Compare full Nov 2-24 points h2h metrics against audit_results.json families['nba:points']."""
    with open(AUDIT_JSON) as fh:
        aud = json.load(fh)["families"][f"nba:{STAT_NAME}"]

    h2h = ok_df[ok_df["p_market"].notna()].copy()
    m_mod = metrics(h2h["p_model"], h2h["y"])
    m_mkt = metrics(h2h["p_market"], h2h["y"])
    bb = h2h[h2h["p_blend"].notna()]
    m_bld = metrics(bb["p_blend"], bb["y"])

    return {
        "n_h2h": int(len(h2h)),
        "audit_n_h2h": int(aud["n_h2h"]),
        "diff_n_h2h": int(len(h2h) - aud["n_h2h"]),
        "model_logloss": m_mod["logloss"],
        "audit_model_logloss": aud["model"]["logloss"],
        "diff_model_logloss": float(m_mod["logloss"] - aud["model"]["logloss"]),
        "market_logloss": m_mkt["logloss"],
        "audit_market_logloss": aud["market"]["logloss"],
        "diff_market_logloss": float(m_mkt["logloss"] - aud["market"]["logloss"]),
        "blend_logloss": m_bld["logloss"],
        "audit_blend_logloss": aud["blend"]["logloss"],
        "diff_blend_logloss": float(m_bld["logloss"] - aud["blend"]["logloss"]),
        "model_slope": m_mod["slope"],
        "audit_model_slope": aud["model"]["slope"],
        "diff_model_slope": float(m_mod["slope"] - aud["model"]["slope"]),
        "model_ece": m_mod["ece"],
        "audit_model_ece": aud["model"]["ece"],
        "diff_model_ece": float(m_mod["ece"] - aud["model"]["ece"]),
    }


def compute_league_fallback_rate(box_df: pd.DataFrame) -> float:
    """Compute fit-window (Nov 2-15) league-average points per minute over played_ok box rows."""
    fit_box = box_df[
        box_df["played_ok"]
        & (box_df["date"] >= DAY_MIN)
        & (box_df["date"] <= FIT_MAX_DAY)
        & box_df["points"].notna()
        & (box_df["mins_full"] > 0)
    ]
    total_pts = float(fit_box["points"].sum())
    total_mins = float(fit_box["mins_full"].sum())
    if total_mins <= 0.0:
        raise ValueError("Fit-window boxscores have zero total minutes for fallback rate")
    return total_pts / total_mins


def compute_trailing_rates(
    df: pd.DataFrame,
    box_df: pd.DataFrame,
    fallback_rate: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute expanding trailing points-per-minute strictly before each row's day."""
    pok = box_df[
        box_df["played_ok"] & box_df["points"].notna() & (box_df["mins_full"] > 0)
    ].copy()
    pok = pok.sort_values(["pnorm", "date", "game_id"], kind="mergesort")
    g = pok.groupby("pnorm", sort=False)
    pok["cum_pts"] = g["points"].cumsum().astype(float)
    pok["cum_mins"] = g["mins_full"].cumsum().astype(float)
    pok["cum_games"] = g.cumcount() + 1

    # Collapse same-day games so merge_asof sees end-of-day cumulative state per (pnorm, date).
    daily = (
        pok.groupby(["pnorm", "date"], as_index=False, sort=False)
        .last()[["pnorm", "date", "cum_pts", "cum_mins", "cum_games"]]
    )
    daily["date_dt"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values(["date_dt", "pnorm"], kind="mergesort").reset_index(drop=True)

    query = pd.DataFrame({
        "_orig_idx": np.arange(len(df)),
        "pnorm": df["pnorm"].to_numpy(),
        "day_dt": pd.to_datetime(df["day"]),
    }).sort_values(["day_dt", "pnorm"], kind="mergesort")

    merged = pd.merge_asof(
        query,
        daily[["pnorm", "date_dt", "cum_pts", "cum_mins", "cum_games"]],
        left_on="day_dt",
        right_on="date_dt",
        by="pnorm",
        direction="backward",
        allow_exact_matches=False,
    ).sort_values("_orig_idx", kind="mergesort")

    prior_games = merged["cum_games"].fillna(0).to_numpy(dtype=int)
    cum_pts = merged["cum_pts"].to_numpy(dtype=float)
    cum_mins = merged["cum_mins"].to_numpy(dtype=float)

    use_player = (
        (prior_games >= MIN_PRIOR_GAMES)
        & np.isfinite(cum_pts)
        & np.isfinite(cum_mins)
        & (cum_mins > 0.0)
    )
    rates = np.where(use_player, cum_pts / cum_mins, float(fallback_rate))
    return rates, prior_games


def compute_candidate_probs(
    df: pd.DataFrame,
    rates: np.ndarray,
) -> tuple[np.ndarray, dict[str, int]]:
    """Compute Normal(usage_proj * rate, sd_row) over probabilities and count exclusions."""
    usage = pd.to_numeric(df["usage_proj"], errors="coerce").to_numpy(dtype=float)
    p10 = pd.to_numeric(df["proj_p10"], errors="coerce").to_numpy(dtype=float)
    p90 = pd.to_numeric(df["proj_p90"], errors="coerce").to_numpy(dtype=float)
    line = pd.to_numeric(df["line"], errors="coerce").to_numpy(dtype=float)
    rate_arr = np.asarray(rates, dtype=float)

    sd_row = (p90 - p10) / NORM_P90_P10_DIVISOR

    bad_usage = ~np.isfinite(usage)
    bad_sd = (~np.isfinite(p10)) | (~np.isfinite(p90)) | (~np.isfinite(sd_row)) | (sd_row <= 0.0)
    bad_rate = ~np.isfinite(rate_arr)
    bad_line = ~np.isfinite(line)

    valid_inputs = (~bad_usage) & (~bad_sd) & (~bad_rate) & (~bad_line)
    p_cand = np.full(len(df), np.nan, dtype=float)
    if valid_inputs.any():
        mu = usage[valid_inputs] * rate_arr[valid_inputs]
        z = (line[valid_inputs] - mu) / sd_row[valid_inputs]
        p_cand[valid_inputs] = 1.0 - scipy_norm.cdf(z)

    bad_pcand = valid_inputs & (~np.isfinite(p_cand))
    p_cand[bad_pcand] = np.nan

    excl_counts = {
        "missing_usage_proj": int(bad_usage.sum()),
        "invalid_p10_p90_or_sd": int(bad_sd.sum()),
        "missing_rate": int(bad_rate.sum()),
        "invalid_line": int(bad_line.sum()),
        "invalid_p_cand": int(bad_pcand.sum()),
    }
    return p_cand, excl_counts


def fit_blend_c(p_cand: np.ndarray, p_mkt: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Grid-search c in [0, 1] step 0.01 minimizing fit log-loss (logit blend)."""
    best_c = 0.0
    best_ll = float("inf")
    for i in range(101):
        c = round(i * 0.01, 2)
        p = clip(blend_vec(p_cand, p_mkt, c, a=0.0, b=1.0))
        ll = float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))
        if ll < best_ll:
            best_ll = ll
            best_c = c
    return float(best_c), float(best_ll)


def main() -> None:
    ok_df, box_df = load_and_grade_points()

    replication = run_sanity_replication(ok_df)
    print("=== Sanity Replication (Nov 2-24 nba:points vs audit_results.json) ===")
    print(
        f"nba:points n_h2h={replication['n_h2h']} (diff={replication['diff_n_h2h']:+d}) "
        f"d_modLL={replication['diff_model_logloss']:+.6f} "
        f"d_mktLL={replication['diff_market_logloss']:+.6f} "
        f"d_bldLL={replication['diff_blend_logloss']:+.6f} "
        f"d_slope={replication['diff_model_slope']:+.4f} "
        f"d_ece={replication['diff_model_ece']:+.6f}"
    )

    fallback_rate = compute_league_fallback_rate(box_df)
    rates, prior_games = compute_trailing_rates(ok_df, box_df, fallback_rate)
    p_cand_all, cand_excl_all = compute_candidate_probs(ok_df, rates)

    ok_df = ok_df.copy()
    ok_df["prior_games"] = prior_games
    ok_df["trailing_rate"] = rates
    ok_df["p_cand"] = p_cand_all

    has_mkt = ok_df["p_market"].notna()
    has_cand = np.isfinite(ok_df["p_cand"].to_numpy(dtype=float))
    eval_mask = has_mkt & has_cand

    fit_all_mask = ok_df["day"] <= FIT_MAX_DAY
    hold_all_mask = ok_df["day"] >= HOLDOUT_MIN_DAY

    fit_eval = ok_df[fit_all_mask & eval_mask].copy()
    hold_eval = ok_df[hold_all_mask & eval_mask].copy()

    # Windowed exclusion counts
    _, fit_h2h_excl = compute_candidate_probs(
        ok_df[fit_all_mask & has_mkt],
        ok_df.loc[fit_all_mask & has_mkt, "trailing_rate"].to_numpy(dtype=float),
    )
    _, hold_h2h_excl = compute_candidate_probs(
        ok_df[hold_all_mask & has_mkt],
        ok_df.loc[hold_all_mask & has_mkt, "trailing_rate"].to_numpy(dtype=float),
    )
    exclusions = {
        "graded_total": int(len(ok_df)),
        "missing_market_total": int((~has_mkt).sum()),
        "missing_market_fit": int((fit_all_mask & (~has_mkt)).sum()),
        "missing_market_holdout": int((hold_all_mask & (~has_mkt)).sum()),
        "candidate_all_graded": cand_excl_all,
        "candidate_fit_h2h": fit_h2h_excl,
        "candidate_holdout_h2h": hold_h2h_excl,
        "used_league_fallback_fit_eval": int((fit_eval["prior_games"] < MIN_PRIOR_GAMES).sum()),
        "used_league_fallback_holdout_eval": int((hold_eval["prior_games"] < MIN_PRIOR_GAMES).sum()),
    }

    p_cand_h = hold_eval["p_cand"].to_numpy(dtype=float)
    p_mod_h = hold_eval["p_model"].to_numpy(dtype=float)
    p_mkt_h = hold_eval["p_market"].to_numpy(dtype=float)
    y_h = hold_eval["y"].to_numpy(dtype=float)

    cand_m = metrics(p_cand_h, y_h)
    mod_m = metrics(p_mod_h, y_h)
    mkt_m = metrics(p_mkt_h, y_h)

    cm_mean, cm_se, cm_z = paired_diff(p_cand_h, p_mkt_h, y_h, "logloss")
    cs_mean, cs_se, cs_z = paired_diff(p_cand_h, p_mod_h, y_h, "logloss")

    slope_ok = bool(cand_m["slope"] >= SLOPE_BAR)
    # Pre-registered bar z is in the improvement direction: z of the paired
    # (market - candidate) LL diffs. cm_z above is candidate-minus-market
    # (positive = candidate worse, the audit's d_logloss convention), so the
    # bar statistic is -cm_z: "no longer significantly worse" <=> -cm_z > -1.65.
    z_bar = -cm_z
    ll_not_sig_worse = bool(z_bar > Z_BAR)
    ll_beats_served = bool(cand_m["logloss"] < mod_m["logloss"])
    verdict = "PASS" if (slope_ok and ll_not_sig_worse and ll_beats_served) else "FAIL"

    proposed_c = None
    proposal_details = None
    if verdict == "PASS":
        c_fit, fit_bld_ll = fit_blend_c(
            fit_eval["p_cand"].to_numpy(dtype=float),
            fit_eval["p_market"].to_numpy(dtype=float),
            fit_eval["y"].to_numpy(dtype=float),
        )
        proposed_c = c_fit
        p_bld_h = blend_vec(p_cand_h, p_mkt_h, c_fit, a=0.0, b=1.0)
        proposal_details = {
            "proposed_c": c_fit,
            "fit_blend_logloss": fit_bld_ll,
            "holdout_blend": metrics(p_bld_h, y_h),
        }

    fit_out = {
        "n": int(len(fit_eval)),
        "graded_n": int(fit_all_mask.sum()),
        "h2h_n": int((fit_all_mask & has_mkt).sum()),
    }
    holdout_out = {
        "n": int(len(hold_eval)),
        "graded_n": int(hold_all_mask.sum()),
        "h2h_n": int((hold_all_mask & has_mkt).sum()),
        "candidate": cand_m,
        "served_model_raw": mod_m,
        "market": mkt_m,
        "paired_ll_diff_candidate_vs_market": {
            "mean": cm_mean,
            "se": cm_se,
            "z": cm_z,
            "bar_z_market_minus_candidate": z_bar,
        },
        "paired_ll_diff_candidate_vs_served": {
            "mean": cs_mean,
            "se": cs_se,
            "z": cs_z,
        },
        "bar": {
            "slope_ge_0_30": slope_ok,
            "ll_vs_market_z_gt_minus_1_65": ll_not_sig_worse,
            "ll_beats_served_raw": ll_beats_served,
        },
        "verdict": verdict,
    }
    if proposal_details is not None:
        holdout_out["proposal_blend"] = proposal_details

    payload = {
        "candidate": "mod03_points_mu_v1",
        "note": "Pre-registered holdout Nov 16-25; graded Nov 16-24 because Nov 25 games are unplayed.",
        "replication": replication,
        "league_fallback_per_min_rate": float(fallback_rate),
        "fit": fit_out,
        "holdout": holdout_out,
        "proposed_c": proposed_c,
        "exclusions": exclusions,
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as fh:
        json.dump(payload, fh, indent=2)

    print("\n=== Candidate: mod03_points_mu_v1 (Nov 16-24 Holdout; Nov 25 unplayed) ===")
    print(
        f"league_fallback_rate={fallback_rate:.6f} pts/min | "
        f"fit n={fit_out['n']}/{fit_out['graded_n']} (fallback={exclusions['used_league_fallback_fit_eval']}) | "
        f"holdout n={holdout_out['n']}/{holdout_out['graded_n']} (fallback={exclusions['used_league_fallback_holdout_eval']})"
    )
    print(
        f"candidate: LL={cand_m['logloss']:.4f} brier={cand_m['brier']:.4f} "
        f"slope={cand_m['slope']:.3f} (se={cand_m['slope_se']:.3f}) ece={cand_m['ece']:.4f} bias={cand_m['bias_pp']:+.2f}pp"
    )
    print(
        f"servedraw: LL={mod_m['logloss']:.4f} brier={mod_m['brier']:.4f} "
        f"slope={mod_m['slope']:.3f} ece={mod_m['ece']:.4f} bias={mod_m['bias_pp']:+.2f}pp"
    )
    print(
        f"market   : LL={mkt_m['logloss']:.4f} brier={mkt_m['brier']:.4f} "
        f"slope={mkt_m['slope']:.3f} ece={mkt_m['ece']:.4f} bias={mkt_m['bias_pp']:+.2f}pp"
    )
    print(
        f"paired LL diff: cand-mkt={cm_mean:+.5f} (se={cm_se:.5f}, z={cm_z:+.2f}) | "
        f"cand-served={cs_mean:+.5f} (se={cs_se:.5f}, z={cs_z:+.2f})"
    )
    print(
        f"bar: slope>=0.30:{slope_ok} bar_z(mkt-cand)={z_bar:+.2f}>-1.65:{ll_not_sig_worse} LL<served:{ll_beats_served} | "
        f"verdict={verdict} | proposed_c={proposed_c}"
    )
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
