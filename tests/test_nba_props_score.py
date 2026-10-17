"""nba.props.score: fixture-based scorer tests + refusal paths (stale history, stale snapshot, unmatched/ambiguous players).

tests/fixtures/odds/nba_props_sample.json is a SYNTHETIC ParlayAPI-shaped snapshot: no real NBA props payload existed to
capture on 2026-10-14 (nothing banked before then carries real props; OddsAccum/basketball_nba/historical/* holds game and
quarter totals plus a few preseason FanDuel rows in a different, flattened shape). It follows the documented shapes
(docs/solutions/integration-issues/parlayapi-nhl-nba-prop-shapes.md and the real NHL fixture): 'Over <player>' /
'Under <player>' outcomes, DFS apps at -112/-112, a team-total row inside a player market, a team-suffixed spelling, a
one-sided alternate ladder. Replace it with a captured payload once the banker has produced one. The model is a stub with
a fixed mean/variance per stat so the expected reasons are deterministic; the feature/identity/consensus plumbing around
it is the real code.
"""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nba.props import config as C
from nba.props import data as D
from nba.props import score as S
from nba.props.features import FeatureContext
from nba.props.identity import build_bridge
from tests.nba_props_helpers import make_masters

FIX = Path(__file__).parent / "fixtures" / "odds" / "nba_props_sample.json"
NOW = datetime(2025, 12, 16, 21, 30, tzinfo=timezone.utc)          # event 1 tips 3 h later (00:30Z = Dec 16, 7:30pm ET)

XWALK = pd.DataFrame([
    {"espn_id": 4065648, "full_name": "Jayson Tatum", "team": "BOS"},
    {"espn_id": 3917376, "full_name": "Jaylen Brown", "team": "BOS"},
    {"espn_id": 999001, "full_name": "Rookie Guy", "team": "BOS"},          # no SportsBlaze history -> bridge drops it
    {"espn_id": 1966, "full_name": "LeBron James", "team": "LAL"},
    {"espn_id": 4066457, "full_name": "Austin Reaves", "team": "LAL"},
    {"espn_id": 777001, "full_name": "Mo Williams", "team": "LAL"},          # two same-named players: ambiguous
    {"espn_id": 777002, "full_name": "Mo Williams", "team": "LAL"},
    {"espn_id": 3934672, "full_name": "Jalen Brunson", "team": "NYK"},
    {"espn_id": 3136195, "full_name": "Karl-Anthony Towns", "team": "NYK"},
    {"espn_id": 4066261, "full_name": "Bam Adebayo", "team": "MIA"},
    {"espn_id": 4395725, "full_name": "Tyler Herro", "team": "MIA"},
])


class Stub:
    """Fixed predictive distribution per stat (mean, variance, family) -- isolates the scorer from model fitting."""
    PARAMS = {"points": (17.0, 50.0), "rebounds": (5.0, 7.0), "assists": (3.2, 4.0), "threes": (1.2, 1.5),
              "pra": (25.0, 120.0)}

    def __init__(self, stat):
        self.stat, self.family = stat, "nbinom"

    def predict(self, X):
        mu, var = self.PARAMS[self.stat]
        return np.full(len(X), mu), np.full(len(X), var)


def make_resources(history_kwargs=None, models=None, xwalk=XWALK):
    pb, gm, tips = make_masters(dnp_rate=0.0, **(history_kwargs or {}))
    rows, tg = D.prepare(pb, gm, tips)
    ctx = FeatureContext(rows, tg)
    from nba.data import espn_players as ep
    return S.Resources(ctx, models or {s: Stub(s) for s in C.STATS}, build_bridge(rows, xwalk), ep.build_resolver(xwalk),
                       {int(r.espn_id): r.team for r in xwalk.itertuples(index=False)}, set(xwalk["team"]),
                       D.history_status(rows)), rows


