"""mlb_core.alerts.discord_routes -- webhook fallback chains, Kambi clone handling, deep links."""

import pandas as pd
import pytest

import mlb.runners.fast_alert_loop as fal
import mlb_core.notify.discord as discord_mod
from ev.props import build_consensus, parse_event_props, scan_book_outliers
from mlb_core.alerts import discord_routes as dr

ALL_VARS = [
    "DISCORD_WEBHOOK_EV_BOOKS", "DISCORD_WEBHOOK_EV_EXCHANGES", "DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_OPS",
    "DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_RECAP", "DISCORD_WEBHOOK_SUMMARY", "DISCORD_WEBHOOK_TWEETS",
    "DISCORD_WEBHOOK_CARD_MLB", "DISCORD_WEBHOOK_CARD_NFL", "DISCORD_WEBHOOK_CARD_NHL", "DISCORD_WEBHOOK_CARD_NBA",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in ALL_VARS:
        monkeypatch.delenv(v, raising=False)


def test_nothing_set_returns_none():
    for kind in ("ev_sportsbooks", "ev_exchanges", "recap", "tweets", "ops"):
        assert dr.webhook_for(kind) is None
    assert dr.webhook_for("card", sport="nfl") is None


@pytest.mark.parametrize("kind,chain", [
    ("ev_sportsbooks", ["DISCORD_WEBHOOK_EV_BOOKS", "DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_OPS"]),
    ("ev_exchanges", ["DISCORD_WEBHOOK_EV_EXCHANGES", "DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_OPS"]),
    ("recap", ["DISCORD_WEBHOOK_RECAP", "DISCORD_WEBHOOK_SUMMARY", "DISCORD_WEBHOOK_URL"]),
    ("tweets", ["DISCORD_WEBHOOK_TWEETS", "DISCORD_WEBHOOK_OPS"]),
    ("ops", ["DISCORD_WEBHOOK_OPS"]),
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
