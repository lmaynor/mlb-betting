"""NBA skewed points marginal (ev.daily_projections) -- offline, synthetic numbers only.

The construction is the closed form of PR #186's simulator points marginal (points = 3 x threes + nonthree
points) at the board's own (mu, sd): see the block above p_over in ev/daily_projections.py and
docs/analysis/nba_joint_simulator_2026-11-05.md. Pins: exact mean / variance preservation, positive skew with
the expected P(over) directions vs the Normal it replaces, the whole-line push convention of the repo's other
count families, the degenerate-input fallbacks, the NBA_SKEWED_POINTS kill switch (off = the exact old Normal
everywhere), and a shape check of the closed form against the simulator's own draws.
"""
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from scipy.stats import norm

from ev import daily_projections as dp
from mlb_core.data import cockpit_form as cf
from nba.sim.joint_sim import GameInput, Marginal, PlayerInput, simulate_game

CTX = dp.StatContext()
# (mu, sd, mu3, sd3): mutually consistent archetypes (var = 9 var3 + var_nt + a small positive covariance),
# spanning a star, a three-heavy shooter, a low-threes big and a volatile bench scorer.
ARCHETYPES = [
    (27.0, 8.95, 2.8, math.sqrt(5.15)),
    (20.0, 9.19, 3.6, math.sqrt(7.49)),
    (18.0, 5.93, 0.4, math.sqrt(0.45)),
    (10.0, 5.19, 1.2, math.sqrt(1.63)),
]


def _moments(mu, sd, mu3, sd3):
    kmax = int(mu + 14 * sd) + 30
    pmf = dp._nba_points_skew_pmf(mu, sd, mu3, sd3, kmax)
    xs = np.arange(kmax + 1)
    mean = float((xs * pmf).sum())
    var = float(((xs - mean) ** 2 * pmf).sum())
    skew = float(((xs - mean) ** 3 * pmf).sum()) / max(var ** 1.5, 1e-12)
    return float(pmf.sum()), mean, var, skew


@pytest.mark.parametrize("mu,sd,mu3,sd3", ARCHETYPES)
def test_mean_and_variance_are_the_projections_own(mu, sd, mu3, sd3):
    mass, mean, var, _ = _moments(mu, sd, mu3, sd3)
    assert mass == pytest.approx(1.0, abs=1e-6)
    assert mean == pytest.approx(mu, abs=0.02)
    assert var == pytest.approx(sd * sd, rel=0.02)


@pytest.mark.parametrize("mu,sd,mu3,sd3", ARCHETYPES)
def test_positive_skew_and_p_over_directions_vs_normal(mu, sd, mu3, sd3):
    _, _, _, skew = _moments(mu, sd, mu3, sd3)
    assert skew > 0.2
    # median below the mean: P(over) at the first half-line above mu is BELOW the Normal's
    line_mid = math.floor(mu) + 0.5
    p_skew = dp.p_over("normal", mu, line_mid, CTX, sd=sd, mu3=mu3, sd3=sd3)
    p_norm = 1.0 - norm.cdf((line_mid - mu) / sd)
    assert p_skew < p_norm
    # and the far right tail is FATTER than the Normal's
    line_tail = math.floor(mu + 2.5 * sd) + 0.5
    p_skew = dp.p_over("normal", mu, line_tail, CTX, sd=sd, mu3=mu3, sd3=sd3)
    p_norm = 1.0 - norm.cdf((line_tail - mu) / sd)
    assert p_skew > p_norm


def test_cdf_is_a_cdf_and_whole_lines_push():
    mu, sd, mu3, sd3 = ARCHETYPES[0]
    ks = np.arange(-2, 80)
    cdf = np.array([dp.nba_points_skew_cdf(k, mu, sd, mu3, sd3) for k in ks])
    assert np.all(np.diff(cdf) >= -1e-12)
    assert cdf[0] == 0.0 and cdf[-1] > 0.999
    # whole-number line: P(over) = 1 - CDF(line) (the push mass belongs to neither side), the same
    # convention as the NegBin branch of p_over; a half line just above it prices identically.
    p_int = dp.p_over("normal", mu, 24.0, CTX, sd=sd, mu3=mu3, sd3=sd3)
    assert p_int == pytest.approx(1.0 - dp.nba_points_skew_cdf(24, mu, sd, mu3, sd3))
    assert p_int == pytest.approx(dp.p_over("normal", mu, 24.5, CTX, sd=sd, mu3=mu3, sd3=sd3))