@pytest.fixture(scope="module")
def world():
    res, rows = make_resources()
    events = json.load(open(FIX))
    events[2]["commence_time"] = (NOW + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    df, stats = S.score_events(events, NOW, res)
    return res, events, df, stats


def _row(df, player, stat, point, book):
    r = df[(df["player"] == player) & (df["stat"] == stat) & (df["point"] == point) & (df["book"] == book)]
    assert len(r) == 1, (player, stat, point, book, len(r))
    return r.iloc[0]


# ----------------------------------------------------------------------------- scoring + guards
def test_fixture_scores_and_flags_exactly_the_designed_rows(world):
    _, _, df, stats = world
    assert len(df) and stats["rows"] == len(df)
    flagged = df[df["flag"]]
    # the stub model (mean 17 / var 50 points; 3.2 assists) disagrees mildly with a 50/50 consensus on exactly these two
    # (player, stat, point) groups, and every guard passes: 6 books x Tatum points OVER, 5 books x Brown assists OVER
    got = set(zip(flagged["player"], flagged["stat"], flagged["point"], flagged["side"], flagged["book"]))
    assert got == ({("Jayson Tatum", "points", 15.5, "OVER", b) for b in
                    ("bet365", "caesars", "draftkings", "fanduel", "betmgm", "bovada")}
                   | {("Jaylen Brown", "assists", 2.5, "OVER", b) for b in
                      ("draftkings", "fanduel", "betmgm", "caesars", "bovada")})
    r = _row(df, "Jayson Tatum", "points", 15.5, "bet365")
    assert r["flag"] and r["reason"] == "FLAG" and r["side"] == "OVER" and r["american"] == 105
    assert 0.03 <= r["ev"] <= C.EV_CAP and r["n_books"] >= C.MIN_BOOKS
    assert r["edge_vs_cons"] == pytest.approx(r["p_model"] - r["p_fair"], abs=1e-3)
    assert r["system"] == S.SYSTEM and r["sport"] == "nba"


def test_each_guard_has_a_reason(world):
    _, _, df, _ = world
    reasons = df.groupby(["player", "stat"])["reason"].agg(lambda s: set(s)).to_dict()
    assert reasons[("Jayson Tatum", "rebounds")] == {"EV_TOO_HIGH"}          # model 0.62 under vs -110: +18% > cap
    assert reasons[("LeBron James", "threes")] == {"THIN_BOOKS"}             # only 2 two-sided books
    assert reasons[("Austin Reaves", "pra")] == {"HIGH_DISP"}                # books disagree with each other
    assert reasons[("Jalen Brunson", "points")] == {"NEAR_START"}            # event 3 tips in 10 minutes
    assert not df[df["player"] == "Jalen Brunson"]["flag"].any()


def test_dfs_books_team_totals_and_alt_ladders_never_appear(world):
    _, _, df, _ = world
    assert "underdog" not in set(df["book"])
    assert not df["player"].str.contains("Celtics").any()
    assert not df["alt"].any()                                                # alternate ladders are skipped entirely
    assert set(df["stat"]) <= set(C.STATS)


def test_spellings_merge_into_one_player_and_ids_are_espn_ids(world):
    """'Jaylen Brown (BOS)' (bovada) and 'Jaylen Brown' (everyone else) are ONE consensus group with ESPN id 3917376."""
    _, _, df, _ = world
    brown = df[(df["player"] == "Jaylen Brown") & (df["stat"] == "assists")]
    assert set(brown["player_id"]) == {3917376}
    assert set(brown["book"]) == {"draftkings", "fanduel", "betmgm", "caesars", "bovada"}
    assert brown["n_books"].nunique() == 1 and int(brown["n_books"].iloc[0]) == 5
    assert brown["sb_player_id"].nunique() == 1 and brown["sb_player_id"].iloc[0] == "BOS-1"


def test_unmatched_and_ambiguous_players_are_refused_not_guessed(world):
    _, _, df, stats = world
    assert "Mo Williams" not in set(df["player"])             # ambiguous namesake: dropped at parse
    assert "Rookie Guy" not in set(df["player"])              # resolved to an ESPN id but no SportsBlaze history
    assert "Zzyzx Unknown" not in set(df["player"])           # not on any roster
    assert stats["ambiguous_names"] >= 1
    assert stats["refused"].get("UNMATCHED_unknown", 0) >= 1                 # the rookie (bridge said unknown)
    assert stats["refused"].get("UNMATCHED_name_not_resolved", 0) >= 1       # Zzyzx
    assert stats["events_partial_crosswalk"] == 1                            # Suns/Nuggets teams not in the crosswalk


def test_partial_crosswalk_event_is_not_scored(world):
    _, events, df, _ = world
    assert "PHX" not in set(df["home"]) and "DEN" not in set(df["away"])


def test_evidence_schema_is_gradeable_by_ev_grade_props(world):
    """The sweep's grader works on these rows unchanged (first_sightings -> grade_frame -> summarize)."""
    from ev import grade_props as gp
    _, _, df, _ = world
    need = {"game_key", "player_norm", "stat", "point", "side", "book", "player_id", "american", "ev", "flag", "ts"}
    assert need <= set(df.columns)
    first = gp.first_sightings(df)
    assert len(first) == len(df)

    def provider(day, away, home):
        players = {int(p): {"points": 20.0, "rebounds": 4.0, "assists": 3.0, "threes": 1.0, "pra": 27.0, "_played": True}
                   for p in df["player_id"].unique()}
        return {"final": True, "players": players}

    g = gp.grade_frame(first, "nba", provider=provider)
    assert set(g["result"]) <= {"win", "loss", "push", "void", "ungradable"}
    s = gp.summarize(g)
    assert s["graded"] > 0 and s["verdict"].startswith("UNDETERMINED")


# ----------------------------------------------------------------------------- refusals
def test_stale_history_refuses_to_score():
    res, _ = make_resources(history_kwargs={"start": "2025-10-01", "n_days": 20})          # newest game ~Oct 20
    events = json.load(open(FIX))[:1]
    df, stats = S.score_events(events, NOW, res)
    assert df.empty and stats["rows"] == 0
    assert any(k.startswith("STALE_HISTORY") for k in stats["refused"]) and sum(stats["refused"].values()) > 0


def test_no_history_refuses():
    res, _ = make_resources()
    res.history = {"n_rows": 0, "last_date": None, "last_season": None}
    df, stats = S.score_events(json.load(open(FIX))[:1], NOW, res)
    assert df.empty and "NO_HISTORY" in stats["refused"]


def test_check_history_rules():
    ok, roll, why = S.check_history({"n_rows": 5, "last_date": "2026-06-10", "last_season": 2025}, pd.Timestamp("2026-06-12"))
    assert ok and not roll and why is None
    ok, roll, why = S.check_history({"n_rows": 5, "last_date": "2026-06-10", "last_season": 2025}, pd.Timestamp("2026-10-23"))
    assert not ok and why.startswith("STALE_HISTORY")
    ok, roll, why = S.check_history({"n_rows": 5, "last_date": "2026-06-10", "last_season": 2025}, pd.Timestamp("2026-10-23"),
                                    allow_rollover=True)
    assert ok and roll
    far, _, why = S.check_history({"n_rows": 5, "last_date": "2026-01-10", "last_season": 2025}, pd.Timestamp("2026-10-23"),
                                  allow_rollover=True)
    assert not far and why.startswith("STALE_HISTORY")                         # even opt-in has a ceiling
    same_season, _, why = S.check_history({"n_rows": 5, "last_date": "2026-02-01", "last_season": 2025},
                                          pd.Timestamp("2026-03-20"), allow_rollover=True)
    assert not same_season                                                      # a mid-season gap is NOT a rollover


def test_rollover_rows_are_scored_but_never_flagged():
    res, _ = make_resources(history_kwargs={"start": "2026-05-01", "n_days": 40, "season_year": 2025})
    events = json.load(open(FIX))[:1]
    events[0]["commence_time"] = "2026-10-23T23:30:00Z"
    now = datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc)
    refused, st0 = S.score_events(copy.deepcopy(events), now, res)
    assert refused.empty and any(k.startswith("STALE_HISTORY") for k in st0["refused"])
    df, st = S.score_events(copy.deepcopy(events), now, res, allow_rollover=True)
    assert len(df) and not df["flag"].any() and set(df["reason"]) == {"ROLLOVER_HISTORY"} and df["rollover"].all()


