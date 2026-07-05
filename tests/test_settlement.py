"""
tests/test_settlement.py — Unit tests for settle_bets grading logic.

Covers every bet_type/result combination. Uses mock game_cache fixtures
so no MLB API calls are needed. These are the most financially consequential
code paths in the entire system — grading bugs discovered post-settlement
corrupt the P&L record.

Run: pytest tests/test_settlement.py -v
"""
import pandas as pd
import pytest

from mlb.runners.settle_bets import (
    _calc_profit,
    _settle_nrfi,
    _settle_f5,
    _settle_hr,
    _settle_k,
    _settle_batter_props,
    _settle_pitcher_er,
    _void_stale_nonfinal_bets,
)


# ── _calc_profit ─────────────────────────────────────────────────────────────

class TestCalcProfit:
    def test_win_positive_odds(self):
        assert _calc_profit(10.0, 200, "win") == 20.0

    def test_win_negative_odds(self):
        # -110: 10 * 100/110 ≈ 9.09
        assert abs(_calc_profit(10.0, -110, "win") - 9.09) < 0.01

    def test_loss(self):
        assert _calc_profit(10.0, -110, "loss") == -10.0

    def test_push_returns_zero(self):
        assert _calc_profit(10.0, -110, "push") == 0.0

    def test_void_returns_zero(self):
        assert _calc_profit(10.0, 200, "void") == 0.0


# ── Shared fixture helpers ────────────────────────────────────────────────────

def _make_pending(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "id": 1, "game_pk": 100, "bet_type": "NRFI",
        "player": None, "stake": 10.0, "odds": -115,
    }
    records = [{**defaults, **r} for r in rows]
    return pd.DataFrame(records)


def _nrfi_game(away_r: int, home_r: int) -> dict:
    return {
        "innings": [{"away_runs": away_r, "home_runs": home_r}],
        "status": "Final",
    }


def _f5_game(per_inning: list[tuple]) -> dict:
    """per_inning: list of (away_runs, home_runs) for innings 1-N."""
    return {
        "innings": [{"away_runs": a, "home_runs": h} for a, h in per_inning],
        "status": "Final",
    }


def _boxscore(batters: dict = None, pitchers: dict = None) -> dict:
    return {
        "innings": [{"away_runs": 1, "home_runs": 0}] * 9,
        "batters":  batters  or {},
        "pitchers": pitchers or {},
        "status": "Final",
    }


# ── NRFI ─────────────────────────────────────────────────────────────────────

class TestSettleNrfi:
    def _settle(self, bet_type, away_r, home_r):
        pending = _make_pending([{"id": 1, "bet_type": bet_type}])
        cache   = {100: _nrfi_game(away_r, home_r)}
        return _settle_nrfi(pending, cache)

    def test_nrfi_win(self):
        results = self._settle("NRFI", 0, 0)
        assert len(results) == 1
        assert results[0]["result"] == "win"

    def test_nrfi_loss_away_scores(self):
        assert self._settle("NRFI", 1, 0)[0]["result"] == "loss"

    def test_nrfi_loss_home_scores(self):
        assert self._settle("NRFI", 0, 1)[0]["result"] == "loss"

    def test_nrfi_loss_both_score(self):
        assert self._settle("NRFI", 1, 1)[0]["result"] == "loss"

    def test_yrfi_win(self):
        assert self._settle("YRFI", 1, 0)[0]["result"] == "win"

    def test_yrfi_loss(self):
        assert self._settle("YRFI", 0, 0)[0]["result"] == "loss"

    def test_1i_away_win(self):
        assert self._settle("1I_AWAY", 2, 1)[0]["result"] == "win"

    def test_1i_away_loss_tied(self):
        assert self._settle("1I_AWAY", 1, 1)[0]["result"] == "loss"

    def test_1i_home_win(self):
        assert self._settle("1I_HOME", 1, 2)[0]["result"] == "win"

    def test_1i_draw_win_scoreless(self):
        assert self._settle("1I_DRAW", 0, 0)[0]["result"] == "win"

    def test_1i_draw_win_both_score_tie(self):
        assert self._settle("1I_DRAW", 1, 1)[0]["result"] == "win"

    def test_1i_draw_loss(self):
        assert self._settle("1I_DRAW", 1, 0)[0]["result"] == "loss"

    def test_game_not_final_skipped(self):
        pending = _make_pending([{"id": 1, "bet_type": "NRFI"}])
        results = _settle_nrfi(pending, {})  # game_pk 100 not in cache
        assert results == []

    def test_no_innings_skipped(self):
        pending = _make_pending([{"id": 1, "bet_type": "NRFI"}])
        cache   = {100: {"innings": [], "status": "Final"}}
        results = _settle_nrfi(pending, cache)
        assert results == []


