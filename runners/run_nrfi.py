"""
runners/run_nrfi.py — NRFI stub. Not yet implemented in GCP.
"""
import logging
from datetime import date
logger = logging.getLogger(__name__)

def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"NRFI run | type={run_type} | date={run_date} — stub, no predictions")
    return {"bets_logged": 0, "system": "NRFI", "status": "stub"}
