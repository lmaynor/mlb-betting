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
    _settle_ev,
    _strip_ev_book_suffix,
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

    def test_k_2plus_exact_hit_is_a_win_not_a_push(self):
        """Regression (2026-08-19): a threshold bet has no complementary
        'under N' side -- hitting exactly N must be a WIN, not the generic
        actual==line push the main O/U logic would otherwise apply."""
        pitchers = self._make_pitchers("gerrit cole", 2, 15)
        results  = self._settle("K_2PLUS_2.0", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "win"

    def test_k_2plus_below_threshold_is_a_loss(self):
        pitchers = self._make_pitchers("gerrit cole", 1, 15)
        results  = self._settle("K_2PLUS_2.0", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "loss"

    def test_k_3plus_above_threshold_is_a_win(self):
        pitchers = self._make_pitchers("gerrit cole", 9, 21)
        results  = self._settle("K_3PLUS_3.0", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "win"

    def test_outs_2plus_exact_hit_is_a_win_not_a_push(self):
        pitchers = self._make_pitchers("gerrit cole", 5, 2)
        results  = self._settle("OUTS_2PLUS_2.0", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "win"

    def test_outs_2plus_below_threshold_is_a_loss(self):
        pitchers = self._make_pitchers("gerrit cole", 5, 1)
        results  = self._settle("OUTS_2PLUS_2.0", "Gerrit Cole", pitchers)
        assert results[0]["result"] == "loss"


# ── Batter props ──────────────────────────────────────────────────────────────

class TestSettleBatterProps:
    def _make_batters(self, name, starter=True, strikeouts=1, hits=1, total_bases=2,
                       stolen_bases=0):
        return {
            name: {
                "starter": starter,
                "strikeouts": strikeouts,
                "hits": hits,
                "total_bases": total_bases,
                "stolen_bases": stolen_bases,
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

    def test_batter_tb_2plus_exact_hit_is_a_win_not_a_push(self):
        """Regression (2026-08-19): same reasoning as K's identical fix --
        a threshold bet has no complementary 'under N' side, so hitting
        exactly N must be a WIN, not a push."""
        batters = self._make_batters("aaron judge", total_bases=2)
        results = self._settle("BATTER_TB_2PLUS_2.0", "Aaron Judge", batters)
        assert results[0]["result"] == "win"

    def test_batter_tb_2plus_below_threshold_is_a_loss(self):
        batters = self._make_batters("aaron judge", total_bases=1)
        results = self._settle("BATTER_TB_2PLUS_2.0", "Aaron Judge", batters)
        assert results[0]["result"] == "loss"

    def test_batter_hits_2plus_exact_hit_is_a_win_not_a_push(self):
        batters = self._make_batters("aaron judge", hits=2)
        results = self._settle("BATTER_HITS_2PLUS_2.0", "Aaron Judge", batters)
        assert results[0]["result"] == "win"

    def test_batter_hits_2plus_non_starter_void(self):
        """Threshold bets go through the same starter check as the main
        line -- the PLUS branch must not bypass it."""
        batters = self._make_batters("aaron judge", starter=False, hits=5)
        results = self._settle("BATTER_HITS_2PLUS_2.0", "Aaron Judge", batters)
        assert results[0]["result"] == "void"
        assert results[0]["profit"] == 0.0

    # -- SB (stolen bases, added 2026-08-20) --------------------------------

    def test_sb_over_win(self):
        batters = self._make_batters("jazz chisholm jr", stolen_bases=1)
        results = self._settle("SB_OVER_0.5", "Jazz Chisholm Jr", batters)
        assert results[0]["result"] == "win"

    def test_sb_over_loss(self):
        batters = self._make_batters("jazz chisholm jr", stolen_bases=0)
        results = self._settle("SB_OVER_0.5", "Jazz Chisholm Jr", batters)
        assert results[0]["result"] == "loss"

    def test_sb_under_win(self):
        batters = self._make_batters("jazz chisholm jr", stolen_bases=0)
        results = self._settle("SB_UNDER_0.5", "Jazz Chisholm Jr", batters)
        assert results[0]["result"] == "win"

    def test_sb_non_starter_void(self):
        """Same DK starter rule as every other batter prop."""
        batters = self._make_batters("jazz chisholm jr", starter=False, stolen_bases=2)
        results = self._settle("SB_OVER_0.5", "Jazz Chisholm Jr", batters)
        assert results[0]["result"] == "void"
        assert results[0]["profit"] == 0.0

    def test_sb_2plus_exact_hit_is_a_win_not_a_push(self):
        """Same reasoning as BATTER_TB/BATTER_HITS's identical fix -- a
        threshold bet has no complementary 'under N' side, so hitting
        exactly N must be a WIN, not a push."""
        batters = self._make_batters("jazz chisholm jr", stolen_bases=2)
        results = self._settle("SB_2PLUS_2.0", "Jazz Chisholm Jr", batters)
        assert results[0]["result"] == "win"

    def test_sb_2plus_below_threshold_is_a_loss(self):
        batters = self._make_batters("jazz chisholm jr", stolen_bases=1)
        results = self._settle("SB_2PLUS_2.0", "Jazz Chisholm Jr", batters)
        assert results[0]["result"] == "loss"

    def test_sb_player_not_in_boxscore_voids(self):
        batters = self._make_batters("someone else", stolen_bases=1)
        results = self._settle("SB_OVER_0.5", "Jazz Chisholm Jr", batters)
        assert results[0]["result"] == "void"


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


# ── EV (fast_alert_loop's posted +EV alerts) ─────────────────────────────────

class TestSettleEv:
    """system="EV" bets carry the underlying market's own bet_type,
    suffixed with "_{book}" (see fast_alert_loop._ev_bet_type). _settle_ev
    must dispatch each row to the SAME settler a real bet on that market
    would use, purely by sniffing the bet_type prefix."""

    def test_dispatches_k_to_settle_k(self):
        pending = _make_pending([
            {"id": 1, "player": "Gerrit Cole", "bet_type": "K_OVER_7.5_draftkings"},
        ])
        cache = {100: _boxscore(pitchers={"gerrit cole": {"strikeouts": 9, "outs": 21, "earned_runs": 2}})}
        results = _settle_ev(pending, cache)
        assert len(results) == 1
        assert results[0]["result"] == "win"

    def test_dispatches_outs_to_settle_k(self):
        pending = _make_pending([
            {"id": 1, "player": "Gerrit Cole", "bet_type": "OUTS_UNDER_17.5_fanduel"},
        ])
        cache = {100: _boxscore(pitchers={"gerrit cole": {"strikeouts": 9, "outs": 15, "earned_runs": 2}})}
        results = _settle_ev(pending, cache)
        assert results[0]["result"] == "win"

    def test_dispatches_hr_to_settle_hr(self):
        # HR has no line to encode -- bet_type is just "HR_{book}".
        pending = _make_pending([
            {"id": 1, "player": "Aaron Judge", "bet_type": "HR_hardrock"},
        ])
        cache = {100: _boxscore(batters={"aaron judge": {"starter": True, "home_runs": 1, "at_bats": 4}})}
        results = _settle_ev(pending, cache)
        assert results[0]["result"] == "win"

    def test_dispatches_batter_tb_to_settle_batter_props(self):
        pending = _make_pending([
            {"id": 1, "player": "Aaron Judge", "bet_type": "BATTER_TB_OVER_1.5_betmgm"},
        ])
        cache = {100: _boxscore(batters={"aaron judge": {"starter": True, "total_bases": 2, "hits": 1}})}
        results = _settle_ev(pending, cache)
        assert results[0]["result"] == "win"

    def test_dispatches_batter_hits_to_settle_batter_props(self):
        pending = _make_pending([
            {"id": 1, "player": "Aaron Judge", "bet_type": "BATTER_HITS_UNDER_0.5_caesars"},
        ])
        cache = {100: _boxscore(batters={"aaron judge": {"starter": True, "total_bases": 0, "hits": 0}})}
        results = _settle_ev(pending, cache)
        assert results[0]["result"] == "win"

    def test_dispatches_sb_to_settle_batter_props(self):
        pending = _make_pending([
            {"id": 1, "player": "Jazz Chisholm Jr", "bet_type": "SB_OVER_0.5_draftkings"},
        ])
        cache = {100: _boxscore(batters={"jazz chisholm jr": {"starter": True, "stolen_bases": 1}})}
        results = _settle_ev(pending, cache)
        assert results[0]["result"] == "win"

    def test_dispatches_pitcher_er_to_settle_pitcher_er(self):
        pending = _make_pending([
            {"id": 1, "player": "Gerrit Cole", "bet_type": "PITCHER_ER_OVER_2.5_novig"},
        ])
        cache = {100: _boxscore(pitchers={"gerrit cole": {"strikeouts": 6, "outs": 15, "earned_runs": 4}})}
        results = _settle_ev(pending, cache)
        assert results[0]["result"] == "win"

    def test_mixed_batch_all_settled_together(self):
        """A single settle run's EV pending set spans multiple underlying
        markets -- one call must grade all of them correctly, not just
        whichever market happens to be checked first."""
        pending = _make_pending([
            {"id": 1, "player": "Gerrit Cole", "bet_type": "K_OVER_7.5_draftkings"},
            {"id": 2, "player": "Aaron Judge", "bet_type": "HR_hardrock"},
            {"id": 3, "player": "Aaron Judge", "bet_type": "BATTER_TB_UNDER_1.5_betmgm"},
        ])
        cache = {100: _boxscore(
            pitchers={"gerrit cole": {"strikeouts": 9, "outs": 21, "earned_runs": 2}},
            batters={"aaron judge": {"starter": True, "home_runs": 0, "total_bases": 1, "hits": 1}},
        )}
        results = _settle_ev(pending, cache)
        assert len(results) == 3
        by_id = {r["id"]: r for r in results}
        assert by_id[1]["result"] == "win"    # 9 Ks > 7.5
        assert by_id[2]["result"] == "loss"   # 0 HR
        assert by_id[3]["result"] == "win"    # 1 TB < 1.5

    def test_two_books_same_prop_settle_independently(self):
        """Regression: two different books' alerts on the identical
        player/line are two separate rows (distinguished only by the book
        suffix on bet_type) -- both must settle, not just one."""
        pending = _make_pending([
            {"id": 1, "player": "Gerrit Cole", "bet_type": "K_OVER_7.5_draftkings"},
            {"id": 2, "player": "Gerrit Cole", "bet_type": "K_OVER_7.5_fanduel"},
        ])
        cache = {100: _boxscore(pitchers={"gerrit cole": {"strikeouts": 9, "outs": 21, "earned_runs": 2}})}
        results = _settle_ev(pending, cache)
        assert len(results) == 2
        assert all(r["result"] == "win" for r in results)

    def test_unrecognised_bet_type_skipped_not_crashed(self, caplog):
        import logging
        pending = _make_pending([
            {"id": 1, "player": "X", "bet_type": "GARBAGE_TYPE_draftkings"},
        ])
        with caplog.at_level(logging.WARNING, logger="mlb.runners.settle_bets"):
            results = _settle_ev(pending, {100: _boxscore()})
        assert results == []
        assert any("unrecognised bet_type" in m for m in caplog.messages)

    # -- kalshi_alert's game-level markets (2026-08-20): NRFI/GAME/F5 use
    # exact-string or rsplit-last-token bet_type parsing, unlike the prefix-
    # tolerant markets above -- these need the book suffix stripped first.

    def test_strip_ev_book_suffix(self):
        assert _strip_ev_book_suffix("NRFI_draftkings", "draftkings") == "NRFI"
        assert _strip_ev_book_suffix("GAME_HOME_hardrock", "hardrock") == "GAME_HOME"
        assert _strip_ev_book_suffix("HOME_betmgm", "betmgm") == "HOME"
        # book missing/mismatched -- leaves bet_type untouched rather than
        # guessing wrong (falls through to "unrecognised", not silently
        # mis-graded).
        assert _strip_ev_book_suffix("NRFI_draftkings", None) == "NRFI_draftkings"

    def test_dispatches_nrfi_yrfi_to_settle_nrfi(self):
        pending = _make_pending([
            {"id": 1, "bet_type": "YRFI_draftkings", "book": "draftkings"},
        ])
        cache = {100: _nrfi_game(1, 0)}  # away scores in inning 1 -> YRFI
        results = _settle_ev(pending, cache)
        assert results[0]["result"] == "win"

    def test_dispatches_nrfi_nrfi_to_settle_nrfi(self):
        pending = _make_pending([
            {"id": 1, "bet_type": "NRFI_fanduel", "book": "fanduel"},
        ])
        cache = {100: _nrfi_game(0, 0)}
        results = _settle_ev(pending, cache)
        assert results[0]["result"] == "win"

    def test_dispatches_game_ml_to_innings_window(self):
        pending = _make_pending([
            {"id": 1, "bet_type": "GAME_HOME_hardrock", "book": "hardrock"},
        ])
        # 9 full innings, home wins 3-1
        innings = [(1, 0)] + [(0, 0)] * 7 + [(0, 2)]
        cache = {100: _f5_game(innings)}
        results = _settle_ev(pending, cache)
        assert results[0]["result"] == "win"

    def test_dispatches_f5_ml_bare_side_to_settle_f5(self):
        pending = _make_pending([
            {"id": 1, "bet_type": "AWAY_betmgm", "book": "betmgm"},
        ])
        innings = [(2, 0), (0, 0), (0, 0), (0, 0), (0, 0)]  # away leads after 5
        cache = {100: _f5_game(innings)}
        results = _settle_ev(pending, cache)
        assert results[0]["result"] == "win"

    def test_two_books_same_nrfi_game_settle_independently(self):
        """Same regression shape as the K case above, for the exact-match
        NRFI settler this time -- confirms stripping doesn't accidentally
        collapse two distinct rows into one."""
        pending = _make_pending([
            {"id": 1, "bet_type": "NRFI_draftkings", "book": "draftkings"},
            {"id": 2, "bet_type": "NRFI_fanduel", "book": "fanduel"},
        ])
        cache = {100: _nrfi_game(0, 0)}
        results = _settle_ev(pending, cache)
        assert len(results) == 2
        assert all(r["result"] == "win" for r in results)

    def test_mixed_batch_incl_game_level_markets(self):
        """One settle run's EV pending set can span prop markets AND
        game-level markets (kalshi_alert's nrfi_ou/game_ml/f5_ml) at once."""
        pending = _make_pending([
            {"id": 1, "player": "Gerrit Cole", "bet_type": "K_OVER_7.5_draftkings", "book": "draftkings"},
            {"id": 2, "bet_type": "YRFI_hardrock", "book": "hardrock"},
            {"id": 3, "bet_type": "GAME_AWAY_novig", "book": "novig"},
        ])
        cache = {
            100: _boxscore(pitchers={"gerrit cole": {"strikeouts": 9, "outs": 21, "earned_runs": 2}}),
        }
        # id 1 is keyed to game_pk=100 (default); ids 2/3 need their own
        # game_pk's cache entries with innings data.
        pending.loc[pending["id"] == 2, "game_pk"] = 200
        pending.loc[pending["id"] == 3, "game_pk"] = 300
        cache[200] = _nrfi_game(1, 0)                                    # YRFI win
        cache[300] = _f5_game([(2, 0)] + [(0, 0)] * 7 + [(0, 0)])         # away wins -> GAME_AWAY win
        results = _settle_ev(pending, cache)
        assert len(results) == 3
        by_id = {r["id"]: r for r in results}
        assert by_id[1]["result"] == "win"
        assert by_id[2]["result"] == "win"
        assert by_id[3]["result"] == "win"


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
                                        player="CLE@LAA", kelly_triggered=True)

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
                                    player="CLE@LAA", kelly_triggered=True)

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
                                    player="CLE@LAA", kelly_triggered=False)

    def test_different_players_same_game_do_not_collide(self, tmp_path):
        """Regression test (2026-08-18): two different players qualifying
        for the same bet_type in the same game on the same day must NOT be
        treated as duplicates of each other. bet_type alone is not a unique
        key for per-player markets -- e.g. every HR bet has bet_type="HR"
        regardless of which batter. This is what idx_bets_dedup_v3 adding
        `player` to the key is meant to guarantee; found via a live incident
        where 24 distinct HR bets across 7 games were misidentified as
        23 "duplicate" rows by a dedup key missing `player`."""
        from mlb_core.tracking.bet_tracker import BetTracker
        db = str(tmp_path / "test_bets.db")
        tracker = BetTracker(db, system="HR")

        id_a = tracker.log_bet(game_date="2026-05-12", game_pk=823385,
                               bet_type="HR", kelly_triggered=True,
                               player="Hunter Goodman", stake=5.31, odds=529,
                               model_prob=0.2048, market_prob=0.16, edge=0.0562,
                               kelly_pct=0.01)
        id_b = tracker.log_bet(game_date="2026-05-12", game_pk=823385,
                               bet_type="HR", kelly_triggered=True,
                               player="Willi Castro", stake=0.0, odds=1200,
                               model_prob=0.1221, market_prob=0.08, edge=0.0502,
                               kelly_pct=0.0)

        assert id_a != -1, "first player's HR bet should log normally"
        assert id_b != -1, "second player's HR bet must not be dropped as a duplicate"
        assert id_a != id_b

        # And logging Hunter Goodman's HR bet again (same player, same
        # everything) IS still correctly caught as a real duplicate.
        id_a_again = tracker.log_bet(game_date="2026-05-12", game_pk=823385,
                                     bet_type="HR", kelly_triggered=True,
                                     player="Hunter Goodman", stake=5.31, odds=529,
                                     model_prob=0.2048, market_prob=0.16, edge=0.0562,
                                     kelly_pct=0.01)
        assert id_a_again == -1
