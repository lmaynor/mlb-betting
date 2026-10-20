"""
Empirical Gaussian-copula correlation for same-game MLB prop pairs.

Joins closing CONSENSUS Over probabilities (odds_history) with realized box-score
outcomes and fits the bivariate-probit (tetrachoric) rho per pair archetype by
maximum likelihood. Compares joint log-loss vs naive independence on a season
holdout (fit 2024-2025, test 2026). Standard errors are game-clustered bootstraps
(several pairs share a game, so a pair-level SE would be too tight).

Research script, read-only: nothing is written to GCS or the DB.

    MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data \
      .venv/bin/python scripts/sgp_empirical_rho.py --out docs/analysis/sgp_empirical_rho_2026-10-19.md
"""
from __future__ import annotations

import argparse
import itertools
import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from mlb.analysis.odds_history import read_history
from mlb_core import storage

PROP_MARKETS = {
    # market: (feature csv, id col, label col)
    "k_ou": ("K_Pro_System/data/model_features.csv", "pitcher", "starter_ks"),
    "outs_ou": ("K_Pro_System/data/model_features.csv", "pitcher", "starter_outs"),
    "bhits_ou": ("BATTER_HITS_System/data/model_features.csv", "batter", "batter_hits"),
    "btb_ou": ("BATTER_TB_System/data/model_features.csv", "batter", "batter_total_bases"),
}

# (leg A, leg B, same_player). Player/game-total pairs are same-game; same-player pairs share the id.
PAIRS = [
    ("k_ou", "game_total", False),
    ("outs_ou", "game_total", False),
    ("bhits_ou", "game_total", False),
    ("btb_ou", "game_total", False),
    ("k_ou", "outs_ou", True),
    ("bhits_ou", "btb_ou", True),
    ("k_ou", "bhits_ou", False),   # a starter's Ks vs ANY batter's hits in the same game (mixed sides)
]

LOCAL_HISTORY: str | None = None   # dir holding a local rsync of Odds/history (set by --local-history)

_GL_X, _GL_W = np.polynomial.legendre.leggauss(24)


def phi2(h: np.ndarray, k: np.ndarray, rho: float) -> np.ndarray:
    """Vectorized bivariate normal CDF P(X<=h, Y<=k; rho), |rho|<1.

    Phi2 = Phi(h)Phi(k) + int_0^rho phi2(h,k;r) dr, Gauss-Legendre on [0, rho]."""
    r = 0.5 * rho * (_GL_X + 1.0)                       # nodes on [0, rho]
    w = 0.5 * abs(rho) * _GL_W * np.sign(rho if rho != 0 else 1.0)
    hh, kk = h[:, None], k[:, None]
    dens = np.exp(-(hh * hh - 2 * r * hh * kk + kk * kk) / (2 * (1 - r * r))) / (2 * np.pi * np.sqrt(1 - r * r))
    return norm.cdf(h) * norm.cdf(k) + dens @ w


def cell_probs(pa, pb, rho):
    z_a, z_b = norm.ppf(pa), norm.ppf(pb)
    p11 = np.clip(phi2(z_a, z_b, rho), 1e-9, 1)
    p10 = np.clip(pa - p11, 1e-9, 1)
    p01 = np.clip(pb - p11, 1e-9, 1)
    p00 = np.clip(1 - pa - pb + p11, 1e-9, 1)
    return p11, p10, p01, p00


def loglik(pa, pb, ha, hb, rho):
    p11, p10, p01, p00 = cell_probs(pa, pb, rho)
    return float(np.sum(np.where(ha & hb, np.log(p11), 0) + np.where(ha & ~hb, np.log(p10), 0)
                        + np.where(~ha & hb, np.log(p01), 0) + np.where(~ha & ~hb, np.log(p00), 0)))


def fit_rho(pa, pb, ha, hb) -> float:
    """MLE rho on a grid refined by golden section (the likelihood is unimodal in rho)."""
    grid = np.linspace(-0.9, 0.9, 37)
    ll = [loglik(pa, pb, ha, hb, r) for r in grid]
    i = int(np.argmax(ll))
    lo, hi = grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]
    for _ in range(25):
        m1, m2 = lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618
        if loglik(pa, pb, ha, hb, m1) < loglik(pa, pb, ha, hb, m2):
            lo = m1
        else:
            hi = m2
    return (lo + hi) / 2


# -- data ---------------------------------------------------------------------

