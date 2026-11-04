"""Point-in-time replay of real Kalshi combos (ev/kalshi_rfq_replay.py): no look-ahead, grading arithmetic, public-API pull."""
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from ev.kalshi_rfq_pricer import leg_from_row
from ev import kalshi_rfq_replay as rp

T0 = datetime(2026, 10, 25, 18, 0, tzinfo=timezone.utc).timestamp()


def row(ticker, game, p=0.5, ts=T0, n=4, stat="rec_yds"):
    return {"ticker": ticker, "game_key": f"nfl:2026-10-25:{game}", "stat": stat, "strike": 49.5, "p_over": p, "n_books": n,
            "disp": 0.01, "ts": datetime.fromtimestamp(ts, timezone.utc).isoformat(), "consensus_src": "exact",
            "player_norm": ticker.lower()}


def snap(ts, *rows_):
    return ts, [leg_from_row(r, ts) for r in rows_]


def combo(tickers, created, y=0.2, result="no", sides=None, vol=10.0):
    return {"ticker": "KXMVE-" + "-".join(t[-3:] for t in tickers), "close_time": "2026-10-25T23:00:00Z", "created_time": created,
            "volume_fp": str(vol), "last_price_dollars": str(y), "result": result,
            "legs": [[t, (sides or ["yes"] * len(tickers))[i]] for i, t in enumerate(tickers)]}


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def test_book_at_never_uses_a_snapshot_from_the_future_or_a_stale_one():
    s = rp.SnapshotStore([snap(T0, row("A1", "X@Y")), snap(T0 + 1800, row("A1", "X@Y", p=0.9, ts=T0 + 1800))])
    assert s.book_at(T0 - 1) is None                                              # before any snapshot
    assert s.book_at(T0 + 1799).get("A1").p_cons_yes == 0.5                       # the 30-min snapshot is still the future
    assert s.book_at(T0 + 1800).get("A1").p_cons_yes == 0.9
    assert s.book_at(T0 + 1800 + rp.MAX_SNAPSHOT_AGE_SEC + 1) is None            # too old to trust


def test_replay_prices_with_the_snapshot_before_creation_and_grades_the_lay():
    rows_ = [row("A1", "G1@H1"), row("B2", "G2@H2"), row("C3", "G3@H3")]
    s = rp.SnapshotStore([snap(T0, *rows_)])
    rec = rp.replay_one(combo(["A1", "B2", "C3"], iso(T0 + 120), y=0.20, result="no"), s)
    assert rec["n_priced"] == 3 and rec["n_unpriced"] == 0 and not rec["same_game"]
    assert rec["p_true"] == pytest.approx(0.125)                                  # three independent 0.5 legs
    assert rec["edge_ex_ante"] == pytest.approx(0.20 - 0.125)
    assert rec["lay_pnl_at_market"] == pytest.approx(0.20 - rp.fee(0.80))         # NO won: collect y, minus the fee on the NO price
    assert rec["quoted"] and rec["no_bid"] is not None
    assert rec["filled"] == (rec["no_bid"] >= 0.80 - 1e-9)


def test_unpriced_legs_make_p_an_upper_bound_and_are_counted():
    s = rp.SnapshotStore([snap(T0, row("A1", "G1@H1"), row("B2", "G2@H2"))])
    rec = rp.replay_one(combo(["A1", "B2", "ZZ9"], iso(T0 + 60)), s)
    assert rec["n_priced"] == 2 and rec["n_unpriced"] == 1
    assert rec["p_true"] == pytest.approx(0.25)                                   # the unknown leg contributes a factor of 1


def test_no_snapshot_before_the_combo_means_no_record():
    s = rp.SnapshotStore([snap(T0 + 600, row("A1", "G1@H1"))])
    assert rp.replay_one(combo(["A1", "B2"], iso(T0)), s) is None


def test_grade_arithmetic_for_a_yes_result_and_a_fill():
    r = rp.grade({"yes_price": 0.30, "yes_won": True, "p_true": 0.10, "quoted": True, "no_bid": 0.75})
    assert r["lay_pnl_at_market"] == pytest.approx(0.30 - 1.0 - rp.fee(0.70))
    assert r["filled"] is True                                                    # 0.75 >= 1 - 0.30 = 0.70
    assert r["pnl_at_our_bid"] == pytest.approx(0.0 - 0.75 - rp.fee(0.75))        # YES won: we lose the whole NO price
    no = rp.grade({"yes_price": 0.30, "yes_won": False, "p_true": None, "quoted": True, "no_bid": 0.65})
    assert no["filled"] is False and no["pnl_at_our_bid"] is None and no["edge_ex_ante"] is None


