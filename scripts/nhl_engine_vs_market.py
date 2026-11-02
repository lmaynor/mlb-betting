"""The SERVING NHL engine against HISTORICAL BettingPros lines (goalie saves, goals, assists, points).

    MLB_BASE_DATA=<data dir> PYTHONPATH=. python -m scripts.nhl_engine_vs_market --data-dir <dir with NHL/ and bp/> \
        --stat goals [--test-from 2022-10-01] [--goalie-beta B] [--xg-dir <nhl.xg.build output>] [--xg-prior] [--out r.json]

For every historical consensus prop (nhl.props.bp_eval: reference books only, de-vigged median, fail-closed player / game
join, close-ish snapshot, integer lines dropped) it calls the production functions (`ev.daily_projections.project_nhl`,
`GameLogs.context`) AS OF the game date -- history, league contexts, team pace, goalie tracker all cut to games strictly
before it -- prices the market's own line with the engine's distribution, and scores that probability against the
market's with the same machinery PR #70 used (blend coefficient c, per-season log loss, disagreement ROI at the best price).
The legacy engine (scripts.nhl_engine_replay.legacy_mu) is scored on the SAME props, so the comparison is paired.

What it can and cannot say (inherited from the feed): there is no closing-line / CLV series, so nothing here reaches
"promote"; c > 0 means the engine adds information beyond the market, and the disagreement ROI is read against the
both-sides no-skill reference. Saves props are scored on goalie STARTS only (a book voids a bet on a goalie who does not
start, and the engine projects probable starters).

Switches under test (nhl.props.hierarchical): --goalie-beta (opposing-goalie GSAx factor on skater scoring) and --xg-prior
(shooter xG-per-shot scaled shooting-% prior; needs --xg-dir, offline only).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import numpy as np
import pandas as pd

from ev import daily_projections as DP
from mlb_core.data import cockpit_form as cf
from nhl.props import bp_eval as BE
from nhl.props import hierarchical as H
from nhl.xg import data as D
from scripts.nhl_engine_replay import legacy_mu

log = logging.getLogger("scripts.nhl_engine_vs_market")
STATS = ("saves", "goals", "assists", "points")


def attach_xg(logs: DP.GameLogs, xg_dir: str) -> int:
    """Merge the walk-forward per-skater-game xG (ixg_sog, sog_pop, ixg, attempts) onto the skater rows (offline research only)."""
    xs = pd.read_parquet(os.path.join(xg_dir, "xg_skater_game.parquet"))
    cols = ["ixg_sog", "sog_pop", "ixg", "attempts"]
    look = {(int(g), int(p)): tuple(v) for g, p, *v in xs[["game_id", "player_id", *cols]].itertuples(index=False)}
    n = 0
    for name, rows in logs.idx["skater"].items():
        if str(name).startswith("id:"):
            continue
        for r in rows:
            v = look.get((int(r.get("game_id", -1)), int(r.get("player_id", -1))))
            if v is not None:
                r.update(zip(cols, v))
                n += 1
    return n


def outcome_frame(masters: dict, stat: str) -> pd.DataFrame:
    if stat == "saves":
        g = masters["goalies"]
        g = g[g["game_type"].isin([2, 3]) & (pd.to_numeric(g["toi_sec"], errors="coerce") > 0)]
        g = g[g["starter"].astype(str).str.lower().isin(["true", "1", "1.0"])]
        return g.assign(actual=g["saves"])[["game_id", "player_id", "actual"]]
    s = masters["skaters"]
    s = s[s["game_type"].isin([2, 3]) & (pd.to_numeric(s["toi_sec"], errors="coerce") > 0)]
    return s.assign(actual=s[stat])[["game_id", "player_id", "actual"]]


def build_props(data_dir: str, stat: str, test_from: str) -> tuple[pd.DataFrame, dict]:
    m = D.load_masters(data_dir)
    names, games = BE.name_table(m), BE.games_table(m["games"])
    long_df = BE.load_bp_long(os.path.join(data_dir, "bp"), BE.SLUG[stat], start=test_from)
    cons = BE.build_consensus(long_df, stat)
    props, cov = BE.join_market(cons, names, games, outcome_frame(m, stat), "actual")
    return props, cov


def engine_probs(logs: DP.GameLogs, props: pd.DataFrame, stat: str) -> pd.DataFrame:
    """-> props with p_new / p_old (P(over) at the market line) and mu_new / mu_old; props the engines cannot project are dropped."""
    goalie = stat == "saves"
    table = logs.idx["goalie" if goalie else "skater"]
    sdef = (cf.NHL_GOALIE_STATS if goalie else cf.NHL_SKATER_STATS)[stat]
    family = DP.PROJECT_STATS["nhl"][stat]
    pos_cache: dict = {}
    out = []
    for r in props.itertuples(index=False):
        rows = table.get(f"id:{int(r.player_id)}")
        if not rows:
            continue
        pm = pos_cache.get(r.player_id)
        if pm is None:
            pm = pos_cache[r.player_id] = {g: i for i, g in enumerate(x.get("game_id") for x in rows)}
        i = pm.get(r.game_id)
        if i is None or i < DP.MIN_GAMES:
            continue
        row, hist = rows[i], rows[max(0, i - 80):i]
        d = str(row["_date"])[:10]
        team, opp = DP.canon_team("nhl", row.get("team_abbr")), DP.canon_team("nhl", row.get("_opp"))
        proj, dist, _ = DP.project_nhl(stat, hist, sdef, logs, team, opp, d, None, None)
        old = legacy_mu(stat, hist, sdef, logs, opp, d)
        if proj is None or old is None:
            continue
        ctx = logs.context(stat, d)
        p_new = dist.p_over(r.line) if dist is not None else DP.p_over(family, proj["mu"], r.line, ctx)
        p_old = DP.p_over(family, old, r.line, ctx)
        out.append((r.game_id, r.player_id, r.line, proj["mu"], old, p_new, p_old))
    cols = ["game_id", "player_id", "line", "mu_new", "mu_old", "p_new", "p_old"]
    e = pd.DataFrame(out, columns=cols)
    return props.merge(e, on=["game_id", "player_id", "line"], how="inner")


def score_variant(p: pd.DataFrame, col: str, label: str) -> dict:
    q = p.assign(p_model=p[col].clip(0.005, 0.995))
    return {"market": BE.score(q, label), "by_season": BE.score_by_season(q)}


def run(data_dir: str, stat: str, test_from: str, out: str | None, xg_dir: str | None) -> dict:
    cf.KEEP_SEASONS = 8
    idx = cf._load_nhl()
    logs = DP.GameLogs("nhl", idx)
    if xg_dir:
        log.info("xG rows attached: %d", attach_xg(logs, xg_dir))
    props, cov = build_props(data_dir, stat, test_from)
    p = engine_probs(logs, props, stat)
    log.info("%s: %d market props scored by both engines (of %d joined)", stat, len(p), len(props))
    rep = {"stat": stat, "test_from": test_from, "n_props": int(len(p)), "coverage": cov,
           "goalie_beta": H.GOALIE_BETA, "xg_prior": H.XG_PRIOR,
           "new": score_variant(p, "p_new", f"{stat}/new"), "legacy": score_variant(p, "p_old", f"{stat}/legacy")}
    ll = lambda col: float(-np.mean(p["won_over"] * np.log(p[col].clip(.005, .995)) + (1 - p["won_over"]) * np.log(1 - p[col].clip(.005, .995))))  # noqa: E731
    d = (-(p["won_over"] * np.log(p["p_new"].clip(.005, .995)) + (1 - p["won_over"]) * np.log(1 - p["p_new"].clip(.005, .995)))
         + (p["won_over"] * np.log(p["p_old"].clip(.005, .995)) + (1 - p["won_over"]) * np.log(1 - p["p_old"].clip(.005, .995))))
    rep["paired_logloss_new_minus_legacy"] = {"mean": float(d.mean()), "se": float(d.std(ddof=1) / np.sqrt(len(d))),
                                              "new": ll("p_new"), "legacy": ll("p_old")}
    if out:
        with open(out, "w") as fh:
            json.dump(rep, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
        p.to_parquet(out.replace(".json", ".parquet"), index=False)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--stat", required=True, choices=STATS)
    ap.add_argument("--test-from", default="2022-10-01")
    ap.add_argument("--goalie-beta", type=float, default=None)
    ap.add_argument("--xg-dir", default=None)
    ap.add_argument("--xg-prior", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.goalie_beta is not None:
        H.GOALIE_BETA = a.goalie_beta
    H.XG_PRIOR = a.xg_prior
    rep = run(a.data_dir, a.stat, a.test_from, a.out, a.xg_dir)
    for lab in ("new", "legacy"):
        print(lab)
        for r in rep[lab]["by_season"]:
            print("  ", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()
                         if k in ("season", "n", "logloss_model", "logloss_market", "blend_c", "blend_t", "roi5", "roi5_n")})
    print("paired logloss new-legacy:", rep["paired_logloss_new_minus_legacy"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
