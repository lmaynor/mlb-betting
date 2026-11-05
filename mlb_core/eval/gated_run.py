"""Two-stage driver for the confidence-gated engine evaluation (protocol in mlb_core.eval.gating).

    python -m mlb_core.eval.gated_run dev  --nfl-records nfl_rec.csv --nba-records nba_rec.pkl --out-dir OUT
    python -m mlb_core.eval.gated_run held --nfl-records nfl_rec.csv --nba-records nba_rec.pkl --out-dir OUT

`dev` reads the DEV seasons only, freezes the thresholds from features, screens every gate and writes OUT/frozen.json.
`held` refuses to run unless frozen.json exists, scores the held-out seasons with exactly those thresholds and survivors, and writes
OUT/held_scored.lock; a second `held` run is refused unless --allow-rescore is passed (and that is printed in the report), because
re-scoring the held-out split after looking at it is how a forking-paths result gets manufactured.
Records files are what `python -m nfl.game.market_eval --records-csv` / `python -m nba.game.market_eval --records-pkl` write.
UNVALIDATED research tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd

from mlb_core.eval import gating as G

SPLITS = {"nfl": {"dev": [2021, 2022, 2023], "held": [2024, 2025]},
          "nba": {"dev": [2021, 2022, 2023], "held": [2024, 2025]}}     # NBA season = start year (2025 = 2025-26)


def _events(sport: str, path: str) -> Dict[str, pd.DataFrame]:
    if sport == "nfl":
        from nfl.game import market_eval as me
        rec = pd.read_csv(path)
    else:
        from nba.game import market_eval as me
        rec = pd.read_pickle(path)
    return me.to_events(rec)


def _strip(res: Dict[Tuple[str, str, str], dict]) -> Dict[str, dict]:
    return {"|".join(k): {a: b for a, b in v.items() if a != "diffs"} for k, v in res.items()}


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _jsonable(o):
    import numpy as np
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


def run_dev(inputs: Dict[str, str], out_dir: str) -> Dict[str, object]:
    frozen: Dict[str, object] = {"spec_version": G.SPEC_VERSION, "splits": SPLITS, "sports": {}, "survivors": [],
                                 "dev_cells_tried": 0}
    dev_all: Dict[Tuple[str, str, str], dict] = {}
    for sport, path in inputs.items():
        ev = _events(sport, path)
        dev, _held_unused = G.split_by_season(ev, SPLITS[sport]["dev"], SPLITS[sport]["held"])
        th = G.freeze_thresholds(sport, dev)              # features only
        frozen["sports"][sport] = th
        dev_all.update(G.run_split(sport, dev, th))
    surv = G.screen_dev(dev_all)
    frozen["survivors"] = ["|".join(k) for k in surv]
    frozen["dev_cells_tried"] = len(G.tried(dev_all))
    frozen["dev_cells_not_applicable"] = ["|".join(k) for k, v in dev_all.items() if v.get("not_applicable")]
    frozen["inputs_sha"] = {s: hashlib.sha256(open(p, "rb").read()).hexdigest()[:16] for s, p in inputs.items()}
    frozen["frozen_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    frozen["frozen_sha"] = _sha({k: v for k, v in frozen.items() if k != "frozen_at"})
    os.makedirs(out_dir, exist_ok=True)
    json.dump(frozen, open(os.path.join(out_dir, "frozen.json"), "w"), indent=1, default=_jsonable)
    json.dump(_strip(dev_all), open(os.path.join(out_dir, "dev_results.json"), "w"), indent=1, default=_jsonable)
    lines = [f"# DEV split ({G.SPEC_VERSION})", "", f"gate cells tried on dev: {frozen['dev_cells_tried']}; survivors "
             f"(dev gain > 0, c > 0, n >= {G.MIN_N_SCREEN}): {len(surv)}", ""]
    for sport in inputs:
        lines += [f"## {sport.upper()} dev", "", G.render_table(sport, dev_all), ""]
    open(os.path.join(out_dir, "dev_report.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return frozen


def run_held(inputs: Dict[str, str], out_dir: str, allow_rescore: bool = False, B: int = 4000) -> Dict[str, object]:
    fz_path = os.path.join(out_dir, "frozen.json")
    if not os.path.exists(fz_path):
        raise SystemExit("frozen.json missing: run the `dev` stage first (thresholds must be frozen before held-out is scored)")
    frozen = json.load(open(fz_path))
    lock = os.path.join(out_dir, "held_scored.lock")
    if os.path.exists(lock) and not allow_rescore:
        raise SystemExit(f"held-out split already scored ({open(lock).read().strip()}); pass --allow-rescore to repeat (it will be flagged)")
    check = {k: v for k, v in frozen.items() if k != "frozen_at"}
    check.pop("frozen_sha", None)
    if _sha(check) != frozen["frozen_sha"]:
        raise SystemExit("frozen.json was edited after freezing")
    held_all: Dict[Tuple[str, str, str], dict] = {}
    for sport, path in inputs.items():
        if hashlib.sha256(open(path, "rb").read()).hexdigest()[:16] != frozen["inputs_sha"][sport]:
            raise SystemExit(f"{sport} records file differs from the one the thresholds were frozen on")
        ev = _events(sport, path)
        _dev_unused, held = G.split_by_season(ev, SPLITS[sport]["dev"], SPLITS[sport]["held"])
        held_all.update(G.run_split(sport, held, frozen["sports"][sport]))
    surv = [tuple(s.split("|")) for s in frozen["survivors"]]
    conf = G.confirm(held_all, surv, B=B)
    v = G.verdict(conf, held_all)
    open(lock, "w").write(time.strftime("%Y-%m-%dT%H:%M:%S") + (" (re-score)" if allow_rescore else ""))
    json.dump({"verdict": v, "n_family": conf["n_family"], "n_survivors": conf["n_survivors"],
               "any_strict": conf["any_strict"], "cells": {"|".join(k): c for k, c in conf["cells"].items()},
               "results": _strip(held_all)}, open(os.path.join(out_dir, "held_results.json"), "w"), indent=1, default=_jsonable)
    lines = [f"# HELD-OUT split ({G.SPEC_VERSION})" + (" -- RE-SCORED, not a clean single look" if allow_rescore else ""), "",
             f"verdict: {v}; family of gate cells tried: {conf['n_family']}; dev survivors: {conf['n_survivors']}; "
             f"any max-T-adjusted p < .05 over the whole family: {conf['any_strict']}", ""]
    for sport in inputs:
        lines += [f"## {sport.upper()} held-out", "", G.render_table(sport, held_all, conf), ""]
    open(os.path.join(out_dir, "held_report.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return {"verdict": v, "conf": conf}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=["dev", "held"])
    ap.add_argument("--nfl-records")
    ap.add_argument("--nba-records")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--allow-rescore", action="store_true")
    ap.add_argument("--B", type=int, default=4000)
    a = ap.parse_args(argv)
    inputs = {s: p for s, p in (("nfl", a.nfl_records), ("nba", a.nba_records)) if p}
    if not inputs:
        ap.error("give at least one of --nfl-records / --nba-records")
    if a.stage == "dev":
        run_dev(inputs, a.out_dir)
    else:
        run_held(inputs, a.out_dir, a.allow_rescore, a.B)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
