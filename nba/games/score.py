"""
nba.games.score -- price the upcoming NBA slate with the game models against the live ParlayAPI consensus.

CODE-ONLY / UNVALIDATED: nothing here writes to a DB, Discord or the picks pages. It prints (and optionally saves) a table
of model vs de-vigged consensus per upcoming game. Use `nba.games.evaluate` first; do not trust a disagreement here until
the walk-forward shows the model carries information the line does not (see docs/analysis/nba_game_models_*.md).

    PYTHONPATH=. python -m nba.games.score [--csv <game_lines csv>] [--tsf-csv <csv>] [--out latest.json]

Schedule = the games the lines cover (ParlayAPI events). History = hoopR/ESPN team box from the GCS mirror (or
--hoopr-dir for offline). Models are refit on every completed game before today.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from nba.config import oddsaccum_csv_key
from nba.games import data, features as F, market, models as M
from nba.teams import team_code


EARLY_SEASON_GAMES = 15


def _latest_csv() -> pd.DataFrame:
    from mlb_core import storage
    ptr = json.loads(storage.read_bytes("OddsAccum/basketball_nba/latest_game_lines.json"))
    return storage.read_csv(oddsaccum_csv_key("basketball_nba", ptr["date"], "game_lines", ptr["hhmm"]))


def _season_of(date: str) -> int:
    y, m = int(date[:4]), int(date[5:7])
    return y if m >= 8 else y - 1


def upcoming_rows(cons: pd.DataFrame) -> pd.DataFrame:
    u = cons[["date", "home", "away"]].drop_duplicates().copy()
    u["game_id"] = "up_" + u["date"] + "_" + u["home"] + "_" + u["away"]
    u["tip"] = pd.NaT
    u["season"] = u["date"].map(_season_of)
    u["season_type"] = 2
    u["home_pts"] = u["away_pts"] = np.nan
    for s in ("h", "a"):
        for b in data.BOX:
            u[f"{s}_{b}"] = np.nan
    return u


def score(hist: pd.DataFrame, lines: pd.DataFrame, kind: str = "ridge", train_from: int = 2008) -> pd.DataFrame:
    long = market.from_parlay(lines)
    cons = market.consensus(long)
    up = upcoming_rows(cons)
    feat = F.build(pd.concat([hist, up], ignore_index=True))
    done = feat[feat["home_pts"].notna() & (feat["season"] >= train_from)]
    gm = M.fit(done, kind)
    fut = feat[feat["home_pts"].isna()]
    out = market.attach(pd.concat([fut, gm.predict(fut)], axis=1), cons)
    out["p_cover_model"] = M.p_home_cover(out["mu_margin"], out["sigma_margin"], out["sp_line"])
    out["p_over_model"] = M.p_over(out["mu_total"], out["sigma_total"], out["tot_line"])
    out["edge_ml"] = out["p_ml"] - out["ml_p_home"]
    out["edge_spread"] = out["p_cover_model"] - out["sp_p_home"]
    out["edge_total"] = out["p_over_model"] - out["tot_p_over"]
    pin = market.sharp_reference(long)
    out = out.merge(pin, on=["date", "home", "away"], how="left")
    out["games_min"] = np.minimum(out["h_season_game_no"], out["a_season_game_no"])
    # Ratings are last season's until teams play: through ~game 15 the model does not know the new rosters (preseason
    # slate on 2026-10-19 showed 13-pt and 20-pt gaps to the line, all of it stale ratings). Edges are NOT meaningful there.
    out["early_season"] = out["games_min"] < EARLY_SEASON_GAMES
    out["validated"] = False
    keep = ["date", "home", "away", "mu_margin", "p_ml", "ml_p_home", "edge_ml", "sp_line", "p_cover_model", "sp_p_home",
            "edge_spread", "mu_total", "tot_line", "p_over_model", "tot_p_over", "edge_total", "pin_p_home", "n_books_ml",
            "n_books_sp", "n_books_tot", "games_min", "early_season", "validated"]
    return out[[c for c in keep if c in out.columns]].sort_values(["date", "home"]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None, help="a banked game_lines CSV (default: the latest in GCS)")
    ap.add_argument("--hoopr-dir", default=None, help="offline hoopR mirror dir (default: GCS NBA/hoopr)")
    ap.add_argument("--tsf-csv", default=None)
    ap.add_argument("--kind", default="ridge", choices=["ridge", "hgb"])
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lines = pd.read_csv(a.csv) if a.csv else _latest_csv()
    hist = data.load(a.hoopr_dir, a.tsf_csv) if a.hoopr_dir else data.load(None, a.tsf_csv)
    res = score(hist, lines, a.kind)
    pd.set_option("display.width", 220, "display.max_columns", 30)
    print(res.round(3).to_string(index=False))
    if a.out:
        res.to_json(a.out, orient="records", indent=1)


if __name__ == "__main__":
    main()
