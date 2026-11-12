"""NFL player-prop MARKET-RESIDUAL model: table builder + CLI. UNVALIDATED, research only, NOT wired into the board or the Official tier.

    # data: nflverse mirror through mlb_core.storage (MLB_BASE_DATA=<local copy> or the bucket) + a local copy of Odds/bettingpros_nfl
    python -m nfl.props.residual build --bp-dir <Odds/bettingpros_nfl copy> --work-dir W [--seasons 2021-2026]
    python -m nfl.props.residual run --table W/residual_table.parquet --out-md dev.md                       # development folds only
    python -m nfl.props.residual run --table W/residual_table.parquet --report-holdout --out-md final.md    # the held-out season, once
    python -m nfl.props.residual build --with-kneel ...   # also attach the N1 QB kneel feature columns (nfl.props.kneel)
    python -m nfl.props.residual run --table W/residual_table.parquet --report-holdout --kneel-ablation --out-md final.md
        # on a kneel-attached table: ALSO report the N1 with/without ablation + Argon verdict for the holdout season

The engine column is the hierarchical NFL engine (`nfl.models.hierarchical_live`, no pbp / injury context: the replay in
nfl.props.engine_eval.replay_hierarchical) priced at the BOOK's line as of the game (history = games strictly before the week).
Markets: pass attempts / completions / yards / TDs / interceptions, rush attempts / yards, receptions, receiving yards (anytime
TD has no two-sided price; rush+rec yards only exists from 2025). Rows are the >= 3 two-sided reference-book consensus at the
modal line, identity fail-closed through nfl.props.resolve (about 30% of consensus rows do not resolve: the same loss as the
2026-10 hierarchical-vs-market study). Pushes (integer lines) are removed from every score.
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
from mlb_core.odds import bettingpros_loader as L
from mlb_core.odds import bettingpros_props as bpp
from mlb_core.odds import model_vs_market as MVM
from mlb_core.odds import props_residual as R

log = logging.getLogger("nfl.props.residual")

HOLDOUT_SEASON = 2025
POST_SEASONS = (2026,)                    # the partial season after the holdout (weeks 1-5 at the time of writing)
LABEL_OF = {"pass_att": "pass_attempts", "pass_cmp": "pass_completions", "pass_int": "interceptions", "pass_yds": "pass_yds",
            "pass_tds": "pass_tds", "rec_yds": "rec_yds", "receptions": "receptions", "rush_att": "rush_attempts",
            "rush_yds": "rush_yds"}      # BettingPros label -> engine stat (same map as nfl.props.engine_eval.MARKET_STAT)


def parse_seasons(spec: str) -> list:
    out = []
    for part in spec.split(","):
        lo, _, hi = part.strip().partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return sorted(set(out))


def load_consensus(bp_dir: str, cache: str | None = None) -> tuple:
    """-> (consensus frame with the BettingPros label in `stat`, {label: long table}) for the markets the engine models."""
    longs, parts = {}, []
    for label, slug in bpp.SPORTS["NFL"].items():
        if label not in LABEL_OF:
            continue
        files = sorted(glob.glob(os.path.join(bp_dir, slug, "*.parquet")))
        if not files:
            continue
        ld = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True).drop_duplicates(subset=bpp.DEDUP_KEY, keep="last")
        c = L.consensus_from_long(ld, min_books=3, stat_map={slug: label}, sport="NFL", grace_minutes=L.DEFAULT_GRACE_MINUTES["NFL"])
        log.info("consensus %s: %d quote rows -> %d props", label, len(ld), len(c))
        longs[label] = ld
        parts.append(c)
    return pd.concat(parts, ignore_index=True), longs


def market_tables(cons: pd.DataFrame, longs: dict, resolver) -> tuple:
    """Screen + identity-resolve the consensus exactly as nfl.props.engine_eval.build_market_table does, and keep every row's
    market extras. Returns ({(gsis_id, game_date, stat): {line, p_market, n_books}} for the replay, DataFrame of the kept rows)."""
    from nfl.props import history_join as HJ
    keep, n_in, dropped = MVM.screen_consensus(cons, LABEL_OF, 3)
    dropped = dict(dropped, unresolved=0, ambiguous=0, duplicate=0)
    ids, bad = HJ._resolve_rows(keep, resolver)
    dropped["unresolved"], dropped["ambiguous"] = bad["unresolved"], bad["ambiguous"]
    keep = keep.assign(gsis_id=ids)
    keep = keep[keep["gsis_id"].notna()]
    dup = keep.duplicated(["gsis_id", "game_date", "model_stat"], keep=False)
    dropped["duplicate"] = int(dup.sum())
    keep = keep[~dup].reset_index(drop=True)
    ex = []
    for label, ld in longs.items():
        k = keep[keep["stat"] == label]
        if len(k):
            ex.append(R.market_extras(ld, k).assign(stat=label))
    ex = pd.concat(ex, ignore_index=True)
    keep = keep.assign(event_id=keep["event_id"].astype("Int64"), player_id=pd.to_numeric(keep["player_id"]).astype("Int64"))
    keep = keep.merge(ex.assign(event_id=ex["event_id"].astype("Int64"), player_id=ex["player_id"].astype("Int64")),
                      on=["event_id", "player_id", "line", "stat"], how="left")
    market = {(r["gsis_id"], str(r["game_date"])[:10], r["model_stat"]): {"line": float(r["line"]), "p_market": float(r["p_over_cons"]),
                                                                           "n_books": int(r["n_books"])} for r in keep.to_dict("records")}
    cov = {"consensus_rows": n_in, "matched_identity": len(market), **dropped}
    return market, keep, cov


def stat_values(df: pd.DataFrame, schedules: pd.DataFrame) -> pd.DataFrame:
    """Player logs -> one row per player-game with the date (nflverse gameday) and every market stat in the line's unit."""
    gd = dict(zip(schedules["game_id"], schedules["gameday"].astype(str).str[:10]))
    out = pd.DataFrame({"pid": df["player_id"].to_numpy(), "date": df["game_id"].map(gd).to_numpy(), "season": df["season"].astype(int).to_numpy()})
    for stat in set(LABEL_OF.values()):
        cols = cf.NFL_STATS[stat][0]
        out[stat] = sum(pd.to_numeric(df[c], errors="coerce").to_numpy() for c in cols)
    return out.dropna(subset=["pid", "date"]).reset_index(drop=True)


