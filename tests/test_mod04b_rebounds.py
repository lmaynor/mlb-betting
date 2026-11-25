"""Offline synthetic tests for MOD-04 B4 NBA rebounds decomposition (`nba.props.rebounds_decomp`)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nba.props import rebounds_decomp as RD
from nhl.props import mod04_features as F


def _synth_nba_frames(
    n_days: int = 18,
    start_date: str = "2026-01-05",
    include_shots: bool = False,
    seed: int = 17,
) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    teams = ["BOS", "NYK", "LAL", "DEN"]
    pairs = [(("BOS", "NYK"), ("LAL", "DEN")), (("BOS", "LAL"), ("NYK", "DEN")), (("BOS", "DEN"), ("NYK", "LAL"))]
    positions = ["PG", "SG", "SF", "PF", "C", "G", "F"]
    base_mins = [34.0, 32.0, 30.0, 31.0, 29.0, 18.0, 16.0]

    pbox, tbox, shots = [], [], []
    base = pd.Timestamp(start_date)
    gid_num = 1000
    for d in range(0, n_days, 2):
        date = (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        for home, away in pairs[(d // 2) % 3]:
            gid_num += 1
            gid = f"nba_g_{gid_num}"
            for team, opp, is_home in ((home, away, True), (away, home, False)):
                t_fga = int(rng.integers(82, 96))
                t_fg3a = int(rng.integers(28, 42))
                t_fg2a = t_fga - t_fg3a
                t_fg3m = int(rng.binomial(t_fg3a, 0.36))
                t_fg2m = int(rng.binomial(t_fg2a, 0.54))
                t_fgm = t_fg2m + t_fg3m
                t_fta = int(rng.integers(16, 26))
                t_tov = int(rng.integers(10, 17))
                t_oreb = 0
                t_dreb = 0
                player_rows_game = []
                for k in range(7):
                    pid = f"{team}_{k}"
                    m = float(np.clip(base_mins[k] + rng.normal(0, 2.0), 8.0, 42.0))
                    pos = positions[k]
                    mu_o = 2.4 if pos == "C" else (1.4 if pos in ("PF", "SF") else 0.5)
                    mu_d = 6.5 if pos == "C" else (4.5 if pos in ("PF", "SF") else 2.5)
                    oreb = int(rng.poisson(mu_o * (m / 32.0)))
                    dreb = int(rng.poisson(mu_d * (m / 32.0)))
                    t_oreb += oreb
                    t_dreb += dreb
                    mm = int(m)
                    ss = int(round((m - mm) * 60)) % 60
                    player_rows_game.append({
                        "game_id": gid,
                        "season_year": 2026,
                        "season_type": "Regular Season",
                        "date": date,
                        "team_id": teams.index(team) + 1,
                        "team_abbr": team,
                        "is_home": is_home,
                        "player_id": pid,
                        "name": f"Player {pid}",
                        "position": pos,
                        "minutes": f"{mm}:{ss:02d}",
                        "rebounds_offensive": oreb,
                        "rebounds_defensive": dreb,
                        "rebounds": oreb + dreb,
                    })
                pbox.extend(player_rows_game)
                tbox.append({
                    "game_id": gid,
                    "season_year": 2026,
                    "season_type": "Regular Season",
                    "date": date,
                    "team_id": teams.index(team) + 1,
                    "team_abbr": team,
                    "opp_abbr": opp,
                    "is_home": is_home,
                    "field_goals_attempted": t_fga,
                    "field_goals_made": t_fgm,
                    "two_points_attempted": t_fg2a,
                    "two_points_made": t_fg2m,
                    "three_points_attempted": t_fg3a,
                    "three_points_made": t_fg3m,
                    "free_throws_attempted": t_fta,
                    "turnovers": t_tov,
                    "rebounds_offensive": t_oreb,
                    "rebounds_defensive": t_dreb,
                    "rebounds": t_oreb + t_dreb,
                })
                if include_shots:
                    # Emit compact synthetic shots matching nba.shots.court schema
                    rim_n = int(t_fg2a * 0.65)
                    mid_n = t_fg2a - rim_n
                    rim_m = min(rim_n, int(t_fg2m * 0.7))
                    mid_m = t_fg2m - rim_m
                    for i in range(rim_n):
                        shots.append({
                            "game_id": gid, "date": date, "team": team, "opp": opp,
                            "three": 0, "made": int(i < rim_m), "zone": "Restricted Area|C",
                        })
                    for i in range(mid_n):
                        shots.append({
                            "game_id": gid, "date": date, "team": team, "opp": opp,
                            "three": 0, "made": int(i < mid_m), "zone": "Mid-Range|C",
                        })
                    for i in range(t_fg3a):
                        shots.append({
                            "game_id": gid, "date": date, "team": team, "opp": opp,
                            "three": 1, "made": int(i < t_fg3m), "zone": "Above the Break 3|C",
                        })

    out = {"player_box": pd.DataFrame(pbox), "team_box": pd.DataFrame(tbox)}
    if include_shots:
        out["shots"] = pd.DataFrame(shots)
    return out


def test_engine_label_and_mod02_docstring():
    assert RD.ENGINE_LABEL == "nba_rebounds_decomp_shadow_v1"
    doc = RD.__doc__ or ""
    assert "MOD-02" in doc
    assert "NOT a re-weight" in doc


def test_build_rebound_features_proxy_and_shots_schema_and_unit_renorm():
    frames_box = _synth_nba_frames(n_days=16, include_shots=False)
    feat_box = RD.build_rebound_features(frames_box)
    F.assert_feature_schema(feat_box)
    assert (feat_box["zone_source"] == "box_2pa_proxy").all()
    assert set(RD.REQUIRED_FEAT_COLS).issubset(set(feat_box.columns))
    assert not ({"rebounds", "rebounds_offensive", "rebounds_defensive", "line", "odds"} & set(feat_box.columns))

    # Teammate-share renormalization keeps expected on-floor unit shares summing to <= 1.0
    unit_rows = feat_box[feat_box["is_expected_unit"] == 1]
    assert not unit_rows.empty
    oreb_unit_sums = unit_rows.groupby(["game_id", "team"])["renorm_oreb_share"].sum()
    dreb_unit_sums = unit_rows.groupby(["game_id", "team"])["renorm_dreb_share"].sum()
    assert (oreb_unit_sums <= 1.0 + 1e-9).all()
    assert (dreb_unit_sums <= 1.0 + 1e-9).all()
    assert np.allclose(oreb_unit_sums, 1.0, atol=1e-9)
    assert np.allclose(dreb_unit_sums, 1.0, atol=1e-9)

    # With shots parquet schema -> uses shots_parquet zone source
    frames_sh = _synth_nba_frames(n_days=16, include_shots=True)
    feat_sh = RD.build_rebound_features(frames_sh)
    F.assert_feature_schema(feat_sh)
    assert (feat_sh["zone_source"] == "shots_parquet").all()


def test_convolution_correctness_hand_computed_example():
    # Tiny exact hand-computed convolution:
    # A ~ {0: 0.5, 1: 0.5}, B ~ {0: 0.2, 1: 0.5, 2: 0.3}
    # P(A+B = 0) = 0.5*0.2 = 0.10
    # P(A+B = 1) = 0.5*0.5 + 0.5*0.2 = 0.35
    # P(A+B = 2) = 0.5*0.3 + 0.5*0.5 = 0.40
    # P(A+B = 3) = 0.5*0.3 = 0.15
    pmf_a = np.array([0.5, 0.5])
    pmf_b = np.array([0.2, 0.5, 0.3])
    conv = RD.convolve_pmfs(pmf_a, pmf_b)
    np.testing.assert_allclose(conv, [0.10, 0.35, 0.40, 0.15], atol=1e-12)
    assert conv.sum() == pytest.approx(1.0, abs=1e-12)

    # Verify model.predict_pmf with sd_min=0 matches exact convolution of OREB and DREB NegBin PMFs
    mdl = RD.ReboundsDecompModel(kmax=20)
    mdl.global_share_mult = 1.0
    mdl.oreb_mult = 1.0
    mdl.dreb_mult = 1.0
    mdl.alpha_oreb = 0.10
    mdl.alpha_dreb = 0.05
    probe = pd.DataFrame([{
        "mu_min": 48.0,
        "sd_min": 0.0,
        "emsa_own_rim": 20.0,
        "emsa_own_long": 20.0,
        "emsa_opp_rim": 20.0,
        "emsa_opp_long": 20.0,
        "renorm_oreb_share": 0.25,
        "renorm_dreb_share": 0.30,
        "opp_oreb_rate": RD.LG_OREB_RATE,
        "opp_dreb_rate": RD.LG_DREB_RATE,
    }])
    mu_o, mu_d = mdl.predict_components(probe)
    expected_o = RD._nb_pmf_grid(mu_o, mdl.alpha_oreb, 20)[0]
    expected_d = RD._nb_pmf_grid(mu_d, mdl.alpha_dreb, 20)[0]
    expected_conv = RD.convolve_pmfs(expected_o, expected_d, kmax=20)
    got_pmf = mdl.predict_pmf(probe, kmax=20)[0]
    np.testing.assert_allclose(got_pmf, expected_conv, atol=1e-10)


def test_rebounds_model_fit_pmf_sums_to_one_and_p_over_monotone():
    frames = _synth_nba_frames(n_days=20, include_shots=True)
    feat = RD.build_rebound_features(frames)
    labels = frames["player_box"][["game_id", "player_id", "rebounds", "rebounds_offensive", "rebounds_defensive"]].copy()
    train_df = F.join_labels(feat, labels)

    model = RD.ReboundsDecompModel(kmax=25).fit(train_df, target="rebounds")
    assert model.fitted is True
    pmfs = model.predict_pmf(feat.head(15), kmax=25)
    assert pmfs.shape == (15, 26)
    assert (pmfs >= 0.0).all()
    assert np.allclose(pmfs.sum(axis=1), 1.0, atol=1e-9, rtol=0.0)

    # Total-only fallback fit (without OREB/DREB split in training frame)
    train_tot = train_df.drop(columns=["rebounds_offensive", "rebounds_defensive"])
    model_tot = RD.ReboundsDecompModel(kmax=25).fit(train_tot, target="rebounds")
    assert np.isclose(model_tot.predict_mu(feat).mean(), train_tot["rebounds"].mean(), rtol=0.05)

    # p_over monotone decreasing
    lines = [2.5, 4.5, 6.5, 8.5, 10.5]
    row0 = feat.iloc[[0]]
    probs = [float(model.p_over(row0, ln, kmax=25)[0]) for ln in lines]
    assert all(probs[i] >= probs[i + 1] for i in range(len(probs) - 1))
    assert probs[0] > probs[-1]
