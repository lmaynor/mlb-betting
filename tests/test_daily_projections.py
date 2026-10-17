"""ev.daily_projections -- projection engine, market join, edge/confluence rules, rotation fallback, run_sport."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from ev import daily_projections as dp
from mlb_core.data import cockpit_form as cf

NOW = datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=5)


# ----------------------------------------------------------------------------------------------- fixtures
def nba_rows(name, team, pts, reb=None, mins=None, opp="BOS", season=2025):
    n = len(pts)
    reb = reb or [5.0] * n
    mins = mins or [32.0] * n
    return [{"name": name, "team_abbr": team, "_date": f"2026-02-{i + 1:02d}", "_sort": f"2026-02-{i + 1:02d}",
             "_opp": opp, "season_year": season, "points": float(p), "rebounds": float(r), "assists": 4.0,
             "three_points_made": 2.0, "minutes": float(m)} for i, (p, r, m) in enumerate(zip(pts, reb, mins))]


def nba_logs(extra=None):
    rng = np.random.default_rng(7)
    idx = {}
    idx[cf.norm_name("Jo Doe")] = nba_rows("Jo Doe", "LAL", [21.5, 29.5] * 10)       # mean 25.5, deterministic
    idx[cf.norm_name("Sam Bench")] = nba_rows("Sam Bench", "LAL", list(6 + rng.normal(0, 2, 20)), mins=[12.0] * 20)
    idx[cf.norm_name("Max Celtic")] = nba_rows("Max Celtic", "BOS", list(18 + rng.normal(0, 3, 20)), opp="LAL")
    for k, v in (extra or {}).items():
        idx[k] = v
    return dp.GameLogs("nba", {"all": idx})


def book(key, over, under, point=24.5, player="Jo Doe", mkey="player_points"):
    return {"key": key, "markets": [{"key": mkey, "outcomes": [
        {"name": "Over", "description": player, "price": over, "point": point},
        {"name": "Under", "description": player, "price": under, "point": point}]}]}


def event(books, start=START, away="Boston Celtics", home="Los Angeles Lakers"):
    return {"id": "e1", "commence_time": start.isoformat().replace("+00:00", "Z"), "away_team": away,
            "home_team": home, "bookmakers": books}


FAIR = [book(b, -110, -110) for b in ("draftkings", "fanduel", "betmgm", "caesars")]


# ----------------------------------------------------------------------------------------------- score parity
@pytest.mark.parametrize("edge,books,p,expected", [
    (0.0, 8, 0.5, 0), (-0.05, 8, 0.5, 0), (0.15, 12, 0.5, 100), (0.05, None, 0.35, 41), (0.10, 8, 0.6, 71),
    (0.01, 12, 0.6, 24),
])
def test_beezy_score_matches_ts_v2(edge, books, p, expected):
    assert dp.beezy_score(edge, books, p) == expected


# ----------------------------------------------------------------------------------------------- projection math
def test_project_mu_constant_series():
    hist = [(20.0, 30.0, 2025, "BOS")] * 12
    out = dp.project_mu(hist, 8.0)
    assert out["mu"] == pytest.approx(20.0, rel=1e-6)
    assert out["usage_proj"] == pytest.approx(30.0)


def test_project_mu_recent_form_and_opp_factor():
    rising = [(10.0, None, 2025, "X")] * 10 + [(30.0, None, 2025, "X")] * 4
    flat = dp.project_mu(rising, 100.0)["mu"]
    quick = dp.project_mu(rising, 2.0)["mu"]
    assert quick > flat                                           # a short half-life leans on the recent games
    assert dp.project_mu(rising, 2.0, 1.1)["mu"] == pytest.approx(quick * 1.1)


def test_project_mu_needs_history():
    assert dp.project_mu([(5.0, None, 2025, "X")] * (dp.MIN_GAMES - 1), 3.0) is None


def test_p_over_families():
    ctx = dp.StatContext(alpha=0.2, cv=0.4)
    assert dp.p_over("normal", 50.0, 50.0, ctx) == pytest.approx(0.5, abs=1e-9)
    assert dp.p_over("normal", 60.0, 50.0, ctx) > 0.5 > dp.p_over("normal", 40.0, 50.0, ctx)
    lo, hi = dp.p_over("negbin", 4.0, 3.5, ctx), dp.p_over("negbin", 4.0, 5.5, ctx)
    assert 0 < hi < lo < 1
    assert dp.p_over("anytime", 0.69314718, 0.5, ctx) == pytest.approx(0.5, abs=1e-6)
    assert dp.p_over("negbin", 0.0, 0.5, ctx) == 0.0
    assert dp.p_over("normal", None, 1.0, ctx) is None


# ----------------------------------------------------------------------------------------------- market selection
def test_pick_main_line_prefers_most_books_then_coin_flip():
    from ev.props import parse_event_props
    ev = event([book("draftkings", -110, -110, 24.5), book("fanduel", -110, -110, 24.5),
                book("betmgm", -110, -110, 24.5), book("caesars", -150, 120, 25.5)])
    q, _ = parse_event_props(ev, "nba")
    pt, cons, at = dp.pick_main_line(q)
    assert pt == 24.5 and cons.n_books == 3 and len(at) == 3


def test_pick_main_line_one_sided_has_no_consensus():
    from ev.props import parse_event_props
    one = {"key": "draftkings", "markets": [{"key": "player_goals", "outcomes": [
        {"name": "Yes", "description": "Jo Doe", "price": 180, "point": 0.5}]}]}
    q, _ = parse_event_props({"bookmakers": [one]}, "nhl")
    pt, cons, at = dp.pick_main_line(q)
    assert pt == 0.5 and cons is None and len(at) == 1


def test_best_prices_pick_highest_decimal():
    from ev.props import parse_event_props
    ev = event([book("draftkings", 105, -125), book("fanduel", 120, -150), book("betmgm", -105, 100)])
    q, _ = parse_event_props(ev, "nba")
    best = dp.best_prices(q)
    assert best["over"][0] == "fanduel" and best["over"][2] == 120
    assert best["under"][0] == "betmgm" and best["under"][2] == 100


# ----------------------------------------------------------------------------------------------- edge + confluence
BEST = {"over": ("x", 1.95, -105), "under": ("y", 1.9, -110)}


def test_confluence_when_both_engines_agree():
    ef = dp.edge_fields(0.62, 0.55, BEST, 5)
    assert ef["side"] == "over" and ef["model_edge"] == pytest.approx(0.07)
    assert ef["market_ev"] == pytest.approx(0.55 * 1.95 - 1)
    assert ef["confluence"] is True


def test_no_confluence_when_price_is_not_plus_ev():
    ef = dp.edge_fields(0.62, 0.55, {"over": ("x", 1.7, -143), "under": None}, 5)
    assert ef["model_edge"] > 0 and ef["market_ev"] < 0 and ef["confluence"] is False


def test_under_side_confluence():
    ef = dp.edge_fields(0.40, 0.48, {"over": None, "under": ("y", 2.0, 100)}, 6)
    assert ef["side"] == "under" and ef["model_edge"] == pytest.approx(0.08)
    assert ef["market_ev"] == pytest.approx(0.52 * 2.0 - 1) and ef["confluence"] is True


def test_confluence_needs_books_and_caps_absurd_edges():
    assert dp.edge_fields(0.62, 0.55, BEST, 2)["confluence"] is False                  # thin consensus
    capped = dp.edge_fields(0.80, 0.55, BEST, 6)                                       # 25pt model edge = model error
    assert capped["edge_capped"] and capped["confluence"] is False


def test_no_market_gives_side_but_no_edges():
    ef = dp.edge_fields(0.3, None, {"over": None, "under": None}, None)
    assert ef["side"] == "under" and ef["model_edge"] is None and ef["market_ev"] is None and not ef["confluence"]


def test_score_inputs_basis():
    conf = dp.edge_fields(0.62, 0.55, BEST, 8)
    score, basis = dp.score_inputs(conf, 8, 0.62, 0.55)
    assert basis == "confluence" and score == dp.beezy_score(min(conf["model_edge"], conf["market_ev"]), 8, 0.62)
    mk = dp.edge_fields(0.55, 0.56, BEST, 8)              # model slightly under market: market-only edge via price
    assert mk["model_edge"] < 0 or mk["side"] == "under"
    capped = dp.edge_fields(0.80, 0.55, BEST, 8)
    assert dp.score_inputs(capped, 8, 0.8, 0.55) == (0, "capped")
    assert dp.score_inputs(dp.edge_fields(0.5, 0.5, {"over": ("x", 1.9, -110), "under": ("y", 1.9, -110)}, 8), 8, .5, .5)[1] == "none"


# ----------------------------------------------------------------------------------------------- end to end rows
def test_build_line_rows_nba_end_to_end():
    books = FAIR[:3] + [book("fanduel", 120, -150)]                  # one book hangs a +120 over
    rows, st = dp.build_line_rows("nba", [event(books)], nba_logs(), NOW)
    r = next(x for x in rows if x["player"] == "Jo Doe" and x["stat"] == "points")
    assert r["consensus_line"] == 24.5 and r["has_line"]
    assert r["team"] == "LAL" and r["opp"] == "BOS"
    assert r["best_over_odds"] == 120 and r["best_over_book"] == "fanduel"
    assert r["best_under_odds"] == -110
    assert r["proj_mean"] == pytest.approx(25.5, abs=2)
    assert r["side"] == "over" and r["model_edge"] > 0 and r["market_ev"] > 0 and r["confluence"] is True
    assert 0 < r["beezy_score"] <= 100 and r["score_basis"] == "confluence"
    assert r["l10_hit_rate"] is not None and r["szn_avg"] is not None
    assert r["validated"] is False and r["engine"] == dp.ENGINE
    assert st["events"] == 1 and st["rows"] == len(rows)


def test_started_event_and_unknown_player_and_wrong_team_are_skipped():
    started = event(FAIR, start=NOW - timedelta(minutes=5))
    assert dp.build_line_rows("nba", [started], nba_logs(), NOW)[0] == []
    ghost = event([book(b, -110, -110, player="No Log") for b in ("draftkings", "fanduel", "betmgm")])
    rows, st = dp.build_line_rows("nba", [ghost], nba_logs(), NOW)
    assert rows == [] and st["no_log"] == 1
    # Jo Doe's log says LAL; an event between two other teams must not project him against a guess
    other = event(FAIR, away="Miami Heat", home="Denver Nuggets")
    rows, st = dp.build_line_rows("nba", [other], nba_logs(), NOW)
    assert rows == [] and st["ambiguous_team"] == 1


def test_thin_history_player_is_not_projected():
    logs = nba_logs({cf.norm_name("Rookie Rae"): nba_rows("Rookie Rae", "LAL", [10, 12])})
    ev = event([book(b, -110, -110, player="Rookie Rae") for b in ("draftkings", "fanduel", "betmgm")])
    rows, st = dp.build_line_rows("nba", [ev], logs, NOW)
    assert rows == [] and st["thin_history"] == 1


def test_context_opponent_factor_direction_and_clip():
    rows = {}
    for i in range(30):                                   # 30 players score 40% more against BOS than against NYK
        vs = ("BOS" if j % 2 == 0 else "NYK" for j in range(16))
        pts = [14.0 if o == "BOS" else 10.0 for o in vs]
        r = nba_rows(f"P{i}", "LAL", pts)
        for k, o in zip(r, ["BOS" if j % 2 == 0 else "NYK" for j in range(16)]):
            k["_opp"] = o
        rows[cf.norm_name(f"P{i}")] = r
    ctx = dp.GameLogs("nba", {"all": rows}).context("points")
    assert ctx.opp_factor["BOS"] > 1.0 > ctx.opp_factor["NYK"]
    assert all(dp.OPP_CLIP[0] <= f <= dp.OPP_CLIP[1] for f in ctx.opp_factor.values())
    assert dp.ALPHA_RANGE[0] <= ctx.alpha <= dp.ALPHA_RANGE[1]


def test_opponent_factor_moves_the_projection():
    base = nba_logs()
    rows, _ = dp.build_line_rows("nba", [event(FAIR)], base, NOW)
    r0 = next(x for x in rows if x["player"] == "Jo Doe")
    boosted = nba_logs()
    boosted.context("points").opp_factor["BOS"] = 1.15
    rows, _ = dp.build_line_rows("nba", [event(FAIR)], boosted, NOW)
    r1 = next(x for x in rows if x["player"] == "Jo Doe")
    assert r1["proj_mean"] > r0["proj_mean"] and r1["opp_factor"] == pytest.approx(1.15)


# ----------------------------------------------------------------------------------------------- rotation fallback
def test_rotation_rows_use_usage_threshold_and_skip_covered():
    logs = nba_logs()
    games = [{"away": "BOS", "home": "LAL", "start": START}]
    rows, st = dp.rotation_rows("nba", logs, games, NOW, set())
    names = {r["player"] for r in rows}
    assert "Jo Doe" in names and "Max Celtic" in names and "Sam Bench" not in names     # bench: 12 min < 20
    assert all(not r["has_line"] and r["line"] is None and r["p_over_model"] is None for r in rows)
    assert all(r["proj_mean"] is not None for r in rows)
    covered = {(cf.norm_name("Jo Doe"), "points")}
    rows2, _ = dp.rotation_rows("nba", logs, games, NOW, covered)
    assert not any(r["player"] == "Jo Doe" and r["stat"] == "points" for r in rows2)


def test_schedule_games_parses_espn_and_survives_errors():
    payload = {"events": [
        {"date": (NOW + timedelta(hours=4)).isoformat().replace("+00:00", "Z"),
         "competitions": [{"status": {"type": {"completed": False}}, "competitors": [
             {"homeAway": "home", "team": {"abbreviation": "LAL"}}, {"homeAway": "away", "team": {"abbreviation": "BOS"}}]}]},
        {"date": (NOW + timedelta(hours=4)).isoformat().replace("+00:00", "Z"),
         "competitions": [{"status": {"type": {"completed": True}}, "competitors": []}]},
        {"date": (NOW + timedelta(hours=90)).isoformat().replace("+00:00", "Z"),
         "competitions": [{"status": {"type": {"completed": False}}, "competitors": [
             {"homeAway": "home", "team": {"abbreviation": "DEN"}}, {"homeAway": "away", "team": {"abbreviation": "MIA"}}]}]},
    ]}
    games = dp.schedule_games("nba", NOW, fetch=lambda url: payload)
    assert {(g["away"], g["home"]) for g in games} == {("BOS", "LAL")}

    def boom(url):
        raise RuntimeError("down")
    assert dp.schedule_games("nba", NOW, fetch=boom) == []


# ----------------------------------------------------------------------------------------------- run_sport
def test_run_sport_status_transitions():
    logs = nba_logs()
    games = [{"away": "BOS", "home": "LAL", "start": START}]
    live = dp.run_sport("nba", NOW, logs=logs, events=[event(FAIR)], meta={"age_min": 12.0}, games=games, write=False)
    assert live["status"] == "live" and live["lines_age_minutes"] == 12.0 and live["validated"] is False
    assert live["rows"][0]["has_line"] and live["summary"]["line_rows"] >= 1
    assert live["stale_gamelog"] is True                               # synthetic log ends in Feb: > 150 days old
    waiting = dp.run_sport("nba", NOW, logs=logs, events=[], meta={}, games=games, write=False)
    assert waiting["status"] == "awaiting_lines" and waiting["rows"] and not waiting["rows"][0]["has_line"]
    idle = dp.run_sport("nba", NOW, logs=logs, events=[], meta={}, games=[], write=False)
    assert idle["status"] == "idle" and idle["rows"] == []


def test_run_sport_unavailable_without_game_log(monkeypatch):
    monkeypatch.setattr(dp.GameLogs, "load", classmethod(lambda cls, sport: None))
    out = dp.run_sport("nfl", NOW, events=[], write=False)
    assert out["status"] == "unavailable" and out["rows"] == []


def test_run_sport_writes_json(monkeypatch):
    written = {}
    import mlb_core.storage as st
    monkeypatch.setattr(st, "write_bytes", lambda data, key: written.setdefault(key, data))
    dp.run_sport("nba", NOW, logs=nba_logs(), events=[event(FAIR)], meta={}, games=[], write=True)
    payload = json.loads(written["EVSweep/nba_props/projections_latest.json"])
    assert payload["engine"] == dp.ENGINE and payload["rows"]


def test_run_sport_rejects_unknown_sport():
    with pytest.raises(ValueError):
        dp.run_sport("mlb", NOW, write=False)


# ----------------------------------------------------------------------------------------------- NFL raw loader
def test_load_events_nfl_newest_payload_per_event(monkeypatch):
    import mlb_core.storage as st
    store = {
        "EVSweep/nfl_props/raw/2026-10-15/abc_101010.json": json.dumps({"id": "abc", "v": 1}),
        "EVSweep/nfl_props/raw/2026-10-15/abc_121212.json": json.dumps({"id": "abc", "v": 2}),
        "EVSweep/nfl_props/raw/2026-10-15/def_090909.json": json.dumps({"id": "def", "v": 1}),
        "EVSweep/nfl_props/raw/2026-10-15/notes.txt": "x",
    }
    monkeypatch.setattr(st, "list_keys", lambda prefix: [k for k in store if k.startswith(prefix)])
    monkeypatch.setattr(st, "read_bytes", lambda k: store[k].encode())
    monkeypatch.setattr(st, "stat", lambda k: {"mtime_utc": NOW - timedelta(minutes=10), "size": 1})
    events, meta = dp.load_events("nfl", NOW)
    assert {(e["id"], e["v"]) for e in events} == {("abc", 2), ("def", 1)}
    assert meta["age_min"] == pytest.approx(10.0) and meta["banked_events"] == 2


def test_load_events_nfl_stale_and_empty(monkeypatch):
    import mlb_core.storage as st
    monkeypatch.setattr(st, "list_keys", lambda prefix: [])
    assert dp.load_events("nfl", NOW)[0] is None
    monkeypatch.setattr(st, "list_keys", lambda prefix: [prefix + "abc_101010.json"] if "2026-10-15" in prefix else [])
    monkeypatch.setattr(st, "read_bytes", lambda k: b"{}")
    monkeypatch.setattr(st, "stat", lambda k: {"mtime_utc": NOW - timedelta(hours=9), "size": 1})
    events, meta = dp.load_events("nfl", NOW)
    assert events is None and "old" in meta["reason"]


# ----------------------------------------------------------------------------------------------- NFL anytime TD + teams
def test_cockpit_form_has_anytime_td():
    assert "anytime_td" in cf.supported_stats("nfl")
    row = {"rushing_tds": 1.0, "receiving_tds": 1.0}
    assert cf._sum_cols(row, cf.NFL_STATS["anytime_td"][0]) == 2.0


@pytest.mark.parametrize("raw,code", [("JAX", "JAC"), ("LA", "LAR"), ("WSH", "WAS"), ("KC", "KC"),
                                      ("Indianapolis Colts", "IND"), ("", None), (None, None)])
def test_canon_team_nfl(raw, code):
    assert dp.canon_team("nfl", raw) == code


def test_canon_team_nba_nhl():
    assert dp.canon_team("nba", "GS") == "GSW" and dp.canon_team("nba", "Los Angeles Lakers") == "LAL"
    assert dp.canon_team("nhl", "Montreal Canadiens") == "MTL"


# ----------------------------------------------------------------------------------------------- full market depth
# 2026-10-16: NFL 10 / NHL 6 / NBA 10 projectable stats. NHL pp_points is a documented data gap (the skater master has
# power_play_goals but no power-play assists), so it is asserted ABSENT rather than faked.
DEPTH = {"nfl": 10, "nhl": 6, "nba": 10}
SPORT_TEAMS = {  # (log team code, event away name, event home name)
    "nfl": ("KC", "Las Vegas Raiders", "Kansas City Chiefs"),
    "nhl": ("TOR", "Boston Bruins", "Toronto Maple Leafs"),
    "nba": ("LAL", "Boston Celtics", "Los Angeles Lakers"),
}
OPP_CODE = {"nfl": "LV", "nhl": "BOS", "nba": "BOS"}


def _stat_defs(sport):
    if sport == "nfl":
        return cf.NFL_STATS
    if sport == "nba":
        return cf.NBA_STATS
    return {**cf.NHL_SKATER_STATS, **cf.NHL_GOALIE_STATS}


def _depth_logs(sport, mean_of):
    """One synthetic player per projectable stat, alternating 0.75x / 1.25x of the stat's mean over 20 games."""
    team, _, _ = SPORT_TEAMS[sport]
    skater, goalie, allt = {}, {}, {}
    for stat in dp.PROJECT_STATS[sport]:
        val_cols, use_cols, _ = _stat_defs(sport)[stat]
        m = mean_of(stat)
        rows = []
        for i in range(20):
            v = m * (0.75 if i % 2 == 0 else 1.25)
            r = {"season": 2025, "season_year": 2025, "_opp": OPP_CODE[sport], "_date": f"2026-02-{i + 1:02d}",
                 "_sort": f"2026-02-{i + 1:02d}" if sport != "nfl" else 202500 + i + 1,
                 "name": f"Player {stat}", "player_display_name": f"Player {stat}"}
            r["recent_team" if sport == "nfl" else "team_abbr"] = team
            for c in val_cols:
                r[c] = v / len(val_cols)
            for c in use_cols:
                r.setdefault(c, 30.0)
            rows.append(r)
        key = cf.norm_name(f"Player {stat}")
        (goalie if stat in cf.NHL_GOALIE_STATS else skater if sport == "nhl" else allt)[key] = rows
    return dp.GameLogs(sport, {"skater": skater, "goalie": goalie} if sport == "nhl" else {"all": allt})


