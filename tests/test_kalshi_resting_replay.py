"""Tests for ev.kalshi_resting_replay (Argon Prong 2 tape-delayed resting simulator)."""
import pytest

from ev.kalshi_resting_replay import (admit_combo, crosses, day_cluster_bootstrap, fee_entry,
                                      fill_pnl, grade_combo_fills, grade_fills, pickoff_pp,
                                      resting_no_bid, verdict)


def leg(sport="nfl", stat="rec_yds", game="nfl:2026-10-18:A@B", player="p1", side="yes",
        n_books=6, pooled=False, p_cons=0.5, p_model=None):
    return {"sport": sport, "stat": stat, "game_key": game, "player": player, "side": side,
            "n_books": n_books, "pooled": pooled, "p_cons_yes": p_cons, "p_model_yes": p_model}


# ---- bid construction at known fairs -------------------------------------------------
def test_bid_sgp_known_fair():
    # (1 - 0.10) - 0.040 - fee(0.85)=0.0044625 -> 0.8515 -> floor 0.85
    assert resting_no_bid(0.10, 0.040) == 0.85


def test_bid_cross_game_known_fair():
    # (1 - 0.20) - 0.050 - fee(0.74)=0.006734 -> 0.7433 -> floor 0.74
    assert resting_no_bid(0.20, 0.050) == 0.74


def test_bid_saves_known_fair():
    assert resting_no_bid(0.05, 0.035) == 0.91


def test_bid_floors_down_not_rounds():
    # fee-free price 0.857 would round to 0.86; the bid must floor to 0.85 territory
    b = resting_no_bid(0.103, 0.040)
    assert b <= 0.85


def test_fee_entry_formula():
    assert fee_entry(0.50) == pytest.approx(0.00875)
    assert fee_entry(0.85) == pytest.approx(0.035 * 0.85 * 0.15)
    assert fee_entry(0.0) == 0.0 and fee_entry(1.0) == 0.0


# ---- crossing detection ---------------------------------------------------------------
def test_cross_exact_price_counts():
    assert crosses(0.15, 0.85) is True          # yes_price == 1.00 - bid exactly


def test_cross_below_does_not_count():
    assert crosses(0.1499, 0.85) is False
    assert crosses(0.16, 0.85) is True


# ---- fee deductions in fill P&L --------------------------------------------------------
def test_fill_pnl_both_fee_treatments():
    b = 0.85
    f = fee_entry(b)
    assert fill_pnl(b, False, roundtrip=False) == pytest.approx(1.0 - b - f)
    assert fill_pnl(b, False, roundtrip=True) == pytest.approx(1.0 - b - 2 * f)
    assert fill_pnl(b, True, roundtrip=False) == pytest.approx(-b - f)
    assert fill_pnl(b, True, roundtrip=True) == pytest.approx(-b - 2 * f)
    # roundtrip costs exactly one extra entry fee vs one-way
    assert fill_pnl(b, False, roundtrip=False) - fill_pnl(b, False, roundtrip=True) == pytest.approx(f)


# ---- 10-minute skip ----------------------------------------------------------------------
def test_ten_minute_skip():
    created = 1_000_000.0
    trades = [{"ts": created + 60, "yes_price": 0.99},     # creation-window print: no fill
              {"ts": created + 599, "yes_price": 0.99},    # still inside window: no fill
              {"ts": created + 600, "yes_price": 0.99},    # exactly at post time: fills
              {"ts": created + 3600, "yes_price": 0.01}]   # post-window but does not cross
    fills = grade_combo_fills(created_epoch=created, p_no_bid=0.85, p_true_yes=0.10,
                              yes_won=False, trades=trades)
    assert len(fills) == 1
    assert fills[0]["ts"] == created + 600


# ---- pickoff ------------------------------------------------------------------------------
def test_pickoff_computation():
    fills = [{"yes_won": True, "p_true_yes": 0.10}, {"yes_won": False, "p_true_yes": 0.20},
             {"yes_won": False, "p_true_yes": 0.30}, {"yes_won": False, "p_true_yes": 0.40}]
    # realized hit rate 0.25 - mean p_true 0.25 = 0.00pp
    assert pickoff_pp(fills) == pytest.approx(0.0)
    fills2 = [{"yes_won": True, "p_true_yes": 0.10}, {"yes_won": True, "p_true_yes": 0.10}]
    assert pickoff_pp(fills2) == pytest.approx(90.0)
    assert pickoff_pp([]) is None


# ---- admission ------------------------------------------------------------------------------
def test_admit_cross_game_tier1():
    legs = [leg(game="g1", player="a"), leg(game="g2", player="b", stat="rush_yds")]
    a = admit_combo(n_legs=2, n_unpriced=0, p_cons_yes=0.30, legs=legs)
    assert a.admitted and a.tier == "tier1" and a.edge_class == "cross_game"


def test_admit_cross_game_p_cap():
    legs = [leg(game="g1", player="a"), leg(game="g2", player="b", stat="rush_yds")]
    a = admit_combo(n_legs=2, n_unpriced=0, p_cons_yes=0.36, legs=legs)
    assert not a.admitted and a.reason == "REJ_CROSS_GAME_P_GT_0_35"


