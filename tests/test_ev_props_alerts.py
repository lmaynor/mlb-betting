"""NHL/NBA props alerts sweep (ev.sweep_props_alerts) + sport-aware ev.props parsing."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ev import props, sweep_props_alerts as sw
from mlb_core.data.player_names import PlayerResolver

FIX = Path(__file__).parent / "fixtures" / "odds" / "nhl_props_sample.json"
NOW = datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc)
HOCKEY_ROSTER = [{"player_id": 8471214, "full_name": "Alex Ovechkin", "team": "WSH"},
                 {"player_id": 8481540, "full_name": "Cole Caufield", "team": "MTL"}]


def _sog_event(extra_prices=None, start_in_min=120, home="Washington Capitals", away="Montreal Canadiens"):
    """5 books quote Ovechkin SOG 2.5 (names spelled four different ways); one is a juicy outlier."""
    def mk(name, over, under):
        return [{"name": f"Over {name}", "description": name, "price": over, "point": 2.5},
                {"name": f"Under {name}", "description": name, "price": under, "point": 2.5}]
    books = [("pinnacle", "Alex Ovechkin", -120, 100), ("bovada", "Alex Ovechkin (WSH)", -118, -102),
             ("betmgm", "Alexander Ovechkin", -122, 102), ("caesars", "Alex Ovechkin", -121, 101),
             ("fanduel", "Alex Ovechkin", 112, -140)]                        # +112 vs ~-120 consensus = ~+11% EV
    if extra_prices:
        books = extra_prices
    start = (NOW + timedelta(minutes=start_in_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": "ev1", "commence_time": start, "home_team": home, "away_team": away,
            "bookmakers": [{"key": b, "markets": [{"key": "player_shots_on_goal", "outcomes": mk(n, o, u)}]}
                           for b, n, o, u in books]}


# ----------------------------------------------------------------------------- sport-aware parsing
def test_sport_tables_do_not_bleed():
    assert props.stat_of("player_points", "nba") == ("points", False)
    assert props.stat_of("player_points", "nhl") == ("points", False)
    assert props.stat_of("player_shots_on_goal", "nhl") == ("sog", False)
    assert props.stat_of("player_shots_on_goal", "nba") == (None, False)
    assert props.stat_of("player_saves", "nhl") == ("saves", False)
    assert props.stat_of("player_total_saves", "nhl") == ("saves", False)
    assert props.stat_of("player_rush_yds") == ("rush_yds", False)           # NFL default untouched
    assert props.stat_of("player_points_alternate", "nba") == ("points", True)


def test_team_totals_are_not_players_and_resolver_merges_spellings():
    ev = json.load(open(FIX))
    quotes, meta = props.parse_event_props(ev, "nhl")
    assert not any(q.player in ("Washington Capitals", "Montreal Canadiens") for q in quotes)
    r = PlayerResolver(HOCKEY_ROSTER)
    from nhl.config import team_abbrev
    q2, m2 = props.parse_event_props(_sog_event(), "nhl", r, team_abbrev)
    assert {q.player_norm for q in q2} == {"id8471214"}                      # 4 spellings -> ONE player
    assert len(q2) == 5 and all(q.player_id == 8471214 for q in q2)
    q3, _ = props.parse_event_props(_sog_event(), "nhl")                     # no resolver: spellings stay split
    assert len({q.player_norm for q in q3}) >= 2


def test_ambiguous_names_are_dropped_not_guessed():
    from nhl.config import team_abbrev
    r = PlayerResolver([{"player_id": 1, "full_name": "Jack Hughes", "team": "NJD"},
                        {"player_id": 2, "full_name": "Jack Hughes", "team": "VAN"}])
    ev = _sog_event(extra_prices=[("fanduel", "Jack Hughes", -110, -110)], home="Calgary Flames", away="Boston Bruins")
    quotes, meta = props.parse_event_props(ev, "nhl", r, team_abbrev)       # neither NJD nor VAN is in this game
    assert quotes == [] and meta["unresolved"]["ambiguous"] == 2


# ----------------------------------------------------------------------------- outlier scan
def test_outlier_found_with_spellings_merged_and_flagged():
    from nhl.config import team_abbrev
    spec = sw.SPORTS["nhl"]
    rows, stats = sw.outlier_rows(spec, [_sog_event()], NOW, PlayerResolver(HOCKEY_ROSTER), {"WSH", "MTL"})
    assert stats["events"] == 1 and stats["events_resolver_off"] == 0
    top = rows[0]
    assert top["book"] == "fanduel" and top["side"] == "OVER" and top["flag"] and top["reason"] == "FLAG"
    assert top["n_books"] == 4 and top["game_key"] == "nhl:2026-10-14:MTL@WSH" and top["player_id"] == 8471214
    assert 0.03 <= top["ev"] <= sw.EV_CAP


def test_partial_crosswalk_turns_the_resolver_off_for_that_event():
    spec = sw.SPORTS["nhl"]
    rows, stats = sw.outlier_rows(spec, [_sog_event()], NOW, PlayerResolver(HOCKEY_ROSTER), {"WSH"})   # MTL missing
    assert stats["events_resolver_off"] == 1
    assert all(r["player_id"] is None for r in rows)


def test_absurd_edge_is_logged_but_never_flagged():
    prices = [("pinnacle", "Alex Ovechkin", -120, 100), ("bovada", "Alex Ovechkin (WSH)", -118, -102),
              ("betmgm", "Alexander Ovechkin", -122, 102), ("fanduel", "Alex Ovechkin", 150, -190)]   # ~+27% EV
    rows, _ = sw.outlier_rows(sw.SPORTS["nhl"], [_sog_event(extra_prices=prices)], NOW,
                              PlayerResolver(HOCKEY_ROSTER), {"WSH", "MTL"})
    assert rows and rows[0]["ev"] > sw.EV_CAP and not rows[0]["flag"] and rows[0]["reason"] == "EV_TOO_HIGH"


def test_near_start_is_logged_but_not_flagged():
    spec = sw.SPORTS["nhl"]
    rows, _ = sw.outlier_rows(spec, [_sog_event(start_in_min=5)], NOW, PlayerResolver(HOCKEY_ROSTER), {"WSH", "MTL"})
    assert rows and all(not r["flag"] and r["reason"] == "NEAR_START" for r in rows)


def test_unknown_team_event_is_skipped():
    rows, stats = sw.outlier_rows(sw.SPORTS["nhl"], [_sog_event(home="Springfield Isotopes")], NOW)
    assert rows == [] and stats["events_skipped_teams"] == 1


# ----------------------------------------------------------------------------- bank + run_once (stubbed storage)
class Mem:
    def __init__(self):
        self.b = {}

    def exists(self, k):
        return k in self.b

    def read_bytes(self, k):
        return self.b[k]

    def write_bytes(self, d, k):
        self.b[k] = d

    def read_csv(self, k, **kw):
        import io, pandas as pd
        return pd.read_csv(io.BytesIO(self.b[k]))


@pytest.fixture
def mem(monkeypatch):
    m = Mem()
    from mlb_core import storage
    for n in ("exists", "read_bytes", "write_bytes", "read_csv"):
        monkeypatch.setattr(storage, n, getattr(m, n))
    return m


def _bank(mem, ts, events):
    from nba.config import oddsaccum_latest_kind_key, oddsaccum_raw_key
    mem.b[oddsaccum_latest_kind_key("icehockey_nhl", "props")] = json.dumps(
        {"timestamp": ts.isoformat(), "date": "2026-10-14", "hhmm": "1730", "events_priced": len(events)}).encode()
    mem.b[oddsaccum_raw_key("icehockey_nhl", "2026-10-14", "props", "1730")] = json.dumps(events).encode()


def test_stale_snapshot_is_refused(mem):
    _bank(mem, NOW - timedelta(minutes=120), [_sog_event()])
    res = sw.run_once(sw.SPORTS["nhl"], NOW, discord=False)
    assert res["status"] == "skipped" and "old" in res["reason"]
    assert not any("book_outliers" in k for k in mem.b)


def test_no_pointer_is_a_clean_skip(mem):
    assert sw.run_once(sw.SPORTS["nhl"], NOW, discord=False)["status"] == "skipped"


def test_run_once_writes_evidence_and_latest_and_dedups_alerts(mem, monkeypatch):
    _bank(mem, NOW - timedelta(minutes=5), [_sog_event()])
    posted = []
    monkeypatch.setattr(sw, "post_discord", lambda text, embeds=None, sport=None: posted.append((text, embeds)) or True)
    monkeypatch.setattr(sw, "load_resolver", lambda spec: (PlayerResolver(HOCKEY_ROSTER), {"WSH", "MTL"}))
    res = sw.run_once(sw.SPORTS["nhl"], NOW)
    assert res["status"] == "ok" and res["flagged"] >= 1 and res["alerts_sent"] >= 1 and res["validated"] is False
    assert any("/book_outliers_" in k for k in mem.b) and "EVSweep/nhl_props/latest.json" in mem.b
    n = len(posted)
    res2 = sw.run_once(sw.SPORTS["nhl"], NOW)                                 # same quotes again: never re-ping
    assert res2["alerts_sent"] == 0 and len(posted) == n


def test_webhook_fallback_never_reaches_a_member_channel_or_ops(monkeypatch):
    for k in ("DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_OPS", "DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_EV_BOOKS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://member.example/picks")
    monkeypatch.setenv("DISCORD_WEBHOOK_EV_BOOKS", "https://member.example/ev")
    assert sw.webhook_url() == ""
    monkeypatch.setenv("DISCORD_WEBHOOK_OPS", "https://ops.example")            # #ops-alerts is for failures, not this feed
    assert sw.webhook_url() == ""
    monkeypatch.setenv("DISCORD_WEBHOOK_ALERTS", "https://alerts.example")
    assert sw.webhook_url() == "https://alerts.example"


def test_nfl_sweep_signals_use_the_daily_thread_and_run_notices_use_ops(monkeypatch):
    from ev import sweep_nfl_props as nfl
    for k in ("DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_OPS", "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    signals, sent = [], []
    monkeypatch.setattr(nfl, "post_props_signal", lambda sport, text, embeds=None: signals.append((sport, text)) or True)

    class _R:
        status_code = 204

    monkeypatch.setattr(nfl.requests, "post", lambda url, **kw: sent.append(url) or _R())
    monkeypatch.setenv("DISCORD_WEBHOOK_OPS", "https://ops.example")
    assert nfl.post_discord("NFL props sweep: 1 new candidate(s)", []) is True
    assert signals == [("nfl", "NFL props sweep: 1 new candidate(s)")] and sent == []   # a signal never touches the ops hook
    assert nfl.post_discord("NFL props sweep stopped: run credit budget exhausted.", ops=True) is True
    assert sent == ["https://ops.example"] and len(signals) == 1                         # run notices stay in ops
    monkeypatch.setattr(nfl, "post_props_signal", lambda *a, **k: False)
    assert nfl.post_discord("NFL props sweep: 1 new candidate(s)", []) is False         # nothing took it: logged, not an error


def test_nhl_nba_sweep_posts_to_its_own_sports_thread(monkeypatch):
    seen = []
    monkeypatch.setattr(sw, "post_props_signal", lambda sport, text, embeds=None: seen.append(sport) or True)
    assert sw.post_discord("x", [], "nba") is True and sw.post_discord("y", [], "nhl") is True
    assert seen == ["nba", "nhl"]
