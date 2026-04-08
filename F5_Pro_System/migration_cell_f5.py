"""
F5 MIGRATION CELL — run once then delete this cell.
"""
import sys
sys.path.insert(0, r"C:\Users\lmayn\Downloads\mlb-betting")
sys.path.insert(0, r"C:\Users\lmayn\Downloads\mlb-betting\F5_Pro_System")

from mlb_core.odds import american_to_implied_prob, implied_to_american, remove_vig
from mlb_core.odds import kelly_stake, kelly_pct, fetch_dk_payloads, resolve_team, dk_to_int
from mlb_core.odds import DK_NAME_TO_ABBR, TEAM_NAME_TO_ABBREV
from mlb_core.data import load_statcast, statcast_nightly, weather_nightly, lineup_nightly
from mlb_core.models import XGBModel
from mlb_core.tracking import BetTracker
from config_f5 import cfg, SEASON_WEIGHTS

assert abs(american_to_implied_prob(+150) - 0.4000) < 0.001
assert cfg["version"] == "v4"
assert 2024 in SEASON_WEIGHTS

print("mlb_core imports OK")
print(f"cfg version: {cfg['version']}")
print()
print("INLINE DEFS TO REMOVE:")
print("  Cell f5v3_0c_001: american_to_implied_prob, kelly_stake, remove_vig,")
print("                    build_date_list, DK_NAME_TO_ABBR, TEAM_NAME_TO_ABBREV, _dk_resolve_name")
print("  Cell f5v3_05_001: _dk_resolve_name (in scraper section)")
print("  Cell f5v3_11_001: class BetTracker -> replace with BetTracker(cfg['bet_db'], system='F5')")
print()
print("REPLACE Section 0 cells (f5v3_0a_001, f5v3_0a_002, f5v3_0b_001)")
print("with contents of: F5_Pro_System/section0_f5.py")