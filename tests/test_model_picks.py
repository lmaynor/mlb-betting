"""ev.model_picks: selection gate, idempotent ledger, closing-line capture, Discord routing, recap split."""
from datetime import datetime, timedelta, timezone

import pytest

from ev import model_picks as MP

NOW = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)


def row(**kw):
    r = {
        "sport": "nba", "stat": "rebounds", "player": "Jalen Duren", "player_norm": "id4594268", "team": "DET", "opp": "CHI",
        "game_key": "nba:2026-10-25:CHI@DET", "commence": (NOW + timedelta(hours=4)).isoformat(), "mins_to_start": 240.0,
        "has_line": True, "line": 10.5, "side": "over", "proj_mean": 11.8, "p_model_side": 0.58, "p_mkt_side": 0.53,
        "best_over_odds": 105, "best_over_book": "fanduel", "best_under_odds": -125, "best_under_book": "draftkings",
        "n_books": 6, "model_edge": 0.05, "market_ev": 0.0865, "confluence": True, "edge_capped": False,
        "beezy_score": 72, "engine": "empirical_ewm_v2", "validated": False,
        "p_blend_side": 0.545, "blend_ev": 0.07,
    }
    r.update(kw)
    return r


def test_confluence_row_with_real_price_is_a_pick():
    assert MP.qualifies(row()) in ("official", "tracked")


@pytest.mark.parametrize("kw", [
    {"confluence": False}, {"edge_capped": True}, {"has_line": False}, {"line": None}, {"side": None},
    {"mins_to_start": 12.0}, {"mins_to_start": None}, {"best_over_odds": None}, {"best_over_book": "pinnacle"},
    {"best_over_book": "prizepicks"}, {"best_over_book": "underdog"},
])
def test_rejections(kw):
    assert MP.qualifies(row(**kw)) is None


def test_official_tier_needs_the_cards_own_gate():
    assert MP.qualifies(row()) == "official"                       # ev 8.65%, model edge 5%, 6 books, score 72
    assert MP.qualifies(row(beezy_score=39)) == "tracked"          # below the card's score floor (40 since the 2026-11-09 calibration)
    assert MP.qualifies(row(n_books=3)) == "tracked"               # below the card's 4-book floor


def test_official_tier_also_needs_the_market_anchored_blend_to_clear_the_bar():
    assert MP.qualifies(row(blend_ev=MP.OFFICIAL_MIN_BLEND_EV)) == "official"
    assert MP.qualifies(row(blend_ev=0.024)) == "tracked"          # the card's gate passes, the blended EV does not (low-c stat)
    assert MP.qualifies(row(blend_ev=None)) == "tracked"           # a board with no blend cannot show the engine clears the bar
    r = row()
    r.pop("blend_ev")
    assert MP.qualifies(r) == "tracked"
    assert MP.qualifies(row(blend_ev=0.5, beezy_score=39)) == "tracked"      # the card's own gate still applies first


def test_under_side_uses_the_under_quote():
    p = MP.to_pick(row(side="under", best_under_odds=-110, best_under_book="betmgm"))
    assert (p["side"], p["book"], p["american"]) == ("under", "betmgm", -110)


def test_select_dedups_and_orders_official_first():
    a = row()
    b = row(player="B", player_norm="id2", beezy_score=50)
    c = row()                                                        # same row key as a
    ps = MP.select({"rows": [b, a, c]})
    assert [p["player"] for p in ps] == ["Jalen Duren", "B"] and ps[0]["tier"] == "official"


def test_stale_board_is_not_logged():
    fresh = {"ts": (NOW - timedelta(minutes=10)).isoformat(), "rows": []}
    stale = {"ts": (NOW - timedelta(minutes=300)).isoformat(), "rows": []}
    assert MP.payload_fresh(fresh, NOW) and not MP.payload_fresh(stale, NOW) and not MP.payload_fresh(None, NOW)


@pytest.fixture
def tracker(tmp_path):
    from mlb_core.tracking import BetTracker
    return BetTracker(str(tmp_path / "b.db"), system="PROPS", sport="nba")


