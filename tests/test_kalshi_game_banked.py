"""Banked game-line legs for the Kalshi RFQ fair book (ev.kalshi_game_banked + the daemon's FeedState wiring). Offline."""

import importlib.util
import os
from datetime import datetime, timedelta, timezone

import pytest

from ev import kalshi_game_banked as kb
from ev.kalshi_rfq_pricer import REJ_STALE_CONSENSUS, InMemoryFairBook, KalshiRFQPricer

NOW = 1_800_000_000.0
NOW_DT = datetime.fromtimestamp(NOW, timezone.utc)
FETCHED = NOW - 600.0                                  # a BestBook file fetched 10 minutes ago


def _iso(dt):
    return dt.isoformat()


def _quotes(ml=((170, -200),), spread=(-3.5, -110, -110), total=(47.5, -110, -110), books=("fanduel", "draftkings", "betmgm")):
    """Board-format quotes for one game: a = AWAY / OVER, b = HOME / UNDER, spread line = HOME handicap."""
    out = []
    for i, book in enumerate(books):
        a, b = ml[i % len(ml)]
        out.append({"market": "ml", "line": None, "book": book, "a": a, "b": b})
        out.append({"market": "spread", "line": spread[0], "book": book, "a": spread[1], "b": spread[2]})
        out.append({"market": "total", "line": total[0], "book": book, "a": total[1], "b": total[2]})
    return out


def _game(away="BUF", home="KC", start=None, quotes=None):
    start = start or NOW_DT + timedelta(hours=3)
    return {"bb_key": f"x:{away}-{home}", "away_team": away, "home_team": home, "commence_time": _iso(start),
            "quotes": _quotes() if quotes is None else quotes}


def _meta(age_min=10.0):
    return {"age_min": age_min, "fetched_at": _iso(datetime.fromtimestamp(FETCHED, timezone.utc)), "games": 1}


@pytest.fixture
def feeds(monkeypatch):
    """Patch the two readers banked_game_rows uses; each test sets .bestbook / .parlay."""
    from ev import bestbook_events

    class F:
        bestbook = (None, {"reason": "no BestBook game-lines file yet"})
        parlay = (None, {"reason": "no pointer yet"})
        calls = []

    monkeypatch.setattr(bestbook_events, "read_latest_game_lines", lambda s, n, m: F.bestbook)
    import nba.game.odds_feed as of
    monkeypatch.setattr(of, "read_banked_game_lines", lambda k, n: F.parlay)
    return F


# ---------------------------------------------------------------------------------------------------- BestBook -> rows
def test_bestbook_nfl_game_prices_ml_spread_total_and_leaves_kalshi_and_dfs_out(feeds):
    quotes = _quotes() + [
        {"market": "ml", "line": None, "book": "kalshi", "a": 500, "b": -700},                 # exchange: never in the consensus
        {"market": "ml", "line": None, "book": "prizepicks", "a": 900, "b": -900},             # DFS: not a price
        {"market": "ml", "line": None, "book": "bet365", "a": 170, "b": None},                 # one-sided: cannot be de-vigged
    ]
    feeds.bestbook = ([_game(quotes=quotes)], _meta())
    rows, stats = kb.banked_game_rows(["nfl"], NOW)
    by = {r["ticker"].split("-", 1)[0] + ":" + r["ticker"].rsplit("-", 1)[1]: r for r in rows}
    kc, buf = by["KXNFLGAME:KC"], by["KXNFLGAME:BUF"]
    assert kc["p_yes"] == pytest.approx(0.6429, abs=0.002) and kc["p_yes"] + buf["p_yes"] == pytest.approx(1.0)
    assert kc["n_books"] == 3 and buf["n_books"] == 3                                          # kalshi / DFS / one-sided excluded
    assert by["KXNFLSPREAD:KC4"]["p_yes"] == pytest.approx(0.5) and by["KXNFLSPREAD:KC4"]["strike"] == 3.5
    assert by["KXNFLTOTAL:48"]["p_yes"] == pytest.approx(0.5) and by["KXNFLTOTAL:48"]["strike"] == 47.5
    assert stats["nfl"]["source"] == "bestbook" and stats["nfl"]["games_priced"] == 1


def test_rows_carry_the_feeds_own_timestamp_not_the_read_time(feeds):
    feeds.bestbook = ([_game()], _meta())
    rows, _ = kb.banked_game_rows(["nfl"], NOW)
    assert rows and all(r["updated_ts"] == pytest.approx(FETCHED) for r in rows)


