"""H1 goalie-pull residual-partition audit (Argon Nov 11, 2026): partition correctness from the
saves-row goalie's perspective, the encoded promotion bars, and the frozen-cohort guard.

SYNTHETIC FIXTURES ONLY. Nothing in this file reads, points at, or runs against the live D1
cohort; the guard tests use throwaway tmp_path directories whose names merely resemble the
live ledger layout, filled with synthetic rows written by the tests themselves.
"""
import gzip
import json
import math

import pytest

from nhl.props import saves_pull_audit as SPA


# -----------------------------------------------------------------------------------------------------------
# synthetic fixture builders (banked formats: NHL API PBP payload, trained payload, settled JSONL)
# -----------------------------------------------------------------------------------------------------------
AWAY_ID, HOME_ID = 10, 20          # synthetic team ids in the PBP payloads


def _goal(period, mmss, side, ptype="REG"):
    return {"typeDescKey": "goal",
            "periodDescriptor": {"number": period, "periodType": ptype},
            "timeInPeriod": mmss,
            "details": {"eventOwnerTeamId": AWAY_ID if side == "away" else HOME_ID}}


def _pbp(goals, date="2026-01-05"):
    """Raw NHL API PBP payload (the banked NHL/raw/pbp/{game_id}.json.gz shape)."""
    return {"id": 2025020001, "gameDate": date,
            "awayTeam": {"id": AWAY_ID, "abbrev": "NYR"}, "homeTeam": {"id": HOME_ID, "abbrev": "BOS"},
            "plays": list(goals)}


def _pred(game_key, player_id, team, opp, mu=28.0, alpha=0.05, **extra):
    row = {"player_id": player_id, "name": f"Goalie {player_id}", "team": team, "opp": opp,
           "date": game_key.split(":")[1], "game_key": game_key, "stat": "saves",
           "mu": mu, "alpha": alpha}
    row.update(extra)
    return row


def _settled(game_key, player_id, actual, mu=28.0, line=27.5, p_model=0.5):
    return {"game_key": game_key, "player_id": player_id, "player": f"Goalie {player_id}",
            "stat": "saves", "line": line, "version": "synthetic",
            "first": {"ts": "2026-01-05T18:00:00+00:00", "p_model": p_model, "p_mkt": 0.5,
                      "over_odds": -110, "under_odds": -110, "mu": mu},
            "last": {"ts": "2026-01-05T23:00:00+00:00", "p_model": p_model, "p_mkt": 0.5,
                     "over_odds": -110, "under_odds": -110, "mu": mu},
            "actual": actual, "void": False,
            "won_over": None if actual == line else float(actual > line),
            "settled_ts": "2026-01-06T05:00:00+00:00"}


GK = "nhl:2026-01-05:NYR@BOS"      # NYR away, BOS home


# -----------------------------------------------------------------------------------------------------------
# score at the mark, from banked PBP
# -----------------------------------------------------------------------------------------------------------
def test_score_at_mark_counts_goals_at_or_before_1500_of_p3_only():
    pbp = _pbp([
        _goal(1, "05:00", "home"),
        _goal(2, "10:00", "away"),
        _goal(3, "14:59", "home"),          # before the mark: counts
        _goal(3, "15:00", "home"),          # exactly at the mark: counts
        _goal(3, "15:01", "away"),          # after the mark: does not count
        _goal(3, "19:30", "away"),
    ])
    assert SPA.score_at_mark_from_pbp(pbp) == (1, 3)


def test_score_at_mark_excludes_shootout_and_handles_missing_teams():
    pbp = _pbp([_goal(3, "10:00", "home"), _goal(5, "00:00", "away", ptype="SO")])
    assert SPA.score_at_mark_from_pbp(pbp) == (0, 1)
    assert SPA.score_at_mark_from_pbp({"plays": []}) is None
    assert SPA.game_key_from_pbp(_pbp([])) == GK


def test_score_at_mark_from_flattened_shots_matches_pbp():
    rows = [  # nhl.data.flatten.flatten_shots grain for one game
        {"period": 1, "period_type": "REG", "time_sec": 300, "event": "goal", "is_home": True},
        {"period": 3, "period_type": "REG", "time_sec": 899, "event": "goal", "is_home": False},
        {"period": 3, "period_type": "REG", "time_sec": 901, "event": "goal", "is_home": True},
        {"period": 3, "period_type": "REG", "time_sec": 100, "event": "sog", "is_home": True},
    ]
    assert SPA.score_at_mark_from_shots(rows) == (1, 1)
    assert SPA.score_at_mark_from_shots([{"event": "goal", "period": 1}]) is None


# -----------------------------------------------------------------------------------------------------------
# the partition: GOALIE-PERSPECTIVE definition (Argon's nitpick; the wrong end must fail)
# -----------------------------------------------------------------------------------------------------------
def test_pull_state_is_leading_by_1_or_2_from_the_goalies_perspective():
    assert SPA.pull_state(3, 2) is True        # leading by 1
    assert SPA.pull_state(3, 1) is True        # leading by 2
    assert SPA.pull_state(4, 1) is False       # leading by 3: no pull pressure priced here
    assert SPA.pull_state(2, 2) is False       # tied
    assert SPA.pull_state(1, 2) is False       # TRAILING by 1: his team pulls, wrong end of the ice
    assert SPA.pull_state(0, 2) is False       # trailing by 2


