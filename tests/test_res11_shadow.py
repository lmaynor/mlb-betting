"""RES-11 Stage 2 shadow logger: natural-key dedupe, G6 disposition, bank
round-trip, next-snapshot fill observation, and exact equivalence of the
shadow's G2 minutes-per-game table with the Stage 1 driver's builder
(RUL-20261116-intraday-pop-approach)."""
import gzip
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from ev import res11_shadow as SH
import run_res11_stage1 as S1
from ev.novig.shadow_bank import make_store


def _row(**kw):
    base = {"sport": "nba", "event_id": "evt1", "player_norm": "alpha one",
            "stat": "points", "line": 24.5, "book": "betmgm", "side": "under",
            "ts": "2026-11-16T17:31:00+00:00", "disposition": "trigger"}
    base.update(kw)
    return base


def test_natural_key_stable_and_distinct():
    assert SH.natural_key(_row()) == SH.natural_key(_row())
    assert SH.natural_key(_row()) != SH.natural_key(_row(side="over"))
    assert SH.natural_key(_row()) != SH.natural_key(_row(line=25.5))
    assert SH.natural_key(_row()) != SH.natural_key(
        _row(ts="2026-11-16T18:01:00+00:00"))


def test_g6_disposition_only_positive_flag_vetoes():
    assert SH.disposition_for(True) == "g6_vetoed"
    assert SH.disposition_for(False) == "trigger"
    assert SH.disposition_for(None) == "trigger"     # unevaluable != veto


def test_chunk_roundtrip_and_restore_keys(tmp_path):
    store = make_store(f"dir:{tmp_path}")
    rows = [_row(), _row(book="fanduel", ts="2026-11-16T18:01:00+00:00")]
    key = SH.write_chunk(store, rows, "20261116T180000Z")
    assert key.startswith("RES11/shadow/chunks/20261116T180000Z-")
    keys, restored = SH.restore_bank(store)
    assert keys == {SH.natural_key(r) for r in rows}
    assert len(restored) == 2
    # banking the same rows again under a new run id adds rows to the bank,
    # but restore still dedupes by natural key at read time in run()
    SH.write_chunk(store, rows, "20261116T183000Z")
    keys2, restored2 = SH.restore_bank(store)
    assert keys2 == keys and len(restored2) == 4


def _quotes_frame():
    ts = pd.Timestamp("2026-11-16T17:31:00Z")
    ts2 = pd.Timestamp("2026-11-16T18:01:00Z")
    cols = ["sport", "event_id", "ts", "player_norm", "stat", "line", "book",
            "q_over", "q_under", "over_am", "under_am"]
    rows = [
        ["nba", "evt1", ts, "alpha one", "points", 24.5, "betmgm", 0.55, 0.50, -120, 100],
        ["nba", "evt1", ts2, "alpha one", "points", 24.5, "betmgm", 0.54, 0.51, -118, 102],
        ["nba", "evt2", ts, "beta two", "points", 10.5, "betmgm", 0.50, 0.55, 100, -120],
    ]
    return pd.DataFrame(rows, columns=cols), ts, ts2


def test_filled_next_present_absent_and_none():
    q, ts, ts2 = _quotes_frame()
    eng = SimpleNamespace(q=q)
    got = SH.filled_next(eng, "nba", "evt1", ts, "alpha one", "points", 24.5,
                         "betmgm", "over")
    assert got["filled_next"] is True and got["q_book_next"] == pytest.approx(0.54)
    assert got["next_snap_ts"] == ts2.isoformat()
    # next snapshot exists for the event but the book/line is gone -> not filled
    got = SH.filled_next(eng, "nba", "evt1", ts, "alpha one", "points", 25.5,
                         "betmgm", "over")
    assert got["filled_next"] is False and got["q_book_next"] is None
    # no later snapshot at all -> unobserved
    got = SH.filled_next(eng, "nba", "evt2", ts, "beta two", "points", 10.5,
                         "betmgm", "over")
    assert got["filled_next"] is None and got["next_snap_ts"] is None


def test_mpg_matches_stage1_builder(tmp_path):
    masters = tmp_path / "masters"
    masters.mkdir()
    nba = pd.DataFrame([
        # date, team, pid, name, played, pts, minutes, threes, rebounds
        ["2026-10-21", "AAA", 1, "Alpha One", 1, 20, "30:00", 2, 5],
        ["2026-10-23", "AAA", 1, "Alpha One", 1, 18, 20.0, 1, 4],
        ["2026-10-25", "AAA", 1, "Alpha One", 1, 22, 28.0, 3, 6],
        ["2026-10-21", "BBB", 2, "Beta Two", 1, 8, 10.0, 0, 2],
        ["2026-10-23", "BBB", 2, "Beta Two", 0, 0, 0.0, 0, 0],      # DNP: excluded
        ["2026-10-25", "BBB", 2, "Beta Two", 1, 30, 40.0, 4, 9],
    ], columns=["date", "team_abbr", "player_id", "name", "played", "points",
                "minutes", "three_points_made", "rebounds"])
    nba.to_csv(masters / "nba_boxscores.csv", index=False)
    pd.DataFrame(columns=["date", "team_abbr", "name", "played", "sog"]).to_csv(
        masters / "nhl_skaters.csv", index=False)
    pd.DataFrame(columns=["date", "team_abbr", "name", "played", "starter",
                          "saves"]).to_csv(masters / "nhl_goalies.csv", index=False)

    _, mpg_stage1, _, _ = S1.build_outcomes(str(tmp_path))
    mpg_shadow = SH.build_mpg_by_norm(str(masters))
    assert mpg_shadow == mpg_stage1
    # hand check: Alpha before 10-25 = mean(30, 20) = 25; Beta before 10-25 = 10
    assert mpg_shadow[("alpha one", "2026-10-25")] == pytest.approx(25.0)
    assert mpg_shadow[("beta two", "2026-10-25")] == pytest.approx(10.0)


def test_same_episode_suppression_and_rearm():
    import pandas as pd
    t0 = pd.Timestamp("2026-11-16T15:00:00Z")
    t1 = pd.Timestamp("2026-11-16T17:00:00Z")
    t2 = pd.Timestamp("2026-11-16T19:00:00Z")
    key = ("nba", "evt1", "alpha one", "points", 24.5, "betmgm", "over")
    cand = _row(side="over", ts=t2.isoformat())
    banked = _row(side="over", ts=t0.isoformat())
    # price stayed popped across the whole interval -> same episode
    dser = {key: [(t0, 0.06), (t1, 0.055), (t2, 0.05)]}
    assert SH.same_episode(dser, cand, banked) is True
    # book repriced back inside the delta in between -> genuine re-arm
    dser = {key: [(t0, 0.06), (t1, 0.02), (t2, 0.05)]}
    assert SH.same_episode(dser, cand, banked) is False
    # no series visible between the sightings -> no dip observed -> same episode
    assert SH.same_episode({}, cand, banked) is True
    # a different book is a different base key (suppression never compares them)
    other = _row(side="over", ts=t0.isoformat(), book="fanduel")
    assert SH.base_key(cand) != SH.base_key(other)


def test_chunk_is_gzipped_jsonl(tmp_path):
    store = make_store(f"dir:{tmp_path}")
    key = SH.write_chunk(store, [_row()], "20261116T180000Z")
    raw = (tmp_path / "RES11" / "shadow" / "chunks" / key.rsplit("/", 1)[-1]).read_bytes()
    lines = gzip.decompress(raw).decode().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["event_id"] == "evt1"
