"""tests/test_kalshi_rfq_taker_window2_wiring.py -- PR-A: Window 2 engine wired
into the RFQ taker replay harness (stacked on PR #345 + PR #346).

Covers:
1. Legacy shape preserved when Window 2 is not enabled (no window2_* keys,
   summary["window2"] is None, Ledger W2 is None).
2. With Window 2 enabled, merged decisions match a direct
   Window2Admission.run_day call on the same fires.
3. Window 1 (frozen FIFO guard) admission is unchanged by enabling Window 2.
4. Dual-Ledger grading: Ledger A (cap-free) grades every fire; Ledger B grades
   only the Window 1 admitted subset; Ledger W2 grades the Window 2 subset.
5. Evaluable-denominator exclusion flag: KXNBA1Q / KXNCAAFGAME legs are flagged
   on the record and counted in the summary, never silently dropped.
6. CLI: --window2 flag exists on both subcommands.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

from ev.combo_taker_scanner import evaluate_combo
from ev.combo_taker_window2 import Window2Admission, Window2Fire
from ev.kalshi_rfq_pricer import InMemoryFairBook, LegFairValue
from ev.kalshi_rfq_taker_replay import candidate_from_legs, run_candidates

T0 = 1_800_000_000.0
DAY = "2026-10-25"


def _leg(ticker: str, game_key: str, stat: str = "total", strike: float = 45.5,
         p: float = 0.50, player: str | None = None, sport: str = "nfl") -> LegFairValue:
    return LegFairValue(
        ticker=ticker,
        sport=sport,
        game_key=game_key,
        market_class="player_prop" if player else "game_total",
        player_norm=player,
        stat=stat,
        strike=strike,
        p_cons_yes=p,
        n_books=4,
        disp=0.01,
        p_model_yes=p,
        p_true_yes=p,
        pooled=False,
        updated_ts=T0,
    )


def _cand(book, combo_id: str, leg_pairs, t: float = T0, bid_yes: float = 0.45):
    cand, status = candidate_from_legs(
        combo_id=combo_id,
        venue="kalshi",
        legs_with_sides=leg_pairs,
        book=book,
        t=t,
        bid_yes=bid_yes,
        game_start_lookup=lambda gk: T0 + 3600.0,
    )
    assert status == "OK" and cand is not None
    return cand


def _two_leg_book():
    book = InMemoryFairBook(clock=lambda: T0)
    book.replace_all([
        _leg("T1", "nfl:2026-10-25:BUF@MIA"),
        _leg("T2", "nfl:2026-10-25:KC@DEN"),
    ])
    return book


def test_no_window2_legacy_shape():
    book = _two_leg_book()
    cand = _cand(book, "c1", [("T1", "yes"), ("T2", "yes")])
    records, summary = run_candidates([(cand, {"day": DAY, "yes_won": False})])
    rec = records[0]
    assert rec["fires"] is True
    assert not any(k.startswith("window2_") for k in rec)
    assert summary["window2"] is None
    assert summary["ledgers"]["W2_window2_shadow"] is None
    # The single fire is graded cap-free in Ledger A and (guard admits) in B.
    assert summary["ledgers"]["A_cap_free"]["n"] == 1
    assert summary["ledgers"]["B_window1_fifo"]["n"] == 1
    assert rec["denominator_excluded"] is False
    assert summary["denominator"]["n_records_excluded"] == 0


def test_window2_merge_matches_direct_engine():
    book = _two_leg_book()
    cands = [
        _cand(book, "c1", [("T1", "yes"), ("T2", "yes")], t=T0),
        _cand(book, "c2", [("T1", "yes"), ("T2", "yes")], t=T0 + 10.0),
    ]
    items = [(c, {"day": DAY, "yes_won": False}) for c in cands]
    records, summary = run_candidates(items, window2_admission=Window2Admission())

    direct = Window2Admission().run_day(
        [Window2Fire(candidate=c, signal=evaluate_combo(c), stress=(), day=DAY) for c in cands],
        DAY,
    )
    direct_map = {d.combo_id: d for d in direct}
    for rec in records:
        assert rec["window2_admitted"] == direct_map[rec["combo_id"]].admitted
        assert rec["window2_tier"] == direct_map[rec["combo_id"]].tier
        assert isinstance(rec["window2_rotten_leg_v2"], bool)
        assert isinstance(rec["window2_refusals"], list)
    assert summary["window2"]["n_fires_considered"] == 2
    assert summary["window2"]["n_admitted"] == sum(1 for r in records if r["window2_admitted"])


def test_window1_admission_unchanged_by_window2():
    book = _two_leg_book()
    cands = [_cand(book, f"c{i}", [("T1", "yes"), ("T2", "yes")], t=T0 + i * 10.0) for i in range(3)]
    items = [(c, {"day": DAY, "yes_won": True}) for c in cands]
    recs_plain, _ = run_candidates(items)
    recs_w2, _ = run_candidates(items, window2_admission=Window2Admission())
    assert [r["admitted"] for r in recs_plain] == [r["admitted"] for r in recs_w2]
    assert [r["fires"] for r in recs_plain] == [r["fires"] for r in recs_w2]


def test_dual_ledgers_grade_their_own_subsets():
    # Six fires sharing one player: Window 1's $75 player cap admits exactly 5
    # (the harness's own Blade-4 fixture shape). Ledger A must grade all 6.
    book = InMemoryFairBook(clock=lambda: T0)
    legs = []
    for i in range(6):
        legs.append(_leg(f"P{i}", f"g{i}a", stat="rush_yds", strike=50.5, player="star_player"))
        legs.append(_leg(f"Q{i}", f"g{i}b", stat="total", strike=45.5))
    book.replace_all(legs)
    items = []
    for i in range(6):
        cand = _cand(book, f"combo_{i}", [(f"P{i}", "yes"), (f"Q{i}", "yes")], t=T0 + i * 10.0)
        items.append((cand, {"day": DAY, "yes_won": False}))
    records, summary = run_candidates(items, window2_admission=Window2Admission())
    led = summary["ledgers"]
    assert led["A_cap_free"]["n"] == 6
    assert led["B_window1_fifo"]["n"] == 5
    w2_graded = [r for r in records if r["window2_admitted"] and r["pnl_per_contract"] is not None]
    assert led["W2_window2_shadow"]["n"] == len(w2_graded)
    assert 1 <= led["W2_window2_shadow"]["n"] <= 6
    # All three ledgers grade the identical pnl field (no re-derived numbers).
    assert led["A_cap_free"]["mean_pnl_per_contract"] == pytest.approx(
        records[0]["pnl_per_contract"], abs=1e-4)  # ledger means round to 4dp


def test_denominator_exclusion_flag():
    book = InMemoryFairBook(clock=lambda: T0)
    book.replace_all([
        _leg("KXNBA1QPTS-26OCT25LALDEN-JJAMES-10", "nba:2026-10-25:LAL@DEN",
             stat="points", strike=10.5, player="j_james", sport="nba"),
        _leg("KXNBA1QPTS-26OCT25LALDEN-ADAVIS-8", "nba:2026-10-25:LAL@DEN",
             stat="points", strike=8.5, player="a_davis", sport="nba"),
        _leg("KXNCAAFGAME-26OCT25OSUMICH-OSU", "ncaaf:2026-10-25:OSU@MICH",
             stat="spread", strike=-3.5, sport="ncaaf"),
        _leg("T9", "nfl:2026-10-25:KC@DEN"),
    ])
    c_q = _cand(book, "cq", [("KXNBA1QPTS-26OCT25LALDEN-JJAMES-10", "yes"),
                             ("KXNBA1QPTS-26OCT25LALDEN-ADAVIS-8", "yes")])
    c_f = _cand(book, "cf", [("KXNCAAFGAME-26OCT25OSUMICH-OSU", "yes"), ("T9", "yes")])
    c_n = _cand(book, "cn", [("T9", "yes"), ("T9", "yes")])
    records, summary = run_candidates([
        (c_q, {"day": DAY}), (c_f, {"day": DAY}), (c_n, {"day": DAY}),
    ])
    by_id = {r["combo_id"]: r for r in records}
    assert by_id["cq"]["denominator_excluded"] is True
    assert by_id["cq"]["denominator_exclusion"] == "KXNBA1Q"
    assert by_id["cf"]["denominator_excluded"] is True
    assert by_id["cf"]["denominator_exclusion"] == "KXNCAAFGAME"
    assert by_id["cn"]["denominator_excluded"] is False
    assert summary["denominator"]["n_records_excluded"] == 2
    assert summary["denominator"]["by_series"] == {"KXNBA1Q": 1, "KXNCAAFGAME": 1}


def test_cli_window2_flag_on_both_subcommands(capsys):
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "kalshi_rfq_taker_replay.py")
    spec = importlib.util.spec_from_file_location("rfq_taker_cli", os.path.abspath(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for sub in ("settled", "tape"):
        with pytest.raises(SystemExit) as exc:
            mod.main([sub, "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--window2" in out
