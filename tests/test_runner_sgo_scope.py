"""Regression guard for the live-odds guard (PR #15) scope bug.

Background: PR #15 added `sgo.is_live_event(...)` calls into several runners, but
in `run_hr._build_predictions` the `sgo` module was imported only in a *different*
function, and in `run_k._score_pitcher_er` it was imported `as _sgo` but used as
`sgo`. Both raised `NameError` at runtime on the 2026-06-24 22:00 UTC betting run.
Unit tests for `is_live_event` itself passed, so nothing caught it.

This test statically verifies, per function, that any `<name>.is_live_event(...)`
call has `<name>` imported in the SAME function scope. No heavy runtime deps.
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
RUNNERS = sorted((REPO / "mlb" / "runners").glob("run_*.py"))


def _scope_violations(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text())
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        bound = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Import):
                bound |= {a.asname or a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                bound |= {a.asname or a.name for a in n.names}
        for n in ast.walk(fn):
            if (
                isinstance(n, ast.Attribute)
                and n.attr == "is_live_event"
                and isinstance(n.value, ast.Name)
                and n.value.id not in bound
            ):
                bad.append(f"{path.name}:{n.lineno} {fn.name}() uses '{n.value.id}.is_live_event' not imported in scope")
    return bad


@pytest.mark.parametrize("path", RUNNERS, ids=lambda p: p.name)
def test_is_live_event_module_in_scope(path):
    violations = _scope_violations(path)
    assert not violations, "live-event guard scope bug:\n" + "\n".join(violations)
