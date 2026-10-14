"""nba.props.features: the leakage guarantee, the ET-day fix, and the training-path == live-path property."""
import numpy as np
import pandas as pd
import pytest

from nba.props import data as D
from nba.props import features as F
from tests.nba_props_helpers import make_masters, perturb_from, prepared, truncate_before


@pytest.fixture(scope="module")
def world():
    pb, gm, tips = make_masters()
    rows, tg = prepared(pb, gm, tips)
    ctx = F.FeatureContext(rows, tg)
    X, meta = F.build_training_frame(rows, tg, ctx)
    return pb, gm, tips, rows, tg, X, meta


def _eq(a: pd.Series, b: pd.Series):
    assert list(a.index) == list(b.index)
    both_nan = a.isna() & b.isna()
    close = np.isclose(a.astype(float), b.astype(float), rtol=1e-9, atol=1e-9)
    bad = ~(both_nan | close)
    assert not bad.any(), f"features differ: {list(a.index[bad])}"


def test_features_exclude_own_game_and_all_later_games(world):
    """Overwrite EVERY game on/after the row's ET day (its own game, same-day games, the future) with absurd stats:
    the row's features must not move."""
    pb, gm, tips, rows, tg, X, meta = world
    pos = len(meta) // 2
    ridx = meta.index[pos]
    r = rows.loc[ridx]
    cutoff = pd.Timestamp(r["date"])
    pb2 = perturb_from(pb, gm, tips, cutoff)
    rows2, tg2 = prepared(pb2, gm, tips)
    X2, meta2 = F.build_training_frame(rows2, tg2)
    # same row (player, game) in the perturbed world
    k2 = meta2[(meta2["player_id"] == r["player_id"]) & (meta2["game_id"] == r["game_id"])]
    assert len(k2) == 1
    _eq(X.loc[ridx], X2.loc[k2.index[0]])
    # and the perturbation really changed the row's OWN target (so the test would catch a leak)
    assert float(meta2.loc[k2.index[0], "points"]) != float(meta.loc[ridx, "points"])


def test_every_row_is_unaffected_by_perturbing_its_own_day_onward(world):
    """Sample many rows (not one lucky pick)."""
    pb, gm, tips, rows, tg, X, meta = world
    rng = np.random.default_rng(1)
    for ridx in rng.choice(meta.index.to_numpy(), size=6, replace=False):
        r = rows.loc[ridx]
        pb2 = perturb_from(pb, gm, tips, pd.Timestamp(r["date"]), factor=5)
        rows2, tg2 = prepared(pb2, gm, tips)
        X2, meta2 = F.build_training_frame(rows2, tg2)
        k2 = meta2[(meta2["player_id"] == r["player_id"]) & (meta2["game_id"] == r["game_id"])]
        _eq(X.loc[ridx], X2.loc[k2.index[0]])


