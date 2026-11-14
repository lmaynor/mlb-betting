"""NOV-03: the paper maker falsification module (Argon ruling 2026-11-12, Q2/Q3/Q4/Q5).
Quote construction, the waterfall, the mandatory cancel triggers, and the pre-registered study
bars -- all pure functions; the study ships disabled and this suite also pins that it stays so."""
from datetime import datetime, timedelta, timezone

from ev.novig import maker as mk
from ev.novig import maker_study as ms

UTC = timezone.utc
T0 = datetime(2026, 11, 14, 17, 0, tzinfo=UTC)


def cand(**over):
    base = dict(league="NHL", stat="sog", game_key="nhl:2026-11-14:DET@CHI", player_norm="patrick kane",
                line=3.5, is_alt=False, p_cons=0.50, p_model=0.52, n_books=6, disp=0.02,
                novig_bid_over=0.40, novig_ask_over=0.52, mins_to_start=120.0, snapshot_age_sec=60.0)
    base.update(over)
    return mk.MakerCandidate(**base)


# ---------------------------------------------------------------- anchor + delta + bid math
def test_blend_anchor_and_floor():
    assert mk.p_blend(0.50, 0.62, 0.0) == 0.50                      # c = 0: pure consensus anchor
    assert 0.50 < mk.p_blend(0.50, 0.62, 0.31) < 0.62               # c tilts toward the model, never past it
    assert mk.p_blend(0.50, 0.38, 0.31) < 0.50
    assert mk.fair_floor(0.50, 0.53) == 0.50 and mk.fair_floor(0.50, 0.47) == 0.47


def test_delta_components_and_hard_floor():
    sog = mk.STAT_CFG[("NHL", "sog")]
    assert mk.delta_offset(sog, 0.0, 120.0, False) == 0.030         # 0.025 base floored to the 3.0c hard floor
    assert abs(mk.delta_offset(sog, 0.06, 120.0, False) - 0.055) < 1e-9    # + 0.50 * disp
    assert abs(mk.delta_offset(sog, 0.0, 240.0, False) - 0.040) < 1e-9    # + 1.5c beyond 180m
    assert abs(mk.delta_offset(sog, 0.0, 30.0, False) - 0.035) < 1e-9     # + 1.0c inside 15-60m
    assert abs(mk.delta_offset(sog, 0.0, 120.0, True) - 0.040) < 1e-9     # + 1.5c news surcharge (over floor)
    saves = mk.STAT_CFG[("NHL", "saves")]
    assert abs(mk.delta_offset(saves, 0.0, 120.0, False) - 0.035) < 1e-9  # workload base 3.5c


def test_quote_bid_cap_stub_and_queue_position():
    assert mk.quote_bid(0.60, 0.045, 0.50) == 0.510                 # capped 1.0c over the existing bid
    assert mk.quote_bid(0.60, 0.045, 0.40) == 0.555                 # stub (>15c below fair): cap lifts
    assert mk.quote_bid(0.60, 0.045, None) == 0.555                 # no existing bid: raw floored price
    assert mk.quote_bid(0.52, 0.050, 0.50) is None                  # 0.47 would sit BEHIND the 0.50 bid
    assert mk.quote_contracts(0.45) == 11 and mk.quote_contracts(0.60) == 8   # ~$5 liability, mid prices


# ---------------------------------------------------------------- the waterfall
def test_decide_maker_quote_on_confluence():
    d = mk.decide(cand())
    assert d.mode == "MAKER_PAPER" and d.side == "yes"
    assert d.bid_price == 0.410 and d.contracts == 12               # fair 0.50, delta 0.035, cap bid+1c
    assert d.fair_floor == 0.50


def test_decide_taker_state_wins():
    d = mk.decide(cand(novig_ask_over=0.46))                        # fair 0.50 - ask 0.46 = 4c >= 2.5c
    assert d.mode == "TAKER" and d.side == "yes"


def test_decide_witness_on_tight_book():
    d = mk.decide(cand(novig_bid_over=0.47, novig_ask_over=0.52))   # spread 0.05 < 0.08
    assert d.mode == "WITNESS"


def test_decide_quotes_the_agreeing_side_only():
    # model leans UNDER (over blend < over consensus): the bid lands on the under side
    d = mk.decide(cand(p_cons=0.55, p_model=0.50, novig_bid_over=0.50, novig_ask_over=0.62))
    assert d.mode == "MAKER_PAPER" and d.side == "no" and d.bid_price == 0.390


