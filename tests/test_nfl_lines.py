"""nfl.props.lines -- banked NFL prop market-lines table. Offline; payloads are tiny synthetic copies of the REAL
ParlayAPI single-event shape (tests/fixtures/ev/nfl_props_event.json, captured 2026-10-04)."""
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from ev.props import build_consensus, parse_event_props
from mlb_core.odds.utils import american_to_implied_prob, devig_two_way
from nfl.props import lines as L

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "ev", "nfl_props_event.json")
UTC = timezone.utc
KICK = datetime(2026, 10, 4, 18, 0, tzinfo=UTC)          # 2:00 pm ET


def _book(key, over, under, player="Tyler Warren", point=49.5, mkey="player_rec_yds", last_update="2026-10-04T16:00:00Z"):
    return {"key": key, "markets": [{"key": mkey, "last_update": last_update, "outcomes": [
        {"name": f"Over {player}", "description": player, "price": over, "point": point},
        {"name": f"Under {player}", "description": player, "price": under, "point": point}]}]}


def _payload(books, commence="2026-10-04T18:00:00Z", reported=True, away="Indianapolis Colts",
             home="Washington Commanders"):
    return {"id": "evt1", "sport_key": "americanfootball_nfl", "commence_time": commence, "home_team": home,
            "away_team": away, "bookmakers": books, "commence_time_reported": reported}


def _ref(ts, event="evt1"):
    day = ts.astimezone(L.ET).date().isoformat()
    return L.RawRef(f"EVSweep/nfl_props/raw/{day}/{event}_{ts:%H%M%S}.json", day, event, ts)


def _snap(books, ts, **kw):
    s, why = L.snapshot_from_payload(_payload(books, **kw), _ref(ts))
    assert why == "ok"
    return s


THREE = lambda: [_book("draftkings", -110, -110), _book("fanduel", -120, 100), _book("betmgm", -105, -115)]


# ---------------------------------------------------------------------------- timestamps / keys
def test_snapshot_ts_recovers_utc_from_et_folder_and_utc_clock():
    # folder = ET day, file = UTC wall clock: 02:30 UTC on the 5th is still the 4th in New York
    assert L.snapshot_ts("2026-10-04", "023000") == datetime(2026, 10, 5, 2, 30, tzinfo=UTC)
    assert L.snapshot_ts("2026-10-04", "070716") == datetime(2026, 10, 4, 7, 7, 16, tzinfo=UTC)
    assert L.snapshot_ts("2026-10-05", "042227") == datetime(2026, 10, 5, 4, 22, 27, tzinfo=UTC)
    assert L.snapshot_ts("nope", "070716") is None


def test_parse_raw_key_and_listing_filters_and_sorts():
    keys = ["EVSweep/nfl_props/raw/2026-10-12/aaa_234337.json", "EVSweep/nfl_props/raw/2026-10-04/bbb_070716.json",
            "EVSweep/nfl_props/raw/2026-10-04/bbb_030000.json", "EVSweep/nfl_props/2026-10-04/cycle_070716.csv",
            "EVSweep/nfl_props/raw/2026-10-04/notes.txt"]
    refs = L.list_raw_refs(lambda prefix: keys)
    assert [r.event_id for r in refs] == ["bbb", "bbb", "aaa"]
    assert refs[0].ts == datetime(2026, 10, 5, 3, 0, tzinfo=UTC) or refs[0].ts < refs[1].ts
    assert refs[0].ts < refs[1].ts < refs[2].ts
    assert [r.et_day for r in L.list_raw_refs(lambda p: keys, start="2026-10-12")] == ["2026-10-12"]
    assert [r.et_day for r in L.list_raw_refs(lambda p: keys, end="2026-10-04")] == ["2026-10-04"] * 2


def test_game_key_matches_nflverse_style_and_et_date():
    s = _snap(THREE(), KICK - timedelta(hours=1))
    assert (s.away, s.home, s.game_key) == ("IND", "WAS", "nfl:2026-10-04:IND@WAS")
    # Monday night kick 00:15 UTC on the 6th is Oct 5 in New York; Jacksonville is JAC (the ev/Kalshi code)
    s2 = _snap(THREE(), datetime(2026, 10, 5, 12, tzinfo=UTC), commence="2026-10-06T00:15:00Z",
               away="Jacksonville Jaguars", home="New Orleans Saints")
    assert s2.game_key == "nfl:2026-10-05:JAC@NO"


def test_unknown_team_or_garbage_payload_is_rejected_not_guessed():
    ref = _ref(KICK - timedelta(hours=1))
    assert L.snapshot_from_payload(_payload(THREE(), away="Toronto Argonauts"), ref) == (None, "unknown_teams")
    assert L.snapshot_from_payload(["not", "a", "dict"], ref) == (None, "bad_payload")
    assert L.snapshot_from_payload({"commence_time": None, "bookmakers": []}, ref) == (None, "no_commence")


