"""Evaluator hooks that score the NFL / NBA / NHL prop models against HISTORICAL BettingPros consensus lines.

Synthetic frames only (no storage, no real data): join counts, dropped-unresolved counting, the coverage invariant
(matched + dropped == consensus rows), and flag-off = unchanged behaviour."""
import numpy as np
import pandas as pd
import pytest

from mlb_core.odds import bettingpros_loader as L
from mlb_core.odds import model_vs_market as MVM


def cons_row(player, team="", game_date="2025-09-07", stat="receptions", line=4.5, p=0.55, n=5, pregame=True,
             away="AWY", home="HOM", event_id=1, player_id=1):
    """One row in the bettingpros_loader.OUTPUT_COLUMNS shape."""
    return {"date": game_date, "game_date": game_date, "sport": "X", "event_id": event_id, "player_id": player_id,
            "player": player, "player_norm": player.lower(), "team": team, "away": away, "home": home,
            "scheduled": game_date + " 23:00:00", "stat": stat, "line": line, "p_over_cons": p, "n_books": n,
            "disp": 0.01, "best_over_odds": -105.0, "best_under_odds": -105.0, "novig_over_odds": np.nan,
            "novig_under_odds": np.nan, "updated_max": game_date + " 20:00:00", "pregame": pregame}


def cons_frame(rows):
    df = pd.DataFrame(rows, columns=L.OUTPUT_COLUMNS)
    df["pregame"] = df["pregame"].astype("boolean")
    return df


def test_coverage_invariant_is_enforced():
    assert MVM.coverage(5, 3, {"a": 2})["matched"] == 3
    with pytest.raises(ValueError):
        MVM.coverage(5, 3, {"a": 1})


def test_score_model_vs_market_matches_hand_values():
    y = np.array([1.0, 0.0, 1.0, 0.0])
    s = MVM.score_model_vs_market(y, [0.8, 0.2, 0.6, 0.4], [0.5, 0.5, 0.5, 0.5], [1, 1, 2, 2])
    assert s["n"] == 4 and s["n_clusters"] == 2
    assert s["brier_market"] == pytest.approx(0.25)
    assert s["brier_model"] == pytest.approx(np.mean([0.04, 0.04, 0.16, 0.16]))
    assert s["d_brier"] < 0 and s["d_logloss"] < 0 and s["blend"] is None          # too few rows for the regression
    assert MVM.score_model_vs_market([], [], [], [])["n"] == 0


# ------------------------------------------------------------------------------------------------------- NFL
def _nfl_world():
    from nfl.props.resolve import PlayerResolver
    r = []
    for gid, name, team, pos in (("00-MAH", "Pat Mahomes", "KC", "QB"), ("00-ADA", "Davante Adams", "LV", "WR"),
                                 ("00-HIL", "Tyreek Hill", "MIA", "WR"), ("00-KEL", "Travis Kelce", "KC", "TE"),
                                 ("00-KUP", "Cooper Kupp", "LA", "WR"), ("00-BQB", "Josh Allen", "BUF", "QB"),
                                 ("00-JLB", "Josh Allen", "JAX", "LB")):
        first, last = name.split()
        r.append(dict(gsis_id=gid, full_name=name, team=team, position=pos, status="ACT", season=2025, week=1,
                      first_name=first, last_name=last, football_name=None))
    sched = pd.DataFrame([dict(season=2025, week=1, gameday="2025-09-07", away_team="MIA", home_team="KC"),
                          dict(season=2025, week=1, gameday="2025-09-07", away_team="LV", home_team="BUF"),
                          dict(season=2025, week=1, gameday="2025-09-07", away_team="JAX", home_team="LA")])
    resolver = PlayerResolver(pd.DataFrame(r), schedules=sched)       # game_date -> (season, week), as the real mirror
    # model rows: Mahomes, Adams, Hill, Kupp (NOT Kelce, NOT either Josh Allen)
    df = pd.DataFrame({"player_id": ["00-MAH", "00-ADA", "00-HIL", "00-KUP"], "gameday": ["2025-09-07"] * 4,
                       "game_id": ["g1", "g1", "g2", "g3"], "season": 2025, "week": 1, "is_upcoming": False})
    res = pd.DataFrame({"y": [6.0, 2.0, 5.0, 5.0], "mu_model": [5.0, 4.0, 4.5, 5.0]}, index=df.index)
    res.attrs["dispersion"] = {"model": {"family": "negbin", "alpha": 0.1}}
    return resolver, df, res


