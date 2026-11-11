"""Tests for the Arm T (text) minutes-restriction harness.

Mocked transports only -- zero live Decisions API calls. Covers: request
construction + decision-time blurb cutoff, response parsing, fail-closed
abstentions (timeout / HTTP error / malformed), calibration fit, band-mapped
math, kill-bar verdict boundaries, combiner shrinkage, the disabled-by-
default CLI, and the missing-API-key config path.
"""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from nba.minutes_restriction.arm_t import (
    Calibration,
    band_mapped_minutes,
    blurbs_for_case,
    classify_cases,
    combine,
    fit_calibration,
    load_arm_s_predictions,
    load_cases,
    load_prose_corpus,
    realized_band,
)
from nba.minutes_restriction.decisions_client import (
    BANDS,
    DecisionsBandClassifier,
    DecisionsConfigError,
    DecisionsTimeoutError,
)
from nba.minutes_restriction.grade_t import grade, verdict_for

DECISION_TS = "2025-01-10T17:00:00Z"


def make_case(**over):
    case = {
        "case_id": "c1",
        "player": "Paolo Banchero",
        "team": "ORL",
        "game_date": "2025-01-10",
        "tip_ts": "2025-01-11T00:00:00Z",
        "decision_report_ts": DECISION_TS,
        "designation": "Questionable",
        "reason": "Oblique",
        "games_missed": 34,
        "missed_band": "10+",
        "baseline_minutes": 34.0,
        "realized_minutes": 27.0,
        "realized_ratio": 27.0 / 34.0,
        "case_class": "return",
        "is_game_two": False,
    }
    case.update(over)
    return case


def corpus_df(rows):
    df = pd.DataFrame(rows)
    df["published_ts"] = pd.to_datetime(df["published_ts"], utc=True)
    return df


def good_payload(picked="light_restriction", probs=None, confidence=0.7):
    probs = probs or {
        "out": 0.05,
        "heavy_restriction": 0.25,
        "light_restriction": 0.45,
        "full_workload": 0.25,
    }
    return {
        "answers": [
            {
                "question_id": "minutes_band",
                "type": "choice",
                "picked": picked,
                "probabilities": probs,
                "confidence": confidence,
            }
        ]
    }


_UNSET = object()


class SpyTransport:
    def __init__(self, payload=_UNSET, status=200, exc=None):
        self.calls = []
        self.payload = good_payload() if payload is _UNSET else payload
        self.status = status
        self.exc = exc

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        return self.status, self.payload


def classifier(transport):
    return DecisionsBandClassifier(api_key="test-key-not-real", transport=transport)


# (a) request construction + cutoff -------------------------------------------

def test_one_call_per_case_choice_question_and_context():
    case = make_case()
    case["decision_report_ts"] = pd.Timestamp(DECISION_TS)
    corpus = corpus_df([
        {"player": "Paolo Banchero", "published_ts": "2025-01-09T10:00:00Z",
         "headline": "Practices fully", "body": "Went through a full practice."},
        {"player": "Paolo Banchero", "published_ts": "2025-01-10T17:01:00Z",
         "headline": "LATE", "body": "published one minute after decision time"},
        {"player": "Paolo Banchero", "published_ts": DECISION_TS,
         "headline": "AT-CUTOFF", "body": "published exactly at decision time"},
        {"player": "Joel Embiid", "published_ts": "2025-01-09T10:00:00Z",
         "headline": "OTHER", "body": "different player"},
    ])
    blurbs = blurbs_for_case(case, corpus)
    spy = SpyTransport()
    pred = classifier(spy).classify_case(case, blurbs)

    assert not pred.abstention
    assert len(spy.calls) == 1  # exactly one batched call per case
    call = spy.calls[0]
    assert call["timeout"] == 20.0  # hard timeout
    body = call["body"]
    assert body["model"] == "gemini-decision-1"
    (q,) = body["questions"]
    assert q["type"] == "choice"
    assert q["options"] == list(BANDS)
    ctx = body["context"]
    assert "Questionable" in ctx and "Oblique" in ctx  # designation + reason
    assert "Practices fully" in ctx                    # pre-decision blurb
    assert "AT-CUTOFF" in ctx                          # exactly at cutoff: in
    assert "LATE" not in ctx                           # 1 min after: excluded
    assert "OTHER" not in ctx                          # other player: excluded


