"""
Forwarder module for tweet_drafter.py.
Allows direct execution and imports via mlb/runners/tweet_drafter.py.
"""
import sys
from pathlib import Path

# Ensure repo root is on sys.path
_root = str(Path(__file__).resolve().parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from tweet_drafter import *  # noqa: F401, F403
import tweet_drafter as _td

if __name__ == "__main__":
    _td.main()
