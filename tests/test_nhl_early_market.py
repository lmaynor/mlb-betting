"""nhl.game.early_market (early-market shadow logger) + the ARI historical alias in nhl.config.

Covers: the pre-registered judgment constants, the section 4.2 gate arithmetic on fixture snapshots,
last-snapshot-before-drop close selection with close_age_min, settle math (same-book CLV, line-moved
exclusion, missing-target tally, pushes), the judgment aggregation (plain + clustered t, checkpoint vs
binding verdicts), temperature-scaling math (T=1 identity, monotone shrinkage, a known-T recovery fit),
and the ARI alias contract (ARI resolves and prices; the current-32 TEAMS table is standings-pinned and
unchanged, UTA stays the current franchise code).
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from nhl import config as C
from nhl.game import early_market as em

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
DROP = datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)          # 12h after NOW, 19:00 ET Nov 11


# --------------------------------------------------------------------------------------------------- judgment constants
def test_judgment_constants_are_the_preregistered_ones():
    assert em.CHECKPOINT_DATE == "2027-01-15"                     # checkpoint ONLY, not a binding date
    assert em.CHECKPOINT_KILL_MEAN_CLV == 0.0                     # early kill iff mean CLV <= 0 there
    assert em.JUDGMENT_N == 200                                   # binding judgment at n=200
    assert em.JUDGMENT_MEAN_CLV_MIN == 0.015                      # mean CLV >= +1.5%
    assert em.JUDGMENT_T_MIN == 2.5                               # t >= 2.5
    assert em.JUDGMENT_ROI_MIN == 0.010                           # ROI at opens > +1.0%
    assert em.EV_GATE == 0.025 and em.MIN_BOOKS_EARLY == 4
    assert (em.WINDOW_MIN_H, em.WINDOW_MAX_H) == (6.0, 36.0)


# --------------------------------------------------------------------------------------------------- ARI alias
def test_teams_table_stays_the_current_32_standings_pinned():
    assert len(C.TEAMS) == 32 and len({t[0] for t in C.TEAMS}) == 32
    assert "UTA" in {t[0] for t in C.TEAMS} and "ARI" not in {t[0] for t in C.TEAMS}
    from ev.sports.nhl import CODES
    assert set(C.KALSHI_OF.values()) == set(CODES)                # Kalshi vocabulary untouched by the alias
    from nhl.data.teams import build_rows
    assert len(build_rows()) == 32                                # teams master still the current 32


def test_ari_is_a_historical_alias_outside_teams():
    assert [t[0] for t in C.HISTORICAL_TEAMS] == ["ARI"]
    assert len(C.ALL_TEAMS) == 33 and C.ALL_TEAMS[:32] == C.TEAMS
    assert C.team_abbrev("ARI") == "ARI"
    assert C.team_abbrev("Arizona Coyotes") == "ARI"


@pytest.mark.parametrize("raw,abbr", [
    ("Montreal Canadiens", "MTL"), ("LA", "LAK"), ("NJ", "NJD"), ("TB", "TBL"), ("SJ", "SJS"),
    ("Utah Mammoth", "UTA"), ("UTA", "UTA"), ("WSH", "WSH")])
def test_current_team_resolution_unchanged(raw, abbr):
    assert C.team_abbrev(raw) == abbr


def test_kalman_seeds_ari_at_the_prior_and_prices_coyotes_games():
    from nhl.game.kalman_ratings import KalmanTeamTracker
    k = KalmanTeamTracker()
    assert len(k.teams) == 33 and "ARI" in k.teams and "UTA" in k.teams
    ari = k.teams["ARI"]
    assert ari.games_played == 0 and np.allclose(ari.theta, 0.0)  # seeded at the league prior
    pred = k.predict_game("ARI", "EDM", "2023-11-01")             # used to raise `Unknown team code`
    assert pred.home_team == "ARI" and pred.exp_total > 0
    uta_before = k.teams["UTA"].theta.copy()
    k.update_game(home="ARI", away="EDM", home_goals=3, away_goals=2, game_date="2023-11-01")
    assert k.teams["ARI"].games_played == 1
    assert np.allclose(k.teams["UTA"].theta, uta_before)          # UTA starts fresh: no ARI continuity


def test_market_eval_replay_no_longer_skips_ari():
    """The replay's unknown-team skip was the ARI hole: a one-game team-box frame pairing ARI with EDM
    must now produce a priced row instead of a skip (totals are what the alias restores end to end)."""
    from nhl.game.market_eval import replay
    rows = []
    for team, opp, home, goals, ga in (("ARI", "EDM", True, 3, 2), ("EDM", "ARI", False, 2, 3)):
        rows.append({"game_id": "1", "team_abbr": team, "opp_abbr": opp, "is_home": home, "date": "2023-11-01",
                     "goals": goals, "goals_against": ga, "sog": 30, "shots_against": 28, "minutes": 60.0})
    team_box = pd.DataFrame(rows)
    goalies = pd.DataFrame(columns=["game_id", "team_abbr", "date", "gsax"])
    pred = replay(team_box, goalies, {"1": {"spread": -1.5, "total": 6.5}})
    assert len(pred) == 1 and pred.iloc[0]["home"] == "ARI"
    assert pred.attrs["skipped"]["unknown_team"] == 0


# --------------------------------------------------------------------------------------------------- temperature scaling
@pytest.mark.parametrize("p", [0.05, 0.25, 0.5, 0.7, 0.95])
def test_temperature_one_is_identity(p):
    assert em.calibrate_prob(p, 1.0) == pytest.approx(p)


def test_temperature_above_one_shrinks_toward_a_half_monotonely():
    assert 0.5 < em.calibrate_prob(0.9, 2.0) < 0.9
    assert 0.1 < em.calibrate_prob(0.1, 2.0) < 0.5
    assert em.calibrate_prob(0.5, 3.0) == pytest.approx(0.5)
    ps = [0.1, 0.3, 0.5, 0.7, 0.9]                                # ordering preserved for any T > 0
    cal = [em.calibrate_prob(p, 2.5) for p in ps]
    assert cal == sorted(cal)
    assert em.calibrate_prob(0.9, 0.5) > 0.9                      # T < 1 sharpens
    with pytest.raises(ValueError):
        em.calibrate_prob(0.5, 0.0)


def test_fit_temperature_recovers_a_known_temperature():
    rng = np.random.default_rng(7)
    p_model = rng.uniform(0.15, 0.85, size=4000)
    p_true = np.array([em.calibrate_prob(p, 2.0) for p in p_model])   # the world is T=2-calibrated
    y = (rng.uniform(size=4000) < p_true).astype(float)
    t = em.fit_temperature(p_model, y)
    assert 1.6 < t < 2.5
    assert em.log_loss([em.calibrate_prob(p, t) for p in p_model], y) < em.log_loss(p_model, y)


def test_fit_temperatures_artifact_reports_validation_ece():
    rng = np.random.default_rng(11)
    frames = {}
    for market in em.MARKETS:
        rows = []
        for season, n in ((2021, 800), (2022, 800), (2023, 800), (2024, 800), (2025, 800)):
            p_model = rng.uniform(0.1, 0.9, size=n)
            p_true = np.array([em.calibrate_prob(p, 2.5) for p in p_model])
            rows.append(pd.DataFrame({"season": season, "p_model": p_model,
                                      "y": (rng.uniform(size=n) < p_true).astype(int)}))
        frames[market] = pd.concat(rows)
    art = em.fit_temperatures(frames)
    for market in em.MARKETS:
        m = art["markets"][market]
        assert m["T"] > 1.5 and m["n_dev"] == 2400 and m["n_val"] == 1600
        assert m["val_ece_cal"] < m["val_ece_raw"]                # overconfident raw probs calibrate down
        assert m["val_ll_cal"] < m["val_ll_raw"]


# --------------------------------------------------------------------------------------------------- snapshot fixtures
def _book(key, home_price, away_price, market="h2h", point=None):
    outs = [{"name": "Edmonton Oilers", "price": home_price}, {"name": "Calgary Flames", "price": away_price}]
    if market == "totals":
        outs = [{"name": "Over", "price": home_price, "point": point}, {"name": "Under", "price": away_price, "point": point}]
    elif point is not None:
        outs[0]["point"], outs[1]["point"] = point, -point
    return {"key": key, "markets": [{"key": market, "outcomes": outs}]}


def _event(books, commence=DROP):
    return {"id": "evt1", "commence_time": commence.isoformat(), "home_team": "Edmonton Oilers",
            "away_team": "Calgary Flames", "bookmakers": books}


def _ml_event(target_home=120):
    books = [_book(f"book{i}", -110, -110) for i in range(5)]
    books.append(_book("softbook", target_home, -110))
    return _event(books)


def _payload(p_home=0.55, commence=DROP):
    return {"engine_version": "test", "ts": NOW.isoformat(),
            "games": [{"game_key": "nhl:2026-11-11:CGY@EDM", "home": "EDM", "away": "CGY",
                       "commence_time": commence.isoformat(), "home_goalie": "Skinner", "away_goalie": "Wolf",
                       "home_goalie_confirmed": True, "away_goalie_confirmed": False}],
            "rows": [{"game_key": "nhl:2026-11-11:CGY@EDM", "system": "NHL_ML", "side": "home", "p_model": p_home},
                     {"game_key": "nhl:2026-11-11:CGY@EDM", "system": "NHL_ML", "side": "away", "p_model": 1 - p_home}]}


# --------------------------------------------------------------------------------------------------- the gate
def test_gate_logs_the_outlier_when_the_calibrated_lean_agrees():
    picks = em.build_picks(_payload(), [_ml_event()], "snap/key.json", NOW, NOW)
    assert len(picks) == 1
    p = picks[0]
    assert p["side"] == "home" and p["target_book"] == "softbook" and p["market"] == "moneyline"
    assert p["target_dec_open"] == pytest.approx(2.2) and p["target_odds_open"] == 120
    assert p["p_fair_early"] == pytest.approx(0.5) and p["n_books_early"] == 5
    assert p["ev_consensus"] == pytest.approx(0.10) and p["hours_to_drop"] == pytest.approx(12.0)
    assert p["pick_id"] == "nhl-early:nhl:2026-11-11:CGY@EDM:NHL_ML:home"
    assert p["goalie_home_confirmed"] is True and p["goalie_away_confirmed"] is False


def test_gate_rejects_when_the_model_lean_disagrees():
    assert em.build_picks(_payload(p_home=0.45), [_ml_event()], "k", NOW, NOW) == []


def test_gate_rejects_a_sub_threshold_outlier():
    assert em.build_picks(_payload(), [_ml_event(target_home=102)], "k", NOW, NOW) == []


def test_gate_rejects_outside_the_window_and_needs_four_consensus_books():
    soon = _payload(commence=NOW + timedelta(hours=3))            # inside 6h: the early window has closed
    assert em.build_picks(soon, [_event([_book(f"b{i}", -110, -110) for i in range(5)] + [_book("s", 120, -110)],
                                        commence=NOW + timedelta(hours=3))], "k", NOW, NOW) == []
    thin = _event([_book(f"b{i}", -110, -110) for i in range(3)] + [_book("s", 120, -110)])
    assert em.build_picks(_payload(), [thin], "k", NOW, NOW) == []   # consensus excl. target would be 3 < 4


def test_gate_never_targets_consensus_or_dfs_books():
    books = [_book(f"book{i}", -110, -110) for i in range(5)]
    books.append(_book("prizepicks", 150, -110))
    books.append(_book("consensus", 150, -110))
    assert em.build_picks(_payload(), [_event(books)], "k", NOW, NOW) == []


# --------------------------------------------------------------------------------------------------- close selection
def test_snapshot_time_from_key_parses_game_lines_only():
    ts = em.snapshot_time_from_key("OddsAccum/icehockey_nhl/raw/2026-11-11/game_lines_1735.json")
    assert (ts.hour, ts.minute) == (17, 35) and str(ts.tzinfo) == "America/New_York"
    assert em.snapshot_time_from_key("OddsAccum/icehockey_nhl/raw/2026-11-11/props_1735.json") is None


def test_select_closing_snapshot_is_last_strictly_before_drop_with_age():
    cands = [("k1", DROP - timedelta(hours=6)), ("k2", DROP - timedelta(minutes=85)),
             ("k3", DROP + timedelta(minutes=5)), ("k4", DROP)]
    key, ts, age = em.select_closing_snapshot(cands, DROP)
    assert key == "k2" and age == pytest.approx(85.0)              # at-drop and post-drop snapshots never count
    assert em.select_closing_snapshot([("k3", DROP + timedelta(minutes=5))], DROP) is None


def test_closing_snapshot_game_lines_scans_prior_et_dates():
    keys = ["OddsAccum/icehockey_nhl/raw/2026-11-10/game_lines_2235.json",
            "OddsAccum/icehockey_nhl/raw/2026-11-11/game_lines_1235.json",
            "OddsAccum/icehockey_nhl/raw/2026-11-11/game_lines_1735.json"]
    out = em.closing_snapshot_game_lines(DROP, list_keys=lambda p: keys, read_bytes=lambda k: b"[]")
    assert out is not None and out[0].endswith("game_lines_1735.json") and out[2] == pytest.approx(85.0)


# --------------------------------------------------------------------------------------------------- settle
def _pick(**kw):
    base = {"pick_id": "nhl-early:g:NHL_ML:home", "game_key": "g", "game_date_et": "2026-11-11",
            "commence_utc": DROP.isoformat(), "home": "EDM", "away": "CGY", "market": "moneyline",
            "system": "NHL_ML", "side": "home", "line": None, "target_book": "softbook",
            "target_dec_open": 2.2, "dry_run": False}
    base.update(kw)
    return base


_RESULT = {"home_goals": 4, "away_goals": 2, "home_goals_ex_so": 4, "away_goals_ex_so": 2}


def _close(books, age=85.0):
    return ("OddsAccum/icehockey_nhl/raw/2026-11-11/game_lines_1735.json", DROP - timedelta(minutes=age), age,
            [_event(books, commence=DROP)])


def test_settle_computes_same_book_clv_and_roi_at_open():
    books = [_book(f"book{i}", -120, 100) for i in range(4)] + [_book("softbook", 100, -120)]
    s = em.settle_pick(_pick(), _close(books), _RESULT, NOW)
    assert s["y"] == 1 and s["roi_open"] == pytest.approx(1.2)    # won at the logged 2.20
    assert s["target_dec_close"] == pytest.approx(2.0)
    assert s["clv_same_book"] == pytest.approx(0.10)              # 2.20 / 2.00 - 1
    assert s["close_age_min"] == pytest.approx(85.0) and s["clv_missing_reason"] is None


def test_settle_missing_target_book_is_tallied_not_dropped():
    books = [_book(f"book{i}", -120, 100) for i in range(5)]      # softbook pulled the market
    s = em.settle_pick(_pick(), _close(books), _RESULT, NOW)
    assert s["clv_same_book"] is None and s["clv_missing_reason"] == "no_target_close"
    assert s["roi_open"] == pytest.approx(1.2)                    # still settled for ROI


def test_settle_line_moved_spread_is_excluded_from_clv_kept_for_roi():
    pick = _pick(market="spread", system="NHL_SPREAD", line=-1.5, pick_id="nhl-early:g:NHL_SPREAD:home")
    books = [_book(f"book{i}", -110, -110, market="spreads", point=-2.5) for i in range(4)]
    books.append(_book("softbook", -110, -110, market="spreads", point=-2.5))
    s = em.settle_pick(pick, _close(books), _RESULT, NOW)
    assert s["line_close"] == -2.5 and s["clv_same_book"] is None
    assert s["clv_missing_reason"] == "line_moved" and s["y"] == 1 and s["roi_open"] == pytest.approx(1.2)


def test_settle_total_push_is_excluded_from_both_metrics():
    pick = _pick(market="total", system="NHL_TOTAL", side="over", line=6.0, pick_id="nhl-early:g:NHL_TOTAL:over")
    books = [_book(f"book{i}", -110, -110, market="totals", point=6.0) for i in range(5)]
    s = em.settle_pick(pick, _close(books), _RESULT, NOW)         # 4 + 2 ex-SO lands exactly on 6.0
    assert s["y"] is None and s["roi_open"] is None


def test_settle_without_close_snapshot_still_settles_roi():
    s = em.settle_pick(_pick(), None, _RESULT, NOW)
    assert s["clv_missing_reason"] == "no_close_snapshot" and s["roi_open"] == pytest.approx(1.2)


# --------------------------------------------------------------------------------------------------- aggregation / judgment
def _settled(n, clv, roi, start_day=1, market="moneyline"):
    rows = []
    for i in range(n):
        c = clv[i % len(clv)] if isinstance(clv, (list, tuple)) else clv
        rows.append({"market": market, "y": 1, "clv_same_book": c, "roi_open": roi,
                     "clv_missing_reason": None if c is not None else "no_target_close",
                     "game_date_et": f"2026-11-{start_day + i:02d}", "close_age_min": 100.0, "dry_run": False})
    return rows


def test_aggregation_math_plain_and_clustered():
    rows = _settled(4, [0.02, 0.04, 0.0, 0.02], 0.05)
    agg = em.aggregate_judgment(rows)
    assert agg["n_settled"] == 4 and agg["n_clv"] == 4 and agg["clv_missing_share"] == 0.0
    assert agg["mean_clv"] == pytest.approx(0.02) and agg["mean_roi"] == pytest.approx(0.05)
    sd = np.std([0.02, 0.04, 0.0, 0.02], ddof=1)
    assert agg["t_clv_plain"] == pytest.approx(0.02 / (sd / 2))
    assert agg["t_clv_clustered"] is not None
    assert agg["binding_verdict"] == "PENDING_N" and agg["checkpoint_verdict"] == "CHECKPOINT_PENDING"
    assert agg["per_market"]["moneyline"]["mean_clv"] == pytest.approx(0.02)


def test_aggregation_counts_missing_and_pushes_and_skips_dry_run():
    rows = _settled(3, [0.02], 0.05) + _settled(1, [None], 0.05) + [
        {"market": "total", "y": None, "clv_same_book": None, "roi_open": None, "clv_missing_reason": None,
         "game_date_et": "2026-11-09", "close_age_min": 90.0, "dry_run": False},
        {"market": "total", "y": 1, "clv_same_book": 0.5, "roi_open": 0.5, "clv_missing_reason": None,
         "game_date_et": "2026-11-09", "close_age_min": 90.0, "dry_run": True}]
    agg = em.aggregate_judgment(rows)
    assert agg["n_settled"] == 4 and agg["n_pushes"] == 1 and agg["n_clv"] == 3
    assert agg["clv_missing_share"] == pytest.approx(0.25)
    assert agg["mean_clv"] == pytest.approx(0.02)                 # the DRY_RUN 0.50 never enters the pool


def test_binding_promote_at_n200_when_all_legs_clear():
    rows = []
    for i in range(200):                                          # distinct dates -> clustered t is real
        d = (datetime(2026, 9, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        rows.append({"market": "moneyline", "y": 1, "clv_same_book": 0.01 if i % 2 else 0.03,
                     "roi_open": 0.02, "clv_missing_reason": None, "game_date_et": d,
                     "close_age_min": 100.0, "dry_run": False})
    agg = em.aggregate_judgment(rows)
    assert agg["mean_clv"] == pytest.approx(0.02) and agg["t_clv_clustered"] >= 2.5
    assert agg["binding_verdict"] == "PROMOTE"


def test_binding_kill_at_n200_when_clv_fails():
    rows = _settled(200, [0.0, -0.01], -0.02, start_day=1)
    for i, r in enumerate(rows):                                  # spread across dates for a real clustered t
        r["game_date_et"] = (datetime(2026, 9, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
    agg = em.aggregate_judgment(rows)
    assert agg["n_settled"] == 200 and agg["binding_verdict"] == "KILL_NHL_GAME_ENGINE"


def test_checkpoint_kills_early_only_on_nonpositive_clv_with_enough_n():
    rows = _settled(25, [-0.01, -0.02], 0.0)
    agg = em.aggregate_judgment(rows, through="2027-01-15")
    assert agg["checkpoint_verdict"] == "CHECKPOINT_KILL"
    agg2 = em.aggregate_judgment(_settled(25, [0.01, 0.02], 0.0), through="2027-01-15")
    assert agg2["checkpoint_verdict"] == "CHECKPOINT_PASS_EXTEND_TO_N200"
    agg3 = em.aggregate_judgment(_settled(5, [-0.05], 0.0), through="2027-01-15")
    assert agg3["checkpoint_verdict"] == "CHECKPOINT_UNDERPOWERED_EXTEND"   # n_clv < 20 never kills


def test_report_header_states_the_judgment_constants():
    text = em.format_judgment_report(em.aggregate_judgment(_settled(2, [0.02], 0.01)))
    assert "2027-01-15" in text and "CHECKPOINT ONLY" in text
    assert "+1.5%" in text and "2.5" in text and "+1.0%" in text and "n=200" in text
