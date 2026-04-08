"""
K Pro MIGRATION CELL — run once then delete this cell.
"""
import sys
sys.path.insert(0, r"C:\Users\lmayn\Downloads\mlb-betting")
sys.path.insert(0, r"C:\Users\lmayn\Downloads\mlb-betting\K_Pro_System")

from mlb_core.odds import american_to_implied_prob, implied_to_american, remove_vig
from mlb_core.odds import kelly_stake, kelly_pct, fetch_dk_payloads, resolve_team, dk_to_int
from mlb_core.odds import DK_NAME_TO_ABBR, TEAM_NAME_TO_ABBREV
from mlb_core.data import load_statcast, statcast_nightly, weather_nightly, lineup_nightly
from mlb_core.models import XGBModel
from mlb_core.tracking import BetTracker
from config_k import cfg

assert cfg["version"] == "v1"
assert cfg["monte_carlo_n"] == 10000

print("mlb_core imports OK")
print(f"cfg version: {cfg['version']}")
print()
print("INLINE DEFS TO REMOVE: same pattern as other systems")
print("REPLACE Section 0 with contents of: K_Pro_System/section0_k.py")