def _closing_main_line(market: str, since: str, until: str) -> pd.DataFrame:
    """One row per (game_pk[, player_id]): the consensus CLOSING main line (fair Over
    probability nearest 0.5) with half-point lines only (whole lines can push)."""
    if LOCAL_HISTORY:
        files = sorted(Path(LOCAL_HISTORY, f"market={market}").glob("date=*/*.parquet"))
        files = [f for f in files if since <= f.parent.name[5:] <= until]
        h = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    else:
        h = read_history(market, since=since, until=until)
    h = h[(h["book"] == "consensus") & (h["is_closing"] == True) & (h["selection"] == "OVER")]  # noqa: E712
    h = h[h["game_pk"].notna() & h["fair_prob"].notna() & h["line"].notna()]
    h = h[(h["line"] * 2) % 2 == 1]
    h = h.copy()
    h["game_pk"] = h["game_pk"].astype("int64")
    pid = market != "game_total"
    if pid:
        h = h[h["player_id"].notna()]
        h["player_id"] = h["player_id"].astype("int64")
    keys = ["game_pk"] + (["player_id"] if pid else [])
    h["_d"] = (h["fair_prob"] - 0.5).abs()
    h = h.sort_values(["_d", "snapshot_ts"], ascending=[True, False]).drop_duplicates(keys)
    cols = keys + ["game_date", "line", "fair_prob"]
    out = h[cols].rename(columns={"fair_prob": "p"})
    out["p"] = pd.to_numeric(out["p"], errors="coerce").astype(float)
    out["line"] = pd.to_numeric(out["line"], errors="coerce").astype(float)
    out["game_date"] = out["game_date"].astype(str)
    return out.dropna(subset=["p", "line"])


def _outcomes() -> dict[str, pd.DataFrame]:
    out = {}
    for mkt, (csv, idc, lab) in PROP_MARKETS.items():
        f = storage.read_csv(csv)
        f = f[["game_pk", idc, lab]].dropna().rename(columns={idc: "player_id", lab: "actual"})
        f["game_pk"] = f["game_pk"].astype("int64")
        f["player_id"] = f["player_id"].astype("int64")
        out[mkt] = f.drop_duplicates(["game_pk", "player_id"])
    sc = storage.read_csv("Scoring/scoring_master.csv")
    tot = sc.groupby("game_pk")["runs"].sum().rename("actual").reset_index()
    tot["game_pk"] = tot["game_pk"].astype("int64")
    out["game_total"] = tot
    return out


def load_legs(since: str, until: str, cache: Path) -> dict[str, pd.DataFrame]:
    cache.mkdir(parents=True, exist_ok=True)
    outs = _outcomes()
    legs = {}
    for mkt in [*PROP_MARKETS, "game_total"]:
        cp = cache / f"{mkt}_{since}_{until}.parquet"
        if cp.exists():
            q = pd.read_parquet(cp)
        else:
            q = _closing_main_line(mkt, since, until)
            q.to_parquet(cp)
        keys = ["game_pk"] + (["player_id"] if mkt != "game_total" else [])
        q = q.merge(outs[mkt], on=keys, how="inner")
        q = q[q["actual"] != q["line"]]                        # belt and braces: no pushes
        q["hit"] = (q["actual"] > q["line"]).astype(int)
        legs[mkt] = q
        print(f"[legs] {mkt}: {len(q)} settled closing legs")
    return legs


def pair_frame(legs, a, b, same_player) -> pd.DataFrame:
    A = legs[a].rename(columns={"p": "p_a", "hit": "hit_a", "line": "line_a"})
    A = A[[c for c in ["game_pk", "player_id", "game_date", "line_a", "p_a", "hit_a"] if c in A.columns]]
    B = legs[b].rename(columns={"p": "p_b", "hit": "hit_b", "line": "line_b"})
    if b == "game_total":
        B = B[["game_pk", "line_b", "p_b", "hit_b"]]
        m = A.merge(B, on="game_pk")
    elif same_player:
        B = B[["game_pk", "player_id", "line_b", "p_b", "hit_b"]]
        m = A.merge(B, on=["game_pk", "player_id"])
    else:
        B = B[["game_pk", "line_b", "p_b", "hit_b"]]
        m = A.merge(B, on="game_pk")
    m = m[(m["p_a"].between(0.02, 0.98)) & (m["p_b"].between(0.02, 0.98))]
    return m.reset_index(drop=True)


# -- analysis -----------------------------------------------------------------

def arr(m):
    return (m["p_a"].to_numpy(float), m["p_b"].to_numpy(float),
            m["hit_a"].to_numpy(bool), m["hit_b"].to_numpy(bool))


def cluster_boot(m: pd.DataFrame, n_boot: int, seed: int = 7) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    games = m["game_pk"].unique()
    idx = {g: np.flatnonzero(m["game_pk"].to_numpy() == g) for g in games}
    pa, pb, ha, hb = arr(m)
    est = []
    for _ in range(n_boot):
        pick = rng.choice(games, size=len(games), replace=True)
        ii = np.concatenate([idx[g] for g in pick])
        est.append(fit_rho(pa[ii], pb[ii], ha[ii], hb[ii]))
    return float(np.percentile(est, 2.5)), float(np.percentile(est, 97.5))


def joint_logloss(m, rho):
    pa, pb, ha, hb = arr(m)
    return -loglik(pa, pb, ha, hb, rho) / len(m)


