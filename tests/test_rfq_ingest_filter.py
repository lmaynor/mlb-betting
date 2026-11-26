"""Unit and integration tests for the RUL-20261125 Kalshi RFQ ingest filter and shadow pricing."""

from __future__ import annotations

import json

import pytest

from ev.kalshi_rfq_filter import (
    EMPTY_HASH_CREATOR,
    FLAG_BITS,
    AuditSampler,
    FilterConfig,
    FilterMode,
    RFQFilterState,
    classify_rfq_record,
    decide_storage,
    mode_for_ram,
    taper_weight,
)
from ev.kalshi_rfq_shadow_pricing import (
    FamilyFrequencyProvider,
    KalshiLegMidProvider,
    build_family_frequency_table,
    shadow_price_combo,
)
from mlb.execution.kalshi.rfq_maker import RFQTape
from scripts.rfq_filter_shadow_proof import run_shadow_proof
from scripts.rfq_tape_finalize import _compute_summary, finalize_day, main as finalize_main
from tests.test_rfq_tape_v2 import DAY, FakeStorageClient, NOW, combo_rfq, stub_decision


def _rec(
    rfq_id="r1",
    *,
    ts=1000.0,
    creator_id="creator_a",
    combo_id="combo_1",
    tickers=("KXNFL-1", "KXNFL-2"),
    preview_10=False,
    excluded_series_class="none",
    is_fire=False,
    target_cost_dollars_num=25.0,
    n_unpriced=0,
    legs=None,
    legs_status=None,
):
    raw_legs = [{"market_ticker": t, "side": "yes"} for t in tickers]
    if legs is None:
        legs = [
            {
                "ticker": t,
                "series": t.split("-", 1)[0],
                "game_key": f"nfl:2026-11-26:G{i}",
                "age_sec": 100.0,
                "pricing_source": "book",
                "assumed_leg": False,
            }
            for i, t in enumerate(tickers)
        ]
    if legs_status is None:
        legs_status = [{"ticker": t, "status": "OK"} for t in tickers]
    return {
        "kind": "rfq",
        "rfq_id": rfq_id,
        "ts": ts,
        "creator_id": creator_id,
        "combo_id": combo_id,
        "n_legs": len(raw_legs),
        "is_combo": len(raw_legs) >= 2,
        "preview_10": preview_10,
        "excluded_series_class": excluded_series_class,
        "in_denominator": (not preview_10 and excluded_series_class == "none"),
        "is_fire": is_fire,
        "target_cost_dollars_num": target_cost_dollars_num,
        "rfq": {"id": rfq_id, "mve_selected_legs": raw_legs},
        "legs": legs,
        "notes": {"n_unpriced": n_unpriced, "legs_status": legs_status},
    }


def test_waterfall_precedence_excluded_and_preview():
    state = RFQFilterState()
    cfg = FilterConfig()

    both = classify_rfq_record(
        _rec("r1", preview_10=True, excluded_series_class="KXNCAAFGAME"),
        state,
        cfg,
    )
    assert both["primary_class"] == "EX_EXCLUDED_SERIES"
    assert both["flag_bitmask"] & FLAG_BITS["EXCLUDED_SERIES"]
    assert both["flag_bitmask"] & FLAG_BITS["PREVIEW_10"]
    assert both["disposition"] == "EX_EXCLUDED_SERIES"

    prev = classify_rfq_record(
        _rec("r2", preview_10=True, excluded_series_class="none", combo_id="combo_2"),
        state,
        cfg,
    )
    assert prev["primary_class"] == "EX_PREVIEW_10"
    assert prev["disposition"] == "EX_PREVIEW_10"

    denom = classify_rfq_record(
        _rec("r3", preview_10=False, excluded_series_class="none", combo_id="combo_3"),
        state,
        cfg,
    )
    assert denom["primary_class"] == "DENOMINATOR"
    assert denom["disposition"] == "DENOM_NOT_FIRED"


