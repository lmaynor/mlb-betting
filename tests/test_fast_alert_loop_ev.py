"""
tests/test_fast_alert_loop_ev.py -- Tests for fast_alert_loop.py's 2026-08-20
changes:
  1. EV bet tracking: every posted +EV alert also logs into the `bets` table
     (system="EV") so profitability can be queried like any model system.
     Covers _ev_bet_type() (market -> settle_bets-compatible bet_type) and
     _log_ev_bets() (the actual BetTracker writes).
  2. The Discord embed's double group-by (sportsbook, then EV) replacing the
     old one-field-per-alert layout, and the removal of the separate
     "Lineup events" field.

See tests/test_settlement.py's TestSettleEv for the settlement side (the
bet_type convention this file produces is graded there).
"""
import os

os.environ.pop("MLB_GCS_BUCKET", None)
os.environ.pop("MLB_DB_URL", None)

import pandas as pd
import pytest

import mlb.runners.fast_alert_loop as fal
from mlb_core.notify.discord import book_display, market_label, ev_alert_emoji, TEAM_NICKNAME


# ── _ev_bet_type ──────────────────────────────────────────────────────────────

class TestEvBetType:
    def test_hr(self):
        assert fal._ev_bet_type("hr_yn", "OVER", 0.5, "draftkings") == "HR_draftkings"

    def test_k(self):
        assert fal._ev_bet_type("k_ou", "OVER", 7.5, "FanDuel") == "K_OVER_7.5_fanduel"

    def test_outs(self):
        assert fal._ev_bet_type("outs_ou", "UNDER", 14.5, "hardrock") == "OUTS_UNDER_14.5_hardrock"

    def test_batter_tb(self):
        assert fal._ev_bet_type("btb_ou", "OVER", 1.5, "betmgm") == "BATTER_TB_OVER_1.5_betmgm"

    def test_batter_hits(self):
        assert fal._ev_bet_type("bhits_ou", "OVER", 0.5, "caesars") == "BATTER_HITS_OVER_0.5_caesars"

    def test_pitcher_er(self):
        assert fal._ev_bet_type("per_ou", "UNDER", 2.5, "novig") == "PITCHER_ER_UNDER_2.5_novig"

    def test_unrecognised_market_returns_none(self):
        # e.g. game_ml/nrfi_ou -- not in _EV_MARKET_PREFIX -- do not log the
        # unsettleable rather than guess.
        assert fal._ev_bet_type("game_ml", "HOME", None, "draftkings") is None

    def test_missing_line_returns_none(self):
        assert fal._ev_bet_type("k_ou", "OVER", float("nan"), "draftkings") is None

    def test_missing_book_falls_back_to_unknown_tag(self):
        assert fal._ev_bet_type("k_ou", "OVER", 7.5, None) == "K_OVER_7.5_unknown"


# ── _log_ev_bets ──────────────────────────────────────────────────────────────

def _alert_row(**overrides):
    row = {
        "market": "k_ou", "game_pk": 12345, "game_date": "2026-08-19",
        "away_team": "NYY", "home_team": "BOS", "player_id": 700001,
        "player_name": "Some Pitcher", "selection": "OVER", "line": 7.5,
        "book": "draftkings", "american": 120, "decimal": 2.20,
        "consensus_fair": 0.42, "ev": 0.05, "anchored": False, "n_books": 5,
    }
    row.update(overrides)
    return row