def _depth_event(sport, line_of):
    from ev.props import STAT_KEYS_BY_SPORT
    _, away, home = SPORT_TEAMS[sport]
    tbl = STAT_KEYS_BY_SPORT[sport]
    books = []
    for b in ("draftkings", "fanduel", "betmgm", "caesars"):
        markets = []
        for stat in dp.PROJECT_STATS[sport]:
            mkey = sorted(tbl[stat])[0]
            markets.append({"key": mkey, "outcomes": [
                {"name": "Over", "description": f"Player {stat}", "price": -110, "point": line_of(stat)},
                {"name": "Under", "description": f"Player {stat}", "price": -110, "point": line_of(stat)}]})
        books.append({"key": b, "markets": markets})
    return event(books, away=away, home=home)


@pytest.mark.parametrize("sport", ["nfl", "nhl", "nba"])
def test_every_projectable_stat_produces_a_valid_row(sport):
    mean_of = lambda st: 0.6 if st == "anytime_td" else 20.0          # noqa: E731
    line_of = lambda st: 0.5 if st == "anytime_td" else 19.5          # noqa: E731
    assert len(dp.PROJECT_STATS[sport]) == DEPTH[sport]
    rows, st = dp.build_line_rows(sport, [_depth_event(sport, line_of)], _depth_logs(sport, mean_of), NOW)
    by_stat = {r["stat"]: r for r in rows}
    assert set(by_stat) == set(dp.PROJECT_STATS[sport]), st
    for stat, r in by_stat.items():
        assert r["has_line"] and r["sport"] == sport
        assert r["stat_label"] == dp.STAT_LABEL[stat]
        assert r["proj_mean"] is not None and r["proj_mean"] > 0
        assert 0.0 <= r["p_over_model"] <= 1.0 and 0.0 <= r["p_over_market"] <= 1.0
        assert r["l5_hit_rate"] is not None and r["l10_hit_rate"] is not None
        assert isinstance(r["beezy_score"], int) and 0 <= r["beezy_score"] <= 100
        assert r["validated"] is False and r["engine"] == dp.ENGINE
        lo, hi = sorted((r["p_over_model"], r["p_over_market"]))
        assert lo - 1e-3 <= r["p_over_blend"] <= hi + 1e-3                # the blend sits between its two inputs


