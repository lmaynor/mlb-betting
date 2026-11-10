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
# Both Cloud Run job-run URI forms: v2 (projects/.../locations/.../jobs/X:run) and the v1 namespaces form
# (apis/run.googleapis.com/v1/namespaces/<project>/jobs/X:run). setup_bestbook_live.sh used v1 and was missed by a v2-only scan.
_JOB_URI = re.compile(r"run\.googleapis\.com/v2/projects/[^\s\"']*/jobs/|apis/run\.googleapis\.com/v1/namespaces/[^\s\"']*/jobs/")


def _triggers_a_job(text: str) -> bool:
    return bool(_JOB_URI.search(text)) and "scheduler jobs create" in text


def _violations(text: str) -> list:
    """Contract problems for ONE script's text (empty list == fine)."""
    out = []
    binds = [m for m in re.finditer(r"gcloud run jobs add-iam-policy-binding[^\n]*(?:\\\n[^\n]*)*roles/run\.invoker", text)]
    first_sched = re.search(r"gcloud scheduler jobs (?:create|update) http", text)
    if not binds:
        return ["never grants roles/run.invoker on the job"]
    if "scheduler-invoker" not in text and "SCHED_SA" not in text:
        out.append("grant is not for the scheduler-invoker service account")
    if not (first_sched and binds[0].start() < first_sched.start()):
        out.append("grant must come BEFORE the scheduler is created")
    # every job variable named in a job-run URI must be the one a grant is issued for
    for var in sorted(set(re.findall(r"/jobs/\$\{?([A-Za-z_]\w*)\}?:run", text))):
        if not any(re.search(r"\$\{?%s\}?" % var, b.group(0)) for b in binds):
            out.append(f"URI targets job ${var} but no run.invoker grant is issued for ${var}")
    if "--oauth-token-scope" not in text:
        out.append("scheduler has no --oauth-token-scope")
    return out


TRIGGERS_A_JOB = [p for p in SCRIPTS if _triggers_a_job(p.read_text())]


def test_the_scan_actually_finds_the_job_trigger_scripts():
    names = {p.name for p in TRIGGERS_A_JOB}
    assert {"setup_nfl_refresh.sh", "setup_props_alerts.sh", "setup_odds_banking.sh", "setup_nhl_jobs.sh",
            "setup_nba_refresh.sh", "setup_fast_alert.sh"} <= names           # a renamed pattern must not silently empty this test


def test_the_scan_covers_the_v1_namespaces_uri_form():
    v1 = 'URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB}:run"\ngcloud scheduler jobs create http x'
    assert _triggers_a_job(v1)
    assert _violations(v1) == ["never grants roles/run.invoker on the job"]
    assert any("scope" in v for v in _violations(v1 + "\ngcloud run jobs add-iam-policy-binding \"$JOB\" --member=serviceAccount:$SCHED_SA --role=roles/run.invoker"))


def test_a_grant_for_a_different_job_variable_is_not_enough():
    txt = ('gcloud run jobs add-iam-policy-binding "$OTHER" --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker"\n'
           'URI="https://run.googleapis.com/v2/projects/p/locations/r/jobs/${JOB}:run"\n'
           'gcloud scheduler jobs create http s --oauth-token-scope=x')
    assert any("no run.invoker grant is issued for $JOB" in v for v in _violations(txt))


@pytest.mark.parametrize("script", TRIGGERS_A_JOB, ids=lambda p: p.name)
def test_binding_is_granted_before_the_scheduler_is_created(script):
    assert _violations(script.read_text()) == [], script.name


def test_bestbook_live_setup_is_ungated_and_has_pause_and_resume_switches():
    s = (DEPLOY / "setup_bestbook_live.sh").read_text()
    assert "setup_bestbook_live.sh" in {p.name for p in TRIGGERS_A_JOB}
    assert "BESTBOOK_ENABLED" not in s                                         # owner decision 2026-11-01: no opt-in gate
    assert "gcloud scheduler jobs pause" in s and "gcloud scheduler jobs resume" in s and "RESUME" in s


