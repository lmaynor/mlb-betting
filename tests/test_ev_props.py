"""ev.props / ev.kalshi_props / ev.sweep_nfl_props. Fixtures are REAL payloads captured 2026-10-04."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from ev import sweep_nfl_props as sw
from ev.kalshi_props import PropLeg, map_prop_market
from ev.props import build_consensus, norm_name, parse_event_props, split_player, stat_of
from ev.props_config import props_config
from mlb.execution.kalshi.mapping import MapFail

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "ev")
EVENT = json.load(open(os.path.join(FIX, "nfl_props_event.json")))
KMK = json.load(open(os.path.join(FIX, "kalshi_prop_markets.json")))


# ---------------- names / keys ----------------
def test_norm_name_and_team_suffix():
    assert norm_name("Ja'Marr Chase Jr.") == "jamarr chase"
    assert norm_name("Jacory Croskey-Merritt") == norm_name("Jacory Croskey Merritt") == "jacory croskey merritt"
    assert norm_name("Amon-Ra St. Brown") == "amon ra st brown"
    assert split_player("Jonathan Taylor (IND)") == ("Jonathan Taylor", "IND")
    assert split_player("Josh Downs") == ("Josh Downs", None)


def test_stat_aliases_cover_every_key_seen_live():
    seen = {"player_pass_tds": "pass_tds", "player_passing_touchdowns": "pass_tds", "player_pass_yds": "pass_yds",
            "player_passing_yards": "pass_yds", "player_rec_yds": "rec_yds", "player_receiving_yards": "rec_yds",
            "player_receptions": "receptions", "player_rush_yards": "rush_yds", "player_rush_yds": "rush_yds",
            "player_rushing_yards": "rush_yds", "player_anytime_touchdown_scorer": "anytime_td"}
    for k, v in seen.items():
        assert stat_of(k) == (v, False)
    assert stat_of("player_rush_yds_alternate") == ("rush_yds", True)
    assert stat_of("player_something_new") == (None, False)


# ---------------- real payload ----------------
def test_real_payload_parses_with_no_unmapped_keys_and_excludes_dfs():
    quotes, meta = parse_event_props(EVENT)
    assert quotes and meta["unmapped_keys"] == {}
    cons = build_consensus(quotes)
    # Daniel Jones rush yds 11.5 comes from caesars (two-sided); sleeper/underdog (DFS) must not count
    c = cons[("daniel jones", "rush_yds", 11.5)]
    assert c.books == ("caesars",) and c.n_books == 1
    assert 0.45 < c.p_over < 0.5 and c.n_dfs == 0
    # a bovada one-sided 'Yes' ladder is counted as one-sided, never as a price
    assert all(v.n_books >= 1 for v in cons.values())
    # team suffix from bovada is captured
    assert ("IND",) == cons[("jonathan taylor", "rec_yds", 19.5)].team_hints


def _two_book_event():
    def book(key, over, under):
        return {"key": key, "markets": [{"key": "player_rec_yds", "outcomes": [
            {"name": "Over Tyler Warren", "description": "Tyler Warren", "price": over, "point": 49.5},
            {"name": "Under Tyler Warren", "description": "Tyler Warren", "price": under, "point": 49.5}]}]}
    return {"bookmakers": [book("draftkings", -110, -110), book("fanduel", -120, 100),
                           book("betmgm", -105, -115), book("underdog", -112, -112)]}


def test_consensus_median_of_devigged_two_sided_real_books():
    quotes, _ = parse_event_props(_two_book_event())
    c = build_consensus(quotes)[("tyler warren", "rec_yds", 49.5)]
    assert c.n_books == 3 and c.n_dfs == 1 and set(c.books) == {"draftkings", "fanduel", "betmgm"}
    assert c.p_over == pytest.approx(0.5, abs=0.03)
    assert c.disp < 0.03


def test_one_sided_book_not_used():
    ev = _two_book_event()
    ev["bookmakers"].append({"key": "bovada", "markets": [{"key": "player_rec_yds", "outcomes": [
        {"name": "Yes", "description": "Tyler Warren (IND)", "price": -120, "point": 49.5}]}]})
    c = build_consensus(parse_event_props(ev)[0])[("tyler warren", "rec_yds", 49.5)]
    assert "bovada" not in c.books and c.n_onesided == 1 and c.team_hints == ("IND",)


# ---------------- kalshi prop mapper (real market payloads) ----------------
@pytest.mark.parametrize("series", list(KMK))
def test_real_kalshi_prop_markets_map(series):
    leg = map_prop_market(KMK[series])
    assert isinstance(leg, PropLeg), leg
    assert leg.strike == pytest.approx(leg.n - 0.5) and leg.player_norm == norm_name(leg.player)
    assert leg.game_key.startswith("nfl:2026-10-") and leg.team in (leg.away, leg.home)
    assert leg.market == f"nfl_{leg.stat}:{leg.player_norm}" and leg.game_pk == leg.game_key and leg.line == leg.strike


def test_prop_mapper_fails_closed():
    base = KMK["KXNFLRECYDS"]
    for patch in (dict(title=base["title"].replace("receiving yards", "rushing yards")),
                  dict(floor_strike=base["floor_strike"] + 1),
                  dict(strike_type="less"),
                  dict(rules_secondary="no void clause"),
                  dict(yes_sub_title="Someone Else: 50+"),
                  dict(rules_primary=base["rules_primary"].replace("Oct 4", "Oct 5")),
                  dict(event_ticker="KXNFLRECYDS-26OCT04INDXXX")):
        r = map_prop_market(dict(base, **patch))
        assert isinstance(r, MapFail), patch
    assert isinstance(map_prop_market(dict(base, event_ticker="KXNFLGAME-26OCT04INDWAS")), MapFail)


# ---------------- sweep: join + classify ----------------
def _leg_and_market(ask_yes=0.50, ask_no=0.52, bid_yes=0.49):
    mk = dict(KMK["KXNFLRECYDS"], yes_ask_dollars=str(ask_yes), no_ask_dollars=str(ask_no),
              yes_bid_dollars=str(bid_yes), yes_ask_size_fp="100", open_interest_fp="500", volume_24h_fp="50")
    leg = map_prop_market(mk)
    return leg, mk


def _parlay_events(leg, books=("draftkings", "fanduel", "betmgm"), price=(-110, -110)):
    away_name = {v: k for k, v in __import__("ev.sports.nfl", fromlist=["x"]).TEAM_NAMES.items()}[leg.away]
    home_name = {v: k for k, v in __import__("ev.sports.nfl", fromlist=["x"]).TEAM_NAMES.items()}[leg.home]
    bks = [{"key": b, "markets": [{"key": "player_reception_yds", "outcomes": [
        {"name": f"Over {leg.player}", "description": leg.player, "price": price[0], "point": leg.strike},
        {"name": f"Under {leg.player}", "description": leg.player, "price": price[1], "point": leg.strike}]}]}
        for b in books]
    return [{"event": {"id": "e1", "away_team": away_name, "home_team": home_name,
                       "commence_time": "2026-10-04T17:00:00Z"}, "props": {"bookmakers": bks}}]


NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def test_rows_join_flag_and_net_of_fee_ev():
    cfg = props_config(env={})
    leg, mk = _leg_and_market(ask_yes=0.50, bid_yes=0.49)        # book fair ~0.50, ask 0.50 -> no edge
    rows, st = sw.compute_rows(_parlay_events(leg), {leg.game_key: [(leg, mk)]}, NOW, cfg, 1.0)
    assert len(rows) == 1 and st["matched"] == 1 and rows[0]["flag"] is False
    # cheap YES vs 3 books at ~0.50 fair: ask 0.43 -> large net edge but must be capped (ev_cap rejects, never clamps)
    leg, mk = _leg_and_market(ask_yes=0.43, bid_yes=0.42)
    rows, _ = sw.compute_rows(_parlay_events(leg), {leg.game_key: [(leg, mk)]}, NOW, cfg, 1.0)
    assert rows[0]["reason"] == "EV_TOO_HIGH" and rows[0]["ev_yes"] > cfg.ev_cap
    # moderate: ask 0.46 -> within [ev_min, ev_cap] and net of the fee
    leg, mk = _leg_and_market(ask_yes=0.46, bid_yes=0.45)
    r = sw.compute_rows(_parlay_events(leg), {leg.game_key: [(leg, mk)]}, NOW, cfg, 1.0)[0][0]
    fee_pc = 0.07 * 0.46 * 0.54
    assert r["flag"] and r["side"] == "yes"
    assert r["ev_yes"] == pytest.approx((r["p_over"] - cfg.h0) / (0.46 + fee_pc) - 1, abs=2e-3)


def test_thin_consensus_is_not_flagged_but_still_logged():
    cfg = props_config(env={})
    leg, mk = _leg_and_market(ask_yes=0.46, bid_yes=0.45)
    rows, _ = sw.compute_rows(_parlay_events(leg, books=("draftkings",)), {leg.game_key: [(leg, mk)]}, NOW, cfg, 1.0)
    assert rows[0]["reason"] == "THIN_CONS" and rows[0]["n_books"] == 1 and rows[0]["flag"] is False


def test_near_kick_wide_spread_and_unmatched_player():
    cfg = props_config(env={})
    leg, mk = _leg_and_market(ask_yes=0.46, bid_yes=0.45)
    late = datetime(2026, 10, 4, 16, 50, tzinfo=timezone.utc)                       # 10 min before kick
    assert sw.compute_rows(_parlay_events(leg), {leg.game_key: [(leg, mk)]}, late, cfg, 1.0)[0][0]["reason"] == "NEAR_KICK"
    leg2, mk2 = _leg_and_market(ask_yes=0.46, bid_yes=0.30)
    assert sw.compute_rows(_parlay_events(leg2), {leg2.game_key: [(leg2, mk2)]}, NOW, cfg, 1.0)[0][0]["reason"] == "WIDE_SPREAD"
    pe = _parlay_events(leg)
    pe[0]["props"]["bookmakers"][0]["markets"][0]["outcomes"][0]["description"] = "Different Guy"
    rows, st = sw.compute_rows(pe, {leg.game_key: [(leg, mk)]}, NOW, cfg, 1.0)
    assert st["matched"] == 1                                                         # other 2 books still match


def test_team_hint_mismatch_is_dropped():
    cfg = props_config(env={})
    leg, mk = _leg_and_market()
    pe = _parlay_events(leg)
    wrong = leg.away if leg.team == leg.home else leg.home
    for b in pe[0]["props"]["bookmakers"]:
        for o in b["markets"][0]["outcomes"]:
            o["description"] = f"{leg.player} ({wrong})"
    rows, st = sw.compute_rows(pe, {leg.game_key: [(leg, mk)]}, NOW, cfg, 1.0)
    assert rows == [] and st["team_mismatch"] == 1


def test_event_game_maps_names_and_et_date():
    gk, start = sw.event_game({"away_team": "Indianapolis Colts", "home_team": "Washington Commanders",
                               "commence_time": "2026-10-04T13:30:00Z"})
    assert gk == "nfl:2026-10-04:IND@WAS" and start.hour == 13
    assert sw.event_game({"away_team": "Nope FC", "home_team": "Washington Commanders",
                          "commence_time": "2026-10-04T13:30:00Z"}) is None


def test_team_name_table_matches_kalshi_code_universe():
    from ev.sports.nfl import CODES, TEAM_NAMES
    assert set(TEAM_NAMES.values()) == set(CODES) and len(TEAM_NAMES) == 32


def test_alert_embed_and_webhook_never_uses_member_channel(monkeypatch):
    cfg = props_config(env={})
    leg, mk = _leg_and_market(ask_yes=0.46, bid_yes=0.45)
    r = sw.compute_rows(_parlay_events(leg), {leg.game_key: [(leg, mk)]}, NOW, cfg, 1.0)[0][0]
    emb = sw.alert_embed([r])
    assert "SHADOW" in emb[0]["title"] and "BUY YES" in emb[0]["fields"][0]["value"]
    for k in ("DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_OPS", "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.invalid/picks")      # member channel: must be ignored
    assert sw.webhook_url() == ""
    monkeypatch.setenv("DISCORD_WEBHOOK_OPS", "https://example.invalid/ops")
    assert sw.webhook_url().endswith("/ops")


def _fake_pc(events):
    class PC:
        credits_remaining = "19000"
        def get_slate(self, sport, markets=None):
            return [e["event"] for e in events]
        def get_event_props(self, sport, eid, markets):
            return next(e["props"] for e in events if e["event"]["id"] == eid)
    return PC()


def test_blocked_kalshi_still_publishes_the_book_consensus(monkeypatch):
    """Cloud Run egress IPs are blocked by Kalshi's CDN: the cloud job must still publish consensus.json so a
    machine that can reach Kalshi does the join."""
    leg, mk = _leg_and_market(ask_yes=0.46, bid_yes=0.45)
    events = _parlay_events(leg)
    events[0]["event"]["commence_time"] = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    import dataclasses
    gk, _ = sw.event_game(events[0]["event"])                    # keep the leg's game on the (relative) event date
    leg = dataclasses.replace(leg, game_key=gk)

    class DeadKalshi:
        def get_series(self, s):
            raise RuntimeError("HTTP 403 could not be satisfied")
        def get_markets(self, s):
            raise RuntimeError("HTTP 403")

    written = {}
    monkeypatch.setattr(sw, "_write", lambda data, key: written.__setitem__(key, data))
    monkeypatch.setattr(sw, "_read", lambda key: None)
    out = sw.run_cycle(_fake_pc(events), DeadKalshi(), props_config(env={}), 0, 40.0, {})
    assert out["kalshi_reachable"] is False and out["rows"] == 0 and out["events_pulled"] == 1
    snap = json.loads(written["EVSweep/nfl_props/consensus.json"])
    assert snap["events"][0]["points"] and snap["events"][0]["points"][0]["n_books"] == 3
    # the published snapshot, joined later, equals the direct join
    cfg = props_config(env={})
    rows, _ = sw.rows_from_snapshot(snap["events"], {gk: [(leg, mk)]}, datetime.now(timezone.utc), cfg, 1.0)
    assert len(rows) == 1 and rows[0]["n_books"] == 3


def test_post_new_dedups_and_persists_and_pending_alerts_flow(monkeypatch):
    cfg = props_config(env={})
    leg, mk = _leg_and_market(ask_yes=0.46, bid_yes=0.45)
    r = sw.compute_rows(_parlay_events(leg), {leg.game_key: [(leg, mk)]}, NOW, cfg, 1.0)[0][0]
    posts, store = [], {}
    monkeypatch.setattr(sw, "post_discord", lambda text, embeds=None: posts.append(text) or True)
    monkeypatch.setattr(sw, "_write", lambda data, key: store.__setitem__(key, data))
    monkeypatch.setattr(sw, "_read", lambda key: store.get(key))
    state = {}
    assert sw.post_new([r], state, "2026-10-04") == 1 and len(posts) == 1
    assert sw.post_new([r], state, "2026-10-04") == 0 and len(posts) == 1          # deduped
    assert f"{r['ticker']}|{r['side']}" in json.loads(store["EVSweep/nfl_props/2026-10-04/notified.json"])
    # a restarted task (empty in-memory state) still does not re-alert
    assert sw.post_new([r], {}, "2026-10-04") == 0
    # pending alerts written by the local executor are posted from here and the file is cleared
    r2 = dict(r, ticker=r["ticker"] + "X")
    store["EVSweep/nfl_props/alerts_pending.json"] = json.dumps([r2]).encode()
    day = datetime.now(timezone.utc).astimezone(sw._ET).date().isoformat()
    assert sw.process_pending_alerts({}) == 1 and len(posts) == 2
    assert store["EVSweep/nfl_props/alerts_pending.json"] == b"[]"


# ---------------- Sink A: book outliers vs leave-self-out consensus ----------------
from ev.props import (Quote, book_points, pooled_consensus, scan_book_outliers,  # noqa: E402
                      shift_p_over)


def _q(book, over, under, point=64.5, stat="rec_yds", player="Tyler Warren"):
    return Quote(book, stat, False, player, norm_name(player), None, point, over, under)


def _pack(extra=(), n=5, price=(-110, -110)):
    return [_q(f"b{i}", *price) for i in range(n)] + list(extra)


def test_outlier_flags_a_cheap_side_vs_leave_self_out_consensus():
    out = scan_book_outliers(_pack([_q("softbook", +110, -135)]))
    assert [(r["book"], r["side"]) for r in out] == [("softbook", "OVER")]
    r = out[0]
    assert r["n_books"] == 5 and r["p_fair"] == pytest.approx(0.5, abs=1e-3)
    assert r["ev"] == pytest.approx(0.5 * 2.10 - 1, abs=2e-3)                 # +110 on a ~50% fair prop = +5%
    # the pack itself (-110/-110, 50%) is not an outlier
    assert not [x for x in out if x["book"].startswith("b")]


def test_outlier_leaves_self_out_and_needs_enough_independent_books():
    # a lone book cannot validate itself: 3 others < min_books=4 -> nothing, even though its price is +EV vs the pack
    assert scan_book_outliers(_pack([_q("softbook", +110, -135)], n=3)) == []
    assert len(scan_book_outliers(_pack([_q("softbook", +110, -135)], n=3), min_books=3)) == 1


def test_outlier_excludes_dfs_dispersed_and_unpriced_sides():
    assert scan_book_outliers(_pack([_q("underdog", +150, -200)])) == []                      # DFS never a candidate
    disp = [_q("a", -110, -110), _q("b", -250, +200), _q("c", +200, -250), _q("d", -110, -110), _q("e", -250, +200),
            _q("soft", +300, -400)]
    assert scan_book_outliers(disp) == []                                                     # dispersed reference
    one_sided = _pack([_q("bovada", +130, None)])                                             # 'Yes' ladder: Over only
    assert [(r["book"], r["side"]) for r in scan_book_outliers(one_sided)] == [("bovada", "OVER")]


def test_book_outlier_rows_use_pulled_payload_no_extra_credits():
    leg, _ = _leg_and_market()
    events = _parlay_events(leg, books=("draftkings", "fanduel", "betmgm", "caesars", "bet365"))
    events[0]["props"]["bookmakers"].append({"key": "softbook", "markets": [{"key": "player_reception_yds", "outcomes": [
        {"name": f"Over {leg.player}", "description": leg.player, "price": 115, "point": leg.strike},
        {"name": f"Under {leg.player}", "description": leg.player, "price": -140, "point": leg.strike}]}]})
    rows = sw.book_outlier_rows(events, NOW)
    assert [(r["book"], r["side"], r["game_key"]) for r in rows] == [("softbook", "OVER", sw.event_game(events[0]["event"])[0])]


# ---------------- pooled adjacent half-point consensus (evidence only) ----------------
def test_shift_p_over_is_monotone_and_identity_at_same_point():
    assert shift_p_over(0.5, 64.5, 64.5, 35.0) == 0.5
    assert shift_p_over(0.5, 63.5, 64.5, 35.0) < 0.5 < shift_p_over(0.5, 65.5, 64.5, 35.0)
    assert shift_p_over(0.5, 63.5, 64.5, 35.0) == pytest.approx(0.5 - 0.0113, abs=1e-3)       # ~pdf(0)/sd per yard


def test_pooling_recovers_books_split_across_adjacent_half_points():
    qs = [_q("a", -110, -110, 63.5), _q("b", -110, -110, 63.5), _q("c", -110, -110, 64.5),
          _q("d", -110, -110, 64.5), _q("e", -110, -110, 65.5)]
    exact = build_consensus(qs)[("tyler warren", "rec_yds", 64.5)]
    assert exact.n_books == 2                                                      # fails n_min_books=3
    pc = pooled_consensus(book_points(qs)[("tyler warren", "rec_yds")], "rec_yds", 64.5)
    assert pc["n_books"] == 5 and pc["n_exact"] == 2 and pc["interpolated"] is True
    assert 0.49 < pc["p_over"] < 0.51 and pc["books"] == ["a", "b", "c", "d", "e"]


def test_pooling_respects_gap_discrete_stats_and_nearest_quote_per_book():
    ents = [("a", 62.5, 0.5), ("b", 64.5, 0.5)]
    pc = pooled_consensus(ents, "rec_yds", 64.5)
    assert pc["books"] == ["b"] and pc["interpolated"] is False                    # 2-yard gap is outside the band
    assert pooled_consensus([("a", 4.5, 0.5), ("b", 5.5, 0.5)], "receptions", 5.5) is None   # discrete: never pooled
    assert pooled_consensus([("a", 64.5, 0.5)], "rec_yds", 64.5, min_books=2) is None
    two_pts = pooled_consensus([("a", 63.5, 0.5), ("a", 64.5, 0.5)], "rec_yds", 64.5)
    assert two_pts["n_books"] == 1 and two_pts["n_exact"] == 1                      # a book counts once, nearest quote


def test_pooled_rows_log_exact_vs_pooled_without_touching_flags():
    cfg = props_config(env={})
    leg, mk = _leg_and_market(ask_yes=0.46, bid_yes=0.45)
    away = {v: k for k, v in __import__("ev.sports.nfl", fromlist=["x"]).TEAM_NAMES.items()}
    def bk(key, pt):
        return {"key": key, "markets": [{"key": "player_reception_yds", "outcomes": [
            {"name": f"Over {leg.player}", "description": leg.player, "price": -110, "point": pt},
            {"name": f"Under {leg.player}", "description": leg.player, "price": -110, "point": pt}]}]}
    ev = {"event": {"id": "e1", "away_team": away[leg.away], "home_team": away[leg.home],
                    "commence_time": "2026-10-04T17:00:00Z"},
          "props": {"bookmakers": [bk("a", leg.strike - 1), bk("b", leg.strike - 1), bk("c", leg.strike),
                                   bk("d", leg.strike + 1)]}}
    snap, _ = sw.consensus_snapshot([ev])
    legs = {leg.game_key: [(leg, mk)]}
    rows, st = sw.rows_from_snapshot(snap, legs, NOW, cfg, 1.0)
    assert len(rows) == 1 and rows[0]["n_books"] == 1 and rows[0]["flag"] is False       # exact path unchanged: thin
    prow, pst = sw.pooled_rows_from_snapshot(snap, legs, NOW, cfg, 1.0)
    assert len(prow) == 1 and pst == {"pool_legs": 1, "pool_ge_min": 1, "pool_recovered": 1}
    r = prow[0]
    assert r["exact_n_books"] == 1 and r["pool_n_books"] == 4 and r["interpolated"] is True and r["pool_n_exact"] == 1
    assert r["pool_p_over"] == pytest.approx(0.5, abs=0.01) and r["pool_ev_yes"] is not None
    assert "flag" not in r                                                                # evidence only


def test_run_cycle_writes_sink_a_and_pooled_evidence_and_counts_them(monkeypatch):
    import dataclasses
    leg, mk = _leg_and_market(ask_yes=0.50, bid_yes=0.49)
    events = _parlay_events(leg, books=("draftkings", "fanduel", "betmgm", "caesars", "bet365"))
    events[0]["event"]["commence_time"] = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    events[0]["props"]["bookmakers"].append({"key": "softbook", "markets": [{"key": "player_reception_yds", "outcomes": [
        {"name": f"Over {leg.player}", "description": leg.player, "price": 115, "point": leg.strike},
        {"name": f"Under {leg.player}", "description": leg.player, "price": -140, "point": leg.strike}]}]})
    gk, _ = sw.event_game(events[0]["event"])
    leg = dataclasses.replace(leg, game_key=gk)
    mk = dict(mk)
    monkeypatch.setattr(sw, "map_prop_market", lambda m: leg)

    class LiveKalshi:
        def get_series(self, s):
            return {"series": {"fee_multiplier": 1.0}}
        def get_markets(self, s):
            return [mk] if s == next(iter(sw.PROP_SERIES)) else []

    written = {}
    monkeypatch.setattr(sw, "_write", lambda data, key: written.__setitem__(key, data))
    monkeypatch.setattr(sw, "_read", lambda key: None)
    monkeypatch.setattr(sw, "post_discord", lambda *a, **k: False)
    out = sw.run_cycle(_fake_pc(events), LiveKalshi(), props_config(env={}), 0, 40.0, {})
    assert out["book_outliers"] == 1 and out["book_outlier_books"] == ["softbook"]
    assert out["pool_legs"] == 1 and out["kalshi_reachable"] is True
    keys = list(written)
    assert any("/book_outliers_" in k for k in keys) and any("/pooled_" in k for k in keys)
