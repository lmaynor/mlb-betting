#!/usr/bin/env python3
"""MOD-02 Phase 2 refit/grade harness.

Replicates the serving blend (ev.daily_projections.blend_p_over) and probability
audit row-building/grading, runs replication checks against audit_results.json,
fits candidate blend parameters on the fit window (<= 2026-11-15), grades on the
holdout window (>= 2026-11-16), evaluates ship gates, and runs the provisional
MLB recalibration section.
"""
from __future__ import annotations

import collections
import glob
import json
import math
import os
import re
import unicodedata

import numpy as np
import pandas as pd

W = "/tmp/proqaudit/"
ARCH = "/tmp/novigcut/arch/"
AUDIT_JSON = os.path.expanduser("~/workspace/beezy/probability-audit-work/audit_results.json")
MLB_JSON = "/tmp/novigcut/mlb_all.json"
OUT_JSON = os.path.expanduser("~/workspace/beezy/mod02-refit-results.json")

DAY_MIN = "2026-11-02"
DAY_MAX = "2026-11-24"
FIT_MAX_DAY = "2026-11-15"
HOLDOUT_MIN_DAY = "2026-11-16"
MIN_SPLIT_N = 100
MLB_MIN_SPLIT_N = 50  # Provisional MLB split guard (allows 292-row pitcher_er_o25 204/88 split)

# Serving blend constants replicated verbatim from ev/daily_projections.py
BLEND_C = 0.10
BLEND_C_BY_SPORT_STAT = {
    "nba": {
        "threes": 0.07, "rebounds": 0.14, "assists": 0.00, "ra": 0.00,
        "pr": 0.00, "pa": 0.00, "pra": 0.06, "points": 0.06,
        "steals": 0.00, "blocks": 0.00,
    },
    "nhl": {
        "sog": 0.30, "saves": 0.15, "blocked_shots": 0.00, "points": 0.03,
        "goals": 0.03, "assists": 0.06, "pp_points": 0.00,
    },
}
BLEND_C_TRAINED = {
    "nhl": {"goals": 0.37, "assists": 0.34, "points": 0.175, "saves": 0.35},
}
BLEND_MAX_SHIFT = 0.08

NBA_STAT = {
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    "threes": "three_points_made", "steals": "steals", "blocks": "blocks",
    "pra": "pra", "pr": "pr", "pa": "pa", "ra": "ra",
}
NHL_STAT = {
    "sog": ("sk", "sog"), "goals": ("sk", "goals"), "assists": ("sk", "assists"),
    "points": ("sk", "points"), "blocked_shots": ("sk", "blocked_shots"),
    "saves": ("gk", "saves"),
}
NFL_STAT = {
    "pass_yds": "passing_yards", "pass_attempts": "attempts",
    "pass_completions": "completions", "pass_tds": "passing_tds",
    "interceptions": "passing_interceptions", "rush_yds": "rushing_yards",
    "rush_attempts": "carries", "rec_yds": "receiving_yards",
    "receptions": "receptions", "anytime_td": "anytd",
}


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
    """Logistic regression IRLS matching audit.py's solver, with optional fixed offset."""
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
    """Compute Brier, log-loss, 10-bin ECE, bias_pp, and IRLS slope/intercept."""
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


def blend_c(sport: str | None, stat: str | None, trained: bool = False) -> float:
    """Return current deployed blend weight c for (sport, stat, trained)."""
    if trained:
        c = BLEND_C_TRAINED.get(sport or "", {}).get(stat)
        if c is not None:
            return float(c)
    table = BLEND_C_BY_SPORT_STAT.get(sport or "", {})
    return float(table.get(stat, table.get("default", BLEND_C)))


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


def nfl_week(day: str):
    """Map board day string to 2026 NFL regular season week."""
    for lo, hi, wk in [
        ("2026-10-29", "2026-11-02", 9),
        ("2026-11-05", "2026-11-09", 10),
        ("2026-11-12", "2026-11-16", 11),
        ("2026-11-19", "2026-11-23", 12),
        ("2026-11-26", "2026-11-30", 13),
    ]:
        if lo <= day <= hi:
            return wk
    return None


def grade_box(Bsub: pd.DataFrame, master: pd.DataFrame, statcol: str):
    """Grade NBA/NHL board rows against boxscore master with +/-1 day fallback."""
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
            out.append((np.nan, "nomatch"))
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
            out.append((np.nan, "dnp"))
            continue
        actual = getattr(rec, statcol)
        if actual is None or (isinstance(actual, float) and math.isnan(actual)):
            out.append((np.nan, "noact"))
            continue
        if actual > r.line:
            out.append((1.0, "ok"))
        elif actual < r.line:
            out.append((0.0, "ok"))
        else:
            out.append((np.nan, "push"))
    return out, offs


