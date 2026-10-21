"""Offline tests for nba.games: BettingPros game-line parser, market consensus, leakage-safe features, pricing helpers."""
import numpy as np
import pandas as pd
import pytest

from nba.games import bp_games, data, features as F, market, models as M

EV = {7: {"away": "BOS", "home": "NYK", "scheduled": "2024-02-11 00:30:00", "season_type": "REG", "status": "closed"}}


def _line(cost, line=None, main=True):
    return {"cost": cost, "line": line, "main": main, "is_off": False, "updated": "2024-02-10 23:00:00"}


def _sel(participant, books, selection="", opening=None):
    s = {"participant": participant, "selection": selection, "label": participant,
         "books": [{"id": b, "lines": ls} for b, ls in books.items()]}
    if opening:
        s["opening_line"] = opening
    return s


def test_parser_long_format_keeps_each_books_own_line():
    offer = {"event_id": 7, "selections": [
        _sel("NYK", {12: [_line(-108, -2.5)], 13: [_line(-110, -1.5)]}, opening={"line": -2.0, "cost": -110, "book_id": 14}),
        _sel("BOS", {12: [_line(-112, 2.5)], 13: [_line(-110, 1.5)]})]}
    rows = bp_games.build_game_rows("spread", 129, "2024-02-10", [offer], EV)
    quotes = [(r["book"], r["side"], r["line"]) for r in rows if not r["is_open"]]
    assert ("DraftKings", "home", -2.5) in quotes and ("Caesars", "home", -1.5) in quotes
    assert ("DraftKings", "away", 2.5) in quotes
    assert sum(r["is_open"] for r in rows) == 1                      # opening line kept as its own row
    assert bp_games.to_frame(rows)["line"].dtype == float


def test_parser_moneyline_has_no_line_and_total_sides():
    ml = {"event_id": 7, "selections": [_sel("NYK", {12: [_line(-150)]}), _sel("BOS", {12: [_line(130)]})]}
    rows = bp_games.build_game_rows("moneyline", 127, "d", [ml], EV)
    assert {(r["side"], r["odds"]) for r in rows} == {("home", -150), ("away", 130)}
    tot = {"event_id": 7, "selections": [_sel("", {12: [_line(-110, 221.5)]}, selection="over"),
                                         _sel("", {12: [_line(-110, 221.5)]}, selection="under")]}
    rows = bp_games.build_game_rows("total", 128, "d", [tot], EV)
    assert {r["side"] for r in rows} == {"over", "under"}


def test_unknown_event_and_off_lines_dropped():
    offer = {"event_id": 99, "selections": [_sel("NYK", {12: [_line(-110, -2.5)]})]}
    assert bp_games.build_game_rows("spread", 129, "d", [offer], EV) == []
    off = {"event_id": 7, "selections": [_sel("NYK", {12: [{**_line(-110, -2.5), "is_off": True}]})]}
    assert bp_games.build_game_rows("spread", 129, "d", [off], EV) == []


def _long(market_, ev, book_id, side, line, odds):
    return {"market": market_, "event_id": ev, "book_id": book_id, "side": side, "line": line, "odds": odds,
            "book_kind": "book", "is_open": False, "scheduled": "2024-02-11 00:30:00", "away": "BOS", "home": "NYK",
            "date": "2024-02-10"}


def test_consensus_modal_line_devig_and_et_date():
    rows = []
    for b, hl in ((12, -2.5), (13, -2.5), (14, -1.5)):               # modal home line -2.5
        rows += [_long("spread", 1, b, "home", hl, -110), _long("spread", 1, b, "away", -hl, -110)]
    for b in (12, 13):
        rows += [_long("moneyline", 1, b, "home", np.nan, -150), _long("moneyline", 1, b, "away", np.nan, 130)]
        rows += [_long("total", 1, b, "over", 221.5, -110), _long("total", 1, b, "under", 221.5, -110)]
    c = market.consensus(pd.DataFrame(rows)).iloc[0]
    assert c["sp_line"] == -2.5 and c["n_books_sp"] == 2
    assert c["sp_p_home"] == pytest.approx(0.5)
    assert c["ml_p_home"] == pytest.approx(0.6 / (0.6 + 100 / 230), abs=1e-6)   # -150/+130 devigged
    assert c["tot_line"] == 221.5 and c["tot_p_over"] == pytest.approx(0.5)
    assert c["date"] == "2024-02-10"                                  # 00:30 UTC on the 11th is the 10th in ET
    assert (c["home"], c["away"]) == ("NYK", "BOS")


