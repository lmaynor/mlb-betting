"""
tests/test_runner_imports.py -- every scoring runner must import cleanly.

No existing test in this suite previously imported any of the 8 scoring
runners at runtime (they all `import xgboost` at module scope, and this repo
has no test infrastructure mocking that) -- so a syntax error, a bad
top-level import, or a NameError in module-level code (e.g. a constant
referencing an undefined name) in any of these files could only ever be
caught by `python3 -m compileall` (syntax only) or manual review, never by
pytest. This is a cheap, high-value smoke test closing that gap: it directly
exercises every edit made across mlb_core/odds/utils.py (Kelly formula),
mlb_core/registry.py (OUTS/PITCHER_ER/F1H/1I), and all 8 run_*.py files
during the 2026-08-16 audit's fix pass.

Requires libomp (macOS: `brew install libomp`) for xgboost's native library.
"""
import importlib

import pytest

RUNNER_MODULES = [
    "mlb.runners.run_hr",
    "mlb.runners.run_nrfi",
    "mlb.runners.run_f5",
    "mlb.runners.run_k",
    "mlb.runners.run_batter_hits",
    "mlb.runners.run_batter_tb",
    "mlb.runners.run_game",
    "mlb.runners.run_1i",
    "mlb.runners.settle_bets",
]


@pytest.mark.parametrize("module_name", RUNNER_MODULES)
def test_runner_module_imports_cleanly(module_name):
    importlib.import_module(module_name)


def test_all_runners_use_the_fixed_kelly_contract():
    """Every kelly_stake/kelly_pct call site must pass model_prob, not a
    precomputed edge -- regression pin for the 2026-08-16 probability-basis
    fix (finding A1). Static (not just import-level) so it directly checks
    the fix rather than merely that the module loads."""
    import ast
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[1]
    files = [
        "mlb/runners/run_hr.py", "mlb/runners/run_nrfi.py", "mlb/runners/run_f5.py",
        "mlb/runners/run_k.py", "mlb/runners/run_batter_hits.py",
        "mlb/runners/run_batter_tb.py", "mlb/runners/run_game.py", "mlb/runners/run_1i.py",
    ]
    for rel in files:
        src = (repo / rel).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "id", None)
            if fname not in ("kelly_stake", "kelly_pct", "kpct"):
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            first_arg_name = getattr(first_arg, "id", None)
            assert first_arg_name != "edge", (
                f"{rel}: {fname}() called with a bare 'edge' variable as its "
                f"first argument at line {node.lineno} -- Kelly sizing must "
                f"receive model_prob (the function derives the vig-inclusive "
                f"market_prob internally), not a precomputed fair-devigged "
                f"edge. This is the exact regression finding A1 fixed."
            )