def test_from_cycle_frames_stamps_each_file_with_its_own_ts():
    df1 = pd.DataFrame([row("A1", "G1@H1", ts=T0)])
    df2 = pd.DataFrame([row("A1", "G1@H1", p=0.7, ts=T0 + 1800)])
    s = rp.SnapshotStore.from_cycle_frames([df2, df1, None])
    assert len(s) == 2 and s.tickers == {"A1"}
    assert s.book_at(T0 + 10).get("A1").p_cons_yes == 0.5


def test_load_combos_keeps_only_traded_yes_no_results(tmp_path):
    p = tmp_path / "c.jsonl"
    lines = [combo(["A1", "B2"], iso(T0)), dict(combo(["A1", "B2"], iso(T0)), volume_fp="0"),
             dict(combo(["A1", "B2"], iso(T0)), result="scalar"), {"_window": "w", "scanned": 5, "truncated": True}]
    p.write_text("\n".join(json.dumps(x) for x in lines))
    combos, windows = rp.load_combos(str(p))
    assert len(combos) == 1 and windows == [{"_window": "w", "scanned": 5, "truncated": True}]


def test_pull_filters_non_sports_and_reports_truncation():
    sports = {"ticker": "M1", "mve_selected_legs": [{"market_ticker": "KXNFLREC-X", "side": "yes"}], "volume_fp": "5",
              "close_time": "c", "created_time": "t", "last_price_dollars": "0.1", "result": "no"}
    crypto = dict(sports, ticker="M2", mve_selected_legs=[{"market_ticker": "KXEURUSD15M-X", "side": "no"}])
    mixed = dict(sports, ticker="M3", mve_selected_legs=[{"market_ticker": "KXNFLREC-X", "side": "yes"},
                                                         {"market_ticker": "KXEURUSD15M-X", "side": "no"}])
    pages = iter([{"markets": [sports, crypto, mixed], "cursor": "next"}])
    out = list(rp.pull_combos(datetime(2026, 10, 25, tzinfo=timezone.utc), datetime(2026, 10, 25, 4, tzinfo=timezone.utc),
                              max_pages=1, get=lambda params: next(pages), sleep=lambda s: None))
    assert [o["ticker"] for o in out if "ticker" in o] == ["M1"]                  # crypto-only and mixed-sport combos are dropped
    assert out[-1] == {"_window": "2026-10-25T00:00:00+00:00", "scanned": 3, "truncated": True}


def test_summarize_groups_fully_priced_vs_bounded():
    base = dict(day="2026-10-25", yes_price=0.2, yes_won=False, quoted=False, quote_reason="REJ_X", filled=False,
                pnl_at_our_bid=None, lay_pnl_at_market=0.2)
    recs = [dict(base, n_unpriced=0, p_true=0.1, edge_ex_ante=0.1), dict(base, n_unpriced=2, p_true=0.15, edge_ex_ante=0.05),
            dict(base, n_unpriced=3, p_true=None, edge_ex_ante=None)]
    s = rp.summarize(recs)
    assert (s["combos"], s["priced_any_leg"], s["fully_priced"], s["bounded"]) == (3, 2, 1, 1)
    assert s["fully_priced_stats"]["edge_ex_ante"]["mean"] == 0.1 and s["by_day"]["2026-10-25"]["combos"] == 3


# ------------------------------------------------------------------------------------------ rung curve + protected twins
def cyc(rows_, ts=T0):
    cols = [dict(r, exact_n_books=r["n_books"], team=r.get("team", "BUF"), player=r["player_norm"]) for r in rows_]
    return pd.DataFrame(cols)


def prop_row(series, strike, p, n=4, player="jim smith", ts=T0):
    n_plus = int(strike + 0.5)
    t = f"{series}-26OCT25BUFKC-BUFJSMITH12-{n_plus}"
    return {"ticker": t, "game_key": "nfl:2026-10-25:BUF@KC", "stat": "rec_yds", "strike": strike, "p_over": p, "n_books": n,
            "disp": 0.01, "ts": datetime.fromtimestamp(ts, timezone.utc).isoformat(), "consensus_src": "exact",
            "player_norm": player}


