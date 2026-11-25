#!/usr/bin/env python3
"""Reproducible study harness for NBA combo joint NegBin distribution.

PRE-REGISTERED CANDIDATE — mod03_joint_v1
- Per combo family f in {pra, pr, pa, ra}: the combo total is distributed NegBin moment-matched to mu = the archived combo row's proj_mean, variance = κ_f × var_served, where var_served is recovered from the archived combo row's proj_p10/proj_p90: sd = (p90 − p10) / 2.5632, var_served = sd².
- κ_f is fitted ONLY on the fit window (Nov 2–15 graded rows): κ_raw = Σ(actual − mu)² / Σ var_served over fit rows; shrunk κ_f = (n·κ_raw + 20) / (n + 20).
- NegBin parameterization: given (mu, var) with var > mu: size r = mu²/(var − mu), p = r/(r + mu); P(over line) = P(X > line) for the count X (line is a .5 line; use the exact discrete tail). If var ≤ mu, use Poisson(mu).
- Blend arbitration (MOD-02 harness convention): p_blend = logit-space blend of p_candidate with p_market at weight c_f, c_f fitted on the fit window only by grid search over [0, 1] step 0.01 minimizing log-loss.
- HOLDOUT GATE (untouched Nov 16–25; in practice grade Nov 16–24 — Nov 25 games are unplayed; say so in outputs): per family, PASS requires ALL of: paired log-loss of p_blend beats the market's log-loss on identical rows; calibration slope of p_blend in [0.75, 1.33]; ECE of p_blend < 0.03. Report candidate-raw metrics alongside.
"""
from __future__ import annotations

import collections
import glob
import json
import math
import os
import sys
import unicodedata

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from nba.props.joint_combo import fit_kappa, p_over_joint, var_from_p10_p90  # noqa: E402

ARCH_DIR = os.environ.get("MOD03_ARCH") or os.path.expanduser("~/workspace/mod03-combo-work/archive")
BOX_CSV = os.environ.get("MOD03_BOX") or os.path.expanduser("~/workspace/mod03-combo-work/player_boxscores_master.csv")
AUDIT_JSON = os.path.expanduser("~/workspace/beezy/probability-audit-work/audit_results.json")
OUT_JSON = os.path.expanduser("~/workspace/beezy/mod03-combo-joint-results.json")

COMBO_STATS = ("pra", "pr", "pa", "ra")
DAY_MIN = "2026-11-02"
DAY_MAX = "2026-11-24"
FIT_MAX_DAY = "2026-11-15"
HOLDOUT_MIN_DAY = "2026-11-16"
BLEND_MAX_SHIFT = 0.08


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


def load_and_grade_combos() -> dict[str, pd.DataFrame]:
    """Load Nov 2-24 NBA board archives, dedupe over-side, and grade combo props."""
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
            if stat not in COMBO_STATS:
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
        usecols=[
            "game_id", "date", "team_abbr", "name", "played", "minutes", "points",
            "rebounds", "assists", "three_points_made", "steals", "blocks", "turnovers",
        ],
    )
    nba = nba[nba["date"] >= "2026-10-28"].copy()
    nba["pnorm"] = nba["name"].map(norm)
    om = opp_map(nba)
    nba["opp_abbr"] = [om.get((g, t)) for g, t in zip(nba["game_id"], nba["team_abbr"])]
    nba["mins_num"] = nba["minutes"].astype(str).str.split(":").str[0].astype(float)
    nba["played_ok"] = (nba["played"] == True) & (nba["mins_num"] > 0)  # noqa: E712
    nba["pra"] = nba["points"] + nba["rebounds"] + nba["assists"]
    nba["pr"] = nba["points"] + nba["rebounds"]
    nba["pa"] = nba["points"] + nba["assists"]
    nba["ra"] = nba["rebounds"] + nba["assists"]

    results = {}
    for stat in COMBO_STATS:
        sub = B[(B["sport"] == "nba") & (B["stat"] == stat)].copy()
        graded, _ = grade_box(sub, nba, stat)
        sub["y"] = [g[0] for g in graded]
        sub["gstat"] = [g[1] for g in graded]
        sub["actual"] = [g[2] for g in graded]
        results[stat] = sub[sub["gstat"] == "ok"].copy()
    return results


