"""
tests/test_ev_kelly_bankroll.py -- mlb.analysis.ev_kelly_bankroll's pure
rescaling math: given real, already-graded system="EV" bets (flat $100
stake, real profit from settle_bets._calc_profit), does resizing each bet to
kelly_pct * a fixed reference bankroll reproduce the correct payout via the
profit/stake ratio, without touching the real stake/profit columns?
"""
import pandas as pd
import pytest

from mlb.analysis.ev_kelly_bankroll import kelly_bankroll_report


def _bet(**overrides):
    row = {
        "game_date": "2026-08-19", "bet_type": "K_OVER_7.5_draftkings",
        "player": "Some Pitcher", "odds": 120, "model_prob": 0.55, "edge": 0.05,
        "kelly_pct": 0.02, "stake": 100.0, "profit": 118.0, "result": "win",
        "created_at": "2026-08-19T00:00:00",
    }
    row.update(overrides)
    return row


class TestKellyBankrollReport:
    def test_kelly_stake_is_pct_times_fixed_bankroll(self):
        df = pd.DataFrame([_bet(kelly_pct=0.03)])
        report = kelly_bankroll_report(df, bankroll=1000.0)
        assert report["detail"]["kelly_stake"].iloc[0] == pytest.approx(30.0)

    def test_kelly_profit_rescales_the_real_payout_ratio(self):
        # stake=100 -> profit=118 is a +1.18x payout (e.g. +120 american win).
        # A $30 kelly stake at the identical price/result must pay the SAME
        # multiple, not be re-derived from odds independently.
        df = pd.DataFrame([_bet(stake=100.0, profit=118.0, kelly_pct=0.03)])
        report = kelly_bankroll_report(df, bankroll=1000.0)
        assert report["detail"]["kelly_profit"].iloc[0] == pytest.approx(30.0 * 1.18)

    def test_loss_rescales_to_a_negative_multiple(self):
        df = pd.DataFrame([_bet(stake=100.0, profit=-100.0, result="loss", kelly_pct=0.05)])
        report = kelly_bankroll_report(df, bankroll=1000.0)
        assert report["detail"]["kelly_profit"].iloc[0] == pytest.approx(-50.0)

    def test_running_bankroll_is_cumulative_non_compounding(self):
        """Each bet's SIZE is always kelly_pct * the fixed bankroll -- a
        prior bet's win/loss must not change how the next bet is sized.
        max_pct raised above these fixtures' 0.10 so the cap (tested
        separately below) doesn't interact with what this test checks."""
        df = pd.DataFrame([
            _bet(kelly_pct=0.10, stake=100.0, profit=100.0, result="win"),   # +100 kelly pnl
            _bet(kelly_pct=0.10, stake=100.0, profit=-100.0, result="loss"),  # -100 kelly pnl
        ])
        report = kelly_bankroll_report(df, bankroll=1000.0, max_pct=0.10)
        detail = report["detail"]
        assert detail["kelly_stake"].tolist() == [100.0, 100.0], "second bet's size must not shrink after the first bet lost nothing yet"
        assert detail["running_bankroll"].tolist() == pytest.approx([1100.0, 1000.0])

    def test_totals_and_roi(self):
        df = pd.DataFrame([
            _bet(kelly_pct=0.10, stake=100.0, profit=100.0, result="win"),
            _bet(kelly_pct=0.10, stake=100.0, profit=-100.0, result="loss"),
        ])
        report = kelly_bankroll_report(df, bankroll=1000.0, max_pct=0.10)
        assert report["kelly_total_staked"] == pytest.approx(200.0)
        assert report["kelly_total_pnl"] == pytest.approx(0.0)
        assert report["kelly_roi_pct"] == pytest.approx(0.0)
        assert report["kelly_ending_bankroll"] == pytest.approx(1000.0)
        assert report["hit_rate"] == pytest.approx(0.5)

    def test_push_excluded_from_hit_rate_denominator(self):
        df = pd.DataFrame([
            _bet(result="win", profit=100.0),
            _bet(result="push", profit=0.0),
        ])
        report = kelly_bankroll_report(df, bankroll=1000.0)
        assert report["n_bets"] == 2
        assert report["hit_rate"] == pytest.approx(1.0), "push is neither a win nor a loss"

    def test_rows_missing_kelly_pct_are_excluded(self):
        """Rows logged before the 2026-09-18 kelly_pct addition/backfill
        (or any future gap) must not silently become a $0 or NaN-propagating
        row -- they're dropped from this report, not zeroed."""
        df = pd.DataFrame([_bet(kelly_pct=0.05), _bet(kelly_pct=None)])
        report = kelly_bankroll_report(df, bankroll=1000.0)
        assert report["n_bets"] == 1

    def test_zero_stake_rows_excluded(self):
        df = pd.DataFrame([_bet(stake=0.0)])
        report = kelly_bankroll_report(df, bankroll=1000.0)
        assert report["n_bets"] == 0

    def test_extreme_kelly_pct_is_capped_at_max_pct(self):
        """Real bug found 2026-09-18 on the recovered EV history: a cluster
        of HR_yn rows carry an implausible model_prob (~0.99 -- no single
        batter has a ~99% per-game HR chance) at extreme-favorite odds,
        producing uncapped kelly_pct up to 0.2185 (21.85% of bankroll on ONE
        prop). kelly_pct itself is stored uncapped by design (kelly_pct()'s
        docstring: "for signal gating") -- capping belongs at the STAKE step,
        exactly like every model system's real kelly_stake() call already
        does via its own max_pct. Default max_pct=0.05 here must cap the
        stake, not silently trust an extreme probability estimate."""
        df = pd.DataFrame([_bet(kelly_pct=0.2185, stake=100.0, profit=-100.0, result="loss")])
        report = kelly_bankroll_report(df, bankroll=1000.0)  # default max_pct=0.05
        assert report["detail"]["kelly_stake"].iloc[0] == pytest.approx(50.0)

    def test_max_pct_is_configurable(self):
        df = pd.DataFrame([_bet(kelly_pct=0.2185, stake=100.0, profit=-100.0, result="loss")])
        report = kelly_bankroll_report(df, bankroll=1000.0, max_pct=0.10)
        assert report["detail"]["kelly_stake"].iloc[0] == pytest.approx(100.0)

    def test_capped_bankroll_cannot_go_deeply_negative_from_one_bad_prop(self):
        """The exact real-world failure mode: an uncapped run on the
        recovered history went to a NEGATIVE ending bankroll from a $1000
        start, driven by a handful of over-leveraged, badly-estimated HR
        props that lost. Capping must keep a single bad bet's damage
        bounded to max_pct of bankroll."""
        df = pd.DataFrame([_bet(kelly_pct=0.2185, stake=100.0, profit=-100.0, result="loss")])
        report = kelly_bankroll_report(df, bankroll=1000.0, max_pct=0.05)
        assert report["kelly_ending_bankroll"] == pytest.approx(950.0)

    def test_kelly_pct_below_cap_is_unaffected(self):
        df = pd.DataFrame([_bet(kelly_pct=0.02, stake=100.0, profit=118.0, result="win")])
        report = kelly_bankroll_report(df, bankroll=1000.0, max_pct=0.05)
        assert report["detail"]["kelly_stake"].iloc[0] == pytest.approx(20.0)

    def test_flat_stats_untouched_by_kelly_rescaling(self):
        """The existing flat-$100 view must still be exactly what's in the
        real stake/profit columns -- this report only adds a parallel view,
        it never mutates or overrides the original."""
        df = pd.DataFrame([_bet(stake=100.0, profit=118.0)])
        report = kelly_bankroll_report(df, bankroll=1000.0)
        assert report["flat_total_staked"] == pytest.approx(100.0)
        assert report["flat_total_pnl"] == pytest.approx(118.0)
        assert report["flat_roi_pct"] == pytest.approx(118.0)