def load_and_grade_props() -> dict[tuple[str, str], pd.DataFrame]:
    """Load Nov 2-24 board archives, dedupe over-side, and grade against masters."""
    boards = {}
    for sp in ["nba", "nhl", "nfl"]:
        for f in sorted(glob.glob(f"{ARCH}{sp}_props/*.json")):
            with open(f) as fh:
                d = json.load(fh)
            day = d["day"]
            if not (DAY_MIN <= day <= DAY_MAX):
                continue
            rows_iter = d["rows"].items() if isinstance(d["rows"], dict) else enumerate(d["rows"])
            for key, r in rows_iter:
                if not r.get("has_line") or r.get("line") is None or r.get("p_over_model") is None:
                    continue
                boards[(sp, day, key)] = dict(
                    sport=sp, day=day, row_key=key, stat=r["stat"], player=r["player"],
                    pnorm=norm(r["player"]), team=r.get("team"), opp=r.get("opp"),
                    line=float(r["line"]), side=r.get("side"),
                    p_model=r["p_over_model"], p_blend=r.get("p_over_blend"),
                    p_market=r.get("p_over_market"), engine=r.get("engine"),
                    nhl_model=r.get("nhl_model"),
                )
    B = pd.DataFrame(boards.values())
    B = B.sort_values("side").drop_duplicates(
        subset=["sport", "day", "pnorm", "stat", "line"], keep="first"
    )

    nba = pd.read_csv(
        W + "player_boxscores_master.csv",
        usecols=["game_id", "date", "team_abbr", "name", "played", "minutes", "points",
                 "rebounds", "assists", "three_points_made", "steals", "blocks", "turnovers"],
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

    sk = pd.read_csv(
        W + "skater_boxscores_master.csv",
        usecols=["game_id", "date", "team_abbr", "name", "played", "toi_sec",
                 "goals", "assists", "points", "sog", "blocked_shots"],
    )
    sk = sk[sk["date"] >= "2026-10-28"].copy()
    sk["pnorm"] = sk["name"].map(norm)
    om = opp_map(sk)
    sk["opp_abbr"] = [om.get((g, t)) for g, t in zip(sk["game_id"], sk["team_abbr"])]
    sk["played_ok"] = (sk["played"] == True) & (sk["toi_sec"] > 0)  # noqa: E712

    gk = pd.read_csv(
        W + "goalie_boxscores_master.csv",
        usecols=["game_id", "date", "team_abbr", "name", "played", "toi_sec", "saves"],
    )
    gk = gk[gk["date"] >= "2026-10-28"].copy()
    gk["pnorm"] = gk["name"].map(norm)
    om = opp_map(gk)
    gk["opp_abbr"] = [om.get((g, t)) for g, t in zip(gk["game_id"], gk["team_abbr"])]
    gk["played_ok"] = (gk["played"] == True) & (gk["toi_sec"] > 0)  # noqa: E712

    nfl = pd.read_parquet(W + "player_stats_2026.parquet")
    nfl = nfl[nfl["season_type"] == "REG"].copy()
    nfl["pnorm"] = nfl["player_display_name"].map(norm)
    nfl["anytd"] = (nfl["rushing_tds"].fillna(0) + nfl["receiving_tds"].fillna(0)).astype(float)

    results = {}
    for sp, statmap, masters in [("nba", NBA_STAT, {"nba": nba}), ("nhl", None, {"sk": sk, "gk": gk})]:
        for stat in sorted(B[B.sport == sp]["stat"].unique()):
            sub = B[(B.sport == sp) & (B.stat == stat)].copy()
            if sp == "nba":
                if stat not in statmap:
                    continue
                graded, _ = grade_box(sub, nba, statmap[stat])
            else:
                if stat not in NHL_STAT:
                    continue
                src, col = NHL_STAT[stat]
                graded, _ = grade_box(sub, masters[src], col)
            sub["y"] = [g[0] for g in graded]
            sub["gstat"] = [g[1] for g in graded]
            results[(sp, stat)] = sub[sub["gstat"] == "ok"].copy()

    nfl_idx = {}
    for rec in nfl.itertuples(index=False):
        nfl_idx[(rec.pnorm, rec.week)] = rec
    for stat in sorted(B[B.sport == "nfl"]["stat"].unique()):
        if stat not in NFL_STAT:
            continue
        sub = B[(B.sport == "nfl") & (B.stat == stat)].copy()
        ys, gs = [], []
        for r in sub.itertuples(index=False):
            wk = nfl_week(r.day)
            rec = nfl_idx.get((r.pnorm, wk)) if wk else None
            if rec is None:
                ys.append(np.nan)
                gs.append("nomatch")
                continue
            actual = getattr(rec, NFL_STAT[stat])
            if actual is None or (isinstance(actual, float) and math.isnan(actual)):
                ys.append(np.nan)
                gs.append("noact")
                continue
            ys.append(1.0 if actual > r.line else (0.0 if actual < r.line else np.nan))
            gs.append("ok" if actual != r.line else "push")
        sub["y"] = ys
        sub["gstat"] = gs
        results[("nfl", stat)] = sub[sub["gstat"] == "ok"].copy()

    return results


def run_replication_checks(results: dict[tuple[str, str], pd.DataFrame]) -> dict:
    """Execute Replication Checks A, B, and C across all graded h2h families."""
    with open(AUDIT_JSON) as fh:
        audit_families = json.load(fh)["families"]

    check_a = {}
    check_b = {}
    check_c = {}

    for (sp, stat), sub in sorted(results.items()):
        fam = f"{sp}:{stat}"
        h2h = sub[sub["p_market"].notna()].copy()
        if len(h2h) == 0:
            continue

        # Check A: Full-window (Nov 2-24) metrics vs audit_results.json
        cur_metrics = {
            "model": metrics(h2h["p_model"], h2h["y"]) if len(h2h) >= 30 else {},
            "market": metrics(h2h["p_market"], h2h["y"]) if len(h2h) >= 30 else {},
        }
        bb = h2h[h2h["p_blend"].notna()]
        cur_metrics["blend"] = metrics(bb["p_blend"], bb["y"]) if len(bb) >= 30 else {}

        aud = audit_families.get(fam, {})
        diffs = {}
        passed = True
        compared = 0
        for kind in ("model", "market", "blend"):
            if kind in aud and aud[kind]:
                compared += 1
                if not cur_metrics.get(kind):
                    passed = False
                    diffs[kind] = {"dll": None, "dslope": None, "pass": False}
                    continue
                dll = abs(cur_metrics[kind]["logloss"] - aud[kind]["logloss"])
                dslope = abs(cur_metrics[kind]["slope"] - aud[kind]["slope"])
                ok = (dll <= 0.002) and (dslope <= 0.05)
                if not ok:
                    passed = False
                diffs[kind] = {
                    "dll": float(dll), "dslope": float(dslope),
                    "ll": cur_metrics[kind]["logloss"], "audit_ll": aud[kind]["logloss"],
                    "slope": cur_metrics[kind]["slope"], "audit_slope": aud[kind]["slope"],
                    "pass": bool(ok),
                }
        check_a[fam] = {
            "n_h2h": int(len(h2h)),
            "audit_n_h2h": aud.get("n_h2h"),
            "compared_blocks": compared,
            "diffs": diffs,
            "status": "PASS" if (compared > 0 and passed) else ("SKIP" if compared == 0 else "FAIL"),
        }

        # Check B: Recompute served blend from (p_model, p_market) vs archived p_over_blend
        if len(bb) > 0:
            c_arr = np.array([
                blend_c(sp, stat, trained=(m == "trained_v1")) for m in bb["nhl_model"]
            ], dtype=float)
            recomp = blend_vec(bb["p_model"], bb["p_market"], c_arr)
            abs_diff = np.abs(recomp - bb["p_blend"].to_numpy(dtype=float))
            check_b[fam] = {
                "n": int(len(bb)),
                "mean_abs_diff": float(np.mean(abs_diff)),
                "max_abs_diff": float(np.max(abs_diff)),
                "pct_within_0002": float(100.0 * np.mean(abs_diff <= 0.002)),
            }

            # Check C: Implied c using UNclipped archived values where |logit diff| > 0.05
            pm_raw = bb["p_model"].to_numpy(dtype=float)
            pk_raw = bb["p_market"].to_numpy(dtype=float)
            pb_raw = bb["p_blend"].to_numpy(dtype=float)
            valid = (pm_raw > 0.0) & (pm_raw < 1.0) & (pk_raw > 0.0) & (pk_raw < 1.0) & (pb_raw > 0.0) & (pb_raw < 1.0)
            if valid.sum() > 0:
                lm = np.log(pm_raw[valid] / (1.0 - pm_raw[valid]))
                lk = np.log(pk_raw[valid] / (1.0 - pk_raw[valid]))
                lb = np.log(pb_raw[valid] / (1.0 - pb_raw[valid]))
                denom = lm - lk
                mask = np.abs(denom) > 0.05
                if mask.sum() > 0:
                    imp_c = (lb[mask] - lk[mask]) / denom[mask]
                    q25, med, q75 = np.percentile(imp_c, [25.0, 50.0, 75.0])
                    check_c[fam] = {
                        "n_implied": int(mask.sum()),
                        "median": float(med),
                        "iqr": float(q75 - q25),
                        "q25": float(q25),
                        "q75": float(q75),
                    }
                else:
                    check_c[fam] = {"n_implied": 0, "median": None, "iqr": None}
            else:
                check_c[fam] = {"n_implied": 0, "median": None, "iqr": None}

    return {"A_audit_match": check_a, "B_blend_recompute": check_b, "C_implied_c": check_c}


def c_grid_fit(fit_df: pd.DataFrame) -> tuple[float, float]:
    """Grid search c in 0.00..1.00 step 0.01 minimizing fit log-loss of the blend."""
    pm = fit_df["p_model"].to_numpy(dtype=float)
    pk = fit_df["p_market"].to_numpy(dtype=float)
    y = fit_df["y"].to_numpy(dtype=float)
    best_c = 0.0
    best_ll = float("inf")
    for i in range(101):
        c = round(i * 0.01, 2)
        p = clip(blend_vec(pm, pk, c))
        ll = float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))
        if ll < best_ll:
            best_ll = ll
            best_c = c
    return float(best_c), float(best_ll)