def test_preview_keyed_on_flag_only():
    state = RFQFilterState()
    cfg = FilterConfig()

    r_flag = classify_rfq_record(
        _rec("r1", preview_10=True, target_cost_dollars_num=None),
        state,
        cfg,
    )
    assert r_flag["flag_bitmask"] & FLAG_BITS["PREVIEW_10"]
    assert r_flag["primary_class"] == "EX_PREVIEW_10"

    r_num_only = classify_rfq_record(
        _rec("r2", preview_10=False, target_cost_dollars_num=10.0, combo_id="combo_2"),
        state,
        cfg,
    )
    assert not (r_num_only["flag_bitmask"] & FLAG_BITS["PREVIEW_10"])
    assert r_num_only["primary_class"] == "DENOMINATOR"


def test_empty_hash_exempt_vs_normal_creator_spam_and_dup():
    state = RFQFilterState()
    cfg = FilterConfig()

    # 130 identical combos from EMPTY_HASH_CREATOR across two 10-min windows
    for i in range(65):
        s = classify_rfq_record(
            _rec(f"eh-w1-{i}", ts=float(i), creator_id=EMPTY_HASH_CREATOR, combo_id="same"),
            state,
            cfg,
        )
        assert not (s["flag_bitmask"] & FLAG_BITS["SPAM_CREATOR"])
        assert not (s["flag_bitmask"] & FLAG_BITS["DUP_COMBO"])
        assert s["flag_bitmask"] & FLAG_BITS["EMPTY_HASH_CREATOR"]
        assert s["flag_bitmask"] & FLAG_BITS["BATCH_EXEMPT"]
    for i in range(65):
        s = classify_rfq_record(
            _rec(f"eh-w2-{i}", ts=660.0 + i, creator_id=EMPTY_HASH_CREATOR, combo_id="same"),
            state,
            cfg,
        )
        assert s["primary_class"] == "DENOMINATOR"
        assert not (s["flag_bitmask"] & FLAG_BITS["SPAM_CREATOR"])
        assert not (s["flag_bitmask"] & FLAG_BITS["DUP_COMBO"])

    # Normal creator: 60 in [0, 600), then 60 in [660, 720) -> dup from 2nd, spam on 120th
    for i in range(60):
        s = classify_rfq_record(
            _rec(f"norm-w1-{i}", ts=float(i), creator_id="norm", combo_id="same"),
            state,
            cfg,
        )
        assert s["primary_class"] == "DENOMINATOR"
        assert s["is_dup_creator_combo"] is (i > 0)
        assert not (s["flag_bitmask"] & FLAG_BITS["SPAM_CREATOR"])
    for i in range(59):
        s = classify_rfq_record(
            _rec(f"norm-w2-{i}", ts=660.0 + i, creator_id="norm", combo_id="same"),
            state,
            cfg,
        )
        assert s["is_dup_creator_combo"] is True
        assert s["primary_class"] == "DENOMINATOR"
    s_120 = classify_rfq_record(
        _rec("norm-w2-59", ts=719.0, creator_id="norm", combo_id="same"),
        state,
        cfg,
    )
    assert s_120["is_dup_creator_combo"] is True
    assert s_120["flag_bitmask"] & FLAG_BITS["SPAM_CREATOR"]
    assert s_120["primary_class"] == "EX_SPAM_CREATOR"


def test_bounded_cap_and_open1_strictness():
    state = RFQFilterState()
    cfg = FilterConfig()

    tier_a = classify_rfq_record(_rec("ra", is_fire=True, n_unpriced=0, combo_id="ca"), state, cfg)
    assert tier_a["disposition"] == "ADMITTED_FIRE"
    assert tier_a["fire_tier"] == "A"
    assert tier_a["strict_bound"] is True

    tier_b = classify_rfq_record(_rec("rb", is_fire=True, n_unpriced=3, combo_id="cb"), state, cfg)
    assert tier_b["disposition"] == "ADMITTED_FIRE"
    assert tier_b["fire_tier"] == "B"
    assert tier_b["strict_bound"] is True

    over_cap = classify_rfq_record(_rec("rc", is_fire=True, n_unpriced=4, combo_id="cc"), state, cfg)
    assert over_cap["disposition"] == "FIRE_BOUNDED_OVER_CAP"
    assert over_cap["fire_tier"] is None

    assumed_legs = [
        {
            "ticker": "KXNFL-1",
            "series": "KXNFL",
            "game_key": "nfl:2026-11-26:G1",
            "age_sec": 100.0,
            "pricing_source": "book",
            "assumed_leg": True,
        }
    ]
    nonstrict = classify_rfq_record(
        _rec("rd", is_fire=True, n_unpriced=2, legs=assumed_legs, combo_id="cd"),
        state,
        cfg,
    )
    assert nonstrict["strict_bound"] is False
    assert nonstrict["disposition"] == "FIRE_BOUNDED_NONSTRICT"
    assert nonstrict["fire_tier"] is None


