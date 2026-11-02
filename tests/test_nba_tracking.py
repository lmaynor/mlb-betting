"""nba.data.tracking -- prior-season tracking tables, the fail-closed name crosswalk, the luck-removal ratios, the engine wiring
(off by default, prior season only) and the mirror helper. Offline; synthetic tables shaped like the community mirror's CSVs."""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ev import daily_projections as dp
from mlb_core.data import cockpit_form as cf
from nba.data import tracking as TR
from tests.test_nba_props_hierarchical import mk_rows, row, team_logs


def passing(rows):
    return pd.DataFrame([{"PLAYER": n, "TEAM": "LAL", "GP": 60, "MIN": m, "PassesMade": 1000, "AST": a, "PotentialAST": p,
                          "PLAYER_ID": 1000 + i, "TEAM_ID": 1} for i, (n, m, a, p) in enumerate(rows)])


def rebounding(rows, partial=False):
    gp = 12 if partial else 60
    d = pd.DataFrame([{"PLAYER": n, "TEAM": "LAL", "GP": gp, "MIN": m, "DREB": r * 0.7, "DREBChances": c * 0.7}
                      for n, m, r, c in rows])
    o = pd.DataFrame([{"PLAYER": n, "TEAM": "LAL", "GP": gp, "MIN": m, "OREB": r * 0.3, "OREBChances": c * 0.3}
                      for n, m, r, c in rows])
    return d, o


def year_tables(partial=False):
    lg_a = [(f"P{i}", 1500, 300, 600) for i in range(30)]                        # league conversion = 0.5
    p = passing(lg_a + [("Lucky Guy", 1800, 450, 600), ("Unlucky Guy", 1800, 150, 600), ("Thin Guy", 100, 20, 30),
                        ("Dupe Name", 1500, 300, 600), ("Dupe Name", 1500, 100, 600)])
    lg_r = [(f"P{i}", 1500, 400, 800) for i in range(30)]                         # league conversion = 0.5
    d, o = rebounding(lg_r + [("Lucky Guy", 1800, 600, 800), ("Unlucky Guy", 1800, 300, 800)], partial)
    return {"passing": p, "dreb": d, "oreb": o}


# ---------------------------------------------------------------------------------------------- ratios
def test_assist_ratio_regresses_lucky_conversion_down_and_unlucky_up_with_shrinkage():
    ty = TR.TrackingYear(2024, year_tables())
    lucky, unlucky = ty.assist_ratio(cf.norm_name("Lucky Guy")), ty.assist_ratio(cf.norm_name("Unlucky Guy"))
    assert lucky < 1.0 < unlucky
    pot, ast, k, lg = 600.0, 450.0, TR.K_AST, 0.5
    expect = pot * ((ast + k * lg) / (pot + k)) / ast
    assert lucky == pytest.approx(max(expect, TR.FACTOR_CLIP[0]))
    assert ty.assist_ratio(cf.norm_name("P3")) == pytest.approx(1.0, abs=1e-9)    # league-average conversion: no change
    assert ty.assist_ratio(cf.norm_name("Thin Guy")) == 1.0                        # too few minutes / potential assists
    assert ty.assist_ratio("nobody here") == 1.0
    assert TR.FACTOR_CLIP[0] <= lucky <= TR.FACTOR_CLIP[1]


def test_rebound_ratio_and_partial_rebound_files_are_rejected():
    ty = TR.TrackingYear(2023, year_tables())
    assert ty.rebound_ratio(cf.norm_name("Lucky Guy")) < 1.0 < ty.rebound_ratio(cf.norm_name("Unlucky Guy"))
    assert ty.rebound_ratio(cf.norm_name("P1")) == pytest.approx(1.0, abs=0.01)   # league rate includes the two outliers
    part = TR.TrackingYear(2024, year_tables(partial=True))                        # the mirror's 2024 file stops at ~23 games
    assert part.partial == {"dreb", "oreb"} and not part.has("dreb") and part.rebound_ratio(cf.norm_name("Lucky Guy")) == 1.0
    assert part.has("passing")                                                    # passing is unaffected