def _nfl_cons():
    return cons_frame([
        cons_row("Pat Mahomes", "KC", line=4.5, p=0.55),                       # matched, over hits (6 > 4.5)
        cons_row("Davante Adams", "LV", line=3.5, p=0.50),                     # matched, under (2 < 3.5)
        cons_row("Tyreek Hill", "MIA", line=4.5, p=0.50, event_id=5),          # duplicate pair -> both dropped
        cons_row("Tyreek Hill", "MIA", line=5.5, p=0.40, event_id=6),
        cons_row("Nobody Here", "", line=2.5),                                 # unresolved
        cons_row("Josh Allen", "", line=2.5),                                  # ambiguous namesakes, no hint
        cons_row("Travis Kelce", "KC", line=5.5),                              # resolved, no model row
        cons_row("Pat Mahomes", "KC", line=4.5, n=2),                          # low_books
        cons_row("Davante Adams", "LV", line=3.5, pregame=False),              # postgame quote
        cons_row("Cooper Kupp", "LA", line=5.0, p=0.5),                        # integer line, y == line -> push
        cons_row("Pat Mahomes", "KC", stat="pass_yds", line=250.5),            # other stat: not part of this join
    ])


def test_nfl_join_counts_and_coverage():
    from nfl.models import props as P
    from nfl.props import history_join as HJ
    resolver, df, res = _nfl_world()
    joined, cov = HJ.join_consensus(res, df, _nfl_cons(), resolver, "receptions", min_books=3)
    assert cov["consensus_rows"] == 10                                         # the pass_yds row is not counted
    assert cov["matched"] == 2 and len(joined) == 2
    assert cov["dropped"] == {"low_books": 1, "postgame_quote": 1, "unresolved": 1, "ambiguous": 1, "duplicate": 2,
                              "no_model_row": 1, "push": 1}
    assert cov["matched"] + sum(cov["dropped"].values()) == cov["consensus_rows"]
    j = joined.set_index("gsis_id")
    # model P(over) is evaluated at the MARKET line with the model's own dispersion
    want = P.p_over("receptions", 5.0, 4.5, {"family": "negbin", "alpha": 0.1})[0]
    assert j.loc["00-MAH", "p_model"] == pytest.approx(want)
    assert j.loc["00-MAH", "p_market"] == pytest.approx(0.55)
    assert j.loc["00-MAH", "over"] == 1.0 and j.loc["00-ADA", "over"] == 0.0
    sc = HJ.score(joined)
    assert sc["n"] == 2 and 0 <= sc["brier_model"] <= 1 and sc["brier_market"] == pytest.approx(
        np.mean([(0.55 - 1) ** 2, (0.5 - 0) ** 2]))


def test_nfl_empty_consensus_and_unknown_stat():
    from nfl.props import history_join as HJ
    resolver, df, res = _nfl_world()
    empty = pd.DataFrame(columns=L.OUTPUT_COLUMNS)
    joined, cov = HJ.join_consensus(res, df, empty, resolver, "receptions")
    assert len(joined) == 0 and cov["consensus_rows"] == 0 and cov["matched"] == 0
    only_other = cons_frame([cons_row("Pat Mahomes", "KC", stat="pass_yds", line=250.5)])
    assert HJ.join_consensus(res, df, only_other, resolver, "receptions")[1]["consensus_rows"] == 0
    assert HJ.score(joined)["n"] == 0