def test_stale_snapshot_is_refused_before_scoring(tmp_path, monkeypatch):
    """run_once reads the bank through ev.sweep_props_alerts.read_latest_props, which refuses a snapshot > max age old."""
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    res, _ = make_resources()
    events = json.load(open(FIX))
    base = tmp_path / "OddsAccum" / "basketball_nba"
    (base / "raw" / "2025-12-16").mkdir(parents=True)
    (base / "raw" / "2025-12-16" / "props_1600.json").write_text(json.dumps(events))
    ptr = {"timestamp": (NOW - timedelta(minutes=120)).isoformat(), "date": "2025-12-16", "hhmm": "1600",
           "events_priced": 3}
    (base / "latest_props.json").write_text(json.dumps(ptr))
    out = S.run_once(res, NOW)
    assert out["status"] == "skipped" and "stale" in out["reason"]
    ptr["timestamp"] = (NOW - timedelta(minutes=5)).isoformat()
    (base / "latest_props.json").write_text(json.dumps(ptr))
    out = S.run_once(res, NOW, out_dir=str(tmp_path / "evidence"))
    assert out["status"] == "ok" and out["rows"] > 0 and out["validated"] is False
    written = list((tmp_path / "evidence").rglob("model_props_*.csv"))
    assert len(written) == 1 and len(pd.read_csv(written[0])) == out["rows"]