def test_blurbs_oldest_first_and_capped_to_latest():
    case = make_case()
    case["decision_report_ts"] = pd.Timestamp(DECISION_TS)
    rows = [
        {"player": "Paolo Banchero", "published_ts": f"2025-01-0{d}T10:00:00Z",
         "headline": f"H{d}", "body": "b"}
        for d in range(1, 6)
    ]
    blurbs = blurbs_for_case(case, corpus_df(rows), max_blurbs=3)
    assert [b["headline"] for b in blurbs] == ["H3", "H4", "H5"]


# (b) response parsing ---------------------------------------------------------

def test_parse_distribution_and_pick():
    spy = SpyTransport()
    pred = classifier(spy).classify_case(make_case(), [])
    assert pred.band == "light_restriction"
    assert pred.probabilities["light_restriction"] == pytest.approx(0.45)
    assert pred.confidence == pytest.approx(0.7)
    assert not pred.abstention


@pytest.mark.parametrize("payload", [
    {"answers": [{"picked": "light_restriction"}]},                       # no distribution
    {"answers": [{"picked": "bogus", "probabilities": {b: 0.25 for b in BANDS}}]},
    {"answers": [{"picked": "out", "probabilities": {"out": 0.5}}]},       # partial dist
    {"answers": []},
    {"nonsense": True},
    "not-a-dict",
    None,
])
def test_malformed_responses_abstain(payload):
    pred = classifier(SpyTransport(payload=payload)).classify_case(make_case(), [])
    assert pred.abstention and pred.band is None
    assert pred.abstention_reason.startswith("malformed_response")


# (c) timeout / HTTP errors fail closed ----------------------------------------

def test_timeout_abstains_without_sleeping():
    # Simulated timeout: the transport raises immediately; the test never
    # actually waits 20s.
    spy = SpyTransport(exc=DecisionsTimeoutError("simulated"))
    pred = classifier(spy).classify_case(make_case(), [])
    assert pred.abstention and pred.abstention_reason == "timeout"


def test_http_error_abstains():
    pred = classifier(SpyTransport(status=500, payload={})).classify_case(make_case(), [])
    assert pred.abstention and pred.abstention_reason == "http_error: status 500"


def test_abstentions_counted_in_harness_output():
    cases = pd.DataFrame([make_case(case_id="a"), make_case(case_id="b")])
    cases["decision_report_ts"] = pd.to_datetime(cases["decision_report_ts"], utc=True)
    corpus = corpus_df([{"player": "Paolo Banchero", "published_ts": "2025-01-09T00:00:00Z",
                         "headline": "h", "body": "b"}])
    out = classify_cases(cases, corpus, classifier(SpyTransport(exc=DecisionsTimeoutError())))
    assert out["abstention"].all() and len(out) == 2


# (d) calibration fit ------------------------------------------------------------

def test_fit_calibration_hand_computed():
    dev = pd.DataFrame([
        make_case(case_id="1", realized_minutes=0.0, realized_ratio=0.0),     # realized out
        make_case(case_id="2", realized_minutes=15.0, realized_ratio=0.5),    # realized heavy
        make_case(case_id="3", realized_minutes=24.0, realized_ratio=0.8),    # realized light
        make_case(case_id="4", realized_minutes=30.0, realized_ratio=1.0),    # realized full
        make_case(case_id="5", realized_minutes=30.0, realized_ratio=1.0),    # realized full
    ])
    preds = pd.DataFrame([
        {"case_id": c, "band": b, "abstention": False}
        for c, b in [("1", "heavy_restriction"), ("2", "heavy_restriction"),
                     ("3", "light_restriction"), ("4", "full_workload"),
                     ("5", "full_workload")]
    ])
    cal = fit_calibration(dev, preds)
    # per predicted band mean realized ratio: heavy -> (0.0 + 0.5) / 2
    assert cal.mean_ratio_by_band["heavy_restriction"] == pytest.approx(0.25)
    assert cal.mean_ratio_by_band["light_restriction"] == pytest.approx(0.8)
    assert cal.mean_ratio_by_band["full_workload"] == pytest.approx(1.0)
    # realized-band base rates: 1/5 each out/heavy/light, 2/5 full
    assert cal.base_rates["out"] == pytest.approx(0.2)
    assert cal.base_rates["heavy_restriction"] == pytest.approx(0.2)
    assert cal.base_rates["light_restriction"] == pytest.approx(0.2)
    assert cal.base_rates["full_workload"] == pytest.approx(0.4)