def analyze(name, m, n_boot, split="2026-01-01"):
    tr, te = m[m["game_date"] < split], m[m["game_date"] >= split]
    row = {"pair": name, "n": len(m), "n_games": m["game_pk"].nunique(), "n_train": len(tr), "n_test": len(te)}
    if len(tr) < 300 or len(te) < 100:
        return row | {"note": "too few pairs"}
    pa, pb, ha, hb = arr(m)
    row["rho_all"] = fit_rho(pa, pb, ha, hb)
    if abs(row["rho_all"]) > 0.89:
        row["note"] = "rho at grid bound: events are nested/deterministic, Gaussian copula not meaningful"
    row["rho_all_lo"], row["rho_all_hi"] = cluster_boot(m, n_boot)
    row["rho_train"] = fit_rho(*arr(tr))
    row["ll_indep_test"] = joint_logloss(te, 0.0)
    row["ll_copula_test"] = joint_logloss(te, row["rho_train"])
    row["ll_lift_test"] = row["ll_indep_test"] - row["ll_copula_test"]
    row["emp_joint_over"] = float((te["hit_a"] & te["hit_b"]).mean())
    row["indep_joint_over"] = float((te["p_a"] * te["p_b"]).mean())
    row["copula_joint_over"] = float(phi2(norm.ppf(te["p_a"].to_numpy()), norm.ppf(te["p_b"].to_numpy()),
                                          row["rho_train"]).mean())
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2024-04-01")
    ap.add_argument("--until", default="2026-10-31")
    ap.add_argument("--boot", type=int, default=150)
    ap.add_argument("--out", default=None)
    ap.add_argument("--cache", default=os.environ.get("SGP_CACHE", "/tmp/sgp_cache"))
    ap.add_argument("--local-history", default=None,
                    help="local rsync of gs://.../Odds/history (much faster than per-partition GCS reads)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        from scipy.stats import multivariate_normal as mvn
        for h, k, r in [(0.3, -0.2, 0.4), (-1.1, 0.8, -0.6), (0.0, 0.0, 0.9), (1.5, 1.5, -0.3)]:
            ref = mvn.cdf([h, k], mean=[0, 0], cov=[[1, r], [r, 1]])
            got = float(phi2(np.array([h]), np.array([k]), r)[0])
            assert abs(ref - got) < 1e-5, (h, k, r, ref, got)
        print("phi2 matches scipy")
        # parameter recovery on simulated data
        rng = np.random.default_rng(0)
        n = 20000
        z = rng.multivariate_normal([0, 0], [[1, 0.35], [0.35, 1]], size=n)
        pa, pb = rng.uniform(0.3, 0.7, n), rng.uniform(0.3, 0.7, n)
        ha, hb = z[:, 0] < norm.ppf(pa), z[:, 1] < norm.ppf(pb)
        est = fit_rho(pa, pb, ha, hb)
        assert abs(est - 0.35) < 0.05, est
        print(f"recovery ok: rho_hat={est:.3f} (true 0.35)")
        return 0

    global LOCAL_HISTORY
    LOCAL_HISTORY = a.local_history
    legs = load_legs(a.since, a.until, Path(a.cache))
    rows = []
    for ma, mb, same in PAIRS:
        name = f"{ma} x {mb}" + (" (same player)" if same else "")
        m = pair_frame(legs, ma, mb, same)
        print(f"[pair] {name}: {len(m)} pairs")
        rows.append(analyze(name, m, a.boot))
        if mb == "game_total" and len(m) > 1000:
            # game-script regime: totals sit on a few half-point lines, so cut on fixed lines
            # (terciles collapse into empty buckets)
            for lab, sub in [("total <=7.5", m[m["line_b"] <= 7.5]),
                             ("total 8-8.5", m[(m["line_b"] > 7.5) & (m["line_b"] < 9)]),
                             ("total >=9", m[m["line_b"] >= 9])]:
                rows.append(analyze(f"  {name} | {lab}", sub, max(a.boot // 3, 30)))
    res = pd.DataFrame(rows)
    pd.set_option("display.width", 250, "display.max_columns", 30)
    print(res.round(4).to_string())
    if a.out:
        Path(a.out).write_text(render(res, a))
        print("wrote", a.out)
    return 0


def _md_table(t: pd.DataFrame) -> str:
    cells = t.astype(object).where(t.notna(), "").astype(str)
    head = "| " + " | ".join(cells.columns) + " |"
    sep = "|" + "|".join("---" for _ in cells.columns) + "|"
    body = ["| " + " | ".join(r) + " |" for r in cells.itertuples(index=False)]
    return "\n".join([head, sep, *body])


def render(res: pd.DataFrame, a) -> str:
    t = res.copy()
    cols = ["pair", "n", "n_games", "rho_all", "rho_all_lo", "rho_all_hi", "rho_train",
            "ll_lift_test", "emp_joint_over", "indep_joint_over", "copula_joint_over"]
    t = t[[c for c in cols if c in t.columns]].round(4)
    md = ["# Empirical SGP correlation (MLB, closing consensus), generated by scripts/sgp_empirical_rho.py", "",
          f"Window {a.since}..{a.until}; train < 2026-01-01, test >= 2026-01-01. Closing consensus MAIN line, "
          "half-point lines only. rho = Gaussian-copula MLE on Over/Over; 95% CI = game-clustered bootstrap.", "",
          _md_table(t), ""]
    return "\n".join(md)


if __name__ == "__main__":
    raise SystemExit(main())
