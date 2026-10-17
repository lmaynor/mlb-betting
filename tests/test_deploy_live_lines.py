"""deploy/setup_bettingpros_live.sh + setup_daily_projections_job.sh: the schedule contract (scripts are text here, never run
against GCP). The generic IAM-grant / bash-parse checks live in test_deploy_scripts_iam.py and cover these files too."""
import os
import re
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
BP = (DEPLOY / "setup_bettingpros_live.sh").read_text()
PROJ = (DEPLOY / "setup_daily_projections_job.sh").read_text()


def _minutes(cron_field: str) -> set:
    return {int(x) for x in cron_field.split(",")}


def test_projection_job_runs_every_30_min_after_the_bettingpros_crawls():
    cron = re.search(r'^CRON="([^"]+)"', PROJ, re.M).group(1)
    proj_min = _minutes(cron.split()[0])
    assert proj_min == {25, 55} and cron.split()[1:] == ["*"] * 4          # every hour, two firings: every 30 min
    crawl_min = {int(m) for row in re.findall(r'"bp-live-\w+\|\w+\|[\w-]+\|([^"]+)"', BP) for m in row.split()[0].split(",")}
    assert crawl_min == {5, 10, 15, 40}                                   # 40 = the NFL Sunday half-hour
    assert max(m for m in crawl_min if m < 30) + 5 <= min(proj_min)       # the last crawl of the hour ends before :25


def test_crawl_schedules_are_seasonal_and_staggered():
    rows = re.findall(r'"(bp-live-\w+)\|(\w+)\|([\w-]+)\|([^"]+)"', BP)
    by_sport = {}
    for job, sport, tag, cron in rows:
        assert job == f"bp-live-{sport}"
        by_sport.setdefault(sport, []).append(cron.split())
    assert set(by_sport) == {"nfl", "nhl", "nba"}
    assert all(c[3] == "10-12,1-6" for c in by_sport["nhl"] + by_sport["nba"])          # Oct-Jun, no offseason requests
    assert all(c[3] == "9-12,1-2" for c in by_sport["nfl"])                             # Sep-Feb
    assert {c[0] for c in by_sport["nhl"]} == {"5"} and {c[0] for c in by_sport["nba"]} == {"15"}
    sun = [c for c in by_sport["nfl"] if c[4] == "0"]
    weekdays = [c for c in by_sport["nfl"] if c[4] == "1-6"]
    assert len(weekdays) == 1 and len(sun) == 2                                          # Sunday is covered, not double-fired
    hours = set()
    for c in sun:
        for part in c[1].split(","):
            lo, _, hi = part.partition("-")
            hours |= set(range(int(lo), int(hi or lo) + 1))
    assert hours == set(range(24))                                                       # every Sunday hour exactly once


def test_both_scripts_use_the_feeds_image_central_time_and_refuse_without_confirmation():
    for text in (BP, PROJ):
        assert "mlb-betting-feeds" in text and "mlb-betting:latest\"" not in text.split("IMAGE=", 1)[1].split("\n", 1)[0]
        assert '--time-zone="America/Chicago"' in text and "--max-retries=" in text
        assert "CONFIRM_IMAGE_HAS_LIVE_LINES" in text
    for name in ("setup_bettingpros_live.sh", "setup_daily_projections_job.sh"):
        env = {"PATH": "/usr/bin:/bin", "PROJECT_ID": "p", "HOME": os.environ.get("HOME", "/")}      # no gcloud on this PATH
        r = subprocess.run(["bash", str(DEPLOY / name)], capture_output=True, text=True, env=env)
        assert r.returncode == 1 and "REFUSING" in r.stdout, (name, r.stdout, r.stderr)


def test_projection_job_runs_every_sport_and_writes():
    assert '--args="-m,ev.daily_projections,--all,--write-gcs"' in PROJ
    assert "--memory=2Gi" in PROJ and "--task-timeout=900" in PROJ and "--max-retries=0" in PROJ
