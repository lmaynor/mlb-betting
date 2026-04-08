"""
HR MIGRATION CELL — run once then delete this cell.
"""
import sys
sys.path.insert(0, r"C:\Users\lmayn\Downloads\mlb-betting")
sys.path.insert(0, r"C:\Users\lmayn\Downloads\mlb-betting\HR_Pro")

from mlb_core.odds import american_to_implied_prob, implied_to_american, remove_vig
from mlb_core.odds import kelly_stake, kelly_pct, fetch_dk_payloads, resolve_team, dk_to_int
from mlb_core.odds import DK_NAME_TO_ABBR, TEAM_NAME_TO_ABBREV
from mlb_core.data import load_statcast, statcast_nightly, weather_nightly, lineup_nightly
from mlb_core.models import XGBModel
from mlb_core.tracking import BetTracker
from config_hr import cfg

assert abs(american_to_implied_prob(-110) - 0.5238) < 0.001
assert resolve_team("A" + chr(39) + "s") == "OAK"
assert cfg["version"] == "v6"

print("mlb_core imports OK")
print(f"cfg version: {cfg['version']}")
print()
print("INLINE DEFS TO REMOVE:")
print("  Cell v6000006: american_to_implied_prob, implied_to_american,")
print("                 kelly_stake, build_date_list")
print("  Cell v6000007: DK_NAME_TO_ABBR, _dk_american_to_int, _dk_resolve_name")
print("  Cell v6s3code: _dk_fetch_payloads, _dk_american_to_int, _dk_resolve_name")
print("  Cell v6s4code: _dk_fetch_payloads (scrape_dk_hr - keep market logic, remove helpers)")
print("  Cell v6s12code: class HRBetTracker -> replace with BetTracker(cfg['bet_db'], system='HR')")
print()
print("REPLACE Section 0 cells (v6000003, v6000004, v6000005)")
print("with contents of: HR_Pro/section0_hr.py")