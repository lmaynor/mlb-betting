"""/edge multi-sport cockpit: game-log form (mlb_core.data.cockpit_form) + the unified candidate payload
(mlb.runners.public_api.get_multisport_cockpit) + its Flask route. Pure in-memory: storage.read_bytes is monkeypatched,
nothing touches GCS, the DB or the network."""
import io
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mlb_core.data import cockpit_form as cf


@pytest.fixture(autouse=True)
def _fresh_cache():
    cf.clear_cache()
    yield
    cf.clear_cache()


def _put(monkeypatch, files):
    """files: key -> bytes | dict (JSON) | DataFrame (parquet for *.parquet keys, CSV otherwise)."""
    def read_bytes(key):
        if key not in files:
            raise FileNotFoundError(key)
        v = files[key]
        if isinstance(v, pd.DataFrame):
            buf = io.BytesIO()
            (v.to_parquet(buf, index=False) if key.endswith(".parquet") else v.to_csv(buf, index=False))
            return buf.getvalue()
        return v if isinstance(v, bytes) else json.dumps(v).encode()
    monkeypatch.setattr("mlb_core.storage.read_bytes", read_bytes)


def _season_year():
    return cf._season_year_now()


def _nfl_frame(name="Test Receiver", weeks=range(1, 13), yds=None, season=None):
    season = season or _season_year()
    yds = yds or [40 + 10 * w for w in weeks]            # 50, 60, 70, ... rising
    return pd.DataFrame({
        "player_id": "00-0001", "player_display_name": name, "team": "IND", "season": season, "week": list(weeks),
        "season_type": "REG", "opponent_team": "WAS", "receiving_yards": yds, "receptions": 5, "targets": 8,
        "carries": 0, "rushing_yards": 0, "passing_yards": 0, "passing_tds": 0, "attempts": 0,
    })


# -- cockpit_form ---------------------------------------------------------------------------------------------------
def test_nfl_form_is_last_ten_games_with_hit_flags_rates_and_usage(monkeypatch):
    _put(monkeypatch, {cf.NFL_KEY.format(season=_season_year()): _nfl_frame()})
    f = cf.recent_form("nfl", "rec_yds", 105.5, "Test Receiver")
    assert f["n_games"] == 10 and len(f["games"]) == 10
    assert [g["val"] for g in f["games"]] == [70 + 10 * i for i in range(10)]            # weeks 3..12 oldest first
    assert [g["date"] for g in f["games"]][0] == "W3" and f["games"][-1]["date"] == "W12"
    assert f["games"][-1]["opp"] == "WAS" and f["games"][-1]["usage"] == 8
    assert [g["hit"] for g in f["games"]] == [False] * 4 + [True] * 6                    # 110..160 clear 105.5
    assert f["l5_rate"] == 1.0 and f["l10_rate"] == 0.6
    assert f["szn_rate"] == 0.5 and f["szn_games"] == 12                                 # weeks 7..12 of 12
    assert f["usage_label"] == "Targets" and f["avg_usage"] == 8.0
    assert f["avg_val"] == 115.0 and f["line"] == 105.5


def test_nfl_name_matching_folds_accents_suffixes_and_punctuation(monkeypatch):
    _put(monkeypatch, {cf.NFL_KEY.format(season=_season_year()): _nfl_frame(name="Ja'Marr Chase")})
    assert cf.recent_form("nfl", "rec_yds", 80.5, "Ja\u2019Marr Chase Jr.") is not None
    assert cf.recent_form("nfl", "rec_yds", 80.5, "jamarr chase") is not None


def test_nfl_early_season_pulls_previous_season_to_fill_ten_games(monkeypatch):
    y = _season_year()
    _put(monkeypatch, {
        cf.NFL_KEY.format(season=y): _nfl_frame(weeks=range(1, 5), yds=[100] * 4, season=y),
        cf.NFL_KEY.format(season=y - 1): _nfl_frame(weeks=range(1, 19), yds=[20] * 18, season=y - 1),
    })
    f = cf.recent_form("nfl", "rec_yds", 50.5, "Test Receiver")
    assert [g["val"] for g in f["games"]] == [20] * 6 + [100] * 4                        # 6 from last season + 4 this season
    assert f["szn_games"] == 4 and f["szn_rate"] == 1.0                                  # season split = the latest season only
    assert f["l10_rate"] == 0.4


