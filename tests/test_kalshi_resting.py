"""Offline tests for the Kalshi resting combo maker (ev.kalshi_resting + daemon).

Every Argon Part A rule gets an admit and a reject case. No network: the venue is a
fake implementing the RestingVenue protocol, and the adapter is tested against a
recording stub of KalshiClient.request.
"""
import pytest

from ev.kalshi_resting import (
    SAME_PLAYER_ALLOWED_PAIRS, ComboCandidate, ExposureBook, KalshiRequestVenue,
    RestingEngine, RestingLeg, RestingLimits, RestingOrder, TierDecision,
    assert_demo_only, DemoOnlyError, evaluate_tier, fee_entry, fee_reserve,
    joint_probabilities, order_liability, price_quote, queue_decision,
    QUEUE_AMEND, QUEUE_CANCEL_REPOST, QUEUE_HOLD, toxicity_ok,
)

NOW = 1_800_000_000.0
GK1 = "nfl:2026-11-12:BUF@KC"
GK2 = "nfl:2026-11-12:DAL@PHI"
GK3 = "nhl:2026-11-12:TOR@BOS"
GK4 = "nba:2026-11-12:LAL@NYK"


def leg(ticker="T1", gk=GK1, sport="nfl", stat="rec_yds", player="p1", p=0.40,
        n=5, disp=0.01, side="yes", **kw):
    kw.setdefault("exact_priced", True)
    return RestingLeg(ticker=ticker, side=side, game_key=gk, sport=sport, stat=stat,
                      n_books=n, disp=disp, p_cons_yes=p, player_norm=player, **kw)


def combo(legs, ticker="KXMVE-TEST", created=NOW - 3600, **kw):
    return ComboCandidate(market_ticker=ticker, legs=tuple(legs), created_ts=created, **kw)


def sgp_combo(p=0.40):
    return combo([leg("T1", GK1, stat="rec_yds", player="p1", p=p),
                  leg("T2", GK1, stat="pass_yds", player="p2", p=p)])


def cross_combo(p=0.40):
    return combo([leg("T1", GK1, player="p1", p=p), leg("T2", GK2, player="p2", p=p)])


def saves_combo(model=0.54, cons=0.50):
    return combo([leg("S1", GK3, sport="nhl", stat="saves", player="g1", p=cons,
                      p_model_yes=model, fair_source="saves_model"),
                  leg("T2", GK1, player="p1", p=0.40)])


# --------------------------------------------------------------------------- tiers
def test_same_player_allowlist_ships_empty():
    # Promotion is a later config change after the Nov 13 judgment; the NFL pair
    # Argon advanced for the RFQ shadow defaults must NOT be in the resting list.
    assert SAME_PLAYER_ALLOWED_PAIRS == frozenset()
    assert ("nfl", "rec_yds", "receptions") not in SAME_PLAYER_ALLOWED_PAIRS


def test_tier1_sgp_admit():
    d = evaluate_tier(sgp_combo(), 0.20)
    assert d.admitted and d.tier == 1 and d.edge_class == 0.040


def test_tier1_cross_game_admit():
    d = evaluate_tier(cross_combo(), 0.30)
    assert d.admitted and d.tier == 1 and d.edge_class == 0.050


def test_tier1_cross_game_rejects_above_longshot_cap():
    d = evaluate_tier(cross_combo(), 0.40)
    assert not d.admitted and d.reason == "REJ_NOT_LONGSHOT"


def test_tier1_four_leg_admit():
    c = combo([leg("T1", GK1, player="p1"), leg("T2", GK2, player="p2"),
               leg("T3", GK3, sport="nhl", stat="saves", player="g1"),
               leg("T4", GK4, sport="nba", stat="points", player="p4")])
    d = evaluate_tier(c, 0.10)
    assert d.admitted and d.tier == 1


def test_tier2_saves_admit():
    d = evaluate_tier(saves_combo(), 0.20)
    assert d.admitted and d.tier == 2 and d.edge_class == 0.035


def test_tier2_saves_small_model_edge_is_not_tier2():
    d = evaluate_tier(saves_combo(model=0.52), 0.20)   # +2.0pp < 3.0pp bar
    assert d.tier != 2


