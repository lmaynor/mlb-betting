"""Offline tests: BettingPros NHL game-line crawler / reader and the NHL engine-vs-closing-line scorer."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mlb_core.odds import bettingpros_games as bg
from nhl.game import market_eval as me


# ------------------------------------------------------------------------------------------------ fixtures
def _line(cost, line, main=True, updated="2024-01-15 22:50:00", off=False):
    return {"cost": cost, "line": line, "main": main, "updated": updated, "is_off": off}


def _book(bid, *lines):
    return {"id": bid, "lines": list(lines)}


def _offer(eid, selections, market_id=193):
    return {"id": f"o{eid}", "market_id": market_id, "event_id": eid, "selections": selections}


EV = {101: {"away": "SJ", "home": "BUF", "scheduled": "2024-01-15 23:00:00", "season_type": "REG", "status": "closed"}}


def test_build_game_rows_moneyline_spread_total():
    ml = _offer(101, [
        {"participant": "BUF", "selection": "", "opening_line": {"line": 1, "cost": -235, "book_id": 18, "created": "2024-01-14 11:17:47"},
         "books": [_book(0, _line(-278, 1)), _book(12, _line(-278, 1)), _book(10, _line(-280, 1, off=True))]},
        {"participant": "SJ", "selection": "", "opening_line": None, "books": [_book(12, _line(230, 1))]}])
    rows = bg.build_game_rows("moneyline", 193, "2024-01-15", [ml], EV)
    df = bg.to_frame(rows)
    assert set(df["side"]) == {"home", "away"} and df["line"].isna().all()           # ML line is NaN, not BettingPros' 1
    assert not ((df["book_id"] == 10) & ~df["is_open"]).any()                       # is_off dropped
    assert df[df["is_open"]]["book_kind"].tolist() == ["open"] and df[df["is_open"]]["odds"].tolist() == [-235]
    assert df[df["book_id"] == 0]["book_kind"].iloc[0] == "consensus" and df[df["book_id"] == 12]["book_kind"].iloc[0] == "book"

    sp = _offer(101, [
        {"participant": "BUF", "selection": "", "books": [_book(12, _line(-110, -1.5))]},
        {"participant": "SJ", "selection": "", "books": [_book(12, _line(-110, 1.5))]}], 195)
    d = bg.to_frame(bg.build_game_rows("spread", 195, "2024-01-15", [sp], EV))
    assert d.set_index("side")["line"].to_dict() == {"home": -1.5, "away": 1.5}

    tot = _offer(101, [{"participant": None, "selection": "over", "books": [_book(12, _line(-115, 6.5))]},
                       {"participant": None, "selection": "under", "books": [_book(12, _line(-105, 6.5))]}], 194)
    d = bg.to_frame(bg.build_game_rows("total", 194, "2024-01-15", [tot], EV))
    assert sorted(d["side"]) == ["over", "under"] and (d["line"] == 6.5).all()
    assert bg.build_game_rows("total", 194, "2024-01-15", [_offer(999, tot["selections"])], EV) == []   # unknown event


def test_crawl_dates_season_months_july_2021_and_order():
    d = bg.crawl_dates("NHL", "2020-12-01", "2021-07-10", newest_first=False)
    assert d[0] == "2021-01-01"                                         # clamped to the sport's first useful date
    assert "2021-07-05" in d and "2021-07-09" not in d and "2021-08-01" not in d
    assert "2021-09-15" not in d                                        # preseason / offseason is never asked
    assert bg.crawl_dates("NHL", "2022-10-01", "2022-10-03")[0] == "2022-10-03"      # newest first by default
    assert bg.is_closed_day("2024-01-14", today=pd.Timestamp("2024-01-15").date())
    assert not bg.is_closed_day("2024-01-15", today=pd.Timestamp("2024-01-15").date())


class _Sess:
    pass


def _fake_api(monkeypatch, events_by_day, fail=None, calls=None, block_catalog=False):
    calls = calls if calls is not None else []

    def get(sess, path, params):
        calls.append((path, params.get("date") or params.get("market_id")))
        if fail and (path != "markets" or block_catalog):
            raise fail
        if path == "markets":
            return {"markets": [{"id": 193, "slug": "moneyline", "category": "game-odds", "period": "game"},
                                {"id": 194, "slug": "total", "category": "game-odds", "period": "game"},
                                {"id": 195, "slug": "puck-line", "category": "game-odds", "period": "game"},
                                {"id": 1, "slug": "points", "category": "player-props", "period": "game"}]}
        if path == "events":
            return {"events": events_by_day.get(params["date"], [])}
        offers = [_offer(101, [{"participant": "BUF", "selection": "over", "books": [_book(12, _line(-110, 1.5))]}], params["market_id"])]
        return {"offers": offers, "_pagination": {"total_pages": 1}}
    monkeypatch.setattr(bg, "get", get)
    monkeypatch.setattr(bg.time, "sleep", lambda s: None)
    monkeypatch.setattr(bg.bpp, "make_session", lambda sport: _Sess())
    return calls


def test_crawl_is_resumable_idempotent_and_marks_empty_days(monkeypatch, tmp_path):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    ev = {"2024-01-15": [{"id": 101, "visitor": "SJ", "home": "BUF", "scheduled": "2024-01-15 23:00:00", "season_type": "REG", "status": "closed"}]}
    calls = _fake_api(monkeypatch, ev)
    st = bg.crawl("NHL", "2024-01-14", "2024-01-16", prefix="Odds/t_games")
    assert st["dates"] == 3 and st["empty_days"] == 2 and st["errors"] == 0 and not st["blocked"]
    for m in bg.MARKETS:                                                # one partition per market per day, empty days are 0-row markers
        assert (tmp_path / "Odds/t_games" / m / "2024-01-14.parquet").exists()
        assert len(pd.read_parquet(tmp_path / "Odds/t_games" / m / "2024-01-14.parquet")) == 0
    n1 = len(calls)
    st2 = bg.crawl("NHL", "2024-01-14", "2024-01-16", prefix="Odds/t_games")
    assert st2["dates"] == 0 and all(c[0] == "markets" for c in calls[n1:])     # second run: only the catalog call, no data requests
    q = bg.read_games("NHL", prefix="Odds/t_games")
    assert set(q["market"]) == {"moneyline", "spread", "total"}


def test_crawl_never_writes_today_and_stops_on_block_and_on_error_streak(monkeypatch, tmp_path):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    today = pd.Timestamp.today().date().isoformat()
    calls = _fake_api(monkeypatch, {})
    bg.crawl("NHL", today, today, prefix="Odds/t_today")
    assert not any(c[0] == "events" for c in calls)                     # today is not closed, never asked

    blocked = _fake_api(monkeypatch, {}, fail=bg.Blocked("403 https://x"))
    st = bg.crawl("NHL", "2024-01-14", "2024-01-30", prefix="Odds/t_block")
    assert st["blocked"] and st["dates"] == 0
    cat = _fake_api(monkeypatch, {}, fail=bg.Blocked("403 https://x"), block_catalog=True)
    assert bg.crawl("NHL", "2024-01-14", "2024-01-30", prefix="Odds/t_block2")["blocked"] and len(cat) == 1   # blocked at the catalog: one request, then stop

    monkeypatch.setattr(bg, "MAX_CONSECUTIVE_ERRORS", 3)
    calls = _fake_api(monkeypatch, {}, fail=RuntimeError("500 boom"))
    st = bg.crawl("NHL", "2024-01-14", "2024-01-30", prefix="Odds/t_err")
    assert st["aborted"] and st["errors"] == 3 and not st["blocked"]


def test_block_detector_does_not_fire_on_market_ids():
    assert bg._is_block(RuntimeError("403 https://api/offers params={'market_id': 194}: Forbidden"))
    assert bg._is_block(RuntimeError("200 x: please solve this CAPTCHA"))
    assert not bg._is_block(RuntimeError("500 https://api/offers params={'market_id': 403}: oops"))


def _q(rows):
    d = pd.DataFrame(rows)
    for c, v in (("date", "2024-01-15"), ("market_id", 0), ("event_id", 101), ("scheduled", "2024-01-15 23:00:00"), ("season_type", "REG"),
                 ("event_status", "closed"), ("away", "SJ"), ("home", "BUF"), ("book_kind", "book"), ("is_main", True), ("is_open", False),
                 ("updated", "2024-01-15 21:00:00"), ("line", np.nan)):
        d[c] = d[c].where(d[c].notna(), v) if c in d else v
    d["book"] = d["book_id"].astype(str)
    return d


def test_dedupe_keeps_the_latest_quote_across_adjacent_partitions():
    a = _q([{"market": "total", "side": "over", "line": 6.5, "book_id": 12, "odds": -110, "date": "2024-01-14", "updated": "2024-01-15 20:00:00"}])
    b = _q([{"market": "total", "side": "over", "line": 6.5, "book_id": 12, "odds": -120, "date": "2024-01-15", "updated": "2024-01-15 22:00:00"}])
    out = bg.dedupe(pd.concat([b, a], ignore_index=True))
    assert len(out) == 1 and int(out["odds"].iloc[0]) == -120


def test_close_quotes_drops_inplay_consensus_dfs_and_open_rows():
    rows = []
    for bid, kind, upd in [(12, "book", "2024-01-15 22:55:00"), (10, "book", "2024-01-15 23:09:00"), (13, "book", "2024-01-15 23:11:00"),
                           (0, "consensus", "2024-01-15 22:59:00"), (37, "dfs", "2024-01-15 22:00:00"), (14, "book", "bad")]:
        rows.append({"market": "total", "side": "over", "line": 6.5, "book_id": bid, "odds": -110, "book_kind": kind, "updated": upd})
    rows.append({"market": "total", "side": "over", "line": 6.5, "book_id": 12, "odds": -105, "book_kind": "open", "is_open": True,
                 "updated": "2024-01-14 10:00:00"})
    c = bg.close_quotes(_q(rows))
    assert sorted(c["book_id"]) == [10, 12]                             # 23:11 is 11 min after the 23:00 drop: in-play, gone; bad stamp gone
    assert (c["gap_min"] <= bg.GRACE_MIN).all()


def _close(rows):
    q = _q(rows)
    return bg.close_quotes(q)


def test_consensus_modal_line_min_books_devig_and_puckline_rekey():
    rows = []
    for bid, hl, ho, ao in [(12, -1.5, 110, -130), (10, -1.5, 115, -135), (13, -1.5, 105, -125), (19, -2.5, 240, -300)]:
        rows.append({"market": "spread", "side": "home", "line": hl, "book_id": bid, "odds": ho})
        rows.append({"market": "spread", "side": "away", "line": -hl, "book_id": bid, "odds": ao})      # away stored at +1.5
    for bid, o, u in [(12, -110, -110), (10, -115, -105)]:
        rows.append({"market": "total", "side": "over", "line": 6.5, "book_id": bid, "odds": o})
        rows.append({"market": "total", "side": "under", "line": 6.5, "book_id": bid, "odds": u})
    cons = me.consensus(_close(rows), min_books=3)
    sp = cons[cons["market"] == "spread"].iloc[0]
    assert sp["line"] == -1.5 and sp["n_books"] == 3 and sp["book_ids"] == "10,12,13"            # the -2.5 outlier book is off the modal line
    p = [ms_p for ms_p in (me.ms.devig_pair(a, b)[0] for a, b in [(110, -130), (115, -135), (105, -125)])]
    assert sp["p_mkt"] == pytest.approx(float(np.median(p)))
    assert sp["dec_a_best"] == pytest.approx(me.ms.american_to_decimal_safe(115))
    assert cons[cons["market"] == "total"].empty                       # only 2 books: below the 3-book minimum


def test_opening_pairs_need_same_book_and_time_and_matching_line():
    o = [{"market": "spread", "side": "home", "line": -1.5, "book_id": 12, "odds": 114, "is_open": True, "book_kind": "open", "updated": "2024-01-14 17:16:51"},
         {"market": "spread", "side": "away", "line": 1.5, "book_id": 12, "odds": -135, "is_open": True, "book_kind": "open", "updated": "2024-01-14 17:16:51"},
         {"market": "total", "side": "over", "line": 6.5, "book_id": 12, "odds": -110, "is_open": True, "book_kind": "open", "updated": "2024-01-14 17:16:51"},
         {"market": "total", "side": "under", "line": 6.5, "book_id": 18, "odds": -110, "is_open": True, "book_kind": "open", "updated": "2024-01-14 17:16:51"}]
    p = me.opening_pairs(_q(o))
    assert p["market"].tolist() == ["spread"] and p["line_open"].iloc[0] == -1.5          # the total's sides came from two books: not an open pair
    assert p["dec_a_open"].iloc[0] == pytest.approx(me.ms.american_to_decimal_safe(114))


# ------------------------------------------------------------------------------------------------ team codes / join
def test_event_table_canonicalises_bettingpros_codes_and_matches_nearest_start():
    q = _q([{"market": "total", "side": "over", "line": 6.5, "book_id": 12, "odds": -110, "event_id": 1, "home": "LA", "away": "NJ",
             "scheduled": "2024-01-16 03:00:00"},
            {"market": "total", "side": "over", "line": 6.5, "book_id": 12, "odds": -110, "event_id": 2, "home": "LA", "away": "NJ",
             "scheduled": "2024-01-17 03:00:00"},
            {"market": "total", "side": "over", "line": 6.5, "book_id": 12, "odds": -110, "event_id": 3, "home": "XXX", "away": "NJ",
             "scheduled": "2024-01-17 03:00:00"},
            {"market": "total", "side": "over", "line": 6.5, "book_id": 12, "odds": -110, "event_id": 4, "home": "ARI", "away": "TB",
             "scheduled": "2023-11-17 03:00:00"},
            {"market": "total", "side": "over", "line": 6.5, "book_id": 12, "odds": -110, "event_id": 5, "home": "UTA", "away": "TB",
             "scheduled": "2024-12-17 03:00:00"}])
    ev = me.event_table(q).set_index("event_id")
    assert ev.loc[1, "home"] == "LAK" and ev.loc[1, "away"] == "NJD" and ev.loc[1, "date_et"] == "2024-01-15"   # 03:00Z is the previous ET evening
    assert ev.loc[4, "home"] == "ARI" and ev.loc[5, "home"] == "UTA"       # dated Coyotes / Utah split
    games = pd.DataFrame({"game_id": [11, 12, 13], "season_year": 2023, "game_type": [2, 2, 2],
                          "date": ["2024-01-15", "2024-01-16", "2023-11-16"], "start_utc": ["2024-01-16T03:00:00Z", "2024-01-17T03:00:00Z", "2023-11-17T03:00:00Z"],
                          "home_abbr": ["LAK", "LAK", "ARI"], "away_abbr": ["NJD", "NJD", "TBL"], "home_score": 1, "away_score": 0,
                          "home_goals_ex_so": 1, "away_goals_ex_so": 0, "last_period_type": "REG"})
    m = me.match_to_games(me.event_table(q), games).set_index("event_id")
    assert int(m.loc[1, "game_id"]) == 11 and int(m.loc[2, "game_id"]) == 12   # two meetings of one pair are not crossed
    assert m.loc[3, "unmatched"] == "bad_code" and int(m.loc[4, "game_id"]) == 13
    assert m.loc[5, "unmatched"] in ("bad_code", "no_game")


# ------------------------------------------------------------------------------------------------ replay / leakage
TEAMS = ["BOS", "MTL", "TOR", "NYR", "PHI", "DAL", "SEA", "VAN"]


def _synth(seed=0, days=40):
    rng = np.random.default_rng(seed)
    rows, grows, gid = [], [], 2023020000
    for d in range(days):
        date = (pd.Timestamp("2023-11-01") + pd.Timedelta(days=d)).date().isoformat()
        order = rng.permutation(len(TEAMS))
        for i in range(0, len(TEAMS), 2):
            h, a = TEAMS[order[i]], TEAMS[order[i + 1]]
            gid += 1
            hg, ag = int(rng.poisson(3.1)), int(rng.poisson(2.9))
            if hg == ag:
                hg += 1
            for t, o, ih, g, ga in ((h, a, True, hg, ag), (a, h, False, ag, hg)):
                rows.append({"game_id": gid, "season_year": 2023, "game_type": 2, "date": date, "team_id": 1, "team_abbr": t, "is_home": ih,
                             "opp_id": 2, "opp_abbr": o, "goals": g, "goals_against": ga, "sog": 25 + int(rng.integers(0, 12)),
                             "shots_against": 25 + int(rng.integers(0, 12)), "saves": 20, "hits": 20, "blocked_shots": 12, "giveaways": 5,
                             "takeaways": 5, "power_play_goals": 0, "pim": 6})
            grows.append({"game_id": gid, "date": date})
    return pd.DataFrame(rows), pd.DataFrame(grows)


def _goalies(team_box):
    g = team_box[["game_id", "date", "team_abbr"]].copy()
    g["player_id"] = g["team_abbr"].map({t: 1000 + i for i, t in enumerate(TEAMS)})
    g["name"] = "G " + g["team_abbr"]
    g["toi_sec"], g["shots_against"], g["goals_against"] = 3600, 28, 3
    return g


def test_replay_day_t_predictions_do_not_depend_on_results_on_or_after_t():
    tb, _ = _synth()
    gl = _goalies(tb)
    lines = {str(g): {"spread": -1.5, "total": 6.0} for g in tb["game_id"].unique()}
    base = me.replay(tb.assign(game_id=tb["game_id"].astype(str)), gl, lines)
    cut = "2023-11-20"
    scr = tb.copy()
    rng = np.random.default_rng(1)
    late = scr["date"] >= cut
    scr.loc[late, "goals"] = rng.integers(0, 9, int(late.sum()))
    scr.loc[late, "goals_against"] = rng.integers(0, 9, int(late.sum()))
    scr.loc[late, "sog"] = rng.integers(10, 50, int(late.sum()))
    gl2 = gl.copy()
    gl2.loc[gl2["date"] >= cut, "goals_against"] = 9
    alt = me.replay(scr.assign(game_id=scr["game_id"].astype(str)), gl2, lines)
    b, a = base.set_index("game_id"), alt.set_index("game_id")
    early = b[b["date"] < cut].index
    assert len(early) > 30
    cols = ["lam_h", "lam_a", "p_home_ml", "p_home_cover", "p_over"]
    assert np.allclose(b.loc[early, cols].to_numpy(float), a.loc[early, cols].to_numpy(float), atol=0, rtol=0)   # bit-identical before the cut
    day_t = b[b["date"] == cut].index                                      # the cut day itself is predicted from earlier days only
    assert len(day_t) > 0 and np.allclose(b.loc[day_t, cols].to_numpy(float), a.loc[day_t, cols].to_numpy(float), atol=0, rtol=0)
    late_ids = b[b["date"] > cut].index                                    # sanity: it can see the difference LATER
    assert not np.allclose(b.loc[late_ids, cols].to_numpy(float), a.loc[late_ids, cols].to_numpy(float))


def test_replay_only_prices_games_with_lines_and_skips_unknown_ari():
    tb, _ = _synth(days=10)
    tb.loc[tb["team_abbr"] == "BOS", "team_abbr"] = "ARI"
    tb.loc[tb["opp_abbr"] == "BOS", "opp_abbr"] = "ARI"
    ids = [str(g) for g in tb["game_id"].unique()]
    lines = {g: {"spread": -1.5, "total": 5.5} for g in ids[: len(ids) // 2]}
    r = me.replay(tb.assign(game_id=tb["game_id"].astype(str)), _goalies(tb).replace({"BOS": "ARI"}), lines)
    assert set(r["game_id"]) <= set(lines) and r.attrs["skipped"]["unknown_team"] >= 1
    assert "ARI" not in set(r["home"]) | set(r["away"])
    r2 = me.replay(tb.assign(game_id=tb["game_id"].astype(str)), _goalies(tb).replace({"BOS": "ARI"}), lines, ari_as_uta=True)
    assert r2.attrs["skipped"]["unknown_team"] == 0 and len(r2) > len(r)


# ------------------------------------------------------------------------------------------------ outcomes
def test_build_events_outcome_rules_shootout_total_and_pushes():
    games = pd.DataFrame({"game_id": ["1", "2", "3"], "home_score": [4, 2, 3], "away_score": [3, 1, 2],
                          "home_goals_ex_so": [4, 2, 2], "away_goals_ex_so": [3, 1, 2], "season_year": 2023})
    pred = pd.DataFrame({"game_id": ["1", "2", "3"], "date": "2024-01-15", "p_home_ml": 0.6, "p_home_cover": 0.4, "p_over": 0.5})
    cons = pd.DataFrame([
        {"event_id": e, "market": m, "line": ln, "p_mkt": 0.55, "dec_a_med": 1.9, "dec_b_med": 1.9, "dec_a_best": 2.0, "dec_b_best": 2.0, "n_books": 5}
        for e in (1, 2, 3) for m, ln in (("moneyline", np.nan), ("spread", -1.5), ("total", 4.0))])
    evm = pd.DataFrame({"event_id": [1, 2, 3], "game_id": [1, 2, 3], "season_year": 2023, "unmatched": ""})
    out = me.build_events(pred, games, cons, evm)
    assert out["moneyline"].sort_values("game_id")["y"].tolist() == [1, 1, 1]            # game 3 is a shootout win: it counts in the moneyline
    assert out["spread"].sort_values("game_id")["y"].tolist() == [0, 0, 0]               # 1-goal wins never cover -1.5, a shootout win included
    t = out["total"].set_index("game_id")
    # game 3 is 2-2 after OT (a 3-2 shootout win): 4 goals == the 4.0 line -> a push, dropped. The final score's 5 would have been an over.
    assert sorted(t.index) == ["1", "2"] and t.loc["1", "y"] == 1 and t.loc["2", "y"] == 0


def test_run_end_to_end_on_synthetic_masters_and_quotes(tmp_path):
    tb, _ = _synth(days=45)
    tb["game_id"] = tb["game_id"].astype(int)
    nhl_dir, bp_dir = tmp_path / "nhl", tmp_path / "bp"
    nhl_dir.mkdir()
    h = tb[tb["is_home"]].set_index("game_id")
    a = tb[~tb["is_home"]].set_index("game_id")
    games = pd.DataFrame({"game_id": h.index, "season_year": 2023, "game_type": 2, "date": h["date"].values,
                          "start_utc": (pd.to_datetime(h["date"]) + pd.Timedelta(hours=23)).dt.strftime("%Y-%m-%dT%H:%M:%SZ").values,
                          "home_abbr": h["team_abbr"].values, "away_abbr": a.loc[h.index, "team_abbr"].values,
                          "home_score": h["goals"].values, "away_score": a.loc[h.index, "goals"].values,
                          "home_goals_ex_so": h["goals"].values, "away_goals_ex_so": a.loc[h.index, "goals"].values,
                          "last_period_type": "REG"})
    games.to_csv(nhl_dir / "games_master.csv", index=False)
    tb.to_csv(nhl_dir / "team_boxscores_master.csv", index=False)
    _goalies(tb).to_csv(nhl_dir / "goalie_boxscores_master.csv", index=False)
    by_day: dict = {m: {} for m in bg.MARKETS}
    for g in games.itertuples(index=False):
        sched = (pd.Timestamp(g.start_utc).tz_localize(None)).strftime("%Y-%m-%d %H:%M:%S")
        ev = {1000 + int(g.game_id) % 100000: {"away": g.away_abbr, "home": g.home_abbr, "scheduled": sched, "season_type": "REG", "status": "closed"}}
        eid = next(iter(ev))
        upd = (pd.Timestamp(sched) - pd.Timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        for bid in (12, 10, 13, 19):
            k = int(g.game_id) % 9                                       # vary the prices so the market is not a constant
            ml = [_offer(eid, [{"participant": g.home_abbr, "selection": "", "books": [_book(bid, _line(-110 - 8 * k, 1, updated=upd))],
                                "opening_line": {"line": 1, "cost": -115, "book_id": 12, "created": "2023-10-01 10:00:00"}},
                               {"participant": g.away_abbr, "selection": "", "books": [_book(bid, _line(-100 + 4 * k, 1, updated=upd))],
                                "opening_line": {"line": 1, "cost": -105, "book_id": 12, "created": "2023-10-01 10:00:00"}}])]
            sp = [_offer(eid, [{"participant": g.home_abbr, "selection": "", "books": [_book(bid, _line(130 + 6 * k, -1.5, updated=upd))]},
                               {"participant": g.away_abbr, "selection": "", "books": [_book(bid, _line(-150 - 5 * k, 1.5, updated=upd))]}])]
            tot = [_offer(eid, [{"participant": None, "selection": "over", "books": [_book(bid, _line(-105 - 3 * k, 6.0, updated=upd))]},
                                {"participant": None, "selection": "under", "books": [_book(bid, _line(-115 + 2 * k, 6.0, updated=upd))]}])]
            for m, offers in (("moneyline", ml), ("spread", sp), ("total", tot)):
                by_day[m].setdefault(g.date, []).extend(bg.build_game_rows(m, 0, g.date, offers, ev))
    for m, days in by_day.items():
        (bp_dir / m).mkdir(parents=True, exist_ok=True)
        for d, rows in days.items():
            bg.to_frame(rows).to_parquet(bp_dir / m / f"{d}.parquet", index=False)
    events, ctx = me.run(local_dir=str(bp_dir), nhl_dir=str(nhl_dir), since="2023-11-15")
    assert set(events) == {"moneyline", "spread", "total"}
    n = len(events["moneyline"])
    assert n > 40 and ctx["unmatched"] == {"": ctx["n_bp_events"]}
    assert (events["moneyline"]["date"] >= "2023-11-15").all()                     # `since` limits pricing, not the training history
    assert (events["total"]["line"] == 6.0).all() and events["spread"]["n_books"].min() == 4
    txt = me.report_markdown(events, "x", close=ctx["close"]) + "\n".join(me.validation_lines(ctx) + me.coverage_lines(ctx))
    assert "NHL moneyline" in txt and "leakage invariants" in txt and "duplicate quote rows removed" in txt
    sb = me.same_book_clv(events["moneyline"], ctx["close"], "moneyline")
    assert sb["n"] > 0 and sb["model"]["n"] == sb["n"]


def test_market_eval_cli_refuses_a_tiny_events_table(tmp_path):
    ev = pd.DataFrame({"date": ["2024-01-01"] * 10, "p_model": 0.5, "p_mkt": 0.5, "y": 1})
    p = tmp_path / "events.csv"
    ev.to_csv(p, index=False)
    assert me.main(["--events", str(p)]) == 2

