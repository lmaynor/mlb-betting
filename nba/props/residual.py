"""NBA player-prop MARKET-RESIDUAL model: table builder + CLI. UNVALIDATED, research only, NOT wired into the board or the Official tier.

    # 1. price every historical consensus prop with the SERVING engine as of the game (existing study script, ~4 min on 8 workers)
    python -m nba.props.residual build --bp-dir <Odds/bettingpros_nba copy> --master <player_boxscores_master.csv> --work-dir W
    # 2. development folds only (the holdout season is NEVER touched without --report-holdout)
    python -m nba.props.residual run --table W/residual_table.parquet --out-md dev.md
    # 3. the held-out latest season, once
    python -m nba.props.residual run --table W/residual_table.parquet --report-holdout --out-md final.md --out-json final.json

The engine column is `ship_noabs` (the shipped hierarchical engine with NO absence information). The `ship` / `old` oracle
variants of scripts.nba_engine_vs_market are fed the box score's own list of absentees (a leak of the game being priced) and are
never used here. The market quote is the last snapshot of the day (after injury news), so `ship_noabs` is under-informed relative
to the market: a residual model can partly learn "the market knows who is out", which is a real but un-actionable gap.

Rows: the five BettingPros NBA markets (points, rebounds, assists, threes, PRA), seasons 2021..2025 (start year), >= 3 two-sided
reference books, one modal line per (event, player, stat), void (DNP) props removed. See scripts/nba_engine_vs_market.py for the
identity join and mlb_core/odds/props_residual.py for the model and the protocol.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os

import numpy as np
import pandas as pd

from mlb_core.data import cockpit_form as cf
from mlb_core.odds import bettingpros_props as bpp
from mlb_core.odds import props_residual as R

log = logging.getLogger("nba.props.residual")

ENGINE_VARIANT = "ship_noabs"
HOLDOUT_SEASON = 2025                     # 2025-26, the latest full season in the price history
STAT_COLS = {"points": ("points",), "rebounds": ("rebounds",), "assists": ("assists",), "threes": ("three_points_made",),
             "pra": ("points", "rebounds", "assists")}
BP_STAT = {"pts": "points", "reb": "rebounds", "ast": "assists", "threes": "threes", "pra": "pra"}


def master_games(master: pd.DataFrame) -> pd.DataFrame:
    """Box-score master -> one row per PLAYED non-preseason game with the five stat values and minutes (engine_eval.load_master's
    filters). Columns: pid, date, season, minutes, points, rebounds, assists, threes, pra."""
    df = master[~master["season_type"].astype(str).str.lower().eq("preseason")]
    df = df[df["played"].astype(str).str.lower().isin(["true", "1", "1.0"])].copy()
    df["minutes"] = df["minutes"].map(cf._minutes)
    df = df[df["minutes"].fillna(0) > 0]
    out = pd.DataFrame({"pid": df["player_id"].to_numpy(), "date": df["date"].astype(str).str[:10].to_numpy(),
                        "season": df["season_year"].astype(int).to_numpy(), "minutes": df["minutes"].astype(float).to_numpy()})
    for stat, cols in STAT_COLS.items():
        out[stat] = sum(pd.to_numeric(df[c], errors="coerce").to_numpy() for c in cols)
    return out.dropna(subset=["pid"]).reset_index(drop=True)


def build_extras(bp_dir: str, cons: pd.DataFrame) -> pd.DataFrame:
    """Median prices, opening line / price and sharp-book fair probabilities for every consensus row (one market at a time)."""
    parts = []
    for bp_stat, slug in {k: v for k, v in bpp.SPORTS["NBA"].items() if k in BP_STAT}.items():
        files = sorted(glob.glob(os.path.join(bp_dir, slug, "*.parquet")))
        c = cons[cons["stat"] == bp_stat]            # the loader labels rows with the BettingPros label (pts, reb, ...)
        if not files or not len(c):
            continue
        long_df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True).drop_duplicates(subset=bpp.DEDUP_KEY, keep="last")
        ex = R.market_extras(long_df, c.assign(player_id=c["player_id"]))
        parts.append(ex.assign(stat=BP_STAT[bp_stat]))
        log.info("extras %s: %d rows", bp_stat, len(ex))
    return pd.concat(parts, ignore_index=True)


def build_table(priced: pd.DataFrame, extras: pd.DataFrame, games: pd.DataFrame, variant: str = ENGINE_VARIANT) -> pd.DataFrame:
    """Priced props (scripts.nba_engine_vs_market `priced.parquet`) + market extras + player form -> the residual model's table.
    `priced` is read as is: its consensus columns are the loader's, its engine columns are as of the game."""
    p = priced.dropna(subset=[f"p_{variant}", f"mu_{variant}"]).copy()
    p["event_id"] = p["event_id"].astype("Int64")
    p["player_id"] = pd.to_numeric(p["player_id"]).astype("Int64")
    ex = extras.assign(event_id=extras["event_id"].astype("Int64"), player_id=pd.to_numeric(extras["player_id"]).astype("Int64"))
    t = p.merge(ex, on=["event_id", "player_id", "stat", "line"], how="left")
    t["pid"] = t["master_pid"]
    base = pd.DataFrame({
        "sport": "nba", "game_id": t["event_id"].astype(str), "player": t["player"], "pid": t["pid"], "date": t["date"],
        "game_date": t["game_date"], "season": t["season"].astype(int), "stat": t["stat"], "line": t["line"].astype(float),
        "y": t["y"], "over": t["over"], "push": t["push"], "p_mkt": t["p_over_cons"], "n_books": t["n_books"], "disp": t["disp"],
        "best_over_odds": t["best_over_odds"], "best_under_odds": t["best_under_odds"], "med_over_odds": t["med_over_odds"],
        "med_under_odds": t["med_under_odds"], "open_line": t["open_line"], "open_p": t["open_p"],
        "p_novig": t["p_novig"], "p_prophetx": t["p_prophetx"], "p_pinnacle": t["p_pinnacle"],
        "eng_mu": t[f"mu_{variant}"], "eng_p": t[f"p_{variant}"], "eng0_p": t["p_old_noabs"] if "p_old_noabs" in t else np.nan,
    })
    parts = []
    for stat, h in base.groupby("stat"):
        gv = games.assign(value=games[stat])
        hist = R.history_features(gv, h[["pid", "date", "season"]])
        gm = games.assign(value=games["minutes"])
        mh = R.history_features(gm, h[["pid", "date", "season"]], prefix="min_")
        parts.append(pd.concat([h, hist, mh[["min_m_l5", "min_m_season"]].rename(columns={"min_m_l5": "min_l5", "min_m_season": "min_season"})], axis=1))
    return pd.concat(parts).sort_index()


