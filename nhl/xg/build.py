"""Build the walk-forward xG tables locally:

    PYTHONPATH=. python -m nhl.xg.build --data-dir <dir with NHL/shots> --out-dir <dir> [--no-save-models]

Writes (parquet, LOCAL ONLY -- nothing is uploaded; there is no --gcs flag on purpose):
    xg_skater_game.parquet  xg_goalie_game.parquet  xg_team_game.parquet  xg_shots_meta.json
and, unless --no-save-models, the booster/meta pair per population fit on ALL seasons (what a live scorer would load;
its past-season xG in the tables above is still the out-of-season walk-forward value, never this final fit).
"""
from __future__ import annotations

import argparse
import json
import os

from nhl.xg import aggregate as A
from nhl.xg import data as D
from nhl.xg import features as F
from nhl.xg import model as M


def build(shots, save_models_to: str | None = None) -> dict:
    prep = F.prepare_shots(shots)
    wf_att = M.walk_forward(prep, "attempt")
    wf_sog = M.walk_forward(prep, "sog")
    s = A.attach_xg(prep, wf_att["xg"], wf_sog["xg"])
    s["xg_src"] = ""
    s.loc[wf_att.index, "xg_src"] = wf_att["xg_src"]
    out = {"skater_game": A.skater_game(s), "goalie_game": A.goalie_game(s), "team_game": A.team_game(s),
           "prep": s}
    if save_models_to:
        seasons = sorted(prep["season"].unique())
        for pop in ("attempt", "sog"):
            art = M.fit(prep, pop, seasons[:-1], val_season=seasons[-1])
            M.save(art, save_models_to)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--no-save-models", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    r = build(D.load_shots(a.data_dir), None if a.no_save_models else os.path.join(a.out_dir, "models"))
    for k in ("skater_game", "goalie_game", "team_game"):
        r[k].to_parquet(os.path.join(a.out_dir, f"xg_{k}.parquet"), index=False)
    meta = {"rows": {k: int(len(r[k])) for k in ("skater_game", "goalie_game", "team_game")},
            "xg_src_counts": r["prep"]["xg_src"].value_counts().to_dict()}
    with open(os.path.join(a.out_dir, "xg_shots_meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