@pytest.mark.parametrize(
    "age,expected_weight",
    [
        (899, 1.0),
        (900, 0.5),
        (1799, 0.5),
        (1800, 0.25),
        (2699, 0.25),
        (2700, 0.0),
    ],
)
def test_taper_boundaries(age, expected_weight):
    assert taper_weight(age) == expected_weight


def test_stale_zero_and_cap_dominance():
    state = RFQFilterState()
    cfg = FilterConfig()
    stale_legs = [
        {
            "ticker": "KXNFL-1",
            "series": "KXNFL",
            "game_key": "nfl:2026-11-26:G1",
            "age_sec": 2700.0,
            "pricing_source": "book",
            "assumed_leg": False,
        }
    ]
    stale = classify_rfq_record(
        _rec("rs", is_fire=True, n_unpriced=2, legs=stale_legs, combo_id="cs"),
        state,
        cfg,
    )
    assert stale["disposition"] == "FIRE_STALE_ZERO_SIZE"
    assert stale["taper_weight"] == 0.0

    over = classify_rfq_record(
        _rec("ro", is_fire=True, n_unpriced=4, legs=stale_legs, combo_id="co"),
        state,
        cfg,
    )
    assert over["disposition"] == "FIRE_BOUNDED_OVER_CAP"
    assert over["taper_weight"] == 0.0


def test_non_sports_tagging_pure_and_mixed():
    state = RFQFilterState()
    cfg = FilterConfig()

    pure = classify_rfq_record(
        _rec("r1", tickers=("KXBTC15M-1", "KXETH15M-2"), combo_id="c1"),
        state,
        cfg,
    )
    assert pure["flag_bitmask"] & FLAG_BITS["NON_SPORTS"]
    assert pure["primary_class"] == "DENOMINATOR"

    mixed = classify_rfq_record(
        _rec("r2", tickers=("KXNFL-1", "KXBTCD-2"), combo_id="c2"),
        state,
        cfg,
    )
    assert mixed["flag_bitmask"] & FLAG_BITS["NON_SPORTS"]
    assert mixed["primary_class"] == "DENOMINATOR"


def test_unmapped_all_and_partial():
    state = RFQFilterState()
    cfg = FilterConfig()

    all_unmapped = classify_rfq_record(
        _rec(
            "ru1",
            tickers=("KXNBA-1", "KXNBA-2"),
            legs=[],
            legs_status=[
                {"ticker": "KXNBA-1", "status": "REJ_UNMAPPED_TICKER"},
                {"ticker": "KXNBA-2", "status": "REJ_UNMAPPED_TICKER"},
            ],
            combo_id="cu1",
        ),
        state,
        cfg,
    )
    assert all_unmapped["unmapped_class"] == "all"
    assert all_unmapped["pricer_short_circuit"] is True
    assert all_unmapped["flag_bitmask"] & FLAG_BITS["UNMAPPED_ALL"]
    assert all_unmapped["shadow_eligible"] is True

    partial = classify_rfq_record(
        _rec(
            "ru2",
            tickers=("KXNFL-1", "KXNBA-2"),
            legs_status=[
                {"ticker": "KXNFL-1", "status": "OK"},
                {"ticker": "KXNBA-2", "status": "REJ_UNMAPPED_TICKER"},
            ],
            combo_id="cu2",
        ),
        state,
        cfg,
    )
    assert partial["unmapped_class"] == "partial"
    assert partial["pricer_short_circuit"] is False
    assert partial["flag_bitmask"] & FLAG_BITS["UNMAPPED_ANY"]
    assert not (partial["flag_bitmask"] & FLAG_BITS["UNMAPPED_ALL"])