def fit_offset_ab(fit_df: pd.DataFrame, c_fixed: float) -> tuple[float, float]:
    """Fit (a, b) by IRLS of y on [1, logit(p_mkt)] with fixed offset c*(logit(p_model)-logit(p_mkt))."""
    pm = np.clip(fit_df["p_model"].to_numpy(dtype=float), 0.01, 0.99)
    pk = np.clip(fit_df["p_market"].to_numpy(dtype=float), 0.01, 0.99)
    y = fit_df["y"].to_numpy(dtype=float)
    lk = np.log(pk / (1.0 - pk))
    lm = np.log(pm / (1.0 - pm))
    offset = c_fixed * (lm - lk)
    X = np.column_stack([np.ones(len(y)), lk])
    beta, _ = fit_irls(X, y, offset=offset)
    return float(beta[0]), float(beta[1])


def evaluate_gate(cand_m: dict, base_m: dict, d_ll: tuple[float, float, float],
                  d_br: tuple[float, float, float]) -> tuple[bool, list[str]]:
    """Evaluate holdout gate: better on LL or Brier, not sig worse on other, slope [0.75, 1.33], ECE <= base."""
    reasons = []
    ll_m, _, ll_z = d_ll
    br_m, _, br_z = d_br

    beats_one = (ll_m < 0.0) or (br_m < 0.0)
    if not beats_one:
        reasons.append("no_loss_improvement")
    if ll_m >= 0.0 and (math.isnan(ll_z) or ll_z >= 1.96):
        reasons.append(f"worse_logloss_z={ll_z:.2f}")
    if br_m >= 0.0 and (math.isnan(br_z) or br_z >= 1.96):
        reasons.append(f"worse_brier_z={br_z:.2f}")

    slope = cand_m.get("slope", float("nan"))
    if math.isnan(slope) or not (0.75 <= slope <= 1.33):
        reasons.append(f"slope={slope:.3f}")

    if cand_m["ece"] > base_m["ece"]:
        reasons.append(f"ece={cand_m['ece']:.4f}>{base_m['ece']:.4f}")

    return len(reasons) == 0, reasons


