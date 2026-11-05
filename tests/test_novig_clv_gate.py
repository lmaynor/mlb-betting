"""ev.novig.clv_gate: forward-CLV slice gate. Pure / offline: synthetic frames and a temp sqlite ledger, no network."""
import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from ev.novig import clv_gate as cg
from mlb.execution.kalshi.ledger import Ledger
from mlb_core.odds.utils import clv_pct_from_prices, implied_to_american

CFG = cg.GateConfig()
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
GK = "nfl:2026-10-12:BUF@LAR"
KICK = datetime(2026, 10, 13, 0, 15, tzinfo=timezone.utc)


# ------------------------------------------------------------------ statistics
@pytest.mark.parametrize("df,q", [(1, 12.7062), (2, 4.3027), (5, 2.5706), (10, 2.2281), (30, 2.0423), (1000, 1.9623)])
def test_t_quantile_matches_tables(df, q):
    assert cg.t_ppf(0.975, df) == pytest.approx(q, abs=2e-3)


def test_cluster_ci_hand_computed_and_wider_than_iid():
    # two days, 2 bets each: day residual sums are +4 / -4 -> V = G/(G-1) * (16 + 16) / n^2 = 2 * 32 / 16 = 4 -> se 2
    ci = cg.cluster_mean_ci([4.0, 4.0, 0.0, 0.0], ["a", "a", "b", "b"], 0.95)
    assert ci["mean"] == 2.0 and ci["n_clusters"] == 2 and ci["df"] == 1
    assert ci["se"] == pytest.approx(2.0)
    assert ci["ci_lo"] == pytest.approx(2.0 - cg.t_ppf(0.975, 1) * ci["se"])
    # the same numbers as if every bet were its own cluster are far tighter -- clustering must not be skipped
    iid = cg.cluster_mean_ci([4.0, 4.0, 0.0, 0.0], ["1", "2", "3", "4"], 0.95)
    assert iid["se"] < ci["se"]


def test_cluster_ci_cannot_be_formed_with_one_day_or_no_variance():
    one_day = cg.cluster_mean_ci([1.0, 2.0, 3.0], ["d", "d", "d"])
    assert one_day["mean"] == 2.0 and one_day["ci_lo"] is None and one_day["t"] is None
    flat = cg.cluster_mean_ci([1.0, 1.0], ["a", "b"])
    assert flat["ci_lo"] is None
    assert cg.cluster_mean_ci([], [])["n"] == 0


# ------------------------------------------------------------------ slice verdict
def mk_bets(n, days, mean, sd=3.0, cons=None, seed=1, sport="nfl", stat="rec_yds"):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        out.append({"day": f"2026-10-{1 + i % days:02d}", "sport": sport, "stat": stat, "clv_pct": float(rng.normal(mean, sd)),
                    "clv_cons_pct": None if cons is None else float(rng.normal(cons, sd))})
    return out


def test_open_only_when_all_conditions_hold():
    v = cg.slice_verdict(mk_bets(300, 30, 5.0, cons=3.0), CFG)
    assert v["status"] == "open" and v["open"] is True and v["n"] == 300 and v["ci_lo"] > 0 and v["n_days"] == 30


def test_no_data_and_insufficient_n_are_closed_however_good_the_mean_is():
    assert cg.slice_verdict([], CFG)["status"] == "no_data"
    v = cg.slice_verdict(mk_bets(249, 30, 50.0, sd=1.0, cons=40.0), CFG)       # 249 < 250, enormous CLV
    assert v["status"] == "insufficient_data" and v["open"] is False


def test_bets_without_a_usable_close_do_not_count_toward_n():
    bets = mk_bets(300, 30, 5.0, cons=3.0)
    for b in bets[:100]:
        b["clv_pct"] = None
    v = cg.slice_verdict(bets, CFG)
    assert v["n"] == 200 and v["n_bets"] == 300 and v["status"] == "insufficient_data"


