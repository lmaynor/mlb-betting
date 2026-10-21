"""nba.games.board: payload contract for the 'our line vs market' board (no edge/EV fields, reliability flag, ladders)."""
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from nba.games import board
from tests.test_nba_games import _games

NOW = datetime(2023, 1, 10, 18, 0, tzinfo=timezone.utc)


def _lines(books=("fanduel", "draftkings", "caesars"), commence="2023-01-11T00:30:00Z", home="Atlanta Hawks",
           away="Boston Celtics"):
    rows = []
    for b in books:
        for market, outs in (("h2h", [(home, -150, None), (away, 130, None)]),
                             ("spreads", [(home, -110, -3.5), (away, -110, 3.5)]),
                             ("totals", [("Over", -110, 224.5), ("Under", -110, 224.5)])):
            for name, price, point in outs:
                rows.append({"event_id": "e1", "commence_time": commence, "event_date": "2023-01-10", "home_team": home,
                             "away_team": away, "book": b, "market": market, "outcome": name, "price": price, "point": point})
    return pd.DataFrame(rows)


def test_payload_shape_and_no_edge_fields():
    p = board.build_payload(_games(260), _lines(), NOW, now=NOW)
    assert p["status"] == "ok" and p["validated"] is False and p["engine"] == board.ENGINE and p["count"] == 1
    assert "NOT +EV" in p["label"]
    g = p["games"][0]
    assert (g["home"], g["away"]) == ("ATL", "BOS") and g["start"] == "2023-01-11T00:30:00Z"
    assert g["market"]["spread_home"] == -3.5 and g["market"]["total"] == 224.5
    flat = repr(g).lower()
    for banned in ("'ev'", "edge", "kelly", "'pick'", "recommend"):
        assert banned not in flat
    assert g["diff"]["margin_pts"] == pytest.approx(g["model"]["margin"] - 3.5, abs=0.11)


def test_ladder_is_monotone_and_centered_on_the_market_line():
    g = board.build_payload(_games(260), _lines(), NOW, now=NOW)["games"][0]
    sp = g["ladders"]["spread_home"]
    assert [x["line"] for x in sp] == [-3.5 + s for s in board.LADDER_STEPS]
    ps = [x["p"] for x in sp]
    assert ps == sorted(ps)                                   # a bigger home handicap can only cover more often
    ov = [x["p"] for x in g["ladders"]["total"]]
    assert ov == sorted(ov, reverse=True)                     # a higher total is over less often


def test_early_season_rows_are_unreliable():
    g = board.build_payload(_games(260), _lines(), NOW, now=NOW)["games"][0]
    assert g["games_min"] is not None and g["reliable"] == (g["games_min"] >= 15)
    now2 = datetime(2023, 10, 20, 18, 0, tzinfo=timezone.utc)                  # next season's opener: ratings are stale
    fresh = board.build_payload(_games(260), _lines(commence="2023-10-21T00:30:00Z"), now2, now=now2)
    assert fresh["count"] == 1 and fresh["reliable_count"] == 0 and fresh["games"][0]["games_min"] == 0


def test_stale_started_and_thin_games_handled():
    old = datetime(2023, 1, 10, 6, 0, tzinfo=timezone.utc)
    assert board.build_payload(_games(260), _lines(), old, now=NOW)["stale"] is True
    started = board.build_payload(_games(260), _lines(commence="2023-01-10T17:00:00Z"), NOW, now=NOW)
    assert started["count"] == 0 and started["status"] == "no_games"
    thin = board.build_payload(_games(260), _lines(books=("fanduel",)), NOW, now=NOW)
    assert thin["count"] == 0                                  # < MIN_BOOKS books on the moneyline
    empty = board.build_payload(_games(260), pd.DataFrame(), None, now=NOW)
    assert empty["status"] == "unavailable" and empty["games"] == []


def test_american_and_json_safety():
    assert board._american(0.6) == -150 and board._american(0.4) == 150 and board._american(1.0) is None
    json.dumps(board.build_payload(_games(260), _lines(), NOW, now=NOW))   # no NaN/np types leak