def test_other_rung_is_interpolated_from_the_snapshots_own_curve():
    frame = cyc([prop_row("KXNFLRECYDS", 49.5, 0.60), prop_row("KXNFLRECYDS", 69.5, 0.30)])
    s = rp.SnapshotStore.from_cycle_frames([frame])
    book = s.book_at(T0 + 60)
    banked = book.get("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50")
    assert banked is not None and banked.p_cons_yes == pytest.approx(0.60) and not banked.pooled
    mid = book.get("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-60")                     # 59.5 was never swept
    assert mid is not None and mid.pooled and 0.30 < mid.p_cons_yes < 0.60


def test_protected_twin_resolves_to_the_base_series_fair_value():
    from ev.kalshi_props import parse_prop_ticker, ParsedPropTicker
    p = parse_prop_ticker("KXNFLRECYDSPROT-26OCT25BUFKC-BUFJSMITH12-50")
    assert isinstance(p, ParsedPropTicker) and p.stat == "rec_yds" and p.strike == 49.5
    s = rp.SnapshotStore.from_cycle_frames([cyc([prop_row("KXNFLRECYDS", 49.5, 0.60), prop_row("KXNFLRECYDS", 69.5, 0.30)])])
    leg = s.book_at(T0 + 60).get("KXNFLRECYDSPROT-26OCT25BUFKC-BUFJSMITH12-50")
    assert leg is not None and leg.p_cons_yes == pytest.approx(0.60)
    assert s.may_price([["KXNFLRECYDSPROT-26OCT25BUFKC-BUFJSMITH12-60", "yes"]]) and not s.may_price([["KXNBAPTS-X-Y-1", "yes"]])


def test_curve_never_extrapolates_far_past_its_anchors():
    s = rp.SnapshotStore.from_cycle_frames([cyc([prop_row("KXNFLRECYDS", 49.5, 0.60), prop_row("KXNFLRECYDS", 69.5, 0.30)])])
    assert s.book_at(T0 + 60).get("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-200") is None


# ------------------------------------------------------------------------------------------ started games, max age, audit
def test_a_leg_whose_game_kicked_off_is_not_priced_from_a_pregame_snapshot():
    r = prop_row("KXNFLRECYDS", 49.5, 0.60)
    frame = cyc([r]); frame["commence"] = datetime.fromtimestamp(T0 + 900, timezone.utc).isoformat()
    s = rp.SnapshotStore.from_cycle_frames([frame])
    tk = "KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50"
    assert s.book_at(T0 + 600).get(tk) is not None                               # 5 min before kickoff: fine
    assert s.book_at(T0 + 1000).get(tk) is None                                  # after kickoff: unpriceable, not stale-priced
    assert s.started_games(T0 + 1000, 0) == frozenset({"nfl:2026-10-25:BUF@KC"})


def test_max_age_is_a_parameter():
    s = rp.SnapshotStore([snap(T0, row("A1", "X@Y"))])
    assert s.book_at(T0 + 3000) is None and s.book_at(T0 + 3000, 7200) is not None


def test_audit_labels_every_outcome():
    frame = cyc([prop_row("KXNFLRECYDS", 49.5, 0.60), prop_row("KXNFLRECYDS", 69.5, 0.30)])
    frame["commence"] = datetime.fromtimestamp(T0 + 5000, timezone.utc).isoformat()
    s = rp.SnapshotStore.from_cycle_frames([frame])
    mk = lambda tk, created: {"created_time": iso(created), "legs": [[tk, "yes"]]}
    combos = [mk("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50", T0 + 60), mk("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-60", T0 + 60),
              mk("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50", T0 + 9000), mk("KXNFLSPREAD-26OCT25BUFKC-KC3", T0 + 60),
              mk("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50", T0 - 5000)]
    a = rp.audit_legs(combos, s)
    assert a["KXNFLRECYDS"]["HIT_exact"] == 1 and a["KXNFLRECYDS"]["HIT_curve"] == 1
    assert a["KXNFLRECYDS"]["NO_SNAPSHOT"] == 2                                  # one too old (age gate), one before the first snapshot
    assert a["KXNFLSPREAD"] == {"MISS_no_game_line": 1}                          # a covered league, but no game lines are banked


