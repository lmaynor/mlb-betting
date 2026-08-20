"""
tests/test_kalshi_alert_ev.py -- Tests for kalshi_alert.py's 2026-08-20
EV bet tracking: every alert it actually posts also logs into the same
system="EV" bets-table pool fast_alert_loop.py's alerts land in (pooled,
not a separate system, since a quote both pagers independently flag is the
same real-world bet -- see fast_alert_loop's "EV bet tracking" section).

The market -> bet_type mapping itself (_ev_bet_type) is tested in
tests/test_fast_alert_loop_ev.py and graded in tests/test_settlement.py's
TestSettleEv -- this file only covers kalshi_alert's own adapter
(_log_ev_bets), which has a different input row shape (ev_pct/p_true/
cons_impl, not ev/consensus_fair/decimal).
"""
import os

os.environ.pop("MLB_GCS_BUCKET", None)
os.environ.pop("MLB_DB_URL", None)

import pandas as pd
import pytest

import mlb.runners.kalshi_alert as ka
import mlb.runners.fast_alert_loop as fal


def _kalshi_row(**overrides):
    row = {
        "market": "hr_yn", "game_pk": 823385, "game_date": "2026-08-19",
        "away_team": "NYY", "home_team": "BOS", "player_id": 700003,
        "selection": "OVER", "line": 0.5, "book": "fliff", "american": 450,
        "p_true": 0.22, "cons_impl": 0.16, "ev_pct": 0.045, "n_books": 6,
        "soft": True,
    }
    row.update(overrides)
    return row


class TestKalshiLogEvBets:
    def test_logs_each_posted_alert(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fal, "_EV_BET_DB", str(tmp_path / "ev_bets.db"))
        posted = pd.DataFrame([_kalshi_row()])
        names = {700003: "Hunter Goodman"}
        logged = ka._log_ev_bets(posted, names, "2026-08-19")
        assert logged == 1

        from mlb_core.tracking.bet_tracker import BetTracker
        tracker = BetTracker(str(tmp_path / "ev_bets.db"), system="EV")
        df = tracker.all_bets()
        assert len(df) == 1
        row = df.iloc[0]
        assert row["bet_type"] == "HR_fliff"
        assert row["player"] == "Hunter Goodman"
        assert row["model_prob"] == pytest.approx(0.22)
        assert row["market_prob"] == pytest.approx(0.16)
        assert row["edge"] == pytest.approx(0.045)
        assert row["kelly_triggered"] == True  # noqa: E712
        assert row["stake"] == fal._EV_STAKE_UNIT

    def test_empty_posted_logs_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fal, "_EV_BET_DB", str(tmp_path / "ev_bets.db"))
        assert ka._log_ev_bets(pd.DataFrame(), {}, "2026-08-19") == 0

    def test_unresolved_player_name_falls_back_to_matchup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fal, "_EV_BET_DB", str(tmp_path / "ev_bets.db"))
        posted = pd.DataFrame([_kalshi_row(player_id=999999)])  # not in names
        ka._log_ev_bets(posted, {}, "2026-08-19")

        from mlb_core.tracking.bet_tracker import BetTracker
        tracker = BetTracker(str(tmp_path / "ev_bets.db"), system="EV")
        assert tracker.all_bets().iloc[0]["player"] == "NYY @ BOS"

    def test_game_level_market_has_no_player_id_uses_matchup(self, tmp_path, monkeypatch):
        """nrfi_ou/game_ml/f5_ml rows have no real player -- player_id is
        NaN, `player` should be the matchup string like the real NRFI/GAME/
        F5 systems use."""
        monkeypatch.setattr(fal, "_EV_BET_DB", str(tmp_path / "ev_bets.db"))
        posted = pd.DataFrame([_kalshi_row(
            market="game_ml", selection="HOME", line=None, player_id=None,
        )])
        ka._log_ev_bets(posted, {}, "2026-08-19")

        from mlb_core.tracking.bet_tracker import BetTracker
        tracker = BetTracker(str(tmp_path / "ev_bets.db"), system="EV")
        row = tracker.all_bets().iloc[0]
        assert row["bet_type"] == "GAME_HOME_fliff"
        assert row["player"] == "NYY @ BOS"

    def test_unsettleable_market_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fal, "_EV_BET_DB", str(tmp_path / "ev_bets.db"))
        posted = pd.DataFrame([_kalshi_row(market="game_total", line=8.5)])
        assert ka._log_ev_bets(posted, {}, "2026-08-19") == 0

    def test_two_pagers_same_real_bet_dedupe_to_one_row(self, tmp_path, monkeypatch):
        """The whole point of pooling into one system="EV": if
        fast_alert_loop AND kalshi_alert both independently flag the exact
        same (game_pk, player, market/selection/line, book) on the same
        day, it's the same real-world bet and must settle once, not
        twice."""
        monkeypatch.setattr(fal, "_EV_BET_DB", str(tmp_path / "ev_bets.db"))
        shared_row = _kalshi_row(book="draftkings")

        from mlb_core.tracking import BetTracker
        # fast_alert_loop logs it first (same day, identical quote identity).
        fal._log_ev_bets(pd.DataFrame([{
            "market": shared_row["market"], "game_pk": shared_row["game_pk"],
            "game_date": shared_row["game_date"], "away_team": shared_row["away_team"],
            "home_team": shared_row["home_team"], "player_id": shared_row["player_id"],
            "player_name": "Hunter Goodman", "selection": shared_row["selection"],
            "line": shared_row["line"], "book": shared_row["book"],
            "american": shared_row["american"], "decimal": 2.5,
            "consensus_fair": 0.20, "ev": 0.05, "anchored": False, "n_books": 5,
        }]), "2026-08-19")
        # kalshi_alert then finds the identical quote independently.
        logged = ka._log_ev_bets(pd.DataFrame([shared_row]), {700003: "Hunter Goodman"}, "2026-08-19")

        assert logged == 0, "the second pager's identical quote should hit the dedup key, not log again"
        tracker = BetTracker(str(tmp_path / "ev_bets.db"), system="EV")
        assert len(tracker.all_bets()) == 1, (
            "two pagers flagging the identical real-world bet must settle "
            "as ONE row, not two"
        )
