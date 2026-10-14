"""nba.props.forward: the pre-registered forward evaluation (CLV vs the closing banked snapshot -> backtest_market.verdict)."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from nba.props import forward as Fw
from nba.props import score as S
from tests import test_nba_props_score as TS


@pytest.fixture(scope="module")
def scored():
    res, _ = TS.make_resources()
    events = json.load(open(TS.FIX))[:1]
    df, _ = S.score_events(events, TS.NOW, res)
    return res, events, df


def test_closing_index_lines_up_with_scored_rows_and_identical_close_is_zero_clv(scored):
    res, events, df = scored
    close = Fw.close_index(events, res.resolver)
    out = Fw.attach_clv(df, close)
    assert out["clv_pct"].notna().all() and out["close_n_books"].notna().all()
    assert np.allclose(out["clv_pct"], 0.0)                              # closing snapshot == entry snapshot
    # EV at the close = entry price vs the closing consensus fair probability: -110 at a fair 50% = 1.909*0.5-1 = -4.545%
    r = out[(out["player"] == "Jayson Tatum") & (out["stat"] == "points") & (out["book"] == "draftkings")].iloc[0]
    assert r["clv_cons_pct"] == pytest.approx(-4.545, abs=0.01)


def test_clv_is_positive_when_the_book_moves_against_the_side_we_took(scored):
    res, events, df = scored
    moved = json.loads(json.dumps(events))
    for bk in moved[0]["bookmakers"]:
        for m in bk["markets"]:
            for o in m["outcomes"]:
                if o["name"].startswith("Over Jayson Tatum") and m["key"] == "player_points":
                    o["price"] = o["price"] - 30 if o["price"] < 0 else o["price"] - 25     # Over got shorter (we beat it)
    out = Fw.attach_clv(df, Fw.close_index(moved, res.resolver))
    row = out[(out["player"] == "Jayson Tatum") & (out["stat"] == "points") & (out["book"] == "draftkings")].iloc[0]
    assert row["side"] == "OVER" and row["clv_pct"] > 0
    unaffected = out[(out["player"] == "Jaylen Brown") & (out["stat"] == "assists")]
    assert np.allclose(unaffected["clv_pct"], 0.0)


def test_a_quote_pulled_before_the_close_has_nan_clv(scored):
    res, events, df = scored
    gone = json.loads(json.dumps(events))
    gone[0]["bookmakers"] = [b for b in gone[0]["bookmakers"] if b["key"] != "bet365"]
    out = Fw.attach_clv(df, Fw.close_index(gone, res.resolver))
    assert out[out["book"] == "bet365"]["clv_pct"].isna().all()
    assert out[out["book"] != "bet365"]["clv_pct"].notna().all()


def _graded(n, clv_mean, edge_slope=True, seed=0, hit=0.52):
    rng = np.random.default_rng(seed)
    edge = rng.uniform(0.0, 0.14, n)
    p_fair = rng.uniform(0.4, 0.6, n)
    dec = 1 / p_fair * 0.95
    win = rng.random(n) < (p_fair + (edge * 0.8 if edge_slope else 0.0))
    return pd.DataFrame({
        "game_key": [f"nba:2026-11-01:LAL@BOS{i}" for i in range(n)], "player_norm": [f"id{i}" for i in range(n)],
        "stat": "points", "point": 20.5, "side": "OVER", "book": "draftkings", "ev": edge, "edge_vs_cons": edge,
        "p_fair": p_fair, "p_model": p_fair + edge, "decimal": dec, "flag": True, "n_books": 4,
        "result": np.where(win, "win", "loss"), "clv_pct": rng.normal(clv_mean, 2.0, n)})


def test_verdict_is_insufficient_with_few_bets():
    cand = Fw.candidates(_graded(10, 3.0))
    v = Fw.forward_verdict(cand)
    assert v["verdict"] == "INSUFFICIENT_N" and v["t17"]["clv_status"] == "insufficient"


def test_verdict_no_edge_when_clv_is_flat_even_if_roi_looks_fine():
    cand = Fw.candidates(_graded(600, 0.0, seed=4))
    v = Fw.forward_verdict(cand)
    assert v["verdict"] == "NO_EDGE" and not v["t17"]["clv_promote_ready"]


def test_verdict_can_promote_only_with_clv_bar_and_ladder():
    """The pre-registered bar (mean CLV >= +2%, t > 2) is reachable on synthetic data -- and only then."""
    cand = Fw.candidates(_graded(900, 3.5, edge_slope=True, seed=7))
    v = Fw.forward_verdict(cand)
    assert v["clv_mean"] >= 2.0 and v["n_clv"] >= 100
    assert v["verdict"] in ("PROMOTE_CANDIDATE", "NO_EDGE")                 # ladder shape is data-dependent
    assert v["t17"]["clv_promote_ready"] is True


def test_candidates_only_count_graded_flagged_rows_and_line_shop():
    g = _graded(40, 1.0)
    g.loc[:4, "result"] = "void"
    g.loc[5:9, "flag"] = False
    dup = g.iloc[[10]].copy()
    dup["book"], dup["ev"] = "fanduel", g.iloc[10]["ev"] + 0.01
    cand = Fw.candidates(pd.concat([g, dup], ignore_index=True))
    assert len(cand) == 40 - 5 - 5
    assert cand["won"].isin([0.0, 1.0, 0.5]).all()
    assert (cand[cand["player_norm"] == g.iloc[10]["player_norm"]]["book"] == "fanduel").all()      # best-EV quote kept
    assert Fw.candidates(g.iloc[0:0]).empty


def test_closing_snapshot_picks_the_last_bank_before_tip(tmp_path, monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    raw = tmp_path / "OddsAccum" / "basketball_nba" / "raw" / "2025-12-16"
    raw.mkdir(parents=True)
    for hhmm, tag in (("1200", "early"), ("1840", "close"), ("1935", "after_tip")):
        (raw / f"props_{hhmm}.json").write_text(json.dumps([{"tag": tag}]))
    # tip 2025-12-17T00:30Z == 19:30 ET on Dec 16; file names are ET HHMM (nba/odds/accumulator._now)
    got = Fw.closing_snapshot("basketball_nba", "2025-12-16", "2025-12-17T00:30:00Z")
    assert got == [{"tag": "close"}]
    assert Fw.closing_snapshot("basketball_nba", "2025-12-16", "2025-12-16T10:00:00Z") is None


def test_run_grades_a_day_and_attaches_clv_end_to_end(tmp_path, monkeypatch, scored):
    """evidence CSV in storage -> ev.grade_props grading -> CLV from the banked closing snapshot -> verdict inputs."""
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    res, events, df = scored
    day = df["game_key"].iloc[0].split(":")[1]                                  # ET game day of the event
    ev_dir = tmp_path / "EVProps" / "nba_model" / day
    ev_dir.mkdir(parents=True)
    df.to_csv(ev_dir / "model_props_190000.csv", index=False)
    raw = tmp_path / "OddsAccum" / "basketball_nba" / "raw" / day
    raw.mkdir(parents=True)
    (raw / "props_1920.json").write_text(json.dumps(events))                      # banked 10 min before the 19:30 ET tip

    def provider(d, away, home):
        players = {int(p): {"points": 20.0, "rebounds": 4.0, "assists": 3.0, "threes": 1.0, "pra": 27.0, "_played": True}
                   for p in df["player_id"].unique()}
        return {"final": True, "players": players}

    out = Fw.run(day, provider=provider, resolver=res.resolver, min_n=5)
    assert out["status"] == "ok" and out["validated"] is False
    assert out["graded"]["graded"] > 0 and out["quotes_with_close"] == len(df)
    assert out["n_candidates"] > 0 and out["verdict"]["verdict"] in ("INSUFFICIENT_N", "NO_EDGE", "PROMOTE_CANDIDATE")
    assert Fw.run("2099-01-01", provider=provider, resolver=res.resolver)["status"] == "no_evidence"