def test_nfl_market_compare_and_flag_off_main(tmp_path, monkeypatch, capsys):
    from nfl.models import walkforward as wf
    from nfl.props import resolve as R
    resolver, df, res = _nfl_world()
    mc = wf.market_compare(res, df, "receptions", _nfl_cons(), resolver)
    assert mc["coverage"]["matched"] == 2 and mc["scores"]["n"] == 2 and len(mc["joined"]) == 2
    text = wf.render_market_section({"receptions": {2025: mc, 2024: None}})
    assert "BettingPros" in text and "2/10 consensus rows scored" in text and "too few eligible" in text

    cache = tmp_path / "feat.parquet"
    df.to_parquet(cache)
    cons_path = tmp_path / "cons.parquet"
    _nfl_cons().to_parquet(cons_path)
    monkeypatch.setattr(wf, "eval_season", lambda d, stat, s, **kw: (res, None))
    monkeypatch.setattr(wf, "summarize", lambda r, stat: {"n": len(r)})
    monkeypatch.setattr(wf, "render_report", lambda results, params: "BASE REPORT")
    monkeypatch.setattr(R.PlayerResolver, "from_mirror", classmethod(lambda cls, seasons: resolver))
    base_args = ["--stats", "receptions", "--seasons", "2025", "--features-cache", str(cache)]
    assert wf.main(base_args) == 0
    off = capsys.readouterr().out
    assert off.strip() == "BASE REPORT"                                           # flag off: report untouched
    assert wf.main(base_args + ["--market-parquet", str(cons_path)]) == 0
    on = capsys.readouterr().out
    assert on.startswith("BASE REPORT") and "Model vs historical BettingPros market" in on


# ------------------------------------------------------------------------------------------------------- NBA
def _nba_world():
    d1, d2 = pd.Timestamp("2025-11-05"), pd.Timestamp("2025-11-06")
    people = [("u-lbj", "LeBron James", "LAL"), ("u-jose", "Jose Alvarado", "NOP"), ("u-jw1", "Jaylin Williams", "OKC"),
              ("u-jw2", "Jaylin Williams", "MIL"), ("u-ad", "Anthony Davis", "DAL"), ("u-unscored", "Bench Guy", "LAL")]
    rows = [{"player_id": pid, "name": nm, "team": tm, "date": d1, "game_id": "g1", "season_year": 2025}
            for pid, nm, tm in people]
    rows.append({"player_id": "u-lbj", "name": "LeBron James", "team": "LAL", "date": d2, "game_id": "g2",
                 "season_year": 2025})
    meta = pd.DataFrame(rows)
    scored = [0, 1, 2, 3, 4, 6]                      # every row except 'Bench Guy' (index 5)
    mu = np.array([25.0, 8.0, 10.0, 10.0, 24.0, 26.0])
    y = np.array([30.0, 8.0, 12.0, 9.0, 24.0, 22.0])
    ev = {"pooled": {"points": {"y": y, "mu": mu, "var_g": mu * 3.0, "fam": np.array(["nbinom"] * 6, dtype=object),
                                "idx": np.array(scored), "season": np.array([2025] * 6), "grp": meta["date"].to_numpy()[scored]}}}
    return ev, meta


def _nba_cons():
    return cons_frame([
        cons_row("LeBron James", game_date="2025-11-05", stat="pts", line=24.5, p=0.58),        # matched (30 > 24.5)
        cons_row("Jos\u00e9 Alvarado", game_date="2025-11-05", stat="pts", line=8.5, p=0.45),    # accent-folded match
        cons_row("Jaylin Williams", game_date="2025-11-05", stat="pts", line=9.5),               # two on the card: ambiguous
        cons_row("Zed Nobody", game_date="2025-11-05", stat="pts", line=9.5),                    # unresolved
        cons_row("Bench Guy", game_date="2025-11-05", stat="pts", line=5.5),                     # no scored model row
        cons_row("LeBron James", game_date="2025-11-20", stat="pts", line=24.5),                 # date not in meta
        cons_row("Anthony Davis", game_date="2025-11-05", stat="pts", line=24.0),                # whole line == y: push
        cons_row("LeBron James", game_date="2025-11-06", stat="pts", line=25.5, n=1),            # low_books
        cons_row("LeBron James", game_date="2025-11-06", stat="pts", line=25.5, pregame=False),  # postgame
        cons_row("LeBron James", game_date="2025-11-06", stat="reb", line=7.5),                  # other stat (no 'reb' in ev)
    ])


