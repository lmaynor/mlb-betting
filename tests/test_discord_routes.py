"""mlb_core.alerts.discord_routes -- webhook fallback chains, Kambi clone handling, deep links."""

import pandas as pd
import pytest

import mlb.runners.fast_alert_loop as fal
import mlb_core.notify.discord as discord_mod
from ev.props import build_consensus, parse_event_props, scan_book_outliers
from mlb_core.alerts import discord_routes as dr

ALL_VARS = [
    "DISCORD_WEBHOOK_EV_BOOKS", "DISCORD_WEBHOOK_EV_EXCHANGES", "DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_OPS",
    "DISCORD_WEBHOOK_PROPS_NFL",
    "DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_RECAP", "DISCORD_WEBHOOK_SUMMARY", "DISCORD_WEBHOOK_TWEETS",
    "DISCORD_WEBHOOK_CARD_MLB", "DISCORD_WEBHOOK_CARD_NFL", "DISCORD_WEBHOOK_CARD_NHL", "DISCORD_WEBHOOK_CARD_NBA",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in ALL_VARS:
        monkeypatch.delenv(v, raising=False)


def test_nothing_set_returns_none():
    for kind in ("ev_sportsbooks", "ev_exchanges", "recap", "tweets", "ops", "props_alerts"):
        assert dr.webhook_for(kind) is None
    assert dr.webhook_for("card", sport="nfl") is None


@pytest.mark.parametrize("kind,chain", [
    ("ev_sportsbooks", ["DISCORD_WEBHOOK_EV_BOOKS", "DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_OPS"]),
    ("ev_exchanges", ["DISCORD_WEBHOOK_EV_EXCHANGES", "DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_OPS"]),
    ("recap", ["DISCORD_WEBHOOK_RECAP", "DISCORD_WEBHOOK_SUMMARY", "DISCORD_WEBHOOK_URL"]),
    ("tweets", ["DISCORD_WEBHOOK_TWEETS", "DISCORD_WEBHOOK_OPS"]),
    ("ops", ["DISCORD_WEBHOOK_OPS"]),
    ("props_alerts", ["DISCORD_WEBHOOK_ALERTS"]),
])
def test_chain_order(monkeypatch, kind, chain):
    # setting each link from the LAST to the first must always surface the earliest one set
    for var in reversed(chain):
        monkeypatch.setenv(var, f"https://hook.test/{var}")
        assert dr.webhook_for(kind) == f"https://hook.test/{var}"


@pytest.mark.parametrize("sport", ["mlb", "nfl", "nhl", "nba"])
def test_card_chain_per_sport(monkeypatch, sport):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hook.test/picks")
    assert dr.webhook_for("card", sport=sport) == "https://hook.test/picks"
    monkeypatch.setenv(f"DISCORD_WEBHOOK_CARD_{sport.upper()}", f"https://hook.test/{sport}")
    assert dr.webhook_for("card", sport=sport) == f"https://hook.test/{sport}"
    other = "nfl" if sport != "nfl" else "nba"
    assert dr.webhook_for("card", sport=other) == "https://hook.test/picks"   # no cross-sport leak


def test_card_defaults_to_mlb(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_CARD_MLB", "https://hook.test/mlb")
    assert dr.webhook_for("card") == "https://hook.test/mlb"


def test_ev_alerts_never_fall_back_to_daily_picks(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hook.test/daily-picks")
    assert dr.webhook_for("ev_sportsbooks") is None
    assert dr.webhook_for("ev_exchanges") is None


def test_blank_values_count_as_unset(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_EV_BOOKS", "   ")
    monkeypatch.setenv("DISCORD_WEBHOOK_ALERTS", "https://hook.test/alerts")
    assert dr.webhook_for("ev_sportsbooks") == "https://hook.test/alerts"


def test_unknown_kind_and_sport_raise():
    with pytest.raises(ValueError):
        dr.webhook_for("nope")
    with pytest.raises(ValueError):
        dr.webhook_for("card", sport="cricket")


# ---------------------------------------------------------------------------------------- books
def test_exchange_books():
    assert dr.is_exchange_book("Novig") and dr.is_exchange_book("kalshi") and dr.is_exchange_book("prophetx")
    assert not dr.is_exchange_book("draftkings") and not dr.is_exchange_book(None)


def test_canonical_book_folds_sugarhouse():
    assert dr.canonical_book("SugarHouse") == "betrivers"
    assert dr.canonical_book("draftkings") == "draftkings"
    assert dr.canonical_book(None) is None


def _rows(*books, **over):
    base = dict(market="k_ou", game_pk=1, player_id=7, line=5.5, selection="OVER", american=110, ev=0.05,
                consensus_fair=0.5, n_books=5, anchored=False, away_team="NYY", home_team="BOS", game_date="2026-10-15")
    base.update(over)
    return pd.DataFrame([{**base, "book": b} for b in books])


KEYS = ["market", "game_pk", "player_id", "line", "selection"]


def test_drop_kambi_clone_when_twin_present():
    out = dr.drop_kambi_clones(_rows("betrivers", "sugarhouse", "draftkings"), KEYS)
    assert sorted(out["book"]) == ["betrivers", "draftkings"]


def test_keep_clone_without_twin():
    out = dr.drop_kambi_clones(_rows("sugarhouse", "draftkings"), KEYS)
    assert sorted(out["book"]) == ["draftkings", "sugarhouse"]


def test_clone_kept_when_twin_quotes_a_different_proposition():
    df = pd.concat([_rows("betrivers", line=4.5), _rows("sugarhouse", line=5.5)], ignore_index=True)
    assert len(dr.drop_kambi_clones(df, KEYS)) == 2


def test_drop_kambi_handles_nan_player_and_empty():
    df = _rows("betrivers", "sugarhouse", player_id=None)
    assert list(dr.drop_kambi_clones(df, KEYS)["book"]) == ["betrivers"]
    assert len(dr.drop_kambi_clones(df.iloc[0:0], KEYS)) == 0


# ---------------------------------------------------------------------------------------- deep links
def test_deep_links_without_description():
    e = dr.with_deep_links({"title": "t"})
    assert e["url"] == "https://beezy.fyi/ev"
    assert e["description"] == dr.ACTION_LINKS
    assert "[Open +EV Terminal](https://beezy.fyi/ev)" in e["description"]
    assert "[L10 & Multi-Book Cockpit](https://beezy.fyi/edge)" in e["description"]


def test_deep_links_append_and_idempotent():
    e = dr.with_deep_links({"description": "Evidence only."})
    assert e["description"].startswith("Evidence only.\n\n")
    assert e["description"].endswith(dr.ACTION_LINKS)
    again = dr.with_deep_links(dict(e))
    assert again["description"] == e["description"]


def test_action_links_use_middle_dot_ascii_source():
    assert "\N{MIDDLE DOT}" in dr.ACTION_LINKS
    src = open(dr.__file__, encoding="utf-8").read()
    assert src.isascii()


# ---------------------------------------------------------------------------------------- fast_alert_loop notify
def _alert(book, **kw):
    base = dict(market="k_ou", game_pk=1, player_id=1, player_name="P One", line=5.5, selection="OVER",
                book=book, american=110, decimal=2.1, consensus_fair=0.5, anchored=False, ev=0.1,
                edge_vs_consensus=0.05, n_books=5, book_spread=0.02, away_team="NYY", home_team="BOS",
                game_date="2026-10-15")
    base.update(kw)
    return base


def test_notify_splits_books_and_exchanges(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_EV_BOOKS", "https://hook.test/books")
    monkeypatch.setenv("DISCORD_WEBHOOK_EV_EXCHANGES", "https://hook.test/exch")
    posts = []
    monkeypatch.setattr(discord_mod, "_post", lambda url, payload: posts.append((url, payload)) or True)
    fal.notify(pd.DataFrame([_alert("draftkings"), _alert("novig", player_id=2, ev=0.2)]), set(), "2026-10-15")
    urls = {u: p["embeds"][0] for u, p in posts}
    assert set(urls) == {"https://hook.test/books", "https://hook.test/exch"}
    for emb in urls.values():
        assert emb["url"] == "https://beezy.fyi/ev"
        assert emb["description"] == dr.ACTION_LINKS
    assert "DraftKings" in str(urls["https://hook.test/books"]) and "Novig" not in str(urls["https://hook.test/books"])
    assert "Novig" in str(urls["https://hook.test/exch"])


def test_notify_does_not_post_to_daily_picks(monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hook.test/daily-picks")
    posts = []
    monkeypatch.setattr(discord_mod, "_post", lambda url, payload: posts.append(url) or True)
    fal.notify(pd.DataFrame([_alert("draftkings")]), set(), "2026-10-15")
    assert posts == []                                    # printed instead
    assert "DraftKings" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------- ev/props Kambi
def _event(book_keys):
    def mk(price_over, price_under):
        return {"key": "player_points", "outcomes": [
            {"name": "Over", "description": "Jo Doe", "price": price_over, "point": 20.5},
            {"name": "Under", "description": "Jo Doe", "price": price_under, "point": 20.5}]}
    return {"bookmakers": [{"key": k, "markets": [mk(-110, -110)]} for k in book_keys]}


def test_props_parse_counts_kambi_pair_once():
    quotes, _ = parse_event_props(_event(["betrivers", "sugarhouse", "fanduel"]), sport="nba")
    assert sorted(q.book for q in quotes) == ["betrivers", "fanduel"]


def test_props_parse_folds_lone_clone_into_betrivers():
    quotes, _ = parse_event_props(_event(["sugarhouse", "fanduel"]), sport="nba")
    assert sorted(q.book for q in quotes) == ["betrivers", "fanduel"]


def test_props_consensus_size_ignores_clone():
    quotes, _ = parse_event_props(_event(["betrivers", "sugarhouse", "fanduel", "draftkings"]), sport="nba")
    (cons,) = build_consensus(quotes).values()
    assert cons.n_books == 3 and "sugarhouse" not in cons.books


def test_props_outlier_not_double_posted_for_clone():
    ev = {"bookmakers": [
        {"key": b, "markets": [{"key": "player_points", "outcomes": [
            {"name": "Over", "description": "Jo Doe", "price": o, "point": 20.5},
            {"name": "Under", "description": "Jo Doe", "price": u, "point": 20.5}]}]}
        for b, o, u in [("betrivers", 130, -150), ("sugarhouse", 130, -150), ("fanduel", -110, -110),
                        ("draftkings", -110, -110), ("betmgm", -112, -108), ("caesars", -108, -112)]]}
    quotes, _ = parse_event_props(ev, sport="nba")
    hits = [r for r in scan_book_outliers(quotes, min_books=4) if r["book"] == "betrivers"]
    assert len(hits) == 1


# ---------------------------------------------------------------------------------------- bot-authored messages
def test_bot_channel_keys():
    assert dr.bot_channel_key("ev_sportsbooks") == "ev_sportsbooks"
    assert dr.bot_channel_key("card", "nfl") == "nfl_card"
    assert dr.bot_channel_key("card") == "mlb_card"
    assert dr.bot_channel_key("recap") == "daily_recap"
    with pytest.raises(ValueError):
        dr.bot_channel_key("card", "cricket")


def test_slip_text_is_plain_capped_and_drops_whole_lines():
    lines = ["Jo Doe Over 24.5 Points @ FanDuel +120", "  ", "Max   Celtic  Under 18.5 Points @ BetMGM -110"]
    assert dr.slip_text(lines) == "Jo Doe Over 24.5 Points @ FanDuel +120\nMax Celtic Under 18.5 Points @ BetMGM -110"
    out = dr.slip_text(["x" * 900, "y" * 900, "z" * 900], limit=2000)
    assert out.count("\n") == 1 and len(out) <= 2000


def test_post_via_bot_off_without_config_and_never_raises(monkeypatch):
    monkeypatch.delenv("BEEZY_BOT_URL", raising=False)
    monkeypatch.delenv("BEEZY_BOT_SECRET", raising=False)
    assert dr.bot_configured() is False and dr.post_via_bot("ev_sportsbooks", "x", None) is False
    monkeypatch.setenv("BEEZY_BOT_URL", "https://bot.test/")
    monkeypatch.setenv("BEEZY_BOT_SECRET", "s3")
    import requests
    seen = {}

    def ok(url, json=None, timeout=None, headers=None):
        seen.update(url=url, json=json, headers=headers)
        return type("R", (), {"status_code": 200})()
    monkeypatch.setattr(requests, "post", ok)
    assert dr.post_via_bot("ev_exchanges", "slip", [{"title": "t"}]) is True
    assert seen["url"] == "https://bot.test/internal/post-message"
    assert seen["json"] == {"channel": "ev_exchanges", "content": "slip", "embeds": [{"title": "t"}]}
    assert seen["headers"] == {"X-Beezy-Secret": "s3"}
    monkeypatch.setattr(requests, "post", lambda *a, **k: type("R", (), {"status_code": 404})())
    assert dr.post_via_bot("ev_exchanges", "slip", None) is False

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "post", boom)
    assert dr.post_via_bot("ev_exchanges", "slip", None) is False


def test_notify_posts_one_bot_message_and_thread_per_alert_then_falls_back(monkeypatch):
    monkeypatch.setenv("BEEZY_BOT_URL", "https://bot.test")
    monkeypatch.setenv("BEEZY_BOT_SECRET", "s3")
    monkeypatch.setenv("DISCORD_WEBHOOK_EV_BOOKS", "https://hook.test/books")
    hooks, bots = [], []
    monkeypatch.setattr(discord_mod, "_post", lambda url, payload: hooks.append(url) or True)
    monkeypatch.setattr(fal, "post_via_bot", lambda kind, content, embeds, **k: bots.append((kind, content, embeds, k)) or True)
    fal.notify(pd.DataFrame([_alert("draftkings"), _alert("fanduel", player_id=2, player_name="Two", ev=0.2)]), set(), "2026-10-15")
    assert hooks == [] and len(bots) == 2                      # one message per alert, best EV first
    kind, content, embeds, kw = bots[0]
    assert kind == "ev_sportsbooks" and content.startswith("Two ") and "@ FanDuel" in content and "**" not in content
    assert kw["thread"] == {"name": content, "content": content}
    assert embeds[0]["url"] == "https://beezy.fyi/ev" and "EV **+20.0%**" in embeds[0]["description"]
    assert bots[1][1].startswith("P One ") and "@ DraftKings" in bots[1][1]
    # bot refuses everything -> the grouped webhook embed is the fallback
    bots.clear()
    monkeypatch.setattr(fal, "post_via_bot", lambda *a, **k: False)
    fal.notify(pd.DataFrame([_alert("draftkings")]), set(), "2026-10-15")
    assert hooks == ["https://hook.test/books"]


def test_post_via_bot_sends_thread_payload(monkeypatch):
    import requests
    monkeypatch.setenv("BEEZY_BOT_URL", "https://bot.test")
    monkeypatch.setenv("BEEZY_BOT_SECRET", "s3")
    seen = {}
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: seen.update(json=json) or type("R", (), {"status_code": 200})())
    assert dr.post_via_bot("ev_sportsbooks", "slip", [{"title": "t"}], thread={"name": "n" * 150, "content": "c"}) is True
    assert seen["json"]["thread"] == {"name": "n" * 100, "content": "c"}
    dr.post_via_bot("ev_sportsbooks", "slip", None)
    assert "thread" not in seen["json"]


def test_kalshi_alert_slip_lines():
    import mlb.runners.kalshi_alert as ka
    df = pd.DataFrame([
        dict(market="k_ou", game_pk=1, player_id=7, line=5.5, selection="OVER", book="draftkings", american=110, ev_pct=0.05,
             away_team="NYY", home_team="BOS"),
        dict(market="k_ou", game_pk=2, player_id=8, line=6.5, selection="UNDER", book="fanduel", american=-105, ev_pct=0.09,
             away_team="NYY", home_team="BOS"),
    ])
    lines = ka._slip_lines(df, {7: "Pat One", 8: "Sam Two"})
    assert lines[0] == "Sam Two Under 6.5 Strikeouts @ FanDuel -105"
    assert lines[1] == "Pat One Over 5.5 Strikeouts @ DraftKings +110"


def test_props_alerts_never_reach_ops_or_a_member_channel(monkeypatch):
    # the unvalidated NFL/NHL/NBA props feed: #ops-alerts is for failures, and the member channels are for validated alerts
    for var in ("DISCORD_WEBHOOK_OPS", "DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_EV_BOOKS", "DISCORD_WEBHOOK_EV_EXCHANGES"):
        monkeypatch.setenv(var, f"https://hook.test/{var}")
    assert dr.webhook_for("props_alerts") is None


def test_props_alerts_use_the_sport_channel_then_the_shared_admin_hook(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_ALERTS", "https://hook.test/alerts")
    assert dr.webhook_for("props_alerts") == "https://hook.test/alerts"
    assert dr.webhook_for("props_alerts", sport="nfl") == "https://hook.test/alerts"
    monkeypatch.setenv("DISCORD_WEBHOOK_PROPS_NFL", "https://hook.test/nfl")
    assert dr.webhook_for("props_alerts", sport="nfl") == "https://hook.test/nfl"
    assert dr.webhook_for("props_alerts", sport="NFL") == "https://hook.test/nfl"
    assert dr.webhook_for("props_alerts", sport="nhl") == "https://hook.test/alerts"     # no cross-sport leak
    assert dr.webhook_for("props_alerts") == "https://hook.test/alerts"
    with pytest.raises(ValueError):
        dr.webhook_for("props_alerts", sport="cricket")
