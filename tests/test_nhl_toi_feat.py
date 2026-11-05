"""Prior-only TOI-split features (nhl.props.toi_feat) and the paired walk-forward scorer (nhl.props.eval_toi_splits): the leakage contract,
NaN handling and the adoption-bar arithmetic. Offline, synthetic."""
import numpy as np
import pandas as pd
import pytest

from nhl.props import eval_toi_splits as E
from nhl.props.toi_feat import MIN_TEAM_PP_S, TOI_FEATURES, toi_features


def synth(n_players=4, n_games=40, seed=0):
    """(base, splits): each player appears once per 2 days; PP time / unit vary game to game."""
    rng = np.random.default_rng(seed)
    base, rows = [], []
    d0 = pd.Timestamp("2024-10-10")
    for p in range(1, n_players + 1):
        for g in range(n_games):
            gid = 2024020000 + g * 10 + p
            base.append({"game_id": gid, "player_id": p, "date": (d0 + pd.Timedelta(days=2 * g)).strftime("%Y-%m-%d")})
            pp = int(rng.integers(0, 200))
            tp = int(rng.integers(60, 400))
            rows.append({"game_id": gid, "player_id": p, "toi_total": 1000 + int(rng.integers(0, 300)), "toi_ev": 800 + int(rng.integers(0, 200)),
                         "toi_pp": pp, "toi_pk": int(rng.integers(0, 100)), "team_pp_s": tp, "pp_share": min(1.0, pp / tp),
                         "pp_unit_guess": 1 if pp / tp >= 0.5 else (2 if pp >= 10 else 0)})
    return pd.DataFrame(base), pd.DataFrame(rows)


def test_features_are_the_mean_of_strictly_earlier_games():
    base, sp = synth()
    f = toi_features(base, sp)
    m = base.merge(sp, on=["game_id", "player_id"]).sort_values(["player_id", "date"])
    for p in (1, 3):
        mp = m[m.player_id == p].reset_index(drop=True)
        fp = f.merge(base, on=["game_id", "player_id"])
        fp = fp[fp.player_id == p].sort_values("date").reset_index(drop=True)
        assert np.isnan(fp.loc[0, "toi_pp_l10"])                                          # no earlier game
        assert fp.loc[1, "toi_pp_l10"] == pytest.approx(mp.loc[0, "toi_pp"] / 60.0)       # only game 0
        assert fp.loc[15, "toi_pp_l10"] == pytest.approx(mp.loc[5:14, "toi_pp"].mean() / 60.0)   # games 5..14, never game 15
        assert fp.loc[15, "toi_ev_l10"] == pytest.approx(mp.loc[5:14, "toi_ev"].mean() / 60.0)
        assert fp.loc[25, "pp_frac_l20"] == pytest.approx(mp.loc[5:24, "toi_pp"].sum() / mp.loc[5:24, "toi_total"].sum())


def test_scrambling_outcomes_on_or_after_t_leaves_every_feature_at_t_unchanged():
    base, sp = synth(seed=1)
    f0 = toi_features(base, sp).merge(base, on=["game_id", "player_id"])
    t = "2024-11-20"
    rng = np.random.default_rng(9)
    sp2 = sp.merge(base, on=["game_id", "player_id"])
    late = sp2["date"] >= t
    for c in ("toi_total", "toi_ev", "toi_pp", "toi_pk", "team_pp_s", "pp_share", "pp_unit_guess"):
        v = sp2.loc[late, c].to_numpy().copy()
        sp2.loc[late, c] = rng.permutation(v) * 3 + 7                                    # destroy them: scrambled AND rescaled
    f1 = toi_features(base, sp2.drop(columns="date")).merge(base, on=["game_id", "player_id"])
    a, b = f0[f0["date"] <= t].sort_values(["player_id", "date"]), f1[f1["date"] <= t].sort_values(["player_id", "date"])
    assert len(a) > 50
    pd.testing.assert_frame_equal(a[TOI_FEATURES].reset_index(drop=True), b[TOI_FEATURES].reset_index(drop=True))
    # and the scramble DID change later rows (the test is not vacuous)
    la, lb = f0[f0["date"] > "2024-11-28"], f1[f1["date"] > "2024-11-28"]
    assert not np.allclose(la[TOI_FEATURES].fillna(-1).to_numpy(), lb[TOI_FEATURES].fillna(-1).to_numpy())