def grade_candidate(hold_df: pd.DataFrame, p_cand: np.ndarray, p_base: np.ndarray,
                    p_mkt: np.ndarray | None = None, is_no_change: bool = False) -> dict:
    """Compute holdout metrics, paired diffs vs base, and gate verdict."""
    y = hold_df["y"].to_numpy(dtype=float)
    cand_m = metrics(p_cand, y)
    base_m = metrics(p_base, y)
    mkt_m = metrics(p_mkt, y) if p_mkt is not None else None
    d_ll = paired_diff(p_cand, p_base, y, "logloss")
    d_br = paired_diff(p_cand, p_base, y, "brier")
    passed, reasons = evaluate_gate(cand_m, base_m, d_ll, d_br)
    if is_no_change:
        verdict = "NO-CHANGE"
    elif passed:
        verdict = "PASS"
    else:
        verdict = f"FAIL ({', '.join(reasons)})"
    out = {
        "metrics": {
            "candidate": cand_m,
            "archived_blend": base_m,
        },
        "paired_vs_blend": {
            "logloss": {"mean": d_ll[0], "se": d_ll[1], "z": d_ll[2]},
            "brier": {"mean": d_br[0], "se": d_br[1], "z": d_br[2]},
        },
        "gate_pass": bool(passed),
        "gate_fail_reasons": reasons,
        "verdict": verdict,
    }
    if mkt_m is not None:
        out["metrics"]["market"] = mkt_m
    return out