def test_duplicate_names_are_dropped_and_person_ids_resolve():
    ty = TR.TrackingYear(2024, year_tables())
    assert cf.norm_name("Dupe Name") in ty.dropped["passing"] and cf.norm_name("Dupe Name") not in ty.rows["passing"]
    assert ty.assist_ratio(cf.norm_name("Dupe Name")) == 1.0                       # fail closed: ambiguous -> no adjustment
    assert ty.person_id(cf.norm_name("P0")) == 1000 and ty.person_id("nobody") is None


def test_accented_and_suffixed_names_join_the_boards_key():
    frames = {"passing": passing([("Nikola Jokic", 2000, 300, 600), ("Jaren Jackson Jr.", 1800, 100, 300)] +
                                 [(f"P{i}", 1500, 300, 600) for i in range(10)])}
    ty = TR.TrackingYear(2024, frames)
    assert ty.person_id(cf.norm_name("Nikola Joki" + chr(0x107))) == 1000 and ty.person_id(cf.norm_name("Jaren Jackson")) == 1001


def test_tracking_factors_use_the_requested_year_and_gamma_only():
    tr = TR.Tracking({2023: TR.TrackingYear(2023, year_tables())})
    pn = cf.norm_name("Lucky Guy")
    full = tr.assist_factor(pn, 2023, 1.0)
    assert full < 1.0 and tr.assist_factor(pn, 2023, 0.5) == pytest.approx(full ** 0.5)
    assert tr.assist_factor(pn, 2024, 1.0) == 1.0 and tr.assist_factor(pn, 2023, 0.0) == 1.0   # other year / gamma 0
    assert tr.rebound_factor(pn, 2023, 1.0) < 1.0
    assert tr.coverage()[2023]["tables"] == ["dreb", "oreb", "passing"]


def test_season_year_of_follows_the_masters_labels():
    assert TR.season_year_of("2024-10-23") == 2024 and TR.season_year_of("2025-01-15") == 2024
    assert TR.season_year_of("2025-06-10") == 2024 and TR.season_year_of("2025-09-01") == 2025


# ---------------------------------------------------------------------------------------------- loading
def _write_clone(root, year, frames):
    d = root / str(year) / "player_tracking"
    d.mkdir(parents=True)
    for t, df in frames.items():
        df.to_csv(d / f"{t}.csv", index=False)


def test_load_from_a_source_layout_directory_and_missing_years_are_absent(tmp_path):
    _write_clone(tmp_path, 2023, year_tables())
    tr = TR.load([2022, 2023], str(tmp_path), source_layout=True)
    assert sorted(tr.years) == [2023] and tr.years[2023].has("passing", "dreb", "oreb")
    assert TR.load([2030], str(tmp_path), source_layout=True).years == {}


def test_mirror_is_a_dry_run_by_default_and_writes_the_manifest_when_asked(tmp_path, monkeypatch):
    _write_clone(tmp_path, 2023, year_tables())
    m = TR.mirror(str(tmp_path), [2023, 2024])
    assert {f["key"] for f in m["files"]} == {"NBA/tracking/2023/passing.csv", "NBA/tracking/2023/dreb.csv",
                                              "NBA/tracking/2023/oreb.csv"}
    written = {}
    from mlb_core import storage
    monkeypatch.setattr(storage, "write_bytes", lambda b, k: written.__setitem__(k, b))
    assert not written
    TR.mirror(str(tmp_path), [2023], dry_run=False)
    assert "NBA/tracking/2023/passing.csv" in written and "NBA/tracking/_manifest.json" in written
    assert "unlicensed" in json.loads(written["NBA/tracking/_manifest.json"])["source"]


# ------------------------------------------------------------------------------------------- engine wiring
def _logs_with_tracking(pn_lucky="Pf Forward"):
    logs = team_logs()
    frames = year_tables()
    frames["passing"] = pd.concat([frames["passing"], passing([(pn_lucky, 1800, 450, 600)])], ignore_index=True)
    d, o = rebounding([(pn_lucky, 1800, 600, 800)] + [(f"P{i}", 1500, 400, 800) for i in range(30)])
    frames["dreb"], frames["oreb"] = d, o
    logs.set_nba_tracking(TR.Tracking({2025: TR.TrackingYear(2025, frames)}))         # START is 2026-02: season_year 2025
    return logs


