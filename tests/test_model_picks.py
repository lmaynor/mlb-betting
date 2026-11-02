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
    assert MP.qualifies(row(beezy_score=50)) == "tracked"          # below the card's score floor
    assert MP.qualifies(row(n_books=3)) == "tracked"               # below the card's 4-book floor


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
    assert b["game_date"] == "2026-10-25" and bool(b["paper"]) and float(b["stake"]) == 1.0
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