def test_missing_master_unsupported_stat_unknown_player_and_no_line_all_degrade(monkeypatch):
    _put(monkeypatch, {})
    assert cf.recent_form("nfl", "rec_yds", 60.5, "Anyone") is None                      # no file at all
    assert cf.recent_form("mlb", "hits", 0.5, "Anyone") is None                          # sport with no game-log master
    _put(monkeypatch, {cf.NFL_KEY.format(season=_season_year()): _nfl_frame()})
    cf.clear_cache()
    assert cf.recent_form("nfl", "anytime_td", 0.5, "Test Receiver") is None             # unsupported stat
    assert cf.recent_form("nfl", "rec_yds", 60.5, "Nobody Here") is None                 # player not in the file
    nl = cf.recent_form("nfl", "rec_yds", None, "Test Receiver")                         # no line: values yes, flags no
    assert nl["games"][0]["hit"] is None and nl["l10_rate"] is None and nl["avg_val"] == 115.0


def test_load_failures_are_cached_briefly_not_retried_per_request(monkeypatch):
    calls = []
    def read_bytes(key):
        calls.append(key)
        raise FileNotFoundError(key)
    monkeypatch.setattr("mlb_core.storage.read_bytes", read_bytes)
    assert cf.recent_form("nfl", "rec_yds", 1, "A") is None
    n = len(calls)
    assert cf.recent_form("nfl", "rec_yds", 1, "B") is None and len(calls) == n          # served from the fail cache


def test_nhl_skater_and_goalie_form_match_by_player_id_with_opponent_and_toi(monkeypatch):
    dates = [f"2026-10-{d:02d}" for d in range(1, 13)]
    sk = pd.DataFrame({"game_id": range(1, 13), "team_id": 9, "player_id": 8478402, "name": "Connor McDavid",
                       "date": dates, "season_year": 2026, "game_type": 2, "played": True,
                       "toi_sec": 1200.0, "sog": [2, 3, 4, 5, 3, 2, 4, 6, 1, 3, 4, 5], "goals": 0, "assists": 1, "points": 1})
    gl = pd.DataFrame({"game_id": range(1, 4), "team_id": 9, "player_id": 8480000, "name": "Some Goalie",
                       "date": dates[:3], "season_year": 2026, "game_type": 2, "played": True,
                       "toi_sec": 3600.0, "saves": [20, 30, 25], "shots_against": [22, 33, 27]})
    tb = pd.DataFrame({"game_id": range(1, 13), "team_id": 9, "opp_abbr": "VAN"})
    _put(monkeypatch, {cf.NHL_SKATER_KEYS[0]: sk, cf.NHL_GOALIE_KEYS[0]: gl, cf.NHL_TEAM_KEY: tb})
    f = cf.recent_form("nhl", "sog", 2.5, "McDavid, C.", player_id=8478402)              # id wins even when the name differs
    assert f["n_games"] == 10 and f["games"][-1]["date"] == "2026-10-12" and f["games"][-1]["opp"] == "VAN"
    assert f["usage_label"] == "TOI (min)" and f["games"][0]["usage"] == 20 and f["avg_usage"] == 20.0
    assert f["l10_rate"] == round(sum(v > 2.5 for v in [4, 5, 3, 2, 4, 6, 1, 3, 4, 5]) / 10, 3)
    g = cf.recent_form("nhl", "saves", 24.5, "Some Goalie")
    assert g["usage_label"] == "Shots faced" and g["avg_val"] == 25.0 and g["n_games"] == 3


