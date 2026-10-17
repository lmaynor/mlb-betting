"""Offline tests for nfl.data.contracts (synthetic frames; no GCS)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from nfl.data import contracts as C

TODAY = date(2026, 10, 16)


def _sched(days_final=("2026-10-04", "2026-10-11"), pending=("2026-10-18",)):
    rows = []
    for i, d in enumerate(days_final):
        rows.append({"game_id": f"g{i}", "season": 2026, "week": i + 1, "gameday": d, "result": 3.0,
                     "spread_line": 2.5, "total_line": 44.0})
    for j, d in enumerate(pending):
        rows.append({"game_id": f"p{j}", "season": 2026, "week": 3, "gameday": d, "result": np.nan,
                     "spread_line": 1.5, "total_line": 40.0})
    return pd.DataFrame(rows)


def _ps(game_ids=("g0", "g1")):
    rows = []
    for g in game_ids:
        for team, pid, pos in (("AAA", "qb", "QB"), ("AAA", "wr", "WR"), ("BBB", "qb2", "QB")):
            rows.append({"player_id": f"{pid}_{g}", "game_id": g, "team": team, "position": pos, "season": 2026,
                         "week": 1, "completions": 20, "attempts": 30 if pos == "QB" else 0,
                         "passing_yards": 250 if pos == "QB" else 0, "passing_tds": 2 if pos == "QB" else 0,
                         "passing_interceptions": 0, "sacks_suffered": 1 if pos == "QB" else 0,
                         "carries": 0 if pos == "QB" else 5, "rushing_yards": 0 if pos == "QB" else 20,
                         "rushing_tds": 0, "receptions": 4 if pos == "WR" else 0, "targets": 6 if pos == "WR" else 0,
                         "receiving_yards": 50 if pos == "WR" else 0, "receiving_tds": 1 if pos == "WR" else 0,
                         "fantasy_points_ppr": np.nan})
    ps = pd.DataFrame(rows)
    ps["fantasy_points_ppr"] = C.ppr_formula(ps)
    return ps


def _ts(ps):
    g = ps.groupby(["game_id", "team"], as_index=False)[list(C.RECONCILE_COLS)].sum()
    return g.assign(season=2026, week=1)


def test_grain_passes_on_clean_frames():
    ps = _ps()
    out = {c["name"]: c for c in C.check_grain(_sched(), ps, _ts(ps))}
    assert all(c["status"] == C.PASS for c in out.values())


def test_grain_flags_duplicate_player_game():
    ps = pd.concat([_ps(), _ps().head(1)], ignore_index=True)
    out = {c["name"]: c for c in C.check_grain(_sched(), ps, _ts(_ps()))}
    assert out["grain_player_stats"]["status"] == C.FAIL and out["grain_player_stats"]["detail"]["duplicates"] == 1


def test_team_level_null_id_row_is_tolerated_but_one_with_offense_is_not():
    ps = _ps()
    phantom = {c: 0 for c in ps.columns if c not in ("player_id", "game_id", "team", "position")}
    phantom.update({"player_id": np.nan, "game_id": "g0", "team": "AAA", "position": np.nan, "season": 2026,
                    "week": 1, "penalties": 28, "penalty_yards": 130, "fantasy_points_ppr": 0.0})
    ok = pd.concat([ps, pd.DataFrame([phantom])], ignore_index=True)
    out = {c["name"]: c for c in C.check_grain(_sched(), ok, _ts(ps))}
    assert out["grain_player_stats"]["status"] == C.PASS
    assert out["grain_player_stats"]["detail"]["phantom_zero_rows"] == 1
    bad = pd.concat([ps, pd.DataFrame([{**phantom, "carries": 3}])], ignore_index=True)
    out = {c["name"]: c for c in C.check_grain(_sched(), bad, _ts(ps))}
    assert out["grain_player_stats"]["status"] == C.FAIL


def test_nonnegative_flags_negative_counts():
    ps = _ps()
    assert C.check_nonnegative(ps)["status"] == C.PASS
    ps.loc[0, "carries"] = -1
    res = C.check_nonnegative(ps)
    assert res["status"] == C.FAIL and res["detail"]["negative_rows"] == {"carries": 1}


def test_coverage_severity_follows_game_age():
    sched = _sched(days_final=("2026-10-15", "2026-10-11"))
    finals = C.final_games(sched, 2026)
    # g0 is 1 day old and missing -> warn; g1 (5 days old) present
    assert C.check_coverage("c", finals, {"g1"}, TODAY)["status"] == C.WARN
    # g1 missing is 5 days old -> fail
    assert C.check_coverage("c", finals, {"g0"}, TODAY)["status"] == C.FAIL
    assert C.check_coverage("c", finals, {"g0", "g1"}, TODAY)["status"] == C.PASS


def test_team_rows_per_game_requires_both_sides():
    ps = _ps()
    ts = _ts(ps)
    finals = C.final_games(_sched(), 2026)
    assert C.check_team_rows_per_game(finals, ts, TODAY)["status"] == C.PASS
    one_sided = pd.concat([ts[ts["game_id"] != "g1"], ts[ts["game_id"] == "g1"].head(1)])
    res = C.check_team_rows_per_game(finals, one_sided, TODAY)
    assert res["status"] == C.FAIL


def test_reconcile_detects_one_yard_mismatch():
    ps = _ps()
    ts = _ts(ps)
    assert C.check_reconcile(ps, ts)["status"] == C.PASS
    ts.loc[0, "passing_yards"] += 1
    res = C.check_reconcile(ps, ts)
    assert res["status"] == C.FAIL and res["detail"]["mismatches"] == {"passing_yards": 1}


def test_reconcile_fails_when_tables_do_not_overlap():
    ps = _ps()
    ts = _ts(ps).assign(game_id="zzz")
    assert C.check_reconcile(ps, ts)["status"] == C.FAIL


def test_ppr_check_catches_semantic_drift():
    ps = _ps()
    assert C.check_ppr_scoring(ps)["status"] == C.PASS
    ps["fantasy_points_ppr"] = ps["fantasy_points_ppr"] + 1.0        # e.g. a half-point-PPR relabel
    assert C.check_ppr_scoring(ps)["status"] == C.FAIL


def test_ppr_formula_known_value():
    row = pd.DataFrame([{"passing_yards": 300, "passing_tds": 2, "passing_interceptions": 1, "rushing_yards": 20,
                         "receiving_yards": 0, "receptions": 0, "rushing_tds": 1, "sack_fumbles_lost": 1}])
    # 12 + 8 - 2 + 2 + 6 - 2
    assert C.ppr_formula(row).iloc[0] == pytest.approx(24.0)


def test_schedule_results_fresh_warns_then_fails():
    s = _sched(days_final=("2026-10-04",), pending=("2026-10-15",))
    assert C.check_schedule_results_fresh(s, TODAY)["status"] == C.WARN          # 1 day overdue
    s2 = _sched(days_final=("2026-10-04",), pending=("2026-10-13",))
    assert C.check_schedule_results_fresh(s2, TODAY)["status"] == C.FAIL         # 3 days overdue
    s3 = _sched(days_final=("2026-10-04",), pending=("2026-10-18",))
    assert C.check_schedule_results_fresh(s3, TODAY)["status"] == C.PASS         # future game


def test_schedule_lines_only_warns_for_games_inside_the_horizon():
    s = _sched(pending=("2026-10-18", "2026-12-20"))
    s.loc[s["game_id"] == "p0", "spread_line"] = np.nan
    s.loc[s["game_id"] == "p1", "total_line"] = np.nan                            # far future: ignored
    res = C.check_schedule_lines(s, TODAY)
    assert res["status"] == C.WARN and res["detail"]["missing_lines"] == 1


def test_expected_through_is_last_fully_final_week():
    s = pd.DataFrame([
        {"game_id": "a", "season": 2026, "week": 1, "gameday": "2026-10-04", "result": 1.0},
        {"game_id": "b", "season": 2026, "week": 2, "gameday": "2026-10-11", "result": 1.0},
        {"game_id": "c", "season": 2026, "week": 2, "gameday": "2026-10-15", "result": np.nan},   # week 2 incomplete
        {"game_id": "d", "season": 2026, "week": 3, "gameday": "2026-10-20", "result": np.nan}])
    assert C.expected_through(s, 2026, TODAY) == {"season": 2026, "week": 1}
    assert C.expected_through(s, 2026, date(2026, 9, 1)) is None


def _loader(sched, ps, ts, pbp_ids=("g0", "g1"), snap_ids=("g0", "g1")):
    def load(name, seasons=None, columns=None, strict=True):
        if name == "schedules":
            return sched
        if name == "player_stats":
            return ps
        if name == "team_stats":
            return ts
        if name == "pbp":
            return pd.DataFrame({"game_id": list(pbp_ids)})
        if name == "snap_counts":
            return pd.DataFrame({"game_id": list(snap_ids)})
        raise FileNotFoundError(name)
    return load


def test_run_contracts_clean_and_report_shape():
    ps = _ps()
    rep = C.run_contracts(2026, TODAY, loader=_loader(_sched(), ps, _ts(ps)))
    assert rep["ok"] and rep["status"] == C.PASS and rep["n_fail"] == 0
    assert rep["expected_through"] == {"season": 2026, "week": 2}
    names = {c["name"] for c in rep["checks"]}
    assert {"grain_player_stats", "reconcile_player_vs_team", "coverage_pbp", "schedule_results_fresh"} <= names


def test_run_contracts_missing_pbp_game_fails_and_snap_gap_only_warns():
    ps = _ps()
    rep = C.run_contracts(2026, TODAY, loader=_loader(_sched(), ps, _ts(ps), pbp_ids=("g0",), snap_ids=("g0",)))
    by = {c["name"]: c for c in rep["checks"]}
    assert by["coverage_pbp"]["status"] == C.FAIL and not rep["ok"]
    assert by["coverage_snap_counts"]["status"] == C.WARN and by["coverage_snap_counts"]["ok"]


def test_run_contracts_never_raises_on_unreadable_mirror():
    def boom(*a, **k):
        raise FileNotFoundError("no mirror")
    rep = C.run_contracts(2026, TODAY, loader=boom)
    assert not rep["ok"] and rep["checks"][0]["name"] == "load_core_tables"


def test_summarize_lists_only_non_passing():
    ps = _ps()
    rep = C.run_contracts(2026, TODAY, loader=_loader(_sched(), ps, _ts(ps), pbp_ids=("g0",)))
    text = C.summarize(rep)
    assert "coverage_pbp" in text and "grain_schedules" not in text