def test_reconciliation_identity_mixed_stream():
    state = RFQFilterState()
    cfg = FilterConfig()
    stale_legs = [
        {
            "ticker": "KXNFL-1",
            "series": "KXNFL",
            "game_key": "nfl:G1",
            "age_sec": 3000.0,
            "pricing_source": "book",
            "assumed_leg": False,
        }
    ]
    assumed_legs = [
        {
            "ticker": "KXNFL-1",
            "series": "KXNFL",
            "game_key": "nfl:G1",
            "age_sec": 100.0,
            "pricing_source": "book",
            "assumed_leg": True,
        }
    ]

    stream = [
        _rec("m1", preview_10=True, combo_id="c1"),
        _rec("m2", excluded_series_class="KXNBA1Q", combo_id="c2"),
        _rec("m3", is_fire=True, n_unpriced=0, combo_id="c3"),
        _rec("m4", is_fire=True, n_unpriced=0, combo_id="c3"),  # dup admitted fire
        _rec("m5", is_fire=True, n_unpriced=2, combo_id="c5"),
        _rec("m6", is_fire=True, n_unpriced=4, combo_id="c6"),
        _rec("m7", is_fire=True, n_unpriced=1, legs=assumed_legs, combo_id="c7"),
        _rec("m8", is_fire=True, n_unpriced=1, legs=stale_legs, combo_id="c8"),
        _rec("m9", is_fire=False, combo_id="c9"),
        {"kind": "rfq", "rfq_id": ""},  # malformed
    ]
    for r in stream:
        classify_rfq_record(r, state, cfg)

    ledger = state.ledger
    assert ledger.assert_reconciles() is True
    excl_sum = sum(
        cnt for k, cnt in ledger.by_primary_class.items() if k != "DENOMINATOR"
    )
    assert ledger.observed == (
        ledger.admitted_raw
        + ledger.not_fired
        + ledger.over_cap
        + ledger.nonstrict
        + ledger.stale_zero
        + excl_sum
    )
    assert ledger.admitted_raw == 3
    assert ledger.admitted_deduped == 2


def test_sampler_determinism_and_rate():
    sampler = AuditSampler()
    assert sampler.sampled("rfq-abc-123", "PREVIEW_10") == sampler.sampled(
        "rfq-abc-123", "PREVIEW_10"
    )
    hits = sum(1 for i in range(2000) if sampler.sampled(f"id-{i}", "PREVIEW_10"))
    rate = hits / 2000.0
    assert 0.02 <= rate <= 0.09


def test_decide_storage_window1_and_shed_modes():
    sampler = AuditSampler()
    assert mode_for_ram(500.0, armed=False) == FilterMode.WINDOW1_SHADOW
    assert mode_for_ram(400.0, armed=True) == FilterMode.TIER1_SHED
    assert mode_for_ram(420.0, armed=True) == FilterMode.TIER2_SHED

    state = RFQFilterState()
    cfg = FilterConfig()
    prev = classify_rfq_record(_rec("rprev", preview_10=True), state, cfg)
    unmapped = classify_rfq_record(
        _rec(
            "runm",
            tickers=("KXNBA-1", "KXNBA-2"),
            legs=[],
            legs_status=[
                {"ticker": "KXNBA-1", "status": "REJ_UNMAPPED_TICKER"},
                {"ticker": "KXNBA-2", "status": "REJ_UNMAPPED_TICKER"},
            ],
            combo_id="cunm",
        ),
        state,
        cfg,
    )
    denom = classify_rfq_record(_rec("rden", is_fire=True, combo_id="cden"), state, cfg)

    assert decide_storage(prev, FilterMode.WINDOW1_SHADOW, sampler, rfq_id="rprev") == "FULL"
    assert decide_storage(unmapped, FilterMode.WINDOW1_SHADOW, sampler, rfq_id="runm") == "FULL"
    assert decide_storage(denom, FilterMode.WINDOW1_SHADOW, sampler, rfq_id="rden") == "FULL"

    mode_400 = mode_for_ram(400.0, armed=True)
    unsampled_id = next(
        f"u-{i}" for i in range(500) if not sampler.sampled(f"u-{i}", "UNMAPPED_ALL_LEGS")
    )
    sampled_id = next(
        f"s-{i}" for i in range(500) if sampler.sampled(f"s-{i}", "UNMAPPED_ALL_LEGS")
    )
    assert decide_storage(unmapped, mode_400, sampler, rfq_id=unsampled_id) == "COUNTER_ONLY"
    assert decide_storage(unmapped, mode_400, sampler, rfq_id=sampled_id) == "FULL"
    assert decide_storage(denom, mode_400, sampler, rfq_id="rden") == "FULL"
    assert decide_storage(denom, FilterMode.TIER2_SHED, sampler, rfq_id="rden") == "FULL"


