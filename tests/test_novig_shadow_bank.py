"""ev.novig.shadow_bank: append-only, idempotent GCS banking of the Novig shadow ledger and its restore / gate read path.
Local DirStore / fake GCS client only: no network, no credentials."""
import gzip
import json
import os
import re
from datetime import timedelta
from pathlib import Path

import pytest

from ev.novig import clv_gate as cg
from ev.novig import exec as nx
from ev.novig import shadow_bank as sb
from ev.props_config import props_config
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.risk import RiskGate
from tests.test_novig_exec import LEG, T0, FakeNC, point


def make_pass(ledger, nc=None, p=None, dt=0.5):
    cfg = props_config(env={})
    risk = RiskGate(cfg, ledger, nx.MODE)
    return nx.run_pass(snap_ts=T0.isoformat(), cands=[(LEG, p or point(), 300.0)], cfg=cfg, nc=nc or FakeNC(0.50, 0.48),
                       ledger=ledger, risk=risk, clock=lambda: T0 + timedelta(seconds=dt), pace_s=0)


def counts(ledger):
    with ledger._lock:
        return {t: ledger._c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("orders", "decisions", "fills", "kv")}


@pytest.fixture
def store(tmp_path):
    return sb.DirStore(str(tmp_path / "bank"))


# ------------------------------------------------------------------ round trip
def test_bank_then_restore_reproduces_the_ledger_and_the_gate_reads_the_same_bets(store, tmp_path):
    led = Ledger(str(tmp_path / "src.sqlite"))
    s = make_pass(led)
    assert s["hist"].get("EXECUTED") == 1
    res = sb.bank(led, store, now=T0)
    assert res["new_rows"] == {"orders": 1, "decisions": 1, "fills": 1, "kv": 1} and res["skipped_known"] == 0
    assert [c["key"].split("/")[:3] for c in res["chunks"]] == [["Novig", "shadow_ledger", s["day"]]]

    back = sb.banked_ledger(store)
    assert counts(back) == counts(led)
    assert cg.ledger_bets(back._c) == cg.ledger_bets(led._c) and len(cg.ledger_bets(back._c)) == 1
    with led._lock, back._lock:                                    # original timestamps survive, they are the entry time
        q = "SELECT ts, day, mode, intent_id, filled_cost, fill_count, fee, raw_json FROM orders"
        assert [tuple(r) for r in back._c.execute(q)] == [tuple(r) for r in led._c.execute(q)]
        assert [tuple(r) for r in back._c.execute("SELECT * FROM kv")] == [tuple(r) for r in led._c.execute("SELECT * FROM kv")]


def test_every_decision_is_banked_not_only_the_fills(store, tmp_path):
    led = Ledger(str(tmp_path / "src.sqlite"))
    make_pass(led, FakeNC(0.30, 0.30), p=point(p_over=0.50))        # no edge: a rejection decision, no order
    res = sb.bank(led, store, now=T0)
    assert res["new_rows"]["decisions"] == 1 and res["new_rows"]["orders"] == 0
    back = sb.banked_ledger(store)
    assert back.histogram(back._c.execute("SELECT day FROM decisions").fetchone()[0]) == led.histogram(
        led._c.execute("SELECT day FROM decisions").fetchone()[0])


# ------------------------------------------------------------------ idempotent / append-safe
def test_banking_twice_writes_nothing_new_and_never_rewrites_a_chunk(store, tmp_path):
    led = Ledger(str(tmp_path / "src.sqlite"))
    make_pass(led)
    first = sb.bank(led, store, now=T0)
    keys = store.list(sb.PREFIX + "/")
    blobs = {k: store.read(k) for k in keys}
    again = sb.bank(led, store, run_id="other-run")                 # cold: reads the bank, finds everything already there
    assert again["chunks"] == [] and again["skipped_known"] == 4 and sum(again["new_rows"].values()) == 0
    assert store.list(sb.PREFIX + "/") == keys                     # no new object
    assert {k: store.read(k) for k in keys} == blobs               # no object changed
    assert first["new_rows"]["orders"] == 1
    # a writer that does NOT know the bank (known=set()) may write a duplicate chunk, but never touches the old ones and the
    # restored ledger is unchanged: reads dedupe on the natural key
    blind = sb.bank(led, store, known=set(), run_id="blind-run")
    assert blind["chunks"][0]["created"] and all(store.read(k) == v for k, v in blobs.items())
    assert counts(sb.banked_ledger(store)) == {"orders": 1, "decisions": 1, "fills": 1, "kv": 1}


def test_incremental_bank_writes_only_the_new_rows(store, tmp_path):
    led = Ledger(str(tmp_path / "src.sqlite"))
    make_pass(led, FakeNC(0.30, 0.30), p=point(p_over=0.50))        # a rejection: a decision, no order
    known = set()
    r1 = sb.bank(led, store, known=known, run_id="r1")
    make_pass(led, dt=1.5)                                          # later: the same player now fills
    r2 = sb.bank(led, store, known=known, run_id="r2")
    assert r1["new_rows"] == {"orders": 0, "decisions": 1, "fills": 0, "kv": 0}
    assert r2["new_rows"] == {"orders": 1, "decisions": 1, "fills": 1, "kv": 1}
    back = sb.banked_ledger(store)
    assert counts(back) == counts(led) == {"orders": 1, "decisions": 2, "fills": 1, "kv": 1}