# ------------------------------------------------------------------------------------------ raw all-book snapshots, composite, ladder
def nba_event(start_ts, away="Toronto Raptors", home="Minnesota Timberwolves", points=(24.5, 29.5), books=("fanduel", "draftkings", "caesars")):
    def mk(book, pt, over, under):
        return {"key": book, "title": book, "markets": [{"key": "player_points", "outcomes": [
            {"name": "Over", "description": "Anthony Edwards", "price": over, "point": pt},
            {"name": "Under", "description": "Anthony Edwards", "price": under, "point": pt}]}]}
    bms = []
    for b in books:
        for pt, (o, u) in zip(points, ((-130, 110), (+150, -180))):
            bms.append(mk(b, pt, o, u))
    merged = {}
    for bm in bms:
        d = merged.setdefault(bm["key"], {"key": bm["key"], "title": bm["title"], "markets": [{"key": "player_points", "outcomes": []}]})
        d["markets"][0]["outcomes"] += bm["markets"][0]["outcomes"]
    return {"id": "e1", "sport_key": "basketball_nba", "commence_time": datetime.fromtimestamp(start_ts, timezone.utc).isoformat(),
            "home_team": home, "away_team": away, "bookmakers": list(merged.values())}


def test_raw_snapshot_store_prices_alt_rungs_and_extrapolates_with_the_ladder():
    ev = nba_event(T0 + 7200)
    store = rp.store_from_raw("nba", [(T0, [ev])])
    book = store.book_at(T0 + 300)
    tk = lambda n: f"KXNBAPTS-26OCT25TORMIN-MINAEDWARDS5-{n}"
    assert book.get(tk(25)).p_cons_yes > book.get(tk(30)).p_cons_yes > 0.0                     # both quoted lines resolve (exact)
    mid = book.get(tk(27))
    assert mid is not None and mid.pooled                                                       # interpolated between the two alternates
    far = book.get(tk(34))
    assert far is not None and far.pooled and far.p_cons_yes < book.get(tk(30)).p_cons_yes     # extrapolated past the last alternate by the ladder
    assert far.disp > mid.disp                                                                  # and it is flagged less certain
    assert book.get(tk(80)) is None                                                             # but not absurdly far


def test_raw_snapshot_store_drops_a_game_that_has_started_by_the_combo_time():
    store = rp.store_from_raw("nba", [(T0, [nba_event(T0 + 600)])])
    tk = "KXNBAPTS-26OCT25TORMIN-MINAEDWARDS5-25"
    assert store.book_at(T0 + 300).get(tk) is not None
    assert store.book_at(T0 + 900).get(tk) is None                                              # tip-off passed: never a pre-game fair value


def test_composite_store_merges_sports_and_prefers_the_freshest_index_per_sport():
    cyc_store = rp.SnapshotStore.from_cycle_frames([cyc([prop_row("KXNFLRECYDS", 49.5, 0.60), prop_row("KXNFLRECYDS", 69.5, 0.30)])])
    nba = rp.store_from_raw("nba", [(T0, [nba_event(T0 + 7200)])])
    comp = rp.CompositeStore([cyc_store, nba])
    b = comp.book_at(T0 + 120)
    assert b.get("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50") is not None and b.get("KXNBAPTS-26OCT25TORMIN-MINAEDWARDS5-25") is not None
    assert comp.has_snapshot(T0 + 120) and not comp.has_snapshot(T0 + 120 + 99999)
    assert comp.may_price([["KXNBAPTSPROT-26OCT25TORMIN-MINAEDWARDS5-27", "yes"]])


def test_resolve_why_reports_ladder_for_a_rung_past_the_curve():
    store = rp.store_from_raw("nba", [(T0, [nba_event(T0 + 7200)])])
    book = store.book_at(T0 + 10)
    why = book._resolve_prop_why("KXNBAPTS-26OCT25TORMIN-MINAEDWARDS5-34")[1]
    assert why == "ladder"


def test_hyphenated_first_names_and_multiword_surnames_resolve():
    from ev.kalshi_props import kalshi_player_codes
    assert "astbrown" in kalshi_player_codes("amon ra st brown", "Amon-Ra St. Brown")              # Kalshi: initial + 'St Brown'
    assert "jsmithnjigba" in kalshi_player_codes("jaxon smith njigba", "Jaxon Smith-Njigba")
    assert "kwalker" in kalshi_player_codes("kenneth walker", "Kenneth Walker III")                 # unchanged
    assert "mharrisonjr" in kalshi_player_codes("marvin harrison", "Marvin Harrison Jr.")


