"""N6 weather variant of the 2D Kalman totals model (nfl.game.weather_totals), offline on fixtures:
vector construction (indoor / observed / missing), the missing-weather fallback (degrade to the base model,
never zero-fill), the dev-only fit with the precipitation coverage gate, and the with-vs-without scoring
on an identical holdout frame."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nfl.game import weather_totals as wt


# ---------------------------------------------------------------------------------------------------- fixtures
def _schedules(rows):
    return pd.DataFrame(rows, columns=["game_id", "season", "roof", "temp", "wind"])


def _fixture(seed=7):
    """Records + schedules where realised totals genuinely fall with wind and cold, so a correct weather
    term must help out of sample. Base p_over/p_under are priced off the base mu with the engine's own
    score matrix, exactly as the replay records them."""
    rng = np.random.default_rng(seed)
    rec_rows, sch_rows = [], []

    def add(season, i, roof, temp, wind):
        gid = f"{season}_{i:03d}"
        sch_rows.append(dict(game_id=gid, season=season, roof=roof, temp=temp, wind=wind))
        v = wt.vector_from_schedule(roof, temp, wind)
        mu = 46.0
        true_mu = mu
        if v.complete:
            d = wt.design_row({"wx_status": v.status, "temp_f": v.temp_f, "wind_mph": v.wind_mph,
                               "precip_mm": v.precip_mm, "is_dome": v.is_dome})
            true_mu = mu - 0.45 * d["wind_mph"] - 0.12 * d["cold_deg"] + 1.0 * d["is_dome"]
        total = float(np.round(true_mu + rng.normal(0, 9)))
        line = 45.5
        p_over = wt._p_over_from_mu(0.0, mu, line)
        rec_rows.append(dict(game_id=gid, season=season, week=8, mu_margin=0.0, mu_total=mu,
                             p_over=p_over, p_under=1.0 - p_over, mkt_total=line, total=total))

    i = 0
    for season in (2021, 2022, 2023):                       # dev: 90 games, all complete vectors
        for k in range(30):
            i += 1
            if k % 3 == 0:
                add(season, i, "dome", np.nan, np.nan)
            else:   # temp and wind drawn independently, or the OLS cannot separate their signs
                add(season, i, "outdoors", float(rng.uniform(25, 90)), float(rng.uniform(0, 22)))
    for season in (2024, 2025):                             # held: 40 games, one with missing weather
        for k in range(20):
            i += 1
            if season == 2025 and k == 0:
                add(season, i, "outdoors", np.nan, np.nan)
            elif k % 3 == 0:
                add(season, i, "closed", np.nan, np.nan)
            else:
                add(season, i, "outdoors", float(rng.uniform(25, 90)), float(rng.uniform(0, 22)))
    return pd.DataFrame(rec_rows), _schedules(sch_rows)


# ---------------------------------------------------------------------------------------------------- vector construction
def test_indoor_vector_is_neutral_and_complete():
    for roof in ("dome", "closed", "retractable"):
        v = wt.vector_from_schedule(roof, np.nan, np.nan)
        assert v.is_dome and v.complete and v.status == "indoor"
        assert v.temp_f == 72.0 and v.wind_mph == 0.0 and v.precip_mm == 0.0


def test_outdoor_vector_keeps_observed_values_and_transforms():
    v = wt.vector_from_schedule("outdoors", 35.0, 18.0)
    assert not v.is_dome and v.complete and v.status == "full"
    assert v.temp_f == 35.0 and v.wind_mph == 18.0 and v.precip_mm is None   # schedules carry no precip
    d = wt.design_row({"wx_status": v.status, "temp_f": v.temp_f, "wind_mph": v.wind_mph,
                       "precip_mm": v.precip_mm, "is_dome": v.is_dome})
    assert d["wind_mph"] == 18.0 and d["cold_deg"] == 15.0 and d["heat_deg"] == 0.0 and d["is_dome"] == 0.0
    hot = wt.vector_from_schedule("outdoors", 91.0, 3.0)
    d = wt.design_row({"wx_status": hot.status, "temp_f": hot.temp_f, "wind_mph": hot.wind_mph,
                       "precip_mm": hot.precip_mm, "is_dome": hot.is_dome})
    assert d["heat_deg"] == 11.0 and d["cold_deg"] == 0.0


def test_missing_outdoor_weather_is_never_filled():
    for temp, wind in ((np.nan, np.nan), (40.0, np.nan), (np.nan, 12.0), (None, None)):
        v = wt.vector_from_schedule("outdoors", temp, wind)
        assert v.status == "missing" and not v.complete
        assert v.temp_f is None and v.wind_mph is None and v.precip_mm is None   # NOT 0 / 72 neutral-filled
        assert wt.design_row({"wx_status": v.status, "temp_f": v.temp_f, "wind_mph": v.wind_mph,
                              "precip_mm": v.precip_mm, "is_dome": v.is_dome}) is None


def test_weather_frame_requires_the_schedule_columns():
    with pytest.raises(ValueError):
        wt.weather_frame(pd.DataFrame({"game_id": ["x"], "roof": ["dome"]}))


# ---------------------------------------------------------------------------------------------------- fallback
def test_missing_weather_degrades_to_the_base_model_exactly():
    adj = wt.WeatherAdjustment(coef={"intercept": -1.0, "wind_mph": -0.5, "cold_deg": -0.1,
                                     "heat_deg": 0.0, "is_dome": 2.0},
                               active_features=list(wt.BASE_FEATURES), n_fit=100, precip_outdoor_coverage=0.0)
    missing = {"wx_status": "missing", "temp_f": None, "wind_mph": None, "precip_mm": None, "is_dome": False}
    assert adj.apply(missing, 47.5) == 47.5                       # bit-for-bit the base model
    windy = {"wx_status": "full", "temp_f": 30.0, "wind_mph": 20.0, "precip_mm": None, "is_dome": False}
    assert adj.apply(windy, 47.5) == pytest.approx(47.5 - 1.0 - 10.0 - 2.0)
    dome = {"wx_status": "indoor", "temp_f": 72.0, "wind_mph": 0.0, "precip_mm": 0.0, "is_dome": True}
    assert adj.apply(dome, 47.5) == pytest.approx(47.5 - 1.0 + 2.0)


# ---------------------------------------------------------------------------------------------------- fit + scoring
def test_fit_is_dev_only_and_precip_stays_inactive_without_coverage():
    rec, sch = _fixture()
    joined = wt._join(rec, sch)
    dev = wt._totals_frame(joined[joined["season"].isin(wt.NFL_DEV_SEASONS)])
    adj = wt.fit_adjustment(dev)
    assert adj.n_fit == 90
    assert "precip_mm" not in adj.active_features and adj.precip_outdoor_coverage == 0.0
    assert adj.coef["wind_mph"] < 0 and adj.coef["cold_deg"] < 0      # recovers the fixture's true signs
    res = wt.score_variant(rec, sch, "with")
    assert res["fit"]["coef"] == adj.coef                            # the scored fit IS the dev-only fit


def test_with_vs_without_on_the_identical_holdout():
    rec, sch = _fixture()
    without = wt.score_variant(rec, sch, "without")
    with_ = wt.score_variant(rec, sch, "with")
    assert without["n"] == with_["n"] == 40                          # identical holdout rows
    assert without["n_adjusted"] == 0 and with_["n_adjusted"] == 39  # only the missing-weather game is untouched
    assert with_["coverage"] == {"indoor": 13, "full": 26, "missing": 1}
    assert with_["log_loss"] < without["log_loss"]                   # weather helps where the fixture says it must
    assert with_["mae_total"] < without["mae_total"]
    with pytest.raises(ValueError):
        wt.score_variant(rec, sch, "sideways")


def test_without_variant_is_the_base_records_scoring():
    rec, sch = _fixture()
    held = rec[rec["season"].isin(wt.NFL_HELD_SEASONS)]
    held = held[held["total"] != held["mkt_total"]]
    p = (held["p_over"] / (held["p_over"] + held["p_under"])).to_numpy()
    y = (held["total"] > held["mkt_total"]).astype(float).to_numpy()
    ll = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    res = wt.score_variant(rec, sch, "without")
    assert res["log_loss"] == pytest.approx(ll)                      # the harness does not move the base model
    assert res["mae_total"] == pytest.approx(float(np.mean(np.abs(held["mu_total"] - held["total"]))))


def test_cli_scores_a_variant_end_to_end(tmp_path, monkeypatch):
    rec, sch = _fixture()
    csv = tmp_path / "rec.csv"
    rec.to_csv(csv, index=False)
    monkeypatch.setattr("nfl.data.read.load", lambda *a, **k: sch)
    out = tmp_path / "with.json"
    assert wt.main(["--records", str(csv), "--variant", "with", "--out", str(out)]) == 0
    import json
    res = json.loads(out.read_text())
    assert res["variant"] == "with" and res["n"] == 40 and "fit" in res
