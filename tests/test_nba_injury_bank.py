"""nba.data.injury_bank: append-only, change-only banking of the ESPN injury list. Offline (fake fetch, temp store)."""
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from nba.data import espn_injuries as EI
from nba.data import injury_bank as B


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    return tmp_path


def frame(rows, ok=True, error=None):
    df = pd.DataFrame(rows, columns=EI.COLUMNS)
    df.attrs.update({"ok": ok, "error": error})
    return df


def row(team="NYK", pid=1, status="Out", detail="Knee", updated="2026-10-16T12:00Z"):
    return [team, pid, f"P{pid}", status, detail, updated, status]


def T(h, m=0, day=16):
    return datetime(2026, 10, day, h, m, tzinfo=timezone.utc)


def test_first_snapshot_is_stored_with_pointer_and_et_day_key(local_store):
    r = B.snapshot(T(15, 30), lambda: frame([row(), row("BOS", 2)]))
    assert r["status"] == "stored" and r["rows"] == 2
    assert r["key"] == "NBA/injuries/espn/2026-10-16/injuries_153000.csv"
    saved = pd.read_csv(local_store / r["key"])
    assert list(saved.columns) == B.COLUMNS and (saved["fetched_at_utc"] == "2026-10-16T15:30:00+00:00").all()
    ptr = json.loads((local_store / "NBA/injuries/espn/latest.json").read_text())
    assert ptr["hash"] == r["hash"] and ptr["checked_at"] == ptr["fetched_at"]


def test_et_day_uses_the_eastern_date_not_utc(local_store):
    r = B.snapshot(T(2, 10, day=17), lambda: frame([row()]))                 # 22:10 ET on the 16th
    assert "/2026-10-16/" in r["key"]


def test_identical_list_is_not_rewritten_but_the_heartbeat_advances(local_store):
    B.snapshot(T(15), lambda: frame([row()]))
    r = B.snapshot(T(16), lambda: frame([row()]))
    assert r["status"] == "unchanged"
    ptr = json.loads((local_store / "NBA/injuries/espn/latest.json").read_text())
    assert ptr["fetched_at"].startswith("2026-10-16T15:00") and ptr["checked_at"].startswith("2026-10-16T16:00")
    assert len(list((local_store / "NBA/injuries/espn/2026-10-16").glob("*.csv"))) == 1


def test_row_order_does_not_change_the_hash_but_a_status_change_does(local_store):
    a = B.content_hash(frame([row(pid=1), row("BOS", 2)]))
    assert a == B.content_hash(frame([row("BOS", 2), row(pid=1)]))
    assert a != B.content_hash(frame([row(pid=1, status="Questionable"), row("BOS", 2)]))
    B.snapshot(T(15), lambda: frame([row()]))
    assert B.snapshot(T(16), lambda: frame([row(status="Questionable")]))["status"] == "stored"


def test_unusable_payload_is_never_stored_and_does_not_move_the_heartbeat(local_store):
    B.snapshot(T(15), lambda: frame([row()]))
    r = B.snapshot(T(16), lambda: frame([], ok=False, error="fetch_error:Timeout"))
    assert r == {"status": "skipped", "reason": "fetch_error:Timeout"}
    ptr = json.loads((local_store / "NBA/injuries/espn/latest.json").read_text())
    assert ptr["checked_at"].startswith("2026-10-16T15:00")


def test_empty_but_ok_list_is_a_real_snapshot(local_store):
    r = B.snapshot(T(15), lambda: frame([]))
    assert r["status"] == "stored" and r["rows"] == 0                         # nobody hurt is information


def test_unreadable_pointer_raises_instead_of_restarting_history(local_store):
    p = local_store / "NBA/injuries/espn"
    p.mkdir(parents=True)
    (p / "latest.json").write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        B.snapshot(T(15), lambda: frame([row()]))


def test_state_asof_returns_the_newest_earlier_snapshot_and_refuses_without_a_heartbeat(local_store):
    keys = []
    for h, st in ((15, "Out"), (17, "Questionable")):
        keys.append(B.snapshot(T(h), lambda st=st: frame([row(status=st)]))["key"])
    B.snapshot(T(19), lambda: frame([row(status="Questionable")]))          # unchanged -> heartbeat 19:00
    snaps = B.load_snapshots(keys)
    hb = json.loads((local_store / "NBA/injuries/espn/latest.json").read_text())["checked_at"]
    assert B.state_asof(snaps, T(16), hb)["status"].tolist() == ["Out"]
    assert B.state_asof(snaps, T(18, 30), hb)["status"].tolist() == ["Questionable"]
    assert B.state_asof(snaps, T(14), hb) is None                            # before the first snapshot: unknown
    assert B.state_asof(snaps, T(20), hb) is None                            # poller not seen since 19:00: unknown
    assert B.state_asof(snaps, T(18), None) is None
    assert B.state_asof(pd.DataFrame(columns=B.COLUMNS), T(18), hb) is None