def test_log_is_idempotent_and_first_sighting_wins(tracker):
    p = {**MP.to_pick(row()), "tier": "official"}
    assert len(MP.log_picks("nba", [p], tracker)) == 1
    later = {**p, "point": 11.5, "american": -105}                  # line moved: same prop, must not be re-logged
    assert MP.log_picks("nba", [later], tracker) == []
    import pandas as pd
    from sqlalchemy import text
    with tracker.engine.connect() as c:
        df = pd.read_sql(text("SELECT * FROM bets"), c)
    assert len(df) == 1
    b = df.iloc[0]
    assert (b["system"], b["sport"], b["bet_type"], int(b["odds"]), b["book"]) == ("PROPS", "nba", "rebounds_over_10.5", 105, "fanduel")
    assert b["game_date"] == "2026-10-25" and bool(b["paper"]) and float(b["stake"]) == MP.MAX_STAKE_UNITS and bool(b["kelly_triggered"])
    import json
    n = json.loads(b["notes"])
    assert n["player_id"] == "4594268" and n["tier"] == "official" and n["row_key"].endswith("|rebounds")


def test_notes_drive_the_winter_settler_parse(tracker):
    """The settler reads stat / side / point from notes (and falls back to bet_type.rsplit('_', 2)); both must agree."""
    p = {**MP.to_pick(row(stat="pass_yds", line=250.5, sport="nfl", player_norm="00-0033873")), "tier": "tracked"}
    assert MP.bet_type_of(p).rsplit("_", 2) == ["pass_yds", "over", "250.5"]


def test_closing_capture_same_line_only(tracker):
    p = {**MP.to_pick(row()), "tier": "official"}
    MP.log_picks("nba", [p], tracker)
    started = NOW + timedelta(hours=5)
    close = row(best_over_odds=-110, p_mkt_side=0.55)
    arch = {"rows": [close]}
    res = MP.capture_closing("nba", started, get_archive=lambda s, d: arch, tracker=tracker)
    assert res["updated"] == 1
    from sqlalchemy import text
    with tracker.engine.connect() as c:
        co, cp, clv = c.execute(text("SELECT closing_odds, closing_prob, clv_pct FROM bets")).fetchone()
    assert co == -110 and cp == pytest.approx(0.55) and clv > 0        # entry +105 beat a -110 close

    # a moved line is not a comparable close
    tr2_p = {**MP.to_pick(row(player="Other", player_norm="id9")), "tier": "tracked"}
    MP.log_picks("nba", [tr2_p], tracker)
    moved = {"rows": [row(player="Other", player_norm="id9", line=11.5)]}
    res2 = MP.capture_closing("nba", started, get_archive=lambda s, d: moved, tracker=tracker)
    assert res2["updated"] == 0


def test_closing_not_taken_before_the_game_starts(tracker):
    p = {**MP.to_pick(row()), "tier": "official"}
    MP.log_picks("nba", [p], tracker)
    res = MP.capture_closing("nba", NOW, get_archive=lambda s, d: {"rows": [row()]}, tracker=tracker)
    assert res["updated"] == 0 and res["skipped"] == 1


def test_discord_posts_official_only_and_never_logs_ev_rows(monkeypatch):
    seen = {}

    def fake_post(sport, rows, log_bets=True):
        seen.update(sport=sport, rows=rows, log_bets=log_bets)
        return True

    import mlb_core.alerts.sport_alerts as SA
    monkeypatch.setattr(SA, "post_sport_alerts", fake_post)
    off = {**MP.to_pick(row()), "tier": "official"}
    trk = {**MP.to_pick(row(player="B", player_norm="id2")), "tier": "tracked"}
    assert MP.post_official("nba", [trk, off]) is True
    assert seen["log_bets"] is False and [r["player"] for r in seen["rows"]] == ["Jalen Duren"]
    seen.clear()
    assert MP.post_official("nba", [trk]) is False and not seen


