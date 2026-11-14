"""Build-time designation assertion for the DEC-02 status fixture.

Re-derives every status fixture item's ``designation_at_decision`` from
the banked official NBA injury reports with the repo's own decision-time
rule (``nba.minutes_restriction.cases.select_designation``: last report
at or before tip - 60 minutes; tip from the hoopR boxscore mirror) and
compares it to the fixture. This is the assertion the 2026-11-13 join
audit called for after finding S154 (Trey Lyles, 2025-03-13 SAC@GSW)
carrying the 19:30 report's Questionable where the rule yields the
20:30 report's Available (tip 22:00 ET, cutoff 21:00).

Modes:
  * check (default): exit 1 listing every item whose fixture designation
    disagrees with the re-derived one. Run at fixture build time; a
    disagreement fails the build.
  * --write: correct the fixture in place and write the derivation
    record (status_derivation.jsonl next to the fixture). The record is
    the committed evidence the offline test
    tests/test_decisions_availability_fixture.py validates, so drift
    between fixture and derivation fails the test suite as well.

Data inputs are local mirrors of the banked sources:
  * --reports-dir: {date}/report_*.csv layout mirroring GCS
    NBA/injuries/official/ (a game's reports live under its own date and
    the evening before).
  * --box-dir: hoopR player_box_{season}.parquet files mirroring GCS
    NBA/hoopr/player_boxscores/ (tip times).

Usage:
  python scripts/derive_status_designations.py \
      --reports-dir /path/to/official --box-dir /path/to/player_boxscores
  python scripts/derive_status_designations.py ... --write
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from nba.minutes_restriction import cases  # noqa: E402

DEFAULT_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "decisions" / "availability" / "status_items.jsonl"
)
RECORD_NAME = "status_derivation.jsonl"


def load_reports_for_dates(reports_dir: Path, game_dates: set[date]) -> pd.DataFrame:
    """Banked report CSVs that can cover the given game dates.

    A game's reports are published on its own date and the evening
    before, so only those two directory days are read per game date
    (mirrors scripts/minutes_arm_t_run.py's loader).
    """
    frames = []
    wanted = set()
    for d in game_dates:
        wanted.add(d.isoformat())
        wanted.add((d - timedelta(days=1)).isoformat())
    for dirname in sorted(wanted):
        day_dir = reports_dir / dirname
        if not day_dir.is_dir():
            continue
        for f in sorted(day_dir.glob("report_*.csv")):
            frames.append(pd.read_csv(f))
    if not frames:
        raise ValueError(f"no report CSVs found under {reports_dir} for the fixture dates")
    df = pd.concat(frames, ignore_index=True)
    gd = pd.to_datetime(df["game_date"]).dt.date
    return df[gd.isin(game_dates)].reset_index(drop=True)


# hoopR/ESPN team abbreviations that differ from the NBA codes the
# fixture and the official reports use (verified by diffing the two
# code sets over the full mirror, 2026-11-13).
ESPN_TO_NBA_ABBR = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "UTAH": "UTA",
    "WSH": "WAS",
}


def load_tips(box_dir: Path, seasons: range) -> dict[tuple[str, str, str], str]:
    """(game_date, away_abbr, home_abbr) -> tip timestamp (ISO, ET offset).

    Built from the hoopR player box mirror: every row of a game carries
    the game's real tip in game_date_time. Abbreviations are normalized
    to the NBA codes the fixture carries.
    """
    tips: dict[tuple[str, str, str], str] = {}
    for season in seasons:
        path = box_dir / f"player_box_{season}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(
            path,
            columns=["game_date", "game_date_time", "team_abbreviation",
                     "opponent_team_abbreviation", "home_away"],
        )
        df["game_date"] = pd.to_datetime(df["game_date"]).dt.date.astype(str)
        games = df.drop_duplicates(
            subset=["game_date", "team_abbreviation", "opponent_team_abbreviation", "home_away"]
        )
        for row in games.itertuples(index=False):
            team = ESPN_TO_NBA_ABBR.get(row.team_abbreviation, row.team_abbreviation)
            opp = ESPN_TO_NBA_ABBR.get(
                row.opponent_team_abbreviation, row.opponent_team_abbreviation
            )
            if row.home_away == "home":
                key = (row.game_date, opp, team)
            else:
                key = (row.game_date, team, opp)
            tips.setdefault(key, str(row.game_date_time))
    return tips


def derive(fixture_path: Path, reports_dir: Path, box_dir: Path) -> list[dict]:
    items = [
        json.loads(line)
        for line in fixture_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    game_dates = {date.fromisoformat(i["game_date"]) for i in items}
    reports = load_reports_for_dates(reports_dir, game_dates)
    norm = cases._reports_normalized(reports)
    index = cases.build_designation_index(norm)
    seasons = range(min(d.year for d in game_dates), max(d.year for d in game_dates) + 2)
    tips = load_tips(box_dir, seasons)

    records = []
    for item in items:
        away, _, home = item["matchup"].partition("@")
        tip_raw = tips.get((item["game_date"], away, home))
        if tip_raw is None:
            raise ValueError(f"no tip found for {item['item_id']} {item['matchup']} {item['game_date']}")
        tip_ts = pd.Timestamp(tip_raw)
        found = cases.select_designation(
            norm,
            player=item["player"],
            team=item["team"],
            game_date=date.fromisoformat(item["game_date"]),
            tip_ts=tip_ts,
            index=index,
        )
        if found is None:
            raise ValueError(
                f"select_designation found no eligible report for {item['item_id']} "
                f"({item['player']}, {item['game_date']})"
            )
        derived = found["designation"]
        records.append(
            {
                "item_id": item["item_id"],
                "player": item["player"],
                "team": item["team"],
                "game_date": item["game_date"],
                "matchup": item["matchup"],
                "tip_ts": tip_ts.isoformat(),
                "decision_report_ts": pd.Timestamp(found["decision_report_ts"]).isoformat(),
                "derived_designation": derived,
                "fixture_designation": item["designation_at_decision"],
                "match": derived == item["designation_at_decision"],
            }
        )
    return records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reports-dir", required=True, type=Path)
    ap.add_argument("--box-dir", required=True, type=Path)
    ap.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    ap.add_argument("--write", action="store_true",
                    help="correct the fixture and write the derivation record")
    args = ap.parse_args(argv)

    records = derive(args.fixture, args.reports_dir, args.box_dir)
    mismatches = [r for r in records if not r["match"]]
    for r in mismatches:
        print(
            f"MISMATCH {r['item_id']} {r['player']} {r['game_date']}: "
            f"fixture={r['fixture_designation']} derived={r['derived_designation']} "
            f"(decision report {r['decision_report_ts']}, tip {r['tip_ts']})"
        )
    print(f"{len(records)} items derived, {len(mismatches)} mismatches")

    if args.write:
        items = [
            json.loads(line)
            for line in args.fixture.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        derived_by_id = {r["item_id"]: r["derived_designation"] for r in records}
        for item in items:
            item["designation_at_decision"] = derived_by_id[item["item_id"]]
        with args.fixture.open("w", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        record_path = args.fixture.parent / RECORD_NAME
        with record_path.open("w", encoding="utf-8") as fh:
            for r in records:
                out = dict(r)
                out["fixture_designation"] = out["derived_designation"]
                out["match"] = True
                fh.write(json.dumps(out) + "\n")
        print(f"fixture corrected and derivation record written: {record_path}")
        return 0

    if mismatches:
        print("ASSERTION FAILED: fixture designations disagree with select_designation")
        return 1
    print("OK: every fixture designation matches the re-derived designation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
