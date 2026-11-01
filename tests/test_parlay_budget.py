"""ParlayAPI credit budgets: ONE shared 100k/mo pool, every budget defined in nba/config.py.

The plan was upgraded from 20k to 100k, but the per-sport ledger budgets (99.5k) and the MLB snapshot ceiling (19.5k) were each
sized as if they owned the pool (~119k combined), the NFL sweep had no ledger, and the shell script re-hard-coded the numbers.
These tests pin the total and that the script's copies equal the Python constants."""
import json
import re
from pathlib import Path

import pytest

from nba import config as C
from nba.odds import credit_ledger as L

ROOT = Path(__file__).resolve().parent.parent


def test_every_budget_in_the_shared_pool_sums_to_well_under_the_quota():
    total = C.parlay_budget_total()
    assert total == (sum(C.ODDS_BANK_DEFAULT_BUDGET.values()) + C.MLB_SNAPSHOT_CEILING + C.NFL_SWEEP_MONTHLY_BUDGET
                     + C.PARLAY_HISTORICAL_MONTHLY_BUDGET)
    assert total <= 95_000, f"{total} committed credits would not fit the {C.PARLAY_MONTHLY_QUOTA} plan with headroom"
    assert total + C.PARLAY_RESERVE_FLOOR < C.PARLAY_MONTHLY_QUOTA


def test_mlb_snapshot_ceiling_comes_from_the_shared_constants(monkeypatch):
    monkeypatch.delenv("PARLAY_CREDIT_CEILING", raising=False)
    import importlib
    from mlb.runners import snapshot_odds as S
    importlib.reload(S)
    assert S.CREDIT_CEILING == C.MLB_SNAPSHOT_CEILING and S.PARLAY_RESERVE_FLOOR == C.PARLAY_RESERVE_FLOOR


def test_historical_puller_uses_the_same_floor_as_the_ledger():
    from nba.odds import parlay_historical as H
    assert H.DEFAULT_FLOOR == C.PARLAY_RESERVE_FLOOR == L.PARLAY_RESERVE_FLOOR


def test_odds_banking_script_defaults_equal_the_python_constants():
    s = (ROOT / "deploy" / "setup_odds_banking.sh").read_text()

    def default(var):
        return int(re.search(r'^%s="\$\{[A-Z_]+:-(\d+)\}"' % var, s, re.M).group(1))

    assert default("BUDGET_NBA") == C.ODDS_BANK_DEFAULT_BUDGET["basketball_nba"]
    assert default("BUDGET_NHL") == C.ODDS_BANK_DEFAULT_BUDGET["icehockey_nhl"]
    assert default("RESERVE_FLOOR") == C.PARLAY_RESERVE_FLOOR
    # the job env is built from those variables, never from literals
    assert "ODDS_BANK_BUDGET_BASKETBALL_NBA=${BUDGET_NBA}" in s and "PARLAY_RESERVE_FLOOR=${RESERVE_FLOOR}" in s
    assert not re.search(r"ODDS_BANK_BUDGET_[A-Z_]+=\d", s)


def test_no_stale_20k_pool_wording_in_the_budget_docs():
    for rel in ("nba/odds/credit_ledger.py", "deploy/rollout_multisport_live.sh", "deploy/setup_parlay_schedules.sh",
                "RUNBOOKS.md", "docs/ADDING_A_NEW_SPORT.md"):
        text = (ROOT / rel).read_text()
        assert not re.search(r"\b20k\b|20,000\b|20k/mo", text), f"{rel} still describes the shared pool as 20k"


# -- ledger concurrency ------------------------------------------------------------------------------------------------------
class FakeBlob:
    """A GCS object with generations. `racer` runs once after our first read, as if another job wrote in between."""

    def __init__(self, credits=None, racer=None):
        self.gen = 0 if credits is None else 1
        self.body = None if credits is None else json.dumps({"month": "2026-11", "credits": credits}).encode()
        self.racer, self.generation = racer, None

    def reload(self):
        from google.api_core.exceptions import NotFound
        if self.body is None:
            raise NotFound("missing")
        self.generation = self.gen

    def download_as_bytes(self, if_generation_match=None):
        data = self.body
        if self.racer:
            r, self.racer = self.racer, None
            r(self)
        return data

    def upload_from_string(self, payload, if_generation_match=None):
        from google.api_core.exceptions import PreconditionFailed
        if if_generation_match != self.gen:
            raise PreconditionFailed("generation mismatch")
        self.body, self.gen = payload, self.gen + 1


def _credits(b):
    return json.loads(b.body)["credits"]


def test_record_does_not_lose_an_update_when_two_jobs_of_one_sport_overlap(monkeypatch):
    def other_job_writes(b):                      # the game_lines job lands +100 between our read and our write
        b.body, b.gen = json.dumps({"month": "2026-11", "credits": 1100}).encode(), b.gen + 1

    blob = FakeBlob(1000, racer=other_job_writes)
    monkeypatch.setattr(L, "_blob_for", lambda key: blob)
    L.record("icehockey_nhl", "2026-11", 50)
    assert _credits(blob) == 1150                 # 1000 + 100 (other job) + 50 (ours): nothing lost


def test_record_creates_the_ledger_only_if_absent(monkeypatch):
    blob = FakeBlob(None)
    monkeypatch.setattr(L, "_blob_for", lambda key: blob)
    L.record("icehockey_nhl", "2026-11", 40)
    assert _credits(blob) == 40


def test_record_resets_on_a_new_month_and_never_raises(monkeypatch, caplog):
    blob = FakeBlob(900)
    blob.body = json.dumps({"month": "2026-10", "credits": 900}).encode()
    monkeypatch.setattr(L, "_blob_for", lambda key: blob)
    L.record("icehockey_nhl", "2026-11", 5)
    assert _credits(blob) == 5

    class Always(FakeBlob):
        def upload_from_string(self, payload, if_generation_match=None):
            from google.api_core.exceptions import PreconditionFailed
            raise PreconditionFailed("always contended")

    monkeypatch.setattr(L, "_blob_for", lambda key: Always(10))
    with caplog.at_level("ERROR"):
        L.record("icehockey_nhl", "2026-11", 5)                       # logs, does not raise into the banker job
    assert "credit ledger write FAILED" in caplog.text
