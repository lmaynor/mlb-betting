"""ev.novig.shadow_job: one scheduled firing of the Novig SHADOW loop (restore -> pass -> bank -> CLV-gate status line).
Fake Novig client, local DirStore, injected clock/snapshot: no network, no credentials, no orders of any kind."""
import json
import os
import types
from datetime import timedelta

import pytest

from ev.novig import shadow_bank as sb
from ev.novig import shadow_job as sj
from ev.novig.client import Book
from tests.test_novig_exec import FX, LEG, T0, point

NOW = T0 + timedelta(seconds=0.5)


class FakeNovig:
    """The public-catalog surface the pass uses. `down=True` simulates an unreachable Novig (every call raises)."""

    def __init__(self, over_bid=0.50, under_bid=0.48, down=False):
        self.b = {LEG.over_id: [{"price": str(over_bid), "qty": 100_000}], LEG.under_id: [{"price": str(under_bid), "qty": 100_000}]}
        self.down, self.calls = down, 0

    def list_events(self, league, **kw):
        self._ok()
        return FX["events"]

    def list_markets(self, league, **kw):
        self._ok()
        return FX["markets"]

    def get_book(self, market_id, depth=10):
        self._ok()
        return Book(market_id, self.b, 1, T0.timestamp())

    def _ok(self):
        self.calls += 1
        if self.down:
            raise RuntimeError("novig unreachable")


def snapshot(age_min=6.0, with_game=True, p_over=0.55):
    ev = {"game_key": LEG.game_key, "commence": LEG.start_utc.isoformat(), "event_id": "e1", "points": [point(p_over=p_over)]}
    return {"ts": (NOW - timedelta(minutes=age_min)).isoformat(), "cycle": 1, "events": [ev] if with_game else []}


def args(**kw):
    d = dict(store="dir:x", bucket=None, max_runtime_sec=0.0, loop_seconds=0.01, wait_fresh_sec=0.0, fresh_margin_min=2.0,
             horizon_hours=30.0, pace_s=0.0)
    d.update(kw)
    return types.SimpleNamespace(**d)


def fire(store, nc=None, snap=None, **kw):
    nc = nc if nc is not None else FakeNovig()
    snap = snap if snap is not None else snapshot()
    return sj.run(args(**kw), store=store, nc=nc, snapshot_fn=lambda: snap, clock=lambda: NOW, sleep=lambda s: None,
                  gate_source=(lambda prefix: [], lambda key: b"")), nc


@pytest.fixture
def store(tmp_path):
    return sb.DirStore(str(tmp_path / "bank"))


def banked_counts(store):
    led = sb.banked_ledger(store)
    with led._lock:
        return {t: led._c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("orders", "decisions", "fills", "kv")}


# ------------------------------------------------------------------ the happy path
def test_a_firing_paper_fills_banks_everything_and_prints_the_gate_line(store, capsys):
    rc, nc = fire(store)
    out = capsys.readouterr().out
    assert rc == 0 and nc.calls > 0
    assert banked_counts(store) == {"orders": 1, "decisions": 1, "fills": 1, "kv": 1}
    line = [l for l in out.splitlines() if l.startswith(sj.STATUS_TAG)]
    assert len(line) == 1 and "filled_bets=1" in line[0] and "open_slices=0/5" in line[0] and "min_n=250" in line[0]
    assert "nfl|rec_yds: insufficient_data n=0/1" in line[0]                 # filled, but no banked closing payloads in this fake
    assert "banked_rows=decisions:1,filled_orders:1,fills:1,orders:1" in line[0]
    assert all(ord(c) < 128 for c in line[0])
    v = json.loads(store.read(sb.VERDICT_KEY))                         # the derived verdict is banked too
    assert v["coverage"]["filled_bets"] == 1 and not any(s["open"] for s in v["slices"].values())


def test_the_next_firing_restores_state_and_does_not_buy_the_same_prop_twice(store, capsys):
    fire(store)
    capsys.readouterr()
    rc, _ = fire(store)
    out = capsys.readouterr().out
    assert rc == 0
    assert banked_counts(store)["orders"] == 1 and banked_counts(store)["fills"] == 1       # still ONE position
    line = [l for l in out.splitlines() if l.startswith(sj.STATUS_TAG)][0]
    assert "filled_bets=1" in line and "(cached)" in line                                    # no new fill: verdict reused


def test_a_new_fill_recomputes_the_verdict_instead_of_serving_the_cache(store, capsys):
    fire(store, snap=snapshot(with_game=True))
    capsys.readouterr()
    # a firing that fills nothing reuses the cache ...
    fire(store)
    assert "(cached)" in capsys.readouterr().out
    # ... and the cache is ignored once the run itself produced a fill (here: wipe the bank's orders by using a new bank)
    other = sb.DirStore(str(store.root) + "_2")
    other.put(sb.VERDICT_KEY, store.read(sb.VERDICT_KEY))
    fire(other)
    assert "(cached)" not in capsys.readouterr().out