def test_classify_row_home_and_away_goalies_are_mirror_images():
    score = (1, 3)                             # away 1, home 3 at the mark
    home_row = {"game_key": GK, "team": "BOS"}   # home goalie leads by 2 -> pull-state
    away_row = {"game_key": GK, "team": "NYR"}   # away goalie trails by 2 -> NOT pull-state
    assert SPA.classify_row(home_row, score) is True
    assert SPA.classify_row(away_row, score) is False
    # and flipped: away leads by 1
    assert SPA.classify_row(away_row, (3, 2)) is True
    assert SPA.classify_row(home_row, (3, 2)) is False
    # unknown score / unknown team -> unclassified, never "other"
    assert SPA.classify_row(home_row, None) is None
    assert SPA.classify_row({"game_key": GK, "team": "XXX"}, score) is None


# -----------------------------------------------------------------------------------------------------------
# verdict: both Argon bars, at / above / below
# -----------------------------------------------------------------------------------------------------------
def test_verdict_residual_bar_at_above_below():
    assert SPA.verdict(0.45, None)["promotes"] is True          # exactly at the bar promotes
    assert SPA.verdict(-0.45, None)["promotes"] is True         # magnitude: a -0.45 shift is a shift
    assert SPA.verdict(0.60, 0.0)["promotes"] is True
    assert SPA.verdict(0.449, None)["promotes"] is False
    assert SPA.verdict(0.449, None)["verdict"] == "NO_EFFECT"


def test_verdict_tail_bar_at_above_below():
    assert SPA.verdict(0.0, 0.0030)["promotes"] is True         # exactly at the bar promotes
    assert SPA.verdict(0.10, 0.0040)["promotes"] is True
    assert SPA.verdict(0.10, 0.0029)["promotes"] is False
    assert SPA.verdict(None, None)["promotes"] is False         # nothing measured -> no-effect
    v = SPA.verdict(0.10, 0.0010)                               # BOTH below: the (e) auto-kill
    assert v["promotes"] is False and v["residual_shift_hit"] is False and v["tail_logloss_hit"] is False


# -----------------------------------------------------------------------------------------------------------
# end-to-end audit on synthetic joined rows
# -----------------------------------------------------------------------------------------------------------
def _joined(rows_with_state):
    """rows_with_state: list of (in_pull_state, residual, actual, p_tail). Builds joined rows on
    two synthetic games (one pull-state for the BOS goalie, one not) via the real join path."""
    settled, preds = [], []
    for i, (in_pull, resid, actual, p_tail) in enumerate(rows_with_state):
        gk = GK if in_pull else "nhl:2026-01-06:NYR@BOS"
        mu = actual - resid
        settled.append(_settled(gk, 100 + i, actual, mu=mu))
        preds.append(_pred(gk, 100 + i, "BOS", "NYR", mu=mu, p_over_32_5=p_tail))
    return SPA.join_rows(settled, {(p["game_key"], p["player_id"]): p for p in preds})


def test_audit_shift_and_tail_diff_on_synthetic_rows():
    joined = _joined([
        (True, 2.0, 35, 0.40), (True, 1.0, 30, 0.40),        # pull: residuals +2, +1
        (False, 0.0, 28, 0.40), (False, 0.0, 20, 0.40),      # other: residuals 0, 0
    ])
    scores = {GK: (1, 3), "nhl:2026-01-06:NYR@BOS": (3, 3)}   # BOS leads by 2 in GK; tied in the other
    rep = SPA.audit(joined, scores)
    assert rep["n_pull"] == 2 and rep["n_other"] == 2 and rep["n_unclassified_no_pbp"] == 0
    assert rep["mean_signed_residual_pull"] == pytest.approx(1.5)
    assert rep["mean_signed_residual_other"] == pytest.approx(0.0)
    assert rep["mean_signed_residual_shift"] == pytest.approx(1.5)
    ll = lambda y, p: -(y * math.log(p) + (1 - y) * math.log(1 - p))
    expected_diff = (ll(1.0, 0.40) + ll(0.0, 0.40)) / 2 - (ll(0.0, 0.40) + ll(0.0, 0.40)) / 2
    assert rep["tail_logloss_diff"] == pytest.approx(expected_diff)
    assert rep["promotes"] is True                              # shift 1.5 >= 0.45


def test_audit_no_effect_and_unclassified_rows_are_not_other():
    joined = _joined([(True, 0.1, 30, 0.40), (False, 0.0, 28, 0.40)])
    joined.append({"game_key": "nhl:2026-01-07:NYR@BOS", "player_id": 999, "team": "BOS",
                   "mu": 28.0, "actual": 40.0, "residual": 12.0, "p_tail": 0.9, "line": 27.5,
                   "alpha": None, "p_model": None})           # no PBP for this game
    rep = SPA.audit(joined, {GK: (1, 3), "nhl:2026-01-06:NYR@BOS": (3, 3)})
    assert rep["n_unclassified_no_pbp"] == 1
    assert rep["n_pull"] == 1 and rep["n_other"] == 1           # the huge residual did NOT leak into either side
    assert rep["verdict"] == "NO_EFFECT"


