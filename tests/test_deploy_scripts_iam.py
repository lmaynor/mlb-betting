"""Every deploy script that creates a Cloud Scheduler entry triggering a Cloud Run JOB must first grant scheduler-invoker
roles/run.invoker ON THAT JOB. Without it every scheduler firing is PERMISSION_DENIED (status code 7) and the job silently
never runs: this was missed on 11 jobs provisioned 2026-10-14 (docs/solutions/runtime-errors/scheduler-invoker-needs-run-invoker-per-job.md),
after earlier recurrences. Scripts are only text here (never executed): the test pins the contract and that each parses under
bash (macOS ships 3.2, so `bash -n` on the system bash also catches bash-4-only syntax)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
SCRIPTS = sorted(p for p in DEPLOY.glob("*.sh"))
TRIGGERS_A_JOB = [p for p in SCRIPTS if "run.googleapis.com/v2" in p.read_text() and "scheduler jobs create" in p.read_text()]


def test_the_scan_actually_finds_the_job_trigger_scripts():
    names = {p.name for p in TRIGGERS_A_JOB}
    assert {"setup_nfl_refresh.sh", "setup_props_alerts.sh", "setup_odds_banking.sh", "setup_nhl_jobs.sh",
            "setup_nba_refresh.sh", "setup_fast_alert.sh"} <= names           # a renamed pattern must not silently empty this test


@pytest.mark.parametrize("script", TRIGGERS_A_JOB, ids=lambda p: p.name)
def test_binding_is_granted_before_the_scheduler_is_created(script):
    s = script.read_text()
    bind = re.search(r"gcloud run jobs add-iam-policy-binding[^\n]*(?:\\\n[^\n]*)*roles/run\.invoker", s)
    assert bind, f"{script.name} creates a job-trigger scheduler but never grants roles/run.invoker on the job"
    assert "scheduler-invoker" in s or "SCHED_SA" in s
    first_sched = re.search(r"gcloud scheduler jobs (?:create|update) http", s)
    assert first_sched and bind.start() < first_sched.start(), f"{script.name}: grant must come BEFORE the scheduler is created"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_every_deploy_script_parses_under_the_system_bash(script):
    bash = shutil.which("bash")
    r = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, f"{script.name}: {r.stderr.strip()}"