def test_nba_form_parses_clock_minutes_computes_pra_and_skips_preseason_and_dnp(monkeypatch):
    rows = []
    for i in range(12):
        rows.append({"date": f"2026-10-{i + 1:02d}", "name": "Test Guard", "player_id": 1, "opp_abbr": "BOS",
                     "season_year": 2026, "season_type": "Regular Season", "played": True,
                     "minutes": f"{30 + i}:30", "points": 20, "rebounds": 5, "assists": 5, "three_points_made": 3})
    rows.append({**rows[-1], "date": "2026-10-20", "season_type": "Preseason", "points": 99})
    rows.append({**rows[-1], "date": "2026-10-21", "season_type": "Regular Season", "played": False, "points": 98})
    _put(monkeypatch, {cf.NBA_KEYS[0]: pd.DataFrame(rows)})
    f = cf.recent_form("nba", "pra", 29.5, "Test Guard")
    assert f["n_games"] == 10 and all(g["val"] == 30 for g in f["games"]) and f["l10_rate"] == 1.0
    assert f["usage_label"] == "Minutes" and f["games"][-1]["usage"] == 41.5 and f["games"][0]["opp"] == "BOS"
    assert cf.recent_form("nba", "threes", 2.5, "Test Guard")["avg_val"] == 3.0


def test_form_from_pairs_shapes_precomputed_mlb_form():
    f = cf.form_from_pairs([("10-01", 0), ("10-02", 1), ("10-03", 2)], 0.5)
    assert [g["hit"] for g in f["games"]] == [False, True, True] and f["l10_rate"] == round(2 / 3, 3)
    assert f["games"][0]["usage"] is None and f["avg_usage"] is None and f["usage_label"] is None
    assert cf.form_from_pairs([], 0.5) is None


# -- get_multisport_cockpit -----------------------------------------------------------------------------------------
def _outlier(**over):
    now = datetime.now(timezone.utc)
    r = {"ts": now.isoformat(), "game_key": "nfl:2026-10-18:IND@WAS",
         "commence": (now + timedelta(hours=2)).isoformat(), "mins_to_kick": 120.0, "book": "fanduel",
         "player": "Test Receiver", "player_norm": "test receiver", "player_id": None, "stat": "rec_yds",
         "point": 105.5, "alt": False, "side": "OVER", "american": 115, "decimal": 2.15, "p_fair": 0.5, "ev": 0.06,
         "n_books": 5, "disp": 0.01,
         "all_books": [{"book": "fanduel", "over": 115, "under": -135, "fair": 0.5},
                       {"book": "draftkings", "over": -108, "under": -112, "fair": 0.5}]}
    r.update(over)
    return r


def _snap(rows=None, outliers=None):
    return {"ts": datetime.now(timezone.utc).isoformat(), "summary": {}, "rows": rows or [], "book_outliers": outliers or []}


