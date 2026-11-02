"""Offline tests for the hierarchical NFL prop engine (nfl.models.hierarchical_props / hierarchical_live), the context
layers (nfl.features.context_gaps), the stadium weather module and the engine_eval hierarchical comparison.

Everything is synthetic: no network, no GCS, no mirrored data."""
import math
import types
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from scipy.stats import gamma, nbinom, norm

from nfl.data import weather as W
from nfl.features import context_gaps as cg
from nfl.models import hierarchical_live as HL
from nfl.models import hierarchical_props as hp

G = hp.GameContext


# ---------------------------------------------------------------------------------------------------- script weights
def test_script_weights_are_a_distribution_and_follow_the_spread():
    for sp in (-14, -7.5, 0, 3, 7.5, 14, None):
        w = hp.script_weights(sp)
        assert w.trail >= 0 and w.lead >= 0 and w.neutral >= 0
        assert w.trail + w.neutral + w.lead == pytest.approx(1.0)
    assert hp.script_weights(7.5).trail > hp.script_weights(-7.5).trail          # underdog trails more
    assert hp.script_weights(-7.5).lead > hp.script_weights(7.5).lead            # favourite leads more
    assert hp.script_weights(-3.5 + 7.0).trail == pytest.approx(float(norm.cdf((3.5 - 3.5) / 13.5)))   # spec formula


def test_average_spread_reproduces_the_trailing_mean():
    """The shifts are centred on the league-average shift, so an unknown spread leaves the trailing mean unchanged."""
    qb = hp.PlayerInputs(pass_att_ewm=35.0)
    assert hp.price("pass_attempts", qb, G()).mean == pytest.approx(35.0, abs=0.05)
    rb = hp.PlayerInputs(carries_ewm=15.0, rush_share=0.6)
    assert hp.price("rush_attempts", rb, G()).mean == pytest.approx(15.0, abs=0.05)


def test_spread_convention_conversion():
    assert hp.spread_from_favoured_positive(7.0) == -7.0


# -------------------------------------------------------------------------------------- 1. cross-prop coherence laws
def _qb():
    return hp.PlayerInputs(pos="QB", pass_att_ewm=35.0, comp_rate=0.65, ypc_ewm=11.2, pass_td_ewm=1.6, int_rate=0.025)


def test_completions_never_exceed_attempts():
    f = hp.price_pass_funnel(_qb(), G(team_spread=-3.0, implied_team_total=24.0))
    att, comp = f["pass_attempts"], f["pass_completions"]
    assert comp.mean < att.mean
    for x in range(1, 50):
        assert comp.sf(x + 0.5) < att.sf(x + 0.5) or att.sf(x + 0.5) < 1e-9
        assert comp.sf(x + 0.5) <= att.sf(x + 0.5) + 1e-12
    # P(C > A) == 0: a completion count above the largest attempt count with mass has no mass
    top_att = int(np.nonzero(att.pmf > 1e-13)[0].max())
    assert comp.pmf[top_att + 1:].sum() < 1e-12


def test_underdog_script_raises_attempts_and_interceptions_and_lowers_rb_carries():
    fav, dog = G(team_spread=-7.5, implied_team_total=24.0), G(team_spread=+7.5, implied_team_total=24.0)
    qb = _qb()
    assert hp.price("pass_attempts", qb, dog).mean > hp.price("pass_attempts", qb, fav).mean
    assert hp.price("interceptions", qb, dog).mean > hp.price("interceptions", qb, fav).mean
    assert hp.price("interceptions", qb, dog).sf(0.5) > hp.price("interceptions", qb, fav).sf(0.5)
    rb = hp.PlayerInputs(pos="RB", carries_ewm=15.0, rush_share=0.6, ypc_rush_ewm=4.4)
    assert hp.price("rush_attempts", rb, dog).mean < hp.price("rush_attempts", rb, fav).mean
    assert hp.price("pass_yds", qb, dog).mean > hp.price("pass_yds", qb, fav).mean       # the funnel is coherent end to end


def test_wind_cuts_completion_rate_and_dome_ignores_it():
    qb = _qb()
    calm, windy = G(wind_mph=5.0), G(wind_mph=25.0)
    assert hp.completion_rate(qb, windy) < hp.completion_rate(qb, calm)
    assert hp.completion_rate(qb, G(wind_mph=25.0, dome=True)) == pytest.approx(hp.completion_rate(qb, calm))
    assert hp.price("pass_yds", qb, windy).mean < hp.price("pass_yds", qb, calm).mean


def test_completion_rate_is_clipped():
    assert hp.completion_rate(hp.PlayerInputs(comp_rate=0.99), G()) == hp.PRIORS["comp_hi"]
    assert hp.completion_rate(hp.PlayerInputs(comp_rate=0.10), G()) == hp.PRIORS["comp_lo"]


# ----------------------------------------------------------------------------------- 2. hurdle-gamma vs normal tails
def _receiver(rec_target, adot, ypr_ewm, yac, catch, mean_yds=55.0):
    """A receiver whose receiving-yards MEAN is exactly mean_yds (the yards mean is linear in target share)."""
    pi = hp.PlayerInputs(pos="WR", team_pass_att=35.0, adot=adot, catch_rate=catch, ypr_ewm=ypr_ewm, yac_per_rec_ewm=yac,
                         target_share=rec_target / catch / 35.0)
    base = hp.price("rec_yds", pi, G()).mean
    pi.target_share *= mean_yds / base
    return pi


def test_deep_threat_has_the_fatter_tail_at_equal_mean():
    deep = _receiver(3.6, 14.5, 15.3, 3.5, 0.65)
    slot = _receiver(6.1, 6.2, 8.45, 5.0, 0.72)
    d, s = hp.price("rec_yds", deep, G()), hp.price("rec_yds", slot, G())
    assert d.mean == pytest.approx(55.0, rel=1e-6) and s.mean == pytest.approx(55.0, rel=1e-6)
    assert hp.price("receptions", deep, G()).mean < hp.price("receptions", slot, G()).mean
    assert d.sf(89.5) > s.sf(89.5)
    assert d.sf(119.5) > s.sf(119.5)                                  # and further out the gap widens in ratio
    assert d.sf(119.5) / s.sf(119.5) > d.sf(89.5) / s.sf(89.5)
    assert d.var > s.var