def build_table(res: pd.DataFrame, keep: pd.DataFrame, df: pd.DataFrame, schedules: pd.DataFrame) -> pd.DataFrame:
    """Replay rows (engine priced at the book's line) + the screened consensus rows + player form -> the residual model's table."""
    gd = dict(zip(schedules["game_id"], schedules["gameday"].astype(str).str[:10]))
    pl = df[["game_id", "_name", "player_id"]].drop_duplicates(["game_id", "_name"], keep=False)
    r = res.merge(pl, left_on=["game_id", "player"], right_on=["game_id", "_name"], how="inner")
    r["game_date"] = r["game_id"].map(gd)
    k = keep.rename(columns={"gsis_id": "player_id", "model_stat": "stat", "stat": "bp_stat", "player_id": "bp_player_id"})
    k = k.assign(game_date=k["game_date"].astype(str).str[:10])
    t = r.merge(k, on=["player_id", "game_date", "stat"], how="inner", suffixes=("", "_k"))
    t = t[np.isclose(t["line"].astype(float), t["line_k"].astype(float))]
    base = pd.DataFrame({
        "sport": "nfl", "game_id": t["game_id"].astype(str), "player": t["player"], "pid": t["player_id"], "date": t["game_date"],
        "game_date": t["game_date"], "season": t["season"].astype(int), "week": t["week"].astype(int), "stat": t["stat"],
        "line": t["line"].astype(float), "y": t["y"], "over": t["over"], "push": False, "p_mkt": t["p_over_cons"], "n_books": t["n_books_k"] if "n_books_k" in t else t["n_books"],
        "disp": t["disp"], "best_over_odds": t["best_over_odds"], "best_under_odds": t["best_under_odds"],
        "med_over_odds": t["med_over_odds"], "med_under_odds": t["med_under_odds"], "open_line": t["open_line"], "open_p": t["open_p"],
        "p_novig": t["p_novig"], "p_prophetx": t["p_prophetx"], "p_pinnacle": t["p_pinnacle"],
        "eng_mu": t["mu_hier"], "eng_p": t["p_hier"], "eng0_p": t["p_base"],
    }).reset_index(drop=True)
    games = stat_values(df, schedules)
    parts = []
    for stat, h in base.groupby("stat"):
        hist = R.history_features(games.assign(value=games[stat]), h[["pid", "date", "season"]])
        parts.append(pd.concat([h, hist], axis=1))
    return pd.concat(parts).sort_index().reset_index(drop=True)


