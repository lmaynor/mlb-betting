"""
NRFI MIGRATION CELL — run once then delete this cell.
Verifies mlb_core is available and cfg loads correctly.
After this passes, manually remove the inline defs listed below.
"""
import sys
sys.path.insert(0, r"C:\Users\lmayn\Downloads\mlb-betting")
sys.path.insert(0, r"C:\Users\lmayn\Downloads\mlb-betting\NRFI_Pro_System")

# 1. Verify imports
from mlb_core.odds import american_to_implied_prob, implied_to_american, remove_vig
from mlb_core.odds import kelly_stake, kelly_pct, fetch_dk_payloads, resolve_team, dk_to_int
from mlb_core.odds import DK_NAME_TO_ABBR, TEAM_NAME_TO_ABBREV
from mlb_core.data import load_statcast, statcast_nightly, weather_nightly, lineup_nightly
from mlb_core.models import XGBModel
from mlb_core.tracking import BetTracker
from config_nrfi import cfg

# 2. Spot-check values match originals
assert abs(american_to_implied_prob(-110) - 0.5238) < 0.001
assert resolve_team("Guardians") == "CLE"
assert cfg["version"] == "v17"
assert "statcast_master" in cfg

print("mlb_core imports OK")
print(f"cfg version: {cfg['version']}")
print(f"statcast_master: {cfg['statcast_master']}")
print()
print("INLINE DEFS TO REMOVE from this notebook:")
print("  Cell v17_46140: american_to_implied_prob, implied_to_american, kelly_stake,")
print("                  build_date_list, TEAM_NAME_TO_ABBREV")
print("  Cell 3494c033:  DK_NAME_TO_ABBR, DK_NRFI_URL, _dk_fetch_payloads,")
print("                  _dk_american_to_int, _dk_resolve_name")
print("  Cell v17_32:    class BetTracker (replace instantiation: BetTracker(cfg['bet_db'], system='NRFI'))")
print()
print("REPLACE Section 0 cells (v17_74570, v17_52812, v17_11471)")
print("with contents of: NRFI_Pro_System/section0_nrfi.py")