def test_hurdle_gamma_tail_is_heavier_than_a_normal_with_the_same_moments():
    deep = hp.price("rec_yds", _receiver(3.6, 14.5, 15.3, 3.5, 0.65), G())
    sd = math.sqrt(deep.var)
    for line in (150.5, 175.5, 200.5):
        assert deep.sf(line) > float(norm.sf(line, deep.mean, sd))
    # a Normal puts mass below zero; the hurdle-gamma has an exact atom at 0 equal to P(0 receptions)
    assert float(norm.cdf(0, deep.mean, sd)) > 0.1
    assert deep.cdf(0.0)[()] == pytest.approx(deep.p0 + (1 - deep.p0) * gamma.cdf(0.5, deep.g_shape, scale=deep.g_scale))
    rec = hp.price("receptions", _receiver(3.6, 14.5, 15.3, 3.5, 0.65), G())
    assert deep.p0 == pytest.approx(rec.pmf[0])                        # P(Y = 0) == P(Rec = 0), exactly


def test_compound_gamma_matches_a_monte_carlo_of_the_generative_process():
    rng = np.random.default_rng(7)
    pmf = np.zeros(hp.N_MAX)
    k = np.arange(hp.N_MAX)
    pmf[:] = nbinom.pmf(k, 1 / 0.3, (1 / 0.3) / (1 / 0.3 + 4.0))
    d = hp.compound_gamma(pmf / pmf.sum(), 12.0, 10.0)
    n = rng.negative_binomial(1 / 0.3, (1 / 0.3) / (1 / 0.3 + 4.0), 200000)
    shape, scale = (12.0 / 10.0) ** 2, 10.0 ** 2 / 12.0
    y = np.array([rng.gamma(shape, scale, size=m).sum() if m else 0.0 for m in n[:60000]])
    assert d.mean == pytest.approx(y.mean(), rel=0.03)
    assert d.var == pytest.approx(y.var(), rel=0.08)
    for line in (20.5, 50.5, 90.5):
        assert d.sf(line) == pytest.approx(float((y > line).mean()), abs=0.03)
    assert d.p0 == pytest.approx(float((y == 0).mean()), abs=0.01)


def test_rush_yards_are_hurdle_gamma_with_a_breakaway_tail():
    rb = hp.PlayerInputs(pos="RB", carries_ewm=15.0, rush_share=0.6, ypc_rush_ewm=4.4)
    f = hp.price_rush_funnel(rb, G())
    y = f["rush_yds"]
    assert y.mean == pytest.approx(f["rush_attempts"].mean * 4.4, rel=0.01)
    # same mean and variance as a Normal, but right-skewed: lighter just above the mean, clearly heavier from ~1.5 sd out
    for line in (140.5, 160.5, 200.5):
        assert y.sf(line) > float(norm.sf(line, y.mean, math.sqrt(y.var)))
    assert hp.price_rush_funnel(rb, G(n_ol_out=2.0))["rush_yds"].mean < y.mean      # OL injuries cost yards per carry
    assert hp.price_rush_funnel(rb, G(def_stuff_factor=1.2))["rush_yds"].mean < y.mean


def test_coverage_shell_moves_targets_by_role():
    slot = hp.PlayerInputs(pos="WR", team_pass_att=35.0, target_share=0.2, adot=6.0)
    deep = hp.PlayerInputs(pos="WR", team_pass_att=35.0, target_share=0.2, adot=14.0)
    two_high = G(def_two_high_rate=0.53, def_single_high_rate=0.40, def_man_rate=0.14)
    single_man = G(def_two_high_rate=0.23, def_single_high_rate=0.70, def_man_rate=0.32)
    t = lambda pi, g: hp.price("receptions", pi, g).meta["targets"]                 # noqa: E731
    assert t(slot, two_high) == pytest.approx(t(slot, G()) * 1.06)
    assert t(deep, single_man) > t(deep, G()) and t(deep, single_man) <= t(deep, G()) * 1.0701
    assert t(slot, single_man) < t(slot, G()) < t(slot, two_high)
    assert t(deep, two_high) < t(deep, G())
    assert hp.shell_multiplier("mid", two_high) == 1.0


# ----------------------------------------------------------------- 3. teammate redistribution and team-TD anchoring
def _team_rows():
    """One team: QB, WR1 (28% of attempts as targets), WR2, WR3, TE, RB1, RB2; 8 games, season 2025."""
    spec = {  # name: (pos, targets, carries, rec_td_rate)
        "qb": ("QB", 0, 3, 0.0), "wr1": ("WR", 9.8, 0, 0.5), "wr2": ("WR", 6.0, 0, 0.3), "wr3": ("WR", 4.0, 0, 0.2),
        "te1": ("TE", 5.5, 0, 0.3), "rb1": ("RB", 3.0, 14, 0.5), "rb2": ("RB", 1.5, 8, 0.2)}
    table = {}
    for nm, (pos, tgt, car, td) in spec.items():
        rows = []
        for wk in range(1, 9):
            rec = 0.65 * tgt
            rows.append({"player_id": nm, "player_display_name": nm.upper(), "team": "AAA", "recent_team": "AAA",
                         "season": 2025, "week": wk, "position": pos, "targets": tgt, "receptions": rec,
                         "receiving_yards": rec * 11.0, "carries": car, "rushing_yards": car * 4.3,
                         "attempts": 35.0 if pos == "QB" else 0.0, "completions": 22.0 if pos == "QB" else 0.0,
                         "passing_yards": 245.0 if pos == "QB" else 0.0, "passing_tds": 1.6 if pos == "QB" else 0.0,
                         "passing_interceptions": 0.8 if pos == "QB" else 0.0, "rushing_tds": td if car else 0.0,
                         "receiving_tds": td if tgt else 0.0, "_sort": 202500 + wk, "season_year": 2025, "_opp": "BBB"})
        table[nm] = rows
    return table


