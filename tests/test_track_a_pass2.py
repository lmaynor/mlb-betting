"""Tests for the Track-A second pass:
- monitor_drift._psi_binary (exact 2-category PSI for 0/1 features; #3)
- capture_closing_lines._parse_prop_bet_type / _norm_player (CLV capture; #2)

Pure logic -- numpy/pandas only, no GCS / DB / sgo (those imports are
function-local in the modules' orchestration paths).
"""
import numpy as np

from mlb.runners.monitor_drift import _psi_binary
from mlb.runners.capture_closing_lines import _parse_prop_bet_type, _norm_player


# --- #3: binary PSI -----------------------------------------------------------

def test_psi_binary_no_shift_near_zero():
    # train P(==1)=0.3; recent also 30% ones -> PSI ~ 0
    actual = np.array([1, 1, 1, 0, 0, 0, 0, 0, 0, 0], dtype=float)
    assert _psi_binary(0.3, actual) < 0.01


def test_psi_binary_large_shift_flagged():
    # train P(==1)=0.1; recent 90% ones -> large PSI (well above 0.25)
    actual = np.array([1, 1, 1, 1, 1, 1, 1, 1, 1, 0], dtype=float)
    assert _psi_binary(0.1, actual) > 0.25


def test_psi_binary_insufficient_is_nan():
    assert np.isnan(_psi_binary(0.5, np.array([1.0])))
    assert np.isnan(_psi_binary(0.5, np.array([np.nan, np.nan])))


def test_psi_binary_never_explodes_like_gaussian():
    # The Gaussian path produced PSI ~12 for binary features; exact PSI for any
    # 0/1 proportion pair stays small/bounded for plausible shifts.
    actual = np.array([1, 0, 1, 0, 1, 0, 1, 0], dtype=float)  # 50%
    assert _psi_binary(0.45, actual) < 0.05


# --- #2: prop bet_type parsing ------------------------------------------------

def test_parse_prop_bet_type_handles_underscored_prefixes():
    assert _parse_prop_bet_type("BATTER_HITS_OVER_0.5") == ("BATTER_HITS", "OVER", 0.5)
    assert _parse_prop_bet_type("BATTER_TB_UNDER_1.5") == ("BATTER_TB", "UNDER", 1.5)
    assert _parse_prop_bet_type("PITCHER_ER_OVER_2.5") == ("PITCHER_ER", "OVER", 2.5)


def test_parse_prop_bet_type_rejects_non_props():
    assert _parse_prop_bet_type("K_OVER_7.5") is None       # handled by its own branch
    assert _parse_prop_bet_type("OUTS_UNDER_17.5") is None
    assert _parse_prop_bet_type("1I_AWAY") is None
    assert _parse_prop_bet_type("HR") is None
    assert _parse_prop_bet_type("NRFI") is None


def test_norm_player_folds_accents_and_case():
    assert _norm_player("José Ramírez") == "jose ramirez"
    assert _norm_player("  Aaron Judge ") == "aaron judge"
    assert _norm_player(None) == "none"  # str(None) -- callers pass "" not None