class TestLogEvBets:
    def test_logs_each_posted_alert(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fal, "_EV_BET_DB", str(tmp_path / "ev_bets.db"))
        posted = pd.DataFrame([
            _alert_row(),
            _alert_row(game_pk=99999, player_id=700002, player_name="Other Pitcher"),
        ])
        logged = fal._log_ev_bets(posted, "2026-08-19")
        assert logged == 2

        from mlb_core.tracking.bet_tracker import BetTracker
        tracker = BetTracker(str(tmp_path / "ev_bets.db"), system="EV")
        df = tracker.all_bets()
        assert len(df) == 2
        assert set(df["bet_type"]) == {"K_OVER_7.5_draftkings"}
        assert (df["kelly_triggered"] == True).all()  # noqa: E712
        assert (df["stake"] == fal._EV_STAKE_UNIT).all()
        assert (df["odds"] == 120).all()

    def test_empty_posted_logs_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fal, "_EV_BET_DB", str(tmp_path / "ev_bets.db"))
        assert fal._log_ev_bets(pd.DataFrame(), "2026-08-19") == 0

    def test_two_books_same_prop_both_logged_not_deduped(self, tmp_path, monkeypatch):
        """Two different books flagging the exact same player/line must NOT
        collide on BetTracker's (system, game_date, game_pk, player,
        bet_type) dedup key -- that's the whole reason the book gets
        suffixed onto bet_type instead of left off."""
        monkeypatch.setattr(fal, "_EV_BET_DB", str(tmp_path / "ev_bets.db"))
        posted = pd.DataFrame([
            _alert_row(book="draftkings"),
            _alert_row(book="fanduel"),
        ])
        logged = fal._log_ev_bets(posted, "2026-08-19")
        assert logged == 2

    def test_unsettleable_market_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fal, "_EV_BET_DB", str(tmp_path / "ev_bets.db"))
        posted = pd.DataFrame([_alert_row(market="game_ml", line=None)])
        assert fal._log_ev_bets(posted, "2026-08-19") == 0

    def test_no_player_name_falls_back_to_matchup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fal, "_EV_BET_DB", str(tmp_path / "ev_bets.db"))
        posted = pd.DataFrame([_alert_row(player_name=None, market="hr_yn", line=0.5)])
        fal._log_ev_bets(posted, "2026-08-19")

        from mlb_core.tracking.bet_tracker import BetTracker
        tracker = BetTracker(str(tmp_path / "ev_bets.db"), system="EV")
        df = tracker.all_bets()
        assert df.iloc[0]["player"] == "NYY @ BOS"


# ── Discord embed: double group-by + Lineup events removal ──────────────────

def _fal_alert(**overrides):
    row = {
        "market": "k_ou", "game_pk": 1, "game_date": "2026-08-19",
        "away_team": "NYY", "home_team": "BOS", "player_id": 1,
        "player_name": "Player A", "selection": "OVER", "line": 7.5,
        "book": "draftkings", "american": 120, "consensus_fair": 0.42,
        "ev": 0.05, "anchored": False, "n_books": 5,
    }
    row.update(overrides)
    return row


class TestGroupedFields:
    def _fields(self, rows, hot=frozenset()):
        df = pd.DataFrame(rows)
        return fal._grouped_fields(df, hot, "2026-08-19", "pinnacle",
                                   book_display, market_label, ev_alert_emoji, TEAM_NICKNAME)

    def test_one_field_per_book(self):
        rows = [
            _fal_alert(book="draftkings", player_id=1, player_name="A", ev=0.05),
            _fal_alert(book="draftkings", player_id=2, player_name="B", ev=0.08),
            _fal_alert(book="fanduel", player_id=3, player_name="C", ev=0.20),
        ]
        fields = self._fields(rows)
        assert len(fields) == 2  # grouped, not one field per alert

    def test_groups_ordered_by_best_ev_descending(self):
        rows = [
            _fal_alert(book="draftkings", player_id=1, player_name="A", ev=0.05),
            _fal_alert(book="fanduel", player_id=2, player_name="B", ev=0.20),
        ]
        fields = self._fields(rows)
        # FanDuel's alert (EV 20%) beats DraftKings's (EV 5%) -- its field comes first.
        assert "FanDuel" in fields[0]["name"]
        assert "DraftKings" in fields[1]["name"]

    def test_within_group_sorted_by_ev_descending(self):
        rows = [
            _fal_alert(book="draftkings", player_id=1, player_name="Low EV", ev=0.04),
            _fal_alert(book="draftkings", player_id=2, player_name="High EV", ev=0.15),
        ]
        fields = self._fields(rows)
        assert len(fields) == 1
        value = fields[0]["value"]
        assert value.index("High EV") < value.index("Low EV")

    def test_book_count_in_field_name(self):
        rows = [
            _fal_alert(book="draftkings", player_id=1, player_name="A"),
            _fal_alert(book="draftkings", player_id=2, player_name="B"),
        ]
        fields = self._fields(rows)
        assert "2 alerts" in fields[0]["name"]

    def test_no_lineup_events_field(self):
        """2026-08-20: the separate raw-game_pk 'Lineup events' summary field
        was removed as unnecessary. notify() no longer even accepts a
        `notes` argument."""
        import inspect
        sig = inspect.signature(fal.notify)
        assert "notes" not in sig.parameters

    def test_notify_no_webhook_prints_grouped_not_raw_alert_list(self, monkeypatch, capsys):
        monkeypatch.delenv("DISCORD_WEBHOOK_ALERTS", raising=False)
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        rows = [_fal_alert(book="draftkings"), _fal_alert(book="fanduel", player_id=2, ev=0.30)]
        fal.notify(pd.DataFrame(rows), set(), today_str="2026-08-19")
        out = capsys.readouterr().out
        assert "Lineup events" not in out
        assert "DraftKings" in out and "FanDuel" in out
