"""
tests/test_odds_math.py — Unit tests for mlb_core.odds.utils (T20).

Covers:
  - american_to_implied_prob at standard odds values
  - remove_vig: two-way market sums to 1.0 after devig
  - kelly_stake: correct formula post T01 fix
  - kelly_pct: consistent with kelly_stake
  - Per-game cap respects multi-system pending stakes (exposure.py)

Run: pytest tests/test_odds_math.py -v
"""
import math
import pytest

from mlb_core.odds.utils import (
    american_to_implied_prob,
    implied_to_american,
    remove_vig,
    kelly_stake,
    kelly_pct,
)


# ── american_to_implied_prob ─────────────────────────────────────────────────

class TestAmericanToImpliedProb:
    def test_minus_110(self):
        # -110 line: 110/(110+100) = 110/210 ≈ 0.5238
        p = american_to_implied_prob(-110)
        assert abs(p - 110 / 210) < 1e-6

    def test_plus_100(self):
        # +100 (even money): 100/(100+100) = 0.5
        p = american_to_implied_prob(100)
        assert abs(p - 0.5) < 1e-6

    def test_plus_200(self):
        # +200: 100/(200+100) = 1/3 ≈ 0.3333
        p = american_to_implied_prob(200)
        assert abs(p - 1 / 3) < 1e-6

    def test_minus_200(self):
        # -200: 200/(200+100) = 2/3 ≈ 0.6667
        p = american_to_implied_prob(-200)
        assert abs(p - 2 / 3) < 1e-6

    def test_nan_input(self):
        import numpy as np
        assert math.isnan(american_to_implied_prob(np.nan))

    def test_heavy_favourite(self):
        # -400: 400/500 = 0.80
        p = american_to_implied_prob(-400)
        assert abs(p - 0.80) < 1e-6

    def test_big_underdog(self):
        # +500: 100/600 ≈ 0.1667
        p = american_to_implied_prob(500)
        assert abs(p - 1 / 6) < 1e-6


# ── remove_vig ───────────────────────────────────────────────────────────────

class TestRemoveVig:
    def test_two_way_sums_to_one(self):
        # Typical NRFI/YRFI market: -115 / -105
        p_nrfi = american_to_implied_prob(-115)
        p_yrfi = american_to_implied_prob(-105)
        fair_nrfi, fair_yrfi = remove_vig(p_nrfi, p_yrfi)
        assert abs(fair_nrfi + fair_yrfi - 1.0) < 1e-9

    def test_symmetric_market(self):
        # -110 / -110: each side should devig to 0.5
        p = american_to_implied_prob(-110)
        fa, fb = remove_vig(p, p)
        assert abs(fa - 0.5) < 1e-6
        assert abs(fb - 0.5) < 1e-6

    def test_zero_total_returns_nan(self):
        import math
        fa, fb = remove_vig(0.0, 0.0)
        assert math.isnan(fa)
        assert math.isnan(fb)

    def test_proportions_preserved(self):
        # Larger implied prob should have larger fair prob.
        p_fav  = american_to_implied_prob(-150)
        p_dog  = american_to_implied_prob(130)
        f_fav, f_dog = remove_vig(p_fav, p_dog)
        assert f_fav > f_dog


# ── kelly_stake (T01 fix) ────────────────────────────────────────────────────

class TestKellyStake:
    """
    Full Kelly: f* = edge * (b + 1) / b
    where b = decimal odds - 1.

    At -110: b = 100/110 ≈ 0.9091, (b+1)/b ≈ 2.1
    At +100: b = 1.0,              (b+1)/b = 2.0
    At +200: b = 2.0,              (b+1)/b = 1.5
    """

    def test_minus_110_known_value(self):
        # p_model=0.55, p_fair=american_to_implied_prob(-110)*(110/210)
        # Using devigged fair for -110/-110 market: fair = 0.5
        # edge = 0.55 - 0.5 = 0.05
        # b = 100/110, full_kelly = 0.05 * (1 + 110/100) = 0.05 * 2.1 = 0.105
        # fractional = 0.105 * 0.25 = 0.02625
        edge     = 0.05
        odds     = -110
        bankroll = 1000.0
        fraction = 0.25
        b        = 100 / 110
        expected_pct = edge * (b + 1) / b * fraction
        expected_stake = round(min(expected_pct, 0.05) * bankroll, 2)
        result = kelly_stake(edge, odds, bankroll, fraction=fraction)
        assert abs(result - expected_stake) < 0.01, (
            f"kelly_stake(-110): expected {expected_stake}, got {result}"
        )

    def test_acceptance_criterion_from_task(self):
        # T01 acceptance: p=0.55, odds=-110, fraction=1.0 → ≈ 0.105 of bankroll
        # With bankroll=1, result ≈ 0.05 (capped by max_pct=0.05).
        # Uncapped (max_pct=1.0) should be ≈ 0.105.
        edge     = 0.05   # 0.55 - 0.50 at even-money fair
        odds     = -110
        bankroll = 1.0
        fraction = 1.0
        b        = 100 / 110
        expected = edge * (b + 1) / b * fraction  # ≈ 0.105
        result   = kelly_stake(edge, odds, bankroll,
                               fraction=fraction, min_pct=0.0, max_pct=1.0)
        assert abs(result - expected) < 0.001, (
            f"T01 acceptance: expected ≈{expected:.4f}, got {result:.4f}"
        )

    def test_zero_edge_returns_zero(self):
        assert kelly_stake(0.0, -110, 1000.0) == 0.0

    def test_negative_edge_returns_zero(self):
        assert kelly_stake(-0.03, -110, 1000.0) == 0.0

    def test_plus_200_stake(self):
        # b = 2.0, full kelly = edge * 1.5
        edge     = 0.10
        odds     = 200
        bankroll = 1000.0
        fraction = 0.25
        b        = 2.0
        expected_pct = edge * (b + 1) / b * fraction
        expected_stake = round(min(expected_pct, 0.05) * bankroll, 2)
        result = kelly_stake(edge, odds, bankroll, fraction=fraction)
        assert abs(result - expected_stake) < 0.01

    def test_below_min_pct_returns_zero(self):
        # Very tiny edge — below min_kelly_pct=0.005 → no bet
        result = kelly_stake(0.001, -110, 1000.0, fraction=0.25, min_pct=0.005)
        assert result == 0.0

    def test_capped_at_max_pct(self):
        # Large edge should still be capped at max_pct * bankroll
        result = kelly_stake(0.30, 200, 1000.0, fraction=1.0, max_pct=0.05)
        assert result == 50.0  # 5% of 1000