def test_two_writers_banking_the_same_fill_are_deduped_on_restore(store, tmp_path):
    a, b = Ledger(str(tmp_path / "a.sqlite")), Ledger(str(tmp_path / "b.sqlite"))
    make_pass(a)
    make_pass(b)                                                   # same intent_id (deterministic), same fill ids
    sb.bank(a, store, run_id="writer-a")
    sb.bank(b, store, known=set(), run_id="writer-b")                # concurrent writer: did not see a's chunk
    assert len([k for k in store.list(sb.PREFIX + "/") if k.endswith(".gz")]) >= 2     # both wrote, neither clobbered
    back = sb.banked_ledger(store)
    c = counts(back)
    assert c["orders"] == 1 and c["fills"] == 1 and c["kv"] == 1
    assert len(cg.ledger_bets(back._c)) == 1                        # n counts the bet once


def test_create_only_never_overwrites(store):
    assert store.write_new("Novig/shadow_ledger/2026-11-05/x.jsonl.gz", b"one") is True
    assert store.write_new("Novig/shadow_ledger/2026-11-05/x.jsonl.gz", b"two") is False
    assert store.read("Novig/shadow_ledger/2026-11-05/x.jsonl.gz") == b"one"


def test_identical_content_in_a_retried_run_is_a_noop(store, tmp_path):
    led = Ledger(str(tmp_path / "src.sqlite"))
    make_pass(led)
    r1 = sb.bank(led, store, known=set(), run_id="same-run")
    r2 = sb.bank(led, store, known=set(), run_id="same-run")        # a crashed-and-retried run that forgot what it banked
    assert [c["key"] for c in r1["chunks"]] == [c["key"] for c in r2["chunks"]]
    assert r1["chunks"][0]["created"] is True and r2["chunks"][0]["created"] is False
    assert counts(sb.banked_ledger(store)) == {"orders": 1, "decisions": 1, "fills": 1, "kv": 1}


# ------------------------------------------------------------------ modes
def test_only_novig_modes_are_banked_by_default(store, tmp_path):
    led = Ledger(str(tmp_path / "src.sqlite"))
    make_pass(led)
    led.record_order(day="2026-10-12", mode="shadow", intent_id="kalshi-1", client_order_id="c", order_id="o", ticker="KX",
                     game_pk="g", side="yes", contracts=1, limit_price=0.5, status="FILLED", fill_count=1.0, filled_cost=0.5)
    led.record_decision(day="2026-10-12", mode="shadow", outcome="EXECUTED", intent_id="kalshi-1")
    led.kv_set("grp:2026-10-12:g|p|rec_yds", "kalshi-1")                    # a Kalshi hold marker (no venue prefix)
    led.kv_set("kill_reason", "x")                                          # never banked
    sb.bank(led, store, now=T0)
    back = sb.banked_ledger(store)
    with back._lock:
        assert {r[0] for r in back._c.execute("SELECT mode FROM orders")} == {"novig"}
        assert [r[0] for r in back._c.execute("SELECT k FROM kv")] == [f"grp:{nx_day(led)}:novig|{LEG.game_key}|{LEG.player_norm}|{LEG.stat}"]
    full = Ledger(":memory:")
    allstore = sb.DirStore(str(store.root) + "_all")
    sb.bank(led, allstore, modes=None, now=T0)
    sb.restore_into(full, allstore)
    with full._lock:
        assert {r[0] for r in full._c.execute("SELECT mode FROM orders")} == {"novig", "shadow"}
        assert "kill_reason" not in {r[0] for r in full._c.execute("SELECT k FROM kv")}


def nx_day(led):
    with led._lock:
        return led._c.execute("SELECT day FROM orders WHERE mode='novig'").fetchone()[0]


# ------------------------------------------------------------------ restore keeps the run state
def test_restored_ledger_still_holds_the_position_and_the_intent(store, tmp_path):
    led = Ledger(str(tmp_path / "src.sqlite"))
    make_pass(led)
    sb.bank(led, store, now=T0)
    fresh = Ledger(":memory:")
    res = sb.restore_into(fresh, store, days=[nx_day(led)], tables=("orders", "fills", "kv"))
    assert res["restored"] == {"orders": 1, "fills": 1, "kv": 1} and counts(fresh)["decisions"] == 0
    s = make_pass(fresh)                                           # a later firing, a different process
    assert s["hist"] == {"REJ_GROUP_HELD": 1} and counts(fresh)["orders"] == 1
    # decisions of the new process are banked, the restored rows are not re-written
    r = sb.bank(fresh, store, known=res["known"], run_id="later")
    assert r["new_rows"]["orders"] == 0 and r["new_rows"]["fills"] == 0 and r["new_rows"]["decisions"] == 0
    assert counts(sb.banked_ledger(store))["orders"] == 1


