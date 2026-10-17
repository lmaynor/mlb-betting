"""scripts/pooled_shift_probe.py --mode outcome: the pure dispersion fit (no storage, no network)."""
import importlib.util
import pathlib

import numpy as np
import pandas as pd
import pytest

_spec = importlib.util.spec_from_file_location("pooled_shift_probe", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "pooled_shift_probe.py")
PSP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PSP)


def _frame(stat, lines, sd_frac, n, seed=0):
    rng = np.random.default_rng(seed)
    line = rng.choice(lines, n).astype(float)
    return pd.DataFrame({"stat": stat, "line": line, "actual": np.clip(line + rng.normal(0, sd_frac * line), 0, None)})


def test_recovers_a_known_sd_fraction_and_flags_a_mismatch_with_production():
    d = pd.concat([_frame("pass_yds", [220, 250, 280], 0.22, 20000), _frame("rec_yds", [30, 50, 70], 0.9, 20000, seed=1)])
    r = PSP.outcome_dispersion(d)
    assert r["pass_yds"]["sd_frac_rms"] == pytest.approx(0.22, abs=0.01)
    assert r["pass_yds"]["production"] == 0.22
    assert r["rec_yds"]["sd_frac_rms"] > 0.75                       # clipping at 0 trims some of the 0.9; still far from 0.55
    assert set(r["pass_yds"]["by_line_tercile"]) == {"low", "mid", "high"}


def test_drops_tiny_lines_and_missing_actuals():
    d = pd.DataFrame({"stat": ["rush_yds"] * 4, "line": [2.0, 40.0, 40.0, 40.0], "actual": [90.0, 40.0, np.nan, 44.0]})
    r = PSP.outcome_dispersion(d)["rush_yds"]
    assert r["n"] == 2 and r["bias"] == pytest.approx(0.05)