def build(args) -> int:
    from nfl.data.read import load
    from nfl.props import engine_eval as EE
    from nfl.props import env_adjust as EA
    from nfl.props.resolve import PlayerResolver
    seasons = parse_seasons(args.seasons)
    os.makedirs(args.work_dir, exist_ok=True)
    cons, longs = load_consensus(args.bp_dir)
    sched = load("schedules")
    resolver = PlayerResolver.from_mirror(range(min(seasons) - 1, max(seasons) + 1))
    market, keep, cov = market_tables(cons, longs, resolver)
    log.info("market table: %s", cov)
    df = EE.load_player_logs(range(min(seasons) - EE.KEEP_SEASONS + 1, max(seasons) + 1))
    cache = os.path.join(args.work_dir, "hier_market.parquet")
    if os.path.exists(cache):
        res = pd.read_parquet(cache)
    else:
        res = EE.replay_hierarchical(df, seasons, EA.game_env_table(sched), stats=sorted(set(LABEL_OF.values())), market=market)
        res.to_parquet(cache)
    table = build_table(res, keep, df, sched)
    if getattr(args, "with_kneel", False):
        from nfl.data.read import load as _load
        from nfl.props import kneel as K
        pbp = _load("pbp", seasons=seasons, columns=["game_id", "posteam", "qtr", "qb_kneel"])
        table = K.attach_kneel_features(table, sched, pbp)
        log.info("kneel features attached: %d eligible QB rush rows", int(table["kneel_eligible"].sum()))
    table.to_parquet(os.path.join(args.work_dir, "residual_table.parquet"), index=False)
    log.info("table: %d rows %s", len(table), table.groupby("season").size().to_dict())
    return 0


def run(args) -> int:
    table = pd.read_parquet(args.table)
    res, preds = R.study(table, holdout=args.holdout, report_holdout=args.report_holdout, post=POST_SEASONS, nthread=args.nthread,
                         n_boot=args.boot, log=log.info)
    kneel_md = ""
    if getattr(args, "kneel_ablation", False):
        from nfl.props import kneel as K
        res["kneel_ablation"] = K.kneel_ablation(table, holdout=args.holdout)
        kneel_md = "\n" + K.render_kneel_ablation(res["kneel_ablation"])
    if args.out_json:
        with open(args.out_json, "w") as fh:
            json.dump(res, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    if args.out_preds:
        preds.to_parquet(args.out_preds)
    md = R.render_study("NFL", res) + kneel_md
    if args.out_md:
        with open(args.out_md, "w") as fh:
            fh.write(md)
    print(md)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="replay the engine at the books' lines and write the residual table")
    b.add_argument("--bp-dir", required=True)
    b.add_argument("--work-dir", required=True)
    b.add_argument("--seasons", default="2021-2026")
    b.add_argument("--with-kneel", action="store_true", help="attach the N1 QB kneel feature columns (nfl.props.kneel) using mirrored pbp")
    r = sub.add_parser("run", help="walk-forward evaluation")
    r.add_argument("--table", required=True)
    r.add_argument("--holdout", type=int, default=HOLDOUT_SEASON)
    r.add_argument("--report-holdout", action="store_true", help="ALSO score the held-out season (once, at the end)")
    r.add_argument("--kneel-ablation", action="store_true", help="ALSO run the N1 kneel with/without ablation on the holdout season (table must carry the kneel columns)")
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
