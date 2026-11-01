"""mlb_core.alerts.sport_alerts + the sweeps' member-channel routing."""
import pytest

from mlb_core.alerts import sport_alerts as sa


def row(ev=0.12, n_books=6, p_fair=0.5, **kw):
    r = dict(game_key="nhl:BOS@NYR", player="Pat One", player_norm="pat one", stat="shots_on_goal", side="over",
             point=2.5, american=110, book="draftkings", ev=ev, n_books=n_books, p_fair=p_fair, flag=True)
    r.update(kw)
    return r


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in ("ALERTS_MIN_SCORE", "DISCORD_WEBHOOK_CARD_NHL", "DISCORD_WEBHOOK_URL", "BEEZY_BOT_URL", "BEEZY_BOT_SECRET"):
        monkeypatch.delenv(v, raising=False)


@pytest.fixture(autouse=True)
def no_ledger_writes(monkeypatch, request):
    """post_sport_alerts logs to the bets ledger; keep these delivery tests from writing a sqlite file into the repo."""
    if "ledger" not in request.node.name:
        monkeypatch.setattr(sa, "log_sport_alert_bets", lambda *a, **k: 0)


def test_gate_drops_low_scores_and_sorts_best_first():
    rows = [row(ev=0.03, player="Low"), row(ev=0.14, player="High"), row(ev=0.09, player="Mid")]
    out = sa.gate(rows)
    assert [r["player"] for r in out] == ["High", "Mid"]            # 3% edge scores below the LEAN cut (45)
    assert out[0]["score"] >= out[1]["score"] >= sa.min_score()


def test_floor_is_configurable(monkeypatch):
    monkeypatch.setenv("ALERTS_MIN_SCORE", "70")
    assert sa.gate([row(ev=0.09)]) == []
    monkeypatch.setenv("ALERTS_MIN_SCORE", "garbage")
    assert sa.min_score() == sa.DEFAULT_MIN_SCORE


def test_alert_embed_explains_the_price_in_plain_words_with_score_and_links():
    r = sa.gate([row(game_key="nfl:2026-10-25:KC@BUF", stat="pass_yds", point=267.5, side="over", american=-110,
                     p_fair=0.542, mins_to_kick=190, player="Pat One")])[0]
    e = sa.alert_embed(r)
    assert e["title"] == "Pat One Over 267.5 Pass Yds"
    assert "DraftKings -110" in e["description"] and "needs 52.4% to hit" in e["description"]
    assert "6 books put it at 54.2%" in e["description"] and "KC @ BUF | starts in 3h 10m" in e["description"]
    assert "paper / unvalidated | not a recommendation" in e["footer"]["text"] and "Beezy Score" in e["footer"]["text"]
    assert "beezy.fyi/ev" in e["url"]


def test_stat_names_mirror_the_projection_board_labels():
    from ev.daily_projections import STAT_LABEL
    assert sa.STAT_NAMES == STAT_LABEL


def test_signal_message_is_copyable_text_plus_one_embed_per_game():
    rows = [row(player="A", game_key="nhl:BOS@NYR"), row(player="B", game_key="nhl:BOS@NYR", ev=0.2),
            row(player="C", game_key="nhl:TOR@MTL")]
    content, embeds = sa.signal_message(rows, "nhl")
    assert "**" not in content and len(content.splitlines()) == 3                      # one plain slip line per row
    assert "A Over 2.5 shots on goal @ DraftKings +110" in content.splitlines()
    assert [e["title"] for e in embeds[:2]] == ["BOS @ NYR | 2 signals", "TOR @ MTL | 1 signal"]
    assert embeds[0]["description"].index("**B**") < embeds[0]["description"].index("**A**")     # best EV first
    assert "unvalidated" in embeds[-1]["description"]


def test_slip_line_is_plain_text():
    assert sa.slip_line(row()) == "Pat One Over 2.5 shots on goal @ DraftKings +110"


def test_post_uses_the_sport_card_webhook_and_reports_delivery(monkeypatch):
    sent = []

    class R:
        status_code = 204

    monkeypatch.setenv("DISCORD_WEBHOOK_CARD_NHL", "https://hook.test/nhl")
    monkeypatch.setattr("requests.post", lambda url, json=None, timeout=None: sent.append((url, json)) or R())
    assert sa.post_sport_alerts("nhl", sa.gate([row()])) is True
    assert sent[0][0] == "https://hook.test/nhl" and sent[0][1]["embeds"]


