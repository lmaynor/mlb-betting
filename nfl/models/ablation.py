"""Paired feature-group ablation on the walk-forward harness: does adding a feature group improve OUT-OF-SAMPLE accuracy?

    python -m nfl.models.ablation --group pbp_ctx --stats rec_yds,receptions,rush_yds,pass_yds,pass_tds,anytime_td \\
        --seasons 2021-2025 --features-cache feat.parquet [--out report.md]

For each (stat, test season) it trains the SAME model twice on seasons < the test season -- once with the default
feature list, once with the default list plus the group -- and scores both on the IDENTICAL held-out rows. The
reported numbers are PAIRED differences (candidate minus baseline), with a paired standard error over rows, so a
"gain" has to survive the same-rows comparison instead of two separately noisy averages:

    d_logscore   mean per-row log score gain (positive = candidate better)   -- the proper scoring rule
    d_mae_pct    change in mean absolute error as % of baseline MAE          (negative = candidate better)
    d_brier      change in Brier score of P(over proxy line)                 (negative = candidate better)

!!! This is ACCURACY evidence only (see nfl.models.walkforward: there are no historical NFL prop lines in this repo).
A feature group that improves log score here has NOT been shown to beat a sportsbook price. It is a necessary,
not a sufficient, condition for shipping a feature.

A group is "supported" only if the pooled d_logscore is positive by more than 2 paired standard errors AND the sign
agrees in at least 4 of 5 test seasons. Anything else is reported as inconclusive -- including positive point estimates.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

import numpy as np
import pandas as pd

from nfl.models import props as P
from nfl.models import walkforward as W

logger = logging.getLogger(__name__)

GROUPS = {
    "pbp_ctx": {"include_pbp_ctx": True},
    "experimental": {"include_experimental": True},
    "all": {"include_pbp_ctx": True, "include_experimental": True},
}
MIN_SEASONS_AGREE = 4
MIN_SE_MULT = 2.0


def paired_stats(base: pd.DataFrame, cand: pd.DataFrame) -> Optional[dict]:
    """Paired comparison of two result frames from eval_season (same index = same held-out rows)."""
    idx = base.index.intersection(cand.index)
    if len(idx) < W.MIN_ROWS_FOR_TEST:
        return None
    b, c = base.loc[idx], cand.loc[idx]
    n = len(idx)
    d_ls = (c["ls_model"] - b["ls_model"]).to_numpy(dtype=float)
    ae_b = np.abs(b["y"] - b["mu_model"]).to_numpy(dtype=float)
    ae_c = np.abs(c["y"] - c["mu_model"]).to_numpy(dtype=float)
    br_b = ((b["p_model"] - b["over"]) ** 2).to_numpy(dtype=float)
    br_c = ((c["p_model"] - c["over"]) ** 2).to_numpy(dtype=float)

    def se(x):
        return float(np.std(x, ddof=1) / np.sqrt(len(x)))

    return {"n": n, "d_logscore": float(d_ls.mean()), "d_logscore_se": se(d_ls),
            "d_mae_pct": float((ae_c.mean() - ae_b.mean()) / max(ae_b.mean(), 1e-9) * 100),
            "d_mae_se_pct": float(se(ae_c - ae_b) / max(ae_b.mean(), 1e-9) * 100),
            "d_brier": float((br_c - br_b).mean()), "d_brier_se": se(br_c - br_b),
            "mae_base": float(ae_b.mean()), "mae_cand": float(ae_c.mean())}


def pool(per_season: list) -> Optional[dict]:
    """Row-weighted pooled paired estimate from per-season dicts (SEs combined as independent strata)."""
    per_season = [p for p in per_season if p]
    if not per_season:
        return None
    n = np.array([p["n"] for p in per_season], dtype=float)
    w = n / n.sum()
    out = {"n": int(n.sum())}
    for k, se_k in (("d_logscore", "d_logscore_se"), ("d_mae_pct", "d_mae_se_pct"), ("d_brier", "d_brier_se")):
        out[k] = float(np.sum(w * np.array([p[k] for p in per_season])))
        out[se_k] = float(np.sqrt(np.sum((w * np.array([p[se_k] for p in per_season])) ** 2)))
    out["seasons_better"] = int(sum(p["d_logscore"] > 0 for p in per_season))
    out["seasons"] = len(per_season)
    return out


def verdict(pooled: Optional[dict]) -> str:
    if not pooled:
        return "no data"
    z = pooled["d_logscore"] / max(pooled["d_logscore_se"], 1e-12)
    agree = pooled["seasons_better"]
    need = min(MIN_SEASONS_AGREE, pooled["seasons"])
    if z >= MIN_SE_MULT and agree >= need:
        return "SUPPORTED (accuracy)"
    if z <= -MIN_SE_MULT and (pooled["seasons"] - agree) >= need:
        return "HURTS"
    return "inconclusive"


def run_ablation(df: pd.DataFrame, stats: list, seasons: list, group: str) -> dict:
    kw = GROUPS[group]
    out = {}
    for stat in stats:
        rows = []
        for s in seasons:
            try:
                base, _ = W.eval_season(df, stat, s)
                cand, _ = W.eval_season(df, stat, s, **kw)
            except ValueError as exc:                       # too few rows (tiny season) -- skip, never fake
                logger.warning("%s %s skipped: %s", stat, s, exc)
                rows.append(None)
                continue
            rows.append(None if base is None or cand is None else paired_stats(base, cand))
            logger.info("%s %s done", stat, s)
        out[stat] = {"per_season": dict(zip(seasons, rows)), "pooled": pool(rows)}
    return out


def render(results: dict, group: str, seasons: list) -> str:
    L = [f"# Feature-group ablation: `{group}` (paired, walk-forward, accuracy only)", "",
         f"Test seasons {seasons[0]}-{seasons[-1]}; each season trained on earlier seasons only. d_logscore > 0, "
         "d_mae_pct < 0, d_brier < 0 favour the candidate. **Accuracy evidence only -- no sportsbook prices involved.**",
         "", "| stat | n | d_logscore (se) | d_mae % (se) | d_brier (se) | seasons better | verdict |",
         "|---|---:|---:|---:|---:|---:|---|"]
    for stat, r in results.items():
        p = r["pooled"]
        if not p:
            L.append(f"| {stat} | - | - | - | - | - | no data |")
            continue
        L.append(f"| {stat} | {p['n']} | {p['d_logscore']:+.4f} ({p['d_logscore_se']:.4f}) | "
                 f"{p['d_mae_pct']:+.2f} ({p['d_mae_se_pct']:.2f}) | "
                 f"{p['d_brier']:+.5f} ({p['d_brier_se']:.5f}) | {p['seasons_better']}/{p['seasons']} | "
                 f"{verdict(p)} |")
    L += ["", "## Per season (d_logscore)", "", "| stat | " + " | ".join(str(s) for s in seasons) + " |",
          "|---|" + "---:|" * len(seasons)]
    for stat, r in results.items():
        cells = []
        for s in seasons:
            ps = r["per_season"].get(s)
            cells.append("-" if not ps else f"{ps['d_logscore']:+.4f}")
        L.append(f"| {stat} | " + " | ".join(cells) + " |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--group", choices=sorted(GROUPS), default="pbp_ctx")
    ap.add_argument("--stats", default=",".join(P.DEFAULT_STATS))
    ap.add_argument("--seasons", default="2021-2025")
    ap.add_argument("--features-cache", required=True, help="parquet from nfl.models.walkforward / build_player_week_features")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    df = pd.read_parquet(args.features_cache)
    df["is_upcoming"] = df["is_upcoming"].astype(bool)
    seasons = W.parse_seasons(args.seasons)
    stats = [s.strip() for s in args.stats.split(",") if s.strip()]
    results = run_ablation(df, stats, seasons, args.group)
    report = render(results, args.group, seasons)
    print(report)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
