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


# ── kelly_stake (T01 fix, probability-basis fix 2026-08-17) ─────────────────

class TestKellyStake:
    """
    Full Kelly: f* = edge * (b + 1) / b
    where b = decimal odds - 1, edge = model_prob - market_prob (the
    VIG-INCLUSIVE implied prob of the *same* odds, derived internally by
    kelly_stake/kelly_pct via american_to_implied_prob -- NOT a de-vigged
    fair prob the caller might have on hand for a different purpose, e.g.
    min_edge gating). Callers now pass model_prob, not a precomputed edge --
    see docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md
    finding A1 for why edge-vs-fair was the wrong basis for Kelly sizing.

    At -110: b = 100/110 ≈ 0.9091, (b+1)/b ≈ 2.1
    At +100: b = 1.0,              (b+1)/b = 2.0
    At +200: b = 2.0,              (b+1)/b = 1.5
    """

    def test_minus_110_known_value(self):
        # p_model=0.55 at -110. market_prob (vig-inclusive, the correct Kelly
        # basis) = american_to_implied_prob(-110) = 110/210 ≈ 0.52381 -- NOT
        # the de-vigged fair=0.50 a -110/-110 two-way market would produce.
        model_prob = 0.55
        odds       = -110
        bankroll   = 1000.0
        fraction   = 0.25
        market_prob  = american_to_implied_prob(odds)
        edge         = model_prob - market_prob
        b            = 100 / 110
        expected_pct = edge * (b + 1) / b * fraction
        expected_stake = round(min(expected_pct, 0.05) * bankroll, 2)
        result = kelly_stake(model_prob, odds, bankroll, fraction=fraction)
        assert abs(result - expected_stake) < 0.01, (
            f"kelly_stake(-110): expected {expected_stake}, got {result}"
        )

    def test_acceptance_criterion_from_task(self):
        # T01's original acceptance value (~0.105 of bankroll) assumed edge was
        # computed vs a hardcoded fair=0.50. Post the 2026-08-17 basis fix, the
        # SAME model_prob=0.55 at -110 correctly yields a smaller edge (vs the
        # vig-inclusive market_prob≈0.52381, not 0.50) and thus a smaller stake
        # -- this is the fix working as intended, not a regression.
        model_prob = 0.55
        odds       = -110
        bankroll   = 1.0
        fraction   = 1.0
        market_prob = american_to_implied_prob(odds)
        edge        = model_prob - market_prob
        b           = 100 / abs(odds)  # 100/110 ≈ 0.909
        expected    = edge * (b + 1) / b * fraction
        result      = kelly_stake(model_prob, odds, bankroll,
                                  fraction=fraction, min_pct=0.0, max_pct=1.0)
        assert abs(result - expected) < 0.01, (
            f"probability-basis fix: expected ≈{expected:.4f}, got {result:.4f}"
        )
        # And explicitly confirm the fix changed behavior: the pre-fix formula
        # (edge vs a hardcoded 0.50 fair) would have given ≈0.105, materially
        # larger than the corrected value.
        pre_fix_edge = model_prob - 0.50
        pre_fix_expected = pre_fix_edge * (b + 1) / b * fraction
        assert result < pre_fix_expected - 0.01, (
            "expected the basis fix to reduce the stake vs the old (wrong) "
            "fair-prob-based calculation"
        )

    def test_zero_edge_returns_zero(self):
        # model_prob exactly at the odds' own breakeven -> edge=0 -> no bet.
        odds = -110
        assert kelly_stake(american_to_implied_prob(odds), odds, 1000.0) == 0.0

    def test_negative_edge_returns_zero(self):
        odds = -110
        model_prob = american_to_implied_prob(odds) - 0.03
        assert kelly_stake(model_prob, odds, 1000.0) == 0.0

    def test_plus_200_stake(self):
        # b = 2.0, full kelly = edge * 1.5. Construct model_prob to give a
        # known edge=0.10 over +200's own market_prob (100/300 ≈ 0.3333).
        odds     = 200
        bankroll = 1000.0
        fraction = 0.25
        market_prob = american_to_implied_prob(odds)
        edge     = 0.10
        model_prob = market_prob + edge
        b        = 2.0
        expected_pct = edge * (b + 1) / b * fraction
        expected_stake = round(min(expected_pct, 0.05) * bankroll, 2)
        result = kelly_stake(model_prob, odds, bankroll, fraction=fraction)
        assert abs(result - expected_stake) < 0.01

    def test_below_min_pct_returns_zero(self):
        # Very tiny edge — below min_kelly_pct=0.005 → no bet
        odds = -110
        model_prob = american_to_implied_prob(odds) + 0.001
        result = kelly_stake(model_prob, odds, 1000.0, fraction=0.25, min_pct=0.005)
        assert result == 0.0

    def test_capped_at_max_pct(self):
        # Large edge should still be capped at max_pct * bankroll
        odds = 200
        model_prob = american_to_implied_prob(odds) + 0.30
        result = kelly_stake(model_prob, odds, 1000.0, fraction=1.0, max_pct=0.05)
        assert result == 50.0  # 5% of 1000


class TestKellyPct:
    def test_consistent_with_kelly_stake(self):
        """kelly_pct * bankroll should equal kelly_stake (before min/max clamps)."""
        odds     = -115
        bankroll = 1000.0
        fraction = 0.25
        model_prob = american_to_implied_prob(odds) + 0.06
        pct    = kelly_pct(model_prob, odds, fraction)
        stake  = kelly_stake(model_prob, odds, bankroll, fraction=fraction,
                             min_pct=0.0, max_pct=1.0)
        assert abs(pct * bankroll - stake) < 0.01

    def test_zero_edge(self):
        odds = -110
        assert kelly_pct(american_to_implied_prob(odds), odds) == 0.0

    def test_plus_100_known_value(self):
        # +100 -> market_prob = 100/200 = 0.5 exactly (even money), so this
        # happens to numerically match the pre-fix hardcoded-fair=0.5 case.
        # b=1.0, (b+1)/b=2.0, fraction=1.0: pct = edge * 2
        model_prob = 0.55
        pct  = kelly_pct(model_prob, 100, fraction=1.0)
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
