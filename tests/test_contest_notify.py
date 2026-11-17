"""contest.notify: what gets announced, once, with no hidden information. Offline: a fake sender."""
from datetime import datetime, timedelta, timezone

import pytest

from contest import notify, service, store
from contest.rules import ContestRules
from contest.schema import ensure_schema
from contest.sync import sync_events
from tests._contest_engine import make_engine
from tests.test_contest_service import KICK, espn_event

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)
SLUG = "bowls"


@pytest.fixture
def w(tmp_path):
    engine = make_engine(tmp_path)
    ensure_schema(engine)
    cid = store.create_contest(engine, SLUG, "Bowl Bankroll", ContestRules(sport="ncaaf"), NOW)
    store.create_invite(engine, cid, "WELCOME", 5, NOW)
    sync_events(engine, cid, [espn_event("1")], NOW)
    service.join(engine, SLUG, "user_secret_1", {"inviteCode": "WELCOME", "displayName": "Alex"}, NOW)
    return engine, cid


class Sink:
    def __init__(self, fail=False):
        self.msgs, self.fail = [], fail

    def __call__(self, m):
        if self.fail:
            raise RuntimeError("webhook down")
        self.msgs.append(m)


def test_the_switch_needs_both_the_flag_and_a_webhook():
    assert not notify.enabled({}) and not notify.enabled({"CONTEST_NOTIFY": "1"}) and not notify.enabled({"CONTEST_DISCORD_WEBHOOK": "https://x"})
    for off in ("", "0", "false", "off", "no"):
        assert not notify.enabled({"CONTEST_NOTIFY": off, "CONTEST_DISCORD_WEBHOOK": "https://x"})
    assert notify.enabled({"CONTEST_NOTIFY": "1", "CONTEST_DISCORD_WEBHOOK": "https://x"})


def test_kickoff_is_announced_once_inside_the_window_and_never_before_or_long_after(w):
    engine, cid = w
    s = Sink()
    assert notify.notify_pass(engine, cid, KICK - timedelta(minutes=5), s)["sent"] == 0
    assert notify.notify_pass(engine, cid, KICK + timedelta(minutes=2), s)["sent"] == 1
    assert notify.notify_pass(engine, cid, KICK + timedelta(minutes=4), s)["sent"] == 0       # already claimed
    assert s.msgs == ["Kickoff: BYU at Kansas. Picks on this game are now visible in the Pool."]


def test_a_late_first_run_does_not_replay_a_kickoff_from_hours_ago(w):
    engine, cid = w
    s = Sink()
    assert notify.notify_pass(engine, cid, KICK + timedelta(hours=3), s) == {"sent": 0, "skipped": 0, "failed": 0}


def test_a_final_posts_the_score_and_the_top_of_the_board_with_names_only(w):
    engine, cid = w
    s = Sink()
    notify.notify_pass(engine, cid, KICK + timedelta(minutes=1), s)                          # first pass: kickoff
    sync_events(engine, cid, [espn_event("1", state="post", name="STATUS_FINAL", completed=True, scores=("21", "20"))], KICK + timedelta(hours=4))
    notify.notify_pass(engine, cid, KICK + timedelta(hours=4), s)
    final = s.msgs[-1]
    assert final.startswith("Final: BYU 20, Kansas 21.") and "1. Alex $5,000.00" in final
    assert "user_secret_1" not in " ".join(s.msgs)


def test_history_is_recorded_quietly_on_the_first_pass_and_a_dead_webhook_retries(w):
    engine, cid = w
    sync_events(engine, cid, [espn_event("1", state="post", name="STATUS_FINAL", completed=True, scores=("21", "20"))], KICK + timedelta(hours=4))
    quiet = Sink()
    assert notify.notify_pass(engine, cid, KICK + timedelta(hours=5), quiet) == {"sent": 0, "skipped": 1, "failed": 0}
    sync_events(engine, cid, [espn_event("2", kick=KICK + timedelta(hours=6))], NOW)
    down = Sink(fail=True)
    assert notify.notify_pass(engine, cid, KICK + timedelta(hours=6, minutes=1), down)["failed"] == 1
    up = Sink()
    assert notify.notify_pass(engine, cid, KICK + timedelta(hours=6, minutes=2), up)["sent"] == 1     # the claim was released, so it retries


def test_an_eliminated_entry_is_announced_once(w):
    engine, cid = w
    with engine.begin() as c:
        import sqlalchemy as sa
        c.execute(sa.text("UPDATE contest_entry SET status = 'eliminated'"))
    s = Sink()
    notify.notify_pass(engine, cid, NOW, s)                                                    # quiet first pass records it
    store.create_invite(engine, cid, "X", 1, NOW)
    with engine.begin() as c:
        import sqlalchemy as sa
        c.execute(sa.text("DELETE FROM contest_notice"))
        c.execute(sa.text("INSERT INTO contest_notice (contest_id, key, sent_at) VALUES (:c, 'marker', 'x')"), {"c": cid})
    assert notify.notify_pass(engine, cid, NOW, s)["sent"] == 1 and s.msgs == ["Alex is out of the contest."]
    assert notify.notify_pass(engine, cid, NOW, s)["sent"] == 0
