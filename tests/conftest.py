"""
tests/conftest.py -- session-wide test setup.

Eagerly import native-extension-backed libraries (xgboost, sklearn.metrics)
before pytest collects any test module. Observed on this repo's test venv
(an ad hoc, unpinned Python 3.14 environment used because Python 3.14 has no
wheels for the pinned numpy==1.26.4/pandas==2.2.3/scikit-learn==1.6.1 --
production itself runs Python 3.11 with those exact pins and does not hit
this): collecting the full test suite could intermittently leave
`sklearn.metrics` in a partially-initialized state by the time a later test
module tried `from sklearn.metrics import log_loss`, raising `ImportError:
cannot import name 'log_loss' from '<unknown module name>'` -- a collection-
order-dependent failure that never reproduced when the same module was
imported alone or via a plain (non-pytest) import of every test file in
order. Importing these up front, once, before collection begins, avoids it.
"""
import sklearn.metrics  # noqa: F401
import xgboost  # noqa: F401

import sys  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _daily_projections_never_reach_gcs(monkeypatch):
    """ev.daily_projections used to detect pytest itself (PYTEST_CURRENT_TEST) to avoid two network/GCS paths; that test
    detection is gone from production code, so the isolation lives here instead:

      * _load_env_lines reads the NFL schedule mirror via nfl.data.read.load. Unless a test stubs that loader (or
        _load_env_lines itself), it returns {} without touching storage.
      * _run_game_models refreshes the NBA/NHL game payload (history replay + GCS writes): a no-op by default. A test that
        wants the real function gets it via inspect.unwrap(dp._run_game_models).
      * _run_soccer_game_predictions refreshes the soccer game payload + board (Understat pulls + GCS writes): a no-op
        by default, same as the other game hooks.
    Only active for tests that have imported ev.daily_projections."""
    dp = sys.modules.get("ev.daily_projections")
    if dp is None:
        yield
        return
    import nfl.data.read as nr

    real_env, stock_load = dp._load_env_lines, nr.load

    def guarded_env_lines():
        return {} if nr.load is stock_load else real_env()

    def no_game_models(sport, events, now):
        return False

    no_game_models.__wrapped__ = dp._run_game_models

    def no_soccer_predictions(now):
        return False

    no_soccer_predictions.__wrapped__ = dp._run_soccer_game_predictions
    monkeypatch.setattr(dp, "_load_env_lines", guarded_env_lines)
    monkeypatch.setattr(dp, "_run_game_models", no_game_models)
    monkeypatch.setattr(dp, "_run_game_board", lambda sport, now: False)
    monkeypatch.setattr(dp, "_run_soccer_game_predictions", no_soccer_predictions)
    monkeypatch.setattr(dp, "_load_nba_spreads", lambda now: {})
    monkeypatch.setattr(dp, "_load_nba_tracking", lambda now: None)
    monkeypatch.setattr(dp, "_nhl_lineup_context", lambda team, player, start: {"pp_unit": None, "lineup_status": None})
    yield


@pytest.fixture(autouse=True)
def _nfl_sweep_ledger_never_reaches_gcs(request, monkeypatch):
    """The NFL sweep meters every ParlayAPI pull through the shared credit ledger (OddsAccum/.../_credits/*.json). A few test
    modules set a fake MLB_GCS_BUCKET, so an un-stubbed ledger call would go to REAL storage. Default: permissive and
    write-nothing. A module that tests the metering itself sets REAL_SWEEP_LEDGER = True and stubs the ledger's storage."""
    sw = sys.modules.get("ev.sweep_nfl_props")
    if sw is None or getattr(request.module, "REAL_SWEEP_LEDGER", False):
        yield
        return
    from nba.odds.credit_ledger import Allowance
    monkeypatch.setattr(sw, "_ledger_allowance", lambda pc, want, today: Allowance(True, want, "test"))
    monkeypatch.setattr(sw, "_ledger_record", lambda today, n: None)
    yield


@pytest.fixture(autouse=True)
def _nhl_game_run_never_fetches_rotowire(monkeypatch):
    """nhl.game.run_daily reads the confirmed-goalie cache (a storage read). Tests never touch storage: default is 'nothing
    confirmed'. A test of the wiring monkeypatches run_daily.cached_confirmed_goalies itself."""
    rd = sys.modules.get("nhl.game.run_daily")
    if rd is not None:
        monkeypatch.setattr(rd, "cached_confirmed_goalies", lambda date_str, now=None: {})
    yield
