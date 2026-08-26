"""
tests/test_discord_notify_grouping.py -- Tests for mlb_core/notify/discord.py's
2026-08-20 follow-up changes:

  1. post_bets() (the regular per-system #daily-picks pings for HR/NRFI/F5/K/
     OUTS/BATTER_TB/BATTER_HITS/GAME/PITCHER_ER) gained the same sportsbook
     double group-by fast_alert_loop.notify()'s +EV embed already has: one
     field per sportsbook (ordered by that book's best edge), bets within a
     book ordered by edge -- replacing the old one-field-per-bet layout.
  2. post_all_systems_summary() (the #daily-recap embed) now also renders
     system="EV" -- via a LOCAL _EXTRA_RECAP_SYSTEMS list, not an addition to
     mlb_core.registry.CANONICAL_ORDER (see that constant's comment for why:
     monitor_performance.py's suppression-gate loop also walks
     CANONICAL_ORDER, and none of that machinery applies to EV).
"""
import os

os.environ.pop("MLB_GCS_BUCKET", None)
os.environ.pop("MLB_DB_URL", None)

import mlb_core.notify.discord as discord_mod
from mlb_core.notify.discord import (
    _grouped_bet_fields, post_bets, post_all_systems_summary,
    CANONICAL_ORDER,
)


def _bet(**overrides):
    b = {
        "player": "Some Player", "team": "NYY", "away_team": "NYY", "home_team": "BOS",
        "bet_type": "K_OVER_7.5", "side": "OVER", "line": 7.5,
        "model_prob": 0.60, "edge": 0.05, "odds": -110, "stake": 25.0,
        "book": "draftkings", "paper": True,
    }
    b.update(overrides)
    return b


def _post_capture(monkeypatch):
    captured = {}

    def _fake_post(url, payload):
        captured["payload"] = payload
        return True

    monkeypatch.setattr(discord_mod, "_post", _fake_post)
    return captured


class TestGroupedBetFields:
    def test_one_field_per_book(self):
        bets = [
            _bet(player="A", book="draftkings", edge=0.05),
            _bet(player="B", book="draftkings", edge=0.08),
            _bet(player="C", book="fanduel", edge=0.20),
        ]
        fields = _grouped_bet_fields(bets, "K")
        assert len(fields) == 2

    def test_groups_ordered_by_best_edge_descending(self):
        bets = [
            _bet(player="A", book="draftkings", edge=0.05),
            _bet(player="B", book="fanduel", edge=0.20),
        ]
        fields = _grouped_bet_fields(bets, "K")
        assert "FanDuel" in fields[0]["name"]
        assert "DraftKings" in fields[1]["name"]

    def test_within_group_sorted_by_edge_descending(self):
        bets = [
            _bet(player="Low Edge", book="draftkings", edge=0.04),
            _bet(player="High Edge", book="draftkings", edge=0.15),
        ]
        fields = _grouped_bet_fields(bets, "K")
        assert len(fields) == 1
        value = fields[0]["value"]
        assert value.index("High Edge") < value.index("Low Edge")

    def test_book_count_in_field_name(self):
        bets = [
            _bet(player="A", book="draftkings"),
            _bet(player="B", book="draftkings"),
        ]
        fields = _grouped_bet_fields(bets, "K")
        assert "2 bets" in fields[0]["name"]

    def test_missing_book_groups_as_unknown_not_dropped(self):
        bets = [_bet(player="A", book=None, bookmaker=None)]
        fields = _grouped_bet_fields(bets, "K")
        assert len(fields) == 1
        assert "Unknown" in fields[0]["name"]

    def test_bookmaker_key_used_as_fallback(self):
        bets = [_bet(player="A", book=None, bookmaker="hardrock")]
        fields = _grouped_bet_fields(bets, "K")
        assert "Hard Rock Bet" in fields[0]["name"]


class TestPostBetsGrouped:
    def test_embed_uses_grouped_fields(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
        captured = _post_capture(monkeypatch)
        bets = [
            _bet(player="A", book="draftkings", edge=0.05),
            _bet(player="B", book="fanduel", edge=0.20),
        ]
        post_bets(bets, system="K", run_date="2026-08-20")
        fields = captured["payload"]["embeds"][0]["fields"]
        assert len(fields) == 2
        assert "FanDuel" in fields[0]["name"]

    def test_no_bets_still_posts_plain_embed(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
        captured = _post_capture(monkeypatch)
        post_bets([], system="K", run_date="2026-08-20")
        embed = captured["payload"]["embeds"][0]
        assert embed["description"] == "No qualifying bets today."


class TestRecapIncludesEv:
    def test_ev_not_in_canonical_order(self):
        """The registry itself must stay untouched -- see the comment on
        _EXTRA_RECAP_SYSTEMS for why (monitor_performance.py's suppression
        gate also walks CANONICAL_ORDER)."""
        assert "EV" not in CANONICAL_ORDER

    def test_ev_rendered_in_recap_when_present(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_SUMMARY", "https://discord.test/webhook")
        captured = _post_capture(monkeypatch)
        stats = {
            "EV": {"bets": 10, "wins": 6, "hit_rate": 0.6, "pnl": 45.0,
                   "roi": 4.5, "avg_edge": 0.06, "pending": 2},
        }
        post_all_systems_summary(stats, run_date="2026-08-20")
        fields = captured["payload"]["embeds"][0]["fields"]
        names = [f["name"] for f in fields]
        assert any("EV" in n for n in names)

    def test_ev_field_uses_its_own_icon_not_default(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_SUMMARY", "https://discord.test/webhook")
        captured = _post_capture(monkeypatch)
        post_all_systems_summary({"EV": {"bets": 1, "wins": 1, "hit_rate": 1.0,
                                         "pnl": 5.0, "roi": 5.0, "avg_edge": 0.05}},
                                 run_date="2026-08-20")
        fields = captured["payload"]["embeds"][0]["fields"]
        ev_field = next(f for f in fields if f["name"].endswith("EV"))
        assert "⚪" not in ev_field["name"]  # not the generic unknown-system fallback

    def test_ev_no_settled_bets_shows_placeholder_not_missing(self, monkeypatch):
        """EV present in the systems list even with nothing settled yet --
        matches every other system's "_No settled bets yet_" placeholder."""
        monkeypatch.setenv("DISCORD_WEBHOOK_SUMMARY", "https://discord.test/webhook")
        captured = _post_capture(monkeypatch)
        post_all_systems_summary({}, run_date="2026-08-20")
        fields = captured["payload"]["embeds"][0]["fields"]
        ev_field = next(f for f in fields if f["name"].endswith("EV"))
        assert ev_field["value"] == "_No settled bets yet_"