def _daily_projections_generated_env(**overrides):
    """Evaluate only the env-construction lines of setup_daily_projections_job.sh (the script itself is never run here)."""
    import os

    text = (DEPLOY / "setup_daily_projections_job.sh").read_text()
    prefixes = ('NFL_ENGINE="${', 'NFL_HIER_LAYERS="${', 'NFL_HIER_WEATHER="${',
                'NHL_TRAINED_PROPS="${', 'ENV_VARS="GCP_PROJECT=')
    lines = [line for line in text.splitlines() if line.startswith(prefixes)]
    assert len(lines) == 5, lines
    env = {"PATH": os.environ["PATH"], "PROJECT_ID": "test-project", "REGION": "us-central1"}
    env.update(overrides)
    script = "\n".join(['PROJECT_ID="test-project"', 'REGION="us-central1"', *lines, "printf '%s' \"$ENV_VARS\""])
    r = subprocess.run([shutil.which("bash"), "-c", script], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_daily_projections_setup_carries_nhl_trained_props_by_default():
    text = (DEPLOY / "setup_daily_projections_job.sh").read_text()
    assert 'NHL_TRAINED_PROPS="${NHL_TRAINED_PROPS:-1}"' in text
    env = _daily_projections_generated_env()
    for token in ("GCP_PROJECT=test-project", "GCP_REGION=us-central1", "NFL_ENGINE=hierarchical",
                  "NFL_HIER_LAYERS=none", "NFL_HIER_WEATHER=0", "NHL_TRAINED_PROPS=1"):
        assert token in env.split(","), token
    assert '--set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"' in text


def test_daily_projections_setup_respects_an_explicit_nhl_trained_props_off():
    env = _daily_projections_generated_env(NHL_TRAINED_PROPS="0")
    assert "NHL_TRAINED_PROPS=0" in env.split(",")


def test_alert_sweep_jobs_that_log_bets_are_wired_for_the_database():
    """sport_alerts.log_sport_alert_bets writes the bets table; a job without MLB_DB_URL + Cloud SQL used to lose every row."""
    for name in ("setup_props_alerts.sh", "setup_nfl_sweep_schedule.sh", "setup_fast_alert.sh", "setup_model_picks.sh"):
        s = (DEPLOY / name).read_text()
        assert "MLB_DB_URL=mlb-db-url:latest" in s, name
        assert "cloudsql-instances" in s and "mlb-betting-db" in s, name
    nfl = (DEPLOY / "setup_nfl_sweep_schedule.sh").read_text()
    assert "--update-secrets" in nfl and "--set-secrets" not in nfl          # must not wipe the job's bot/bucket secrets


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_every_deploy_script_parses_under_the_system_bash(script):
    bash = shutil.which("bash")
    r = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, f"{script.name}: {r.stderr.strip()}"


def _run_deploy_service(cwd, **env):
    import os
    e = {"PATH": os.environ["PATH"], "HOME": str(cwd), "GIT_CONFIG_GLOBAL": "/dev/null"}
    e.update(env)
    return subprocess.run([shutil.which("bash"), str(DEPLOY / "deploy_service.sh")], cwd=cwd, env=e, capture_output=True, text=True)


def test_skip_tests_needs_an_explicit_acknowledgement(tmp_path):
    r = _run_deploy_service(tmp_path, SKIP_TESTS="1")
    assert r.returncode == 1 and "WARNING: SKIP_TESTS=1" in r.stderr and "I_KNOW_TESTS_ARE_SKIPPED=1" in r.stderr


def test_skip_tests_is_refused_on_a_dirty_main_even_when_acknowledged(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "wip.py").write_text("print('uncommitted')\n")
    r = _run_deploy_service(tmp_path, SKIP_TESTS="1", I_KNOW_TESTS_ARE_SKIPPED="1")
    assert r.returncode == 1 and "uncommitted changes" in r.stderr          # refused BEFORE step 0 touches git or CONTEXT.md
    assert not (tmp_path / "CONTEXT.md").exists()
