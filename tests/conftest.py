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


class NetworkAccessInTest(ConnectionError):
    """Raised on any non-loopback socket use inside a test. A ConnectionError so production code that treats an outage as
    'feed down' degrades exactly as it would offline; the fixture below then FAILS the test, so the dependency is not hidden."""


@pytest.fixture(autouse=True)
def _tests_never_reach_the_network(monkeypatch):
    """Tests are hermetic: no DNS, no outbound connect (ESPN, api.beezy.fyi, Google, GCS, Discord ...). Loopback and unix
    sockets stay open (local servers / test clients). Most production fetchers swallow connection errors, so a test that
    forgot to stub one used to pass anyway while depending on the live service (different data every day) or on a
    network timeout (a 25-minute suite when offline). Each attempt is recorded and fails the test at teardown with the
    host that was contacted: stub it (monkeypatch the fetcher / use the conftest fixtures), don't allow it."""
    import socket

    attempts: list[str] = []
    real_connect, real_connect_ex, real_gai = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    loopback = {"localhost", "127.0.0.1", "::1", "", None}

    def _local(addr) -> bool:
        host = addr[0] if isinstance(addr, tuple) else addr
        return host in loopback or str(host).startswith("127.")

    def connect(self, addr, *a, **k):
        if self.family == socket.AF_UNIX or _local(addr):
            return real_connect(self, addr, *a, **k)
        attempts.append(f"connect {addr[0] if isinstance(addr, tuple) else addr}")
        raise NetworkAccessInTest(f"network blocked in tests: connect {addr}")

    def connect_ex(self, addr, *a, **k):
        if self.family == socket.AF_UNIX or _local(addr):
            return real_connect_ex(self, addr, *a, **k)
        attempts.append(f"connect {addr[0] if isinstance(addr, tuple) else addr}")
        raise NetworkAccessInTest(f"network blocked in tests: connect {addr}")

    def getaddrinfo(host, *a, **k):
        if host in loopback or str(host).startswith("127."):
            return real_gai(host, *a, **k)
        attempts.append(f"dns {host}")
        raise NetworkAccessInTest(f"network blocked in tests: dns {host}")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield
    if attempts:
        pytest.fail("test tried to reach the network (" + ", ".join(sorted(set(attempts))) + "): stub the fetcher", pytrace=False)


@pytest.fixture(autouse=True)
def _no_ambient_gcs_db_or_startup_jitter(monkeypatch):
    """Per-test environment, independent of collection / run order.

    Several test modules mutate os.environ at IMPORT time (test_public_api / test_admin_auth `setdefault` a fake
    MLB_GCS_BUCKET; others pop it), so which value a test saw depended on collection order and on which sibling test ran
    before it. With a bucket set, storage.* goes to REAL GCS (403 on a dev box with ADC, a connection error / hang
    anywhere else). Storage re-reads the env at call time, so clearing it per test makes every test local-only unless it
    opts in itself with monkeypatch.setenv / patches the storage functions.
    Runner jobs also sleep 5-30 s of startup jitter to spread load in prod: never in a test."""
    for var in ("MLB_GCS_BUCKET", "GCS_BUCKET", "MLB_DB_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SKIP_STARTUP_JITTER", "1")
    yield


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

    # GameLogs.nba_absences pulls the LIVE ESPN injury report: the projections would change with the day's real injuries.
    # Default = report unavailable (ok=False, no absences). A caller that passes its own session (the espn_injuries unit
    # tests) still gets the real parser.
    from nba.data import espn_injuries as ei
    real_fetch = ei.fetch_injuries

    def no_live_injuries(session=None, timeout=20.0):
        return real_fetch(session, timeout) if session is not None else ei._empty(False, "test: no live fetch")

    monkeypatch.setattr(ei, "fetch_injuries", no_live_injuries)
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
