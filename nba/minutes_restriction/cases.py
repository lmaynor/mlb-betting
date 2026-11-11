"""nba.minutes_restriction.cases -- case construction for the Arm S minutes-restriction backtest.

Inputs are the two banked frames of the pre-registered design (spike sections 1-2, 4):

  * box scores: the hoopR mirror (NBA/hoopr/player_boxscores/, see nba/data/hoopr.py) -- one row per
    player per game, played or not, carrying minutes / did_not_play / reason / starter /
    game_date_time (the real ET tip). Labels and absence spells come from here alone.
  * designations: the official NBA injury report, parsed (nba/data/nba_injury_report.py COLUMNS:
    report_ts_et, game_date, game_time_et, matchup, team, team_code, player_raw, player, status,
    reason, reason_cat). Optional: without it only box-score return cases are built.

Definitions (pre-registered; do not soften):

  * A player-team STINT is a maximal run of played games for one team. An ABSENCE SPELL is the run of
    team games between two consecutive played games of the stint in which the player did not play
    (a DNP row, or no row at all -- ESPN omits many inactives; an unlisted missed game classifies
    UNLISTED). A spell must be BOUNDED by played games on both sides: trailing absences after a
    player's last game for a team (trade, release, season end) are never spells.
  * BASELINE = mean minutes over the last BASELINE_WINDOW (8) played games before the spell, within
    the stint; at least BASELINE_MIN_GAMES (5) played games are required. ROTATION = baseline >= 15.
  * Absence class = MODAL box-score reason class over the spell's DNP rows (classify_absence_reason,
    mirroring nba.props.espn_data.classify_reason); a spell with no DNP row at all is UNLISTED.
  * Case class (i) RETURN: the played game closing a spell of >= 2 missed games whose modal class is
    injury (INJURY or ILLNESS -- the official report lumps both under Injury/Illness). Case class
    (ii) DESIGNATION: a played game whose decision-time designation is Questionable or Probable.
    A game that qualifies as a return case is a return case (its designation is a feature, not a
    second case). GAME-2: the next played game after a return game, when the player plays the team's
    very next game -- emitted as a secondary cohort (case_class "game_two", is_game_two True) with
    the return case's baseline and spell, because restrictions persist into game 2.
  * COACH_DECISION EXCLUSION: a spell whose modal class is COACH is a role loss, not an injury
    return. No case of any class is built on such a spell (the spike's section-2 trap).
  * DECISION TIME: the last official report published at least DECISION_LEAD_MIN (60) minutes
    before tip. select_designation enforces the cutoff: a report inside the final hour NEVER
    contributes features, however late the status change. This is the harness's central honesty
    property and is pinned by tests.
"""
from __future__ import annotations

import re
from collections import Counter

import pandas as pd

from nba.teams import team_code

BASELINE_WINDOW = 8
BASELINE_MIN_GAMES = 5
ROTATION_MIN_MPG = 15.0
DECISION_LEAD_MIN = 60
MIN_RETURN_MISSED = 2
DESIGNATION_CASE_STATUSES = ("Questionable", "Probable")
_ET = "America/New_York"

CASE_COLUMNS = [
    "case_id", "game_id", "player_id", "player", "team", "opponent", "season", "season_start",
    "season_label", "game_date", "tip_ts", "decision_report_ts", "designation", "report_reason",
    "reason_cat", "games_missed", "missed_band", "baseline_minutes", "realized_minutes",
    "realized_ratio", "case_class", "is_game_two", "is_injury_return", "absence_reason",
    "absence_reason_class", "injury_class", "injury_location", "injury_side", "is_back_to_back",
    "started",
]

# -----------------------------------------------------------------------------------------------
# Absence reason classification. Mirrors nba.props.espn_data.classify_reason rule-for-rule (the
# box-score DNP reason vocabulary is the same ESPN free text); a parity test in
# tests/test_minutes_arm_s.py pins the two implementations together so they cannot drift.
_CATEGORY_RULES = (
    ("COACH", re.compile(r"COACH'?S? DECISION")),
    ("SUSPENSION", re.compile(r"SUSPEND")),
    ("NOT_WITH_TEAM", re.compile(r"NOT WITH TEAM|G LEAGUE|TWO[- ]WAY|ASSIGN")),
    ("PERSONAL", re.compile(r"PERSONAL|BEREAVEMENT|FAMILY|PATERNITY|BIRTH")),
    ("REST", re.compile(r"^REST$|LOAD MANAGEMENT|^RESTING")),
    ("ILLNESS", re.compile(r"ILLNESS|VIRUS|SICK|FLU\b|COVID")),
    ("UNKNOWN", re.compile(r"DID NOT DRESS|^NAN$|^NONE$|^$")),
)