# ------------------------------------------------------------------ nothing to do / loud failures
def test_no_game_in_the_window_touches_neither_novig_nor_the_bank(store, capsys):
    rc, nc = fire(store, snap=snapshot(with_game=False))
    assert rc == 0 and nc.calls == 0 and banked_counts(store)["orders"] == 0
    assert sj.STATUS_TAG in capsys.readouterr().out                    # the status line is still printed


def test_a_stale_snapshot_is_never_traded(store):
    rc, nc = fire(store, snap=snapshot(age_min=24.0))                    # limit 25 - margin 2 = 23
    assert rc == 0 and nc.calls == 0 and banked_counts(store)["decisions"] == 0


def test_a_fresh_enough_snapshot_is_traded(store):
    rc, nc = fire(store, snap=snapshot(age_min=22.0, p_over=0.57))      # an old snapshot widens the EV hurdle
    assert rc == 0 and nc.calls > 0 and banked_counts(store)["orders"] == 1


def test_missing_snapshot_fails_the_execution(store):
    rc = sj.run(args(), store=store, nc=FakeNovig(), snapshot_fn=lambda: None, clock=lambda: NOW, sleep=lambda s: None,
                gate_source=(lambda p: [], lambda k: b""))
    assert rc == 1


def test_unreachable_novig_fails_the_execution_and_banks_nothing(store):
    rc, _ = fire(store, nc=FakeNovig(down=True))
    assert rc == 1 and banked_counts(store) == {"orders": 0, "decisions": 0, "fills": 0, "kv": 0}


def test_a_bank_write_failure_fails_the_execution_loudly(store, monkeypatch):
    monkeypatch.setattr(store, "write_new", lambda key, data: (_ for _ in ()).throw(OSError("gcs down")))
    rc, _ = fire(store)
    assert rc == 1


def test_the_status_line_never_raises(store, monkeypatch):
    monkeypatch.setattr(sb, "banked_ledger", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    line = sj.gate_status(store, now=NOW)
    assert line.startswith(sj.STATUS_TAG + " unavailable") and "ValueError" in line


# ------------------------------------------------------------------ pure helpers
def test_games_in_window_respects_the_pre_kick_buffer_and_horizon():
    from ev.props_config import props_config
    cfg = props_config(env={})
    s = snapshot()
    assert sj.games_in_window(s, cfg, NOW, 30.0) == 1
    assert sj.games_in_window(s, cfg, LEG.start_utc - timedelta(minutes=10), 30.0) == 0       # inside the 20 min buffer
    assert sj.games_in_window(s, cfg, LEG.start_utc - timedelta(hours=40), 30.0) == 0         # beyond the horizon
    assert sj.games_in_window({"events": [{"commence": "garbage"}]}, cfg, NOW, 30.0) == 0


def test_format_status_collapses_empty_slices_and_stays_ascii():
    v = {"computed_at": "2026-11-05T12:00:00+00:00", "rule": {"min_n": 250},
         "coverage": {"filled_bets": 3, "with_usable_close": 2, "dropped_reasons": {}},
         "slices": {"nfl|rec_yds": {"status": "insufficient_data", "open": False, "n": 2, "n_bets": 3, "n_days": 1,
                                    "mean_clv_pct": -1.1, "ci_lo": None, "mean_clv_cons_pct": 2.7},
                    "nfl|pass_yds": {"status": "no_data", "open": False, "n": 0, "n_bets": 0, "n_days": 0,
                                     "mean_clv_pct": None, "ci_lo": None, "mean_clv_cons_pct": None}}}
    line = sj.format_status(v, {"orders": 3})
    assert "open_slices=0/2" in line and "nfl|rec_yds: insufficient_data n=2/3 days=1 meanCLV=-1.10% loCI=n/a consCLV=+2.70%" in line
    assert "no_fills: nfl|pass_yds" in line and "banked_rows=orders:3" in line and all(ord(c) < 128 for c in line)


def test_the_job_never_constructs_a_paper_venue_or_a_keyed_client(store, monkeypatch):
    seen = {}
    real = sj.nx.run_pass

    def spy(**kw):
        seen.update(kw)
        return real(**kw)
    monkeypatch.setattr(sj.nx, "run_pass", spy)
    fire(store)
    assert seen["paper_venue"] is None and seen["paper_risk"] is None
    from ev.novig.client import NovigClient, NovigProductionNotEnabled
    with pytest.raises(NovigProductionNotEnabled):
        NovigClient("public-data", key_id="k")                           # the only client the job builds refuses credentials


def test_setup_script_is_shadow_only_and_wired_per_repo_convention():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sh = open(os.path.join(root, "deploy", "setup_novig_shadow.sh"), encoding="ascii").read()
    assert 'PAUSE:-0' in sh and "roles/run.invoker" in sh and "mlb-betting-feeds" in sh
    assert 'set-secrets="MLB_GCS_BUCKET=mlb-gcs-bucket:latest"' in sh                       # the ONLY secret mounted
    code = "\n".join(l for l in sh.splitlines() if not l.lstrip().startswith("#"))
    assert "gcloud secrets create" not in code and "NOVIG" not in code.replace("novig-shadow", "").replace("ev.novig", "")
    assert "build_all_images" not in code