def test_cockpit_unifies_sports_attaches_form_and_reports_per_sport_status(monkeypatch):
    from mlb.runners import public_api as api
    nhl_row = _outlier(game_key="nhl:2026-10-18:EDM@VAN", player="Connor McDavid", player_norm="connor mcdavid",
                       player_id=8478402, stat="sog", point=2.5, ev=0.09, team="EDM", book="betmgm")
    nhl_row.pop("all_books")
    sk = pd.DataFrame({"game_id": range(1, 4), "team_id": 9, "player_id": 8478402, "name": "Connor McDavid",
                       "date": ["2026-10-01", "2026-10-02", "2026-10-03"], "season_year": 2026, "game_type": 2,
                       "played": True, "toi_sec": 1200.0, "sog": [1, 4, 5], "goals": 0, "assists": 0, "points": 0})
    kalshi = {"ticker": "K1", "stat": "rec_yds", "player": "Kalshi Guy", "strike": 60.5, "p_over": 0.5, "ask_yes": 0.4,
              "ev_yes": 0.05, "ask_no": 0.62, "ev_no": -0.1, "n_books": 4, "books": "a,b,c,d", "flag": True,
              "commence": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(), "game_key": "nfl:2026-10-18:IND@WAS"}
    _put(monkeypatch, {
        "EVSweep/nfl_props/latest.json": _snap(rows=[kalshi], outliers=[_outlier(), _outlier(book="lowbooks", ev=0.20, n_books=2)]),
        "EVSweep/nhl_props/latest.json": _snap(rows=[nhl_row]),
        cf.NFL_KEY.format(season=_season_year()): _nfl_frame(),
        cf.NHL_SKATER_KEYS[0]: sk,
    })
    out = api.get_multisport_cockpit("all", 60, engine=None)
    assert set(out["sports"]) == {"mlb", "nfl", "nhl", "nba"}
    assert out["sports"]["mlb"]["status"] == "unavailable"                              # no engine: MLB reported, not faked
    assert out["sports"]["nba"]["status"] == "unavailable" and out["sports"]["nba"]["count"] == 0
    assert out["sports"]["nfl"]["count"] == 3 and out["sports"]["nhl"]["count"] == 1
    by_book = {r["book"]: r for r in out["rows"]}
    fd = by_book["fanduel"]
    assert fd["sport"] == "nfl" and fd["shadow"] is True and fd["source"] == "sink_a" and fd["line"] == 105.5
    assert fd["side"] == "OVER" and fd["american"] == 115 and fd["matchup"] == "IND @ WAS"
    assert fd["recentForm"]["n_games"] == 10 and fd["recentForm"]["l10_rate"] == 0.6
    assert [b["book"] for b in fd["all_books"]] == ["fanduel", "draftkings"]
    mg = by_book["betmgm"]
    assert mg["sport"] == "nhl" and mg["player_id"] == 8478402 and mg["recentForm"]["games"][-1]["val"] == 5
    ka = by_book["kalshi"]
    assert ka["source"] == "kalshi" and ka["american"] == 150 and ka["line"] == 60.5 and ka["recentForm"] is None  # unknown player
    assert by_book["lowbooks"]["flag"] is False
    assert out["rows"][-1]["book"] == "lowbooks"                                         # unflagged sink last, despite the biggest EV
    flagged = [r for r in out["rows"] if r["flag"]]
    assert [r["ev"] for r in flagged] == sorted((r["ev"] for r in flagged), reverse=True)


def test_cockpit_degrades_to_null_form_when_game_logs_are_absent_and_filters_by_sport(monkeypatch):
    from mlb.runners import public_api as api
    _put(monkeypatch, {"EVSweep/nfl_props/latest.json": _snap(outliers=[_outlier()])})
    out = api.get_multisport_cockpit("nfl", 10)
    assert list(out["sports"]) == ["nfl"] and out["rows"][0]["recentForm"] is None
    with pytest.raises(ValueError):
        api.get_multisport_cockpit("curling")