def test_embed_carries_the_model_side_probability():
    from mlb_core.alerts.sport_alerts import alert_embed
    e = alert_embed({**MP.to_pick(row()), "tier": "official"})
    assert "Model: 58.0%" in e["description"]


def test_recap_separates_model_picks_from_ev_alerts(monkeypatch):
    import mlb_core.notify.discord as D
    posted = {}
    monkeypatch.setattr(D, "webhook_for", lambda *a, **k: "http://x")
    monkeypatch.setattr(D, "_post", lambda url, body: posted.update(body=body))
    st = {"bets": 4, "wins": 3, "hit_rate": 0.75, "pnl": 1.5, "roi": 37.5, "avg_edge": 0.04, "pending": 0}
    D.post_all_systems_summary({}, settle_date="2026-10-25", sport_stats={"NBA": None}, model_stats={"NBA": st})
    names = [f["name"] for f in posted["body"]["embeds"][0]["fields"]]
    assert any("NBA model picks (unvalidated)" in n for n in names) and any("NBA props (unvalidated)" in n for n in names)
    assert not any("NFL model picks" in n for n in names)


def test_monitor_ops_flags_a_stale_model_pick_job_and_ignores_a_missing_heartbeat(monkeypatch):
    import json
    import mlb.runners.monitor_ops as MO
    import mlb_core.storage as st
    monkeypatch.setattr(MO, "_gcs_age_hours", lambda key: None)
    assert MO._check_model_picks(month=11) == []                      # not provisioned yet: silent
    monkeypatch.setattr(MO, "_gcs_age_hours", lambda key: 5.0)
    assert "stale" in MO._check_model_picks(month=11)[0]
    monkeypatch.setattr(MO, "_gcs_age_hours", lambda key: 0.4)
    hb = {"sports": {"nba": {"status": "stale_or_missing_board"}, "nfl": {"status": "ok"}, "mlb": {"status": "x"}}}
    monkeypatch.setattr(st, "exists", lambda k: True)
    monkeypatch.setattr(st, "read_bytes", lambda k: json.dumps(hb).encode())
    out = MO._check_model_picks(month=11)
    assert len(out) == 1 and out[0].startswith("NBA model picks")
    assert MO._check_model_picks(month=7) == []                       # NBA out of season in July


def test_heartbeat_merges_per_sport(monkeypatch):
    import json
    import mlb_core.storage as st
    store = {}
    monkeypatch.setattr(st, "exists", lambda k: k in store)
    monkeypatch.setattr(st, "read_bytes", lambda k: store[k])
    monkeypatch.setattr(st, "write_bytes", lambda data, k: store.__setitem__(k, data))
    MP._heartbeat("nba", {"status": "ok", "selected": 3, "logged": 2}, NOW)
    MP._heartbeat("nhl", {"status": "stale_or_missing_board"}, NOW)
    hb = json.loads(store[MP.HEARTBEAT_KEY])
    assert set(hb["sports"]) == {"nba", "nhl"} and hb["sports"]["nba"]["logged"] == 2


def _ledger(tracker):
    import pandas as pd
    from sqlalchemy import text
    with tracker.engine.connect() as c:
        return pd.read_sql(text("SELECT * FROM bets ORDER BY id"), c)


def test_every_pick_is_sized_by_kelly_on_the_blend_and_a_no_edge_pick_is_a_zero_stake_monitoring_row(tracker):
    off = {**MP.to_pick(row()), "tier": "official"}
    trk = {**MP.to_pick(row(player="B", player_norm="id2", p_blend_side=0.50)), "tier": "tracked"}       # +105: breakeven 0.4878
    none = {**MP.to_pick(row(player="C", player_norm="id3", p_blend_side=0.45)), "tier": "tracked"}      # blend below breakeven
    assert len(MP.log_picks("nba", [off, trk, none], tracker)) == 3
    df = _ledger(tracker).set_index("player")
    assert (float(df.loc["Jalen Duren", "stake"]), bool(df.loc["Jalen Duren", "kelly_triggered"])) == (MP.MAX_STAKE_UNITS, True)       # capped
    edge, b = 0.50 - 100 / 205, 1.05
    want = round(edge * (b + 1) / b * MP.KELLY_FRACTION * MP.BANKROLL_UNITS, 2)
    assert 0 < want < MP.MAX_STAKE_UNITS
    assert float(df.loc["B", "stake"]) == pytest.approx(want) and bool(df.loc["B", "kelly_triggered"])      # a TRACKED pick with edge is staked
    assert float(df.loc["B", "kelly_pct"]) == pytest.approx(want / MP.BANKROLL_UNITS, abs=1e-4)
    assert (float(df.loc["C", "stake"]), bool(df.loc["C", "kelly_triggered"])) == (0.0, False)
    import json
    n = json.loads(df.loc["B", "notes"])
    assert n["tier"] == "tracked" and n["blend_ev"] == pytest.approx(0.07) and n["p_blend"] == pytest.approx(0.50)