class TestKellyPct:
    def test_consistent_with_kelly_stake(self):
        """kelly_pct * bankroll should equal kelly_stake (before min/max clamps)."""
        edge     = 0.06
        odds     = -115
        bankroll = 1000.0
        fraction = 0.25
        pct    = kelly_pct(edge, odds, fraction)
        stake  = kelly_stake(edge, odds, bankroll, fraction=fraction,
                             min_pct=0.0, max_pct=1.0)
        assert abs(pct * bankroll - stake) < 0.01

    def test_zero_edge(self):
        assert kelly_pct(0.0, -110) == 0.0

    def test_plus_100_known_value(self):
        # b=1.0, (b+1)/b=2.0, fraction=1.0: pct = edge * 2
        edge = 0.05
        pct  = kelly_pct(edge, 100, fraction=1.0)
        assert abs(pct - 0.10) < 1e-6


# ── exposure cap (multi-system) ──────────────────────────────────────────────

class TestExposureCap:
    """
    apply_cap should correctly reduce remaining_cap when both prefetched and
    pending stakes are present for the same game_pk.
    """

    def test_cap_respects_prefetched_plus_pending(self):
        from mlb_core.risk.exposure import apply_cap

        bankroll    = 1000.0
        cap_units   = 2.0
        unit_pct    = 0.01
        unit        = bankroll * unit_pct       # $10
        cap         = cap_units * unit           # $20

        game_pk     = 12345
        prefetched  = {game_pk: 10.0}           # $10 already open (from DB)
        pending     = {game_pk: 5.0}            # $5 pending from THIS runner run

        _, remaining = apply_cap(
            bankroll, game_pk, prefetched, pending,
            cap_units=cap_units, unit_pct=unit_pct,
        )
        # Total open = 10 + 5 = 15. Remaining = 20 - 15 = 5.
        assert abs(remaining - 5.0) < 0.01

    def test_cap_returns_zero_when_fully_used(self):
        from mlb_core.risk.exposure import apply_cap

        bankroll   = 1000.0
        game_pk    = 99999
        prefetched = {game_pk: 15.0}
        pending    = {game_pk: 10.0}   # total 25 >= cap of 20

        _, remaining = apply_cap(bankroll, game_pk, prefetched, pending,
                                 cap_units=2.0, unit_pct=0.01)
        assert remaining == 0.0

    def test_cap_zero_open(self):
        from mlb_core.risk.exposure import apply_cap

        bankroll = 500.0
        _, remaining = apply_cap(bankroll, 11111, {}, {},
                                 cap_units=2.0, unit_pct=0.01)
        # cap = 2 * 500 * 0.01 = $10; nothing open, full cap remaining
        assert abs(remaining - 10.0) < 0.01


# ── devig_unilateral ─────────────────────────────────────────────────────────

class TestDevigUnilateral:
    def test_hr_props_typical(self):
        from mlb_core.odds.utils import devig_unilateral
        # +350 HR prop → implied = 100/450 ≈ 0.2222; fair ≈ 0.2222/1.07 ≈ 0.2077
        from mlb_core.odds.utils import american_to_implied_prob
        raw = american_to_implied_prob(350)
        fair = devig_unilateral(raw, vig_pct=0.07)
        assert fair < raw  # devigging reduces the probability
        assert abs(fair - raw / 1.07) < 1e-9

    def test_zero_vig_identity(self):
        from mlb_core.odds.utils import devig_unilateral, american_to_implied_prob
        raw = american_to_implied_prob(200)
        assert abs(devig_unilateral(raw, vig_pct=0.0) - raw) < 1e-9

    def test_nan_input(self):
        import math
        from mlb_core.odds.utils import devig_unilateral
        assert math.isnan(devig_unilateral(float("nan")))

    def test_zero_prob_returns_nan(self):
        import math
        from mlb_core.odds.utils import devig_unilateral
        assert math.isnan(devig_unilateral(0.0))