# ------------------------------------------------------------------------------------------ one-sided quotes (anytime TD, alternate ladders)
def nfl_td_event(start_ts, books_td, books_pairs=None):
    """books_td: {book: yes price} (one-sided Over 0.5); books_pairs: {book: (over, under)} two-sided rec_yds at 49.5 used to learn the hold."""
    bms = {}
    for b, price in books_td.items():
        bms.setdefault(b, {"key": b, "title": b, "markets": []})["markets"].append(
            {"key": "player_anytime_td", "outcomes": [{"name": "Over", "description": "Jim Smith (BUF)", "price": price, "point": 0.5}]})
    for b, (o, u) in (books_pairs or {}).items():
        bms.setdefault(b, {"key": b, "title": b, "markets": []})["markets"].append(
            {"key": "player_reception_yds", "outcomes": [{"name": "Over", "description": "Jim Smith (BUF)", "price": o, "point": 49.5},
                                                         {"name": "Under", "description": "Jim Smith (BUF)", "price": u, "point": 49.5}]})
    return {"id": "n1", "sport_key": "americanfootball_nfl", "commence_time": datetime.fromtimestamp(start_ts, timezone.utc).isoformat(),
            "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills", "bookmakers": list(bms.values())}


def test_one_sided_anytime_td_is_priced_with_a_prior_hold_and_flagged_less_certain():
    ev = nfl_td_event(T0 + 7200, {b: +150 for b in ("fanduel", "draftkings", "caesars", "betmgm")})          # implied 0.40 each
    idx = rp.store_from_raw("nfl", [(T0, [ev])]).book_at(T0 + 60)
    leg = idx.get("KXNFLTD-26OCT25BUFKC-BUFJSMITH12-1")
    assert leg is not None and leg.n_books == 4
    assert leg.p_cons_yes == pytest.approx(0.40 * 0.85, abs=0.005)                                          # scorer prior factor 0.85
    assert leg.disp >= 0.02 - 1e-9                                                                          # one-sided padding on dispersion


def test_one_sided_factor_is_learned_from_the_same_books_two_sided_pairs():
    pairs = {b: (-110, -110) for b in ("fanduel", "draftkings", "caesars", "betmgm")}                        # fair 0.5 vs implied 0.5238
    # 4 pairs on rec_yds: factor for rec_yds ~ 0.5/0.5238; TD (a different stat) still uses the TD prior
    from ev.kalshi_rfq_props import _factor_for, _one_sided_factors
    from ev.props import parse_event_props
    q, _ = parse_event_props(nfl_td_event(T0 + 7200, {"fanduel": +150}, pairs), "nfl")
    f = _one_sided_factors(q)
    assert f["stat"]["rec_yds"] == pytest.approx(0.5 / (110 / 210), abs=0.002)
    assert _factor_for(f, "fanduel", "rec_yds") == pytest.approx(0.955, abs=0.003)
    assert _factor_for(f, "fanduel", "anytime_td") == 0.85                                                  # no TD pairs: the prior
    assert _factor_for(f, "fanduel", "pass_yds") == 0.93                                                    # unknown stat: the default


def test_an_under_only_quote_is_ignored():
    ev = nfl_td_event(T0 + 7200, {})
    ev["bookmakers"] = [{"key": "fanduel", "title": "fanduel", "markets": [{"key": "player_reception_yds", "outcomes": [
        {"name": "Under", "description": "Jim Smith (BUF)", "price": -120, "point": 49.5}]}]}]
    idx = rp.store_from_raw("nfl", [(T0, [ev])]).book_at(T0 + 60)
    assert idx.get("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50") is None


def test_audit_calls_a_missing_sport_snapshot_no_snapshot_not_unknown_series():
    nba = rp.store_from_raw("nba", [(T0, [nba_event(T0 + 7200)])])
    a = rp.audit_legs([{"created_time": iso(T0 + 60), "legs": [["KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50", "yes"],
                                                                  ["KXNBAPTS-26OCT25TORMIN-MINAEDWARDS5-25", "yes"]]}], rp.CompositeStore([nba]))
    assert a["KXNFLRECYDS"] == {"NO_SNAPSHOT": 1} and a["KXNBAPTS"] == {"HIT_exact": 1}