def test_shadow_pricing_providers_and_fail_closed():
    mid_provider = KalshiLegMidProvider({"T2": (0.40, 0.50)})
    table = build_family_frequency_table(
        [{"series": "KXNBA", "won": True}, {"series": "KXNBA", "won": False}] * 2
        + [{"series": "KXNHL", "won": False}]
    )
    assert table["KXNBA"] == pytest.approx(0.50)
    assert "KXNFL" not in table

    fam_provider = FamilyFrequencyProvider({"KXMISC": 0.40}, shade=0.10)
    legs = [
        {"ticker": "T1-1", "side": "yes", "series": "KXNFL", "event_ticker": "EV1"},
        {"ticker": "T2", "side": "no", "series": "KXNBA", "event_ticker": "EV2"},
        {"ticker": "KXMISC-1", "side": "yes", "series": "KXMISC", "event_ticker": "EV3"},
    ]
    res = shadow_price_combo(legs, {"T1-1": 0.60}, [mid_provider, fam_provider])
    assert res["status"] == "SHADOW_PRICED"
    assert res["disposition"] == "SHADOW_PRICED"
    assert res["disposition"] != "ADMITTED_FIRE"
    assert res["n_assumed"] == 1
    assert res["correlation_unmodeled"] is False

    by_tk = {l["ticker"]: l for l in res["legs"]}
    assert by_tk["T1-1"]["source"] == "book" and by_tk["T1-1"]["assumed_leg"] is False
    assert by_tk["T2"]["source"] == "kalshi_leg_mid" and by_tk["T2"]["assumed_leg"] is False
    assert by_tk["T2"]["yes_p"] == pytest.approx(0.55)
    assert by_tk["KXMISC-1"]["source"] == "family_default"
    assert by_tk["KXMISC-1"]["assumed_leg"] is True
    assert by_tk["KXMISC-1"]["yes_p"] == pytest.approx(min(0.99, 0.40 * 1.10))
    assert res["p_yes_shadow"] == pytest.approx(0.60 * 0.55 * (0.40 * 1.10))
    assert res["fair_no"] == pytest.approx(1.0 - res["p_yes_shadow"])

    non_sports = shadow_price_combo(
        [{"ticker": "KXBTC15M-1", "side": "yes", "series": "KXBTC15M"}],
        {"KXBTC15M-1": 0.5},
        [mid_provider],
    )
    assert non_sports["status"] == "FAIL_CLOSED_NON_SPORTS"

    unresolved = shadow_price_combo(
        [{"ticker": "MISSING-1", "side": "yes", "series": "KXNBA"}],
        {},
        [mid_provider, fam_provider],
    )
    assert unresolved["status"] == "FAIL_CLOSED_NO_REFERENCE"


