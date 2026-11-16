"""RES-11 Stage 1 primitives: distribution recovery, snapshot parsing, peer
context, clustered CIs, ROI drag (RUL-20261116-intraday-pop-approach)."""
from datetime import datetime, timezone

import pytest

from ev import res11_pop as R


def test_negbin_sf_monotone_and_bounds():
    p_small = R.negbin_sf(5.5, mu=6.0, alpha=0.05)
    p_big = R.negbin_sf(5.5, mu=6.0, alpha=0.60)
    assert 0.0 < p_small < 1.0 and 0.0 < p_big < 1.0
    # heavier dispersion fattens the upper tail above the mean-ish line region
    assert R.negbin_sf(10.5, mu=6.0, alpha=0.60) > R.negbin_sf(10.5, mu=6.0, alpha=0.05)


def test_invert_dispersion_negbin_roundtrip():
    mu, alpha, line = 24.0, 0.08, 26.5
    p = R.negbin_sf(line, mu, alpha)
    rec = R.invert_dispersion("negbin", mu, line, p)
    assert rec == pytest.approx(alpha, rel=1e-3)


def test_invert_dispersion_normal_roundtrip():
    mu, sd, line = 22.0, 5.5, 24.5
    p = R.normal_sf(line, mu, sd)
    rec = R.invert_dispersion("normal", mu, line, p)
    assert rec == pytest.approx(sd, rel=1e-6)


def test_invert_dispersion_rejects_unidentifiable():
    assert R.invert_dispersion("negbin", 10.0, 10.5, 0.9995) is None
    assert R.invert_dispersion("normal", 10.0, 10.0, 0.40) is None
    assert R.invert_dispersion("negbin", None, 10.5, 0.4) is None


def test_side_prob_push_mass_belongs_to_neither_side():
    mu, alpha, line = 3.0, 0.20, 3.0      # integer line on a count stat
    po = R.dist_side_prob("negbin", mu, alpha, line, "over")
    pu = R.dist_side_prob("negbin", mu, alpha, line, "under")
    push = R.negbin_pmf_at(line, mu, alpha)
    assert po + pu + push == pytest.approx(1.0)
    assert po == pytest.approx(R.negbin_sf(line, mu, alpha))


def test_snap_ts_is_eastern_wall_clock():
    ts = R.snap_ts("2026-11-14", "props_1232.json")   # EST (UTC-5) on Nov 14
    assert ts == datetime(2026, 11, 14, 17, 32, tzinfo=timezone.utc)
    assert R.snap_ts("2026-11-14", "game_lines_1232.json") is None


def _payload():
    return [{
        "id": "evt1", "commence_time": "2026-11-14T18:00:00Z",
        "home_team": "Carolina Hurricanes", "away_team": "Seattle Kraken",
        "bookmakers": [
            {"key": "novig", "last_update": "2026-11-14T17:31:00Z", "markets": [
                {"key": "player_saves", "last_update": "2026-11-14T17:30:00Z", "outcomes": [
                    {"name": "Over Pyotr Kochetkov", "description": "Pyotr Kochetkov",
                     "price": -110, "point": 24.5},
                    {"name": "Under Pyotr Kochetkov", "description": "Pyotr Kochetkov",
                     "price": -110, "point": 24.5},
                    {"name": "Over Joey Daccord", "description": "Joey Daccord",
                     "price": -115, "point": 27.5},   # one-sided -> dropped
                ]},
            ]},
        ],
    }]


def test_parse_snapshot_two_sided_only_and_age():
    ts = datetime(2026, 11, 14, 17, 32, tzinfo=timezone.utc)
    snap = R.parse_snapshot(_payload(), ts, "nhl")
    assert snap.n_events == 1 and snap.n_one_sided == 1
    assert len(snap.quotes) == 1
    q = snap.quotes[0]
    assert q.book == "novig" and q.stat == "saves" and q.line == 24.5
    assert q.age_sec == 120.0
    assert q.hold == pytest.approx(2 * (1 / 1.9090909) - 1, abs=1e-3)


def test_peer_context_leave_one_out_and_age_filter():
    def q(book, over_am, under_am, age):
        return R.Quote(event_id="e", commence=datetime(2026, 11, 14, 18, tzinfo=timezone.utc),
                       home="H", away="A", book=book, stat="sog", player="P",
                       player_norm="p", line=2.5, over_am=over_am, under_am=under_am,
                       age_sec=age)
    quotes = [q("target", -110, -110, 60), q("pinnacle", 100, -120, 60),
              q("bovada", 120, -140, 60), q("stalebook", 150, -170, 3600)]
    pc = R.peer_context(quotes, "target")
    assert pc.n_peers == 2 and pc.n_sharp_peers == 1     # stalebook aged out (>25m)
    assert pc.peer_med_fair_over == pytest.approx(
        ((1 / 2.0) / (1 / 2.0 + 1 / 1.8333) + (1 / 2.2) / (1 / 2.2 + 1 / 1.7143)) / 2, abs=1e-3)


def test_clustered_mean_ci_two_clusters():
    m, lo, hi, G = R.clustered_mean_ci([1.0, 1.0, 3.0, 3.0], ["a", "a", "b", "b"])
    assert m == 2.0 and G == 2 and lo < 2.0 < hi


def test_roi_profit_drag_only_on_exchanges():
    assert R.roi_profit(-110, True, "draftkings") == pytest.approx(100 / 110, rel=1e-6)
    assert R.roi_profit(-110, False, "draftkings") == -1.0
    dragged = R.roi_profit(-110, True, "novig")
    assert dragged < 100 / 110 and dragged > 0.90
    assert R.roi_profit(-110, False, "novig") == -1.0


def test_modal_line_tiebreak():
    def q(book, line):
        return R.Quote(event_id="e", commence=datetime(2026, 11, 14, 18, tzinfo=timezone.utc),
                       home="H", away="A", book=book, stat="points", player="P",
                       player_norm="p", line=line, over_am=-110, under_am=-110, age_sec=0)
    assert R.modal_line([q("a", 24.5), q("b", 24.5), q("c", 25.5)]) == 24.5
    assert R.modal_line([q("a", 24.5), q("b", 25.5)]) == 24.5   # tie -> smaller |line|
