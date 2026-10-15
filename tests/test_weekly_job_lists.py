"""main.RETRAIN_JOBS / CALIBRATE_JOBS must cover every retrain/calibrate job the registry declares.

SB's two jobs were declared in registry.py and provisioned by deploy/setup_model_jobs.sh but missing from these lists, so SB
was never retrained or recalibrated by the Monday scheduler (found 2026-10-15). main.py is parsed, not imported, so the test
needs no Flask app or env.
"""
import ast
from pathlib import Path

from mlb_core.registry import SYSTEMS

MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _list_constant(name: str) -> list:
    for node in ast.parse(MAIN.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found in main.py")


def _declared(attr: str) -> set:
    return {j for s in SYSTEMS.values() for j in (getattr(s, attr, None) or [])}


def test_every_registry_retrain_job_is_scheduled_weekly():
    assert _declared("retrain_jobs") <= set(_list_constant("RETRAIN_JOBS"))


def test_every_registry_calibrate_job_is_scheduled_weekly():
    assert _declared("calibrate_jobs") <= set(_list_constant("CALIBRATE_JOBS"))


def test_weekly_lists_name_no_job_the_registry_does_not_know():
    assert set(_list_constant("RETRAIN_JOBS")) <= _declared("retrain_jobs")
    assert set(_list_constant("CALIBRATE_JOBS")) <= _declared("calibrate_jobs")


def test_no_duplicate_entries():
    for name in ("RETRAIN_JOBS", "CALIBRATE_JOBS"):
        lst = _list_constant(name)
        assert len(lst) == len(set(lst)), name
