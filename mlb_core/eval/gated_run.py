"""Two-stage driver for the confidence-gated evaluation (protocol in mlb_core.eval.gating), ONE model / sport at a time.

A sport adapter builds a `GatedSource` (event frames, the records file they came from, its season split and its PHASE minimum) and calls:

    run_dev(source, out_dir)    reads the DEV seasons only, freezes thresholds from features, screens every gate, writes
                                out_dir/frozen.json, dev_results.json, dev_report.md
    run_held(source, out_dir)   refuses to run unless frozen.json exists and is unedited and the records file is the one the
                                thresholds were frozen on; scores the held-out seasons with exactly those thresholds and survivors;
                                writes held_results.json, held_report.md and held_scored.lock. A second run is refused unless
                                allow_rescore=True (and the report says so): re-scoring the held-out split after looking at it is how
                                a forking-paths result gets manufactured.

Nothing here imports a sport package; the family of cells (and so the max-T penalty) is the one source's own cells.
UNVALIDATED research tool.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

import pandas as pd

from mlb_core.eval import gating as G


@dataclass
class GatedSource:
    label: str                                         # names the cells, e.g. "nfl"; never interpreted
    events_fn: Callable[[], Dict[str, pd.DataFrame]]   # market -> event frame with a `season` column (see gating.py)
    records_path: str                                  # file the events were built from (its hash is frozen)
    dev_seasons: List[int]
    held_seasons: List[int]
    phase_min: float


def _strip(res: Dict[Tuple[str, str, str], dict]) -> Dict[str, dict]:
    return {"|".join(k): {a: b for a, b in v.items() if a != "diffs"} for k, v in res.items()}


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _file_sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def _jsonable(o):
    import numpy as np
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


def run_dev(src: GatedSource, out_dir: str) -> Dict[str, object]:
    dev, _held_unused = G.split_by_season(src.events_fn(), src.dev_seasons, src.held_seasons)
    th = G.freeze_thresholds(src.label, dev, src.phase_min)           # features only
    dev_res = G.run_split(src.label, dev, th)
    surv = G.screen_dev(dev_res)
    frozen: Dict[str, object] = {
        "spec_version": G.SPEC_VERSION, "label": src.label, "dev_seasons": src.dev_seasons, "held_seasons": src.held_seasons,
        "thresholds": th, "survivors": ["|".join(k) for k in surv], "dev_cells_tried": len(G.tried(dev_res)),
        "dev_cells_not_applicable": ["|".join(k) for k, v in dev_res.items() if v.get("not_applicable")],
        "inputs_sha": _file_sha(src.records_path)}
    frozen["frozen_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    frozen["frozen_sha"] = _sha({k: v for k, v in frozen.items() if k != "frozen_at"})
    os.makedirs(out_dir, exist_ok=True)
    json.dump(frozen, open(os.path.join(out_dir, "frozen.json"), "w"), indent=1, default=_jsonable)
    json.dump(_strip(dev_res), open(os.path.join(out_dir, "dev_results.json"), "w"), indent=1, default=_jsonable)
    lines = [f"# DEV split ({G.SPEC_VERSION}, {src.label})", "",
             f"gate cells tried on dev: {frozen['dev_cells_tried']}; survivors (dev gain > 0, c > 0, n >= {G.MIN_N_SCREEN}): "
             f"{len(surv)}", "", G.render_table(src.label, dev_res), ""]
    open(os.path.join(out_dir, "dev_report.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return frozen


def run_held(src: GatedSource, out_dir: str, allow_rescore: bool = False, B: int = 4000) -> Dict[str, object]:
    fz_path = os.path.join(out_dir, "frozen.json")
    if not os.path.exists(fz_path):
        raise SystemExit("frozen.json missing: run the dev stage first (thresholds must be frozen before held-out is scored)")
    frozen = json.load(open(fz_path))
    lock = os.path.join(out_dir, "held_scored.lock")
    if os.path.exists(lock) and not allow_rescore:
        raise SystemExit(f"held-out split already scored ({open(lock).read().strip()}); pass allow_rescore to repeat (it is flagged)")
    check = {k: v for k, v in frozen.items() if k not in ("frozen_at", "frozen_sha")}
    if _sha(check) != frozen["frozen_sha"]:
        raise SystemExit("frozen.json was edited after freezing")
    if _file_sha(src.records_path) != frozen["inputs_sha"]:
        raise SystemExit("records file differs from the one the thresholds were frozen on")
    if src.label != frozen["label"] or list(src.dev_seasons) != list(frozen["dev_seasons"]) \
            or list(src.held_seasons) != list(frozen["held_seasons"]):
        raise SystemExit("label / season split differs from the frozen one")
    _dev_unused, held = G.split_by_season(src.events_fn(), src.dev_seasons, src.held_seasons)
    held_res = G.run_split(src.label, held, frozen["thresholds"])
    surv = [tuple(s.split("|")) for s in frozen["survivors"]]
    conf = G.confirm(held_res, surv, B=B)
    v = G.verdict(conf, held_res)
    open(lock, "w").write(time.strftime("%Y-%m-%dT%H:%M:%S") + (" (re-score)" if allow_rescore else ""))
    json.dump({"verdict": v, "label": src.label, "n_family": conf["n_family"], "n_survivors": conf["n_survivors"],
               "any_strict": conf["any_strict"], "cells": {"|".join(k): c for k, c in conf["cells"].items()},
               "results": _strip(held_res)}, open(os.path.join(out_dir, "held_results.json"), "w"), indent=1, default=_jsonable)
    lines = [f"# HELD-OUT split ({G.SPEC_VERSION}, {src.label})" + (" -- RE-SCORED, not a clean single look" if allow_rescore else ""),
             "", f"verdict: {v}; family of gate cells tried: {conf['n_family']}; dev survivors: {conf['n_survivors']}; "
             f"any max-T-adjusted p < .05 over the family: {conf['any_strict']}", "", G.render_table(src.label, held_res, conf), ""]
    open(os.path.join(out_dir, "held_report.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return {"verdict": v, "conf": conf}