def test_no_destination_means_not_delivered_so_callers_retry():
    assert sa.post_sport_alerts("nhl", sa.gate([row()])) is False
    assert sa.post_sport_alerts("nhl", []) is False


def test_bot_first_then_webhook_fallback(monkeypatch):
    calls = []
    monkeypatch.setenv("BEEZY_BOT_URL", "https://bot.test")
    monkeypatch.setenv("BEEZY_BOT_SECRET", "s")
    monkeypatch.setattr(sa, "post_via_bot", lambda kind, content, embeds, sport=None, **kw: calls.append((kind, sport)) or True)
    assert sa.post_sport_alerts("nfl", sa.gate([row()])) is True
    assert calls == [("card", "nfl")]


def test_each_alert_is_its_own_bot_message_with_a_slip_thread(monkeypatch):
    calls = []
    monkeypatch.setenv("BEEZY_BOT_URL", "https://bot.test")
    monkeypatch.setenv("BEEZY_BOT_SECRET", "s")
    monkeypatch.setattr(sa, "post_via_bot", lambda kind, content, embeds, sport=None, **kw: calls.append((content, embeds, kw)) or True)
    rows = sa.gate([row(player="Low", ev=0.10), row(player="High", ev=0.14)])
    assert sa.post_sport_alerts("nhl", rows) is True
    assert [c[0] for c in calls] == ["High Over 2.5 shots on goal @ DraftKings +110", "Low Over 2.5 shots on goal @ DraftKings +110"]
    for content, embeds, kw in calls:
        assert len(embeds) == 1 and kw["thread"] == {"name": content, "content": content}   # slip bots are @mentioned in the thread


def test_highlights_are_capped_per_pass(monkeypatch):
    calls = []
    monkeypatch.setenv("BEEZY_BOT_URL", "https://bot.test")
    monkeypatch.setenv("BEEZY_BOT_SECRET", "s")
    monkeypatch.setattr(sa, "post_via_bot", lambda *a, **k: calls.append(1) or True)
    rows = sa.gate([row(player=f"P{i}", ev=0.14) for i in range(sa.MAX_ALERTS + 5)])
    assert sa.post_sport_alerts("nhl", rows) is True and len(calls) == sa.MAX_ALERTS


def test_webhook_fallback_keeps_the_slip_text_and_every_embed(monkeypatch):
    sent = []

    class R:
        status_code = 204

    monkeypatch.setenv("DISCORD_WEBHOOK_CARD_NHL", "https://hook.test/nhl")
    monkeypatch.setattr("requests.post", lambda url, json=None, timeout=None: sent.append(json) or R())
    assert sa.post_sport_alerts("nhl", sa.gate([row(player="A"), row(player="B", ev=0.2)])) is True
    assert sent[0]["content"].count("\n") == 1 and len(sent[0]["embeds"]) == 2


def test_props_sweep_posts_gated_highlights_and_every_row_to_the_signals_thread(monkeypatch):
    import ev.sweep_props_alerts as spa
    from mlb_core import storage
    member, admin = [], []
    monkeypatch.setattr(storage, "exists", lambda k: False)
    monkeypatch.setattr(storage, "write_bytes", lambda *a, **k: None)
    monkeypatch.setattr("mlb_core.alerts.sport_alerts.post_via_bot", lambda *a, **k: False)
    monkeypatch.setattr("mlb_core.alerts.sport_alerts.post_sport_alerts",
                        lambda sport, rows: member.append((sport, [r["player"] for r in rows])) or True)
    monkeypatch.setattr(spa, "post_discord", lambda text, embeds=None, sport=None, **kw: admin.append((sport, text)) or True)
    rows = [row(player="Strong", ev=0.14), row(player="Weak", ev=0.03, player_norm="weak")]
    assert spa.post_new(spa.SPORTS["nhl"], rows, "2026-10-17") == 2
    assert member == [("nhl", ["Strong"])]                 # only the gated row is highlighted top-level
    assert len(admin) == 1 and admin[0][0] == "nhl"       # the signals thread still receives the full batch
    assert "Strong Over 2.5" in admin[0][1] and "Weak Over 2.5" in admin[0][1]    # as plain copyable slip lines


# -- bets-ledger logging (real sweep row shape: ev.sweep_props_alerts.outlier_rows) ------------------------------------------
class _FakeTracker:
    instances = []

    def __init__(self, db, system=None, sport=None):
        self.db, self.system, self.sport, self.calls = db, system, sport, []
        _FakeTracker.instances.append(self)

    def log_bet(self, **kw):
        self.calls.append(kw)
        return len(self.calls)


