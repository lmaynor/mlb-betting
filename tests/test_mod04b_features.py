"""Offline synthetic tests for MOD-04 U1 feature-store plumbing and B1 NHL volume features."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nhl.props import mod04_features as F
from nhl.props import mod04_nhl_features as NF
from nhl.props import xgfeat as X

PAIRS = [((1, 2), (3, 4)), ((1, 3), (2, 4)), ((1, 4), (2, 3))]
ABBR = {1: "AAA", 2: "BBB", 3: "CCC", 4: "DDD"}


def _synth_frames(
    n_days: int = 36,
    start_date: str = "2025-10-10",
    seed: int = 42,
    include_xg: bool = False,
) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    games, sbox, gbox, xgl, xt, xsk = [], [], [], [], [], []
    base = pd.Timestamp(start_date)
    gid = 2025020000
    for d in range(0, n_days, 2):
        date = (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        for home, away in PAIRS[(d // 2) % 3]:
            gid += 1
            stats = {}
            for team in (home, away):
                sa = int(rng.integers(22, 38))
                ga = int(rng.integers(1, 5))
                stats[team] = {"sa": sa, "ga": ga, "xga": float(sa * 0.095), "att_ag": sa + int(rng.integers(6, 16))}
            games.append({
                "game_id": gid,
                "season_year": 2025,
                "game_type": 2,
                "date": date,
                "start_utc": f"{date}T23:00:00Z",
                "home_id": home,
                "away_id": away,
                "home_abbr": ABBR[home],
                "away_abbr": ABBR[away],
                "home_score": stats[away]["ga"],
                "away_score": stats[home]["ga"],
                "home_sog": stats[away]["sa"],
                "away_sog": stats[home]["sa"],
            })
            for team, opp, is_home in ((home, away, 1), (away, home, 0)):
                s = stats[team]
                o = stats[opp]
                # starter + backup
                for g_idx, is_starter in ((1, True), (2, False)):
                    gpid = team * 10 + g_idx
                    toi_g = 3600.0 if is_starter else 0.0
                    if is_starter or d % 6 == 0:
                        toi_g = 3000.0 if is_starter else 600.0 if d % 6 == 0 else 3600.0
                    sa_g = s["sa"] if is_starter else 0
                    ga_g = s["ga"] if is_starter else 0
                    if toi_g > 0:
                        gbox.append({
                            "game_id": gid, "season_year": 2025, "game_type": 2, "date": date,
                            "team_id": team, "team_abbr": ABBR[team], "is_home": bool(is_home),
                            "player_id": gpid, "name": f"Goalie {gpid}", "toi_sec": toi_g,
                            "starter": is_starter, "shots_against": sa_g,
                            "saves": sa_g - ga_g, "goals_against": ga_g,
                        })
                        if include_xg:
                            xgl.append({
                                "game_id": gid, "player_id": gpid, "shots_faced": sa_g,
                                "xga": s["xga"] if is_starter else 0.0, "ga_pop": ga_g,
                                "xga_pk": 0.2 * s["xga"] if is_starter else 0.0,
                                "sa_pk": 4 if is_starter else 0,
                                "hd_faced": sa_g // 4,
                                "gsax": (s["xga"] - ga_g) if is_starter else 0.0,
                            })
                if include_xg:
                    xt.append({
                        "game_id": gid, "team_id": team, "att_for": o["att_ag"], "xgf": o["xga"],
                        "sog_for": o["sa"], "xgf_sog": o["xga"], "goals_for_pop": o["ga"],
                        "hd_for": o["sa"] // 4, "att_pp_for": 5, "att_against": s["att_ag"],
                        "xga": s["xga"], "sog_against": s["sa"], "xga_sog": s["xga"],
                        "goals_against_pop": s["ga"], "hd_against": s["sa"] // 4, "att_pp_against": 5,
                    })
                for k in range(5):
                    pid = team * 100 + k
                    sog = int(rng.poisson(2.2))
                    att = sog + int(rng.integers(0, 3))
                    g = int(rng.poisson(0.25))
                    a = int(rng.poisson(0.35))
                    sbox.append({
                        "game_id": gid, "season_year": 2025, "game_type": 2, "date": date,
                        "team_id": team, "team_abbr": ABBR[team], "is_home": bool(is_home),
                        "player_id": pid, "name": f"Skater {pid}",
                        "position": "D" if k < 2 else "C",
                        "toi_sec": float(rng.integers(800, 1400)),
                        "goals": g, "assists": a, "points": g + a, "sog": sog, "attempts": att,
                    })
                    if include_xg:
                        xsk.append({
                            "game_id": gid, "player_id": pid, "attempts": att, "ixg": 0.22,
                            "ixg_ev": 0.16, "ixg_pp": 0.06, "att_ev": max(0, att - 1),
                            "att_pp": 1, "hd_att": 1, "sog_pop": sog, "ixg_sog": 0.25, "goals_pop": g,
                        })

    out = {
        "games": pd.DataFrame(games),
        "skater_box": pd.DataFrame(sbox),
        "goalie_box": pd.DataFrame(gbox),
    }
    if include_xg:
        out["xg_team"] = pd.DataFrame(xt)
        out["xg_goalie"] = pd.DataFrame(xgl)
        out["xg_skater"] = pd.DataFrame(xsk)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# U1: assert_feature_schema, build_pregame_features, join_labels
# ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad_col", [
    "sv_l60_post",
    "SV_L60_POST",
    "actual_saves",
    "Actual_SOG",
    "outcome_over",
    "OUTCOME_y",
])
def test_schema_gate_rejects_forbidden_columns_case_insensitive(bad_col):
    df = pd.DataFrame({"game_id": [1], "player_id": [10], bad_col: [0.5]})
    with pytest.raises(ValueError, match="Forbidden feature column"):
        F.assert_feature_schema(df)


def test_schema_gate_allows_clean_columns():
    df = pd.DataFrame({"game_id": [1], "player_id": [10], "toi_l10": [18.5], "g_sv_l60": [0.91]})
    F.assert_feature_schema(df)


def test_build_pregame_features_drops_future_rows_and_respects_cutoff_without_mutation():
    frames = _synth_frames(n_days=20)
    frames["rotowire"] = pd.DataFrame({
        "date": ["2025-10-18", "2025-10-20", "2025-10-20"],
        "team": ["AAA", "AAA", "BBB"],
        "player_id": [11, 11, 21],
        "confirmed": [True, True, True],
        "fetched_at": ["2025-10-18T17:00:00Z", "2025-10-20T18:00:00Z", "2025-10-20T22:00:00Z"],
    })
    snap_before = {k: v.copy(deep=True) for k, v in frames.items()}

    target_date = "2025-10-20"
    cutoff_utc = "2025-10-20T19:00:00Z"
    out = F.build_pregame_features(target_date, cutoff_utc, frames)

    # Inputs untouched
    for k, orig in snap_before.items():
        pd.testing.assert_frame_equal(frames[k], orig)

    assert (out["skater_box"]["date"] < target_date).all()
    assert (out["goalie_box"]["date"] < target_date).all()
    assert (out["games"]["date"] <= target_date).all()
    # 2025-10-20T22:00:00Z rotowire row after cutoff_utc must be dropped
    assert len(out["rotowire"]) == 2
    assert "BBB" not in set(out["rotowire"]["team"])


def test_build_pregame_features_rejects_forbidden_input_schema():
    frames = _synth_frames(n_days=10)
    frames["skater_box"]["actual_sog"] = 2
    with pytest.raises(ValueError, match="Forbidden feature column"):
        F.build_pregame_features("2025-10-16", "2025-10-16T18:00:00Z", frames)


def test_join_labels_merges_on_game_and_player_without_mutation():
    feats = pd.DataFrame({"game_id": [1, 2], "player_id": [10, 10], "toi_l10": [18.0, 19.0]})
    labels = pd.DataFrame({"game_id": [1, 2], "player_id": [10, 10], "actual_sog": [3, 1]})
    feats_orig = feats.copy(deep=True)
    labels_orig = labels.copy(deep=True)

    joined = F.join_labels(feats, labels)
    pd.testing.assert_frame_equal(feats, feats_orig)
    pd.testing.assert_frame_equal(labels, labels_orig)
    assert list(joined["actual_sog"]) == [3, 1]


def test_time_travel_property_pregame_and_volume_features():
    """Deleting all rows on/after target_date D+1 from input produces identical features for rows < target_date."""
    full_frames = _synth_frames(n_days=30, include_xg=True)
    dates = sorted(full_frames["games"]["date"].unique())
    target = dates[10]  # D + 1

    pre_full = F.build_pregame_features(target, f"{target}T18:00:00Z", full_frames)

    valid_gids = set(full_frames["games"].loc[full_frames["games"]["date"] < target, "game_id"])
    trunc_frames = {}
    for k, df in full_frames.items():
        if "date" in df.columns:
            trunc_frames[k] = df[df["date"] < target].copy().reset_index(drop=True)
        else:
            trunc_frames[k] = df[df["game_id"].isin(valid_gids)].copy().reset_index(drop=True)
    pre_trunc = F.build_pregame_features(target, f"{target}T18:00:00Z", trunc_frames)

    for k in ("skater_box", "goalie_box"):
        pd.testing.assert_frame_equal(pre_full[k], pre_trunc[k])

    # Also verify full-frame volume features restricted to < target equal pre-truncated volume features
    vol_full = NF.build_nhl_volume_features(full_frames)
    vol_trunc = NF.build_nhl_volume_features(trunc_frames)
    for key in ("skater", "saves"):
        a = vol_full[key][vol_full[key]["date"] < target].sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)
        b = vol_trunc[key].sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b)


# ---------------------------------------------------------------------------------------------------------------------
# B1: build_nhl_volume_features
# ---------------------------------------------------------------------------------------------------------------------
def test_volume_features_without_toi_splits_sets_proxy_flags_and_passes_schema():
    frames = _synth_frames(n_days=24, include_xg=False)
    res = NF.build_nhl_volume_features(frames, toi_splits=None)

    sk = res["skater"]
    sv = res["saves"]
    F.assert_feature_schema(sk)
    F.assert_feature_schema(sv)

    assert not sk.empty and not sv.empty
    assert sk["pp_toi_share_prior"].isna().all()
    assert (sk["is_pp1_proxy"] == 0).all()
    assert (sk["deployment_source"] == "total_toi_proxy").all()
    assert sk["toi_sd_l10"].notna().any()
    assert sk["on_target_rate_l30"].notna().any()
    assert sk["att_p60_l20"].notna().any()

    # No outcome or market columns leaked into feature outputs
    banned = {"sog", "saves", "shots_against", "goals", "assists", "points", "toi_sec", "starter", "line", "odds"}
    assert not (banned & set(sk.columns))
    assert not (banned & set(sv.columns))


def test_volume_features_with_toi_splits_computes_prior_pp_and_top3_pp1():
    frames = _synth_frames(n_days=20, include_xg=True)
    sb = frames["skater_box"]
    # Assign deterministic PP shares so players k=2,3,4 on each team have highest PP share
    splits = sb[["game_id", "player_id"]].copy()
    splits["team_pp_s"] = 180.0
    splits["pp_share"] = (splits["player_id"] % 10) * 0.15

    res = NF.build_nhl_volume_features(frames, toi_splits=splits)
    sk = res["skater"]
    assert (sk.loc[sk["n_prior"] > 0, "deployment_source"] == "toi_splits").all()
    assert (sk.loc[sk["n_prior"] == 0, "deployment_source"] == "total_toi_proxy").all()

    later = sk[sk["n_prior"] >= 2]
    pp1_ranks = set(later.loc[later["is_pp1_proxy"] == 1, "player_id"] % 10)
    assert pp1_ranks == {2, 3, 4}


def test_rotowire_span_enforced_strictly():
    assert NF.ROTOWIRE_SPAN == ("2026-11-16", "2026-11-24")
    # Build frames spanning 2026-11-08 .. 2026-11-28
    frames = _synth_frames(n_days=22, start_date="2026-11-08", include_xg=True)
    # Deliberately pass rotowire rows both INSIDE and OUTSIDE ROTOWIRE_SPAN
    dates = sorted(frames["games"]["date"].unique())
    rw_rows = []
    for d in dates:
        for team_id, abbr in ABBR.items():
            rw_rows.append({
                "date": d,
                "team": abbr,
                "player_id": team_id * 10 + 1,
                "confirmed": True,
            })
    rotowire = pd.DataFrame(rw_rows)

    res = NF.build_nhl_volume_features(frames, rotowire=rotowire)
    sv = res["saves"]
    sk = res["skater"]

    in_span = (sv["date"] >= NF.ROTOWIRE_SPAN[0]) & (sv["date"] <= NF.ROTOWIRE_SPAN[1])
    out_span = ~in_span
    assert in_span.any() and out_span.any()

    assert sv.loc[out_span, "rotowire_confirmed"].isna().all()
    assert (sv.loc[out_span, "starter_source"] == "expected_proxy").all()
    assert sv.loc[in_span, "rotowire_confirmed"].notna().all()
    assert (sv.loc[in_span, "starter_source"] == "rotowire").all()
    assert sk["rotowire_pp_unit"].isna().all()