def test_tier2_saves_leg_needs_four_books():
    c = combo([leg("S1", GK3, sport="nhl", stat="saves", player="g1", p=0.50, n=3,
                   p_model_yes=0.56, fair_source="saves_model"),
               leg("T2", GK1, player="p1")])
    d = evaluate_tier(c, 0.20)
    assert not d.admitted and d.reason == "REJ_LOW_BOOKS"


def test_single_leg_rejected():
    d = evaluate_tier(combo([leg()]), 0.40)
    assert not d.admitted and d.reason == "REJ_SINGLE_LEG"


def test_unpriced_leg_rejected():
    c = combo([leg("T1"), leg("T2", GK2, player="p2", p=None)], unpriced_legs=1)
    d = evaluate_tier(c, 0.20)
    assert not d.admitted and d.reason == "REJ_UNPRICED_LEG"


def test_low_books_rejected():
    d = evaluate_tier(combo([leg("T1", n=3), leg("T2", GK2, player="p2")]), 0.20)
    assert not d.admitted and d.reason == "REJ_LOW_BOOKS"


def test_high_dispersion_rejected():
    d = evaluate_tier(combo([leg("T1", disp=0.09), leg("T2", GK2, player="p2")]), 0.20)
    assert not d.admitted and d.reason == "REJ_HIGH_DISPERSION"


def test_long_tail_rejected_when_not_exact_priced():
    legs = [leg(f"T{i}", f"nfl:2026-11-12:A{i}@B{i}", player=f"p{i}", exact_priced=False)
            for i in range(5)]
    d = evaluate_tier(combo(legs), 0.10)
    assert not d.admitted and d.reason == "REJ_LONG_TAIL"


def test_long_tail_rejected_when_joint_too_likely():
    legs = [leg(f"T{i}", f"nfl:2026-11-12:A{i}@B{i}", player=f"p{i}") for i in range(5)]
    d = evaluate_tier(combo(legs), 0.20)
    assert not d.admitted and d.reason == "REJ_LONG_TAIL"


def test_long_tail_admit_exact_priced_deep_longshot():
    legs = [leg(f"T{i}", f"nfl:2026-11-12:A{i}@B{i}", player=f"p{i}") for i in range(5)]
    d = evaluate_tier(combo(legs), 0.10)
    assert d.admitted and d.tier == 1


def test_same_player_pair_rejected_unmeasured():
    c = combo([leg("T1", GK1, stat="rec_yds", player="p1"),
               leg("T2", GK1, stat="receptions", player="p1")])
    d = evaluate_tier(c, 0.20)
    assert not d.admitted and d.reason == "REJ_SAME_PLAYER_UNMEASURED"


def test_nba_assists_excluded():
    c = combo([leg("T1", GK4, sport="nba", stat="assists", player="p1"),
               leg("T2", GK1, player="p2")])
    d = evaluate_tier(c, 0.20)
    assert not d.admitted and d.reason == "REJ_EXCLUDED_FAIR_INPUT"


def test_nhl_skater_model_excluded():
    c = combo([leg("T1", GK3, sport="nhl", stat="points", player="s1",
                   fair_source="trained_model", p_model_yes=0.45),
               leg("T2", GK1, player="p2")])
    d = evaluate_tier(c, 0.20)
    assert not d.admitted and d.reason == "REJ_EXCLUDED_FAIR_INPUT"


def test_nfl_hierarchical_excluded():
    c = combo([leg("T1", GK1, hierarchical=True), leg("T2", GK2, player="p2")])
    d = evaluate_tier(c, 0.20)
    assert not d.admitted and d.reason == "REJ_EXCLUDED_FAIR_INPUT"


# --------------------------------------------------------------------------- fees + pricing
def test_fee_math():
    assert fee_entry(0.50) == pytest.approx(0.00875)          # 0.875c one-way
    assert fee_reserve(0.50, unwind=False) == pytest.approx(0.00875)
    assert fee_reserve(0.50, unwind=True) == pytest.approx(0.0175)   # 2x for unwind
    assert fee_entry(0.85) == pytest.approx(0.0044625)         # ~0.45c
    assert fee_entry(0.0) == 0.0 and fee_entry(1.0) == 0.0


def _manual_bid(p_cons, edge, unwind):
    from ev.kalshi_rfq_pricer import floor_to_tick
    gross = (1.0 - p_cons) - edge
    bid = gross
    for _ in range(3):
        bid = floor_to_tick(gross - fee_reserve(bid, unwind), 0.01)
    return bid