def test_a_pick_with_no_blend_fails_closed_to_zero_stake():
    assert MP.ledger_stake({**MP.to_pick(row(p_blend_side=None)), "tier": "official"}) == (0.0, False)


def test_closing_lines_are_captured_on_official_and_tracked_rows(tracker):
    off = {**MP.to_pick(row()), "tier": "official"}
    trk = {**MP.to_pick(row(player="B", player_norm="id2")), "tier": "tracked"}
    MP.log_picks("nba", [off, trk], tracker)
    started = NOW + timedelta(hours=5)
    arch = {"rows": [row(best_over_odds=-110, p_mkt_side=0.55), row(player="B", player_norm="id2", best_over_odds=-115, p_mkt_side=0.56)]}
    res = MP.capture_closing("nba", started, get_archive=lambda s, d: arch, tracker=tracker)
    assert res["updated"] == 2
    df = _ledger(tracker).set_index("player")
    assert df.loc["Jalen Duren", "closing_odds"] == -110 and df.loc["B", "closing_odds"] == -115
    assert df.loc["B", "clv_pct"] > 0 and bool(df.loc["B", "kelly_triggered"]) is True        # CLV on a Kelly-staked tracked row


def test_settlement_grades_zero_stake_rows_on_a_one_unit_reference_and_the_recap_counts_only_staked_rows(tracker, monkeypatch):
    """A no-edge (zero-stake) row settles (profit on 1u) but never reaches the recap headline; the Kelly-staked row alone does."""
    from sqlalchemy import text
    from mlb.runners import settle_bets as SB
    off = {**MP.to_pick(row()), "tier": "official"}
    trk = {**MP.to_pick(row(player="B", player_norm="id2", p_blend_side=0.45)), "tier": "tracked"}
    MP.log_picks("nba", [off, trk], tracker)
    df = _ledger(tracker)
    assert SB._calc_profit(float(df.loc[df["player"] == "B", "stake"].iloc[0]) or 1.0, 105, "win") == pytest.approx(1.05)
    with tracker.engine.begin() as c:
        c.execute(text("UPDATE bets SET result = 'win', profit = stake * 1.05 WHERE stake > 0"))
        c.execute(text("UPDATE bets SET result = 'win', profit = 1.05 WHERE stake = 0"))
    monkeypatch.setattr(SB, "_winter_season_start", lambda d: "2026-08-01")
    stats = SB._winter_sport_stats(tracker.engine, "2026-10-26", system="PROPS")
    assert stats["NBA"]["bets"] == 1 and stats["NBA"]["roi"] == pytest.approx(105.0) and stats["NBA"]["pending"] == 0