INJURY_ABSENCE_CLASSES = ("INJURY", "ILLNESS")
COACH_CLASS = "COACH"
UNLISTED_CLASS = "UNLISTED"
NO_SPELL_CLASS = "NONE"


def classify_absence_reason(reason) -> str:
    """Free-text ESPN DNP reason -> COACH | SUSPENSION | NOT_WITH_TEAM | PERSONAL | REST | ILLNESS |
    UNKNOWN | INJURY. Everything unmatched is a body part / injury. A missing reason is UNKNOWN."""
    s = "" if reason is None or (isinstance(reason, float) and pd.isna(reason)) else str(reason).strip().upper()
    for name, rx in _CATEGORY_RULES:
        if rx.search(s):
            return name
    return "INJURY"


_LOCATIONS = (
    "achilles", "ankle", "back", "calf", "concussion", "elbow", "foot", "groin", "hamstring",
    "hand", "hip", "knee", "neck", "quadriceps", "quad", "shoulder", "toe", "wrist", "illness",
)


def parse_injury_detail(reason_text) -> tuple:
    """Light parse of a reason string -> (location, side). Deliberately shallow: the raw reason and
    its category are kept on the case; this only surfaces a body part / side when the text states
    one plainly ("Injury/Illness - Left Knee; ...", "LEFT ANKLE SPRAIN")."""
    if reason_text is None or (isinstance(reason_text, float) and pd.isna(reason_text)):
        return None, None
    s = str(reason_text).lower()
    location = next((loc for loc in _LOCATIONS if loc in s), None)
    if location == "quadriceps":
        location = "quad"
    side = "left" if re.search(r"\bleft\b", s) else ("right" if re.search(r"\bright\b", s) else None)
    return location, side


def missed_band(games_missed: int) -> str:
    """The pre-registered missed bands: 0, 1, 2-3, 4-9, 10+."""
    m = int(games_missed)
    if m <= 0:
        return "0"
    if m == 1:
        return "1"
    if m <= 3:
        return "2-3"
    if m <= 9:
        return "4-9"
    return "10+"


def season_label(season_start: int) -> str:
    """2021 -> '2021-22'."""
    return f"{int(season_start)}-{str(int(season_start) + 1)[-2:]}"


def _norm_name(name) -> str:
    """Name key for the box <-> report join: case/punctuation/suffix folded, whitespace collapsed."""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    s = re.sub(r"[^a-z0-9 ]", " ", str(name).lower().replace(".", " "))
    parts = [p for p in s.split() if p not in ("jr", "sr", "ii", "iii", "iv")]
    return " ".join(parts)


# -----------------------------------------------------------------------------------------------
# Decision-time designation selection (the honesty core)

def _reports_normalized(reports: pd.DataFrame) -> pd.DataFrame:
    """Designation frame (nba_injury_report COLUMNS) -> typed frame with ET report timestamps."""
    df = reports.copy()
    df["_report_ts"] = pd.to_datetime(df["report_ts_et"], utc=True).dt.tz_convert(_ET)
    df["_game_date"] = pd.to_datetime(df["game_date"]).dt.date
    df["_name_key"] = df["player"].map(_norm_name)
    return df