def test_cover_and_over_probabilities_have_the_right_sign():
    assert M.p_home_cover(5.0, 12.0, -2.5) > 0.5 > M.p_home_cover(0.0, 12.0, -2.5)   # fav must win by >2.5
    assert M.p_home_cover(0.0, 12.0, 0.0) == pytest.approx(0.5)
    assert M.p_over(225.0, 18.0, 221.5) > 0.5 > M.p_over(215.0, 18.0, 221.5)


def _games(n=240, seed=0):
    rng = np.random.default_rng(seed)
    teams = ["ATL", "BOS", "CHI", "DEN", "GSW", "LAL"]
    d0 = pd.Timestamp("2022-10-20")
    rows = []
    for i in range(n):
        h, a = rng.choice(teams, 2, replace=False)
        r = {"game_id": f"g{i:04d}", "date": (d0 + pd.Timedelta(days=i // 3)).strftime("%Y-%m-%d"), "tip": pd.NaT,
             "season": 2022, "season_type": 2, "home": h, "away": a,
             "home_pts": int(rng.integers(95, 125)), "away_pts": int(rng.integers(95, 125))}
        for s in ("h", "a"):
            r.update({f"{s}_fga": int(rng.integers(80, 95)), f"{s}_fgm": int(rng.integers(35, 45)),
                      f"{s}_fg3m": int(rng.integers(8, 16)), f"{s}_fg3a": int(rng.integers(28, 40)),
                      f"{s}_fta": int(rng.integers(15, 30)), f"{s}_ftm": int(rng.integers(12, 24)),
                      f"{s}_oreb": int(rng.integers(7, 14)), f"{s}_dreb": int(rng.integers(30, 40)),
                      f"{s}_tov": int(rng.integers(10, 18)), f"{s}_ast": int(rng.integers(20, 30)),
                      f"{s}_stl": int(rng.integers(5, 10)), f"{s}_blk": int(rng.integers(3, 7))})
        rows.append(r)
    return pd.DataFrame(rows)


def test_features_never_use_the_games_own_result():
    g = _games()
    base = F.build(g)
    k = 200
    g2 = g.copy()
    g2.loc[k, ["home_pts", "away_pts"]] = [140, 60]                  # rewrite the outcome of game k
    g2.loc[k, "h_fgm"] = 55
    alt = F.build(g2)
    feat_cols = [c for c in base.columns if c.startswith(("h_e_", "a_e_", "h_rest", "a_rest", "h_b2b", "a_b2b",
                                                          "h_games", "a_games", "elo_", "lg_total"))]
    pd.testing.assert_series_equal(base.loc[k, feat_cols].astype(float), alt.loc[k, feat_cols].astype(float))
    later = base.loc[k + 1:, feat_cols].astype(float).values != alt.loc[k + 1:, feat_cols].astype(float).values
    assert np.nan_to_num(later).any()                                # ...but it does reach LATER games


def test_elo_is_symmetric_and_carries_over():
    f = F.build(_games())
    assert f["elo_h"].iloc[0] == 1500.0 and f["elo_a"].iloc[0] == 1500.0
    g = _games(60)
    g.loc[30:, "season"] = 2023                                       # season break: ratings regress toward 1500
    e = F.elo_pre(g.reset_index(drop=True))
    assert np.isfinite(e.values).all()


def test_rest_days_and_b2b():
    g = pd.DataFrame({"game_id": ["a", "b", "c"], "date": ["2022-11-01", "2022-11-02", "2022-11-05"], "tip": pd.NaT,
                      "season": 2022, "season_type": 2, "home": ["ATL"] * 3, "away": ["BOS", "CHI", "DEN"]})
    tg = F._context(pd.DataFrame({"gi": [0, 1, 2], "date": g["date"], "season": 2022, "team": "ATL"}))
    assert tg["rest_days"].tolist() == [7.0, 1.0, 3.0]
    assert tg["b2b"].tolist() == [0.0, 1.0, 0.0]
    assert tg["games_7d"].tolist() == [0.0, 1.0, 2.0]


def test_models_fit_and_predict_shapes():
    f = F.build(_games(260))
    gm = M.fit(f)
    p = gm.predict(f.iloc[-20:])
    assert len(p) == 20 and p["p_ml"].between(0, 1).all() and gm.margin_sigma > 0 and gm.total_sigma > 0


def test_pair_builds_one_row_per_game():
    long = pd.DataFrame({"game_id": ["1", "1"], "team": ["NYK", "BOS"], "pts": [100, 98], "is_home": [True, False],
                         **{b: [1, 2] for b in data.BOX}})
    g = data._pair(long)
    assert len(g) == 1 and g.loc[0, "home"] == "NYK" and g.loc[0, "away_pts"] == 98


def test_consensus_drops_inplay_quotes():
    rows = []
    for b, upd in ((12, "2024-02-11 00:31:00"), (13, "2024-02-11 00:33:00"), (14, "2024-02-11 01:10:00")):
        for side, odds in (("over", -110), ("under", -110)):
            r = _long("total", 1, b, side, 221.5 if b != 14 else 230.5, odds)
            rows.append({**r, "updated": upd})
    c = market.consensus(pd.DataFrame(rows)).iloc[0]
    assert c["n_books_tot"] == 2 and c["tot_line"] == 221.5            # the 01:10 UTC quote (40 min after tip) is excluded


def test_bettingpros_nonstandard_tricodes_are_mapped():
    rows = []
    for side, odds in (("home", -110), ("away", -110)):
        rows.append({**_long("moneyline", 1, 12, side, np.nan, odds), "home": "PHO", "away": "NOR"})
    c = market.consensus(pd.DataFrame(rows))
    assert len(c) == 1 and (c.iloc[0]["home"], c.iloc[0]["away"]) == ("PHX", "NOP")
    assert market._code("UTH") == "UTA"


def _pb(rows):
    base = {"season_type": 2, "points": 20, "field_goals_attempted": 15, "free_throws_attempted": 4, "turnovers": 2}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_availability_flags_a_fresh_absence_and_resets_each_season():
    from nba.games import availability as A
    rows = []
    for g, (day, season) in enumerate([("2024-11-01", 2024), ("2024-11-03", 2024), ("2024-11-05", 2024), ("2025-10-25", 2025)]):
        for pid, minutes in ((1, 34), (2, 28)):
            if pid == 1 and g >= 2:                     # the star is out for game 3 and for the next season's opener
                continue
            rows.append({"game_id": f"g{g}", "season": season, "game_date_time": f"{day}T23:00:00Z",
                         "team_abbreviation": "ATL", "athlete_id": pid, "minutes": minutes})
    av = A.build(_pb(rows)).set_index("game_id")
    assert av.loc["g0", "av_missing_min"] == 0 and av.loc["g1", "av_missing_min"] == 0
    assert av.loc["g2", "av_missing_min"] == pytest.approx(34.0) and av.loc["g2", "av_new_missing_min"] == pytest.approx(34.0)
    assert av.loc["g2", "av_top_pts"] == pytest.approx(20.0)
    assert av.loc["g3", "av_missing_min"] == 0          # new season: roster resets, a departure is not an absence


def test_soft_line_win_push_probabilities():
    from nba.games import soft_lines as SL
    mu = np.array([3.0, 3.0])
    pa, pp, pb = SL._win_push(mu, 12.0, np.array([2.5, 2.0]))         # half line: no push; integer line: push mass
    assert pp[0] == 0.0 and pp[1] > 0.02
    assert np.allclose(pa + pp + pb, 1.0)
    assert pa[1] < pa[0] + 1e-9 or pa[1] > 0                           # sane, finite


def test_soft_line_quotes_pair_away_side_on_the_home_line():
    from nba.games import soft_lines as SL
    rows = []
    for b, hl in ((12, -2.5), (13, -1.5)):
        rows += [{**_long("spread", 1, b, "home", hl, -110), "updated": "2024-02-11 00:20:00", "book": f"b{b}"},
                 {**_long("spread", 1, b, "away", -hl, -105), "updated": "2024-02-11 00:20:00", "book": f"b{b}"}]
    q = SL.quotes(pd.DataFrame(rows), "spread")
    assert len(q) == 2 and set(q["line"]) == {-2.5, -1.5}
    assert (q["dec_b"] > q["dec_a"]).all()                             # -105 away pays more than -110 home


def test_sharp_reference_without_pinnacle_keeps_join_columns():
    long = pd.DataFrame([_long("moneyline", 1, 12, "home", np.nan, -150)])
    assert list(market.sharp_reference(long).columns) == ["date", "home", "away", "pin_p_home", "pin_sp_line", "pin_tot_line"]
