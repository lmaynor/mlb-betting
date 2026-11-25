"""MOD-01 saves train/serve parity regression tests for ev.daily_projections.make_row.

Trained NHL saves rows must serve the raw trained NegBin probability (`p_over_model == saves_p_model_raw`)
matching the paper track, while still computing and stamping `saves_p_hardened` and all officials-gate stamps.
"""
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from ev import daily_projections as dp
from ev.props import Quote, build_consensus
from mlb_core.data import cockpit_form as cf
from nhl.props import hierarchical as H
from nhl.props import saves_hardening as SH
from tests.test_nhl_props_hierarchical import AS_OF, START
from tests.test_nhl_trained_board import PIDS, logs_with_ids


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(dp._rosters, "load", lambda sport, now=None, **k: None)
    monkeypatch.setattr(dp, "write_archive", lambda payload, now: [])


def _saves_history_with_bias_and_calibration(as_of: str = AS_OF, n_days: int = 40, per_day: int = 3,
                                             resid: float = -0.35, seed: int = 7) -> list[dict]:
    """Settled saves history strictly before `as_of` with >= CALIB_MIN_N rows and nonzero centering residual."""
    rng = np.random.default_rng(seed)
    end = date.fromisoformat(as_of)
    start = end - timedelta(days=n_days)
    out = []
    for k in range(n_days):
        day = (start + timedelta(days=k)).isoformat()
        for _ in range(per_day):
            pm = float(rng.uniform(0.38, 0.62))
            pe = SH._clip_prob(SH.sigmoid(SH.logit(pm) + resid + float(rng.normal(0.0, 0.05))))
            true = SH.sigmoid(SH.logit(pm) + 0.35 * (SH.logit(pe) - SH.logit(pm)))
            out.append({"date": day, "p_model": pe, "p_mkt": pm, "won_over": float(rng.uniform() < true)})
    return out


def _saves_quotes(player: str = "Gus Goalie", line: float = 25.5) -> list[Quote]:
    pn = cf.norm_name(player)
    books = (("draftkings", -115, -105), ("fanduel", -112, -108), ("betmgm", -110, -110), ("caesars", -114, -106))
    return [Quote(b, "saves", False, player, pn, "TOR", line, ov, un, None) for b, ov, un in books]


def test_trained_saves_serves_raw_probability_and_stamps_hardened():
    mu, alpha, line = 24.4, 0.04, 25.5
    served = {(PIDS["Gus Goalie"], AS_OF, "saves"): (mu, alpha, "v1")}
    logs = logs_with_ids(served)
    history = _saves_history_with_bias_and_calibration(AS_OF)
    logs.set_saves_history(history)

    state = logs.saves_state(AS_OF)
    assert state is not None
    assert state.centering.applied is True
    assert abs(state.centering.residual_mean) > 0.1
    assert state.calibration is not None

    quotes = _saves_quotes("Gus Goalie", line)
    cons = build_consensus(quotes)[(cf.norm_name("Gus Goalie"), "saves", line)]
    raw_p = H.NegBinDist(mu, alpha).p_over(line)
    hardened = state.harden(raw_p, cons.p_over)
    assert abs(hardened["p_final"] - raw_p) > 0.02

    table, sdef = logs.table_and_def("saves")
    row = dp.make_row(
        "nhl", "saves", "Gus Goalie", cf.norm_name("Gus Goalie"),
        table[cf.norm_name("Gus Goalie")], sdef, logs, line, cons, quotes,
        "TOR", "BOS", f"nhl:{AS_OF}:TOR@BOS", START, 60.0,
    )
    assert row is not None
    assert row["nhl_model"] == "trained_v1"
    assert row["engine"] == "nhl_trained_v1"

    # Served probability must be the RAW trained probability, NOT the hardened value.
    assert row["saves_p_model_raw"] == dp._f(raw_p)
    assert row["saves_p_hardened"] == dp._f(hardened["p_final"])
    assert row["saves_p_hardened"] != row["saves_p_model_raw"]
    assert row["p_over_model"] == row["saves_p_model_raw"]
    assert row["p_over_model"] != row["saves_p_hardened"]

    # Downstream fields (edges, side, blend, gate median edge) must all be computed from the RAW probability.
    ef = dp.edge_fields(raw_p, cons.p_over, dp.best_prices(quotes), cons.n_books)
    expected_side_p = raw_p if ef["side"] == "over" else 1.0 - raw_p
    expected_blend = dp.blend_p_over(raw_p, cons.p_over, sport="nhl", stat="saves", trained=True)
    assert row["side"] == ef["side"]
    assert row["model_edge"] == dp._f(ef["model_edge"])
    assert row["p_model_side"] == dp._f(expected_side_p)
    assert row["p_over_blend"] == dp._f(expected_blend)
    assert row["saves_median_edge"] == round(float(ef["model_edge"]), 6)
    assert row["saves_median_n_books"] == cons.n_books

    # Existing hardening and officials-gate stamps remain intact.
    assert row["saves_p_centered"] == dp._f(hardened["p_centered"])
    assert row["saves_center_residual"] == dp._f(hardened["center_residual"])
    assert row["saves_center_n"] == hardened["center_n"]
    assert row["saves_calibrated"] is True
    for key in SH.ROW_STAMPS:
        assert key in row


def test_trained_saves_without_history_stamps_saves_p_hardened_equal_to_raw():
    mu, alpha, line = 26.4, 0.04, 25.5
    logs = logs_with_ids({(PIDS["Gus Goalie"], AS_OF, "saves"): (mu, alpha, "v1")})
    raw_p = H.NegBinDist(mu, alpha).p_over(line)
    table, sdef = logs.table_and_def("saves")
    row = dp.make_row(
        "nhl", "saves", "Gus Goalie", cf.norm_name("Gus Goalie"),
        table[cf.norm_name("Gus Goalie")], sdef, logs, line, None, [],
        "TOR", "BOS", f"nhl:{AS_OF}:TOR@BOS", START, 60.0,
    )
    assert row is not None
    assert row["p_over_model"] == row["saves_p_model_raw"] == dp._f(raw_p)
    assert row["saves_p_hardened"] == row["saves_p_model_raw"]
    assert row["saves_center_n"] == 0