def test_missing_pointer_is_skipped_not_guessed(tmp_path, monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    res, _ = make_resources()
    assert S.run_once(res, NOW)["status"] == "skipped"


# ----------------------------------------------------------------------------- minutes uncertainty + math
def _f(**kw):
    base = {"n_played": 40.0, "min_std10": 3.0, "missed_l10": 0.0, "team_games_since_last": 0.0,
            "min_ratio_l5_l20": 1.0, "team_changed": 0.0}
    base.update(kw)
    return pd.Series(base)


def test_abstain_rules_cover_each_unstable_minutes_situation():
    assert S.abstain_reason(_f()) is None
    assert S.abstain_reason(_f(n_played=3)) == "THIN_HISTORY"
    assert S.abstain_reason(_f(min_std10=9.0)) == "VOLATILE_MINUTES"
    assert S.abstain_reason(_f(missed_l10=4.0)) == "RECENT_ABSENCES"
    assert S.abstain_reason(_f(team_games_since_last=6.0)) == "RETURNING_FROM_ABSENCE"
    assert S.abstain_reason(_f(min_ratio_l5_l20=1.5)) == "MINUTES_TREND_SHIFT"
    assert S.abstain_reason(_f(min_ratio_l5_l20=0.5)) == "MINUTES_TREND_SHIFT"
    assert S.abstain_reason(_f(team_changed=1.0)) == "NEW_TEAM"
    assert S.abstain_reason(pd.Series({"n_played": np.nan})) == "THIN_HISTORY"


def test_abstained_player_is_scored_but_never_flagged():
    """A player the scorer abstains on appears in the evidence (for the forward evaluation) with flag=False."""
    res, _ = make_resources()
    real = res.ctx.features

    def volatile(q):
        f = real(q)
        f["min_std10"] = 12.0
        return f

    res.ctx.features = volatile
    df, _ = S.score_events(json.load(open(FIX))[:1], NOW, res)
    assert len(df) and not df["flag"].any()
    assert set(df["reason"]) <= {"ABSTAIN_VOLATILE_MINUTES"}


def test_outcome_probs_sum_to_one_and_whole_lines_have_a_push():
    po, pu, pp = S.outcome_probs(16.5, 17.0, 50.0, "nbinom")
    assert pp == 0.0 and po + pu == pytest.approx(1.0)
    po, pu, pp = S.outcome_probs(17.0, 17.0, 50.0, "nbinom")
    assert pp > 0.03 and po + pu + pp == pytest.approx(1.0)
    assert S.ev_at(0.5, 0.0, -110) == pytest.approx(0.5 * (1 + 100 / 110) - 1)
    assert S.ev_at(0.5, 0.2, 100) == pytest.approx(0.5 * 1.0 - 0.3)


def test_scorer_never_touches_discord_or_the_bets_table(monkeypatch):
    """LOG-ONLY: any attempt to POST (a Discord webhook) fails the test, and the module cannot reach the bet tracker."""
    import requests
    import ev.sweep_props_alerts as sw

    def boom(*a, **k):
        raise AssertionError("scorer attempted a network write")

    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests.Session, "request", boom)
    monkeypatch.setattr(sw, "post_discord", boom)
    res, _ = make_resources()
    df, _ = S.score_events(json.load(open(FIX)), NOW, res)
    assert len(df)
    src = Path(S.__file__).read_text()
    for banned in ("BetTracker", "bet_tracker", "log_bet", "post_discord", "webhook", "DISCORD_WEBHOOK"):
        assert banned not in src.replace("NEVER posts to Discord", ""), banned


def test_check_history_auto_rollover_only_in_opening_window():
    hist = {"n_rows": 5, "last_date": "2026-06-13", "last_season": 2025}
    ok, roll, why = S.check_history(hist, pd.Timestamp("2026-10-20"), allow_rollover="auto")      # Opening Night
    assert ok and roll and why is None
    ok, roll, why = S.check_history(hist, pd.Timestamp("2026-10-20"))                             # direct default: unchanged
    assert not ok and "STALE_HISTORY" in why
    ok, _, why = S.check_history(hist, pd.Timestamp("2026-11-20"), allow_rollover="auto")        # past the window
    assert not ok and "STALE_HISTORY" in why
    ok, _, why = S.check_history({"n_rows": 5, "last_date": "2026-03-01", "last_season": 2025}, pd.Timestamp("2026-10-22"),
                                 allow_rollover="auto")                                           # gap > ROLLOVER_MAX_GAP_DAYS
    assert not ok and "STALE_HISTORY" in why
    assert S.in_rollover_window(pd.Timestamp("2026-10-20")) and not S.in_rollover_window(pd.Timestamp("2026-07-01"))