def test_realized_band_edges():
    assert realized_band(0.0, 0.0) == "out"
    assert realized_band(0.69, 20.0) == "heavy_restriction"
    assert realized_band(0.7, 20.0) == "light_restriction"
    assert realized_band(0.9, 20.0) == "full_workload"


# (e) band-mapped math -----------------------------------------------------------

def test_band_mapped_minutes_expectation_and_point():
    cal = Calibration(
        mean_ratio_by_band={"out": 0.0, "heavy_restriction": 0.5,
                            "light_restriction": 0.8, "full_workload": 1.0},
        base_rates={b: 0.25 for b in BANDS},
    )
    probs = {"out": 0.1, "heavy_restriction": 0.2, "light_restriction": 0.3,
             "full_workload": 0.4}
    # E[ratio] = .1*0 + .2*.5 + .3*.8 + .4*1.0 = 0.74 -> 30 * .74 = 22.2
    assert band_mapped_minutes(30.0, probs, cal) == pytest.approx(22.2)
    # point variant: argmax band is full_workload -> 30 * 1.0
    assert band_mapped_minutes(30.0, probs, cal, point=True) == pytest.approx(30.0)


# (f) kill-bar verdict boundaries --------------------------------------------------

def test_verdict_boundaries():
    # exactly 10% relative + exactly 0.4 min at n=150, log-loss better -> PASS
    assert verdict_for(150, 0.10, 0.4, 1.0, 1.2) == "PASS"
    # 9.9% relative -> KILL
    assert verdict_for(150, 0.099, 0.5, 1.0, 1.2) == "KILL"
    # 0.39 min absolute -> KILL
    assert verdict_for(150, 0.20, 0.39, 1.0, 1.2) == "KILL"
    # n=149 -> INSUFFICIENT_N even with everything else passing
    assert verdict_for(149, 0.50, 2.0, 0.5, 1.2) == "INSUFFICIENT_N"
    # log-loss worse than base rates -> KILL even if MAE passes
    assert verdict_for(200, 0.30, 1.0, 1.3, 1.2) == "KILL"


def _synthetic_grade_inputs(n, t_prob_full):
    cases = pd.DataFrame([
        make_case(case_id=f"c{i}", realized_minutes=27.0, realized_ratio=0.9,
                  baseline_minutes=30.0)
        for i in range(n)
    ])
    arm_s = pd.DataFrame({"case_id": cases["case_id"], "pred_ratio": 1.0})
    t_preds = pd.DataFrame([
        {"case_id": f"c{i}", "band": "full_workload", "abstention": False,
         "confidence": 0.9,
         **{f"p_{b}": (t_prob_full if b == "full_workload" else (1 - t_prob_full) / 3)
            for b in BANDS}}
        for i in range(n)
    ])
    cal = Calibration(
        mean_ratio_by_band={"out": 0.0, "heavy_restriction": 0.5,
                            "light_restriction": 0.8, "full_workload": 0.9},
        base_rates={"out": 0.1, "heavy_restriction": 0.27,
                    "light_restriction": 0.27, "full_workload": 0.36},
    )
    return cases, arm_s, t_preds, cal


def test_grade_end_to_end_pass():
    cases, arm_s, t_preds, cal = _synthetic_grade_inputs(150, 0.97)
    report = grade(cases, arm_s, t_preds, cal, combiner_weight=0.5)
    # S predicts baseline (30) vs realized 27 -> MAE 3.0; T maps to 27 -> ~0
    assert report.n_shared == 150 and report.n_abstentions == 0
    assert report.s_mae == pytest.approx(3.0)
    # E[ratio] = .97*.9 + .01*0 + .01*.5 + .01*.8 = 0.886 -> 26.58 vs 27
    assert report.t_mae == pytest.approx(0.42)
    assert report.verdict == "PASS"
    # combiner at w=0.5: (30 + 26.58) / 2 = 28.29 vs 27
    assert report.combiner_mae == pytest.approx(1.29)


