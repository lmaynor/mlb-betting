"""Offline synthetic tests for MOD-04 B3 NHL skater SOG shadow model (`nhl.props.sog_trained`)."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nhl.props import mod04_features as F
from nhl.props import mod04_nhl_features as NF
from nhl.props import sog_trained as ST


def _synth_nhl_frames(
    n_days: int = 24,
    start_date: str = "2026-11-05",
    seed: int = 11,
) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    pairs = [((1, 2), (3, 4)), ((1, 3), (2, 4)), ((1, 4), (2, 3))]
    abbr = {1: "AAA", 2: "BBB", 3: "CCC", 4: "DDD"}
    games, sbox, gbox = [], [], []
    base = pd.Timestamp(start_date)
    gid = 2026020000
    for d in range(0, n_days, 2):
        date = (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        for home, away in pairs[(d // 2) % 3]:
            gid += 1
            games.append({
                "game_id": gid,
                "season_year": 2026,
                "game_type": 2,
                "date": date,
                "home_id": home,
                "away_id": away,
                "home_abbr": abbr[home],
                "away_abbr": abbr[away],
                "home_score": 3,
                "away_score": 2,
                "home_sog": 29,
                "away_sog": 27,
            })
            for team, is_home in ((home, 1), (away, 0)):
                gbox.append({
                    "game_id": gid, "season_year": 2026, "game_type": 2, "date": date,
                    "team_id": team, "team_abbr": abbr[team], "is_home": bool(is_home),
                    "player_id": team * 10 + 1, "name": f"Goalie {team}", "toi_sec": 3600.0,
                    "starter": True, "shots_against": 28, "saves": 25, "goals_against": 3,
                })
                for k in range(5):
                    pid = team * 100 + k
                    toi_sec = float(rng.integers(750, 1450))
                    att = int(rng.poisson(4.2))
                    sog = int(rng.binomial(att, 0.56))
                    sbox.append({
                        "game_id": gid, "season_year": 2026, "game_type": 2, "date": date,
                        "team_id": team, "team_abbr": abbr[team], "is_home": bool(is_home),
                        "player_id": pid, "name": f"Skater {pid}",
                        "position": "D" if k < 2 else "C",
                        "toi_sec": toi_sec, "goals": int(sog > 2), "assists": 0,
                        "points": int(sog > 2), "sog": sog, "attempts": att,
                    })
    return {
        "games": pd.DataFrame(games),
        "skater_box": pd.DataFrame(sbox),
        "goalie_box": pd.DataFrame(gbox),
    }


def test_shadow_contract_and_no_serving_imports():
    assert ST.ENGINE_LABEL == "nhl_sog_trained_v1"
    doc = ST.__doc__ or ""
    assert "Novig" in doc and "refusal" in doc

    src = Path(ST.__file__).read_text()
    tree = ast.parse(src)
    forbidden = ("daily_projections", "paper_track", "serve_trained", "nhl.props.trained")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(f in alias.name for f in forbidden)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not any(f in mod for f in forbidden)


def test_build_sog_features_paths_schema_and_rotowire_span():
    frames = _synth_nhl_frames(n_days=26, start_date="2026-11-08")
    dates = sorted(frames["games"]["date"].unique())
    rw = pd.DataFrame([
        {"date": d, "player_id": 102, "pp_unit": 1}
        for d in dates
    ])

    # Path 1: full assembly via mod04_nhl_features
    feat_full = ST.build_sog_features(frames, rotowire=rw)
    F.assert_feature_schema(feat_full)
    assert set(ST.REQUIRED_FEAT_COLS).issubset(set(feat_full.columns))
    assert not ({"sog", "attempts", "line", "odds", "p_market"} & set(feat_full.columns))

    in_span = NF._in_rotowire_span(feat_full["date"])
    assert in_span.any() and (~in_span).any()
    assert feat_full.loc[~in_span, "rotowire_pp_unit"].isna().all()
    assert feat_full.loc[in_span & (feat_full["player_id"] == 102), "rotowire_pp_unit"].notna().all()

    # Path 2: pre-built skater frame from mod04_nhl_features
    vol = NF.build_nhl_volume_features(frames, rotowire=rw)
    feat_prebuilt = ST.build_sog_features({"skater": vol["skater"]})
    pd.testing.assert_frame_equal(feat_full, feat_prebuilt)

    # Path 3: skater_box-only direct fallback
    feat_direct = ST.build_sog_features({"skater_box": frames["skater_box"]}, rotowire=rw)
    F.assert_feature_schema(feat_direct)
    assert set(ST.REQUIRED_FEAT_COLS).issubset(set(feat_direct.columns))
    assert len(feat_direct) == len(feat_full)


def test_sog_pmf_sums_to_one_toi_uncertainty_increases_variance_and_p_over_monotone():
    frames = _synth_nhl_frames(n_days=30, seed=99)
    feat = ST.build_sog_features(frames)
    labels = frames["skater_box"][["game_id", "player_id", "sog"]].copy()
    train_df = F.join_labels(feat, labels)

    model = ST.SogStructuralModel(kmax=15).fit(train_df, target="sog")
    assert model.fitted is True
    assert model.att_mult > 0 and model.ontarget_mult > 0

    # Calibration check: mean predicted mu matches mean actual on training rows
    mu_pred = model.predict_mu(feat)
    assert np.isclose(mu_pred.mean(), train_df["sog"].mean(), rtol=0.05)

    # PMF sums to 1 over 0..kmax
    pmfs = model.predict_pmf(feat.head(20), kmax=15)
    assert pmfs.shape == (20, 16)
    assert (pmfs >= 0.0).all()
    assert np.allclose(pmfs.sum(axis=1), 1.0, atol=1e-9, rtol=0.0)

    # TOI-uncertainty integration increases PMF variance vs zero-sd point TOI
    probe = pd.DataFrame({
        "exp_toi_min": [18.0, 18.0],
        "exp_toi_sd": [0.0, 4.5],
        "att_rate_per_min": [0.22, 0.22],
        "ontarget_rate": [0.60, 0.60],
    })
    probe_pmfs = model.predict_pmf(probe, kmax=25)
    k = np.arange(26, dtype=float)
    mean0 = float((probe_pmfs[0] * k).sum())
    mean1 = float((probe_pmfs[1] * k).sum())
    var0 = float((probe_pmfs[0] * (k - mean0) ** 2).sum())
    var1 = float((probe_pmfs[1] * (k - mean1) ** 2).sum())
    assert var1 > var0

    # p_over monotone decreasing in line and matches PMF tail sum
    lines = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]
    row0 = feat.iloc[[0]]
    probs = [float(model.p_over(row0, ln, kmax=15)[0]) for ln in lines]
    assert all(probs[i] >= probs[i + 1] for i in range(len(probs) - 1))
    assert probs[0] > probs[-1]
    pmf0 = model.predict_pmf(row0, kmax=15)[0]
    assert probs[2] == pytest.approx(float(pmf0[3:].sum()), abs=1e-12)