@pytest.mark.parametrize("edge", [0.040, 0.045, 0.050, 0.035])
def test_pricing_per_class(edge):
    tier = TierDecision(True, 1, "TIER_1", edge)
    q = price_quote(cross_combo(), tier, p_true_yes=0.16, p_cons_yes=0.175, unwind=True)
    assert q is not None
    assert q.no_bid == _manual_bid(0.175, edge, True)
    assert q.edge_class == edge


def test_pricing_roi_floor_rejection():
    tier = TierDecision(True, 1, "TIER_1", 0.040)
    # Model truth is WORSE for the lay than the padded consensus implies: net < 3.5c.
    q = price_quote(cross_combo(), tier, p_true_yes=0.30, p_cons_yes=0.28, unwind=True)
    assert q is None


def test_pricing_unwind_choice_changes_bid_and_reserve():
    tier = TierDecision(True, 2, "TIER_2", 0.035)
    held = price_quote(cross_combo(), tier, 0.16, 0.175, unwind=False)
    unwound = price_quote(cross_combo(), tier, 0.16, 0.175, unwind=True)
    assert held is not None and unwound is not None
    assert held.no_bid >= unwound.no_bid
    assert held.fee_reserve_per_contract == pytest.approx(fee_entry(held.no_bid))
    assert unwound.fee_reserve_per_contract == pytest.approx(2 * fee_entry(unwound.no_bid))


def test_joint_probabilities_cross_game_and_pads():
    pt, pc = joint_probabilities(cross_combo(0.40))
    assert pt == pytest.approx(0.16)                          # independent across games
    assert pc == pytest.approx(0.408 * 0.408)                 # +0.008/leg pad
    assert pc > pt


def test_joint_probabilities_prot_pad():
    c = combo([leg("T1", GK1, player="p1", protected=True), leg("T2", GK2, player="p2")])
    pt, pc = joint_probabilities(c)
    assert pt == pytest.approx(0.408 * 0.40)                  # prot pad applies to truth too (venue asymmetry)
    assert pc == pytest.approx(0.416 * 0.408)


# --------------------------------------------------------------------------- fake venue + engine
class FakeVenue:
    def __init__(self):
        self.orders = {}          # order_id -> venue order dict
        self.fills = []
        self.positions = []
        self.cancels = []
        self.amends = []
        self.ignore_cancel = False
        self._n = 0

    def submit_resting_no(self, *, market_ticker, client_order_id, count, no_price):
        self._n += 1
        oid = f"oid-{self._n}"
        self.orders[oid] = {"order_id": oid, "client_order_id": client_order_id,
                            "status": "resting", "ticker": market_ticker}
        return {"order_id": oid, "client_order_id": client_order_id}

    def cancel(self, *, order_id, market_ticker):
        self.cancels.append((order_id, market_ticker))
        if not self.ignore_cancel and order_id in self.orders:
            self.orders[order_id]["status"] = "canceled"
        return {"order_id": order_id, "reduced_by": "2.00"}

    def amend(self, *, order_id, market_ticker, count=None, expiration_ts=None):
        self.amends.append((order_id, count, expiration_ts))
        return {"order_id": order_id}

    def queue_position(self, *, order_id):
        return 0.0

    def get_orders(self):
        return [o for o in self.orders.values() if o["status"] == "resting"]

    def get_positions(self):
        return list(self.positions)

    def get_fills(self):
        return list(self.fills)

    def get_balance(self):
        return {"balance_dollars": "97.78"}


def engine(**kw):
    return RestingEngine(venue=FakeVenue(), **kw)


def posted(e=None, c=None, now=NOW):
    e = e or engine()
    res = e.consider(c or cross_combo(), now)
    assert res["action"] == "POSTED", res
    return e, res["order"]


def test_engine_posts_combo_only_with_unwind_default():
    e, o = posted()
    assert o.count == 2 and o.unwind is True
    assert o.fee_reserve_per_contract == pytest.approx(2 * fee_entry(o.no_price))
    assert o.status == "resting" and len(e.venue.orders) == 1


def test_engine_toxicity_window():
    e = engine()
    young = cross_combo()
    young = ComboCandidate(young.market_ticker, young.legs, created_ts=NOW - 599)
    assert e.consider(young, NOW)["reason"] == "REJ_TOXICITY_WINDOW"
    boundary = ComboCandidate(young.market_ticker, young.legs, created_ts=NOW - 600)
    assert e.consider(boundary, NOW)["action"] == "POSTED"
    assert toxicity_ok(NOW - 600, NOW) and not toxicity_ok(NOW - 599, NOW)