@pytest.fixture
def tracker(monkeypatch):
    _FakeTracker.instances = []
    monkeypatch.setattr("mlb_core.tracking.BetTracker", _FakeTracker)
    for v in ("MLB_DB_URL", "DB_URL", "K_SERVICE", "CLOUD_RUN_JOB"):
        monkeypatch.delenv(v, raising=False)
    return _FakeTracker


def sweep_row(**kw):
    """One row exactly as ev.sweep_props_alerts.outlier_rows builds it for an evening NHL game (UTC commence is the next day)."""
    r = dict(sport="nhl", ts="2026-10-28T20:10:00+00:00", game_key="nhl:2026-10-28:TOR@BOS", event_id="e1",
             commence="2026-10-29T00:00:00+00:00", mins_to_start=230.0, away="TOR", home="BOS",
             book="draftkings", player="Auston Matthews", player_norm="id8479318", player_id=8479318, stat="sog",
             point=3.5, alt=False, side="OVER", american=-115, decimal=1.8696, p_fair=0.58, ev=0.084, n_books=6,
             disp=0.02, all_books=[], flag=True, reason="FLAG")
    r.update(kw)
    return r


def test_ledger_row_uses_et_game_day_and_stores_the_player_id(tracker):
    assert sa.log_sport_alert_bets("nhl", [sweep_row()]) == 1
    kw = tracker.instances[0].calls[0]
    assert kw["game_date"] == "2026-10-28"                       # NOT commence[:10] == 2026-10-29
    import json
    notes = json.loads(kw["notes"])
    assert notes["player_id"] == 8479318 and notes["game_key"] == "nhl:2026-10-28:TOR@BOS" and notes["side"] == "over"
    assert kw["odds"] == -115 and abs(kw["model_prob"] - 0.58) < 1e-9 and kw["away_team"] == "TOR"


def test_ledger_game_day_falls_back_to_commence_in_eastern_time():
    assert sa.alert_game_day({"commence": "2026-10-29T00:00:00+00:00"}) == "2026-10-28"
    assert sa.alert_game_day({"commence": "2026-10-29T00:00:00Z"}) == "2026-10-28"
    assert sa.alert_game_day({"commence": "2026-10-28T23:30:00+00:00"}) == "2026-10-28"
    assert sa.alert_game_day({"game_key": "nfl:2027-01-10:ATL@NO", "commence": "2027-01-11T01:00:00+00:00"}) == "2027-01-10"


def test_ledger_player_id_recovered_from_player_norm_and_teams_from_game_key(tracker):
    sa.log_sport_alert_bets("nfl", [sweep_row(player_id=None, away=None, home=None, game_key="nfl:2026-10-25:KC@BUF")])
    kw = tracker.instances[0].calls[0]
    import json
    assert json.loads(kw["notes"])["player_id"] == "8479318" and (kw["away_team"], kw["home_team"]) == ("KC", "BUF")


def test_ledger_skips_alerts_with_no_price_instead_of_inventing_minus_110(tracker):
    bad = sweep_row(american=None)
    assert sa.log_sport_alert_bets("nhl", [bad, sweep_row()]) == 1
    assert len(tracker.instances[0].calls) == 1 and tracker.instances[0].calls[0]["odds"] == -115


def test_ledger_refuses_ephemeral_sqlite_in_cloud_run(tracker, monkeypatch, caplog):
    monkeypatch.setenv("CLOUD_RUN_JOB", "ev-nfl-props-sweep")
    with caplog.at_level("ERROR", logger="mlb_core.alerts.sport_alerts"):
        assert sa.log_sport_alert_bets("nfl", [sweep_row()]) == 0
    assert tracker.instances == [] and "MLB_DB_URL" in caplog.text
    monkeypatch.setenv("MLB_DB_URL", "postgresql+pg8000://u:p@/db?unix_sock=/cloudsql/x/.s.PGSQL.5432")
    assert sa.log_sport_alert_bets("nfl", [sweep_row()]) == 1


def test_post_sport_alerts_logs_ledger_failures_with_a_traceback(monkeypatch, caplog):
    monkeypatch.setattr(sa, "log_sport_alert_bets", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(sa, "bot_configured", lambda: False)
    monkeypatch.setattr(sa, "webhook_for", lambda *a, **k: None)
    with caplog.at_level("ERROR", logger="mlb_core.alerts.sport_alerts"):
        sa.post_sport_alerts("nhl", sa.gate([row()]))
    rec = [r for r in caplog.records if "NOT recorded" in r.getMessage()]
    assert rec and rec[0].exc_info and "db down" in caplog.text