def test_stale_banked_rows_are_rejected_by_the_leg_gate_not_priced_silently(feeds):
    feeds.bestbook = ([_game()], _meta())
    rows, _ = kb.banked_game_rows(["nfl"], NOW)
    book = InMemoryFairBook(clock=lambda: NOW)
    book.load_rows(rows, NOW)
    t = next(r["ticker"] for r in rows if r["market_class"] == "game_ml")
    pricer = KalshiRFQPricer(book)
    assert pricer.leg_gate(book.get(t, NOW), NOW) is None                                      # 10 min old: inside the 1500 s gate
    assert pricer.leg_gate(book.get(t, NOW + 3600.0), NOW + 3600.0) == REJ_STALE_CONSENSUS     # an hour later the same row is stale


def test_started_and_unmappable_games_are_dropped_and_counted(feeds):
    started = _game(start=NOW_DT - timedelta(minutes=5))
    unknown = _game(away="ZZZ")
    same = _game(away="KC", home="KC")
    feeds.bestbook = ([started, unknown, same, _game(away="DAL", home="PHI")], _meta())
    rows, stats = kb.banked_game_rows(["nfl"], NOW)
    assert {r["game_key"].split(":")[2] for r in rows} == {"DAL@PHI"}
    assert stats["nfl"]["started"] == 1 and stats["nfl"]["unmapped"] == 2


def test_nhl_uses_the_kalshi_code_crosswalk_and_never_emits_totals(feeds):
    feeds.bestbook = ([_game(away="EDM", home="NJD", quotes=_quotes(spread=(-1.5, 150, -180), total=(6.5, -110, -110)))], _meta())
    rows, _ = kb.banked_game_rows(["nhl"], NOW)
    tickers = [r["ticker"] for r in rows]
    assert tickers and all(t.startswith(("KXNHLGAME-", "KXNHLSPREAD-")) for t in tickers)       # NHL totals stay disabled
    assert any(t.endswith("-NJ") for t in tickers) and not any("NJD" in t for t in tickers)    # repo NJD -> Kalshi NJ
    assert any(t.startswith("KXNHLSPREAD-") and t.endswith("-NJ2") for t in tickers)            # home -1.5 == 'NJ wins by over 1.5'


def test_spread_rung_is_the_home_handicap_even_for_an_away_favourite(feeds):
    # home handicap +2.5 => the AWAY team is the favourite: 'BUF wins by over 2.5'
    feeds.bestbook = ([_game(quotes=_quotes(spread=(2.5, -120, 100)))], _meta())
    rows, _ = kb.banked_game_rows(["nfl"], NOW)
    r = next(x for x in rows if x["ticker"].startswith("KXNFLSPREAD-") and x["ticker"].endswith("-BUF3") and not x.get("pooled"))
    assert r["strike"] == 2.5 and 0.5 < r["p_yes"] < 0.6                                       # the favourite covers, a bit better than a coin flip


# ---------------------------------------------------------------------------------------------------- source fallback
def _parlay_event():
    def bm(key):
        return {"key": key, "markets": [{"key": "h2h", "outcomes": [{"name": "Boston Celtics", "price": -200},
                                                                    {"name": "Los Angeles Lakers", "price": 170}]}]}
    return {"home_team": "Boston Celtics", "away_team": "Los Angeles Lakers", "bookmakers": [bm("fanduel"), bm("draftkings")],
            "commence_time": _iso(NOW_DT + timedelta(hours=4))}


def test_nba_falls_back_to_banked_parlayapi_when_bestbook_is_stale(feeds):
    snap = datetime.fromtimestamp(NOW - 3000.0, timezone.utc)
    feeds.parlay = ([_parlay_event()], {"snapshot_ts": _iso(snap), "age_min": 50.0})
    rows, stats = kb.banked_game_rows(["nba"], NOW)
    assert stats["nba"]["source"] == "oddsaccum" and {r["market_class"] for r in rows} == {"game_ml"}
    assert all(r["updated_ts"] == pytest.approx(NOW - 3000.0) for r in rows)                   # the snapshot's age, not now