def select_designation(reports_norm: pd.DataFrame, *, player: str, team: str, game_date,
                       tip_ts, lead_min: float = DECISION_LEAD_MIN):
    """The decision-time designation for one player-game, or None when no eligible report lists him.

    Eligible = reports for (player, team_code, game_date) stamped at or before tip - lead_min.
    The LAST eligible report wins; anything published inside the lead window is invisible here by
    construction. Returns {designation, decision_report_ts, report_reason, reason_cat}.
    `reports_norm` must already carry _report_ts / _game_date / _name_key (see _reports_normalized);
    build_cases prepares it once.
    """
    if reports_norm is None or not len(reports_norm):
        return None
    tip = pd.Timestamp(tip_ts)
    tip = tip.tz_convert(_ET) if tip.tzinfo is not None else tip.tz_localize(_ET)
    cutoff = tip - pd.Timedelta(minutes=float(lead_min))
    cand = reports_norm[
        (reports_norm["_name_key"] == _norm_name(player))
        & (reports_norm["team_code"] == team)
        & (reports_norm["_game_date"] == game_date)
        & (reports_norm["_report_ts"] <= cutoff)
    ]
    if not len(cand):
        return None
    latest = cand["_report_ts"].max()
    row = cand[cand["_report_ts"] == latest].iloc[-1]
    return {
        "designation": row["status"],
        "decision_report_ts": latest,
        "report_reason": None if pd.isna(row["reason"]) else row["reason"],
        "reason_cat": None if pd.isna(row["reason_cat"]) else row["reason_cat"],
    }


# -----------------------------------------------------------------------------------------------
# Box-score normalization

_NAME_COLUMNS = ("athlete_display_name", "athlete_name", "player", "name")


def _normalize_box(box: pd.DataFrame) -> pd.DataFrame:
    """hoopR player box -> typed working frame (one row per player-game, played or DNP)."""
    df = box.copy()
    name_col = next((c for c in _NAME_COLUMNS if c in df.columns), None)
    if name_col is None or "game_id" not in df.columns:
        raise ValueError("box frame needs game_id and a player name column "
                         f"(one of {_NAME_COLUMNS}); got {list(df.columns)}")
    if "athlete_id" in df.columns:
        df["_player_id"] = pd.to_numeric(df["athlete_id"], errors="coerce")
    else:
        df["_player_id"] = pd.factorize(df[name_col].astype(str))[0].astype(float)
    df["_player"] = df[name_col].astype(str)
    raw_team = df["team_abbreviation"].astype(str) if "team_abbreviation" in df.columns else ""
    df["_team"] = [team_code(t) or str(t).strip().upper() for t in raw_team] \
        if "team_abbreviation" in df.columns else None
    if "game_date_time" in df.columns:
        tips = pd.to_datetime(df["game_date_time"], utc=True).dt.tz_convert(_ET)
    else:
        tips = pd.to_datetime(df["game_date"]).dt.tz_localize(_ET) + pd.Timedelta(hours=19)
    df["_tip"] = tips
    if "game_date" in df.columns:
        df["_date"] = pd.to_datetime(df["game_date"]).dt.date
    else:
        df["_date"] = df["_tip"].dt.date
    if "season" in df.columns:                       # hoopR season = year the season ends
        df["_season"] = pd.to_numeric(df["season"], errors="coerce")
        df["_season_start"] = df["_season"] - 1
    else:
        months = df["_tip"].dt.month
        years = df["_tip"].dt.year
        end_year = [y + 1 if m >= 8 else y for y, m in zip(years, months)]
        df["_season"] = end_year
        df["_season_start"] = [y - 1 for y in end_year]
    if "did_not_play" in df.columns:
        df["_played"] = ~df["did_not_play"].fillna(False).astype(bool)
    else:
        df["_played"] = pd.to_numeric(df["minutes"], errors="coerce").fillna(0) > 0
    df["_minutes"] = pd.to_numeric(df["minutes"], errors="coerce")
    df["_starter"] = df["starter"].fillna(False).astype(bool) if "starter" in df.columns else False
    df["_reason"] = df["reason"] if "reason" in df.columns else None
    # One row per player-game: if a played and a DNP row ever collide, the played row is the truth.
    df = df.sort_values("_played").drop_duplicates(["_player_id", "game_id"], keep="last")
    return df.reset_index(drop=True)


