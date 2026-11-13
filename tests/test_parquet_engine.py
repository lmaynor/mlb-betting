"""Canary for the parquet engine the game-log fixtures depend on.

2026-11-13 incident: the dev venv's pyarrow drifted to 26.0.1, which refuses to import under
numpy 1.26.4 (pyarrow 26 requires numpy >= 2.0). Every parquet read/write in the suite then
failed, and because the game-log loaders degrade gracefully (a missing/unreadable master just
serves less data), the breakage surfaced as 11 misleading assertion failures across
test_edge_cockpit.py and test_nba_inseason_gamelogs.py: recent_form returning None, hoopR
top-up rows silently absent, season logs frozen at the master.

requirements.txt pins pyarrow==17.0.0 + numpy==1.26.4, a combination that works. If this test
fails, the environment has drifted from the pins and the ImportError pandas raises names the
underlying conflict directly. Fix the environment; do not "fix" the tests that use parquet.
"""
import io

import pandas as pd


def test_parquet_engine_round_trips():
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    buf.seek(0)
    back = pd.read_parquet(buf)
    pd.testing.assert_frame_equal(back, df)