def fit_and_grade_props(results: dict[tuple[str, str], pd.DataFrame]) -> dict:
    """Fit and grade all target prop families (including NHL trained/other splits)."""
    specs = [
        ("nhl:sog", ("nhl", "sog"), None, "c_grid"),
        ("nba:rebounds", ("nba", "rebounds"), None, "c_grid"),
        ("nba:threes", ("nba", "threes"), None, "c_grid"),
        ("nba:blocks", ("nba", "blocks"), None, "c_grid"),
        ("nba:assists", ("nba", "assists"), None, "c_grid"),
        ("nba:points", ("nba", "points"), None, "fixed_zero_plus_ref"),
        ("nba:pr", ("nba", "pr"), None, "fixed_zero"),
        ("nba:pa", ("nba", "pa"), None, "fixed_zero"),
        ("nba:pra", ("nba", "pra"), None, "fixed_zero"),
        ("nba:ra", ("nba", "ra"), None, "fixed_zero"),
        ("nhl:assists[trained_v1]", ("nhl", "assists"), "trained_v1", "nhl_trained_dual"),
        ("nhl:assists[other]", ("nhl", "assists"), "other", "c_grid"),
        ("nhl:points[trained_v1]", ("nhl", "points"), "trained_v1", "nhl_trained_dual"),
        ("nhl:points[other]", ("nhl", "points"), "other", "c_grid"),
        ("nfl:rec_yds", ("nfl", "rec_yds"), None, "c_grid_cap_020"),
        ("nfl:receptions", ("nfl", "receptions"), None, "c_grid"),
    ]

    out = {}
    for label, (sp, stat), split_mode, mode in specs:
        sub = results.get((sp, stat))
        if sub is None:
            out[label] = {"sport": sp, "stat": stat, "fit_n": 0, "holdout_n": 0, "verdict": "INSUFFICIENT"}
            continue
        h2h = sub[sub["p_market"].notna() & sub["p_blend"].notna()].copy()
        if split_mode == "trained_v1":
            h2h = h2h[h2h["nhl_model"] == "trained_v1"].copy()
            current_c = blend_c(sp, stat, trained=True)
        elif split_mode == "other":
            h2h = h2h[h2h["nhl_model"] != "trained_v1"].copy()
            current_c = blend_c(sp, stat, trained=False)
        else:
            current_c = blend_c(sp, stat, trained=False)

        fit_df = h2h[h2h["day"] <= FIT_MAX_DAY].copy()
        hold_df = h2h[h2h["day"] >= HOLDOUT_MIN_DAY].copy()
        entry = {
            "sport": sp,
            "stat": stat,
            "split": split_mode,
            "current_c": float(current_c),
            "fit_n": int(len(fit_df)),
            "holdout_n": int(len(hold_df)),
        }
        if len(fit_df) < MIN_SPLIT_N or len(hold_df) < MIN_SPLIT_N:
            entry["verdict"] = "INSUFFICIENT"
            out[label] = entry
            continue

        pm_h = hold_df["p_model"].to_numpy(dtype=float)
        pk_h = hold_df["p_market"].to_numpy(dtype=float)
        pb_h = hold_df["p_blend"].to_numpy(dtype=float)

        if mode == "nhl_trained_dual":
            # Candidate 1: (a, b) map with c fixed at trained c; clamp applied
            a1, b1 = fit_offset_ab(fit_df, current_c)
            p_c1 = blend_vec(pm_h, pk_h, current_c, a=a1, b=b1)
            g1 = grade_candidate(hold_df, p_c1, pb_h, pk_h, is_no_change=False)
            g1["params"] = {"a": a1, "b": b1, "c": float(current_c), "clamped": True}

            # Candidate 2: c-grid with (a, b) = (0, 1)
            c2, fit_ll2 = c_grid_fit(fit_df)
            p_c2 = blend_vec(pm_h, pk_h, c2, a=0.0, b=1.0)
            no_chg2 = abs(c2 - current_c) < 1e-9
            g2 = grade_candidate(hold_df, p_c2, pb_h, pk_h, is_no_change=no_chg2)
            g2["params"] = {"a": 0.0, "b": 1.0, "c": float(c2), "fit_logloss": fit_ll2, "clamped": False}

            ll1 = g1["metrics"]["candidate"]["logloss"]
            ll2 = g2["metrics"]["candidate"]["logloss"]
            ship_key = "cand1_ab_fixed_c" if ll1 <= ll2 else "cand2_c_grid"
            ship = g1 if ll1 <= ll2 else g2

            entry["candidates"] = {
                "cand1_ab_fixed_c": g1,
                "cand2_c_grid": g2,
            }
            entry["ship_candidate"] = ship_key
            entry["fitted_params"] = ship["params"]
            entry["metrics"] = ship["metrics"]
            entry["paired_vs_blend"] = ship["paired_vs_blend"]
            entry["gate_pass"] = ship["gate_pass"]
            entry["gate_fail_reasons"] = ship["gate_fail_reasons"]
            entry["verdict"] = ship["verdict"]
        else:
            if mode == "c_grid":
                c_cand, fit_ll = c_grid_fit(fit_df)
                ref_c = c_cand
            elif mode == "c_grid_cap_020":
                c_raw, fit_ll = c_grid_fit(fit_df)
                c_cand = min(c_raw, 0.20)
                ref_c = c_raw
            elif mode == "fixed_zero_plus_ref":
                c_raw, fit_ll = c_grid_fit(fit_df)
                c_cand = 0.00
                ref_c = c_raw
            elif mode == "fixed_zero":
                c_cand = 0.00
                ref_c = None
                fit_ll = None
            else:
                raise ValueError(f"Unknown mode: {mode}")

            p_cand = blend_vec(pm_h, pk_h, c_cand, a=0.0, b=1.0)
            is_nc = abs(c_cand - current_c) < 1e-9
            g = grade_candidate(hold_df, p_cand, pb_h, pk_h, is_no_change=is_nc)
            entry["fitted_params"] = {
                "a": 0.0, "b": 1.0, "c": float(c_cand),
                "c_grid_opt_ref": ref_c, "fit_logloss": fit_ll,
            }
            entry["metrics"] = g["metrics"]
            entry["paired_vs_blend"] = g["paired_vs_blend"]
            entry["gate_pass"] = g["gate_pass"]
            entry["gate_fail_reasons"] = g["gate_fail_reasons"]
            entry["verdict"] = g["verdict"]

        out[label] = entry

    return out