def test_decide_abstain_guards():
    assert mk.decide(cand(p_model=0.70)).reasons == ("model_consensus_disagreement",)
    assert mk.decide(cand(n_books=3)).reasons == ("too_few_reference_books",)   # maker bar is 4, taker is 3
    assert mk.decide(cand(disp=0.07)).reasons == ("dispersion_too_wide",)
    assert mk.decide(cand(snapshot_age_sec=400.0)).reasons == ("reference_snapshot_stale",)
    assert mk.decide(cand(novig_bid_over=None)).reasons == ("novig_book_incomplete",)


def test_decide_never_list():
    assert mk.decide(cand(league="NBA", stat="points")).mode == "ABSTAIN"
    assert mk.decide(cand(league="NBA", stat="assists")).mode == "ABSTAIN"
    assert mk.decide(cand(league="NFL", stat="pass_yds")).mode == "ABSTAIN"
    assert mk.decide(cand(league="NHL", stat="goals")).mode == "ABSTAIN"
    assert mk.decide(cand(is_alt=True)).reasons == ("ladder_never_listed",)
    assert mk.NEVER_STATS == frozenset({("NBA", "assists"), ("NBA", "points"), ("NFL", "pass_yds")})


def test_decide_tier2_window_and_cleanliness():
    nba = dict(league="NBA", stat="rebounds", p_cons=0.50, p_model=0.53, novig_bid_over=0.38,
               novig_ask_over=0.52)
    assert mk.decide(cand(mins_to_start=30.0, **nba)).mode == "MAKER_PAPER"
    assert mk.decide(cand(mins_to_start=90.0, **nba)).reasons == ("outside_tier2_window",)
    assert mk.decide(cand(mins_to_start=30.0, game_unresolved_q=True, **nba)).reasons == \
        ("tier2_injury_reports_not_clean",)
    assert mk.decide(cand(mins_to_start=30.0, injury_reports_clean=False, **nba)).mode == "ABSTAIN"


def test_decide_blackouts():
    assert mk.decide(cand(league="NFL", stat="receptions", mins_to_start=90.0)).reasons == \
        ("nfl_inactives_window",)
    assert mk.decide(cand(stat="saves", goalie_confirmed=False)).reasons == \
        ("nhl_saves_goalie_unconfirmed",)
    assert mk.decide(cand(stat="saves", mins_to_start=25.0)).reasons == ("nhl_warmup_window",)
    assert mk.decide(cand(mins_to_start=3.0)).reasons == ("inside_t_minus_5",)
    assert mk.decide(cand(league="NBA", stat="threes", mins_to_start=90.0,
                          game_unresolved_q=True)).reasons == ("nba_unresolved_q_before_t60",)


# ---------------------------------------------------------------- cancel controls
def _quote(**over):
    base = dict(game_key="nhl:2026-11-14:DET@CHI", league="NHL", stat="sog", side="yes",
                bid_price=0.41, t_post=T0, commence=T0 + timedelta(hours=2),
                p_cons_side_at_post=0.50, sharp_at_post={"fanduel": 0.50, "betmgm": 0.49})
    base.update(over)
    return mk.RestingQuote(**base)


def _reasons(q, **over):
    kw = dict(now=T0 + timedelta(seconds=300), p_cons_side_now=0.50, sharp_now={"fanduel": 0.50},
              n_books_now=6, snapshot_age_sec_now=120.0)
    kw.update(over)
    return mk.cancel_reasons(q, **kw)


def test_cancel_quiet_quote_rests():
    assert _reasons(_quote()) == ()


def test_cancel_triggers():
    q = _quote()
    assert "t_minus_5_hard_cancel" in _reasons(q, now=q.commence - timedelta(seconds=240))
    assert "ttl_expired" in _reasons(q, now=T0 + timedelta(seconds=901))
    assert "reference_snapshot_stale" in _reasons(q, snapshot_age_sec_now=1000.0)
    assert "consensus_velocity_against" in _reasons(q, p_cons_side_now=0.48)
    assert "sharp_book_velocity" in _reasons(q, sharp_now={"fanduel": 0.535})
    assert "sharp_book_velocity" not in _reasons(q, sharp_now={"betmgm": 0.535})   # not a sharp book
    assert "reference_books_dropped" in _reasons(q, n_books_now=3)
    assert "game_status_change" in _reasons(q, status_changed_game_keys=frozenset({q.game_key}))