def test_nba_join_counts_coverage_and_p_model():
    from nba.props import dist as Dd
    from nba.props import evaluate as E
    from nba.props import history_join as HJ
    ev, meta = _nba_world()
    rep = E.market_report(ev, None, meta, _nba_cons(), tables={}, min_books=3)
    pts = rep["by_stat"]["points"]
    cov = pts["coverage"]
    assert cov["consensus_rows"] == 9 and cov["matched"] == 2 and len(pts["frame"]) == 2
    assert cov["dropped"]["ambiguous"] == 1 and cov["dropped"]["unresolved"] == 1 and cov["dropped"]["push"] == 1
    assert cov["dropped"]["low_books"] == 1 and cov["dropped"]["postgame_quote"] == 1
    assert cov["dropped"]["no_model_row"] == 2 and cov["dropped"]["duplicate"] == 0
    assert cov["matched"] + sum(cov["dropped"].values()) == cov["consensus_rows"]
    f = pts["frame"].set_index("player_id")
    want = Dd.p_over_cal(24.5, 25.0, 75.0, "nbinom", None) / (1 - Dd.p_push_cal(24.5, 25.0, 75.0, "nbinom", None))
    assert f.loc["u-lbj", "p_model"] == pytest.approx(float(want))
    assert f.loc["u-lbj", "over"] == 1.0 and f.loc["u-jose", "over"] == 0.0     # 8 vs 8.5 -> under
    assert rep["all"]["coverage"]["matched"] == 2 and pts["scores"]["n"] == 2
    assert "BettingPros" in HJ.render_market_section(rep)


def test_nba_duplicate_consensus_rows_are_dropped_not_averaged():
    from nba.props import history_join as HJ
    ev, meta = _nba_world()
    cons = cons_frame([cons_row("LeBron James", game_date="2025-11-05", stat="pts", line=24.5, event_id=1),
                       cons_row("LeBron James", game_date="2025-11-05", stat="pts", line=25.5, event_id=2)])
    joined, cov = HJ.join_stat(ev, meta, cons, "points")
    assert len(joined) == 0 and cov["dropped"]["duplicate"] == 2


def test_nba_flag_off_and_empty_consensus_leave_ev_untouched():
    import copy

    from nba.props import evaluate as E
    ev, meta = _nba_world()
    before = copy.deepcopy(ev)
    rep = E.market_report(ev, None, meta, pd.DataFrame(columns=L.OUTPUT_COLUMNS))
    assert rep["by_stat"]["points"]["coverage"]["consensus_rows"] == 0 and rep["all"]["scores"]["n"] == 0
    for k in ("y", "mu", "var_g", "idx", "season"):
        np.testing.assert_array_equal(ev["pooled"]["points"][k], before["pooled"]["points"][k])


# ------------------------------------------------------------------------------------------------------- NHL
def test_nhl_add_model_default_cutoffs_unchanged_and_custom_cutoffs_skip_unfittable(monkeypatch):
    from nhl.props import study
    from tests.test_nhl_props_features import synth_gamelog
    gl = synth_gamelog(n_days=20)
    seen = []

    def fake_fit(feat, cutoff):
        seen.append(cutoff)
        if cutoff == "BAD":
            raise RuntimeError("too few rows")
        return {"meta": {"nb_alpha": 0.1}}

    monkeypatch.setattr(study.train, "fit", fake_fit)
    monkeypatch.setattr(study.train, "predict_mu", lambda art, f: np.full(len(f), 2.0))
    pairs = pd.DataFrame({"game_id": [2025020001], "player_id": [1], "game_date": ["2025-10-10"], "line": [1.5],
                          "status": ["ok"]})
    study.add_model(pairs, gl)
    assert seen == study.CUTOFFS                                               # flag off: pre-registered cutoffs
    seen.clear()
    p, arts, _ = study.add_model(pairs, gl, cutoffs=["2025-10-01", "BAD"])
    assert seen == ["2025-10-01", "BAD"] and list(arts) == ["2025-10-01"]


def _nhl_world():
    from tests.test_nhl_props_features import TEAMS, synth_gamelog
    gl = synth_gamelog(n_days=170)
    names = pd.DataFrame([{"player_id": TEAMS.index(t) * 100 + k, "full_name": f"Skater{k} Team{t.lower()}", "team": t}
                          for t in TEAMS for k in range(6)])
    # a roster-only skater who never appears in a game log row -> resolves but VOID; two same-name skaters -> ambiguous
    extra = pd.DataFrame([{"player_id": 999, "full_name": "Ghost Skater", "team": "AAA"},
                          {"player_id": 881, "full_name": "Twin Name", "team": "BBB"},
                          {"player_id": 882, "full_name": "Twin Name", "team": "BBB"}])
    return gl, pd.concat([names, extra], ignore_index=True)