_ENV = types.SimpleNamespace(implied_total=24.0, team_spread=0.0, wind=None, roof=None, temp=None, season=2025, week=9,
                             home="AAA")
_ALPHA = HL.AlphaLookup(lambda stat: types.SimpleNamespace(alpha=0.3, cv=0.2))


def _price(stat, name, live=None, env=_ENV, table=None):
    table = table or _team_rows()
    ctx = HL.build_team_ctx(table)
    return HL.price_row(stat, table[name], "AAA", ctx, env, _ALPHA, None, live=live, pid=name, opp="BBB")


def _out(*pids, weight=1.0):
    return HL.LiveContext(absent_by_team={("AAA", 2025, 9): [{"pid": p, "weight": weight} for p in pids]})


def test_team_context_shares():
    ctx = HL.build_team_ctx(_team_rows())["teams"]["AAA"]
    wr1 = next(p for p in ctx["players"] if p["pid"] == "wr1")
    assert wr1["tgt"] == pytest.approx(9.8 / 35.0)                       # the 28% receiver
    assert ctx["pass_att"] == pytest.approx(35.0) and ctx["carries"] == pytest.approx(25.0)


def test_wr1_out_lifts_wr2_and_te1_receptions_and_yards():
    for name in ("wr2", "te1", "wr3"):
        for stat in ("receptions", "rec_yds"):
            base, out = _price(stat, name), _price(stat, name, live=_out("wr1"))
            assert out.mean > base.mean, (name, stat)
    # role-weighted: the WR peers gain more than the TE does, per unit of their own share
    g = {n: _price("receptions", n, live=_out("wr1")).mean / _price("receptions", n).mean for n in ("wr2", "te1")}
    assert g["wr2"] > g["te1"] + 0.02 and g["te1"] > 1.0              # a real margin, not float noise
    # and the roles agree whether or not the logs carry air yards (the bug CI caught: unknown aDOT split the roles)
    assert HL._role("WR", None) == HL._role("WR", HL.POS_ADOT["WR"]) == "perimeter"
    assert HL._role("TE", None) == "slot" and HL._role("RB", None) == "rb"
    assert _price("receptions", "wr2", live=_out("wr1")).inputs.vacated_target_share > 0


def test_questionable_teammate_counts_less_than_an_out_teammate():
    base = _price("receptions", "wr2").mean
    q = _price("receptions", "wr2", live=_out("wr1", weight=cg.absence_weight("Questionable", "Limited Participation in Practice"))).mean
    o = _price("receptions", "wr2", live=_out("wr1")).mean
    assert base < q < o


def test_rb1_out_lifts_rb2_carries():
    base, out = _price("rush_attempts", "rb2"), _price("rush_attempts", "rb2", live=_out("rb1"))
    assert out.mean > base.mean and out.inputs.vacated_rush_share > 0


def test_redistribute_conserves_the_pool():
    active = [("a", "slot", 0.2), ("b", "perimeter", 0.15), ("c", "rb", 0.1)]
    added = cg.redistribute({"perimeter": 0.28}, active)
    assert sum(added.values()) == pytest.approx(0.28)
    assert added["b"] > added["a"] > 0                                         # same-role peer gets the lion's share
    assert sum(cg.redistribute({"slot": 0.1}, [("x", "rb", 0.3)]).values()) == pytest.approx(0.1)   # no peer -> spills
    assert cg.redistribute({"slot": 0.1}, []) == {}


def test_redistribute_with_zero_share_peers_spills_instead_of_dividing_by_zero():
    """Found on real data: a role whose active peers all have 0 share crashed with ZeroDivisionError."""
    added = cg.redistribute({"slot": 0.1}, [("a", "slot", 0.0), ("b", "perimeter", 0.2)])
    assert sum(added.values()) == pytest.approx(0.1) and added["b"] == pytest.approx(0.1) and added["a"] == 0.0


def test_absence_weights_follow_the_spec():
    w = cg.absence_weight
    assert [w(s) for s in ("Out", "IR", "PUP", "Suspended")] == [1.0] * 4
    assert w("Doubtful") == 0.80
    assert w("Questionable", "Did Not Participate In Practice") == 0.55
    assert w("Questionable", "Limited Participation in Practice") == 0.25
    assert w("Questionable", "Full Participation in Practice") == 0.0
    assert w("Questionable", None) == cg.Q_UNKNOWN_PRACTICE
    assert w("Active") == 0.0 and w(None) == 0.0


def _tds():
    z = [{"gl": 0.5, "rz": 0.3, "ex": 0.1}, {"gl": 0.2, "rz": 0.3, "ex": 0.4}, {"gl": 0.1, "rz": 0.2, "ex": 0.3},
         {"gl": 0.15, "rz": 0.15, "ex": 0.15}, {"gl": 0.05, "rz": 0.05, "ex": 0.05}]
    return [hp.PlayerInputs(td_ewm=t, td_zone=zz) for t, zz in zip((0.6, 0.5, 0.4, 0.3, 0.2), z)]


def test_higher_implied_total_raises_every_skill_players_td_probability():
    team = _tds()
    lo = hp.anchor_team_tds(hp.td_lambda_priors(team), 17.5)
    hi = hp.anchor_team_tds(hp.td_lambda_priors(team), 28.5)
    assert all(h > l for h, l in zip(hi, lo))
    assert all(hp.anytime_td_prob(h) > hp.anytime_td_prob(l) for h, l in zip(hi, lo))
    for i, p in enumerate(team):                                                # the same through the per-player pricer
        mates = team[:i] + team[i + 1:]
        a = hp.price_anytime_td(p, G(implied_team_total=17.5), mates).sf(0.5)
        b = hp.price_anytime_td(p, G(implied_team_total=28.5), mates).sf(0.5)
        assert b > a


