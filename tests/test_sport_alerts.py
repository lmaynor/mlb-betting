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
