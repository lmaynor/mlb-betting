"""Train == serve parity on the REAL NHL masters.

    PYTHONPATH=. python -m scripts.nhl_serving_parity --data-dir <dir with NHL/> --xg-dir <nhl.xg.build output> \
        [--dates 2025-11-15,2026-01-20,...] [--n-dates 5]

For each sampled game date D: build the historical feature frames from ALL data (the training path, expected-starter mode),
then pretend D's games have not been played -- cut the masters and the xG tables to games strictly before D, append placeholder
rows for D's games and the players who actually dressed -- and build the features with nhl.props.live_features. Every
SAVES_FEATURES / SKATER_FEATURES value of every row must agree (NaN == NaN, float tolerance 1e-9). Any disagreement means a
served model would see different inputs from the ones it was trained on. Exit code 1 on any mismatch.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

from nhl.props import live_features as LF
from nhl.props import serve_trained as ST
from nhl.props import xgfeat as X
from nhl.xg import data as D


def cut(masters: dict, xg: dict, day: str) -> tuple[dict, dict]:
    m = {k: v for k, v in masters.items()}
    keep = set(masters["games"].loc[masters["games"]["date"].astype(str) < day, "game_id"])
    for k in ("games", "skaters", "goalies", "teams"):
        m[k] = masters[k][masters[k]["game_id"].isin(keep)]
    return m, {k: v[v["game_id"].isin(keep)] for k, v in xg.items()}


def compare(live: pd.DataFrame, hist: pd.DataFrame, cols: list, keys=("player_id", "team")) -> dict:
    j = live.merge(hist, on=list(keys), suffixes=("_live", "_hist"))
    bad = {}
    for c in cols:
        a, b = j[f"{c}_live"].astype(float).to_numpy(), j[f"{c}_hist"].astype(float).to_numpy()
        ok = (np.isnan(a) & np.isnan(b)) | np.isclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=False)
        if not ok.all():
            bad[c] = int((~ok).sum())
    return {"rows_live": int(len(live)), "rows_hist": int(len(hist)), "rows_joined": int(len(j)), "mismatched_columns": bad}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--xg-dir", required=True)
    ap.add_argument("--dates", default=None)
    ap.add_argument("--n-dates", type=int, default=5)
    a = ap.parse_args(argv)
    masters = D.load_masters(a.data_dir)
    xg = {"skater": pd.read_parquet(os.path.join(a.xg_dir, "xg_skater_game.parquet")),
          "goalie": pd.read_parquet(os.path.join(a.xg_dir, "xg_goalie_game.parquet")),
          "team": pd.read_parquet(os.path.join(a.xg_dir, "xg_team_game.parquet"))}
    fr = ST.training_frames(masters, xg)
    skf, svf = fr["skater"], fr["saves"]
    if a.dates:
        days = a.dates.split(",")
    else:
        pool = sorted(set(skf.loc[skf["date"] >= "2024-11-01", "date"]))
        days = [pool[i] for i in np.linspace(0, len(pool) - 1, a.n_dates).astype(int)]
    failed = False
    for day in days:
        m, x = cut(masters, xg, day)
        gday = masters["games"][masters["games"]["date"].astype(str) == day]
        upcoming = [{"date": day, "home": h, "away": w} for h, w in zip(gday["home_abbr"], gday["away_abbr"])]
        if not upcoming:
            print(day, "SKIPPED (no games)")
            continue
        h_sk, h_sv = skf[skf["date"] == day], svf[svf["date"] == day]
        ents = {}
        for team in {t for u in upcoming for t in (u["home"], u["away"])}:
            ents[team] = {"skaters": [(int(r.player_id), str(r.name), r.position) for r in h_sk[h_sk["team"] == team].itertuples()],
                          "goalies": [(int(r.player_id), str(r.name)) for r in h_sv[h_sv["team"] == team].itertuples()]}
        live = LF.build_live(m, x, upcoming, entities=ents)
        rs = compare(live["skater"], h_sk, X.SKATER_FEATURES)
        rv = compare(live["saves"], h_sv, X.SAVES_FEATURES)
        ok = not rs["mismatched_columns"] and not rv["mismatched_columns"] and rs["rows_joined"] > 0 and rv["rows_joined"] > 0
        failed |= not ok
        print(day, "OK" if ok else "MISMATCH", "| skaters", rs, "| goalies", rv)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
