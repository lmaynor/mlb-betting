"""The board's use of served trained-model predictions (ev.daily_projections.load_trained / project_nhl): used only for the right
player-game, ignored when stale, exact additivity of points, and no change at all when nothing is served."""
import json
import math
from datetime import datetime, timedelta, timezone

import pytest
from scipy.stats import nbinom

from ev import daily_projections as dp
from mlb_core.data import cockpit_form as cf
from nhl.props import hierarchical as H
from tests.test_nhl_props_hierarchical import AS_OF, make_logs, row_for

NOW = datetime(2026, 2, 4, 16, 0, tzinfo=timezone.utc)
PIDS = {"Ann Forward": 701, "Dan Defense": 702, "Gus Goalie": 900}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(dp._rosters, "load", lambda sport, now=None, **k: None)
    monkeypatch.setattr(dp, "write_archive", lambda payload, now: [])


def logs_with_ids(served=None):
    logs = make_logs()
    for nm, pid in PIDS.items():
        for tbl in ("skater", "goalie"):
            for r in logs.idx[tbl].get(cf.norm_name(nm), []):
                r["player_id"] = pid
    if served is not None:
        logs.set_trained(served)
    return logs


def test_served_prediction_replaces_the_empirical_mean_and_prices_with_its_own_negbin():
    base = row_for(logs_with_ids(), "goals", "Ann Forward")
    served = {(701, AS_OF, "goals"): (0.41, 0.35, "v7")}
    r = row_for(logs_with_ids(served), "goals", "Ann Forward")
    assert r["nhl_model"] == "trained_v1" and r["trained_version"] == "v7" and r["proj_mean"] == pytest.approx(0.41)
    n = 1 / 0.35
    assert r["p_over_model"] == pytest.approx(1 - nbinom.cdf(0, n, n / (n + 0.41)), abs=1e-4)
    assert base["nhl_model"] == "hierarchical_v1" and base["proj_mean"] != r["proj_mean"]
    assert r["proj_p10"] <= r["proj_p90"]


def test_a_prediction_for_another_date_or_player_or_stat_is_ignored():
    base = row_for(logs_with_ids(), "goals", "Ann Forward")
    for key in ((701, "2026-02-05", "goals"), (999, AS_OF, "goals"), (701, AS_OF, "assists")):
        r = row_for(logs_with_ids({key: (0.9, 0.3, "v1")}), "goals", "Ann Forward")
        assert r["nhl_model"] == "hierarchical_v1" and r["proj_mean"] == base["proj_mean"]


def test_nothing_served_changes_nothing():
    a, b = row_for(logs_with_ids(), "goals", "Ann Forward"), row_for(logs_with_ids({}), "goals", "Ann Forward")
    assert a == b


def test_served_saves_and_points_stay_additive():
    served = {(701, AS_OF, "goals"): (0.30, 0.4, "v1"), (701, AS_OF, "assists"): (0.55, 0.3, "v1"),
              (701, AS_OF, "points"): (0.85, 0.2, "v1"), (900, AS_OF, "saves"): (26.4, 0.04, "v1")}
    logs = logs_with_ids(served)
    g, a, p = (row_for(logs, s, "Ann Forward")["proj_mean"] for s in ("goals", "assists", "points"))
    assert p == pytest.approx(g + a, abs=1.1e-3)
    s = row_for(logs, "saves", "Gus Goalie", line=25.5)
    assert s["nhl_model"] == "trained_v1" and s["proj_mean"] == pytest.approx(26.4) and 0 < s["p_over_model"] < 1


def test_served_prediction_needs_a_real_history():
    logs = logs_with_ids({(701, AS_OF, "goals"): (0.4, 0.3, "v1")})
    rows = logs.idx["skater"][cf.norm_name("Ann Forward")][:2]
    sdef = cf.NHL_SKATER_STATS["goals"]
    assert dp.project_nhl("goals", rows, sdef, logs, "TOR", "BOS", AS_OF)[0] is None


def test_negbin_dist_matches_scipy_and_handles_poisson():
    d = H.NegBinDist(2.0, 0.5)
    n = 2.0
    assert d.p_over(2.5) == pytest.approx(1 - nbinom.cdf(2, n, n / (n + 2.0)))
    assert d.ppf(0.5) == nbinom.ppf(0.5, n, n / (n + 2.0))
    assert H.NegBinDist(1.2, 0.0).p_over(0.5) == pytest.approx(1 - math.exp(-1.2))


def test_load_trained_reads_fresh_files_and_ignores_stale_or_broken_ones(monkeypatch):
    import mlb_core.storage as st
    rows = [{"player_id": 701, "date": AS_OF, "stat": "goals", "mu": 0.4, "alpha": 0.3}]
    def serve(age_h):
        ts = (NOW - timedelta(hours=age_h)).isoformat()
        monkeypatch.setattr(st, "read_bytes", lambda key: json.dumps({"ts": ts, "version": "v9", "rows": rows}).encode())
    serve(2.0)
    assert dp.load_trained(NOW) == {(701, AS_OF, "goals"): (0.4, 0.3, "v9")}
    serve(40.0)
    assert dp.load_trained(NOW) == {}
    monkeypatch.setattr(st, "read_bytes", lambda key: (_ for _ in ()).throw(FileNotFoundError(key)))
    assert dp.load_trained(NOW) == {}


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("NHL_TRAINED_PROPS", raising=False)
    assert dp.trained_enabled() is False
    monkeypatch.setenv("NHL_TRAINED_PROPS", "1")
    assert dp.trained_enabled() is True


def test_run_sport_installs_served_predictions_only_when_flagged(monkeypatch):
    calls = []
    monkeypatch.setattr(dp, "load_trained", lambda now=None: calls.append(1) or {})
    logs = logs_with_ids()
    monkeypatch.delenv("NHL_TRAINED_PROPS", raising=False)
    dp.run_sport("nhl", NOW, logs=logs, events=[], meta={}, games=[], write=False, roster=None)
    assert calls == []
    monkeypatch.setenv("NHL_TRAINED_PROPS", "1")
    dp.run_sport("nhl", NOW, logs=logs, events=[], meta={}, games=[], write=False, roster=None)
    assert calls == [1]
    nfl = dp.GameLogs("nfl", {"all": {}})
    assert nfl.trained_pred(1, AS_OF, "goals") is None