def test_cockpit_mlb_rows_combine_model_picks_and_ev_alerts_with_enrichment(monkeypatch):
    from mlb.runners import public_api as api
    picks = [
        {"id": 1, "system": "K", "player": "Ace Pitcher", "bet_type": "K_OVER_6.5", "away_team": "NYY", "home_team": "BOS",
         "odds": -105, "model_prob": 0.6, "market_prob": 0.5, "edge": 0.08, "kelly_triggered": True, "result": None},
        {"id": 2, "system": "NRFI", "player": None, "bet_type": "NRFI", "odds": -120, "edge": 0.09, "result": None},   # game market
        {"id": 3, "system": "EV", "player": "Skip Me", "bet_type": "HR_draftkings", "odds": 300, "edge": 0.2, "result": None},
        {"id": 4, "system": "HR", "player": "Slugger", "bet_type": "HR", "away_team": "NYY", "home_team": "BOS", "odds": 400,
         "model_prob": 0.3, "market_prob": 0.2, "edge": 0.01, "kelly_triggered": False, "result": None},             # below min_ev
    ]
    ev_rows = [{"id": 9, "player": "Slugger", "bet_type": "HR", "away_team": "NYY", "home_team": "BOS", "odds": 380,
                "book": "fanduel", "model_prob": 0.24, "edge": 0.07, "n_books": 6, "result": None},
               {"id": 10, "player": "Done Deal", "bet_type": "HR", "odds": 380, "book": "fanduel", "edge": 0.07, "result": "win"}]
    monkeypatch.setattr(api, "get_today_picks", lambda *a, **k: picks)
    monkeypatch.setattr(api, "_ev_mlb_rows", lambda *a, **k: ev_rows)
    enrich = {"date": "x", "players": {"slugger": {"recent_form": {"stat": "home_runs", "line": 0.5, "games": [
        {"date": "10-01", "value": 1, "over": True}, {"date": "10-02", "value": 0, "over": False}]},
        "spray": [{"x": 100, "y": 100, "hit": True}], "ev_la": [{"ev": 100.0, "la": 20.0, "hit": True}]},
        "ace pitcher": {"recent_form": {"stat": "outs", "line": 17.5, "games": [{"date": "10-01", "value": 18, "over": True}]}}}}
    _put(monkeypatch, {f"Enrich/edge/{api._ct_today()}.json": enrich})
    out = api.get_multisport_cockpit("mlb", 20, engine=object())
    rows = {r["id"].split("|", 1)[1]: r for r in out["rows"]}
    assert set(rows) == {"model|1", "ev_alert|9"}                                        # game market / EV-in-model / low edge / settled dropped
    k = rows["model|1"]
    assert (k["stat"], k["side"], k["line"], k["shadow"], k["source"]) == ("strikeouts", "OVER", 6.5, False, "model")
    assert k["recentForm"] is None                                                       # enrichment stat (outs) != pick stat (strikeouts)
    assert k["model_prob"] == 0.6 and k["p_fair"] == 0.5 and k["ev"] == 0.08
    e = rows["ev_alert|9"]
    assert (e["stat"], e["side"], e["line"], e["book"], e["n_books"]) == ("home_runs", "OVER", 0.5, "fanduel", 6)
    assert e["recentForm"]["l10_rate"] == 0.5 and e["spray"] and e["ev_la"] and e["model_prob"] is None
    assert e["p_fair"] == 0.24 and e["decimal"] == 4.8
    assert out["sports"]["mlb"]["status"] == "live" and out["sports"]["mlb"]["count"] == 2


def test_parse_mlb_prop_handles_thresholds_hr_sides_and_non_props():
    from mlb.runners.public_api import _parse_mlb_prop as p
    assert p("K_OVER_7.5") == ("K", "OVER", 7.5)
    assert p("BATTER_TB_UNDER_1.5") == ("BATTER_TB", "UNDER", 1.5)
    assert p("BATTER_HITS_2PLUS_2.0") == ("BATTER_HITS", "OVER", 1.5)
    assert p("HR") == ("HR", "OVER", 0.5) and p("HR_NO") == ("HR", "UNDER", 0.5)
    assert p("NRFI") is None and p("F5") is None and p("GAME_HOME") is None and p(None) is None


# -- route ----------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    with patch("main._get_engine", return_value=MagicMock()):
        import main as m
        m.app.config["TESTING"] = True
        m.SITE_API_KEY = "k"
        with m.app.test_client() as c:
            yield c


def test_route_requires_key_serves_payload_and_400s_unknown_sport(client):
    assert client.get("/api/public/edge/cockpit").status_code == 401
    fake = {"as_of": "x", "sport": "all", "rows": [], "sports": {}}
    with patch("main.get_multisport_cockpit", return_value=fake) as g:
        r = client.get("/api/public/edge/cockpit?sport=nfl&limit=5&min_ev=0.03", headers={"X-API-Key": "k"})
    assert r.status_code == 200 and r.get_json() == fake and r.headers["Cache-Control"] == "public, max-age=60"
    assert g.call_args.kwargs["sport"] == "nfl" and g.call_args.kwargs["limit"] == 5 and g.call_args.kwargs["min_ev"] == 0.03
    bad = client.get("/api/public/edge/cockpit?sport=curling", headers={"X-API-Key": "k"})
    assert bad.status_code == 400