# ---------------------------------------------------------------------------- pre-kick filter
def test_pre_kick_filter_drops_at_and_after_kick_and_unreported_commence():
    snaps = [_snap(THREE(), KICK - timedelta(minutes=30)),
             _snap(THREE(), KICK - timedelta(seconds=1)),
             _snap(THREE(), KICK),                                    # AT kickoff -> dropped
             _snap(THREE(), KICK + timedelta(minutes=45)),            # in-play -> dropped
             _snap(THREE(), KICK - timedelta(minutes=10), reported=False)]   # placeholder start time -> dropped
    rep = {}
    series, books = L.build_lines(snaps, report=rep)
    assert sorted(series["snap_ts"]) == sorted([KICK - timedelta(minutes=30), KICK - timedelta(seconds=1)])
    assert (series["mins_to_kick"] > 0).all()
    assert rep["dropped_not_pre_kick"] == 2 and rep["dropped_commence_unreported"] == 1
    assert set(books["snap_ts"]) == set(series["snap_ts"])           # the books table obeys the same filter
    # an extra safety margin is honoured
    series2, _ = L.build_lines(snaps, min_lead_min=20)
    assert list(series2["snap_ts"]) == [KICK - timedelta(minutes=30)]


# ---------------------------------------------------------------------------- consensus / DFS
def _expected_median(prices):
    fair = sorted(float(devig_two_way(american_to_implied_prob(o), american_to_implied_prob(u), "proportional")[0])
                  for o, u in prices)
    return fair[len(fair) // 2] if len(fair) % 2 else (fair[len(fair) // 2 - 1] + fair[len(fair) // 2]) / 2


def test_consensus_equals_ev_props_and_dfs_and_onesided_are_excluded():
    books = THREE() + [_book("underdog", -112, -112), _book("sleeper", -130, -120), _book("fliff", -115, -115)]
    books.append({"key": "bovada", "markets": [{"key": "player_rec_yds", "outcomes": [
        {"name": "Yes", "description": "Tyler Warren (IND)", "price": -120, "point": 49.5}]}]})
    snap = _snap(books, KICK - timedelta(hours=1))
    series, bk = L.build_lines([snap], pooled=False)
    assert len(series) == 1
    r = series.iloc[0]
    ref = build_consensus(snap.quotes)[("tyler warren", "rec_yds", 49.5)]        # ev.props is the source of truth
    assert r["p_over"] == ref.p_over and r["n_books"] == ref.n_books == 3 and r["disp"] == ref.disp
    assert r["p_over"] == pytest.approx(_expected_median([(-110, -110), (-120, 100), (-105, -115)]))
    assert r["n_dfs"] == 3 and r["n_onesided"] == 1 and set(r["books"].split(",")) == {"draftkings", "fanduel", "betmgm"}
    assert r["team_hint"] == "IND"
    assert json.loads(r["book_fair"]).keys() == {"draftkings", "fanduel", "betmgm"}
    roles = dict(zip(bk["book"], bk["role"]))
    assert roles == {"draftkings": "consensus", "fanduel": "consensus", "betmgm": "consensus", "underdog": "dfs",
                     "sleeper": "dfs", "fliff": "dfs", "bovada": "onesided"}
    assert bk[bk["role"] != "consensus"]["fair_over"].isna().all()           # DFS / one-sided carry no fair price
    dk = bk[bk["book"] == "draftkings"].iloc[0]
    assert (dk["over"], dk["under"]) == (-110, -110) and dk["hold"] == pytest.approx(2 * american_to_implied_prob(-110) - 1)


def test_alias_duplicate_market_keys_collapse_last_wins_like_consensus():
    fd = _book("fanduel", -120, 100)
    fd["markets"].append({"key": "player_receiving_yards", "outcomes": [      # same stat under a second key alias
        {"name": "Over Tyler Warren", "description": "Tyler Warren", "price": -125, "point": 49.5},
        {"name": "Under Tyler Warren", "description": "Tyler Warren", "price": 105, "point": 49.5}]})
    snap = _snap([fd, _book("draftkings", -110, -110)], KICK - timedelta(hours=1))
    assert snap.meta["dup_quotes"] == 1
    series, bk = L.build_lines([snap], pooled=False)
    assert (bk["book"] == "fanduel").sum() == 1 and series.iloc[0]["n_books"] == 2
    fanduel = bk[bk["book"] == "fanduel"].iloc[0]
    assert (fanduel["over"], fanduel["under"]) == (-125, 105)
    assert series.iloc[0]["p_over"] == build_consensus(snap.quotes)[("tyler warren", "rec_yds", 49.5)].p_over


def test_real_fixture_payload_roundtrips_through_the_same_consensus():
    payload = json.load(open(FIX))
    ts = datetime.fromisoformat(payload["commence_time"].replace("Z", "+00:00")) - timedelta(hours=2)
    snap, why = L.snapshot_from_payload(payload, _ref(ts, payload["id"]))
    assert why == "ok" and snap.game_key == "nfl:2026-10-04:IND@WAS"
    series, books = L.build_lines([snap])
    ref = build_consensus(parse_event_props(payload)[0])
    assert len(series) == len(ref) > 0
    for _, r in series.iterrows():
        c = ref[(r["player_norm"], r["stat"], r["point"])]
        assert r["p_over"] == c.p_over and r["n_books"] == c.n_books
    assert not any(b in set(series["books"].str.cat(sep=",").split(",")) for b in ("sleeper", "underdog"))


# ---------------------------------------------------------------------------- opening / closing
def _three_snapshots():
    t0 = KICK - timedelta(hours=6)
    return [
        _snap([_book("draftkings", -110, -110), _book("fanduel", -110, -110)], t0),                       # open: 0.50
        _snap(THREE(), KICK - timedelta(hours=3)),
        _snap([_book("draftkings", -130, 110), _book("fanduel", -135, 115), _book("betmgm", -125, 105)],
              KICK - timedelta(minutes=25)),                                                              # close
    ]


def test_opening_and_closing_selection_and_move():
    series, _ = L.build_lines(_three_snapshots(), pooled=False)
    oc = L.collapse_props(series)
    assert len(oc) == 1
    r = oc.iloc[0]
    assert r["n_snaps"] == 3 and r["open_ts"] == KICK - timedelta(hours=6) and r["close_ts"] == KICK - timedelta(minutes=25)
    assert r["open_n_books"] == 2 and r["close_n_books"] == 3
    assert r["open_p_over"] == pytest.approx(0.5)
    assert r["close_p_over"] == pytest.approx(_expected_median([(-130, 110), (-135, 115), (-125, 105)]))
    assert r["move"] == pytest.approx(r["close_p_over"] - r["open_p_over"]) and r["move"] > 0.04
    assert r["open_lead_min"] == pytest.approx(360) and r["close_lead_min"] == pytest.approx(25)
    assert r["alive_at_close"] and r["close_fresh"]
    assert r["n_common"] == 2 and r["move_common"] == pytest.approx(
        _expected_median([(-130, 110), (-135, 115)]) - 0.5)                  # like-for-like books only
    # restricting to >= 3 books moves the OPEN to the first snapshot that had three books
    oc3 = L.collapse_props(series, min_books=3).iloc[0]
    assert oc3["open_ts"] == KICK - timedelta(hours=3) and oc3["n_snaps"] == 2


def test_prop_that_left_the_board_has_no_true_close_and_a_stale_close_is_flagged():
    first = _snap(THREE(), KICK - timedelta(hours=5))
    # the final pre-kick snapshot prices a DIFFERENT point (the line moved to 50.5): the 49.5 prop is not alive
    last = _snap([_book("draftkings", -110, -110, point=50.5), _book("fanduel", -110, -110, point=50.5)],
                 KICK - timedelta(minutes=30))
    series, _ = L.build_lines([first, last], pooled=False)
    oc = L.collapse_props(series).set_index("point")
    assert not oc.loc[49.5, "alive_at_close"] and oc.loc[49.5, "close_lead_min"] == pytest.approx(300)
    assert not oc.loc[49.5, "close_fresh"]                       # last seen 5h before kick: not a closing line
    assert oc.loc[50.5, "alive_at_close"] and oc.loc[50.5, "n_snaps"] == 1
    moves = L.player_stat_moves(series)
    assert len(moves) == 1 and moves.iloc[0]["point_move"] == pytest.approx(1.0) and moves.iloc[0]["line_changed"]


def test_closing_proxy_is_per_game_last_snapshot_and_flag_is_set():
    series, _ = L.build_lines(_three_snapshots(), pooled=False)
    assert series["is_game_last"].tolist() == [False, False, True]


# ---------------------------------------------------------------------------- as-of carry-forward / quality
def test_asof_carries_a_flapping_book_forward_but_not_past_the_staleness_window():
    full = _snap(THREE(), KICK - timedelta(minutes=80), )
    # fanduel's market last_update is stamped relative to its snapshot time; later snapshots drop betmgm entirely
    part1 = _snap([_book("draftkings", -112, -108), _book("fanduel", -120, 100)], KICK - timedelta(minutes=50))
    part2 = _snap([_book("draftkings", -115, -105)], KICK - timedelta(minutes=25))
    for s in (full, part1, part2):                              # make quote times == snapshot times for the test
        s.ages = {k: 0.0 for k in s.ages}
    series, books = L.build_lines([full, part1, part2], pooled=False)
    asof = L.asof_series(series, books, max_stale_min=40).set_index("snap_ts")
    assert asof.loc[KICK - timedelta(minutes=50), "n_books"] == 3          # betmgm 30 min old: still live
    assert asof.loc[KICK - timedelta(minutes=50), "n_fresh"] == 2
    last = asof.loc[KICK - timedelta(minutes=25)]
    assert last["n_books"] == 2 and last["books"] == "draftkings,fanduel"   # betmgm (55 min) aged out; fanduel (25) carried
    assert last["n_fresh"] == 1
    # DK's NEW price wins over its older one; the carried fanduel quote is the 50-min snapshot's
    fair_dk = float(devig_two_way(american_to_implied_prob(-115), american_to_implied_prob(-105))[0])
    fair_fd = float(devig_two_way(american_to_implied_prob(-120), american_to_implied_prob(100))[0])
    assert last["p_over"] == pytest.approx((fair_dk + fair_fd) / 2)
    oc = L.collapse_props(asof.reset_index()).iloc[0]                       # the as-of table collapses like the raw one
    assert oc["close_ts"] == KICK - timedelta(minutes=25) and oc["n_snaps"] == 3
    q = L.snapshot_quality(series, books)
    assert q["n_props"].tolist() == [1, 1, 1] and q["n_real_books"].tolist() == [3, 2, 1]


# ---------------------------------------------------------------------------- driver + CLI
def _write_raw_tree(root: Path):
    """A local mirror laid out like the GCS bank: <root>/raw/<ET day>/<event>_<HHMMSS>.json"""
    n = 0
    for snap_ts, books in ((KICK - timedelta(hours=3), THREE()), (KICK - timedelta(minutes=30), THREE()),
                           (KICK + timedelta(minutes=20), THREE())):                # last one is IN-PLAY
        ref = _ref(snap_ts)
        p = root / "raw" / ref.et_day / f"{ref.event_id}_{snap_ts:%H%M%S}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_payload(books)))
        n += 1
    (root / "raw" / "2026-10-04" / "evt2_061500.json").write_text("{not json")        # corrupt payload is skipped
    return n


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "nfl_banked_lines", Path(__file__).resolve().parent.parent / "scripts" / "nfl_banked_lines.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_from_source_and_cli_end_to_end(tmp_path, capsys):
    _write_raw_tree(tmp_path / "bank")
    mod = _load_script()
    lister, reader = mod.dir_source(str(tmp_path / "bank"))
    series, books, rep = L.build_from_source(lister, reader)
    assert rep["raw_refs"] == 4 and rep["unreadable"] == 1 and rep["snapshots_parsed"] == 3
    assert rep["dropped_not_pre_kick"] == 1 and rep["snapshots_kept"] == 2 and len(series) == 2
    out = tmp_path / "out"
    rc = mod.main(["--raw-dir", str(tmp_path / "bank"), "--out", str(out), "--no-schedule"])
    assert rc == 0
    for f in ("lines_series.parquet", "lines_books.parquet", "props_open_close.parquet", "props_open_close.csv",
              "props_open_close_asof.parquet", "snapshot_quality.parquet", "coverage_summary.txt"):
        assert (out / f).exists(), f
    oc = pd.read_parquet(out / "props_open_close.parquet")
    assert len(oc) == 1 and oc.iloc[0]["n_snaps"] == 2 and oc.iloc[0]["close_lead_min"] == pytest.approx(30)
    text = capsys.readouterr().out
    assert "PRE-KICK" in text and "dropped_not_pre_kick: 1" in text
    text.encode("ascii")                                                            # ASCII-only report


def test_schedule_join_adds_week_and_game_id():
    mod = _load_script()
    series, _ = L.build_lines(_three_snapshots(), pooled=False)
    sched = pd.DataFrame({"game_key": ["nfl:2026-10-04:IND@WAS"], "season": [2026], "week": [4], "game_id": ["2026_04_IND_WAS"]})
    out = mod.attach_schedule(series, loader=lambda: sched)
    assert set(out["week"]) == {4} and set(out["game_id"]) == {"2026_04_IND_WAS"}
    broken = mod.attach_schedule(series, loader=lambda: (_ for _ in ()).throw(RuntimeError("no gcs")))
    assert broken["week"].isna().all()                                              # best effort, never fatal