def test_positive_mean_with_ci_straddling_zero_is_closed():
    bets = mk_bets(300, 30, 0.0, sd=8.0, cons=3.0)
    vals = np.array([b["clv_pct"] for b in bets])
    for b, x in zip(bets, (vals - vals.mean()) / vals.std() * 8.0 + 0.4):          # exact sample mean +0.4%, sd 8
        b["clv_pct"] = float(x)
    v = cg.slice_verdict(bets, CFG)
    assert v["mean_clv_pct"] > 0 and v["ci_lo"] < 0 and v["status"] == "closed_no_edge" and not v["open"]


def test_negative_clv_is_closed():
    assert cg.slice_verdict(mk_bets(300, 30, -4.0, cons=3.0), CFG)["status"] == "closed_no_edge"


def test_consensus_confirmation_is_an_extra_restriction():
    v = cg.slice_verdict(mk_bets(300, 30, 5.0, cons=-3.0), CFG)
    assert v["status"] == "closed_unconfirmed" and not v["open"]
    assert cg.slice_verdict(mk_bets(300, 30, 5.0, cons=None), CFG)["status"] == "closed_unconfirmed"   # not computable -> closed
    off = cg.GateConfig(require_consensus_confirm=False)
    assert cg.slice_verdict(mk_bets(300, 30, 5.0, cons=None), off)["status"] == "open"


def test_clustering_by_day_closes_a_slice_that_iid_would_open():
    # 300 bets on only 3 game days, with a day effect: iid-looking, but 3 clusters -> t(2) = 4.3 and a wide CI
    rng = np.random.default_rng(7)
    bets = []
    for d, eff in enumerate((6.0, -2.0, 3.0)):
        for _ in range(100):
            bets.append({"day": f"d{d}", "sport": "nfl", "stat": "rec_yds", "clv_pct": float(eff + rng.normal(0, 1)),
                         "clv_cons_pct": 1.0})
    v = cg.slice_verdict(bets, CFG)
    iid = cg.cluster_mean_ci([b["clv_pct"] for b in bets], list(range(len(bets))), 0.95)
    assert iid["ci_lo"] > 0 and v["ci_lo"] < 0 and not v["open"]


# ------------------------------------------------------------------ ledger -> bets
def seed_ledger(tmp_path):
    L = Ledger(str(tmp_path / "l.sqlite"))

    def order(iid, mode, units, cost, side="yes", outcome="EXECUTED", player="Greg Dortch", stat="rec_yds"):
        L.record_order(day="2026-10-12", mode=mode, intent_id=iid, client_order_id="c", order_id="o", ticker="m",
                       game_pk=GK, side=side, contracts=int(units), limit_price=0.5, status="FILLED", fill_count=units,
                       filled_cost=cost, fee=0.0)
        L.record_decision(day="2026-10-12", mode=mode, outcome=outcome, intent_id=iid, ticker="m", series="NOVIGPROP",
                          market="x", game_pk=GK, side=side, inputs={"player": player, "stat": stat, "line": 3.5})
    order("a", "novig", 8.0, 3.68)
    order("b", "novig", 0.0, 0.0, player="Blake Corum")                       # no fill -> not a bet
    order("c", "novig_paper", 5.0, 2.5)                                       # sandbox plumbing -> never counted
    L.record_decision(day="2026-10-12", mode="novig", outcome="REJ_EV_LOW", intent_id="a", ticker="m", series="N",
                      market="x", game_pk=GK, side="yes", inputs={"player": "Wrong Name", "stat": "pass_yds", "line": 9})
    return L


def test_ledger_bets_reads_only_filled_shadow_plans_with_vwap_entry(tmp_path):
    bets = cg.ledger_bets(seed_ledger(tmp_path)._c)
    assert len(bets) == 1
    b = bets[0]
    assert b["sport"] == "nfl" and b["stat"] == "rec_yds" and b["player_norm"] == "greg dortch" and b["line"] == 3.5
    assert b["entry_price"] == pytest.approx(0.46) and b["day"] == "2026-10-12" and b["side"] == "yes"


