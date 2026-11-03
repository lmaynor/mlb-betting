"""Blend (a, b, c) form, trained-model weights, sharp-book fields and the Official-tier signal allowlist of ev.model_picks."""
import math

import pytest

from ev import daily_projections as dp
from ev import model_picks as MP
from ev.props import PropConsensus
from tests.test_model_picks import NOW, row


def _logit(p):
    return math.log(p / (1 - p))


def _cons(p_over, book_fair):
    return PropConsensus(p_over=p_over, n_books=len(book_fair), disp=0.0, books=tuple(sorted(b for b, _ in book_fair)),
                         n_onesided=0, n_dfs=0, book_fair=tuple(sorted(book_fair)))


# ------------------------------------------------------------------------------------------------ blend (a, b, c)
def test_default_blend_is_the_c_only_form_and_unclamped():
    p = dp.blend_p_over(0.70, 0.50, c=0.5)
    assert p == pytest.approx(1 / (1 + math.exp(-0.5 * _logit(0.70))))
    assert dp.blend_ab("nba", "threes") == (0.0, 1.0)               # nothing refit: every stat keeps the c-only form


def test_explicit_intercept_and_slope_shift_the_posterior_and_are_clamped_to_the_study_band():
    base = dp.blend_p_over(0.50, 0.50, c=0.1)
    assert dp.blend_p_over(0.50, 0.50, c=0.1, a=0.05, b=1.0) > base                 # a > 0 lifts P(over)
    lifted = dp.blend_p_over(0.50, 0.50, c=0.1, a=3.0, b=1.0)
    assert lifted == pytest.approx(0.50 + dp.BLEND_MAX_SHIFT)                       # never more than 8 points from the market
    assert dp.blend_p_over(0.50, 0.50, c=0.1, a=-3.0, b=1.0) == pytest.approx(0.50 - dp.BLEND_MAX_SHIFT)
    # b < 1 pulls a far-from-even market toward 0.5 (tail compression), still inside the band
    assert dp.blend_p_over(0.5, 0.80, c=0.0, a=0.0, b=0.95) < 0.80


def test_table_lookup_applies_the_refit_for_that_stat_only(monkeypatch):
    monkeypatch.setitem(dp.BLEND_AB_BY_SPORT_STAT, "nba", {"rebounds": (0.04, 1.0)})
    assert dp.blend_ab("nba", "rebounds") == (0.04, 1.0) and dp.blend_ab("nba", "threes") == (0.0, 1.0)
    assert dp.blend_p_over(0.5, 0.5, sport="nba", stat="rebounds") > 0.5 + 0.005
    assert dp.blend_p_over(0.5, 0.5, sport="nba", stat="threes") == pytest.approx(0.5)


def test_an_intercept_offsets_a_biased_model_so_the_blend_has_the_markets_mean():
    """The 2026-11-01 study: the engine's mean P(over) at the line is 0.449 against a market 0.497 (NegBin skew). With c = 0.13 the
    c-only blend inherits c x the gap; the fitted intercept removes exactly that."""
    pm, pk, c = 0.449, 0.497, 0.13
    c_only = dp.blend_p_over(pm, pk, c=c)
    a = -c * (_logit(pm) - _logit(pk))                                 # the intercept that cancels the model's average offset
    fixed = dp.blend_p_over(pm, pk, c=c, a=a, b=1.0)
    assert abs(fixed - pk) < 1e-9 < abs(c_only - pk)
    assert abs(c_only - pk) < 0.01                                    # and the c-only leak is small to begin with (< 1 point)


# -------------------------------------------------------------------------------------------- trained-model weights
def test_trained_nhl_rows_use_the_measured_trained_weights_and_everything_else_is_unchanged():
    assert dp.blend_c("nhl", "goals") == 0.03 and dp.blend_c("nhl", "goals", trained=True) == 0.37
    assert dp.blend_c("nhl", "assists", trained=True) == 0.34 and dp.blend_c("nhl", "points", trained=True) == 0.175
    assert dp.blend_c("nhl", "saves", trained=True) == 0.35                          # weakest season, not the pooled +0.75
    assert dp.blend_c("nhl", "sog", trained=True) == dp.blend_c("nhl", "sog")         # not a trained stat -> the engine's own entry
    assert dp.blend_c("nba", "threes", trained=True) == 0.19
    assert dp.blend_p_over(0.30, 0.20, sport="nhl", stat="goals", trained=True) > dp.blend_p_over(0.30, 0.20, sport="nhl", stat="goals")


# ------------------------------------------------------------------------------------------------ sharp-book fields
BEST = {"over": ("fanduel", 2.10, 110), "under": ("draftkings", 1.85, -118)}


def test_no_sharp_book_is_fail_open():
    f = dp.sharp_fields(_cons(0.50, [("fanduel", 0.50), ("draftkings", 0.50), ("betmgm", 0.50)]), "over", BEST)
    assert f == {"p_sharp_over": None, "n_sharp": 0, "sharp_agrees": True}
    assert dp.sharp_fields(None, "over", BEST)["sharp_agrees"] is True


def test_a_sharp_book_pricing_our_side_lower_than_the_pack_disagrees():
    pack = [("fanduel", 0.52), ("draftkings", 0.52), ("betmgm", 0.52), ("novig", 0.49), ("prophetx", 0.50)]
    f = dp.sharp_fields(_cons(0.52, pack), "over", BEST)
    assert f["n_sharp"] == 2 and f["p_sharp_over"] == pytest.approx(0.495) and f["sharp_agrees"] is False   # 0.495 < 0.52 - 0.01
    assert dp.sharp_fields(_cons(0.52, pack), "under", BEST)["sharp_agrees"] is True    # the same books favour the Under


