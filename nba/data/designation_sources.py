"""Alternate designation sources aligned to the official report's frame (nba.data.nba_injury_report.COLUMNS).

THE SOURCE STORY (which source is authoritative for what):

  * OFFICIAL PDF (nba/data/nba_injury_report.py) -- the historical + decision-time source of truth. The league's
    own per-version reports, every version preserved with its `report_ts_et`, the raw PDF banked beside the parse
    under NBA/injuries/official/. This is the ONLY source that can answer "what was the designation at time t"
    for a past game, so it stays primary.
  * ESPN LIVE LIST (nba/data/injury_bank.py) -- the going-forward live source. ESPN's list is live-only (no
    history), so injury_bank banks change-only snapshots under NBA/injuries/espn/ as they are polled;
    `espn_snapshot_to_designations` below aligns one of those snapshots to the designation COLUMNS so live and
    historical frames can be compared row-by-row. It is NOT a substitute for the official archive: an ESPN
    snapshot is league-wide (no game, no matchup, no tip time), its timestamps are poll times rather than
    league publication times, and its status vocabulary has two values the official report never prints
    (Day-To-Day, Other -- passed through unchanged, never silently remapped).
  * SPORTSDATAVERSE -- a republication of the SAME official reports as per-version rows. An adapter interface
    is provided (`sportsdataverse_to_designations`) but deliberately NOT implemented yet; see its docstring.

DESIGN RULES THAT MUST NOT DRIFT (from nba_injury_report): every report version is preserved with its
report_ts_et (never collapse to latest); a team absent from an OFFICIAL report means "nothing reported",
never "healthy" (the ESPN rule is the opposite -- absent means healthy ONLY when the fetch was ok -- and the
adapter below does not change either rule: it converts rows that exist, it never invents rows for absent teams).
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

from nba.config import TIMEZONE
from nba.data import nba_injury_report as R
from nba.teams import NAME_OF, team_code

_ET = ZoneInfo(TIMEZONE)

# ----------------------------------------------------------------------------------------------- SportsDataverse
# Columns of the SportsDataverse NBA injury-report release that its public release notes DO document
# (github.com/sportsdataverse/sportsdataverse-data releases, read 2026-11-11): one row per listing per
# published version of the official report.
SDV_DOCUMENTED_COLUMNS = (
    "report_datetime",      # US/Eastern, naive -- the per-version stamp (== official report_ts_et)
    "current_status",       # the printed status: Available / Probable / Questionable / Doubtful / Out
    "report_player_name",   # null on a NOT YET SUBMITTED team row (that row's reason is "NOT YET SUBMITTED")
    "reason",
    "game_id",              # joined from the schedule; null for postponed / unneeded playoff games
    "team_id",
    "person_id",            # matched by name on the team (boxscores, then rosters)
    "match_method",         # exact / family / league / roster, null when unmatched
)


def sportsdataverse_to_designations(raw: pd.DataFrame) -> pd.DataFrame:
    """SportsDataverse injury-report rows -> designation frame (nba_injury_report.COLUMNS). NOT IMPLEMENTED.

    Why the interface ships without an implementation: the release notes document the columns in
    SDV_DOCUMENTED_COLUMNS, but they do not pin the release's full schema -- in particular the exact
    game-date / game-time / matchup / team-name columns the designation frame carries have not been
    verified against a published file, and guessing column names would produce a silently wrong adapter
    (the failure mode this codebase fails closed against everywhere else). Pinning the schema takes one
    read of a published season file's header; until that is done this raises rather than guesses.

    Why the official PDF stays primary even once this is implemented: SportsDataverse is a republication
    of the same league reports, one step removed from the source, with ids matched by name upstream
    (match_method admits misses). The official path banks the RAW PDF beside every parse, so any parse
    can be re-derived and audited; a republication cannot offer that. The adapter's role is a cross-check
    and a convenience mirror, never the source of truth.

    Expected mapping once the schema is pinned: report_datetime (ET, naive) -> report_ts_et (ET, aware
    ISO); current_status -> status, with a null report_player_name + reason "NOT YET SUBMITTED" row ->
    status NOT_SUBMITTED (team flag, player None); report_player_name -> player (and player_raw, modulo
    the source's name format); reason -> reason (+ reason_cat via nba_injury_report.reason_category);
    team identity -> team + team_code via nba.teams; game columns from the pinned game columns.
    """
    raise NotImplementedError(
        "SportsDataverse designation adapter is not implemented: the release's full column schema is not "
        "pinned (see SDV_DOCUMENTED_COLUMNS and the docstring). The official PDF path "
        "(nba.data.nba_injury_report) is the supported source.")


def fetch_sportsdataverse(seasons, session=None) -> pd.DataFrame:
    """Fetch SportsDataverse injury-report rows for hoopR `seasons` (end years). NOT IMPLEMENTED -- see
    sportsdataverse_to_designations. Raises rather than fetching anything."""
    raise NotImplementedError(
        "SportsDataverse designation fetch is not implemented: see sportsdataverse_to_designations. "
        "Use --source official-pdf.")


# ----------------------------------------------------------------------------------------------- ESPN live snapshots
# ESPN statuses that are spelled exactly as the official report spells them pass through. ESPN-only values
# (Day-To-Day, Other) ALSO pass through unchanged and are flagged here so a consumer mixing frames sees
# them coming; remapping Day-To-Day to Questionable would invent precision ESPN does not claim.
ESPN_ONLY_STATUSES = ("Day-To-Day", "Other")


def espn_snapshot_to_designations(frame: pd.DataFrame) -> pd.DataFrame:
    """One banked ESPN snapshot (nba.data.injury_bank frame: espn_injuries.COLUMNS + fetched_at_utc) ->
    designation frame (nba_injury_report.COLUMNS, plus `espn_id` carried from espn_player_id when present,
    mirroring what nba_injury_report.resolve_ids adds to an official frame).

    Mapping (everything ESPN does not know stays None -- never invented):
      report_ts_et <- fetched_at_utc converted to ET (the poll time: when WE saw the list, the closest
                      honest decision-time stamp a live-only source has)
      game_date    <- the ET date of fetched_at_utc; game_time_et / matchup <- None (league-wide list)
      team         <- NAME_OF[team_code];  team_code <- team_abbrev (validated via nba.teams.team_code;
                      rows whose team cannot be validated are dropped and counted in attrs, fail closed)
      player_raw   <- player (ESPN prints "First Last"; the official report prints "Last, First" --
                      player_raw preserves each source's own raw form);  player <- player
      status       <- status, unchanged (see ESPN_ONLY_STATUSES)
      reason       <- detail;  reason_cat <- nba_injury_report.reason_category(detail)

    Raises ValueError when the frame has no fetched_at_utc column: without a fetch time there is no
    report_ts_et, and a designation row without a version stamp breaks the program's core invariant.
    """
    if "fetched_at_utc" not in frame.columns:
        raise ValueError("ESPN snapshot frame has no fetched_at_utc column: no decision-time stamp, refusing to align")
    out_rows, dropped = [], 0
    for r in frame.itertuples(index=False):
        code = team_code(str(r.team_abbrev)) if pd.notna(r.team_abbrev) else None
        if code is None:
            dropped += 1
            continue
        ts = pd.Timestamp(r.fetched_at_utc)
        ts = ts.tz_convert(_ET) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(_ET)
        detail = "" if pd.isna(r.detail) else str(r.detail)
        row = {"report_ts_et": ts.isoformat(), "game_date": ts.date().isoformat(), "game_time_et": None,
               "matchup": None, "team": NAME_OF[code], "team_code": code,
               "player_raw": r.player, "player": r.player, "status": r.status,
               "reason": detail, "reason_cat": R.reason_category(detail)}
        if "espn_player_id" in frame.columns:
            row["espn_id"] = None if pd.isna(r.espn_player_id) else int(r.espn_player_id)
        out_rows.append(row)
    cols = list(R.COLUMNS) + (["espn_id"] if "espn_player_id" in frame.columns else [])
    out = pd.DataFrame(out_rows, columns=cols)
    out.attrs["n_dropped_unknown_team"] = dropped
    out.attrs["source"] = "espn_snapshot"
    return out