def test_engine_rejects_single_leg_and_unpriced():
    e = engine()
    assert e.consider(combo([leg()]), NOW)["reason"] == "REJ_SINGLE_LEG"
    bad = combo([leg("T1"), leg("T2", GK2, player="p2", p=None)], unpriced_legs=1)
    assert e.consider(bad, NOW)["reason"] == "REJ_UNPRICED_LEG"


# --------------------------------------------------------------------------- sizing caps
def test_exposure_per_order_liability_cap():
    book = ExposureBook(RestingLimits())
    assert book.check([], "M", (GK1,), 2, 0.96) is not None      # $1.92 > $1.90
    assert book.check([], "M", (GK1,), 2, 0.95) is None          # exactly $1.90
    assert order_liability(2, 0.95) == pytest.approx(1.90)


def test_engine_per_market_cap():
    e = engine()
    c = cross_combo()
    assert e.consider(c, NOW)["action"] == "POSTED"              # 2 contracts
    assert e.consider(c, NOW)["action"] == "POSTED"              # 4 = cap
    res = e.consider(c, NOW)
    assert res["action"] == "REJECT" and "REJ_SIZE" in res["reason"]


def test_engine_total_collateral_and_open_order_caps():
    e = engine(limits=RestingLimits(max_total_collateral=2.00))
    assert e.consider(cross_combo(), NOW)["action"] == "POSTED"
    res = e.consider(cross_combo(), NOW)
    assert res["action"] == "REJECT" and "REJ_SIZE" in res["reason"]
    e2 = engine(limits=RestingLimits(max_open_orders=1))
    assert e2.consider(cross_combo(), NOW)["action"] == "POSTED"
    res2 = e2.consider(cross_combo(), NOW)
    assert res2["action"] == "REJECT" and "REJ_SIZE" in res2["reason"]


def test_engine_per_game_liability_cap():
    e = engine(limits=RestingLimits(max_liability_per_game=2.50))
    assert e.consider(cross_combo(), NOW)["action"] == "POSTED"  # ~$1.54 on GK1
    res = e.consider(cross_combo(), NOW)                          # another ~$1.54 -> > $2.50
    assert res["action"] == "REJECT" and "REJ_SIZE" in res["reason"]


# --------------------------------------------------------------------------- kill switch
def test_reconcile_clean():
    e, _o = posted()
    res = e.reconcile(NOW + 30)
    assert res["ok"] and not e.killed


def test_reconcile_orphan_fill_trips_kill():
    e, _o = posted()
    e.venue.fills.append({"fill_id": "f-9", "client_order_id": "kxrest-unknown"})
    res = e.reconcile(NOW + 30)
    assert not res["ok"] and e.killed and e.kill_reason == "kalshi_resting"
    assert e.venue.cancels, "kill switch must cancel everything"
    assert e.consider(cross_combo(), NOW + 60)["reason"] == "KILLED"


def test_reconcile_position_without_known_order_trips():
    e, _o = posted()
    e.venue.positions.append({"ticker": "KXMVE-OTHER", "position_fp": "2.00"})
    res = e.reconcile(NOW + 30)
    assert not res["ok"] and e.killed


def test_reconcile_unknown_resting_order_trips():
    e, _o = posted()
    e.venue.orders["oid-x"] = {"order_id": "oid-x", "client_order_id": "kxrest-stranger",
                                "status": "resting", "ticker": "KXMVE-TEST"}
    res = e.reconcile(NOW + 30)
    assert not res["ok"] and e.killed


def test_reconcile_uncancelled_order_trips_after_grace_only():
    e, o = posted()
    e.venue.ignore_cancel = True
    e.cancel_order(o, NOW + 30)
    assert e.reconcile(NOW + 32)["ok"]                            # inside 5s grace
    res = e.reconcile(NOW + 37)                                   # 7s after cancel request
    assert not res["ok"] and e.killed


def test_daily_loss_limit_trips_kill():
    e = engine()
    e.daily_pnl = -15.00
    assert e.check_daily_loss() and e.killed and e.kill_reason == "kalshi_resting"
    e2 = engine()
    e2.daily_pnl = -14.99
    assert not e2.check_daily_loss()