def test_degenerate_inputs_stay_valid_distributions():
    cases = [
        (25.0, 7.5, 0.0, 0.0),        # no threes leg at all: the total is the nonthree component
        (20.0, 6.4, 3.6, 2.74),       # inconsistent board pair (threes dispersion too big for the points sd)
        (12.0, 3.0, 3.4, 2.9),        # deeply inconsistent: single discretised-gamma fallback
        (30.0, 36.0, 4.0, 3.0),       # cv > 1
        (5.0, 1.2, 0.2, 0.5),         # small mu, tight sd
    ]
    for mu, sd, mu3, sd3 in cases:
        mass, mean, var, skew = _moments(mu, sd, mu3, sd3)
        assert mass == pytest.approx(1.0, abs=1e-3)          # the cv > 1 case's tail outruns the pmf window
        assert mean == pytest.approx(mu, rel=0.02)
        assert var == pytest.approx(sd * sd, rel=0.05)
        assert skew > 0.0
        for line in (0.5, mu - 1.5, mu + 0.5, mu + 3 * sd):
            p = dp.p_over("normal", mu, line, CTX, sd=sd, mu3=mu3, sd3=sd3)
            assert 0.0 <= p <= 1.0


def test_no_split_is_the_exact_old_normal():
    # the kill switch works by _nba_component supplying no split; every price without one must be
    # bit-identical to the pre-change Normal, on both the explicit-sd and the cv paths.
    for mu, line in [(24.0, 22.5), (10.0, 12.5), (31.0, 31.5)]:
        assert dp.p_over("normal", mu, line, CTX, sd=7.0) == 1.0 - norm.cdf((line - mu) / 7.0)
        ctx = dp.StatContext(cv=0.3)
        sd = max(0.3 * mu, dp.MIN_SD_FRAC * mu, 1e-6)
        assert dp.p_over("normal", mu, line, ctx) == 1.0 - norm.cdf((line - mu) / sd)
        lo, hi = dp.proj_range("normal", mu, CTX, sd=7.0)
        assert lo == max(norm.ppf(0.10, mu, 7.0), 0.0) and hi == norm.ppf(0.90, mu, 7.0)


def test_proj_range_quantiles_match_the_priced_cdf():
    mu, sd, mu3, sd3 = ARCHETYPES[0]
    lo, hi = dp.proj_range("normal", mu, CTX, sd=sd, mu3=mu3, sd3=sd3)
    assert dp.nba_points_skew_cdf(lo - 1, mu, sd, mu3, sd3) < 0.10 <= dp.nba_points_skew_cdf(lo, mu, sd, mu3, sd3) + 1e-9
    assert dp.nba_points_skew_cdf(hi - 1, mu, sd, mu3, sd3) < 0.90 <= dp.nba_points_skew_cdf(hi, mu, sd, mu3, sd3) + 1e-9


# --------------------------------------------------------------------------- wiring: _nba_component and the flag
START = datetime(2026, 2, 20, 1, 0, tzinfo=timezone.utc)