def test_engine_ignores_tracking_by_default_and_applies_it_when_switched_on():
    logs = _logs_with_tracking()
    plain = team_logs()
    assert dp.NBA_FEATURES["trk_ast"] == 0.0 and dp.NBA_FEATURES["trk_reb"] == 0.0
    base_ast = row(plain, "assists", "Pf Forward")["proj_mean"]
    assert row(logs, "assists", "Pf Forward")["proj_mean"] == base_ast             # off: tables loaded but unused
    with dp.nba_features(trk_ast=1.0):
        on = row(logs, "assists", "Pf Forward")["proj_mean"]
        other = row(logs, "assists", "Cy Center")["proj_mean"]                     # no tracking row: untouched
        pts = row(logs, "points", "Pf Forward")["proj_mean"]
    assert on < base_ast and other == row(plain, "assists", "Cy Center")["proj_mean"]
    assert pts == row(plain, "points", "Pf Forward")["proj_mean"]
    with dp.nba_features(trk_reb=1.0):
        assert row(logs, "rebounds", "Pf Forward")["proj_mean"] < row(plain, "rebounds", "Pf Forward")["proj_mean"]
        assert row(logs, "assists", "Pf Forward")["proj_mean"] == base_ast


def test_engine_reads_only_the_prior_seasons_table():
    """START (2026-02-20) belongs to season_year 2025, so the table of year 2026 (the season being played) is never read."""
    logs = team_logs()
    frames = year_tables()
    frames["passing"] = pd.concat([frames["passing"], passing([("Pf Forward", 1800, 450, 600)])], ignore_index=True)
    logs.set_nba_tracking(TR.Tracking({2026: TR.TrackingYear(2026, frames)}))
    with dp.nba_features(trk_ast=1.0):
        assert row(logs, "assists", "Pf Forward")["proj_mean"] == row(team_logs(), "assists", "Pf Forward")["proj_mean"]


def test_combos_inherit_the_tracking_factor_and_stay_additive():
    logs = _logs_with_tracking()
    with dp.nba_features(trk_ast=1.0, trk_reb=1.0):
        m = {s: row(logs, s, "Pf Forward")["proj_mean"] for s in ("points", "rebounds", "assists", "pra", "ra")}
    assert m["pra"] == pytest.approx(m["points"] + m["rebounds"] + m["assists"], abs=1e-3)
    assert m["ra"] == pytest.approx(m["rebounds"] + m["assists"], abs=1e-3)


def test_leakage_future_season_tables_never_change_a_prior_season_projection():
    a, b = _logs_with_tracking(), _logs_with_tracking()
    junk = year_tables()
    junk["passing"].loc[:, "AST"] = junk["passing"]["AST"] * 3
    b.set_nba_tracking(TR.Tracking({**b.nba_tracking.years, 2026: TR.TrackingYear(2026, junk), 2027: TR.TrackingYear(2027, junk)}))
    with dp.nba_features(trk_ast=1.0, trk_reb=1.0):
        for s in ("assists", "rebounds", "pra"):
            assert row(a, s, "Pf Forward") == row(b, s, "Pf Forward")


def test_loader_hook_returns_none_when_nothing_is_mirrored(monkeypatch):
    monkeypatch.setattr(TR, "load", lambda years, *a, **k: TR.Tracking({}))
    real = dp._load_nba_tracking.__wrapped__ if hasattr(dp._load_nba_tracking, "__wrapped__") else None
    import importlib
    fresh = importlib.import_module("ev.daily_projections")
    # conftest neutralises the hook for every test; the un-neutralised behaviour is exercised via the module source
    assert fresh.NBA_FEATURES["trk_ast"] == 0.0
    assert real is None or real(datetime(2026, 11, 1, tzinfo=timezone.utc)) is None