def test_a_sharp_book_within_tolerance_or_on_our_side_agrees():
    pack = [("fanduel", 0.52), ("draftkings", 0.52), ("betmgm", 0.52), ("novig", 0.515)]
    assert dp.sharp_fields(_cons(0.52, pack), "over", BEST)["sharp_agrees"] is True       # 0.5 pt below the pack, inside 1 pt
    pack = [("fanduel", 0.52), ("draftkings", 0.52), ("pinnacle", 0.54)]
    assert dp.sharp_fields(_cons(0.52, pack), "over", BEST)["sharp_agrees"] is True


def test_the_book_we_would_bet_at_cannot_confirm_its_own_price():
    pack = [("fanduel", 0.52), ("draftkings", 0.52), ("novig", 0.40)]
    best = {"over": ("novig", 2.4, 140), "under": ("draftkings", 1.85, -118)}
    f = dp.sharp_fields(_cons(0.52, pack), "over", best)
    assert f["n_sharp"] == 0 and f["sharp_agrees"] is True                          # the only sharp book IS the bet book
    assert dp.sharp_fields(_cons(0.52, pack), "over", BEST)["sharp_agrees"] is False


# ----------------------------------------------------------------------------------- Official-tier signal allowlist
def test_zero_signal_stats_are_tracked_never_official():
    assert MP.qualifies(row(stat="rebounds")) == "official"
    for stat in ("points", "pra", "blocks", "steals", "pr", "pa", "ra"):
        assert MP.qualifies(row(stat=stat)) == "tracked", stat             # still logged at stake 0, never staked or posted
    for stat in ("threes", "assists"):
        assert MP.qualifies(row(stat=stat)) == "official", stat


def test_nhl_board_engine_skater_stats_have_no_measured_signal_but_saves_does():
    assert MP.qualifies(row(sport="nhl", stat="saves")) == "official"
    for stat in ("goals", "assists", "points", "sog", "blocked_shots", "pp_points"):
        assert MP.qualifies(row(sport="nhl", stat=stat)) == "tracked", stat


def test_nhl_rows_served_from_the_trained_models_carry_the_measured_trained_signal():
    for stat in ("goals", "assists", "points", "saves"):
        assert MP.qualifies(row(sport="nhl", stat=stat, nhl_model="trained_v1")) == "official", stat
    assert MP.qualifies(row(sport="nhl", stat="sog", nhl_model="trained_v1")) == "tracked"


def test_nfl_only_rush_yds_priced_by_the_hierarchical_engine_counts():
    assert MP.qualifies(row(sport="nfl", stat="rush_yds", engine="empirical_ewm_v2+hierarchical_v1")) == "official"
    assert MP.qualifies(row(sport="nfl", stat="rush_yds", engine="empirical_ewm_v2")) == "tracked"       # c was not measured on it
    for stat in ("pass_yds", "rec_yds", "pass_tds", "anytime_td", "receptions", "pass_attempts"):
        assert MP.qualifies(row(sport="nfl", stat=stat, engine="empirical_ewm_v2+hierarchical_v1")) == "tracked", stat


def test_a_sharp_book_against_the_side_demotes_to_tracked_and_an_old_board_passes():
    assert MP.qualifies(row(sharp_agrees=False)) == "tracked"
    assert MP.qualifies(row(sharp_agrees=True, n_sharp=2)) == "official"
    assert MP.qualifies(row()) == "official"                                  # no sharp fields on the row (older schema): fail-open


def test_tracked_rows_are_still_selected_and_logged_for_the_forward_record():
    picks = MP.select({"rows": [row(stat="points", player="A", player_norm="a"), row(stat="rebounds", player="B", player_norm="b")]},
                      NOW)
    assert {p["stat"]: p["tier"] for p in picks} == {"points": "tracked", "rebounds": "official"}


# ------------------------------------------------------------------------------------------------- deploy script
def test_projections_job_script_never_switches_the_trained_nhl_board_on_by_default():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "deploy" / "setup_daily_projections_job.sh").read_text()
    assert 'NHL_TRAINED_PROPS="${NHL_TRAINED_PROPS:-}"' in text                      # unset unless the operator passes it
    assert '[ -z "$NHL_TRAINED_PROPS" ] ||' in text                                  # only appended to the env when non-empty
    assert 'NHL_TRAINED_PROPS=1,' not in text and 'NHL_TRAINED_PROPS=1"' not in text


# ------------------------------------------------------------------------------------------------ refit script
def test_fit_blend_params_recovers_a_known_blend_and_reports_a_holdout():
    import numpy as np
    import pandas as pd

    from scripts import fit_blend_params as F

    rng = np.random.default_rng(7)
    n = 12000
    pm = rng.uniform(0.35, 0.65, n)
    pe = np.clip(pm + rng.normal(0, 0.06, n), 0.05, 0.95)
    z = -0.05 + _logit_arr(pm) + 0.3 * (_logit_arr(pe) - _logit_arr(pm))
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-z))).astype(float)
    df = pd.DataFrame({"stat": "threes", "season": np.repeat([2022, 2023, 2024, 2025], n // 4), "game_date": np.arange(n) // 20,
                       "over": y, "p_market": pm, "p_eng": pe})
    (r,) = F.evaluate(df, "p_eng", 2024)
    assert r["a"] == pytest.approx(-0.05, abs=0.05) and r["c"] == pytest.approx(0.3, abs=0.12)
    assert r["n_test"] == n // 2 and r["ll_abc"] <= r["ll_market"]


def _logit_arr(p):
    import numpy as np
    return np.log(p / (1 - p))