def _mk_rows(name, team, n=30, pts=20.0, fg3a=6.0, fg3m=2.2, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        m = float(np.clip(32.0 + rng.normal(0, 3.0), 10, 44))
        k = m / 32.0
        out.append({"name": name, "team_abbr": team,
                    "_date": (datetime(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d"),
                    "_sort": (datetime(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d"),
                    "_opp": "BOS", "season_year": 2025, "minutes": m,
                    "points": float(max(round(pts * k + rng.normal(0, 3.0)), 0)),
                    "rebounds": float(max(round(5.0 * k + rng.normal(0, 1.5)), 0)),
                    "assists": float(max(round(5.0 * k + rng.normal(0, 1.2)), 0)),
                    "three_points_attempted": float(max(round(fg3a * k + rng.normal(0, 1.2)), 0)),
                    "three_points_made": float(max(round(fg3m * k + rng.normal(0, 0.9)), 0)),
                    "free_throws_made": float(max(round(3.0 * k + rng.normal(0, 1.0)), 0)),
                    "steals": 1.0, "blocks": 0.5, "fouls": 2.5})
    return out


def _logs():
    idx = {}
    for j, nm in enumerate(["Pat Guard", "Bo Backup", "Cy Center", "Pf Forward", "Wes Wing"]):
        idx[cf.norm_name(nm)] = _mk_rows(nm, "LAL", seed=10 + j)
    idx[cf.norm_name("Opp Man")] = _mk_rows("Opp Man", "BOS", seed=99)
    return dp.GameLogs("nba", {"all": idx})


def _points_component(logs):
    pn = cf.norm_name("Pat Guard")
    rows, _ = logs.rows_for("points", pn)
    return dp._nba_component("points", pn, rows, logs, "LAL", "BOS", "nba:2026-02-19:BOS@LAL", START), pn, rows


def test_component_supplies_the_players_own_threes_split(monkeypatch):
    monkeypatch.delenv("NBA_SKEWED_POINTS", raising=False)
    logs = _logs()
    comp, pn, rows = _points_component(logs)
    assert comp["mu3"] is not None and comp["sd3"] is not None and comp["mu3"] > 0
    t3 = dp._nba_component("threes", pn, rows, logs, "LAL", "BOS", "nba:2026-02-19:BOS@LAL", START)
    assert comp["mu3"] == t3["mu"] and comp["sd3"] == t3["sd"]          # one shared threes distribution
    proj = dp._nba_projection("points", pn, rows, logs, "LAL", "BOS", "nba:2026-02-19:BOS@LAL", START)
    assert proj["mu3"] == comp["mu3"]
    combo = dp._nba_projection("pra", pn, rows, logs, "LAL", "BOS", "nba:2026-02-19:BOS@LAL", START)
    assert combo.get("mu3") is None                                      # combos stay on the Normal


def test_kill_switch_restores_the_normal_exactly(monkeypatch):
    logs = _logs()
    comp_on, _, _ = _points_component(logs)
    monkeypatch.setenv("NBA_SKEWED_POINTS", "0")
    assert not dp.nba_skewed_points_enabled()
    comp_off, _, _ = _points_component(logs)
    assert comp_off["mu3"] is None and comp_off["sd3"] is None
    assert comp_off["mu"] == comp_on["mu"] and comp_off["sd"] == comp_on["sd"]
    line = math.floor(comp_off["mu"]) + 0.5
    p = dp.p_over(comp_off["family"], comp_off["mu"], line, logs.context("points"), sd=comp_off["sd"],
                  mu3=comp_off["mu3"], sd3=comp_off["sd3"])
    assert p == 1.0 - norm.cdf((line - comp_off["mu"]) / comp_off["sd"])


# --------------------------------------------------------------------------- shape vs the simulator itself
def _nb_marginal(mu, var):
    a = (var - mu) / (mu * mu)
    return Marginal("negbin", mu, math.sqrt(var), a)


def test_closed_form_tracks_the_simulators_points_marginal():
    """The simulator's structural draws for one archetype are the ground truth the closed form approximates:
    across the five model-centred lines its mean |P(over) error| must beat the Normal's, as in the study."""
    mu, sd, mu3, sd3 = ARCHETYPES[0]
    tgt = PlayerInput("TGT", "home", 36.0, {
        "points": Marginal("normal", mu, sd),
        "rebounds": _nb_marginal(6.0, 6.8), "assists": _nb_marginal(6.0, 7.3),
        "threes": _nb_marginal(mu3, sd3 * sd3)})

    def generic(name, team, pts_mu):
        return PlayerInput(name, team, 24.0, {
            "points": Marginal("normal", pts_mu, 0.32 * pts_mu),
            "rebounds": _nb_marginal(5.0, 5.8), "assists": _nb_marginal(3.5, 4.0),
            "threes": _nb_marginal(1.5, 1.8)})

    home = [tgt] + [generic(f"H{i}", "home", 12.0) for i in range(7)]
    away = [generic(f"A{i}", "away", 13.0) for i in range(8)]
    draws = simulate_game(GameInput("skew-test", home + away), n=6000, seed=11, marginals="structural")
    pts = draws.series("TGT", "points")
    err_skew, err_norm = [], []
    for z in (-1.0, -0.5, 0.0, 0.5, 1.0):
        line = math.floor(mu + z * sd) + 0.5
        p_sim = float((pts > line).mean())
        err_skew.append(abs(dp.p_over("normal", mu, line, CTX, sd=sd, mu3=mu3, sd3=sd3) - p_sim))
        err_norm.append(abs((1.0 - norm.cdf((line - mu) / sd)) - p_sim))
    assert float(np.mean(err_skew)) < float(np.mean(err_norm))
    assert float(np.mean(err_skew)) < 0.02