def test_a_row_never_sees_its_own_split():
    base, sp = synth(n_players=1, n_games=15)
    f0 = toi_features(base, sp)
    sp2 = sp.copy()
    sp2.loc[sp2.index[7], ["toi_pp", "toi_ev", "toi_total"]] = [10 ** 6, 10 ** 6, 2 * 10 ** 6]            # blow up game 7 only
    f1 = toi_features(base, sp2)
    d = base.reset_index(drop=True)
    g7 = d.loc[7, "game_id"]
    r0, r1 = f0.set_index("game_id").loc[g7], f1.set_index("game_id").loc[g7]
    assert (r0.fillna(-1) == r1.fillna(-1)).all()                                    # game 7's own features unchanged
    g8 = d.loc[8, "game_id"]
    assert f0.set_index("game_id").loc[g8, "toi_pp_l10"] != f1.set_index("game_id").loc[g8, "toi_pp_l10"]   # but game 8 sees it


def test_missing_splits_stay_nan_and_are_not_zero_filled():
    base, sp = synth(n_players=1, n_games=14)
    sp_missing = sp.iloc[[0, 1, 2]]                                       # only the first three games have splits
    f = toi_features(base, sp_missing).merge(base, on=["game_id", "player_id"]).sort_values("date").reset_index(drop=True)
    assert f.loc[10, "toi_pp_l10"] == pytest.approx(sp_missing["toi_pp"].mean() / 60.0)   # NaN games are skipped, not zero
    empty = toi_features(base, sp.iloc[:0])
    assert empty[TOI_FEATURES].isna().all().all() and len(empty) == len(base)


def test_placeholder_rows_for_unplayed_games_get_features_through_the_same_path():
    base, sp = synth(n_players=1, n_games=12)
    ph = pd.DataFrame({"game_id": [9_000_000_001], "player_id": [1], "date": ["2025-02-01"]})
    f = toi_features(pd.concat([base, ph], ignore_index=True), sp)
    r = f[f.game_id == 9_000_000_001].iloc[0]
    assert r["toi_pp_l10"] == pytest.approx(sp.sort_values("game_id").tail(10)["toi_pp"].mean() / 60.0)


def test_pp1_share_ignores_games_where_the_team_had_no_real_power_play():
    base, sp = synth(n_players=1, n_games=6)
    sp["team_pp_s"] = [MIN_TEAM_PP_S - 1] * 3 + [100] * 3
    sp["pp_unit_guess"] = [1, 1, 1, 1, 2, 0]
    f = toi_features(base, sp).merge(base, on=["game_id", "player_id"]).sort_values("date").reset_index(drop=True)
    assert np.isnan(f.loc[3, "pp1_share_l10"])                                        # games 0-2 had no PP to deploy on
    assert f.loc[5, "pp1_share_l10"] == pytest.approx(0.5)                            # games 3 (PP1) and 4 (PP2)


# ---------------------------------------------------------------------------------------------------------------------
def test_adoption_bar_arithmetic():
    assert E.adopt_z(4) == pytest.approx(2.498, abs=0.01) and E.adopt_z(6) == pytest.approx(2.638, abs=0.01)


def test_cluster_se_is_larger_than_naive_when_rows_in_a_game_move_together():
    rng = np.random.default_rng(0)
    g = np.repeat(np.arange(200), 10)
    x = np.repeat(rng.normal(size=200), 10) + rng.normal(scale=0.1, size=2000)
    m, se = E.cluster_mean_se(x, g)
    assert m == pytest.approx(x.mean()) and se > x.std() / np.sqrt(len(x)) * 2


def test_summarize_and_verdict_flag_a_real_gain_and_reject_noise():
    rng = np.random.default_rng(1)
    n = 30000
    gid = np.repeat(np.arange(n // 10), 10)
    base = pd.DataFrame({"game_id": gid, "ll_b": rng.normal(-1.0, 0.5, n), "ae_b": rng.normal(1.0, 0.3, n), "season": rng.integers(2022, 2026, n)})
    real = base.assign(ll_t=base["ll_b"] + 0.02 + rng.normal(0, 0.02, n), ae_t=base["ae_b"] - 0.01)
    noise = base.assign(ll_t=base["ll_b"] + rng.normal(0, 0.02, n), ae_t=base["ae_b"] + rng.normal(0, 0.01, n))
    for d, want in ((real, True), (noise, False)):
        per = {"x": {"overall": E.summarize(d), "by_season": [{"season": int(s), **E.summarize(g)} for s, g in d.groupby("season")]}}
        assert E.verdict(per, 4)["x"]["adopt"] is want
    assert E.summarize(real)["log_score_gain"]["mean"] == pytest.approx(0.02, abs=0.002)


def test_nb_logpmf_matches_scipy_and_orders_a_better_mean_higher():
    y = np.array([0, 1, 2, 3])
    good, bad = E.nb_logpmf(y, np.array([0.2, 1.1, 2.0, 2.9]), 0.1), E.nb_logpmf(y, np.array([3.0, 3.0, 0.2, 0.2]), 0.1)
    assert good.sum() > bad.sum()
    from scipy.stats import poisson
    assert E.nb_logpmf([2], [1.5], 1e-12)[0] == pytest.approx(poisson.logpmf(2, 1.5), abs=1e-3)
