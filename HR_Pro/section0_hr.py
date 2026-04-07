# ── Section 0: Config & Imports (mlb_core edition) ────────────────────────
import os, sys, json, time, random, re, pickle, sqlite3, warnings, unicodedata
from datetime import datetime, date, timedelta
from pathlib import Path
from io import StringIO

import pandas as pd
import numpy as np
import xgboost as xgb
import requests
import matplotlib.pyplot as plt

try:
    from tqdm.auto import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

warnings.filterwarnings("ignore")
pd.set_option("display.max_columns", None)
pd.set_option("display.width", 160)
try: plt.style.use("seaborn-v0_8-darkgrid")
except: plt.style.use("ggplot")

# ── mlb_core imports ──────────────────────────────────────────────────────
sys.path.insert(0, r"C:\Users\lmayn\Downloads\mlb-betting")

from mlb_core.odds import (
    american_to_implied_prob, implied_to_american,
    remove_vig, kelly_stake, kelly_pct,
    fetch_dk_payloads, resolve_team, dk_to_int,
    DK_NAME_TO_ABBR, TEAM_NAME_TO_ABBREV,
)
from mlb_core.data import load_statcast, statcast_nightly, weather_nightly, lineup_nightly
from mlb_core.models import XGBModel
from mlb_core.tracking import BetTracker

# ── System config ─────────────────────────────────────────────────────────
sys.path.insert(0, r"C:\Users\lmayn\Downloads\mlb-betting\HR_Pro")
from config_hr import cfg

# ── Aliases ───────────────────────────────────────────────────────────────
TEAM_NAME_TO_ABBR = TEAM_NAME_TO_ABBREV
DK_NAME_TO_ABBREV = DK_NAME_TO_ABBR
BASE_DIR          = cfg["base_dir"]
BANKROLL          = cfg["BANKROLL"]
PAPER             = cfg["PAPER"]

data  = {}
model = {}

tracker = BetTracker(cfg["bet_db"], system="HR")

print(f"HR Pro {cfg['version']} | mlb_core loaded | {datetime.now().strftime('%Y-%m-%d %H:%M')}")