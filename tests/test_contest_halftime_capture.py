"""Offline tests for scripts/contest_halftime_capture.py: phase classification, ESPN<->Kalshi matching, the summary. No network."""
import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("contest_halftime_capture", Path(__file__).resolve().parents[1] / "scripts" / "contest_halftime_capture.py")
cap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cap)


def st(name="STATUS_IN_PROGRESS", state="in", period=1, clock=600.0, detail="10:00 - 1st Half", completed=False):
    return {"clock": clock, "period": period, "type": {"name": name, "state": state, "completed": completed, "detail": detail, "shortDetail": detail}}


@pytest.mark.parametrize("sport,status,expected", [
    ("ncaab", st(period=1, clock=900), "H1"),
    ("ncaab", st(period=1, clock=200), "H1_LATE"),
    ("ncaab", st(name="STATUS_HALFTIME", period=2, clock=0, detail="Halftime"), "HALFTIME"),
    ("ncaab", st(period=2, clock=1150, detail="19:10 - 2nd Half"), "H2_EARLY"),   # "2nd Half" must NOT read as halftime
    ("ncaab", st(period=2, clock=400, detail="6:40 - 2nd Half"), "H2"),
    ("ncaab", st(name="STATUS_FINAL", state="post", completed=True, detail="Final"), "FINAL"),
    ("ncaab", st(name="STATUS_SCHEDULED", state="pre", period=0, clock=0, detail="7:00 PM"), "PRE"),
    ("ncaaf", st(period=2, clock=120, detail="2:00 - 2nd Quarter"), "H1_LATE"),
    ("ncaaf", st(period=1, clock=60, detail="1:00 - 1st Quarter"), "H1"),            # end of Q1 is not the half
    ("ncaaf", st(period=3, clock=880, detail="14:40 - 3rd Quarter"), "H2_EARLY"),
    ("ncaaf", st(period=3, clock=300, detail="5:00 - 3rd Quarter"), "H2"),
    ("ncaaf", st(name="STATUS_HALFTIME", period=2, clock=0, detail="Halftime"), "HALFTIME"),
])
def test_phase(sport, status, expected):
    assert cap.phase(sport, status) == expected


def test_norm_aliases():
    assert cap.norm("Long Beach St.") == cap.norm("Long Beach State") == "long beach state"
    assert cap.norm("Saint Mary's") == "saint marys"


def _event(eid, away, home, status):
    t = lambda loc: {"location": loc, "shortDisplayName": loc, "displayName": loc + " Team"}
    return {"id": eid, "name": f"{away} at {home}", "status": status, "competitions": [{"competitors": [
        {"homeAway": "home", "score": "40", "team": t(home)}, {"homeAway": "away", "score": "38", "team": t(away)}]}]}


def _kalshi(series, ticker, event_ticker, title, bid, ask):
    return {"ticker": ticker, "event_ticker": event_ticker, "title": title, "yes_bid_dollars": bid, "yes_ask_dollars": ask, "last_price_dollars": "0.5"}


MARKETS = {
    "game": [_kalshi("game", "KXNCAAMBGAME-26NOV17ILLDUKE-DUKE", "KXNCAAMBGAME-26NOV17ILLDUKE", "Duke wins", "0.58", "0.62"),
             _kalshi("game", "KXNCAAMBGAME-26NOV17ILLDUKE-ILL", "KXNCAAMBGAME-26NOV17ILLDUKE", "Illinois wins", "0.38", "0.42")],
    "spread": [_kalshi("spread", "KXNCAAMBSPREAD-26NOV17ILLDUKE-DUKE3", "KXNCAAMBSPREAD-26NOV17ILLDUKE", "Duke wins by over 2.5 points", "0.51", "0.55"),
               _kalshi("spread", "KXNCAAMBSPREAD-26NOV17ILLDUKE-DUKE40", "KXNCAAMBSPREAD-26NOV17ILLDUKE", "Duke wins by over 39.5 points", "0.00", "0.01")],
    "total": [_kalshi("total", "KXNCAAMBTOTAL-26NOV17ILLDUKE-150", "KXNCAAMBTOTAL-26NOV17ILLDUKE", "Full Game: Over 149.5 points scored", "0.45", "0.50")],
}


def test_match_and_rows():
    events = cap.kalshi_events(MARKETS)
    assert set(events) == {"26NOV17ILLDUKE"}
    assert events["26NOV17ILLDUKE"]["teams"] == {"duke", "illinois"}
    row = cap.espn_rows("ncaab", {"events": [_event("1", "Illinois", "Duke", st(name="STATUS_HALFTIME", period=2, clock=0, detail="Halftime"))]}, "2026-11-18T01:00:00+00:00")[0]
    assert row["phase"] == "HALFTIME" and row["score"] == ["38", "40"]
    key = cap.match_game(row, events)
    assert key == "26NOV17ILLDUKE"
    quotes = cap.kalshi_rows("1", events[key], "t")
    assert {q["series"] for q in quotes} == {"game", "spread", "total"}
    assert all("DUKE40" not in q["ticker"] for q in quotes)          # 0/1c placeholder rung dropped


def test_match_fails_closed():
    events = cap.kalshi_events(MARKETS)
    other = cap.espn_rows("ncaab", {"events": [_event("2", "Kansas", "Duke", st())]}, "t")[0]
    assert cap.match_game(other, events) is None


def test_summarize(tmp_path):
    p = tmp_path / "cap.jsonl"
    rows = []
    for i, (phase_, mid) in enumerate([("H1_LATE", 0.60), ("HALFTIME", 0.60), ("HALFTIME", 0.62), ("H2_EARLY", 0.62)]):
        t = f"2026-11-18T01:0{i}:00+00:00"
        rows.append({"t": t, "k": "espn", "id": "1", "name": "Illinois at Duke", "status": "STATUS_HALFTIME" if phase_ == "HALFTIME" else "STATUS_IN_PROGRESS",
                     "detail": "Halftime" if phase_ == "HALFTIME" else "x", "period": 2, "clock": 0, "phase": phase_, "score": ["38", "40"]})
        rows.append({"t": t, "k": "kalshi", "id": "1", "series": "game", "ticker": "DUKE", "title": "Duke wins", "bid": mid - 0.02, "ask": mid + 0.02, "last": mid})
        rows.append({"t": t, "k": "kalshi", "id": "1", "series": "game", "ticker": "ILL", "title": "Illinois wins", "bid": 0.36, "ask": 0.40, "last": 0.38})
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = cap.summarize(str(p))
    assert "STATUS_HALFTIME" in out and "favourite DUKE" in out and "distinct scores from halftime start through window end: 1" in out
    assert "share of snapshots within 6c: 100%" in out
