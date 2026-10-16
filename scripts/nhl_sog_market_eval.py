"""NHL shots-on-goal model vs historical BettingPros consensus, plus an always-Under / always-Over price baseline. READ-ONLY.

    # 1) consensus table (slow: serial GCS reads; NHL sog ~1 h)
    GCS_BUCKET=<bucket> python scripts/join_bettingpros_history.py --sport nhl --markets sog --out /tmp/nhl_sog_cons.parquet
    # 2) local copies of our own NHL masters
    mkdir -p /tmp/nhl_masters && gcloud storage cp gs://<bucket>/NHL/{games_master,skater_boxscores_master,players_master}.csv \\
        "gs://<bucket>/NHL/shots/shots_*.csv" /tmp/nhl_masters/
    # 3) run
    python scripts/nhl_sog_market_eval.py --consensus /tmp/nhl_sog_cons.parquet --masters-dir /tmp/nhl_masters

Reports nhl.props.study.bettingpros_model_vs_market (coverage, Brier/log-loss vs market, blend regression), then joins the
scored rows back to the consensus table's best over/under prices and prices a flat bet on every Under / Over at the best book.
The price join key is (event_id, line, consensus probability) because the scored frame carries no BettingPros player id; only
keys that are unique on BOTH sides are kept (the printed `unique-join` count vs the scored count shows the loss).

FINDINGS (2026-10-15, docs/analysis/bettingpros_model_vs_market_2026-10-15.md): model ~ market; always-Under at best price is
-0.08% ROI (95% CI -1.2% .. +1.1%), yearly results flip sign. Not an edge claim; CLV is the go/no-go and BettingPros keeps one
snapshot per day, so there is no closing-line series.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nhl.props import study  # noqa: E402


def _profit_per_unit(american) -> np.ndarray:
    o = np.asarray(american, float)
    return np.where(o > 0, o / 100.0, 100.0 / np.abs(o))


def _cluster_boot_roi(profit: np.ndarray, groups: pd.Series, n: int = 400, seed: int = 1) -> tuple:
    """95% CI of mean profit per bet, resampling whole game dates."""
    tot = pd.Series(profit).groupby(groups.values).sum().to_numpy()
    cnt = pd.Series(1, index=range(len(profit))).groupby(groups.values).sum().to_numpy()
    rng = np.random.default_rng(seed)
    idx = np.arange(len(tot))
    out = [tot[s].sum() / cnt[s].sum() for s in (rng.choice(idx, len(idx)) for _ in range(n))]
    return tuple(np.percentile(out, [2.5, 97.5]))


def price_baseline(cons: pd.DataFrame, frame: pd.DataFrame) -> None:
    c, f = cons.copy(), frame.copy()
    c["k"] = c.p_over_cons.round(6)
    f["k"] = f.cons_fair.round(6)
    cu = c.drop_duplicates(["event_id", "line", "k"], keep=False)
    fu = f.drop_duplicates(["event_id", "line", "k"], keep=False)
    m = fu.merge(cu[["event_id", "line", "k", "best_over_odds", "best_under_odds"]], on=["event_id", "line", "k"])
    m = m.dropna(subset=["best_over_odds", "best_under_odds", "won_over"]).reset_index(drop=True)
    m["won_over"] = m.won_over.astype(float)
    print(f"\nscored rows={len(f)} unique-join={len(m)}")
    ru = np.where(m.won_over == 0, _profit_per_unit(m.best_under_odds), -1.0)
    ro = np.where(m.won_over == 1, _profit_per_unit(m.best_over_odds), -1.0)
    print(f"over_rate={m.won_over.mean():.4f}  mean market fair p_over={m.cons_fair.mean():.4f}")
    for name, r in (("UNDER", ru), ("OVER", ro)):
        lo, hi = _cluster_boot_roi(r, m.game_date)
        print(f"always {name:5} @ best price: ROI={r.mean():+.4f}  95% CI (date-cluster bootstrap) [{lo:+.4f}, {hi:+.4f}]")
    yr = m.game_date.astype(str).str[:4]
    for y, g in m.groupby(yr):
        u = np.where(g.won_over == 0, _profit_per_unit(g.best_under_odds), -1.0)
        print(f"  {y}: n={len(g):6} under ROI={u.mean():+.4f} over_rate={g.won_over.mean():.4f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--consensus", required=True, help="parquet from scripts/join_bettingpros_history.py --sport nhl --markets sog")
    ap.add_argument("--masters-dir", required=True, help="local dir with games_master.csv, skater_boxscores_master.csv, "
                                                          "players_master.csv, shots_*.csv")
    ap.add_argument("--min-books", type=int, default=3)
    a = ap.parse_args(argv)
    cons = pd.read_parquet(a.consensus)
    gl = study.load_history_dir(a.masters_dir)
    pm = pd.read_csv(os.path.join(a.masters_dir, "players_master.csv"))
    names = pm[["player_id", "full_name", "team"]]
    print(f"consensus rows={len(cons)} gamelog rows={len(gl)} names={len(names)}", flush=True)
    r = study.bettingpros_model_vs_market(cons, gl, names, min_books=a.min_books, allow_compat=False)
    print(json.dumps({"coverage": r["coverage"], "resolve_how": r["resolve_how"], "scores": r["scores"]}, indent=1, default=str))
    price_baseline(cons, r["frame"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
