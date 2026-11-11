"""nba.minutes_restriction.features -- the Arm S model matrix: structured features only, all frozen
at decision time.

Every column here is knowable at the decision report (tip - 60 min):

  * baseline_minutes, games_missed, log_games_missed -- arithmetic on box scores before the game;
  * designation one-hots -- the official-report status at decision time (missing = not listed);
  * injury one-hots -- injury_class (report reason_cat, falling back to the absence class);
  * is_back_to_back, is_game_two, case-class flags -- schedule / case construction facts.

Deliberately EXCLUDED: `started`. The box score's starter flag is only published with the starting
lineups (~30 min pre-tip, after decision time), so using it would leak post-decision information
into a supposedly decision-time model. It stays on the case frame as a descriptive column only.
Also excluded: season / date (the walk-forward split carries time; the model must not memorize it)
and anything derived from the case game's own box score.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DESIGNATIONS = ("Available", "Probable", "Questionable", "Doubtful", "Out")

# Fixed injury-class vocabulary: official-report reason_cat values (nba_injury_report) plus the
# box-score absence classes (lowercased) the fallback can produce. A value outside the vocabulary
# maps to inj_other -- the vocabulary never grows from test data, so train/test matrices align.
INJURY_VOCAB = (
    "injury_illness", "injury_management", "injury", "illness", "return_to_competition",
    "rest", "personal", "g_league", "not_with_team", "suspension", "coach", "unknown",
    "unlisted", "none", "other",
)

_NUMERIC = ["baseline_minutes", "games_missed", "log_games_missed"]
_FLAGS = ["is_back_to_back", "is_game_two", "is_return", "is_designation"]
_DESIG = [f"desig_{d.lower()}" for d in DESIGNATIONS] + ["desig_missing"]
_INJ = [f"inj_{v}" for v in INJURY_VOCAB]

FEATURE_COLUMNS = _NUMERIC + _FLAGS + _DESIG + _INJ


def build_features(cases: pd.DataFrame) -> pd.DataFrame:
    """Case frame -> float feature matrix with FEATURE_COLUMNS, row-aligned with `cases`."""
    if cases is None or not len(cases):
        return pd.DataFrame(columns=FEATURE_COLUMNS, dtype=float)
    out = pd.DataFrame(index=cases.index)
    out["baseline_minutes"] = pd.to_numeric(cases["baseline_minutes"], errors="coerce").astype(float)
    missed = pd.to_numeric(cases["games_missed"], errors="coerce").fillna(0).astype(float)
    out["games_missed"] = missed
    out["log_games_missed"] = np.log1p(missed)
    out["is_back_to_back"] = cases["is_back_to_back"].fillna(False).astype(bool).astype(float)
    out["is_game_two"] = cases["is_game_two"].fillna(False).astype(bool).astype(float)
    out["is_return"] = (cases["case_class"] == "return").astype(float)
    out["is_designation"] = (cases["case_class"] == "designation").astype(float)

    desig = cases["designation"]
    for status, col in zip(DESIGNATIONS, _DESIG):
        out[col] = (desig == status).astype(float)
    out["desig_missing"] = (~desig.isin(DESIGNATIONS)).astype(float)

    inj = cases["injury_class"].map(lambda v: str(v).strip().lower() if isinstance(v, str) else "none")
    inj = inj.where(inj.isin(INJURY_VOCAB), "other")
    for vocab, col in zip(INJURY_VOCAB, _INJ):
        out[col] = (inj == vocab).astype(float)

    return out[FEATURE_COLUMNS].astype(float)
