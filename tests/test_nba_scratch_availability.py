"""availability="history_excl_scratch": training-side absentees exclude players ESPN lists as healthy scratches
(COACH'S DECISION), because no injury list can contain a benching. Synthetic world (tests/nba_props_helpers.py)."""
import numpy as np
import pandas as pd
import pytest

from nba.props import config as C
from nba.props import features as F
from tests.nba_props_helpers import make_masters, prepared, truncate_before

AV = list(C.AVAILABILITY_FEATURES)


@pytest.fixture(scope="module")
def world():
    pb, gm, tips = make_masters(skip={"BOS-0": set(range(14, 30))})
    rows, tg = prepared(pb, gm, tips)
    dnp = pb[~pb["played"].astype(bool)]
    # BOS-0's stretch is an UNLISTED absence (an injured inactive is not on the box); every other DNP is a listed scratch
    sc = dnp[dnp["player_id"] != "BOS-0"]
    team = sc["team_abbr"].map({"BOS": "BOS", "LAL": "LAL", "NY": "NYK", "MIA": "MIA"})
    scratches = pd.DataFrame({"game_id": sc["game_id"].values, "team": team.values, "player_id": sc["player_id"].values})
    ctx = F.FeatureContext(rows, tg, scratches=scratches)
    return pb, gm, tips, rows, tg, scratches, ctx


def _case(world):
    pb, gm, tips, rows, tg, scratches, ctx = world
    for ridx in rows.index:
        r = rows.loc[ridx]
        if r["team"] == "BOS" and r["player_id"] != "BOS-0":
            ab = ctx.absent_set(r["team"], r["date"])
            if ab and "BOS-0" in ab:
                return ridx, r
    pytest.fail("no case")


def test_requires_the_scratch_frame_and_rejects_unknown_modes(world):
    pb, gm, tips, rows, tg, scratches, ctx = world
    plain = F.FeatureContext(rows, tg)
    q = rows[F.ID_COLS].head(3)
    with pytest.raises(ValueError, match="scratches"):
        plain.features(q, availability="history_excl_scratch")
    with pytest.raises(ValueError):
        ctx.features(q, availability="nonsense")


def test_scratches_are_removed_but_unlisted_absences_stay(world):
    pb, gm, tips, rows, tg, scratches, ctx = world
    X_hist = ctx.features(rows[F.ID_COLS], availability="history")
    X_ex = ctx.features(rows[F.ID_COLS], availability="history_excl_scratch")
    assert (X_ex["team_missing_n_rotation"].fillna(0) <= X_hist["team_missing_n_rotation"].fillna(0) + 1e-9).all()
    assert (X_ex["team_missing_min_l10"].fillna(0) <= X_hist["team_missing_min_l10"].fillna(0) + 1e-9).all()
    assert (X_ex["team_missing_n_rotation"].fillna(0) < X_hist["team_missing_n_rotation"].fillna(0) - 1e-9).any()
    ridx, r = _case(world)
    assert X_ex.loc[ridx, "team_missing_n_rotation"] >= 1.0          # BOS-0's unlisted stretch still counts


def test_empty_scratch_listing_reproduces_history_exactly(world):
    pb, gm, tips, rows, tg, scratches, ctx = world
    empty = F.FeatureContext(rows, tg, scratches=pd.DataFrame(columns=["game_id", "team", "player_id"]))
    a = empty.features(rows[F.ID_COLS], availability="history_excl_scratch")
    b = empty.features(rows[F.ID_COLS], availability="history")
    pd.testing.assert_frame_equal(a[AV], b[AV])


def test_training_value_equals_live_value_for_the_same_list(world):
    """Truncated history + an injury list {team: absent minus scratches} == the training feature."""
    pb, gm, tips, rows, tg, scratches, ctx = world
    X = ctx.features(rows[F.ID_COLS], availability="history_excl_scratch")
    checked = 0
    for ridx in rows.index[::13]:
        r = rows.loc[ridx]
        absent = ctx.absent_set(r["team"], r["date"])
        if absent is None:
            continue
        listed = set(scratches.loc[(scratches["game_id"] == r["game_id"]) & (scratches["team"] == r["team"]),
                                   "player_id"])
        pb_t, gm_t, tips_t = truncate_before(pb, gm, tips, pd.Timestamp(r["date"]))
        if pb_t.empty:
            continue
        rows_t, tg_t = prepared(pb_t, gm_t, tips_t)
        live_ctx = F.FeatureContext(rows_t, tg_t)
        q = pd.DataFrame([{c: r[c] for c in F.ID_COLS}])
        live = live_ctx.features(q, availability={r["team"]: absent - listed}).iloc[0]
        for c in AV:
            a, b = float(X.loc[ridx, c]), float(live[c])
            assert (np.isnan(a) and np.isnan(b)) or np.isclose(a, b), (c, a, b)
        checked += 1
    assert checked >= 8


def test_a_player_never_counts_himself(world):
    pb, gm, tips, rows, tg, scratches, ctx = world
    X = ctx.features(rows[F.ID_COLS], availability="history_excl_scratch")
    own = rows[rows["player_id"] == "BOS-0"].index
    assert X.loc[own, "team_missing_n_rotation"].dropna().ge(0).all()