def derive_mlb_family(bet_type: str) -> str:
    """Derive MLB family using audit.py's exact regex rules."""
    famname = re.sub(r"_(over|under)_[\d.]+.*$", "", bet_type, flags=re.I)
    famname = re.sub(r"_[a-z]+$", "", famname) if famname.endswith(tuple(["_betmgm"])) else famname
    return famname


def run_mlb_section() -> dict:
    """Run provisional chronological 70/30 logistic recalibration on MLB groups."""
    with open(MLB_JSON) as fh:
        raw = json.load(fh)
    picks = raw["picks"] if isinstance(raw, dict) and "picks" in raw else raw

    rows = []
    for r in picks:
        if r.get("result") not in ("win", "loss"):
            continue
        if r.get("model_prob") is None or r.get("market_prob") is None:
            continue
        bt = r["bet_type"]
        rows.append({
            "bet_type": bt,
            "fam": derive_mlb_family(bt),
            "game_date": str(r.get("game_date") or ""),
            "model_prob": float(r["model_prob"]),
            "market_prob": float(r["market_prob"]),
            "y": 1.0 if r["result"] == "win" else 0.0,
        })
    M = pd.DataFrame(rows)
    distinct_fams = {k: int(v) for k, v in sorted(M["fam"].value_counts().items())}

    print("\n=== MLB DERIVED FAMILIES (PROVISIONAL - selected population) ===")
    for fam, cnt in distinct_fams.items():
        print(f"  {fam}: {cnt}")

    groups = {
        "game_markets": M[M["fam"].str.startswith(("HOME", "AWAY", "F1H", "NRFI"))].copy(),
        "hr": M[M["fam"].str.startswith(("HOME_RUN", "HR"))].copy(),
        "pitcher_er_o25": M[
            (M["fam"] == "PITCHER_ER")
            & M["bet_type"].str.contains("OVER_2.5", na=False)
        ].copy(),
    }

    out = {"distinct_families": distinct_fams, "groups": {}, "provisional": True}
    for gname, gdf in groups.items():
        if len(gdf) == 0:
            print(f"\n[MLB] Group '{gname}' matched 0 rows. Existing derived families: {list(distinct_fams.keys())}")
            out["groups"][gname] = {
                "provisional": True,
                "note": "PROVISIONAL (selected population)",
                "n_total": 0,
                "verdict": "NO_MATCH",
                "existing_families": list(distinct_fams.keys()),
            }
            continue

        raw_bet_types = {k: int(v) for k, v in sorted(gdf["bet_type"].value_counts().items())}
        gdf = gdf.sort_values("game_date", kind="mergesort").reset_index(drop=True)
        split_idx = int(len(gdf) * 0.70)
        fit_df = gdf.iloc[:split_idx].copy()
        grade_df = gdf.iloc[split_idx:].copy()

        if len(fit_df) < MLB_MIN_SPLIT_N or len(grade_df) < MLB_MIN_SPLIT_N:
            out["groups"][gname] = {
                "provisional": True,
                "note": "PROVISIONAL (selected population)",
                "n_total": int(len(gdf)),
                "fit_n": int(len(fit_df)),
                "grade_n": int(len(grade_df)),
                "raw_bet_types": raw_bet_types,
                "verdict": "INSUFFICIENT",
            }
            continue

        p_fit = clip(fit_df["model_prob"].to_numpy(dtype=float))
        x_fit = np.log(p_fit / (1.0 - p_fit))
        X_fit = np.column_stack([np.ones(len(fit_df)), x_fit])
        beta, _ = fit_irls(X_fit, fit_df["y"].to_numpy(dtype=float))
        a_fit, b_fit = float(beta[0]), float(beta[1])

        p_grade_raw = grade_df["model_prob"].to_numpy(dtype=float)
        p_grade_clip = clip(p_grade_raw)
        x_grade = np.log(p_grade_clip / (1.0 - p_grade_clip))
        p_cand = 1.0 / (1.0 + np.exp(-np.clip(a_fit + b_fit * x_grade, -40.0, 40.0)))
        p_mkt = grade_df["market_prob"].to_numpy(dtype=float)

        g = grade_candidate(grade_df, p_cand, p_grade_raw, p_mkt, is_no_change=False)
        out["groups"][gname] = {
            "provisional": True,
            "note": "PROVISIONAL (selected population)",
            "n_total": int(len(gdf)),
            "fit_n": int(len(fit_df)),
            "grade_n": int(len(grade_df)),
            "raw_bet_types": raw_bet_types,
            "fit_date_range": [fit_df["game_date"].iloc[0], fit_df["game_date"].iloc[-1]],
            "grade_date_range": [grade_df["game_date"].iloc[0], grade_df["game_date"].iloc[-1]],
            "fitted_params": {"a": a_fit, "b": b_fit},
            "metrics": {
                "candidate": g["metrics"]["candidate"],
                "raw_model": g["metrics"]["archived_blend"],
                "market": g["metrics"]["market"],
            },
            "paired_vs_raw_model": g["paired_vs_blend"],
            "gate_pass": g["gate_pass"],
            "gate_fail_reasons": g["gate_fail_reasons"],
            "verdict": f"{g['verdict']} [PROVISIONAL]",
        }
    return out