# ── F5 ───────────────────────────────────────────────────────────────────────

class TestSettleF5:
    def _settle(self, bet_type, innings):
        pending = _make_pending([{"id": 1, "bet_type": bet_type}])
        cache   = {100: _f5_game(innings)}
        return _settle_f5(pending, cache)

    def test_home_win(self):
        # Away 1, Home 3 over 5 innings
        innings = [(0,1),(0,1),(0,0),(0,1),(1,0)]
        result  = self._settle("HOME", innings)
        assert result[0]["result"] == "win"

    def test_home_loss(self):
        innings = [(1,0),(0,0),(0,0),(0,0),(0,0)]
        assert self._settle("HOME", innings)[0]["result"] == "loss"

    def test_away_win(self):
        innings = [(2,0),(0,0),(0,0),(0,0),(0,0)]
        assert self._settle("AWAY", innings)[0]["result"] == "win"

    def test_push_tie(self):
        # 1-1 after 5 innings
        innings = [(1,0),(0,0),(0,0),(0,0),(0,1)]
        results = self._settle("HOME", innings)
        assert results[0]["result"] == "push"
        assert results[0]["profit"] == 0.0

    def test_fewer_than_5_innings_skipped(self):
        innings = [(1,0),(0,1),(0,0),(0,0)]  # only 4
        results = self._settle("HOME", innings)
        assert results == []

    def test_game_not_final_skipped(self):
        pending = _make_pending([{"id": 1, "bet_type": "HOME"}])
        results = _settle_f5(pending, {})
        assert results == []


# ── HR ───────────────────────────────────────────────────────────────────────

class TestSettleHr:
    def _make_batters(self, name, starter, hrs):
        return {name: {"starter": starter, "home_runs": hrs, "at_bats": 4}}

    def _settle(self, player, batters):
        pending = _make_pending([{"id": 1, "player": player, "bet_type": "HR_YES"}])
        cache   = {100: _boxscore(batters=batters)}
        return _settle_hr(pending, cache)

    def test_starter_hr_win(self):
        results = self._settle("Aaron Judge", self._make_batters("aaron judge", True, 1))
        assert results[0]["result"] == "win"

    def test_starter_no_hr_loss(self):
        results = self._settle("Aaron Judge", self._make_batters("aaron judge", True, 0))
        assert results[0]["result"] == "loss"

    def test_non_starter_void(self):
        results = self._settle("Aaron Judge", self._make_batters("aaron judge", False, 0))
        assert results[0]["result"] == "void"
        assert results[0]["profit"] == 0.0

    def test_player_not_in_final_boxscore_voided(self):
        # Game is FINAL and the player is absent from the boxscore -> he did
        # not play -> VOID (DK rule). The old skip-and-retry left these bets
        # pending forever, since the game never stops being final.
        results = self._settle("Aaron Judge", {})
        assert len(results) == 1
        assert results[0]["result"] == "void"
        assert results[0]["profit"] == 0.0

    def test_accent_normalization(self):
        # "José Abreu" should match "jose abreu" after NFD normalisation
        batters = {"jose abreu": {"starter": True, "home_runs": 1, "at_bats": 4}}
        results = self._settle("José Abreu", batters)
        assert results[0]["result"] == "win"

    def test_profit_on_win(self):
        pending = _make_pending([{"id": 1, "player": "judge", "bet_type": "HR_YES",
                                   "odds": 500, "stake": 10.0}])
        cache = {100: _boxscore(batters={"judge": {"starter": True, "home_runs": 1}})}
        results = _settle_hr(pending, cache)
        assert results[0]["profit"] == 50.0  # 10 * 500/100


# ── K / OUTS ─────────────────────────────────────────────────────────────────