def run_sanity_replication(results: dict[str, pd.DataFrame]) -> dict:
    """Compare full Nov 2-24 served model/market/blend h2h metrics against audit_results.json."""
    with open(AUDIT_JSON) as fh:
        audit_families = json.load(fh)["families"]

    rep = {}
    for stat in COMBO_STATS:
        sub = results[stat]
        h2h = sub[sub["p_market"].notna()].copy()
        aud = audit_families[f"nba:{stat}"]

        m_mod = metrics(h2h["p_model"], h2h["y"])
        m_mkt = metrics(h2h["p_market"], h2h["y"])
        bb = h2h[h2h["p_blend"].notna()]
        m_bld = metrics(bb["p_blend"], bb["y"])

        rep[stat] = {
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
    return rep


def _compute_var_served(df: pd.DataFrame) -> np.ndarray:
    p10 = pd.to_numeric(df["proj_p10"], errors="coerce").to_numpy(dtype=float)
    p90 = pd.to_numeric(df["proj_p90"], errors="coerce").to_numpy(dtype=float)
    out = np.full(len(df), np.nan, dtype=float)
    for i, (a, b) in enumerate(zip(p10, p90)):
        if math.isfinite(a) and math.isfinite(b):
            out[i] = var_from_p10_p90(a, b)
    return out


def _compute_candidate_probs(df: pd.DataFrame, kappa_f: float) -> np.ndarray:
    mu_arr = pd.to_numeric(df["proj_mean"], errors="coerce").to_numpy(dtype=float)
    line_arr = pd.to_numeric(df["line"], errors="coerce").to_numpy(dtype=float)
    var_served = _compute_var_served(df)
    out = np.full(len(df), np.nan, dtype=float)
    for i, (mu, vs, line) in enumerate(zip(mu_arr, var_served, line_arr)):
        if math.isfinite(vs) and vs > 0.0:
            p = p_over_joint(mu, kappa_f * vs, line)
            if p is not None and math.isfinite(p):
                out[i] = float(p)
    return out


def fit_family(fit_df: pd.DataFrame) -> dict:
    """Fit frozen (kappa_f, c_f) using ONLY fit-window rows (day <= 2026-11-15)."""
    assert (fit_df["day"] <= FIT_MAX_DAY).all(), "fit_family received non-fit-window rows"

    mu_arr = pd.to_numeric(fit_df["proj_mean"], errors="coerce").to_numpy(dtype=float)
    act_arr = pd.to_numeric(fit_df["actual"], errors="coerce").to_numpy(dtype=float)
    var_served = _compute_var_served(fit_df)

    kappa_mask = (
        np.isfinite(mu_arr)
        & np.isfinite(act_arr)
        & np.isfinite(var_served)
        & (var_served > 0.0)
    )
    fit_n_kappa = int(kappa_mask.sum())
    sum_sq_resid = float(np.sum((act_arr[kappa_mask] - mu_arr[kappa_mask]) ** 2))
    sum_var_served = float(np.sum(var_served[kappa_mask]))
    kappa_raw, kappa_f = fit_kappa(sum_sq_resid, sum_var_served, fit_n_kappa, shrink=20.0)

    c_df = fit_df[fit_df["p_market"].notna()].copy()
    p_cand_fit = _compute_candidate_probs(c_df, kappa_f)
    valid_c = np.isfinite(p_cand_fit)
    pm = p_cand_fit[valid_c]
    pk = c_df["p_market"].to_numpy(dtype=float)[valid_c]
    y = c_df["y"].to_numpy(dtype=float)[valid_c]
    fit_n_c = int(valid_c.sum())

    best_c = 0.0
    best_ll = float("inf")
    for i in range(101):
        c = round(i * 0.01, 2)
        p = clip(blend_vec(pm, pk, c, a=0.0, b=1.0))
        ll = float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))
        if ll < best_ll:
            best_ll = ll
            best_c = c

    return {
        "fit_n_kappa": fit_n_kappa,
        "fit_n_c": fit_n_c,
        "kappa_raw": float(kappa_raw),
        "kappa_f": float(kappa_f),
        "c_f": float(best_c),
    }