def test_bestbook_wins_over_banked_parlayapi_when_both_are_fresh(feeds):
    feeds.bestbook = ([_game(away="LAL", home="BOS")], _meta())
    feeds.parlay = ([_parlay_event()], {"snapshot_ts": _iso(NOW_DT), "age_min": 0.0})
    _, stats = kb.banked_game_rows(["nba"], NOW)
    assert stats["nba"]["source"] == "bestbook"


def test_nfl_has_no_banked_parlayapi_fallback_and_says_why(feeds):
    feeds.bestbook = (None, {"reason": "BestBook game-lines file is 200 min old (> 180)"})
    rows, stats = kb.banked_game_rows(["nfl"], NOW)
    assert rows == [] and "200 min old" in stats["nfl"]["unavailable"]


def test_one_failing_sport_is_reported_never_fatal(feeds, monkeypatch):
    from ev import bestbook_events

    def boom(sport, now, max_age):
        if sport == "nhl":
            raise RuntimeError("gcs down")
        return [_game()], _meta()
    monkeypatch.setattr(bestbook_events, "read_latest_game_lines", boom)
    rows, stats = kb.banked_game_rows(["nhl", "nfl"], NOW)
    assert "error" in stats["nhl"] and stats["nfl"]["source"] == "bestbook" and rows


# ---------------------------------------------------------------------------------------------------- daemon wiring
def _daemon():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("kalshi_rfq_daemon", os.path.join(root, "scripts", "kalshi_rfq_daemon.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(sport, ticker, ts=NOW):
    return {"ticker": ticker, "sport": sport, "game_key": f"{sport}:2027-01-15:A@B", "market_class": "game_ml", "player_norm": None,
            "stat": "ml", "strike": 0.0, "p_yes": 0.55, "n_books": 4, "disp": 0.01, "updated_ts": ts}


class _Client:
    def __init__(self):
        self.pulled = []

    def get_slate(self, key, markets=None):
        self.pulled.append(key)
        return []


def test_banked_sport_is_not_pulled_live_and_a_failed_one_is():
    d = _daemon()
    book = InMemoryFairBook(clock=lambda: NOW)
    client = _Client()
    loader = lambda sports, now: ([_row("nfl", "KXNFLGAME-A")], {"nfl": {"source": "bestbook"}, "nba": {"unavailable": "x"}})  # noqa: E731
    fs = d.FeedState(book, "unused", None, client, ("nfl", "nba"), banked_sports=("nfl", "nba"), banked_loader=loader)
    fs.refresh_banked()
    fs.refresh_games()
    assert client.pulled == ["basketball_nba"]                       # nfl served by the free feed (no credits), nba falls back to live
    assert fs.banked_ok == {"nfl"}


def test_live_source_keeps_the_old_behaviour_and_auto_filters_to_banked_capable_sports():
    d = _daemon()
    assert d.banked_sports_for("live", ("nfl", "nba")) == ()
    assert d.banked_sports_for("auto", ("nfl", "nba", "mlb")) == ("nfl", "nba")
    book = InMemoryFairBook(clock=lambda: NOW)
    client = _Client()
    fs = d.FeedState(book, "unused", None, client, ("nfl", "nba"))     # no banked sports: every sport is pulled live
    fs.refresh_banked()
    fs.refresh_games()
    assert client.pulled == ["americanfootball_nfl", "basketball_nba"]


def test_banked_rows_replace_live_rows_for_that_sport_and_a_failed_refresh_keeps_the_old_ones():
    d = _daemon()
    book = InMemoryFairBook(clock=lambda: NOW)
    state = {"rows": [_row("nfl", "KXNFLGAME-OLD")]}
    loader = lambda sports, now: (list(state["rows"]), {})                                  # noqa: E731
    fs = d.FeedState(book, "unused", None, None, (), banked_sports=("nfl",), banked_loader=loader)
    fs.game_rows = [_row("nfl", "KXNFLGAME-LIVE"), _row("nba", "KXNBAGAME-LIVE")]
    fs.refresh_banked()
    assert {r["ticker"] for r in fs.game_rows} == {"KXNBAGAME-LIVE"}                          # nfl live rows dropped, nba untouched
    assert book.get("KXNFLGAME-OLD", NOW) is not None and book.get("KXNFLGAME-LIVE", NOW) is None
    state["rows"] = []                                                                        # a refresh that served nothing
    fs.refresh_banked()
    assert fs.banked_ok == set() and book.get("KXNFLGAME-OLD", NOW) is not None               # old rows stay; the staleness gate ages them