def test_team_td_conservation():
    team = _tds()
    pri = hp.td_lambda_priors(team)
    assert sum(pri) == pytest.approx(sum(p.td_ewm for p in team))              # zone shares redistribute, never create TDs
    lam = hp.team_td_expectation(24.0)
    assert lam == pytest.approx((24.0 - 4.5) / 6.9) and hp.team_td_expectation(5.0) == 0.8
    assert sum(hp.anchor_team_tds(pri, 24.0, anchor_w=1.0)) == pytest.approx(lam)           # hard conservation
    eb = sum(hp.anchor_team_tds(pri, 24.0))
    assert min(sum(pri), lam) <= eb <= max(sum(pri), lam)                                    # shrinks toward, not past
    assert hp.anchor_team_tds(pri, None) == pri


def test_vacated_red_zone_share_raises_td_intensity():
    base = hp.td_lambda_priors([hp.PlayerInputs(td_ewm=0.4), hp.PlayerInputs(td_ewm=0.4)])
    vac = hp.td_lambda_priors([hp.PlayerInputs(td_ewm=0.4, vacated_rz_share=0.2), hp.PlayerInputs(td_ewm=0.4)])
    assert vac[0] > base[0] and vac[1] == base[1]


def test_higher_implied_total_raises_pass_tds_and_anytime_td_through_the_adapter():
    lo = types.SimpleNamespace(**{**vars(_ENV), "implied_total": 17.5})
    hi = types.SimpleNamespace(**{**vars(_ENV), "implied_total": 28.5})
    for name, stat in (("qb", "pass_tds"), ("wr1", "anytime_td"), ("wr2", "anytime_td"), ("te1", "anytime_td"),
                       ("rb1", "anytime_td")):
        assert _price(stat, name, env=hi).dist.sf(0.5) > _price(stat, name, env=lo).dist.sf(0.5), (name, stat)


# ------------------------------------------------------------------------------------------- adapter edge behaviour
def test_price_row_declines_instead_of_guessing():
    table = _team_rows()
    ctx = HL.build_team_ctx(table)
    assert HL.price_row("rec_yds", table["wr1"][:3], "AAA", ctx, _ENV, _ALPHA) is None          # < MIN_ROWS
    assert HL.price_row("not_a_stat", table["wr1"], "AAA", ctx, _ENV, _ALPHA) is None
    assert HL.price_row("pass_yds", table["wr1"], "AAA", ctx, _ENV, _ALPHA) is None            # a non-passer


def test_every_hierarchical_stat_prices_with_no_environment_at_all():
    table = _team_rows()
    ctx = HL.build_team_ctx(table)
    for stat, name in (("pass_attempts", "qb"), ("pass_completions", "qb"), ("pass_yds", "qb"), ("pass_tds", "qb"),
                       ("interceptions", "qb"), ("rush_attempts", "rb1"), ("rush_yds", "rb1"), ("receptions", "wr1"),
                       ("rec_yds", "wr1"), ("anytime_td", "wr1")):
        r = HL.price_row(stat, table[name], "AAA", ctx, None, _ALPHA, None, pid=name, opp="BBB")
        assert r is not None and r.mean > 0, stat
        assert 0.0 <= r.p_over(0.5) <= 1.0 and r.dist.ppf(0.9) >= r.dist.ppf(0.1)


def test_calibration_multiplier_scales_volume_only():
    table = _team_rows()
    ctx = HL.build_team_ctx(table)
    a = HL.price_row("receptions", table["wr1"], "AAA", ctx, _ENV, _ALPHA, None, pid="wr1")
    b = HL.price_row("receptions", table["wr1"], "AAA", ctx, _ENV, _ALPHA, {"k": 0.96}, pid="wr1")
    assert b.mean == pytest.approx(a.mean * 0.96, rel=0.02) and b.vol_mult == 0.96
    td = HL.price_row("anytime_td", table["wr1"], "AAA", ctx, _ENV, _ALPHA, {"b0": -0.04, "b1": 0.27, "b2": 0.0}, pid="wr1")
    assert td.vol_mult == pytest.approx(math.exp(-0.04))            # TD stats take the scale only (no env term twice)


def test_engine_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("NFL_ENGINE", raising=False)
    assert HL.enabled() is False
    monkeypatch.setenv("NFL_ENGINE", "hierarchical")
    assert HL.enabled() is True


def test_daily_projections_serves_the_hierarchical_engine_only_when_asked(monkeypatch):
    from ev import daily_projections as dp
    from mlb_core.data import cockpit_form as cf
    table = _team_rows()
    for rows in table.values():
        for r in rows:
            r["_date"] = f"W{r['week']}"
    logs = dp.GameLogs("nfl", {"all": table})
    monkeypatch.setattr(dp.GameLogs, "nfl_live", lambda self: None)
    monkeypatch.setattr(dp.GameLogs, "env_lines", lambda self: {})
    start = datetime(2025, 11, 2, 18, 0, tzinfo=timezone.utc)
    sdef = cf.NFL_STATS["rec_yds"]
    args = ("nfl", "rec_yds", "WR1", "wr1", table["wr1"], sdef, logs, 54.5, None, [], "AAA", "BBB", "nfl:2025-11-02:BBB@AAA",
            start, 60.0)
    monkeypatch.delenv("NFL_ENGINE", raising=False)
    legacy = dp.make_row(*args)
    assert legacy["engine"] == dp.ENGINE and dp.engine_id("nfl") == dp.ENGINE
    monkeypatch.setenv("NFL_ENGINE", "hierarchical")
    hier = dp.make_row(*args)
    assert hier["engine"] == dp.ENGINE_NFL_HIER == dp.engine_id("nfl") and hier["validated"] is False
    assert 0.0 < hier["p_over_model"] < 1.0 and hier["proj_p10"] <= hier["proj_p90"]
    assert dp.engine_id("nhl") == dp.ENGINE                                   # other sports are untouched


