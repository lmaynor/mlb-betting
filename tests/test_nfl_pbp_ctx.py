"""pbp-context features (pc_*): opt-in, strictly pre-game, aligned with the derived tables. Offline / synthetic."""
import numpy as np
import pandas as pd
import pytest

from nfl.data import derived as D
from nfl.features import props_features as F
from tests.test_nfl_features import PLAYED_WEEKS, SEASON, make_frames


def _derived(frames, seed=3):
    """Synthetic derived tables for every PLAYED team-game of `frames` (random but fixed)."""
    rng = np.random.default_rng(seed)
    sched = frames.schedules
    team_rows, player_rows = [], []
    for g in sched[sched["week"].isin(list(PLAYED_WEEKS))].itertuples():
        for team, opp in ((g.home_team, g.away_team), (g.away_team, g.home_team)):
            row = {"game_id": g.game_id, "team": team, "season": g.season, "week": g.week, "opponent": opp}
            for pre in ("off", "def"):
                for m in D.TEAM_METRICS:
                    row[f"{pre}_{m}"] = float(rng.normal(0.5, 0.1))
            team_rows.append(row)
    ps = frames.player_stats
    for r in ps.itertuples():
        player_rows.append({"game_id": r.game_id, "player_id": r.player_id, "season": r.season, "week": r.week,
                            "team": r.team, **{c: float(rng.integers(0, 5)) for c in D.PLAYER_METRICS}})
    return pd.DataFrame(team_rows), pd.DataFrame(player_rows)


@pytest.fixture(scope="module")
def frames_ctx():
    fr = make_frames()
    fr.pbp_team, fr.pbp_player = _derived(fr)
    return fr


@pytest.fixture(scope="module")
def built_ctx(frames_ctx):
    return F.build_from_frames(frames_ctx, [SEASON])


def test_default_feature_list_is_unchanged_by_the_new_group(frames_ctx, built_ctx):
    plain = F.build_from_frames(make_frames(), [SEASON])
    assert F.feature_columns(built_ctx) == F.feature_columns(plain)
    assert not any(c.startswith(F.PC_PREFIX) for c in F.feature_columns(built_ctx))
    ctx_cols = [c for c in F.feature_columns(built_ctx, include_pbp_ctx=True) if c.startswith(F.PC_PREFIX)]
    assert ctx_cols and set(ctx_cols) == {c for c in built_ctx.columns if c.startswith(F.PC_PREFIX)}


def test_no_raw_pbp_columns_are_emitted(built_ctx):
    assert not [c for c in built_ctx.columns if c.startswith("pcraw_") or c.startswith("pcx_")]


def test_absent_derived_tables_add_no_columns():
    plain = F.build_from_frames(make_frames(), [SEASON])
    assert not any(c.startswith(F.PC_PREFIX) for c in plain.columns)


def test_team_features_are_the_trailing_ewm_of_prior_games_only(frames_ctx, built_ctx):
    pt = frames_ctx.pbp_team
    team = "CCC"
    games = pt[pt["team"] == team].sort_values("week")
    row = built_ctx[(built_ctx["team"] == team) & (built_ctx["week"] == 5) & (built_ctx["position"] == "QB")].iloc[0]
    prior = games[games["week"] < 5]["off_epa_dropback"]
    expect = prior.ewm(halflife=F.EWM_HALFLIFE, ignore_na=True).mean().iloc[-1]
    assert row["pc_tm_epa_dropback_ewm"] == pytest.approx(expect)
    first = built_ctx[(built_ctx["team"] == team) & (built_ctx["week"] == 1) & (built_ctx["position"] == "QB")].iloc[0]
    assert np.isnan(first["pc_tm_epa_dropback_ewm"])


def test_opponent_defense_feature_reads_the_opponents_defense_not_the_teams_own(frames_ctx, built_ctx):
    pt = frames_ctx.pbp_team
    row = built_ctx[(built_ctx["team"] == "AAA") & (built_ctx["week"] == 4) & (built_ctx["position"] == "WR")].iloc[0]
    opp = row["opponent"]
    prior = pt[(pt["team"] == opp) & (pt["week"] < 4)].sort_values("week")["def_epa_dropback"]
    expect = prior.ewm(halflife=F.EWM_HALFLIFE, ignore_na=True).mean().iloc[-1]
    assert row["pc_opp_def_epa_dropback_ewm"] == pytest.approx(expect)


def test_player_trailing_and_adot(frames_ctx, built_ctx):
    pp = frames_ctx.pbp_player
    p = pp[pp["player_id"] == "wr1_AAA"].sort_values("week")
    row = built_ctx[(built_ctx["player_id"] == "wr1_AAA") & (built_ctx["week"] == 5)].iloc[0]
    prior = p[p["week"] < 5]
    assert row["pc_pl_deep_tgt_l5"] == pytest.approx(prior["deep_tgt"].tail(5).mean())
    ew = lambda c: prior[c].ewm(halflife=F.EWM_HALFLIFE, ignore_na=True).mean().iloc[-1]
    assert row["pc_pl_adot_ewm"] == pytest.approx(ew("air_yards") / ew("tgt_pbp"))


def test_covered_game_without_a_player_event_counts_as_zero_not_nan():
    fr = make_frames()
    pt, pp = _derived(fr)
    pp = pp[~((pp["player_id"] == "wr1_AAA") & (pp["week"] == 2))]       # no pbp event row for that player-game
    fr.pbp_team, fr.pbp_player = pt, pp
    b = F.build_from_frames(fr, [SEASON])
    nxt = b[(b["player_id"] == "wr1_AAA") & (b["week"] == 4)].iloc[0]       # week 3 is a bye; week 4 trails weeks 1,2
    p = pp[(pp["player_id"] == "wr1_AAA") & (pp["week"] == 1)]
    # week-2 deep_tgt counted as 0.0 (game covered by pbp) so the L5 average of weeks 1,2 is half of week 1's value
    assert nxt["pc_pl_deep_tgt_l5"] == pytest.approx(p["deep_tgt"].iloc[0] / 2)


@pytest.mark.parametrize("wk", [2, 4, 6])
def test_leakage_perturbing_a_week_moves_no_earlier_pbp_context_feature(frames_ctx, wk):
    r = F.leakage_check(frames_ctx, [SEASON], SEASON, wk, seed=wk)
    assert r["changed_cols"] == [], r["changed_cols"]
    assert r["n_checked"] > 0 and r["n_changed_after"] > 0          # the check has power on the new columns too


def test_leakage_check_would_catch_a_same_game_pbp_feature(frames_ctx, monkeypatch):
    """A feature built from the CURRENT game's pbp (no shift) must be flagged -- proves the new check can fail."""
    real = F._pbp_team_trailing

    def leaky(tg):
        out = real(tg)
        out["pc_tm_leak"] = tg["pcx_off_epa_dropback"]          # same-game value, NOT shifted
        return out
    monkeypatch.setattr(F, "_pbp_team_trailing", leaky)
    r = F.leakage_check(frames_ctx, [SEASON], SEASON, 4, seed=1)
    assert "pc_tm_leak" in r["changed_cols"]