def test_tail_prob_fallbacks():
    # explicit field wins
    assert SPA._tail_prob({"p_over_32_5": 0.31, "mu": 28.0, "alpha": 0.05}) == pytest.approx(0.31)
    # NegBin(mu, alpha) path
    p = SPA._tail_prob({"mu": 30.0, "alpha": 0.05})
    assert p is not None and 0.0 < p < 1.0
    # own line exactly 32.5 -> closing p_model
    assert SPA._tail_prob({"line": 32.5, "p_model": 0.47, "mu": None, "alpha": None}) == pytest.approx(0.47)
    # nothing usable -> None, never invented
    assert SPA._tail_prob({"line": 27.5, "p_model": 0.5, "mu": None, "alpha": None}) is None


# -----------------------------------------------------------------------------------------------------------
# the freeze guard: refusal without the override, and the override is explicit
# -----------------------------------------------------------------------------------------------------------
def test_guard_refuses_live_cohort_paths_before_any_read():
    live = "NHL/paper_track/settled/2026-12-01.jsonl"           # does not exist: refusal must come first
    assert SPA.is_live_cohort_path(live) is True
    assert SPA.is_live_cohort_path("gs://bucket/NHL/paper_track/settled/x.jsonl") is True
    assert SPA.is_live_cohort_path("/data/EVSweep/nhl_props/trained_latest.json") is True
    assert SPA.is_live_cohort_path("/tmp/synthetic/settled.jsonl") is False
    with pytest.raises(SPA.FrozenCohortError):
        SPA.assert_paths_allowed([live], override=False)
    SPA.assert_paths_allowed([live], override=True)             # the named override is the only way through
    SPA.assert_paths_allowed(["/tmp/synthetic/settled.jsonl"], override=False)


def test_cli_refuses_live_path_with_exit_2_and_no_override():
    rc = SPA.main(["--predictions", "preds.json",
                   "--settled", "NHL/paper_track/settled/2026-12-01.jsonl",
                   "--pbp", "pbp/"])
    assert rc == 2


def _write_synthetic_cohort(tmp_path):
    """A full synthetic cohort under a live-LOOKING layout inside tmp_path (synthetic rows only)."""
    settled_dir = tmp_path / "NHL" / "paper_track" / "settled"
    settled_dir.mkdir(parents=True)
    pbp_dir = tmp_path / "pbp"
    pbp_dir.mkdir()
    preds = [_pred(GK, 100, "BOS", "NYR", mu=28.0), _pred("nhl:2026-01-06:NYR@BOS", 101, "BOS", "NYR", mu=28.0)]
    (tmp_path / "preds.json").write_text(json.dumps({"rows": preds}))
    rows = [_settled(GK, 100, 31), _settled("nhl:2026-01-06:NYR@BOS", 101, 28)]
    (settled_dir / "2026-01-05.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with gzip.open(pbp_dir / "2025020001.json.gz", "wt") as fh:  # BOS leads 3-1 at the mark -> pull-state
        json.dump(_pbp([_goal(1, "05:00", "home"), _goal(2, "05:00", "home"),
                        _goal(2, "09:00", "away"), _goal(3, "10:00", "home")]), fh)
    (pbp_dir / "g2.json").write_text(json.dumps(_pbp([_goal(1, "05:00", "away")], date="2026-01-06")))
    return tmp_path / "preds.json", settled_dir / "2026-01-05.jsonl", pbp_dir


def test_cli_runs_on_synthetic_files_and_override_opens_lookalike_paths(tmp_path):
    preds, settled, pbp_dir = _write_synthetic_cohort(tmp_path)
    # without the override the lookalike live layout is refused even though the files are synthetic
    assert SPA.main(["--predictions", str(preds), "--settled", str(settled), "--pbp", str(pbp_dir)]) == 2
    # with the override it runs and partitions the two synthetic games
    out = tmp_path / "report.json"
    rc = SPA.main(["--predictions", str(preds), "--settled", str(settled), "--pbp", str(pbp_dir),
                   "--out", str(out), SPA.OVERRIDE_FLAG])
    assert rc == 0
    rep = json.loads(out.read_text())
    assert rep["n_pull"] == 1 and rep["n_other"] == 1
    assert rep["mean_signed_residual_pull"] == pytest.approx(3.0)   # actual 31 - mu 28
    assert rep["mean_signed_residual_other"] == pytest.approx(0.0)
    assert rep["verdict"] == "PROMOTE_TO_D1_FEATURE"


def test_cli_runs_on_plain_synthetic_paths_without_override(tmp_path):
    preds, settled, pbp_dir = _write_synthetic_cohort(tmp_path)
    plain = tmp_path / "plain_settled.jsonl"
    plain.write_text(settled.read_text())
    rc = SPA.main(["--predictions", str(preds), "--settled", str(plain), "--pbp", str(pbp_dir)])
    assert rc == 0