def test_nhl_pp_points_is_a_documented_gap_not_a_projection():
    assert "pp_points" not in dp.PROJECT_STATS["nhl"]
    assert "pp_points" not in cf.supported_stats("nhl")


def test_cockpit_form_supports_the_new_volume_stats():
    assert {"pass_completions", "pass_attempts", "rush_attempts", "interceptions"} <= cf.supported_stats("nfl")
    assert {"pr", "pa", "ra", "steals", "blocks"} <= cf.supported_stats("nba")
    assert {"blocked_shots", "saves"} <= cf.supported_stats("nhl")
    row = {"completions": 22.0}
    assert cf._sum_cols(row, cf.NFL_STATS["pass_completions"][0]) == 22.0


def test_rotation_covers_new_stats():
    logs = _depth_logs("nba", lambda st: 25.0)
    games = [{"away": "BOS", "home": "LAL", "start": START}]
    rows, _ = dp.rotation_rows("nba", logs, games, NOW, set())
    assert {"pr", "pa", "ra", "steals", "blocks"} <= {r["stat"] for r in rows}


def test_blend_p_over_properties():
    assert dp.blend_p_over(None, 0.5) is None and dp.blend_p_over(0.5, None) is None
    assert dp.blend_p_over(0.7, 0.5, c=0.0) == pytest.approx(0.5)
    assert dp.blend_p_over(0.7, 0.5, c=1.0) == pytest.approx(0.7)
    mid = dp.blend_p_over(0.7, 0.5)
    assert 0.5 < mid < 0.7 and mid < 0.5 + 0.5 * (0.7 - 0.5)           # default c is a small prior toward the market
    assert 0.0 < dp.blend_p_over(1.0, 0.0) < 1.0                       # clipped, never 0/1