# ------------------------------------------------------------------ closing lines
def tables(snap_offsets, over=233, under=-285, age=1.0, point=3.5, fair=None):
    rows_s, rows_b = [], []
    for off in snap_offsets:                                   # minutes BEFORE kick
        ts = pd.Timestamp(KICK - timedelta(minutes=off))
        rows_s.append({"snap_ts": ts, "game_key": GK, "player_norm": "greg dortch", "stat": "rec_yds", "point": point,
                       "mins_to_kick": float(off),
                       "book_fair": json.dumps(fair or {"novig": 0.40, "a": 0.50, "b": 0.50, "c": 0.50})})
        rows_b.append({"snap_ts": ts, "game_key": GK, "player_norm": "greg dortch", "stat": "rec_yds", "point": point,
                       "book": "novig", "over": over, "under": under, "age_min": age})
    return pd.DataFrame(rows_s), pd.DataFrame(rows_b)


def bet(ts="2026-10-12T22:28:58Z", price=0.46, side="yes"):
    return {"intent_id": "a", "day": "2026-10-12", "ts": ts, "game_key": GK, "sport": "nfl", "stat": "rec_yds",
            "player_norm": "greg dortch", "line": 3.5, "side": side, "entry_price": price, "units": 8.0, "cost": 3.68}


def test_clv_is_the_repo_price_ratio_definition_and_uses_the_last_snapshot():
    s, b = tables([100, 60, 30])                               # last pre-kick snapshot is 30 min out
    out = cg.attach_closing([bet()], s, b, CFG)[0]
    assert out["close_reason"] is None and out["close_lead_min"] == 30.0
    entry_am = implied_to_american(0.46)
    assert out["clv_pct"] == pytest.approx(clv_pct_from_prices(entry_am, 233), abs=0.6)   # entry american rounds to a whole number
    assert out["clv_pct"] == pytest.approx(((1 / 0.46) / (1 + 2.33) - 1) * 100, abs=1e-3)
    # entry 0.46 (+117) vs close +233 for the SAME side: the price got worse -> negative CLV
    assert out["clv_pct"] < 0
    # closing consensus fair P(over) = 0.50 leave-Novig-out vs entry 0.46 -> +8.7% EV at the close
    assert out["clv_cons_pct"] == pytest.approx((0.50 / 0.46 - 1) * 100, abs=1e-3)


def test_under_side_uses_the_under_price():
    s, b = tables([30], over=233, under=-285)
    out = cg.attach_closing([bet(side="no", price=0.70)], s, b, CFG)[0]
    assert out["clv_pct"] == pytest.approx(((1 / 0.70) / (1 + 100 / 285) - 1) * 100, abs=1e-3)
    assert out["clv_cons_pct"] == pytest.approx((0.50 / 0.70 - 1) * 100, abs=1e-3)


@pytest.mark.parametrize("kw,reason", [
    (dict(snap_offsets=[200, 150]), "close_stale"),                    # last snapshot > 60 min before kick
    (dict(snap_offsets=[30], age=45.0), "close_quote_stale_or_age_unknown"),
    (dict(snap_offsets=[30], age=None), "close_quote_stale_or_age_unknown"),
    (dict(snap_offsets=[30], point=4.5), "quote_gone_at_close"),       # the line moved off the point
    (dict(snap_offsets=[30], under=None), None),                       # (over side still fine for a yes bet)
])
def test_unusable_closes_are_dropped_not_guessed(kw, reason):
    offs = kw.pop("snap_offsets")
    s, b = tables(offs, **kw)
    out = cg.attach_closing([bet()], s, b, CFG)[0]
    assert out["close_reason"] == reason
    assert (out["clv_pct"] is None) == (reason is not None)


def test_entry_after_the_last_snapshot_is_not_a_clv_observation():
    s, b = tables([30])
    out = cg.attach_closing([bet(ts="2026-10-12T23:59:00Z")], s, b, CFG)[0]
    assert out["close_reason"] == "entry_after_close" and out["clv_pct"] is None


