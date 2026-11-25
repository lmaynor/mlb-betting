"""Tests for mod04.backbone_nba — injury redistribution, Out player handling, and arm_s caps."""
from __future__ import annotations

import pandas as pd

from mod04.backbone_nba import compute_minutes_backbone


def _prior_history() -> pd.DataFrame:
    rows = []
    dates = [f"2026-11-{d:02d}" for d in range(1, 13, 2)]
    for d in dates:
        for pid, mins, st in [
            ("star", 34.0, True),
            ("backup", 20.0, False),
            ("wing", 28.0, True),
            ("big", 30.0, True),
        ]:
            rows.append({
                "player_id": pid,
                "team": "BOS",
                "date": d,
                "minutes": mins,
                "starter": st,
            })
    return pd.DataFrame(rows)


def test_out_teammate_lifts_backup_and_out_player_gets_zero():
    prior = _prior_history()
    slate = pd.DataFrame([
        {"player_id": "star", "player_norm": "star", "team": "BOS", "spread": -3.0, "rest_days": 1, "pf_per36": 2.4},
        {"player_id": "backup", "player_norm": "backup", "team": "BOS", "spread": -3.0, "rest_days": 1, "pf_per36": 2.4},
        {"player_id": "wing", "player_norm": "wing", "team": "BOS", "spread": -3.0, "rest_days": 1, "pf_per36": 2.4},
    ])

    healthy = compute_minutes_backbone(prior, slate, target_date="2026-11-15")
    with_out = compute_minutes_backbone(
        prior,
        slate,
        absences_by_team={"BOS": {"star": "Out"}},
        status_by_player={("BOS", "star"): "Out"},
        target_date="2026-11-15",
    )

    assert len(with_out) == 3  # Out player row still emitted for shadow completeness
    assert with_out.loc[0, "is_player_out"] == 1
    assert with_out.loc[0, "mu_min"] == 0.0
    assert with_out.loc[0, "sigma_min"] == 0.0

    # Backup and wing absorb vacated minutes
    assert with_out.loc[1, "mu_min"] > healthy.loc[1, "mu_min"]
    assert with_out.loc[1, "absorbed_min_delta"] > 0.0
    assert with_out.loc[1, "vacated_min_team"] > 0.0
    assert with_out.loc[1, "teammate_out_count"] == 1


def test_arm_s_cap_and_blowout_b2b_trim():
    prior = _prior_history()
    slate = pd.DataFrame([
        {"player_id": "star", "player_norm": "star", "team": "BOS", "spread": -3.0, "rest_days": 2, "pf_per36": 2.4},
    ])
    uncapped = compute_minutes_backbone(prior, slate, target_date="2026-11-15")
    capped = compute_minutes_backbone(
        prior,
        slate,
        arm_s_caps={("BOS", "star"): {"cap_ratio": 0.72, "cap_minutes": None, "is_injury_return": True}},
        target_date="2026-11-15",
    )

    assert capped.loc[0, "min_cap_flag"] == 1
    assert capped.loc[0, "is_injury_return_flag"] == 1
    assert capped.loc[0, "mu_min"] < uncapped.loc[0, "mu_min"]
    assert capped.loc[0, "sigma_min"] < uncapped.loc[0, "sigma_min"]

    b2b_blowout = pd.DataFrame([
        {"player_id": "star", "player_norm": "star", "team": "BOS", "spread": -18.0, "rest_days": 0, "pf_per36": 2.4},
    ])
    trimmed = compute_minutes_backbone(prior, b2b_blowout, target_date="2026-11-15")
    assert trimmed.loc[0, "mu_min"] < uncapped.loc[0, "mu_min"]