def _modal(values) -> object:
    """Most common value (ties: first seen in spell order -- deterministic); None when empty."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


# -----------------------------------------------------------------------------------------------
# Case construction

def build_cases(box: pd.DataFrame, reports: pd.DataFrame | None = None, *,
                baseline_window: int = BASELINE_WINDOW, baseline_min: int = BASELINE_MIN_GAMES,
                rotation_mpg: float = ROTATION_MIN_MPG, lead_min: float = DECISION_LEAD_MIN,
                regular_season_only: bool = True) -> pd.DataFrame:
    """Box scores (+ optional official-report designations) -> the CASE_COLUMNS case frame.

    Pure and offline: no I/O, no clock. See the module docstring for the pre-registered definitions.
    Only regular-season games build cases by default (hoopR season_type 2; play-in 5 counts as
    regular-season for spell purposes when season_type is present).
    """
    if box is None or not len(box):
        return pd.DataFrame(columns=CASE_COLUMNS)
    df = _normalize_box(box)
    if regular_season_only and "season_type" in df.columns:
        types = pd.to_numeric(df["season_type"], errors="coerce")
        if types.isin([1, 2, 3, 5]).any():
            df = df[types.isin([2, 5])].reset_index(drop=True)
    if not len(df):
        return pd.DataFrame(columns=CASE_COLUMNS)
    reports_norm = _reports_normalized(reports) if reports is not None and len(reports) else None

    # Team calendar: ordered games per team, the previous game (back-to-back), and the opponent.
    cal = (df.drop_duplicates(["_team", "game_id"])
             .sort_values(["_team", "_tip", "game_id"])
             .reset_index(drop=True))
    team_games: dict = {}
    prev_game_date: dict = {}
    opponent_of: dict = {}
    game_teams = df.drop_duplicates(["game_id", "_team"]).groupby("game_id")["_team"].apply(list)
    for team, g in cal.groupby("_team", sort=False):
        games = list(zip(g["game_id"], g["_date"], g["_tip"]))
        team_games[team] = games
        for i, (gid, gdate, _tip) in enumerate(games):
            prev_game_date[(team, gid)] = games[i - 1][1] if i else None
            others = [t for t in game_teams.get(gid, []) if t != team]
            opponent_of[(team, gid)] = others[0] if others else None
    game_pos = {(team, gid): i for team, games in team_games.items() for i, (gid, _d, _t) in enumerate(games)}

    dnp = df[~df["_played"]]
    dnp_reason = {(float(pid), int(gid)): reason
                  for pid, gid, reason in zip(dnp["_player_id"], dnp["game_id"], dnp["_reason"])
                  if reason is not None and not (isinstance(reason, float) and pd.isna(reason))}

    def spell_between(player_id, team, g_before, g_after):
        """Team games strictly between two played games -> (n_missed, modal reason, modal class)."""
        games = team_games.get(team, [])
        i, j = game_pos.get((team, g_before)), game_pos.get((team, g_after))
        if i is None or j is None or j <= i + 1:
            return 0, None, NO_SPELL_CLASS
        missed = games[i + 1:j]
        reasons, classes = [], []
        listed = 0
        for gid, _d, _t in missed:
            reason = dnp_reason.get((player_id, gid))
            if reason is not None:
                listed += 1
                reasons.append(reason)
                classes.append(classify_absence_reason(reason))
        if not listed:
            return len(missed), None, UNLISTED_CLASS
        return len(missed), _modal(reasons), _modal(classes)

    def baseline_of(played_minutes):
        """Mean of the last `baseline_window` played minutes, or None when history is too short."""
        if len(played_minutes) < baseline_min:
            return None
        window = played_minutes[-baseline_window:]
        return float(sum(window) / len(window))

    def designation_for(player, team, game_date, tip):
        if reports_norm is None:
            return {"designation": None, "decision_report_ts": pd.NaT,
                    "report_reason": None, "reason_cat": None}
        found = select_designation(reports_norm, player=player, team=team,
                                   game_date=game_date, tip_ts=tip, lead_min=lead_min)
        if found is None:
            return {"designation": None, "decision_report_ts": pd.NaT,
                    "report_reason": None, "reason_cat": None}
        return found

    rows: list = []

    def emit(rec, *, case_class, games_missed, absence_reason, absence_class, baseline, is_game_two):
        desig = designation_for(rec["_player"], rec["_team"], rec["_date"], rec["_tip"])
        reason_text = desig["report_reason"] if desig["report_reason"] else absence_reason
        location, side = parse_injury_detail(reason_text)
        realized = float(rec["_minutes"]) if pd.notna(rec["_minutes"]) else 0.0
        rows.append({
            "case_id": f"{int(rec['game_id'])}-{int(rec['_player_id'])}-{case_class}",
            "game_id": int(rec["game_id"]),
            "player_id": int(rec["_player_id"]),
            "player": rec["_player"],
            "team": rec["_team"],
            "opponent": opponent_of.get((rec["_team"], rec["game_id"])),
            "season": int(rec["_season"]),
            "season_start": int(rec["_season_start"]),
            "season_label": season_label(rec["_season_start"]),
            "game_date": rec["_date"],
            "tip_ts": rec["_tip"],
            "decision_report_ts": desig["decision_report_ts"],
            "designation": desig["designation"],
            "report_reason": desig["report_reason"],
            "reason_cat": desig["reason_cat"],
            "games_missed": int(games_missed),
            "missed_band": missed_band(games_missed),
            "baseline_minutes": float(baseline),
            "realized_minutes": realized,
            "realized_ratio": realized / float(baseline) if baseline else None,
            "case_class": case_class,
            "is_game_two": bool(is_game_two),
            "is_injury_return": absence_class in INJURY_ABSENCE_CLASSES,
            "absence_reason": absence_reason,
            "absence_reason_class": absence_class,
            "injury_class": desig["reason_cat"] if desig["reason_cat"] else absence_class,
            "injury_location": location,
            "injury_side": side,
            "is_back_to_back": bool(
                prev_game_date.get((rec["_team"], rec["game_id"])) is not None
                and (rec["_date"] - prev_game_date[(rec["_team"], rec["game_id"])]).days == 1),
            "started": bool(rec["_starter"]),
        })

    for player_id, pdf in df[df["_played"]].sort_values(["_tip", "game_id"]).groupby("_player_id", sort=False):
        played = pdf.to_dict("records")
        # Stints: maximal same-team runs of played games.
        stints: list = []
        for rec in played:
            if stints and stints[-1][0]["_team"] == rec["_team"]:
                stints[-1].append(rec)
            else:
                stints.append([rec])
        for stint in stints:
            team = stint[0]["_team"]
            minutes_hist: list = []
            cased_games: set = set()
            pending_game_two = None          # (return baseline, spell) awaiting the next played game
            for idx, rec in enumerate(stint):
                spell = None
                if idx > 0:
                    prev = stint[idx - 1]
                    spell = spell_between(player_id, team, prev["game_id"], rec["game_id"])
                n_missed, absence_reason, absence_class = spell if spell else (0, None, NO_SPELL_CLASS)
                baseline = baseline_of(minutes_hist)

                is_return = (
                    spell is not None and n_missed >= MIN_RETURN_MISSED
                    and absence_class in INJURY_ABSENCE_CLASSES
                    and baseline is not None and baseline >= rotation_mpg
                )
                if is_return:
                    emit(rec, case_class="return", games_missed=n_missed,
                         absence_reason=absence_reason, absence_class=absence_class,
                         baseline=baseline, is_game_two=False)
                    cased_games.add(rec["game_id"])
                    pending_game_two = (baseline, n_missed, absence_reason, absence_class)
                elif pending_game_two is not None and n_missed == 0:
                    base2, missed2, reason2, class2 = pending_game_two
                    emit(rec, case_class="game_two", games_missed=missed2,
                         absence_reason=reason2, absence_class=class2,
                         baseline=base2, is_game_two=True)
                    cased_games.add(rec["game_id"])
                    pending_game_two = None
                else:
                    pending_game_two = None
                    # Designation class (ii): dressed + Questionable/Probable at decision time,
                    # never on a COACH_DECISION (role-loss) spell.
                    if (reports_norm is not None and rec["game_id"] not in cased_games
                            and absence_class != COACH_CLASS
                            and baseline is not None and baseline >= rotation_mpg):
                        desig = designation_for(rec["_player"], team, rec["_date"], rec["_tip"])
                        if desig["designation"] in DESIGNATION_CASE_STATUSES:
                            emit(rec, case_class="designation", games_missed=n_missed,
                                 absence_reason=absence_reason, absence_class=absence_class,
                                 baseline=baseline, is_game_two=False)
                            cased_games.add(rec["game_id"])
                minutes_hist.append(float(rec["_minutes"]) if pd.notna(rec["_minutes"]) else 0.0)

    if not rows:
        return pd.DataFrame(columns=CASE_COLUMNS)
    out = pd.DataFrame(rows, columns=CASE_COLUMNS)
    return out.sort_values(["game_date", "case_id"]).reset_index(drop=True)