# ----------------------------------------------------------------------------------------------- public API + route
from unittest.mock import MagicMock, patch   # noqa: E402

import mlb.runners.public_api as pa   # noqa: E402


@pytest.fixture
def clean_proj(monkeypatch):
    pa._PROJ_LAST_TRY.clear()
    yield
    pa._PROJ_LAST_TRY.clear()


def _payload(ts, rows=None, status="live"):
    rows = rows if rows is not None else [{"stat": "points", "player": "A"}, {"stat": "rebounds", "player": "B"}]
    return {"sport": "nba", "engine": dp.ENGINE, "validated": True, "ts": ts.isoformat(), "rows": rows, "status": status,
            "stats": ["points"], "summary": {}, "gamelog_latest": "2026-06-13", "stale_gamelog": True,
            "lines_age_minutes": 5.0}


def test_get_sport_projections_serves_fresh_file_and_filters(monkeypatch, clean_proj):
    fresh = _payload(datetime.now(timezone.utc) - timedelta(minutes=10))
    monkeypatch.setattr(pa, "_read_projections", lambda sp: fresh)
    boom = MagicMock(side_effect=AssertionError("must not recompute a fresh board"))
    monkeypatch.setattr(dp, "run_sport", boom)
    out = pa.get_sport_projections("NBA")
    assert out["count"] == 2 and out["stale"] is False and out["age_minutes"] == pytest.approx(10, abs=0.2)
    assert out["validated"] is False                                  # never trusts the stored flag
    assert [r["player"] for r in pa.get_sport_projections("nba", market="reb")["rows"]] == ["B"]
    assert pa.get_sport_projections("nba", limit=1)["count"] == 2 and len(pa.get_sport_projections("nba", limit=1)["rows"]) == 1