def test_live_path_equals_training_path(world):
    """Features for a row built from history TRUNCATED to strictly before its day (the live situation: the game is not in
    history yet) equal the features computed for that row inside the full history."""
    pb, gm, tips, rows, tg, X, meta = world
    for ridx in (meta.index[len(meta) // 3], meta.index[2 * len(meta) // 3]):
        r = rows.loc[ridx]
        cutoff = pd.Timestamp(r["date"])
        pb_t, gm_t, tips_t = truncate_before(pb, gm, tips, cutoff)
        rows_t, tg_t = prepared(pb_t, gm_t, tips_t)
        ctx_t = F.FeatureContext(rows_t, tg_t)
        q = pd.DataFrame([{c: r[c] for c in F.ID_COLS}])
        live = ctx_t.features(q).iloc[0]
        _eq(X.loc[ridx], live)


def test_a_player_with_no_history_gets_nan_features_not_a_crash(world):
    _, _, _, rows, tg, X, meta = world
    ctx = F.FeatureContext(rows, tg)
    q = pd.DataFrame([{"player_id": "nobody", "date": pd.Timestamp("2025-12-05"), "team": "BOS", "opp": "LAL",
                       "is_home": True, "season_year": 2025, "season_type": "Regular Season"}])
    f = ctx.features(q).iloc[0]
    assert f["n_played"] == 0 and np.isnan(f["points_l10"]) and np.isnan(f["days_since_last"])


def test_et_game_day_not_utc_date(world):
    """The masters' date is the UTC date: a 22:30 ET tip is stored on the NEXT day. prepare() restores the ET day."""
    pb, gm, tips, rows, tg, X, meta = world
    late = gm.iloc[1]                                         # second game of night 0: 22:30 ET
    utc_date = pd.Timestamp(late["date"])
    et = rows[rows["game_id"] == late["game_id"]]["date"].iloc[0]
    assert et == utc_date - pd.Timedelta(days=1)
    assert D.et_day("2025-11-02T03:30:00Z") == pd.Timestamp("2025-11-01")


def test_back_to_back_uses_et_days_and_schedule_is_prior_games_only(world):
    _, _, _, rows, tg, X, meta = world
    f = X.join(meta[["team", "date"]])
    b2b = f[f["tm_b2b"] == 1]
    assert len(b2b) and (b2b["tm_rest_days"] == 1).all()
    assert set(f["tm_g7"].dropna().unique()) <= set(range(0, 8))


def test_missed_games_are_counted_from_team_games(world):
    """A rotation player who sits three straight team games shows team_games_since_last == 3 on his return."""
    pb, gm, tips = make_masters(skip={"BOS-0": set(range(10, 22))})
    rows, tg = prepared(pb, gm, tips)
    ctx = F.FeatureContext(rows, tg)
    X, meta = F.build_training_frame(rows, tg, ctx)
    f = X.join(meta[["player_id", "game_id", "date"]])
    f = f[f["player_id"] == "BOS-0"].sort_values("date")
    assert (f["team_games_since_last"] >= 2).any()
    assert f["missed_l10"].max() >= 2
    assert f["days_since_last"].max() >= 3


def test_season_rollover_does_not_reset_windows():
    """Rolling windows cross a season boundary on purpose; season-to-date stats do not."""
    pb1, gm1, tips1 = make_masters(n_days=40, start="2025-03-01", season_year=2024)
    pb2, gm2, tips2 = make_masters(n_days=40, start="2025-11-01", season_year=2025, seed=9)
    gm2["game_id"] = "n" + gm2["game_id"]
    pb2["game_id"] = "n" + pb2["game_id"]
    tips2.index = ["n" + i for i in tips2.index]
    pb, gm, tips = pd.concat([pb1, pb2]), pd.concat([gm1, gm2]), pd.concat([tips1, tips2])
    rows, tg = prepared(pb, gm, tips)
    ctx = F.FeatureContext(rows, tg)
    q = pd.DataFrame([{"player_id": "BOS-0", "date": pd.Timestamp("2025-11-01"), "team": "BOS", "opp": "LAL",
                       "is_home": True, "season_year": 2025, "season_type": "Regular Season"}])
    f = ctx.features(q).iloc[0]
    assert f["season_rollover"] == 1.0 and f["n_szn"] == 0 and np.isnan(f["points_szn"])
    assert f["n_played"] > 10 and np.isfinite(f["points_l10"])          # last season's games still feed the windows


def test_window_ratio_uses_strictly_earlier_days():
    days = np.array([1, 2, 3, 10])
    vals = np.array([10.0, 20.0, 30.0, 100.0])
    r = F.window_ratio(days, vals, np.ones(4), np.array([3, 1, 11, 200]), window=90)[:, 0]
    assert r[0] == 15.0                       # days 1,2 only (day 3 itself excluded)
    assert np.isnan(r[1])                     # nothing earlier than day 1
    assert r[2] == pytest.approx(40.0)        # 10,20,30,100
    assert r[3] == pytest.approx(40.0)        # empty 90-day window falls back to the last non-empty one


def test_prepare_without_tip_times_falls_back_to_the_master_date_and_says_so(world):
    """No schedule dump for a game -> keep the UTC master date, mark tip_known=False (the scorer reports the share)."""
    pb, gm, tips, rows, tg, X, meta = world
    rows0, tg0 = D.prepare(pb, gm, None)
    assert not rows0["tip_known"].any() and D.history_status(rows0)["tip_known_share"] == 0.0
    g0 = gm.iloc[0]
    assert rows0[rows0["game_id"] == g0["game_id"]]["date"].iloc[0] == pd.Timestamp(g0["date"])
    assert D.history_status(rows)["tip_known_share"] == 1.0
    # the raw boxscore cache can supply tips for games newer than the schedule dumps
    assert D.tips_from_raw_boxscores([], local_dir="/nonexistent").empty


def test_real_fixture_boxscore_event_provides_a_tip(tmp_path):
    """tests/fixtures/nba_boxscore_sample.json is a real SportsBlaze /boxscores payload: its events carry the tip time."""
    import json
    from pathlib import Path
    fx = Path(__file__).parent / "fixtures" / "nba_boxscore_sample.json"
    raw = json.loads(fx.read_text())
    d = tmp_path / "raw" / "boxscores"
    d.mkdir(parents=True)
    (d / "2026-03-15.json").write_text(json.dumps(raw))
    tips = D.tips_from_raw_boxscores(["2026-03-15"], local_dir=str(tmp_path))
    assert len(tips) == len(raw["events"]) and str(tips.dtype).startswith("datetime64[ns, UTC]")
    first = raw["events"][0]
    assert tips[first["id"]] == pd.Timestamp(first["date"])