# ------------------------------------------------------------------------------------------- API getter, route, digest
def _stored(**kw):
    p = board.build_payload(_games(260), _lines(), NOW, now=NOW)
    p["games"][0]["reliable"] = True
    p["games"][0]["diff"].update(margin_pts=4.2, total_pts=-1.0)
    p.update(kw)
    return p


def test_public_getter_serves_empty_shape_and_filters_day(monkeypatch):
    from mlb.runners import public_api as pa
    monkeypatch.setattr(board, "read", lambda: None)
    out = pa.get_nba_games_board()
    assert out["status"] == "unavailable" and out["games"] == [] and out["validated"] is False
    stored = _stored(ts=datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(board, "read", lambda: stored)
    live = pa.get_nba_games_board()
    assert live["count"] == 1 and live["reliable_count"] == 1 and live["validated"] is False and live["stale"] is False
    assert pa.get_nba_games_board(day="2023-01-10")["count"] == 1          # 00:30Z on the 11th is Jan 10 in Central
    assert pa.get_nba_games_board(day="2023-01-11")["count"] == 0
    old = _stored(ts="2020-01-01T00:00:00+00:00")
    monkeypatch.setattr(board, "read", lambda: old)
    assert pa.get_nba_games_board()["stale"] is True
    with pytest.raises(ValueError):
        pa.get_nba_games_board(day="tomorrow")


def test_route_wires_the_getter(monkeypatch):
    import main
    from unittest.mock import patch
    monkeypatch.setattr(main, "_auth_required", lambda req: None)
    client = main.app.test_client()
    with patch("main.get_nba_games_board", return_value={"sport": "nba", "games": []}) as g:
        assert client.get("/api/public/nba-games?day=2026-10-21").status_code == 200
        client.get("/api/public/nba-games")
    assert g.call_args_list[0].kwargs == {"day": "2026-10-21"} and g.call_args_list[1].kwargs == {"day": None}
    with patch("main.get_nba_games_board", side_effect=ValueError("day must be YYYY-MM-DD")):
        assert client.get("/api/public/nba-games?day=bad").status_code == 400


def test_digest_gating_dedupe_and_language(monkeypatch):
    from nba.games import board_post as bpost
    payload = _stored()
    assert len(bpost.pick_rows(payload)) == 1
    payload["games"][0]["reliable"] = False
    assert bpost.pick_rows(payload) == []                                   # unreliable rows are never posted
    payload = _stored()
    payload["games"][0]["diff"].update(margin_pts=1.0, total_pts=1.0)
    assert bpost.pick_rows(payload) == []                                   # below both gaps
    payload = _stored()
    monkeypatch.setattr(bpost, "_read_state", lambda: {})
    dry = bpost.post_digest(payload, now=NOW, dry_run=True)
    assert dry["status"] == "dry_run"
    text = json.dumps(dry["embed"]).lower()
    for banned in ("edge", "+ev", "value", "pick of", "lock", "bet "):
        assert banned not in text.replace("not +ev", "")
    assert "unvalidated" in text and "https://beezy.fyi/picks/nba" in text
    monkeypatch.setattr(bpost, "_read_state", lambda: {"day": bpost._central_day(NOW)})
    assert bpost.post_digest(payload, now=NOW)["status"] == "already_posted"
    assert bpost.post_digest({**payload, "stale": True}, now=NOW)["status"] == "stale"


def test_digest_posts_once_and_records_state(monkeypatch):
    from nba.games import board_post as bpost
    written = {}
    monkeypatch.setattr(bpost, "_read_state", lambda: {})
    monkeypatch.setattr(bpost, "bot_configured", lambda: True)
    monkeypatch.setattr(bpost, "post_via_bot", lambda *a, **k: True)
    from mlb_core import storage
    monkeypatch.setattr(storage, "write_bytes", lambda b, k: written.update({k: json.loads(b)}))
    res = bpost.post_digest(_stored(), now=NOW)
    assert res["status"] == "posted" and written[bpost.STATE_KEY]["day"] == bpost._central_day(NOW)
    monkeypatch.setattr(bpost, "post_via_bot", lambda *a, **k: False)
    monkeypatch.setattr(bpost, "webhook_for", lambda *a, **k: None)
    written.clear()
    assert bpost.post_digest(_stored(), now=NOW)["status"] == "undelivered" and not written     # no state on failure
