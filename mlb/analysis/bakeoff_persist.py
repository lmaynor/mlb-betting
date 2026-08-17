"""
mlb.analysis.bakeoff_persist -- durable storage for model_bakeoff.py / hr_model_bakeoff.py
runs. Neither script persists anything today (print-only) -- a run's results, and any
tuned hyperparameters found, vanish when the Cloud Shell terminal closes. This module is
the fix, built entirely on the storage primitives already used everywhere else
(`mlb_core.storage.write_csv`/`write_bytes`/`read_csv`/`read_bytes`) -- no new I/O layer --
following the same idiom `mlb.analysis.walkforward.rolling()`'s existing `out_prefix`
already uses.

New GCS namespace (nothing like this existed before this module):

    Analysis/bakeoff/runs/{run_id}/
      run_meta.json                    -- git sha/branch, cutoff/until, gates, tune params,
                                           timestamps, status, systems_completed
      scorecard.csv                    -- one row per system x model (the `board` frame)
      candidates/{system}_{model}.csv  -- every settled bet (backtest()'s candidates frame)
      tuning/{system}_tuned.json       -- {"params":..., "meta":...} for this run
      tuning/{system}_trials.csv       -- full Optuna trial history (optional)
    Analysis/bakeoff/latest.json       -- {"run_id":..., "prefix":...} best-effort pointer

run_id = "{cutoff}_{git_sha7}_{HHMMSS_UTC}" -- leads with the walk-forward cutoff so a
gated run and its ungated sibling (same question, different gate) sort together; the
short git SHA ties every number to the exact code that produced it.

HARD RULE, not just hygiene: tuning/{system}_tuned.json must NEVER be written to
`mlb.training.tune_hyperparams.SYSTEM_CONFIG[system]["gcs_output"]` (e.g.
HR_Pro/models/hr_tuned_params.json). retrain_batter_hits_v1.py, retrain_batter_tb_v1.py,
and retrain_game_v1.py already call `load_tuned_params(SYSTEM)` inside `run()` and will
silently adopt whatever JSON sits at that key on the NEXT REAL production retrain. Every
key this module writes lives under the separate `Analysis/bakeoff/` tree, which makes
that impossible by construction -- see
docs/solutions/conventions/bakeoff-tuned-params-storage.md.
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone

import pandas as pd

from mlb_core import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bakeoff_persist")

DEFAULT_RUN_ROOT = "Analysis/bakeoff/runs"
LATEST_POINTER_KEY = "Analysis/bakeoff/latest.json"


# ── run identity ───────────────────────────────────────────────────────────────

def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=5)
        val = out.stdout.strip()
        return val if out.returncode == 0 and val else None
    except Exception:  # noqa: BLE001 -- git metadata is nice-to-have, never load-bearing
        return None


def git_sha7() -> str:
    return _git("rev-parse", "--short=7", "HEAD") or "nogit"


def git_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown"


def make_run_id(cutoff: str) -> str:
    """{cutoff}_{git_sha7}_{HHMMSS_UTC} -- see module docstring for why this ordering."""
    ts = datetime.now(timezone.utc).strftime("%H%M%S")
    return f"{cutoff}_{git_sha7()}_{ts}"


def run_prefix(run_id: str, run_root: str = DEFAULT_RUN_ROOT) -> str:
    return f"{run_root.rstrip('/')}/{run_id}"


# ── per-artifact read/write ──────────────────────────────────────────────────────

def write_candidates(prefix: str, system: str, model: str, cand: pd.DataFrame) -> str:
    """Every settled bet for one system x model -- backtest()'s `candidates` frame,
    columns unchanged. Called incrementally, right after each backtest() call returns,
    so a Cloud Shell disconnect never loses a model that already finished."""
    key = f"{prefix}/candidates/{system}_{model}.csv"
    storage.write_csv(cand, key)
    return key


def write_scorecard(prefix: str, board: pd.DataFrame) -> str:
    """The cross-system board (one row per system x model, incl. verdict columns).
    Rewritten wholesale after every system completes -- cheap even at 6 systems x
    ~7 models, and means a partial run still leaves a readable scorecard."""
    key = f"{prefix}/scorecard.csv"
    storage.write_csv(board, key)
    return key


def read_scorecard(prefix: str) -> pd.DataFrame:
    return storage.read_csv(f"{prefix}/scorecard.csv")


def write_tuning(prefix: str, system: str, params: dict, meta: dict,
                trials: pd.DataFrame | None = None) -> str:
    """Tuned params + Optuna metadata for THIS bake-off run. Deliberately separate from
    tune_hyperparams.SYSTEM_CONFIG[system]["gcs_output"] -- see module docstring."""
    key = f"{prefix}/tuning/{system}_tuned.json"
    storage.write_bytes(json.dumps({"system": system, "params": params, "meta": meta},
                                   indent=2, default=str).encode(), key)
    if trials is not None and len(trials):
        storage.write_csv(trials, f"{prefix}/tuning/{system}_trials.csv")
    return key


def load_tuning(prefix: str, system: str) -> tuple[dict, dict] | None:
    """Read back a prior run's tuned params (for --load-tuned-from, e.g. the ungated
    comparison run that must reuse the SAME model and vary only the gate). Returns None
    on any miss/error -- callers should fall back to a fresh search, not crash."""
    key = f"{prefix}/tuning/{system}_tuned.json"
    try:
        if not storage.exists(key):
            return None
        payload = json.loads(storage.read_bytes(key))
        return payload["params"], payload["meta"]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{system}] load_tuning({prefix}) failed: {type(e).__name__}: {e}")
        return None


def write_run_meta(prefix: str, meta: dict) -> str:
    key = f"{prefix}/run_meta.json"
    storage.write_bytes(json.dumps(meta, indent=2, default=str).encode(), key)
    return key


def read_run_meta(prefix: str) -> dict:
    return json.loads(storage.read_bytes(f"{prefix}/run_meta.json"))


def new_run_meta(run_id: str, prefix: str, cutoff: str, until: str | None,
                 systems: list, **extra) -> dict:
    """Initial run_meta, written with status='running' BEFORE any system starts -- so a
    disconnected/killed Cloud Shell session still leaves a breadcrumb explaining what
    was attempted, not just silence."""
    return {
        "run_id": run_id, "prefix": prefix,
        "git_sha": git_sha7(), "git_branch": git_branch(),
        "cutoff": cutoff, "until": until, "systems": list(systems),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "status": "running", "systems_completed": [],
        **extra,
    }


def mark_system_complete(prefix: str, meta: dict, system: str) -> dict:
    """Append `system` to systems_completed and rewrite run_meta.json immediately --
    the per-system persistence granularity that makes a partial run legible."""
    done = list(meta.get("systems_completed", []))
    if system not in done:
        done.append(system)
    meta["systems_completed"] = done
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_run_meta(prefix, meta)
    return meta


def finish_run_meta(prefix: str, meta: dict, status: str = "complete") -> dict:
    meta["status"] = status
    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_run_meta(prefix, meta)
    update_latest_pointer(meta["run_id"], prefix)
    return meta


def notify_discord(message: str) -> None:
    """Best-effort completion/failure alert to #ops-alerts (opt-in via --notify --
    intended for the unattended Cloud Run Job, not spammy for ad-hoc Cloud Shell runs).
    Never raises -- a Discord hiccup must not affect the bake-off's own exit status."""
    try:
        from mlb_core.notify.discord import post_ops_alert
        post_ops_alert(message)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"discord notify failed (non-fatal): {type(e).__name__}: {e}")


def update_latest_pointer(run_id: str, prefix: str) -> None:
    """Best-effort convenience pointer at Analysis/bakeoff/latest.json -- never raises;
    a failed write here must not fail the run itself."""
    try:
        storage.write_bytes(json.dumps({
            "run_id": run_id, "prefix": prefix,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2).encode(), LATEST_POINTER_KEY)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"update_latest_pointer failed (non-fatal): {type(e).__name__}: {e}")