# --------------------------------------------------------------------------- velocity gate
def test_velocity_leg_move_trips_and_cancels_game():
    e, o = posted()
    other = combo([leg("T3", GK3, sport="nhl", stat="saves", player="g1"),
                   leg("T4", GK4, sport="nba", stat="points", player="p4")],
                  ticker="KXMVE-OTHER")
    res2 = e.consider(other, NOW)
    assert res2["action"] == "POSTED"
    assert not e.on_leg_consensus(GK1, "T1", 0.40, NOW + 100)
    assert e.on_leg_consensus(GK1, "T1", 0.44, NOW + 103)         # +4pp in 3s
    assert o.status == "cancel_pending"
    assert res2["order"].status == "resting"                      # other game untouched
    assert e.velocity.in_cooldown(GK1, NOW + 103 + 119)
    assert not e.velocity.in_cooldown(GK1, NOW + 103 + 121)
    assert e.consider(cross_combo(), NOW + 150)["reason"] == "REJ_COOLDOWN"


def test_velocity_leg_move_slow_or_small_does_not_trip():
    e = engine()
    assert not e.on_leg_consensus(GK1, "T1", 0.40, NOW)
    assert not e.on_leg_consensus(GK1, "T1", 0.44, NOW + 10)      # 4pp but over 10s
    e2 = engine()
    assert not e2.on_leg_consensus(GK1, "T1", 0.40, NOW)
    assert not e2.on_leg_consensus(GK1, "T1", 0.42, NOW + 3)      # 2pp in 3s


def test_velocity_print_jump_trips():
    e, o = posted()
    assert not e.on_kalshi_print(GK1, "T1", 0.50, NOW + 100)
    assert e.on_kalshi_print(GK1, "T1", 0.66, NOW + 101.5)        # +16c in 1.5s
    assert o.status == "cancel_pending"
    e2, o2 = posted()
    assert not e2.on_kalshi_print(GK1, "T1", 0.50, NOW)
    assert not e2.on_kalshi_print(GK1, "T1", 0.66, NOW + 3)       # same jump, too slow
    assert o2.status == "resting"


# --------------------------------------------------------------------------- queue policy
def _order(no_price=0.77, p_true=0.16):
    return RestingOrder(client_order_id="c1", market_ticker="M", game_keys=(GK1,),
                        leg_tickers=("T1", "T2"), count=2, no_price=no_price,
                        p_true_yes=p_true, p_cons_yes=0.175, fee_reserve_per_contract=0.012,
                        unwind=True, posted_ts=NOW)


def test_queue_hold_inside_band():
    o = _order()
    assert queue_decision(o, 0.16) == QUEUE_HOLD                  # no drift
    assert queue_decision(o, 0.17) == QUEUE_HOLD                  # -1.0c adverse, at band edge
    assert queue_decision(o, 0.15) == QUEUE_HOLD                  # +1.0c favourable


def test_queue_amend_for_size_or_expiry():
    o = _order()
    assert queue_decision(o, 0.16, size_change=True) == QUEUE_AMEND
    assert queue_decision(o, 0.16, expiry_change=True) == QUEUE_AMEND


def test_queue_cancel_on_adverse_move_and_velocity():
    o = _order()
    assert queue_decision(o, 0.185) == QUEUE_CANCEL_REPOST        # -2.5c adverse
    assert queue_decision(o, 0.175) == QUEUE_HOLD                 # exactly -1.5c: not beyond
    assert queue_decision(o, 0.16, velocity_tripped=True) == QUEUE_CANCEL_REPOST


def test_queue_cancel_when_edge_gone():
    o = _order(no_price=0.815)
    # fair drifts -1.0c (inside band) but net edge = 0.83-0.815-fee ~ 0.4c < 2.5c
    assert queue_decision(o, 0.17) == QUEUE_CANCEL_REPOST


def test_engine_apply_queue_policy_amend_preserves_order():
    e, o = posted()
    action = e.apply_queue_policy(o, o.p_true_yes, NOW + 10, new_count=1)
    assert action == QUEUE_AMEND and o.count == 1 and o.status == "resting"
    assert e.venue.amends and e.venue.amends[0][1] == 1


