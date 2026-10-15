"""deploy/build_all_images.sh: one build, one digest, four repos -- and it refuses to bake a dirty/non-main tree into prod images.
The script is never run against GCP here: guards and DRY_RUN return before any gcloud call."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "build_all_images.sh"
BASH = shutil.which("bash")
IMAGES = ("mlb-betting:latest", "mlb-betting-feeds:latest", "mlb-betting-ev:shadow", "mlb-betting-bp-crawl:latest")


def test_script_is_executable_and_names_all_four_images():
    assert os.access(SCRIPT, os.X_OK)
    text = SCRIPT.read_text()
    for img in IMAGES:
        assert f"/{img}" in text
    assert "git status --porcelain" in text and "ALLOW_DIRTY" in text
    assert text.count("gcloud builds submit") == 1                      # built ONCE
    assert "add-tag" in text and "image_summary.digest" in text         # ...then retagged by digest
    # the printed service command must reference the repo that actually holds the digest (BUILD_REPO), which is the only one
    # that does under SKIP_PROD=1
    assert "--image=${BUILD_REPO}@${DIGEST}" in text and "/mlb-betting@${DIGEST}" not in text
    SCRIPT.read_bytes().decode("ascii")                                  # shell scripts stay ASCII too


def _repo(tmp_path):
    """A throwaway git repo + origin with the script at deploy/build_all_images.sh."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    run = lambda *a, cwd=work: subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True,
                                              env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    run("git", "init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    (work / "deploy").mkdir(parents=True)
    shutil.copy(SCRIPT, work / "deploy" / "build_all_images.sh")
    run("git", "init", "-b", "main")
    run("git", "add", "-A")
    run("git", "commit", "-m", "init")
    run("git", "remote", "add", "origin", str(origin))
    run("git", "push", "origin", "main")
    return work, run


def _exec(work, **env):
    return subprocess.run([BASH, str(work / "deploy" / "build_all_images.sh")], cwd=work, capture_output=True, text=True,
                          env={**os.environ, "PROJECT_ID": "p", **env})


def test_dirty_tree_is_refused_unless_explicitly_allowed(tmp_path):
    work, _ = _repo(tmp_path)
    (work / "stray.txt").write_text("local edit")                         # untracked counts: the Docker context is the directory
    r = _exec(work, DRY_RUN="1")
    assert r.returncode == 1 and "not clean" in r.stderr and "stray.txt" in r.stderr
    ok = _exec(work, DRY_RUN="1", ALLOW_DIRTY="1")
    assert ok.returncode == 0 and "DRY_RUN=1" in ok.stdout


def test_dry_run_on_a_clean_main_lists_all_four_targets_and_skip_prod_drops_the_production_tag(tmp_path):
    work, _ = _repo(tmp_path)
    r = _exec(work, DRY_RUN="1")
    assert r.returncode == 0, r.stderr
    for img in IMAGES:
        assert f"gcr.io/p/{img}" in r.stdout
    r2 = _exec(work, DRY_RUN="1", SKIP_PROD="1")
    assert "gcr.io/p/mlb-betting:latest" not in r2.stdout and "gcr.io/p/mlb-betting-ev:shadow" in r2.stdout


def test_head_that_is_not_origin_main_is_refused(tmp_path):
    work, run = _repo(tmp_path)
    run("git", "switch", "-c", "feature")
    (work / "f.txt").write_text("x")
    run("git", "add", "f.txt")
    run("git", "commit", "-m", "feature work")
    r = _exec(work, DRY_RUN="1")
    assert r.returncode == 1 and "not origin/main" in r.stderr
    assert _exec(work, DRY_RUN="1", ALLOW_NON_MAIN="1").returncode == 0
