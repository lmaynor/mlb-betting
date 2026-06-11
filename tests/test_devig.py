"""
tests/test_devig.py -- two-way de-vig methods (Task #4 building block).

Proportional (current), Shin, and log/power de-vig. All must remove the
over-round (sum to 1); Shin and log apply a favorite-longshot correction
(shade the favorite differently from proportional).

Imports the functions directly from the module file to avoid the package
__init__ pulling optional deps (requests) in the local test env.
"""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_odds_utils", str(Path(__file__).resolve().parents[1] / "mlb_core" / "odds" / "utils.py")
)
u = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(u)


# Favorite -180 / dog +160 -> over-round ~1.027
QF, QD = 180 / 280, 100 / 260


class TestNormalization:
    def test_proportional_sums_to_one(self):
        a, b = u.remove_vig(QF, QD)
        assert abs(a + b - 1.0) < 1e-6

    def test_shin_sums_to_one(self):
        a, b = u.shin_two_way(QF, QD)
        assert abs(a + b - 1.0) < 1e-6

    def test_log_sums_to_one(self):
        a, b = u.log_two_way(QF, QD)
        assert abs(a + b - 1.0) < 1e-6


class TestFavoriteLongshot:
    def test_shin_shades_favorite_up_vs_proportional(self):
        pf, _ = u.remove_vig(QF, QD)
        sf, _ = u.shin_two_way(QF, QD)
        assert sf > pf  # Shin gives the favorite more true prob

    def test_log_shades_favorite_up_vs_proportional(self):
        pf, _ = u.remove_vig(QF, QD)
        lf, _ = u.log_two_way(QF, QD)
        assert lf > pf


class TestEdgeCases:
    def test_no_vig_just_normalizes(self):
        # sum <= 1 -> normalization only, all methods agree
        a1, b1 = u.remove_vig(0.4, 0.5)
        a2, b2 = u.shin_two_way(0.4, 0.5)
        assert abs(a1 - a2) < 1e-6 and abs(b1 - b2) < 1e-6

    def test_method_selector(self):
        assert u.devig_two_way(QF, QD, "proportional") == u.remove_vig(QF, QD)
        assert u.devig_two_way(QF, QD, "shin") == u.shin_two_way(QF, QD)
        assert u.devig_two_way(QF, QD, "log") == u.log_two_way(QF, QD)

    def test_nan_inputs(self):
        import math
        a, b = u.shin_two_way(float("nan"), 0.5)
        assert math.isnan(a)
