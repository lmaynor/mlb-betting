"""The March Madness side: basketball defaults, the tournament round label from ESPN, and a basketball contest end to end. Offline."""
from datetime import datetime, timedelta, timezone

import pytest

from contest import service, store
from contest.rules import ContestRules
from contest.schema import ensure_schema
from contest.sync import sync_events
from tests._contest_engine import make_engine
from tests.test_contest_service import espn_event

NOW = datetime(2027, 3, 18, 16, 0, tzinfo=timezone.utc)
TIP = NOW + timedelta(hours=5)


def test_basketball_defaults_differ_from_football():
    b, f = ContestRules(sport="ncaab"), ContestRules(sport="ncaaf")
    assert b.quote_max_age_s == 4_500 and f.quote_max_age_s == 1_800                       # hourly board vs 20-minute board
    assert b.alt_max_points == 10.0 and f.alt_max_points == 14.0
    assert b.key_weights() == {} and f.key_weights() == {3: 2.5, 7: 1.9}
    assert b.cashout_sigma("total") == 17.2 and b.teaser_price(4.0, 2) == -120 and b.teaser_price(6.0, 2) is None
    assert b.sgp_rho("spread") == pytest.approx(0.02) and b.sgp_rho("ml") == 0.0


def test_an_override_wins_and_old_stored_rules_still_load():
    assert ContestRules(sport="ncaab", quote_max_age_s_override=600).quote_max_age_s == 600
    import json
    old = json.loads(ContestRules(sport="ncaab").to_json())
    old["quote_max_age_s"] = 1_200
    old.pop("quote_max_age_s_override")
    assert ContestRules.from_json(json.dumps(old)).quote_max_age_s == 1_200


def test_a_tournament_game_carries_its_round_and_an_hour_old_board_still_takes_bets(tmp_path):
    engine = make_engine(tmp_path)
    ensure_schema(engine)
    cid = store.create_contest(engine, "mm", "March Madness", ContestRules(sport="ncaab"), NOW)
    store.create_invite(engine, cid, "WELCOME", 5, NOW)
    ev = espn_event("7", kick=TIP)
    ev["round"] = "NCAA Tournament - First Round"
    sync_events(engine, cid, [ev], NOW)
    service.join(engine, "mm", "u1", {"inviteCode": "WELCOME", "displayName": "Alex"}, NOW)
    f = service.feed(engine, "mm", "u1", NOW)
    assert f["games"][0]["round"] == "NCAA Tournament - First Round"
    assert f["rules"]["teaser"]["4.0"]["2"] == -120 and f["rules"]["altMaxPoints"] == 10.0
    later = NOW + timedelta(minutes=70)                                                     # 70 minutes after the last sync: past football's 30, inside basketball's 75
    pick = {"gameId": 1, "market": "spread", "side": "home", "line": 5.5, "price": -112}
    assert service.place(engine, "mm", "u1", {"kind": "straight", "picks": [pick], "stakeCents": 5_000, "key": "k"}, later)["balanceCents"] == 495_000