def test_lockout_helpers():
    until = mk.lockout_until(T0)
    assert until == T0 + timedelta(minutes=15)
    assert mk.requote_blocked(until, T0 + timedelta(minutes=5), True)
    assert mk.requote_blocked(until, T0 + timedelta(minutes=20), False)     # needs post-news books
    assert not mk.requote_blocked(until, T0 + timedelta(minutes=20), True)
    assert not mk.requote_blocked(None, T0, False)


# ---------------------------------------------------------------- study config + evaluation
def test_study_config_is_the_ruling_verbatim():
    assert ms.STUDY_ARMED is False                                  # ships disabled
    assert (ms.WINDOW_START.isoformat(), ms.WINDOW_END.isoformat()) == ("2026-11-14", "2026-11-28")
    assert ms.TARGET_FILLS == 150 and ms.EARLY_KILL_FILLS == 50 and ms.EARLY_KILL_DAY == 7
    assert ms.PROMOTION_BARS["pickoff_drift"] == {"mean_pp_max": 1.50, "upper_ci_pp_max": 2.00}
    assert ms.PROMOTION_BARS["closing_markout"] == {"mean_c_min": 1.50, "lower_ci_c_min": 0.25}
    assert ms.PROMOTION_BARS["markout_30m"] == {"mean_c_min": 1.00}
    assert ms.PROMOTION_BARS["news_window_share"] == {"max": 0.12}
    assert ms.PROMOTION_BARS["winners_curse_gap"] == {"pp_max": 1.25}
    assert ms.KILL_BARS["pickoff_drift"] == {"mean_pp_over": 2.00, "min_fills": 45}
    assert ms.KILL_BARS["markout_30m"] == {"mean_c_at_most": -0.50, "min_fills": 50}
    assert ms.KILL_BARS["news_window_share"] == {"over": 0.25, "min_fills": 45}
    assert ms.KILL_BARS["winners_curse_gap"] == {"pp_over": 2.50}
    assert "p_cons_close" in ms.QUOTE_LOG_FIELDS and "in_news_window" in ms.FILL_FIELDS


def _fill(day, i, drift_pp=0.5, close_c=4.5, m30_c=1.5, news=False):
    p_cons_post = 0.50 + (i % 5) * 0.001
    return {"game_day": day, "p_cons_at_post": p_cons_post, "p_cons_close": p_cons_post - drift_pp / 100.0,
            "p_fill": 0.45, "p_cons_fill_plus_5m": 0.46, "p_cons_fill_plus_30m": 0.45 + m30_c / 100.0,
            "in_news_window": news}


def _quote_row(filled, drift_pp):
    return {"filled": filled, "p_cons_at_post": 0.50, "p_cons_close": 0.50 - drift_pp / 100.0}


def test_study_promotion_pass_case():
    days = [f"2026-11-{d}" for d in range(14, 24)]                  # 10 game days
    fills = [_fill(day, i, news=(i < 1)) for day in days for i in range(15)]
    assert len(fills) == 150
    quotes = [_quote_row(True, 0.5) for _ in fills] + [_quote_row(False, 0.4) for _ in range(50)]
    m = ms.compute_metrics(quotes, fills)
    assert m["n_fills"] == 150 and m["n_game_days"] == 10 and abs(m["pickoff_drift_pp"] - 0.5) < 0.2
    v = ms.evaluate_study(m, days_elapsed=14)
    assert v["promote"] is True and v["killed"] is False, v


def test_study_kill_cases():
    fills = [_fill("2026-11-14", i, drift_pp=2.5) for i in range(50)]
    m = ms.compute_metrics([_quote_row(True, 2.5) for _ in fills], fills)
    v = ms.evaluate_study(m, days_elapsed=7)
    assert v["kill"]["pickoff_drift"] is True and v["killed"] is True
    thin = ms.compute_metrics([], [_fill("2026-11-14", i) for i in range(20)])
    v2 = ms.evaluate_study(thin, days_elapsed=14)
    assert v2["kill"]["volume"] is True                             # < 30 fills over the full window
    assert ms.evaluate_study(thin, days_elapsed=7)["kill"]["volume"] is False