def test_restore_into_a_populated_ledger_adds_only_missing_rows(store, tmp_path):
    led = Ledger(str(tmp_path / "src.sqlite"))
    make_pass(led)
    sb.bank(led, store, now=T0)
    before = counts(led)
    res = sb.restore_into(led, store)
    assert counts(led) == before and sum(res["restored"].values()) == 0 and len(res["known"]) == 4


def test_day_filter_reads_only_those_day_folders(store, tmp_path):
    led = Ledger(str(tmp_path / "src.sqlite"))
    make_pass(led)
    sb.bank(led, store, now=T0)
    assert sb.chunk_keys(store, days=["1999-01-01"]) == []
    assert len(sb.chunk_keys(store, days=[nx_day(led)])) == 1
    assert sb.chunk_keys(store) == sb.chunk_keys(store, days=[nx_day(led)])


def test_a_corrupt_chunk_raises_instead_of_silently_dropping_fills(store):
    store.write_new(f"{sb.PREFIX}/2026-11-05/bad_deadbeef.jsonl.gz", b"not gzip")
    with pytest.raises(Exception):
        sb.banked_ledger(store)


def test_verdict_object_is_not_mistaken_for_a_chunk(store):
    store.put(sb.VERDICT_KEY, b"{}")
    assert sb.chunk_keys(store) == []
    assert counts(sb.banked_ledger(store)) == {"orders": 0, "decisions": 0, "fills": 0, "kv": 0}


# ------------------------------------------------------------------ the gate reads the banked copy
def test_clv_gate_cli_compute_can_read_the_bank(store, tmp_path, capsys):
    led = Ledger(str(tmp_path / "src.sqlite"))
    make_pass(led)
    sb.bank(led, store, now=T0)
    out = tmp_path / "v.json"
    assert cg.main(["compute", "--bank", "--store", f"dir:{store.root}", "--out", str(out)]) == 0
    v = json.loads(out.read_text())
    assert v["coverage"]["filled_bets"] == 1 and v["coverage"]["dropped_reasons"] == {"no_closing_snapshot": 1}
    assert v["slices"]["nfl|rec_yds"]["status"] == "insufficient_data" and not any(s["open"] for s in v["slices"].values())


# ------------------------------------------------------------------ GCS store contract (fake client)
class _Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    def upload_from_string(self, data, content_type=None, if_generation_match=None, timeout=None):
        from google.api_core.exceptions import PreconditionFailed
        if if_generation_match == 0 and self.name in self.bucket.objs:
            raise PreconditionFailed("exists")
        self.bucket.objs[self.name] = data

    def download_as_bytes(self, timeout=None):
        return self.bucket.objs[self.name]


class _Bucket:
    def __init__(self):
        self.objs = {}

    def blob(self, name):
        return _Blob(self, name)


class _Client:
    def __init__(self):
        self.b = _Bucket()

    def bucket(self, _name):
        return self.b

    def list_blobs(self, _bucket, prefix=""):
        return [_Blob(self.b, n) for n in self.b.objs if n.startswith(prefix)]


def test_gcs_store_writes_create_only_and_roundtrips(tmp_path):
    client = _Client()
    gs = sb.GcsStore("bkt", client=client)
    led = Ledger(str(tmp_path / "src.sqlite"))
    make_pass(led)
    r = sb.bank(led, gs, now=T0)
    assert r["chunks"][0]["created"] and list(client.b.objs) == [r["chunks"][0]["key"]]
    assert gs.write_new(r["chunks"][0]["key"], b"clobber") is False and client.b.objs[r["chunks"][0]["key"]] != b"clobber"
    assert counts(sb.banked_ledger(gs)) == {"orders": 1, "decisions": 1, "fills": 1, "kv": 1}
    assert gzip.decompress(client.b.objs[r["chunks"][0]["key"]]).decode("ascii").startswith('{"hdr"')


def test_bucket_is_required_not_guessed(monkeypatch):
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    with pytest.raises(RuntimeError):
        sb.GcsStore()


# ------------------------------------------------------------------ safety: shadow records only
def test_bank_and_job_modules_have_no_order_client_or_credential_code():
    root = Path(__file__).resolve().parent.parent / "ev" / "novig"
    banned = re.compile(r"place_order|paper_orders|PaperVenue|NovigPaperVenue|trading_client|novig\.setup|signing|private_key|"
                        r"api_key|password|os\.environ\[|KEXEC_RFQ|load_ed25519|requests|urllib|httpx|socket", re.I)
    for name in ("shadow_bank.py", "shadow_job.py"):
        src = (root / name).read_text(encoding="ascii")                                # also proves ASCII-only
        code = re.sub(r'"""[\s\S]*?"""', "", "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#")))
        assert not banned.search(code), (name, banned.search(code).group(0))
    job = (root / "shadow_job.py").read_text(encoding="ascii")
    assert "NovigClient(\"public-data\")" in job and "paper_venue=None" in job
    assert os.path.exists(root / "shadow_bank.py")
