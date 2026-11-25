"""Tests for MOD-04 Cluster (e) E3 live shadow wiring."""
import json
import pytest

from nfl.mod04e import model as M
from nfl.mod04e import shadow as S
from nfl.mod04e.inactives import InactiveRecord
from tests.test_mod04e_model import _synthetic_training_frame


def test_shadow_dual_fields_and_inactive_rescore_handling(tmp_path):
    train_df, train_labels = _synthetic_training_frame(n=90)
    mdl_rec = M.fit(train_df, train_labels, "receptions")

    slate = train_df.iloc[:2].copy().reset_index(drop=True)
    slate["player_id"] = ["P_ACTIVE", "P_SCRATCH"]
    slate["player_name"] = ["Active WR", "Scratch WR"]
    slate["team"] = ["KC", "KC"]
    slate["opponent"] = ["BUF", "BUF"]
    slate["game_id"] = ["2026_13_BUF_KC", "2026_13_BUF_KC"]
    slate["game_date"] = ["2026-11-29", "2026-11-29"]

    lines = {
        ("P_ACTIVE", "receptions"): {"line": 4.5, "p_market": 0.54},
        ("P_SCRATCH", "receptions"): {"line": 4.5, "p_market": 0.51},
    }
    inactives = [
        InactiveRecord(
            player="Scratch WR",
            team="KC",
            game="2026_13_BUF_KC",
            status="INA",
            source="live_feed",
            retrieved_at_utc="2026-11-29T16:30:00Z",
            player_id="P_SCRATCH",
        )
    ]
    kickoff_map = {"2026_13_BUF_KC": "2026-11-29T18:00:00Z"}

    # 1. thu run: does NOT apply game-day inactives
    thu_rows = S.score_slate(
        slate,
        {"receptions": mdl_rec},
        lines,
        run_kind="thu",
        inactives=inactives,
        kickoff_utc_by_game=kickoff_map,
    )
    assert len(thu_rows) == 2
    for r in thu_rows:
        assert r["engine"] == "mod04_nfl_gamescript_shadow_v1"
        assert r["run_kind"] == "thu"
        assert r["blend_c"] == 0.00
        assert 0.0 < r["p_model"] < 1.0
        assert r["p_served"] == pytest.approx(r["p_market"], abs=1e-9)
        assert r["inactive_scratch"] is False
        assert r["gradeable"] is True

    # 2. sun_90m_rescore run: flags inactive player, keeps logged, excludes from grading
    rescore_rows = S.score_slate(
        slate,
        {"receptions": mdl_rec},
        lines,
        run_kind="sun_90m_rescore",
        inactives=inactives,
        kickoff_utc_by_game=kickoff_map,
    )
    assert len(rescore_rows) == 2
    by_pid = {r["player_id"]: r for r in rescore_rows}
    assert by_pid["P_ACTIVE"]["inactive_scratch"] is False
    assert by_pid["P_ACTIVE"]["gradeable"] is True
    assert by_pid["P_ACTIVE"]["p_served"] == pytest.approx(0.54, abs=1e-9)

    assert by_pid["P_SCRATCH"]["inactive_scratch"] is True
    assert by_pid["P_SCRATCH"]["gradeable"] is False
    assert by_pid["P_SCRATCH"]["p_model"] == 0.0
    assert by_pid["P_SCRATCH"]["p_served"] == pytest.approx(0.51, abs=1e-9)

    # 3. write_shadow writes NFL/mod04e/shadow/<date>/<run_kind>.json
    out_path = S.write_shadow(rescore_rows, tmp_path, "2026-11-29", "sun_90m_rescore")
    assert out_path.endswith("NFL/mod04e/shadow/2026-11-29/sun_90m_rescore.json")
    payload = json.loads(open(out_path).read())
    assert payload["engine"] == "mod04_nfl_gamescript_shadow_v1"
    assert payload["run_kind"] == "sun_90m_rescore"
    assert payload["count"] == 2
