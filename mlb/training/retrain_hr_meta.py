"""
training/retrain_hr_meta.py — DEPRECATED. Use retrain_hr_v6.py instead. (T11)

This script was a metadata-patching shim that did not actually retrain the
model. It has been replaced by training/retrain_hr_v6.py which performs a
full walk-forward CV + OOS eval + retrain pipeline.

Kept as a stub so existing Cloud Run Job definitions don't hard-fail.
"""
import sys
import json
import logging

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


def run() -> dict:
    msg = (
        "retrain_hr_meta.py is deprecated (T11, 2026-05-19). "
        "Use training.retrain_hr_v6 instead. "
        "Update Cloud Run Job to: python -m training.retrain_hr_v6"
    )
    logger.warning(msg)
    return {"status": "deprecated", "message": msg}


def main():
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(1)  # Non-zero so scheduler alerts


if __name__ == "__main__":
    main()