def grade_family_holdout(hold_df: pd.DataFrame, frozen: dict) -> dict:
    """Grade holdout rows (day >= 2026-11-16) using frozen (kappa_f, c_f) from fit_family."""
    assert (hold_df["day"] >= HOLDOUT_MIN_DAY).all(), "grade_family_holdout received non-holdout rows"

    h2h = hold_df[hold_df["p_market"].notna()].copy()
    p_cand_all = _compute_candidate_probs(h2h, frozen["kappa_f"])
    valid = np.isfinite(p_cand_all)
    eval_df = h2h.loc[valid].copy()
    p_cand = p_cand_all[valid]
    p_mkt = eval_df["p_market"].to_numpy(dtype=float)
    y = eval_df["y"].to_numpy(dtype=float)
    p_bld = blend_vec(p_cand, p_mkt, frozen["c_f"], a=0.0, b=1.0)

    cand_m = metrics(p_cand, y)
    bld_m = metrics(p_bld, y)
    mkt_m = metrics(p_mkt, y)
    ll_mean, ll_se, ll_z = paired_diff(p_bld, p_mkt, y, "logloss")

    ll_beats_market = bool(ll_mean < 0.0)
    slope_ok = bool(0.75 <= bld_m["slope"] <= 1.33)
    ece_ok = bool(bld_m["ece"] < 0.03)
    verdict = "PASS" if (ll_beats_market and slope_ok and ece_ok) else "FAIL"

    return {
        "fit_n_kappa": frozen["fit_n_kappa"],
        "fit_n_c": frozen["fit_n_c"],
        "kappa_raw": frozen["kappa_raw"],
        "kappa_f": frozen["kappa_f"],
        "c_f": frozen["c_f"],
        "holdout_n": int(len(eval_df)),
        "candidate": cand_m,
        "blend": bld_m,
        "market": mkt_m,
        "paired_ll_diff_blend_vs_market": {
            "mean": ll_mean,
            "se": ll_se,
            "z": ll_z,
        },
        "gate": {
            "ll_beats_market": ll_beats_market,
            "slope_ok": slope_ok,
            "ece_ok": ece_ok,
            "verdict": verdict,
        },
    }


def main() -> None:
    results = load_and_grade_combos()

    replication = run_sanity_replication(results)
    print("=== Sanity Replication (Nov 2-24 vs audit_results.json) ===")
    for stat, r in replication.items():
        print(
            f"nba:{stat:3s} n_h2h={r['n_h2h']} (diff={r['diff_n_h2h']:+d}) "
            f"d_modLL={r['diff_model_logloss']:+.6f} d_mktLL={r['diff_market_logloss']:+.6f} "
            f"d_bldLL={r['diff_blend_logloss']:+.6f} d_slope={r['diff_model_slope']:+.4f} "
            f"d_ece={r['diff_model_ece']:+.6f}"
        )

    families = {}
    print("\n=== Holdout Gate Summary (Nov 16-24; Nov 25 games unplayed) ===")
    for stat in COMBO_STATS:
        sub = results[stat]
        fit_df = sub[sub["day"] <= FIT_MAX_DAY].copy()
        hold_df = sub[sub["day"] >= HOLDOUT_MIN_DAY].copy()
        frozen = fit_family(fit_df)
        fam_out = grade_family_holdout(hold_df, frozen)
        families[stat] = fam_out

        d = fam_out["paired_ll_diff_blend_vs_market"]
        g = fam_out["gate"]
        print(
            f"nba:{stat:3s} fit_n=({fam_out['fit_n_kappa']},{fam_out['fit_n_c']}) "
            f"kappa={fam_out['kappa_f']:.3f} (raw={fam_out['kappa_raw']:.3f}) c={fam_out['c_f']:.2f} "
            f"hold_n={fam_out['holdout_n']} | "
            f"candLL={fam_out['candidate']['logloss']:.4f} "
            f"bldLL={fam_out['blend']['logloss']:.4f} mktLL={fam_out['market']['logloss']:.4f} "
            f"diff={d['mean']:+.5f} (z={d['z']:+.2f}) | "
            f"bld_slope={fam_out['blend']['slope']:.3f} bld_ece={fam_out['blend']['ece']:.4f} | "
            f"{g['verdict']}"
        )

    payload = {
        "candidate": "mod03_joint_v1",
        "note": "Pre-registered holdout Nov 16-25; graded Nov 16-24 because Nov 25 games are unplayed.",
        "replication": replication,
        "families": families,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nWrote {OUT_JSON}")


if __name__ == "__main__":
    main()