def test_get_sport_projections_recomputes_when_stale_then_throttles(monkeypatch, clean_proj):
    old = _payload(datetime.now(timezone.utc) - timedelta(minutes=500), rows=[{"stat": "points", "player": "OLD"}])
    monkeypatch.setattr(pa, "_read_projections", lambda sp: old)
    calls = []
    new = _payload(datetime.now(timezone.utc), rows=[{"stat": "points", "player": "NEW"}])
    monkeypatch.setattr(dp, "run_sport", lambda sp, now=None, write=True: calls.append(sp) or new)
    assert pa.get_sport_projections("nba")["rows"][0]["player"] == "NEW"
    out = pa.get_sport_projections("nba")                                # within the throttle window: no second compute
    assert calls == ["nba"] and out["rows"][0]["player"] == "OLD" and out["stale"] is True


def test_get_sport_projections_degrades_when_nothing_exists(monkeypatch, clean_proj):
    monkeypatch.setattr(pa, "_read_projections", lambda sp: None)
    monkeypatch.setattr(dp, "run_sport", MagicMock(side_effect=RuntimeError("no gcs")))
    out = pa.get_sport_projections("nfl")
    assert out["status"] == "unavailable" and out["rows"] == [] and out["stale"] is True and out["validated"] is False


def test_get_sport_projections_rejects_unknown_sport():
    with pytest.raises(ValueError):
        pa.get_sport_projections("mlb")
    with pytest.raises(ValueError):
        pa.get_sport_projections("")


@pytest.fixture(scope="module")
def client():
    with patch("main._get_engine", return_value=MagicMock()):
        import main as m
        m.app.config["TESTING"] = True
        m.SITE_API_KEY = "k"
        with m.app.test_client() as c:
            yield c


def test_projections_route_auth_payload_and_400(client):
    assert client.get("/api/public/projections?sport=nba").status_code == 401
    fake = {"sport": "nba", "rows": [], "count": 0}
    with patch("main.get_sport_projections", return_value=fake) as g:
        r = client.get("/api/public/projections?sport=nba&market=pts&limit=9", headers={"X-API-Key": "k"})
    assert r.status_code == 200 and r.get_json() == fake and r.headers["Cache-Control"] == "public, max-age=60"
    assert g.call_args.args == ("nba",) and g.call_args.kwargs == {"market": "pts", "limit": 9}
    assert client.get("/api/public/projections?sport=mlb", headers={"X-API-Key": "k"}).status_code == 400
    assert client.get("/api/public/projections", headers={"X-API-Key": "k"}).status_code == 400