def test_rfq_tape_integration_and_finalize_summary(tmp_path):
    tape = RFQTape(str(tmp_path), clock=lambda: NOW)
    msg1 = combo_rfq(["KXNFL-1", "KXNFL-2"], rfq_id="r1")
    msg1["creator_id"] = "c1"
    tape.record_rfq(msg1, stub_decision(action="QUOTE", no_bid=0.80))

    msg2 = combo_rfq(["KXNFL-1", "KXNFL-2"], rfq_id="r2")
    msg2["creator_id"] = "c1"
    tape.record_rfq(msg2, stub_decision(action="QUOTE", no_bid=0.80))

    msg3 = combo_rfq(["KXNBA1Q-X", "KXNFL-2"], rfq_id="r3")
    msg3["target_cost_dollars"] = "10.0000"
    tape.record_rfq(msg3, stub_decision())

    records = [json.loads(line) for line in open(tape.path(), encoding="utf-8")]
    assert len(records) == 3
    for rec in records:
        for field in (
            "primary_class",
            "flag_bitmask",
            "flag_names",
            "disposition",
            "fire_tier",
            "taper_weight",
            "worst_leg_age_sec",
            "is_dup_creator_combo",
            "dup_group_id",
            "unmapped_class",
            "pricer_short_circuit",
            "strict_bound",
            "shadow_eligible",
        ):
            assert field in rec

    assert records[0]["in_denominator"] is True
    assert records[0]["is_fire"] is True
    assert records[0]["disposition"] == "ADMITTED_FIRE"
    assert records[0]["is_dup_creator_combo"] is False
    assert records[1]["is_dup_creator_combo"] is True
    assert records[2]["primary_class"] == "EX_EXCLUDED_SERIES"

    ds = tape.day_summary()
    assert ds["by_primary_class"] == {"DENOMINATOR": 2, "EX_EXCLUDED_SERIES": 1}
    assert ds["by_disposition"] == {"ADMITTED_FIRE": 2, "EX_EXCLUDED_SERIES": 1}
    assert ds["fires_deduped"] == 1

    fin_ds = _compute_summary("2027-01-15", records)
    assert fin_ds["by_primary_class"] == ds["by_primary_class"]
    assert fin_ds["by_disposition"] == ds["by_disposition"]
    assert fin_ds["fires_deduped"] == 1

    # Smoke test the shadow proof script over the taped JSONL
    report, ok = run_shadow_proof(str(tmp_path))
    assert ok is True
    assert "RECONCILIATION: PASS" in report


def test_record_rfq_leg_quotes_universal_banking(tmp_path):
    tape = RFQTape(str(tmp_path), clock=lambda: NOW)
    priced_snap = {
        "ticker": "KXNFL-PRICED",
        "game_key": "nfl:2027-01-15:A@B",
        "player_norm": "p1",
        "side": "yes",
        "p_leg": 0.55,
        "p_true_yes": 0.55,
        "n_books": 5,
        "sport": "nfl",
    }
    notes = {
        "bounded": True,
        "n_priced": 1,
        "n_unpriced": 2,
        "legs_status": [
            {"ticker": "KXNFL-PRICED", "status": "OK"},
            {"ticker": "KXNBA-UNMAPPED", "status": "REJ_UNMAPPED_TICKER"},
            {"ticker": "KXNHL-NOBOOK", "status": "REJ_UNMAPPED_TICKER"},
        ],
    }
    notes_snapshot = json.loads(json.dumps(notes))
    dec = stub_decision(legs=[priced_snap], action="QUOTE", no_bid=0.70, notes=notes)

    msg1 = combo_rfq(["KXNFL-PRICED", "KXNBA-UNMAPPED", "KXNHL-NOBOOK"], rfq_id="lq1")
    tape.record_rfq(
        msg1,
        dec,
        q_by_ticker={
            "KXNFL-PRICED": (NOW - 1.0, 0.40, 0.50),
            "KXNBA-UNMAPPED": {"ts": NOW - 2.0, "bid": 0.30, "ask": 0.36},
        },
    )

    msg2 = combo_rfq(["KXNFL-PRICED", "KXNBA-UNMAPPED"], rfq_id="lq2")
    tape.record_rfq(msg2, dec, q_by_ticker=None)

    recs = [json.loads(line) for line in open(tape.path(), encoding="utf-8")]
    assert len(recs) == 2

    r1, r2 = recs[0], recs[1]
    assert r1["leg_quotes"] == {
        "KXNFL-PRICED": {"ts": NOW - 1.0, "bid": 0.40, "ask": 0.50},
        "KXNBA-UNMAPPED": {"ts": NOW - 2.0, "bid": 0.30, "ask": 0.36},
        "KXNHL-NOBOOK": {"ts": None, "bid": None, "ask": None},
    }
    # Priced leg enrichment unchanged
    assert len(r1["legs"]) == 1
    assert r1["legs"][0]["q_kalshi_bid"] == 0.40
    assert r1["legs"][0]["q_kalshi_ask"] == 0.50
    assert r1["legs"][0]["q_kalshi"] == pytest.approx(0.45)
    assert r1["legs"][0]["q_kalshi_ts"] == NOW - 1.0
    # decision.notes and legs_status not mutated
    assert dec.notes == notes_snapshot

    # q_by_ticker=None -> leg_quotes is None
    assert r2["leg_quotes"] is None
    assert r2["legs"][0]["q_kalshi"] is None