def test_no_banked_data_means_no_clv_at_all():
    out = cg.attach_closing([bet()], None, None, CFG)[0]
    assert out["close_reason"] == "no_closing_snapshot" and out["clv_pct"] is None


# ------------------------------------------------------------------ verdict table + fail-closed gate
def test_every_known_slice_is_listed_and_closed_with_no_data():
    v = cg.compute_verdicts([], None, None, CFG, NOW)
    assert set(v["slices"]) == {f"nfl|{s}" for s in ("pass_tds", "pass_yds", "rec_yds", "receptions", "rush_yds")}
    assert all(not r["open"] and r["status"] == "no_data" for r in v["slices"].values())
    assert v["coverage"] == {"filled_bets": 0, "with_usable_close": 0, "dropped_reasons": {}}


def test_two_forward_bets_with_a_close_are_insufficient_not_open(tmp_path):
    s, b = tables([30])
    v = cg.compute_verdicts([bet(), dict(bet(), intent_id="b")], s, b, CFG, NOW)
    r = v["slices"]["nfl|rec_yds"]
    assert r["n"] == 2 and r["status"] == "insufficient_data" and not r["open"]
    assert "n=2 < 250" in r["reasons"][0]


def write(tmp_path, verdicts):
    p = tmp_path / "v.json"
    cg.write_verdicts(str(p), verdicts)
    return cg.GateConfig(verdict_file=str(p))


def test_slice_gate_fails_closed_on_missing_stale_corrupt_and_unknown(tmp_path):
    assert not cg.SliceGate.load(cg.GateConfig(verdict_file=str(tmp_path / "nope.json")), NOW).is_open("nfl", "rec_yds")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert not cg.SliceGate.load(cg.GateConfig(verdict_file=str(bad)), NOW).is_open("nfl", "rec_yds")
    open_row = {"status": "open", "open": True, "n": 300, "mean_clv_pct": 5.0, "ci_lo": 1.0}
    v = {"computed_at": NOW.isoformat(), "slices": {"nfl|rec_yds": open_row}}
    cfg = write(tmp_path, v)
    g = cg.SliceGate.load(cfg, NOW + timedelta(hours=1))
    assert g.is_open("nfl", "rec_yds") and not g.is_open("nfl", "pass_yds") and not g.is_open("nba", "pts")
    assert not cg.SliceGate.load(cfg, NOW + timedelta(hours=100)).is_open("nfl", "rec_yds")     # stale (> 72 h)
    assert cg.SliceGate.load(cfg, NOW + timedelta(hours=100)).status("nfl", "rec_yds")["status"] == "stale_verdict"
    # a hand-edited 'open': true without status == 'open' is not honoured
    v["slices"]["nfl|rec_yds"] = {"status": "insufficient_data", "open": True}
    assert not cg.SliceGate.load(write(tmp_path, v), NOW).is_open("nfl", "rec_yds")


def test_config_defaults_are_off_and_env_overridable():
    assert CFG.enabled is False and CFG.min_n == 250 and CFG.ci_level == 0.95 and CFG.require_consensus_confirm is True
    c = cg.load_gate_config({"NOVIG_GATE_ENABLED": "1", "NOVIG_GATE_MIN_N": "400", "NOVIG_GATE_CI_LEVEL": "0.99"})
    assert c.enabled and c.min_n == 400 and c.ci_level == 0.99
    assert cg.load_gate_config({}).enabled is False


def test_cli_compute_reads_the_ledger_readonly_and_reports_insufficient(tmp_path, capsys):
    L = seed_ledger(tmp_path)
    out = tmp_path / "out.json"
    assert cg.main(["compute", "--db", L.path, "--out", str(out)]) == 0
    v = json.loads(out.read_text())
    assert v["slices"]["nfl|rec_yds"]["status"] == "insufficient_data" and v["slices"]["nfl|pass_yds"]["status"] == "no_data"
    assert v["coverage"]["filled_bets"] == 1 and v["coverage"]["with_usable_close"] == 0
    assert all(not r["open"] for r in v["slices"].values())
    assert "rec_yds" in capsys.readouterr().out