def test_fix_tracked_stakes_demotes_only_pre_split_tracked_rows_and_is_idempotent(tracker):
    import json
    from sqlalchemy import text
    off = {**MP.to_pick(row()), "tier": "official"}
    trk = {**MP.to_pick(row(player="B", player_norm="id2")), "tier": "tracked"}
    MP.log_picks("nba", [off, trk], tracker)
    with tracker.engine.begin() as c:                     # re-create the pre-split state: the tracked row was staked
        c.execute(text("UPDATE bets SET kelly_triggered = :t, stake = 1.0"), {"t": True})
        c.execute(text("UPDATE bets SET notes = 'not json' WHERE player = 'Jalen Duren'"))
        c.execute(text("UPDATE bets SET notes = :n WHERE player = 'Jalen Duren'"), {"n": json.dumps({"tier": "official"})})
    dry = MP.fix_tracked_stakes(tracker, dry_run=True)
    assert dry == {"candidates": 1, "updated": 0, "unparsed": 0}
    assert bool(_ledger(tracker).set_index("player").loc["B", "kelly_triggered"]) is True       # a dry run changes nothing
    assert MP.fix_tracked_stakes(tracker) == {"candidates": 1, "updated": 1, "unparsed": 0}
    df = _ledger(tracker).set_index("player")
    assert (float(df.loc["B", "stake"]), bool(df.loc["B", "kelly_triggered"])) == (0.0, False)
    assert (float(df.loc["Jalen Duren", "stake"]), bool(df.loc["Jalen Duren", "kelly_triggered"])) == (1.0, True)
    assert MP.fix_tracked_stakes(tracker)["candidates"] == 0


def test_rederive_kelly_resizes_old_rows_from_their_notes_and_is_idempotent(tracker):
    from sqlalchemy import text
    a = {**MP.to_pick(row()), "tier": "official"}
    b = {**MP.to_pick(row(player="B", player_norm="id2", p_blend_side=0.50)), "tier": "tracked"}
    MP.log_picks("nba", [a, b], tracker)
    with tracker.engine.begin() as c:                     # the pre-Kelly state: official flat 1u, tracked 0u, both graded winners on 1u
        c.execute(text("UPDATE bets SET stake = 1.0, kelly_triggered = :t, kelly_pct = NULL, result = 'win', profit = 1.05 WHERE player = 'Jalen Duren'"), {"t": True})
        c.execute(text("UPDATE bets SET stake = 0, kelly_triggered = :f, kelly_pct = NULL, result = 'win', profit = 1.05 WHERE player = 'B'"), {"f": False})
    assert MP.rederive_kelly(tracker, dry_run=True)["updated"] == 2
    assert float(_ledger(tracker).set_index("player").loc["B", "stake"]) == 0.0                 # a dry run changes nothing
    assert MP.rederive_kelly(tracker) == {"rows": 2, "updated": 2, "staked": 2, "no_blend": 0, "unparsed": 0}
    df = _ledger(tracker).set_index("player")
    assert float(df.loc["Jalen Duren", "stake"]) == MP.MAX_STAKE_UNITS and float(df.loc["Jalen Duren", "profit"]) == pytest.approx(2.10)
    sb = float(df.loc["B", "stake"])
    assert sb > 0 and bool(df.loc["B", "kelly_triggered"]) and float(df.loc["B", "profit"]) == pytest.approx(round(1.05 * sb, 2))
    first = _ledger(tracker)[["stake", "profit"]].astype(float).values.tolist()
    MP.rederive_kelly(tracker)
    assert _ledger(tracker)[["stake", "profit"]].astype(float).values.tolist() == first


def test_cli_needs_a_sport_unless_fixing_stakes(monkeypatch):
    with pytest.raises(SystemExit):
        MP.main([])
    monkeypatch.setattr(MP, "fix_tracked_stakes", lambda dry_run=False: {"candidates": 0, "updated": 0, "unparsed": 0})
    assert MP.main(["--fix-tracked-stakes", "--dry-run"]) == 0


def test_board_rows_are_stamped_with_the_one_definition_of_a_pick():
    # the site's Daily Card reads `pick_tier` instead of re-deriving the gates, so the stamp must be exactly qualifies()
    from ev import daily_projections as DP
    for kw in ({}, {"blend_ev": 0.01}, {"confluence": False}, {"best_over_book": "pinnacle"}, {"stat": "points"}):
        assert DP.pick_tier(row(**kw)) == MP.qualifies(row(**kw))
    assert DP.pick_tier(row()) == "official"
    assert DP.pick_tier(row(confluence=False)) is None