def test_admit_sgp_tier1_and_same_player_rejected():
    legs = [leg(game="g1", player="a"), leg(game="g1", player="b", stat="rush_yds")]
    a = admit_combo(n_legs=2, n_unpriced=0, p_cons_yes=0.30, legs=legs)
    assert a.admitted and a.edge_class == "sgp_diff_player"
    legs_sp = [leg(game="g1", player="a"), leg(game="g1", player="a", stat="receptions")]
    a2 = admit_combo(n_legs=2, n_unpriced=0, p_cons_yes=0.30, legs=legs_sp)
    assert not a2.admitted and a2.reason == "REJ_SGP_SAME_PLAYER_UNMEASURED"


def test_admit_gates_unpriced_and_books():
    legs = [leg(game="g1", player="a"), leg(game="g2", player="b", stat="rush_yds")]
    assert not admit_combo(n_legs=2, n_unpriced=1, p_cons_yes=0.3, legs=legs).admitted
    thin = [leg(game="g1", player="a", n_books=3), leg(game="g2", player="b", stat="rush_yds")]
    a = admit_combo(n_legs=2, n_unpriced=0, p_cons_yes=0.3, legs=thin)
    assert not a.admitted and a.reason == "REJ_LEG_BOOKS_LT_4"


def test_admit_saves_tier2():
    saves = leg(sport="nhl", stat="saves", game="nhl:2026-10-18:A@B", player="goalie",
                p_cons=0.55, p_model=0.60)
    other = leg(game="g2", player="b", stat="rush_yds")
    a = admit_combo(n_legs=2, n_unpriced=0, p_cons_yes=0.30, legs=[saves, other])
    assert a.admitted and a.tier == "tier2" and a.edge_class == "saves_anchored"
    weak = leg(sport="nhl", stat="saves", game="nhl:2026-10-18:A@B", player="goalie",
               p_cons=0.55, p_model=0.57)
    a2 = admit_combo(n_legs=2, n_unpriced=0, p_cons_yes=0.30, legs=[weak, other])
    assert a2.admitted and a2.tier == "tier1"   # falls back to cross-game class, not saves


def test_admit_long_k():
    legs = [leg(game=f"g{i}", player=f"p{i}", stat="rush_yds") for i in range(5)]
    a = admit_combo(n_legs=5, n_unpriced=0, p_cons_yes=0.14, legs=legs)
    assert a.admitted and a.tier == "long_k"
    pooled = [dict(l, pooled=True) if i == 0 else l for i, l in enumerate(legs)]
    assert not admit_combo(n_legs=5, n_unpriced=0, p_cons_yes=0.14, legs=pooled).admitted
    assert not admit_combo(n_legs=5, n_unpriced=0, p_cons_yes=0.16, legs=legs).admitted


# ---- verdict mapping at the boundaries -------------------------------------------------------
def _g(mean, lo, n=60):
    return {"n": n, "mean_c": mean, "ci95_c": [lo, lo + 5.0]}


def test_verdict_pass_at_boundaries():
    assert verdict(_g(3.50, 0.50), _g(2.50, 0.50), 2.0) == "PASS"


def test_verdict_near_miss_is_neither_not_pass():
    assert verdict(_g(3.49, 0.50), _g(2.50, 0.50), 2.0) == "NEITHER"
    assert verdict(_g(3.50, 0.49), _g(2.50, 0.50), 2.0) == "NEITHER"
    assert verdict(_g(3.50, 0.50), _g(2.50, 0.50), 2.01) == "NEITHER"
    assert verdict(_g(3.50, 0.50, n=49), _g(2.50, 0.50, n=49), 2.0) == "NEITHER"


def test_verdict_kill_boundaries():
    assert verdict(_g(0.0, -1.0), _g(1.0, -1.0), 0.0) == "KILL"          # mean exactly 0.0 kills
    assert verdict(_g(2.0, -1.0), _g(0.0, -1.0), 0.0) == "KILL"          # roundtrip mean 0.0 kills
    assert verdict(_g(2.0, -2.01), _g(2.0, -1.0), 0.0) == "KILL"         # lower bound < -2.0 kills
    assert verdict(_g(2.0, -2.0), _g(2.0, -2.0), 0.0) == "NEITHER"       # lower bound exactly -2.0 does not
    assert verdict(_g(-0.01, -1.0), _g(1.0, -1.0), 0.0) == "KILL"


def test_verdict_no_fills_is_neither():
    empty = {"n": 0, "mean_c": None, "ci95_c": None}
    assert verdict(empty, empty, None) == "NEITHER"


def test_grade_fills_end_to_end():
    fills = {"2026-10-20": [{"pnl_oneway": 0.10, "pnl_roundtrip": 0.09, "yes_won": False, "p_true_yes": 0.05}]}
    g = grade_fills(fills)
    assert g["n_fills"] == 1 and g["oneway"]["mean_c"] == pytest.approx(10.0)
    assert g["pickoff_pp"] == pytest.approx(-5.0)
    b = day_cluster_bootstrap({"2026-10-20": [0.1, 0.1]})
    assert b["n"] == 2 and b["ci95_c"] == [10.0, 10.0]