def print_summary(rep: dict, props: dict, mlb: dict) -> None:
    """Print compact auditable summary tables to stdout."""
    print("\n=== REPLICATION CHECK A (Nov 2-24 vs audit_results.json) ===")
    for fam, r in sorted(rep["A_audit_match"].items()):
        parts = []
        for k in ("model", "market", "blend"):
            d = r["diffs"].get(k)
            if d:
                parts.append(f"{k}:dLL={d['dll']:.4f},dSl={d['dslope']:.3f}")
        print(f"  {fam:20s} n={r['n_h2h']:4d} (aud={str(r['audit_n_h2h']):>4s}) {r['status']:4s} | " + " ; ".join(parts))

    print("\n=== REPLICATION CHECK B & C (Served Blend Recompute & Implied c) ===")
    for fam in sorted(rep["B_blend_recompute"].keys()):
        b = rep["B_blend_recompute"][fam]
        c = rep["C_implied_c"].get(fam, {})
        med = f"{c['median']:+.3f}" if c.get("median") is not None else "  n/a"
        iqr = f"{c['iqr']:.3f}" if c.get("iqr") is not None else " n/a"
        print(f"  {fam:20s} n={b['n']:4d} | mad={b['mean_abs_diff']:.6f} <=0.002:{b['pct_within_0002']:6.2f}% | "
              f"imp_c(n={c.get('n_implied', 0):4d}) med={med} iqr={iqr}")

    print("\n=== PROPS REFIT / HOLDOUT GRADE SUMMARY ===")
    hdr = (f"{'family':24s} {'fitN':>5s} {'hldN':>5s} {'curC':>5s} {'cand':>16s} | "
           f"{'cLL':>7s} {'bLL':>7s} {'mLL':>7s} {'dLL(z)':>14s} {'dBr(z)':>14s} | "
           f"{'cSlp':>5s} {'cECE':>5s} {'bECE':>5s} | verdict")
    print(hdr)
    print("-" * len(hdr))
    for label, e in props.items():
        if e["verdict"] == "INSUFFICIENT":
            print(f"{label:24s} {e['fit_n']:5d} {e['holdout_n']:5d} {e.get('current_c', 0):5.3f} {'-':>16s} | INSUFFICIENT")
            continue
        fp = e["fitted_params"]
        if fp.get("a", 0.0) != 0.0 or fp.get("b", 1.0) != 1.0:
            cand_str = f"a={fp['a']:+.2f},b={fp['b']:.2f}"
        else:
            cand_str = f"c={fp['c']:.3f}"
        cm = e["metrics"]["candidate"]
        bm = e["metrics"]["archived_blend"]
        mm = e["metrics"]["market"]
        dll = e["paired_vs_blend"]["logloss"]
        dbr = e["paired_vs_blend"]["brier"]
        print(f"{label:24s} {e['fit_n']:5d} {e['holdout_n']:5d} {e['current_c']:5.3f} {cand_str:>16s} | "
              f"{cm['logloss']:7.4f} {bm['logloss']:7.4f} {mm['logloss']:7.4f} "
              f"{dll['mean']:+.4f}({dll['z']:+5.2f}) {dbr['mean']:+.4f}({dbr['z']:+5.2f}) | "
              f"{cm['slope']:5.2f} {cm['ece']:5.3f} {bm['ece']:5.3f} | {e['verdict']}")
        if "candidates" in e:
            for ck, cv in e["candidates"].items():
                cp = cv["params"]
                ccm = cv["metrics"]["candidate"]
                cdll = cv["paired_vs_blend"]["logloss"]
                cdbr = cv["paired_vs_blend"]["brier"]
                mark = "*" if ck == e["ship_candidate"] else " "
                print(f"  {mark} [{ck:16s}] a={cp['a']:+.3f} b={cp['b']:.3f} c={cp['c']:.3f} | "
                      f"LL={ccm['logloss']:.4f} dLL={cdll['mean']:+.4f}({cdll['z']:+.2f}) "
                      f"dBr={cdbr['mean']:+.4f}({cdbr['z']:+.2f}) slp={ccm['slope']:.2f} ECE={ccm['ece']:.3f} -> {cv['verdict']}")

    print("\n=== MLB PROVISIONAL SUMMARY (selected population) ===")
    for gname, ge in mlb["groups"].items():
        if ge.get("verdict") in ("NO_MATCH", "INSUFFICIENT"):
            print(f"  {gname:18s} n={ge['n_total']} -> {ge['verdict']} (PROVISIONAL)")
        else:
            fp = ge["fitted_params"]
            cm = ge["metrics"]["candidate"]
            rm = ge["metrics"]["raw_model"]
            mm = ge["metrics"]["market"]
            dll = ge["paired_vs_raw_model"]["logloss"]
            dbr = ge["paired_vs_raw_model"]["brier"]
            print(f"  {gname:18s} fit={ge['fit_n']:4d} grd={ge['grade_n']:4d} a={fp['a']:+.3f} b={fp['b']:.3f} | "
                  f"cLL={cm['logloss']:.4f} rLL={rm['logloss']:.4f} mLL={mm['logloss']:.4f} "
                  f"dLL={dll['mean']:+.4f}({dll['z']:+.2f}) dBr={dbr['mean']:+.4f}({dbr['z']:+.2f}) "
                  f"slp={cm['slope']:.2f} cECE={cm['ece']:.3f} rECE={rm['ece']:.3f} | {ge['verdict']}")
        raw_bts = ge.get("raw_bet_types", {})
        if raw_bts:
            bts_str = ", ".join(f"{k}({v})" for k, v in raw_bts.items())
            print(f"    pooled raw bet_types: {bts_str}")


def main() -> None:
    results = load_and_grade_props()
    rep = run_replication_checks(results)
    props = fit_and_grade_props(results)
    mlb = run_mlb_section()

    payload = {
        "config": {
            "day_range": [DAY_MIN, DAY_MAX],
            "fit_max_day": FIT_MAX_DAY,
            "holdout_min_day": HOLDOUT_MIN_DAY,
            "min_split_n": MIN_SPLIT_N,
        },
        "replication_checks": rep,
        "props_families": props,
        "mlb_provisional": mlb,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)

    print_summary(rep, props, mlb)
    print(f"\nWrote {OUT_JSON}")


if __name__ == "__main__":
    main()