def _late_game(gl):
    d = sorted(gl["date"].unique())[-10]                                       # a mid-season game day, well after Jan
    g = gl[gl["date"] == d].iloc[0]
    home = gl[(gl["game_id"] == g["game_id"]) & (gl["is_home"] == 1)]["team"].iloc[0]
    away = gl[(gl["game_id"] == g["game_id"]) & (gl["is_home"] == 0)]["team"].iloc[0]
    return d, g["game_id"], home, away


def test_nhl_hook_counts_and_end_to_end_model_scoring():
    from nhl.props import study
    gl, names = _nhl_world()
    d, gid, home, away = _late_game(gl)
    team_rows = gl[(gl["game_id"] == gid) & (gl["team"] == home)].sort_values("player_id")
    pid0, pid1, pid2 = (int(x) for x in team_rows["player_id"].iloc[:3])
    nm = lambda pid, t: f"Skater{pid % 100} Team{t.lower()}"                   # noqa: E731
    sog1 = float(team_rows[team_rows["player_id"] == pid1]["sog"].iloc[0])

    def row(player, line, home=home, away=away, **kw):
        return cons_row(player, home, game_date=d, stat="sog", line=line, p=0.5, home=home, away=away, **kw)

    cons = cons_frame([
        row(nm(pid0, home), 1.5, event_id=1), row(nm(pid2, home), 2.5, event_id=2),      # matched (x2)
        row(nm(pid1, home), sog1, event_id=3),                                           # integer line == sog: push
        row("Nobody Atall", 1.5, event_id=4),                                            # unresolved
        row("Twin Name", 1.5, home="BBB", away="AAA", event_id=5),                       # unresolved-in-game -> no_game
        row("Ghost Skater", 1.5, event_id=6),                                            # void (did not appear)
        row(nm(pid0, home), 1.5, event_id=7, n=1),                                       # low_books
        row(nm(pid0, home), 1.5, event_id=8, pregame=False),                             # postgame_quote
        row(nm(pid0, home), 1.5, home="ZZZ", away="YYY", event_id=9),                    # team codes match no game
    ])
    out = study.bettingpros_model_vs_market(cons, gl, names, min_books=3)
    cov = out["coverage"]
    assert cov["consensus_rows"] == 9
    assert cov["matched"] + sum(cov["dropped"].values()) == 9
    d_ = cov["dropped"]
    assert d_["low_books"] == 1 and d_["postgame_quote"] == 1 and d_["unresolved"] == 1 and d_["void"] == 1
    assert d_["no_game"] == 2 and d_["push"] == 1
    assert cov["matched"] == 2 and len(out["frame"]) == 2
    assert out["scores"]["n"] == 2 and np.isfinite(out["scores"]["brier_model"])
    assert out["cutoffs"] == study.month_cutoffs([d])
    assert set(out["frame"]["status"]) == {"ok"} and out["frame"]["p_model"].between(0, 1).all()


def test_nhl_hook_ambiguous_namesakes_are_dropped_and_counted():
    from nhl.props import study
    gl, names = _nhl_world()
    d, gid, home, away = _late_game(gl)
    other = gl[(gl["game_id"] == gid)]["team"].unique()
    cons = cons_frame([cons_row("Twin Name", "BBB", game_date=d, stat="sog", line=1.5, home=other[0], away=other[1])])
    # force both Twins onto this game's teams so the resolver sees two candidates inside the event
    names2 = names.copy()
    names2.loc[names2["full_name"] == "Twin Name", "team"] = other[0]
    out = study.bettingpros_model_vs_market(cons, gl, names2, min_books=3)
    assert out["coverage"]["dropped"]["ambiguous"] == 1 and out["coverage"]["matched"] == 0


def test_nhl_hook_empty_consensus():
    from nhl.props import study
    gl, names = _nhl_world()
    out = study.bettingpros_model_vs_market(pd.DataFrame(columns=L.OUTPUT_COLUMNS), gl, names)
    assert out["coverage"]["consensus_rows"] == 0 and len(out["frame"]) == 0 and out["scores"]["n"] == 0
