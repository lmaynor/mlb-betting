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
That run used the STRICT pregame flag and `players_master.csv` names (current rosters only). Re-run (2026-10-16, see the follow-up
section of that note) with the 10-minute grace window and full-history names:

    python scripts/nhl_sog_market_eval.py --bp-dir <dir with shots/*.parquet> --masters-dir <dir with NHL masters> \
        --names-from-masters --slack-min 10 --json out.json

`--bp-dir` builds the consensus from local `Odds/bettingpros_nhl/shots/{date}.parquet` copies instead of `--consensus`; the
per-season table (coverage, blend c, log-loss, blind Under ROI, 5pp disagreement ROI at best price) is the one to read.
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
    if "p_model" in m.columns:
        from mlb_core.odds import model_vs_market as MVM
        bt = m.rename(columns={"won_over": "over", "cons_fair": "p_market"}).dropna(subset=["p_model", "p_market"])
        print("\n" + MVM.render_price_test("NHL SOG market-anchored blend (walk-forward by season)", MVM.blend_price_test(bt)))
    yr = m.game_date.astype(str).str[:4]
    for y, g in m.groupby(yr):
        u = np.where(g.won_over == 0, _profit_per_unit(g.best_under_odds), -1.0)
        print(f"  {y}: n={len(g):6} under ROI={u.mean():+.4f} over_rate={g.won_over.mean():.4f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--consensus", help="parquet from scripts/join_bettingpros_history.py --sport nhl --markets sog")
    ap.add_argument("--bp-dir", help="alternative to --consensus: local dir holding shots/{YYYY-MM-DD}.parquet partitions")
    ap.add_argument("--slack-min", type=float, default=None,
                    help="pregame grace window in minutes (default: the loader's strict flag; use 10)")
    ap.add_argument("--names-from-masters", action="store_true",
                    help="resolve names via every skater who ever dressed (skater_boxscores_master.csv) instead of players_master.csv")
    ap.add_argument("--json", help="write the per-season table + coverage here")
    ap.add_argument("--masters-dir", required=True, help="local dir with games_master.csv, skater_boxscores_master.csv, "
                                                          "players_master.csv, shots_*.csv")
    ap.add_argument("--min-books", type=int, default=3)
    a = ap.parse_args(argv)
    if bool(a.consensus) == bool(a.bp_dir):
        ap.error("give exactly one of --consensus / --bp-dir")
    if a.consensus:
        cons = pd.read_parquet(a.consensus)
    else:
        from nhl.props import bp_eval
        cons = bp_eval.build_consensus(bp_eval.load_bp_long(a.bp_dir, "shots"), "sog", min_books=a.min_books)
    gl = study.load_history_dir(a.masters_dir)
    if a.names_from_masters:
        from nhl.props import bp_eval
        sk = pd.read_csv(os.path.join(a.masters_dir, "skater_boxscores_master.csv"), usecols=["player_id", "name", "team_abbr"],
                         low_memory=False)
        names = bp_eval.name_table({"skaters": sk, "goalies": sk.iloc[0:0]})
    else:
        pm = pd.read_csv(os.path.join(a.masters_dir, "players_master.csv"))
        names = pm[["player_id", "full_name", "team"]]
    print(f"consensus rows={len(cons)} gamelog rows={len(gl)} names={len(names)} slack_min={a.slack_min}", flush=True)
    r = study.bettingpros_model_vs_market(cons, gl, names, min_books=a.min_books, allow_compat=False, slack_min=a.slack_min)
    print(json.dumps({"coverage": r["coverage"], "resolve_how": r["resolve_how"], "scores": r["scores"]}, indent=1, default=str))
    from nhl.props import bp_eval
    seasons = bp_eval.score_by_season(r["frame"])
    print("\nPer season (c = blend coefficient; ROI5 = flat 1u on the side the model disagrees with by >= 5pp at the best price):")
    for row in seasons:
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()})
    ladder = bp_eval.score(r["frame"], "sog") if len(r["frame"]) else {}
    print("\nDisagreement-bet ladder (gap = model - consensus P(over) in probability points; price = best book / Novig):")
    for x in ladder.get("roi", []):
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in x.items()})
    fr = r["frame"]
    if len(fr) and {"best_over", "best_under", "p_model", "cons_fair", "won_over"} <= set(fr.columns):
        from mlb_core.odds import model_vs_market as MVM
        bt = fr.rename(columns={"won_over": "over", "cons_fair": "p_market", "best_over": "best_over_odds",
                                "best_under": "best_under_odds"})
        print("\n" + MVM.render_price_test("NHL SOG market-anchored blend (walk-forward by season)", MVM.blend_price_test(bt)))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"coverage": r["coverage"], "resolve_how": r["resolve_how"], "scores": r["scores"], "by_season": seasons,
                       "roi_ladder": ladder.get("roi", []), "slack_min": a.slack_min}, fh, indent=1, default=str)
    if a.consensus and a.slack_min is None:
        price_baseline(cons, r["frame"])                  # the original unique-join price check (strict-flag legacy path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
