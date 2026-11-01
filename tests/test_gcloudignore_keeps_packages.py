"""Cloud Build uploads the directory minus .gcloudignore. An unanchored `data/` line also matched the PACKAGES mlb_core/data,
nfl/data, nhl/data and nba/data, so the image built on 2026-11-01 failed with `ModuleNotFoundError: mlb_core.data`. Every Python
package the Dockerfile copies must survive the ignore file."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ignored(rel: str) -> bool:
    r = subprocess.run(["git", "-c", f"core.excludesFile={ROOT / '.gcloudignore'}", "check-ignore", "--no-index", "-q", rel],
                       cwd=ROOT, capture_output=True)
    return r.returncode == 0


def test_no_python_package_is_excluded_from_the_build_context():
    lost = []
    for top in ("mlb_core", "mlb", "nba", "nfl", "nhl", "ev"):
        for init in (ROOT / top).rglob("__init__.py"):
            rel = init.relative_to(ROOT).as_posix()
            if _ignored(rel):
                lost.append(rel)
    assert not lost, f".gcloudignore drops package files from the image: {lost[:5]}"


def test_local_data_dumps_and_worktrees_are_still_excluded():
    assert _ignored("data/anything.csv") and _ignored(".claude/worktrees/x/main.py")