def test_finalize_day_leg_settlements_opt_in_and_no_resolver_byte_identical():
    import io
    import pyarrow.parquet as pq

    prefix = "Kalshi/rfq_tape/production"
    rec_mve = {
        "kind": "rfq",
        "tape_schema": 2,
        "ts": NOW,
        "day_utc": DAY,
        "rfq_id": "r_mve",
        "rfq": {
            "id": "r_mve",
            "mve_selected_legs": [
                {"market_ticker": "KXNFL-YESWIN", "side": "yes"},
                {"market_ticker": "KXNBA-NO_ON_YESWIN", "side": "no"},
                {"market_ticker": "KXNHL-ACTIVE", "side": "yes"},
            ],
        },
        "legs": [{"ticker": "KXNFL-YESWIN", "side": "yes"}],
        "notes": {
            "legs_status": [
                {"ticker": "KXNFL-YESWIN", "status": "OK"},
                {"ticker": "KXNBA-NO_ON_YESWIN", "status": "REJ_UNMAPPED_TICKER"},
                {"ticker": "KXNHL-ACTIVE", "status": "REJ_UNMAPPED_TICKER"},
            ]
        },
    }
    # Fallback record without mve_selected_legs: reads from rec["legs"] and defaults legs_status="PRICED"
    rec_fallback = {
        "kind": "rfq",
        "tape_schema": 2,
        "ts": NOW + 1.0,
        "day_utc": DAY,
        "rfq_id": "r_fb",
        "rfq": {"id": "r_fb"},
        "legs": [
            {"ticker": "KXNFL-YESWIN", "side": "no"},
            {"ticker": "KXMLB-NOLOSE", "side": "no"},
        ],
        "notes": {},
    }
    chunk = "\n".join(json.dumps(r) for r in (rec_mve, rec_fallback)) + "\n"
    raw_key = f"{prefix}/{DAY}/000000_00001.jsonl"
    derived = f"{prefix}_derived"

    # 1. Default (market_resolver=None): no leg_settlements file, skipped_no_resolver, summary untouched
    store_default = {raw_key: chunk.encode()}
    out_default = finalize_day(
        DAY, prefix, bucket="bkt", storage_client=FakeStorageClient(store_default)
    )
    assert out_default["leg_settlements"] == "skipped_no_resolver"
    assert "leg_settlements_settled" not in out_default
    assert f"{derived}/dt={DAY}/leg_settlements.parquet" not in store_default
    summary_default = json.loads(store_default[f"{derived}/dt={DAY}/day_summary.json"])
    assert "leg_settlements_banked" not in summary_default
    assert "leg_settlements_settled" not in summary_default

    # 2. With stub resolver: distinct tickers resolved once, rows and outcomes verified
    calls: list[str] = []
    markets = {
        "KXNFL-YESWIN": {"status": "finalized", "result": "yes"},
        "KXNBA-NO_ON_YESWIN": {"status": "finalized", "result": "yes"},
        "KXNHL-ACTIVE": {"status": "active", "result": ""},
        "KXMLB-NOLOSE": {"status": "finalized", "result": "no"},
    }

    def stub_resolver(ticker: str):
        calls.append(ticker)
        return markets.get(ticker)

    store_settled = {raw_key: chunk.encode()}
    out_settled = finalize_day(
        DAY,
        prefix,
        bucket="bkt",
        storage_client=FakeStorageClient(store_settled),
        market_resolver=stub_resolver,
    )
    assert len(calls) == len(set(calls)) == 4
    assert out_settled["leg_settlements"] == 5
    assert out_settled["leg_settlements_settled"] == 4

    ls_key = f"{derived}/dt={DAY}/leg_settlements.parquet"
    assert ls_key in store_settled
    assert ls_key in out_settled["written"]

    # Non-settlement derived files are byte-identical (except day_summary.json's additive keys)
    for key in (
        f"{derived}/dt={DAY}/combos.parquet",
        f"{derived}/dt={DAY}/legs.parquet",
        f"{derived}/index/rfq_index.parquet",
    ):
        assert store_settled[key] == store_default[key]

    summary_settled = json.loads(store_settled[f"{derived}/dt={DAY}/day_summary.json"])
    assert summary_settled["leg_settlements_banked"] == 5
    assert summary_settled["leg_settlements_settled"] == 4

    rows = pq.read_table(io.BytesIO(store_settled[ls_key])).to_pylist()
    assert len(rows) == 5
    by_key = {(r["rfq_id"], r["ticker"]): r for r in rows}

    r_yes = by_key[("r_mve", "KXNFL-YESWIN")]
    assert r_yes["series"] == "KXNFL"
    assert r_yes["side"] == "yes"
    assert r_yes["legs_status"] == "OK"
    assert r_yes["market_status"] == "finalized"
    assert r_yes["market_result"] == "yes"
    assert r_yes["leg_won"] is True

    r_no_on_yes = by_key[("r_mve", "KXNBA-NO_ON_YESWIN")]
    assert r_no_on_yes["series"] == "KXNBA"
    assert r_no_on_yes["side"] == "no"
    assert r_no_on_yes["legs_status"] == "REJ_UNMAPPED_TICKER"
    assert r_no_on_yes["leg_won"] is False

    r_active = by_key[("r_mve", "KXNHL-ACTIVE")]
    assert r_active["market_status"] == "active"
    assert r_active["market_result"] == ""
    assert r_active["leg_won"] is None

    r_fb_yeswin_no = by_key[("r_fb", "KXNFL-YESWIN")]
    assert r_fb_yeswin_no["legs_status"] == "PRICED"
    assert r_fb_yeswin_no["leg_won"] is False

    r_fb_nolose = by_key[("r_fb", "KXMLB-NOLOSE")]
    assert r_fb_nolose["legs_status"] == "PRICED"
    assert r_fb_nolose["leg_won"] is True

    # Downstream compatibility with build_family_frequency_table
    settled_freq_rows = [
        {"series": r["series"], "won": r["market_result"] == "yes"}
        for r in rows
        if r["leg_won"] is not None
    ]
    freq = build_family_frequency_table(settled_freq_rows)
    assert freq["KXNFL"] == pytest.approx(1.0)
    assert freq["KXNBA"] == pytest.approx(1.0)
    assert freq["KXMLB"] == pytest.approx(0.0)
    assert "KXNHL" not in freq


def test_finalize_cli_settle_legs_aborts_cleanly_without_credentials(monkeypatch, capsys):
    for k in (
        "KALSHI_RFQ_ENV",
        "KALSHI_PROD_API_KEY_ID",
        "KALSHI_PROD_PRIVATE_KEY_PATH",
        "KALSHI_PROD_PRIVATE_KEY_PEM",
        "KALSHI_API_KEY_ID",
        "KALSHI_PRIVATE_KEY_PATH",
        "KALSHI_PRIVATE_KEY_PEM",
        "KEXEC_ENV_FILE",
    ):
        monkeypatch.delenv(k, raising=False)

    called = []
    monkeypatch.setattr(
        "scripts.rfq_tape_finalize.finalize_day",
        lambda *a, **kw: called.append((a, kw)),
    )
    rc = finalize_main(["--day", DAY, "--settle-legs"])
    captured = capsys.readouterr()
    assert rc != 0
    assert "error" in captured.err.lower()
    assert called == []
