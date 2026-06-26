"""Regression guard for the abort-branch return-type bug (2026-06-25).

Background: `run_hr._fetch_hr_odds` is annotated `-> dict` and its caller does
`if not player_odds:`. Its stale-snapshot / stale-feature-build abort branches
returned `pd.DataFrame()` instead of `{}`. When the HR feature build went stale,
`not <DataFrame>` raised "The truth value of a DataFrame is ambiguous", crashing
the HR runner on the very path meant to skip gracefully.

This test statically verifies, per function, that a function annotated `-> dict`
never `return pd.DataFrame(...)`. No heavy runtime deps.
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
RUNNERS = sorted((REPO / "mlb" / "runners").glob("run_*.py"))


def _returns_dict(fn: ast.FunctionDef) -> bool:
    return isinstance(fn.returns, ast.Name) and fn.returns.id == "dict"


def _df_return_violations(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text())
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or not _returns_dict(fn):
            continue
        for n in ast.walk(fn):
            if (
                isinstance(n, ast.Return)
                and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Attribute)
                and n.value.func.attr == "DataFrame"
            ):
                bad.append(
                    f"{path.name}:{n.lineno} {fn.name}() is annotated '-> dict' "
                    f"but returns pd.DataFrame() (caller likely does 'if not x:')"
                )
    return bad


@pytest.mark.parametrize("path", RUNNERS, ids=lambda p: p.name)
def test_dict_funcs_do_not_return_dataframe(path):
    violations = _df_return_violations(path)
    assert not violations, "abort-branch return-type bug:\n" + "\n".join(violations)