def test_hierarchical_failure_falls_back_to_the_legacy_price(monkeypatch):
    from ev import daily_projections as dp
    from mlb_core.data import cockpit_form as cf
    monkeypatch.setenv("NFL_ENGINE", "hierarchical")
    table = _team_rows()
    for rows in table.values():
        for r in rows:
            r["_date"] = f"W{r['week']}"
    logs = dp.GameLogs("nfl", {"all": table})
    monkeypatch.setattr(dp.GameLogs, "env_lines", lambda self: {})
    monkeypatch.setattr(dp.GameLogs, "nfl_live", lambda self: None)
    monkeypatch.setattr(dp._hl, "price_row", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    row = dp.make_row("nfl", "rec_yds", "WR1", "wr1", table["wr1"], cf.NFL_STATS["rec_yds"], logs, 54.5, None, [], "AAA", "BBB",
                      "k", datetime(2025, 11, 2, 18, 0, tzinfo=timezone.utc), 60.0)
    assert row is not None and row["engine"] == dp.ENGINE


# --------------------------------------------------------------------------------------- 4. zero time-travel leakage
def _pbp(weeks=8, seed=0):
    rng = np.random.default_rng(seed)
    teams = ["AAA", "BBB", "CCC", "DDD"]
    rows = []
    pid = 0
    for wk in range(1, weeks + 1):
        pairs = [("AAA", "BBB"), ("CCC", "DDD")] if wk % 2 else [("AAA", "CCC"), ("BBB", "DDD")]
        for h, a in pairs:
            gid = f"2025_{wk:02d}_{a}_{h}"
            for pos, dfn in ((h, a), (a, h)):
                for i in range(40):
                    pid += 1
                    is_pass = rng.random() < 0.58
                    sack = float(is_pass and rng.random() < 0.07)
                    rows.append({
                        "game_id": gid, "play_id": pid, "season": 2025, "week": wk, "season_type": "REG", "posteam": pos,
                        "defteam": dfn, "play_type": "pass" if is_pass else "run", "qb_dropback": float(is_pass),
                        "qb_kneel": 0.0, "qb_spike": 0.0, "sack": sack, "qb_hit": float(rng.random() < 0.1),
                        "air_yards": np.nan if (not is_pass or sack) else float(rng.integers(-3, 35)),
                        "yards_after_catch": float(rng.integers(0, 12)), "complete_pass": float(is_pass and rng.random() < 0.65),
                        "yards_gained": float(rng.integers(-3, 25)), "rushing_yards": float(rng.integers(-3, 15)),
                        "receiver_player_id": f"{pos}_r{rng.integers(0, 4)}" if is_pass and not sack else None,
                        "passer_player_id": f"{pos}_qb", "cpoe": float(rng.normal(0, 6)), "xpass": float(rng.uniform(0.3, 0.8)),
                        "down": float(rng.integers(1, 4)), "wp": float(rng.uniform(0.2, 0.8)),
                        "half_seconds_remaining": float(rng.integers(100, 1700))})
    return pd.DataFrame(rows)


def _part(pbp, seed=1):
    rng = np.random.default_rng(seed)
    p = pbp[["game_id", "play_id"]].rename(columns={"game_id": "nflverse_game_id"}).copy()
    p["defense_coverage_type"] = rng.choice(["COVER_1", "COVER_3", "COVER_2", "COVER_4", "COVER_6", "2_MAN"], len(p))
    p["defense_man_zone_type"] = rng.choice(["MAN_COVERAGE", "ZONE_COVERAGE"], len(p))
    p["was_pressure"] = rng.choice([True, False], len(p))
    return p


_NUM = ["sack", "qb_hit", "air_yards", "yards_after_catch", "complete_pass", "yards_gained", "rushing_yards", "cpoe", "xpass"]


def _scramble(pbp, from_week, seed=99):
    """Replace every outcome column of games in weeks >= from_week with different values."""
    rng = np.random.default_rng(seed)
    p = pbp.copy()
    m = p["week"] >= from_week
    for c in _NUM:
        p.loc[m, c] = rng.uniform(-5, 40, int(m.sum()))
    p.loc[m, "receiver_player_id"] = "ZZZ"
    p.loc[m, "play_type"] = rng.choice(["pass", "run"], int(m.sum()))
    return p


def _eq(a, b):
    pd.testing.assert_frame_equal(a.sort_index(), b.sort_index(), check_exact=True)
    assert a.attrs == b.attrs


POS = {f"{t}_r{i}": pos for t in ("AAA", "BBB", "CCC", "DDD") for i, pos in enumerate(("WR", "WR", "TE", "RB"))}


@pytest.mark.parametrize("cut", [3, 5, 8])
def test_context_features_ignore_week_w_and_later(cut):
    pbp, part = _pbp(), None
    part = _part(pbp)
    mut = _scramble(pbp, cut)
    part_mut = part.copy()
    later = pbp.loc[pbp["week"] >= cut, "game_id"].unique()
    part_mut.loc[part_mut["nflverse_game_id"].isin(later), "defense_coverage_type"] = "COVER_0"
    part_mut.loc[part_mut["nflverse_game_id"].isin(later), "was_pressure"] = True
    _eq(cg.defense_context_before(pbp, 2025, cut, POS, part), cg.defense_context_before(mut, 2025, cut, POS, part_mut))
    _eq(cg.offense_context_before(pbp, 2025, cut), cg.offense_context_before(mut, 2025, cut))
    _eq(cg.receiver_context_before(pbp, 2025, cut, POS), cg.receiver_context_before(mut, 2025, cut, POS))
    _eq(cg.passer_context_before(pbp, 2025, cut), cg.passer_context_before(mut, 2025, cut))


def test_mutating_week_w_plus_one_alone_changes_nothing_for_week_w():
    pbp = _pbp()
    part = _part(pbp)
    only_next = pbp.copy()
    m = only_next["week"] == 6
    only_next.loc[m, "air_yards"] = 99.0
    only_next.loc[m, "sack"] = 1.0
    _eq(cg.defense_context_before(pbp, 2025, 5, POS, part), cg.defense_context_before(only_next, 2025, 5, POS, part))
    _eq(cg.receiver_context_before(pbp, 2025, 5, POS), cg.receiver_context_before(only_next, 2025, 5, POS))


def test_features_do_move_when_PRIOR_games_change():
    """Sanity: the leakage tests are not passing because the features are constant."""
    pbp = _pbp()
    part = _part(pbp)
    mut = _scramble(pbp, 1)
    mut = mut[mut["week"] < 4]
    base = pbp[pbp["week"] < 4]
    a = cg.defense_context_before(base, 2025, 4, POS, part)
    b = cg.defense_context_before(_scramble(base, 1), 2025, 4, POS, part)
    assert not a["def_short_target_share_allowed"].equals(b["def_short_target_share_allowed"])


def test_context_through_week_w_minus_one_is_the_same_whether_or_not_later_weeks_exist():
    pbp = _pbp()
    part = _part(pbp)
    _eq(cg.defense_context_before(pbp, 2025, 5, POS, part), cg.defense_context_before(pbp[pbp["week"] < 5], 2025, 5, POS, part))


def test_snap_and_injury_layers_ignore_the_future():
    rows = []
    for wk in range(1, 9):
        for i, pos in enumerate(("T", "G", "C", "G", "T")):
            rows.append({"season": 2025, "week": wk, "team": "AAA", "position": pos, "pfr_player_id": f"p{i}",
                         "offense_pct": 0.95 if wk < 6 or i != 0 else 0.0})
    snaps = pd.DataFrame(rows)
    players = pd.DataFrame({"pfr_id": [f"p{i}" for i in range(5)], "gsis_id": [f"g{i}" for i in range(5)]})
    mut = snaps.copy()
    mut.loc[mut["week"] >= 5, "offense_pct"] = 0.0
    a = cg.ol_starters_before(snaps, players, 2025, 5)
    assert a == cg.ol_starters_before(mut, players, 2025, 5)
    assert a["AAA"] == {f"g{i}" for i in range(5)}
    inj = pd.DataFrame([{"season": 2025, "week": 5, "team": "AAA", "gsis_id": "g0", "report_status": "Out"},
                        {"season": 2025, "week": 5, "team": "AAA", "gsis_id": "g1", "report_status": "Questionable"},
                        {"season": 2025, "week": 5, "team": "AAA", "gsis_id": "gX", "report_status": "Out"},
                        {"season": 2025, "week": 6, "team": "AAA", "gsis_id": "g2", "report_status": "Out"}])
    assert cg.n_ol_out(inj, a, 2025, 5) == {"AAA": 1}                      # starters only, this week's report only
    assert cg.n_ol_out(None, a, 2025, 5) == {} and cg.ol_starters_before(None, players, 2025, 5) == {}


def test_missing_optional_tables_degrade_to_empty_not_errors():
    empty = pd.DataFrame()
    assert len(cg.defense_context_before(empty, 2025, 5)) == 0
    assert len(cg.offense_context_before(empty, 2025, 5)) == 0
    assert len(cg.receiver_context_before(empty, 2025, 5)) == 0
    assert len(cg.passer_context_before(empty, 2025, 5)) == 0
    pbp = _pbp()
    d = cg.defense_context_before(pbp, 2025, 5)                           # no participation, no positions
    assert d["def_two_high_rate"].isna().all() and d["def_rb_target_share_allowed"].isna().all()
    assert d["def_short_target_share_allowed"].notna().all() and d["def_pass_rush_pressure_rate"].notna().all()


def test_coverage_shell_rates_from_participation():
    pbp = pd.DataFrame({
        "game_id": ["g1"] * 6, "play_id": range(6), "season": 2025, "week": 1, "season_type": "REG", "posteam": "AAA",
        "defteam": "BBB", "play_type": "pass", "qb_dropback": 1.0, "sack": 0.0, "qb_hit": 0.0, "air_yards": 8.0,
        "yards_after_catch": 3.0, "complete_pass": 1.0, "yards_gained": 11.0, "rushing_yards": np.nan,
        "receiver_player_id": "r1"})
    part = pd.DataFrame({"nflverse_game_id": ["g1"] * 6, "play_id": range(6),
                         "defense_coverage_type": ["COVER_2", "COVER_2", "COVER_4", "COVER_3", "COVER_1", "COVER_6"],
                         "defense_man_zone_type": ["ZONE_COVERAGE"] * 4 + ["MAN_COVERAGE"] * 2, "was_pressure": [False] * 6})
    d = cg.defense_context_before(pbp, 2025, 2, None, part).loc["BBB"]
    assert d["def_two_high_rate"] == pytest.approx(4 / 6) and d["def_middle_closed_rate"] == pytest.approx(2 / 6)
    assert d["def_man_rate"] == pytest.approx(2 / 6) and d["def_zone_rate"] == pytest.approx(4 / 6)
    assert d["def_short_target_share_allowed"] == 1.0 and d["def_deep_target_share_allowed"] == 0.0
    assert d["def_yac_per_rec_allowed"] == 3.0


def test_blank_coverage_tags_are_unknown_not_a_category():
    """The real participation file writes '' for untagged plays; they must not enter the man / coverage denominators."""
    pbp = pd.DataFrame({
        "game_id": ["g1"] * 4, "play_id": range(4), "season": 2025, "week": 1, "season_type": "REG", "posteam": "AAA",
        "defteam": "BBB", "play_type": "pass", "qb_dropback": 1.0, "sack": 0.0, "qb_hit": 0.0, "air_yards": 8.0,
        "yards_after_catch": 3.0, "complete_pass": 1.0, "yards_gained": 11.0, "rushing_yards": np.nan,
        "receiver_player_id": "r1"})
    part = pd.DataFrame({"nflverse_game_id": ["g1"] * 4, "play_id": range(4),
                         "defense_coverage_type": ["COVER_2", None, None, "COVER_1"],
                         "defense_man_zone_type": ["ZONE_COVERAGE", "", "", "MAN_COVERAGE"], "was_pressure": [False] * 4})
    d = cg.defense_context_before(pbp, 2025, 2, None, part).loc["BBB"]
    assert d["def_man_rate"] == pytest.approx(0.5) and d["def_two_high_rate"] == pytest.approx(0.5)


def test_receiver_and_passer_profiles():
    pbp = _pbp()
    r = cg.receiver_context_before(pbp, 2025, 6, POS)
    assert {"adot", "wopr", "racr", "deep_target_share", "ypr", "yac_per_rec", "catch_rate"} <= set(r.columns)
    row = r.loc["AAA_r0"]
    assert row["pos"] == "WR" and row["team"] == "AAA" and 0.0 <= row["deep_target_share"] <= 1.0
    assert row["wopr"] == pytest.approx(1.5 * row["target_share"] + 0.7 * row["air_yards_share"], rel=0.2)
    p = cg.passer_context_before(pbp, 2025, 6)
    assert "cpoe_dev" in p.columns and p["adot"].between(0, 25).all()


def test_trench_pressure_and_ol_delta():
    assert cg.ol_health_delta(2) == pytest.approx(0.05) and cg.ol_health_delta(-1) == 0.0
    assert cg.net_pressure_mismatch(0.09, 0, 0.09, 0.09, 0.09) == pytest.approx(1.0)       # league average = 1
    assert cg.net_pressure_mismatch(0.09, 2, 0.09, 0.09, 0.09) > 1.0
    assert cg.net_pressure_mismatch(0.09, 0, 0.12, 0.09, 0.09) == pytest.approx(0.12 / 0.09)
    assert math.isnan(cg.net_pressure_mismatch(float("nan"), 0, 0.09, 0.09, 0.09))
    # more pressure raises interceptions and lowers completion rate
    qb = _qb()
    hot = G(team_spread=0.0, net_pressure_mismatch=1.4)
    cold = G(team_spread=0.0, net_pressure_mismatch=0.7)
    assert hp.price("interceptions", qb, hot).mean > hp.price("interceptions", qb, cold).mean
    assert hp.completion_rate(qb, hot) < hp.completion_rate(qb, cold)


# ------------------------------------------------------------------------------------------------------------ weather
def test_closed_roof_stadiums_are_neutral_without_a_network_call():
    def boom(*a, **k):
        raise AssertionError("no network for a dome")
    ko = datetime.now(timezone.utc) + timedelta(days=2)
    for tm in ("IND", "LV", "LAR", "LAC", "MIN", "NO", "DET", "ATL", "DAL", "ARI", "HOU"):
        w = W.kickoff_weather(tm, ko, http_get=boom)
        assert w["dome"] and w["wind_mph"] == 0.0 and w["temp_f"] == 72.0 and w["precip_mm"] == 0.0 and w["source"] == "dome"


def test_stadium_table_is_complete():
    assert len(W.STADIUM_COORDS) == 32 and {W.canon_team(t) for t in ("JAC", "LA", "WSH")} == {"JAX", "LAR", "WAS"}
    for tm, (lat, lon, roof) in W.STADIUM_COORDS.items():
        assert -90 < lat < 90 and -180 < lon < 0 and roof in ("open", "dome", "retractable", "canopy")


def test_schedule_values_and_roof_override_win():
    ko = datetime.now(timezone.utc) + timedelta(days=2)
    s = W.kickoff_weather("BUF", ko, schedule_wind=17.0, schedule_temp=30.0, http_get=lambda *a: (_ for _ in ()).throw(AssertionError))
    assert s["source"] == "schedule" and s["wind_mph"] == 17.0 and s["temp_f"] == 30.0
    assert W.kickoff_weather("BUF", ko, schedule_roof="dome")["dome"]                       # schedule says closed
    assert not W.kickoff_weather("DAL", ko, schedule_roof="open", fetch=False)["dome"]       # retractable, opened


def _om(hour_iso, wind=14.0):
    hours = [f"{hour_iso[:10]}T{h:02d}:00" for h in range(24)]
    return {"hourly": {"time": hours, "temperature_2m": [50.0] * 24, "wind_speed_10m": [wind] * 24,
                       "wind_gusts_10m": [wind + 8] * 24, "precipitation": [0.2] * 24, "precipitation_probability": [40] * 24}}


def test_open_meteo_forecast_fills_missing_wind_and_is_cached():
    W._CACHE.clear()
    ko = (datetime.now(timezone.utc) + timedelta(days=3)).replace(hour=18, minute=0, second=0, microsecond=0)
    calls = []

    def fake(url, params, timeout):
        calls.append((url, params, timeout))
        return _om(ko.isoformat())
    w = W.kickoff_weather("BUF", ko, http_get=fake)
    assert w["source"] == "open-meteo" and w["wind_mph"] == 14.0 and w["gust_mph"] == 22.0 and w["precip_prob"] == 40
    assert calls[0][0] == W.OPEN_METEO_URL and calls[0][2] == 2.0 and calls[0][1]["wind_speed_unit"] == "mph"
    W.kickoff_weather("BUF", ko, http_get=fake)
    assert len(calls) == 1                                                                   # cached


def test_weather_never_raises():
    W._CACHE.clear()
    ko = datetime.now(timezone.utc) + timedelta(days=2)

    def down(*a, **k):
        raise TimeoutError("down")
    w = W.kickoff_weather("BUF", ko, http_get=down)
    assert w["source"] == "none" and w["wind_mph"] is None and not w["dome"]
    W._CACHE.clear()
    assert W.kickoff_weather("BUF", ko, http_get=lambda *a: {"junk": 1})["source"] == "none"
    assert W.kickoff_weather("BUF", None)["source"] == "none"
    assert W.kickoff_weather(None, ko)["source"] == "none" and W.kickoff_weather("XXX", ko)["source"] == "none"
    far = datetime.now(timezone.utc) + timedelta(days=40)
    assert W.kickoff_weather("BUF", far, http_get=lambda *a: (_ for _ in ()).throw(AssertionError))["source"] == "none"


# -------------------------------------------------------------------------------------- 5. engine_eval comparison
def _eval_frame():
    """A synthetic league: two seasons of weekly lines for a few QBs / RBs / WRs on 6 teams plus a schedule."""
    rng = np.random.default_rng(3)
    teams = [f"T{i}" for i in range(6)]
    rows, sched = [], []
    for season in (2023, 2024):
        for wk in range(1, 18):
            ids = list(rng.permutation(teams))
            for h, a in zip(ids[0::2], ids[1::2]):
                sched.append({"game_id": f"{season}_{wk}_{a}_{h}", "season": season, "week": wk,
                              "gameday": f"{season}-09-{wk:02d}" if wk < 28 else "2024-12-01", "home_team": h, "away_team": a,
                              "spread_line": float(rng.choice([-3.0, 3.0, 6.5])), "total_line": 45.0})
                for tm, opp in ((h, a), (a, h)):
                    for nm, pos, att, car, tgt in (("qb", "QB", 35, 3, 0), ("rb", "RB", 0, 15, 3), ("wr", "WR", 0, 0, 8)):
                        c = rng.poisson(car) if car else 0
                        t = rng.poisson(tgt) if tgt else 0
                        rec = rng.binomial(t, 0.65) if t else 0
                        n = rng.poisson(att) if att else 0
                        comp = rng.binomial(n, 0.64) if n else 0
                        rows.append({"player_id": f"{tm}_{nm}", "player_display_name": f"{tm} {nm}", "team": tm,
                                     "season": season, "week": wk, "season_type": "REG", "game_id": sched[-1]["game_id"],
                                     "opponent_team": opp, "position": pos, "attempts": n, "completions": comp,
                                     "passing_yards": comp * 11.0 + rng.normal(0, 8), "passing_tds": rng.poisson(1.5) if n else 0,
                                     "passing_interceptions": rng.poisson(0.8) if n else 0, "carries": c,
                                     "rushing_yards": c * 4.3 + rng.normal(0, 4), "rushing_tds": rng.poisson(0.3) if c else 0,
                                     "targets": t, "receptions": rec, "receiving_yards": rec * 11.0 + rng.normal(0, 5),
                                     "receiving_tds": rng.poisson(0.3) if t else 0})
    df = pd.DataFrame(rows)
    from mlb_core.data import cockpit_form as cf
    df["_name"] = df["player_display_name"].map(cf.norm_name)
    df["_opp"] = df["opponent_team"]
    df["_sort"] = df["season"] * 100 + df["week"]
    return df.sort_values(["season", "week"], kind="stable").reset_index(drop=True), pd.DataFrame(sched)


def test_hierarchical_replay_scores_both_engines_on_identical_rows(monkeypatch):
    from nfl.props import engine_eval as EE
    from nfl.props import env_adjust as EA
    monkeypatch.setattr(EE, "MIN_ROWS", 20)
    df, sched = _eval_frame()
    res = EE.replay_hierarchical(df, [2024], EA.game_env_table(sched), stats=("pass_yds", "rec_yds", "receptions", "anytime_td"))
    assert len(res) > 100 and set(res["stat"]) == {"pass_yds", "rec_yds", "receptions", "anytime_td"}
    assert (res["season"] == 2024).all()
    assert np.isfinite(res[["ls_base", "ls_hier", "p_base", "p_hier", "crps_base", "crps_hier"]].to_numpy()).all()
    assert res["p_hier"].between(0, 1).all() and res["p_base"].between(0, 1).all()
    assert "p_hier_plus200" in res.columns and res.loc[res["stat"] == "rec_yds", "hit_plus400"].isin([0.0, 1.0]).all()
    cmp = EE.compare_hierarchical(res, [2024])
    ry = cmp["rec_yds"]
    assert ry["n"] > 20 and ry["verdict"] in ("ADOPT", "WORSE", "NO CLEAR GAIN")
    assert set(ry["tails"]) == {"plus200", "plus400"}
    # the baseline's rung IS ~1/3 and ~1/5 by construction: its mean predicted P(over) sits near them
    assert 0.2 < ry["tails"]["plus200"]["pred_base"] < 0.5 and 0.08 < ry["tails"]["plus400"]["pred_base"] < 0.35
    txt = EE.render_hierarchical(cmp, [2024])
    assert "rec_yds" in txt and "adoption bar" in txt and "NFL_ENGINE" in txt


def test_hierarchical_replay_has_no_lookahead(monkeypatch):
    """Scoring week w from history that has week w and later scrambled must give the identical hierarchical price."""
    from nfl.props import engine_eval as EE
    from nfl.props import env_adjust as EA
    monkeypatch.setattr(EE, "MIN_ROWS", 20)
    df, sched = _eval_frame()
    env = EA.game_env_table(sched)
    stats = ("rec_yds", "pass_yds")
    mut = df.copy()
    m = (mut["season"] == 2024) & (mut["week"] > 10)             # future of every week <= 10 ... but week 10's own row stays
    for c in ("receiving_yards", "passing_yards", "receptions", "targets", "attempts", "carries", "rushing_yards"):
        mut.loc[m, c] = mut.loc[m, c] * 3.0 + 7.0
    a = EE.replay_hierarchical(df[(df["season"] == 2023) | (df["week"] <= 10)], [2024], env, stats=stats)
    b = EE.replay_hierarchical(mut[(mut["season"] == 2023) | (mut["week"] <= 10)], [2024], env, stats=stats)
    cols = ["season", "week", "player", "stat", "mu_hier", "mu_base", "p_hier", "p_base"]
    pd.testing.assert_frame_equal(a[cols].reset_index(drop=True), b[cols].reset_index(drop=True))
    full = EE.replay_hierarchical(df, [2024], env, stats=stats)
    f10 = full[full["week"] <= 10][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(f10, a[cols].reset_index(drop=True))     # same prices with or without later weeks existing
