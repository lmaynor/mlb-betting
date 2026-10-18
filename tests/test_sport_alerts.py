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


def test_embed_is_labelled_paper_unvalidated_with_score_and_links():
    e = sa.build_embed("nhl", sa.gate([row()]))
    assert "NHL +EV alerts" in e["title"] and "PAPER / UNVALIDATED" in e["title"]
    assert "Pat One Over 2.5 shots on goal" in e["description"]
    assert "not a recommendation" in e["footer"]["text"]
    assert "beezy.fyi/ev" in e["url"]


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


def test_props_sweep_posts_gated_rows_to_members_and_all_rows_to_admin(monkeypatch):
    import ev.sweep_props_alerts as spa
    from mlb_core import storage
    member, admin = [], []
    monkeypatch.setattr(storage, "exists", lambda k: False)
    monkeypatch.setattr(storage, "write_bytes", lambda *a, **k: None)
    monkeypatch.setattr("mlb_core.alerts.sport_alerts.post_via_bot", lambda *a, **k: False)
    monkeypatch.setattr("mlb_core.alerts.sport_alerts.post_sport_alerts",
                        lambda sport, rows: member.append((sport, [r["player"] for r in rows])) or True)
    monkeypatch.setattr(spa, "post_discord", lambda text, embeds=None: admin.append(text) or True)
    rows = [row(player="Strong", ev=0.14), row(player="Weak", ev=0.03, player_norm="weak")]
    assert spa.post_new(spa.SPORTS["nhl"], rows, "2026-10-17") == 2
    assert member == [("nhl", ["Strong"])]                 # only the gated row reaches members
    assert len(admin) == 1                                   # admin still receives the full batch