def build(args) -> int:
    """Run the price pipeline (scripts.nba_engine_vs_market) when `priced.parquet` is absent, then write residual_table.parquet."""
    from scripts import nba_engine_vs_market as S
    os.makedirs(args.work_dir, exist_ok=True)
    cons = S.load_consensus(args.bp_dir, os.path.join(args.work_dir, "cons.parquet"))
    cache = os.path.join(args.work_dir, "priced.parquet")
    if not os.path.exists(cache):
        index, dnp = S.load_master(args.master)
        props, cov = S.join_props(cons, index, dnp, None)
        log.info("joined %d of %d consensus rows", cov["matched"], cov["consensus_rows"])
        priced, _ = S.replay_props(props, index, dnp, args.workers, 1)
        priced.to_parquet(cache, index=False)
    priced = pd.read_parquet(cache)
    ex_path = os.path.join(args.work_dir, "residual_extras.parquet")
    if os.path.exists(ex_path):
        extras = pd.read_parquet(ex_path)
    else:
        extras = build_extras(args.bp_dir, cons)
        extras.to_parquet(ex_path, index=False)
    games = master_games(pd.read_csv(args.master, low_memory=False))
    table = build_table(priced, extras, games)
    table.to_parquet(os.path.join(args.work_dir, "residual_table.parquet"), index=False)
    log.info("table: %d rows, seasons %s", len(table), sorted(table["season"].unique()))
    return 0


def run(args) -> int:
    table = pd.read_parquet(args.table)
    res, preds = R.study(table, holdout=args.holdout, report_holdout=args.report_holdout, nthread=args.nthread, n_boot=args.boot, log=log.info)
    if args.out_json:
        with open(args.out_json, "w") as fh:
            json.dump(res, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    if args.out_preds:
        preds.to_parquet(args.out_preds)
    md = R.render_study("NBA", res)
    if args.out_md:
        with open(args.out_md, "w") as fh:
            fh.write(md)
    print(md)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="price the props and write the residual table")
    b.add_argument("--bp-dir", required=True)
    b.add_argument("--master", required=True)
    b.add_argument("--work-dir", required=True)
    b.add_argument("--workers", type=int, default=8)
    r = sub.add_parser("run", help="walk-forward evaluation")
    r.add_argument("--table", required=True)
    r.add_argument("--holdout", type=int, default=HOLDOUT_SEASON)
    r.add_argument("--report-holdout", action="store_true", help="ALSO score the held-out season (once, at the end)")
    r.add_argument("--nthread", type=int, default=4)
    r.add_argument("--boot", type=int, default=600)
    r.add_argument("--out-md")
    r.add_argument("--out-json")
    r.add_argument("--out-preds")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return build(a) if a.cmd == "build" else run(a)


if __name__ == "__main__":
    raise SystemExit(main())