# --------------------------------------------------------------------------- revalidation
def test_revalidation_cancels_order_that_fails_admission():
    from ev.kalshi_resting import FreshSnapshot
    e, o = posted()
    bad_legs = [leg("T1", GK1, n=2), leg("T2", GK2, player="p2", n=2)]
    snap = FreshSnapshot(combo(bad_legs), p_true_yes=0.16, p_cons_yes=0.175)
    cancelled = e.revalidate({o.market_ticker: snap}, NOW + 10)
    assert cancelled == [o.client_order_id] and o.status == "cancel_pending"


def test_revalidation_keeps_valid_order():
    from ev.kalshi_resting import FreshSnapshot
    e, o = posted()
    pt, pc = joint_probabilities(cross_combo())
    snap = FreshSnapshot(cross_combo(), p_true_yes=pt, p_cons_yes=pc)
    assert e.revalidate({o.market_ticker: snap}, NOW + 10) == []
    assert o.status == "resting"


def test_revalidation_cancels_when_price_floor_moves_against():
    from ev.kalshi_resting import FreshSnapshot
    e, o = posted(c=sgp_combo(0.40))
    fresh = sgp_combo(0.75)                                       # legs steamed: our bid is now rich
    pt, pc = joint_probabilities(fresh)
    snap = FreshSnapshot(fresh, p_true_yes=pt, p_cons_yes=pc)
    cancelled = e.revalidate({o.market_ticker: snap}, NOW + 10)
    assert cancelled == [o.client_order_id]


# --------------------------------------------------------------------------- venue adapter
class StubRequestClient:
    def __init__(self):
        self.calls = []

    def request(self, method, path, params=None, body=None, **kw):
        self.calls.append((method, path, params, body))
        if path.endswith("/queue_position"):
            return {"queue_position_fp": "3.00"}
        return {"order_id": "oid-1", "orders": [], "fills": [], "market_positions": []}


def test_adapter_wire_format():
    stub = StubRequestClient()
    v = KalshiRequestVenue(stub)
    v.submit_resting_no(market_ticker="KXMVE-1", client_order_id="c1", count=2, no_price=0.77)
    method, path, _params, body = stub.calls[-1]
    assert method == "POST" and path == "/portfolio/events/orders"
    assert body["post_only"] is True and body["time_in_force"] == "good_till_canceled"
    assert body["side"] == "ask" and body["price"] == "0.2300"    # buy NO 0.77 == sell YES 0.23
    assert body["count"] == "2.00"
    v.cancel(order_id="oid-1", market_ticker="KXMVE-1")
    method, path, params, _body = stub.calls[-1]
    assert method == "DELETE" and params == {"market_ticker": "KXMVE-1"}   # NOT the stale `ticker`
    assert v.queue_position(order_id="oid-1") == 3.0
    v.amend(order_id="oid-1", market_ticker="KXMVE-1", count=1)
    method, path, _params, body = stub.calls[-1]
    assert method == "POST" and path.endswith("/amend") and body["count_fp"] == "1.00"


# --------------------------------------------------------------------------- demo-only
def test_demo_only_guard():
    assert_demo_only("demo", "https://external-api.demo.kalshi.co/trade-api/v2")
    with pytest.raises(DemoOnlyError):
        assert_demo_only("production", "https://external-api.demo.kalshi.co/trade-api/v2")
    with pytest.raises(DemoOnlyError):
        assert_demo_only("production-readonly", "https://external-api.kalshi.com/trade-api/v2")
    with pytest.raises(DemoOnlyError):
        assert_demo_only("demo", "https://external-api.kalshi.com/trade-api/v2")   # prod URL, demo env
    with pytest.raises(DemoOnlyError):
        assert_demo_only("demo", "https://api.elections.kalshi.com/trade-api/v2")
    with pytest.raises(DemoOnlyError):
        assert_demo_only("public-data", "https://external-api.demo.kalshi.co/trade-api/v2")
    with pytest.raises(DemoOnlyError):
        assert_demo_only(None, None)


def test_daemon_startup_refusals():
    from scripts.kalshi_resting_daemon import resolve_resting_run
    env, url = resolve_resting_run(None, None)
    assert env == "demo" and "demo.kalshi.co" in url
    with pytest.raises(SystemExit):
        resolve_resting_run("production", None)
    with pytest.raises(SystemExit):
        resolve_resting_run("production-readonly", None)
    with pytest.raises(SystemExit):
        resolve_resting_run("demo", "https://external-api.kalshi.com/trade-api/v2")