def test_grade_insufficient_n_and_abstention_accounting():
    cases, arm_s, t_preds, cal = _synthetic_grade_inputs(10, 0.97)
    t_preds.loc[0, "abstention"] = True
    report = grade(cases, arm_s, t_preds, cal)
    assert report.n_shared == 9 and report.n_abstentions == 1
    assert report.abstention_rate == pytest.approx(0.1)
    assert report.verdict == "INSUFFICIENT_N"


def test_grade_accepts_pred_minutes_arm_s():
    cases, arm_s, t_preds, cal = _synthetic_grade_inputs(3, 0.97)
    arm_s_min = pd.DataFrame({"case_id": arm_s["case_id"], "pred_minutes": 30.0})
    report = grade(cases, arm_s_min, t_preds, cal)
    assert report.s_mae == pytest.approx(3.0)


# (g) combiner shrinkage -----------------------------------------------------------

def test_combiner_endpoints():
    assert combine(28.0, 20.0, 0.0) == 28.0   # weight 0 -> S unchanged
    assert combine(28.0, 20.0, 1.0) == 20.0   # weight 1 -> T-mapped value
    assert combine(28.0, 20.0, 0.5) == 24.0
    with pytest.raises(ValueError):
        combine(28.0, 20.0, 1.5)


# (h) CLI disabled by default -------------------------------------------------------

def _load_runner():
    path = Path(__file__).resolve().parents[1] / "scripts" / "minutes_arm_t_backtest.py"
    spec = importlib.util.spec_from_file_location("minutes_arm_t_backtest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_refuses_without_execute_and_makes_no_http(capsys, monkeypatch):
    mod = _load_runner()
    # If anything tried to build a client / transport, this would explode.
    import nba.minutes_restriction.decisions_client as dc
    monkeypatch.setattr(dc, "urllib_transport",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("HTTP attempted")))
    rc = mod.main(["--dev-cases", "dev.parquet", "--cases", "test.parquet",
                   "--arm-s-dev", "s_dev.csv", "--arm-s", "s.csv",
                   "--prose", "corpus.jsonl", "--out", "outdir"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PLAN ONLY" in out and "--execute" in out


# (i) missing API key -----------------------------------------------------------------

def test_missing_api_key_raises_config_error_before_any_request(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    spy = SpyTransport()
    with pytest.raises(DecisionsConfigError, match="GEMINI_API_KEY"):
        DecisionsBandClassifier(transport=spy)
    assert spy.calls == []


def test_harness_records_abstentions_when_unconfigured():
    cases = pd.DataFrame([make_case()])
    cases["decision_report_ts"] = pd.to_datetime(cases["decision_report_ts"], utc=True)
    corpus = corpus_df([{"player": "Paolo Banchero", "published_ts": "2025-01-09T00:00:00Z",
                         "headline": "h", "body": "b"}])
    out = classify_cases(cases, corpus, classifier=None)
    assert out.iloc[0]["abstention"] and out.iloc[0]["abstention_reason"] == "not_configured"
    assert out.iloc[0]["band"] is None


# loaders ----------------------------------------------------------------------------

def test_loaders_roundtrip(tmp_path):
    cases = pd.DataFrame([make_case()])
    cases_path = tmp_path / "cases.csv"
    cases.to_csv(cases_path, index=False)
    loaded = load_cases(cases_path)
    assert loaded.iloc[0]["case_id"] == "c1"
    assert str(loaded.iloc[0]["decision_report_ts"].tz) is not None

    s_path = tmp_path / "s.csv"
    pd.DataFrame({"case_id": ["c1"], "pred_minutes": [28.0]}).to_csv(s_path, index=False)
    assert "pred_minutes" in load_arm_s_predictions(s_path).columns

    corpus_path = tmp_path / "corpus.jsonl"
    corpus_path.write_text(json.dumps(
        {"player": "Paolo Banchero", "published_ts": "2025-01-09T10:00:00Z",
         "headline": "h", "body": "b"}) + "\n")
    corpus = load_prose_corpus(corpus_path)
    assert corpus.iloc[0]["headline"] == "h"