class TestSettleK:
    def _make_pitchers(self, name, ks, outs):
        return {name: {"strikeouts": ks, "outs": outs, "earned_runs": 0}}

    def _settle(self, bet_type, pitcher, pitchers):
        pending = _make_pending([{"id": 1, "player": pitcher, "bet_type": bet_type}])
        cache   = {100: _boxscore(pitchers=pitchers)}
        return _settle_k(pending, cache)

    def test_k_over_win(self):
        pitchers = self._make_pitchers("gerrit cole", 9, 21)
        results  = self._settle("K_OVER_7.5", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "win"

    def test_k_over_loss(self):
        pitchers = self._make_pitchers("gerrit cole", 5, 15)
        results  = self._settle("K_OVER_7.5", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "loss"

    def test_k_under_win(self):
        pitchers = self._make_pitchers("gerrit cole", 4, 15)
        results  = self._settle("K_UNDER_7.5", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "win"

    def test_k_under_loss(self):
        pitchers = self._make_pitchers("gerrit cole", 9, 21)
        results  = self._settle("K_UNDER_7.5", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "loss"

    def test_outs_over_win(self):
        pitchers = self._make_pitchers("gerrit cole", 9, 18)
        results  = self._settle("OUTS_OVER_14.5", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "win"

    def test_outs_under_loss(self):
        pitchers = self._make_pitchers("gerrit cole", 9, 18)
        results  = self._settle("OUTS_UNDER_14.5", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "loss"

    def test_pitcher_not_in_boxscore_void(self):
        # Pitcher did not appear → void per DK rules
        results = self._settle("K_OVER_7.5", "Gerrit Cole", {})
        assert results[0]["result"] == "void"
        assert results[0]["profit"] == 0.0

    def test_whole_number_line_warns_not_crash(self, caplog):
        """Whole-number line should log a warning and grade push, not crash."""
        import logging
        pitchers = self._make_pitchers("gerrit cole", 7, 21)
        with caplog.at_level(logging.WARNING, logger="mlb.runners.settle_bets"):
            results = self._settle("K_OVER_7", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "push"
        assert any("whole-number line" in m for m in caplog.messages)

    def test_accent_normalized_pitcher(self):
        pitchers = {"jose berrios": {"strikeouts": 8, "outs": 18, "earned_runs": 2}}
        results  = self._settle("K_OVER_6.5", "José Berríos", pitchers)
        assert results[0]["result"] == "win"


# ── Batter props ──────────────────────────────────────────────────────────────

class TestSettleBatterProps:
    def _make_batters(self, name, starter=True, strikeouts=1, hits=1, total_bases=2):
        return {
            name: {
                "starter": starter,
                "strikeouts": strikeouts,
                "hits": hits,
                "total_bases": total_bases,
            }
        }

    def _settle(self, bet_type, player, batters):
        pending = _make_pending([{"id": 1, "player": player, "bet_type": bet_type}])
        cache = {100: _boxscore(batters=batters)}
        return _settle_batter_props(pending, cache)

    def test_batter_tb_over_win(self):
        batters = self._make_batters("aaron judge", total_bases=2)
        results = self._settle("BATTER_TB_OVER_1.5", "Aaron Judge", batters)
        assert results[0]["result"] == "win"

    def test_batter_tb_under_loss(self):
        batters = self._make_batters("aaron judge", total_bases=2)
        results = self._settle("BATTER_TB_UNDER_1.5", "Aaron Judge", batters)
        assert results[0]["result"] == "loss"

    def test_batter_tb_non_starter_void(self):
        batters = self._make_batters("aaron judge", starter=False, total_bases=0)
        results = self._settle("BATTER_TB_OVER_1.5", "Aaron Judge", batters)
        assert results[0]["result"] == "void"
        assert results[0]["profit"] == 0.0


# ── Stale non-final voids ─────────────────────────────────────────────────────

class TestStaleNonFinalVoids:
    def test_nonfinal_game_auto_voids_after_two_days(self):
        pending = _make_pending([{"id": 1, "game_date": "2026-05-01"}])
        results = _void_stale_nonfinal_bets(pending, {100: None}, "2026-05-03")
        assert results == [{"id": 1, "result": "void", "profit": 0.0}]

    def test_nonfinal_game_inside_grace_period_stays_pending(self):
        pending = _make_pending([{"id": 1, "game_date": "2026-05-02"}])
        results = _void_stale_nonfinal_bets(pending, {100: None}, "2026-05-03")
        assert results == []

    def test_final_game_does_not_auto_void_even_when_stale(self):
        pending = _make_pending([{"id": 1, "game_date": "2026-05-01"}])
        results = _void_stale_nonfinal_bets(pending, {100: _boxscore()}, "2026-05-03")
        assert results == []


# ── PITCHER_ER ───────────────────────────────────────────────────────────────

class TestSettlePitcherEr:
    def _settle(self, bet_type, player, pitchers):
        pending = _make_pending([{"id": 1, "player": player, "bet_type": bet_type}])
        cache   = {100: _boxscore(pitchers=pitchers)}
        return _settle_pitcher_er(pending, cache)

    def test_er_over_win(self):
        pitchers = {"gerrit cole": {"strikeouts": 6, "outs": 15, "earned_runs": 4}}
        results  = self._settle("PITCHER_ER_OVER_2.5", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "win"

    def test_er_over_loss(self):
        pitchers = {"gerrit cole": {"strikeouts": 6, "outs": 15, "earned_runs": 1}}
        results  = self._settle("PITCHER_ER_OVER_2.5", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "loss"

    def test_er_under_win(self):
        pitchers = {"gerrit cole": {"strikeouts": 6, "outs": 15, "earned_runs": 1}}
        results  = self._settle("PITCHER_ER_UNDER_2.5", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "win"

    def test_er_exact_match_push(self):
        # Whole-number line: 2 ER == 2.0 → push
        pitchers = {"gerrit cole": {"strikeouts": 6, "outs": 15, "earned_runs": 2}}
        results  = self._settle("PITCHER_ER_UNDER_2", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "push"

    def test_pitcher_not_in_boxscore_void(self):
        results = self._settle("PITCHER_ER_OVER_2.5", "Gerrit Cole", {})
        assert results[0]["result"] == "void"


# ── Deduplication guard ───────────────────────────────────────────────────────

class TestBetTrackerDedup:
    """Verify is_duplicate correctly gates morning vs evening runs."""

    def _make_tracker(self, tmp_path):
        from mlb_core.tracking.bet_tracker import BetTracker
        db = str(tmp_path / "test_bets.db")
        return BetTracker(db, system="NRFI")

    def test_non_triggered_does_not_block_triggered(self, tmp_path):
        """Morning run logs a non-triggered prediction; evening run should
        still be allowed to place a triggered bet on the same market."""
        tracker = self._make_tracker(tmp_path)
        # Morning: edge below gate → not triggered
        tracker.log_bet(game_date="2026-05-19", game_pk=100,
                        bet_type="NRFI", kelly_triggered=False,
                        player="CLE@LAA", stake=0.0, odds=-115,
                        model_prob=0.54, market_prob=0.52, edge=0.02,
                        kelly_pct=0.0)
        # Evening: edge crosses gate → triggered
        # Should NOT be blocked by the non-triggered morning row
        assert not tracker.is_duplicate("2026-05-19", 100, "NRFI",
                                        kelly_triggered=True)

    def test_triggered_blocks_second_triggered(self, tmp_path):
        """Once a triggered bet is logged, a second triggered bet on the
        same market must be blocked."""
        tracker = self._make_tracker(tmp_path)
        tracker.log_bet(game_date="2026-05-19", game_pk=100,
                        bet_type="NRFI", kelly_triggered=True,
                        player="CLE@LAA", stake=10.0, odds=-115,
                        model_prob=0.58, market_prob=0.52, edge=0.06,
                        kelly_pct=0.02)
        assert tracker.is_duplicate("2026-05-19", 100, "NRFI",
                                    kelly_triggered=True)

    def test_any_row_blocks_non_triggered_duplicate(self, tmp_path):
        """A non-triggered prediction should not be logged twice (already
        exists regardless of triggered status)."""
        tracker = self._make_tracker(tmp_path)
        tracker.log_bet(game_date="2026-05-19", game_pk=100,
                        bet_type="NRFI", kelly_triggered=False,
                        player="CLE@LAA", stake=0.0, odds=-115,
                        model_prob=0.54, market_prob=0.52, edge=0.02,
                        kelly_pct=0.0)
        assert tracker.is_duplicate("2026-05-19", 100, "NRFI",
                                    kelly_triggered=False)