def test_leg_records_export_priced_legs_beside_price_and_result():
    s = rp.SnapshotStore([snap(T0, row("A1", "G1@H1", p=0.4), row("B2", "G1@H1", p=0.6))])
    rec = rp.leg_records(combo(["A1", "B2", "ZZ9"], iso(T0 + 60), y=0.30, result="yes"), s)
    assert [l["ticker"] for l in rec["legs"]] == ["A1", "B2"] and rec["n_legs"] == 3                 # the unpriced leg is dropped, not guessed
    assert rec["yes_price"] == 0.30 and rec["yes_won"] is True
    assert {l["game_key"] for l in rec["legs"]} == {"nfl:2026-10-25:G1@H1"} and rec["legs"][0]["p_yes"] == pytest.approx(0.4)
    assert rp.leg_records(combo(["ZZ9"], iso(T0 + 60)), s) is None


# ------------------------------------------------------------------------------------------ banked game lines (NBA / NHL)
def game_line_event(start_ts, home="Minnesota Timberwolves", away="Toronto Raptors"):
    def bm(key, ml, spread, total):
        return {"key": key, "markets": [
            {"key": "h2h", "outcomes": [{"name": home, "price": ml[0]}, {"name": away, "price": ml[1]}]},
            {"key": "spreads", "outcomes": [{"name": home, "point": -4.5, "price": spread[0]}, {"name": away, "point": 4.5, "price": spread[1]}]},
            {"key": "totals", "outcomes": [{"name": "Over", "point": 220.5, "price": total[0]}, {"name": "Under", "point": 220.5, "price": total[1]}]}]}
    return {"id": "g1", "commence_time": datetime.fromtimestamp(start_ts, timezone.utc).isoformat(), "home_team": home, "away_team": away,
            "bookmakers": [bm("fanduel", (-190, 160), (-110, -110), (-110, -110)), bm("draftkings", (-185, 155), (-112, -108), (-108, -112))]}


def test_game_line_store_prices_game_spread_and_total_legs_at_the_snapshot_time():
    store = rp.store_from_game_lines("nba", [(T0, [game_line_event(T0 + 7200)])])
    book = store.book_at(T0 + 300)
    ml = book.get("KXNBAGAME-26OCT25TORMIN-MIN")
    assert ml is not None and 0.6 < ml.p_cons_yes < 0.7 and ml.n_books == 2
    assert book.get("KXNBAGAME-26OCT25TORMIN-TOR").p_cons_yes == pytest.approx(1.0 - ml.p_cons_yes)
    assert book.get("KXNBASPREAD-26OCT25TORMIN-MIN5") is not None                    # MIN wins by over 4.5
    assert book.get("KXNBATOTAL-26OCT25TORMIN-221").p_cons_yes == pytest.approx(0.5, abs=0.01)   # Over 220.5


def test_game_line_store_has_no_look_ahead_and_drops_a_started_game():
    store = rp.store_from_game_lines("nba", [(T0, [game_line_event(T0 + 600)]), (T0 + 1800, [game_line_event(T0 + 600)])])
    t = "KXNBAGAME-26OCT25TORMIN-MIN"
    assert store.book_at(T0 - 1) is None                                            # nothing was banked yet
    assert store.book_at(T0 + 300).get(t) is not None
    assert store.book_at(T0 + 900).get(t) is None                                   # tip-off passed: never a pre-game fair value
    assert store.book_at(T0 + 2000).get(t) is None                                  # ... including from the later snapshot


def test_game_line_and_prop_stores_compose_for_one_game():
    comp = rp.CompositeStore([rp.store_from_raw("nba", [(T0, [nba_event(T0 + 7200)])]),
                              rp.store_from_game_lines("nba", [(T0, [game_line_event(T0 + 7200)])])])
    b = comp.book_at(T0 + 300)
    assert b.get("KXNBAPTS-26OCT25TORMIN-MINAEDWARDS5-25") is not None and b.get("KXNBAGAME-26OCT25TORMIN-MIN") is not None
    assert comp.may_price([["KXNBAGAME-26OCT25TORMIN-MIN", "yes"]])
