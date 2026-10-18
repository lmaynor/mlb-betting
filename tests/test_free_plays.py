"""mlb.runners.free_plays -- selection rules, candidate building, once-per-day state."""
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

import mlb.runners.free_plays as fp

NOW = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)
DAY = "2026-10-17"


def cand(text, score, sport="mlb", edge=0.1, key=None, basis="ev"):
    return dict(key=key or f"k|{text}|{sport}", sport=sport, text=text, game="A @ B", book="draftkings", odds=110,
                edge=edge, n_books=6, score=score, basis=basis)


def test_pick_takes_best_two_above_the_floor_only():
    out = fp.pick([cand("a", 72), cand("b", 90), cand("c", 69), cand("d", 71)], set(), 2)
    assert [c["text"] for c in out] == ["b", "a"]


def test_pick_returns_fewer_when_few_qualify_and_nothing_below_floor():
    assert [c["text"] for c in fp.pick([cand("a", 70), cand("b", 60)], set(), 2)] == ["a"]
    assert fp.pick([cand("a", 69)], set(), 2) == []


def test_pick_skips_already_posted_and_zero_slots():
    cs = [cand("a", 80, key="x"), cand("b", 75, key="y")]
    assert [c["text"] for c in fp.pick(cs, {"x"}, 1)] == ["b"]
    assert fp.pick(cs, set(), 0) == []


def test_same_proposition_at_two_books_is_one_play():
    out = fp.pick([cand("Judge HR", 85, key="1"), cand("Judge HR", 80, key="2"), cand("Ohtani HR", 75, key="3")], set(), 2)
    assert [c["text"] for c in out] == ["Judge HR", "Ohtani HR"]


def test_ties_break_on_edge():
    out = fp.pick([cand("lo", 80, edge=0.05), cand("hi", 80, edge=0.12)], set(), 1)
    assert out[0]["text"] == "hi"


def test_candidates_from_bets_scores_filters_and_labels():
    df = pd.DataFrame([
        dict(id=1, system="EV", sport="mlb", game_date=DAY, player="Pat One", away_team="NYY", home_team="BOS",
             bet_type="K_OVER_5.5_draftkings", model_prob=0.55, edge=0.12, odds=110, kelly_triggered=True,
             result=None, notes="soft-book +EV alert vs Pinnacle (9 books)", book="draftkings"),
        dict(id=2, system="EV", sport="mlb", game_date=DAY, player="Done", away_team="NYY", home_team="BOS",
             bet_type="HR_draftkings", model_prob=0.3, edge=0.2, odds=300, kelly_triggered=True,
             result="WIN", notes="(9 books)", book="draftkings"),                     # graded -> excluded
        dict(id=3, system="K", sport="mlb", game_date=DAY, player="NoEdge", away_team="NYY", home_team="BOS",
             bet_type="K_OVER_5.5", model_prob=0.5, edge=None, odds=-110, kelly_triggered=True,
             result=None, notes=None, book="fanduel"),                                  # no edge -> excluded
        dict(id=4, system="K", sport="mlb", game_date=DAY, player="Zero", away_team="NYY", home_team="BOS",
             bet_type="K_OVER_5.5", model_prob=0.5, edge=0.1, odds=-110, kelly_triggered=False,
             result=float("nan"), notes=None, book="fanduel"),                          # not kelly_triggered -> excluded
    ])
    out = fp.candidates_from_bets(df, DAY)
    assert [c["key"] for c in out] == ["bet|mlb|1"]
    c = out[0]
    assert c["basis"] == "ev" and c["n_books"] == 9 and c["score"] >= fp.MIN_SCORE
    assert c["text"] == "Pat One - K Over 5.5"            # book tag stripped from the +EV bet_type


def sweep_payload(**over):
    r = dict(game_key="nhl:BOS@NYR", player="Pat One", player_norm="pat one", stat="shots_on_goal", side="over", point=2.5,
             american=110, book="draftkings", ev=0.14, n_books=6, p_fair=0.5, flag=True,
             commence="2026-10-17T23:00:00+00:00")
    r.update(over)
    return {"ts": NOW.isoformat(), "rows": [r]}


def test_sweep_candidates_basic_and_label():
    out = fp.candidates_from_sweep("nhl", sweep_payload(), DAY, NOW)
    assert len(out) == 1 and out[0]["basis"] == "sweep" and out[0]["text"] == "Pat One Over 2.5 shots on goal"
    assert out[0]["score"] >= fp.MIN_SCORE
    assert fp._label(out[0]) == "paper / unvalidated"


def test_sweep_candidates_skip_stale_started_and_unflagged():
    old = dict(sweep_payload(), ts=datetime(2026, 10, 15, 18, tzinfo=timezone.utc).isoformat())
    assert fp.candidates_from_sweep("nhl", old, DAY, NOW) == []
    assert fp.candidates_from_sweep("nhl", sweep_payload(commence="2026-10-17T17:00:00+00:00"), DAY, NOW) == []
    assert fp.candidates_from_sweep("nhl", sweep_payload(flag=False), DAY, NOW) == []
    assert fp.candidates_from_sweep("nhl", {"ts": "garbage", "rows": []}, DAY, NOW) == []


def test_nfl_payload_uses_book_outliers_key():
    p = sweep_payload()
    out = fp.candidates_from_sweep("nfl", {"ts": p["ts"], "book_outliers": p["rows"]}, DAY, NOW)
    assert len(out) == 1 and out[0]["sport"] == "nfl"


@pytest.fixture
def store(monkeypatch):
    mem = {}
    from mlb_core import storage
    monkeypatch.setattr(storage, "exists", lambda k: k in mem)
    monkeypatch.setattr(storage, "read_bytes", lambda k: mem[k])
    monkeypatch.setattr(storage, "write_bytes", lambda data, k: mem.__setitem__(k, data))
    return mem


def test_run_posts_top_two_once_and_tops_up_never_replaces(monkeypatch, store):
    posted = []
    monkeypatch.setattr(fp, "post_play", lambda c, day: posted.append(c["text"]) or True)
    pool = [cand("a", 90, key="a"), cand("b", 80, key="b")]
    monkeypatch.setattr(fp, "load_candidates", lambda day, now: pool)
    assert fp.run(NOW)["posted"] == 2
    assert posted == ["a", "b"]
    assert fp.run(NOW)["status"] == "full"                       # nothing more today
    assert posted == ["a", "b"]


def test_run_with_one_qualifier_posts_one_then_tops_up_later(monkeypatch, store):
    posted = []
    monkeypatch.setattr(fp, "post_play", lambda c, day: posted.append(c["text"]) or True)
    pool = [cand("a", 90, key="a"), cand("weak", 50, key="w")]
    monkeypatch.setattr(fp, "load_candidates", lambda day, now: pool)
    assert fp.run(NOW)["posted"] == 1
    pool.append(cand("late", 85, key="l"))
    assert fp.run(NOW)["posted"] == 1                            # second slot filled by a later qualifier
    assert posted == ["a", "late"]
    assert json.loads(store[f"FreePlays/{DAY}/posted.json"])[0]["text"] == "a"


def test_undelivered_play_is_not_recorded_so_it_retries(monkeypatch, store):
    monkeypatch.setattr(fp, "post_play", lambda c, day: False)
    monkeypatch.setattr(fp, "load_candidates", lambda day, now: [cand("a", 90, key="a")])
    assert fp.run(NOW)["posted"] == 0
    assert f"FreePlays/{DAY}/posted.json" not in store


def test_dry_run_posts_and_persists_nothing(monkeypatch, store, capsys):
    monkeypatch.setattr(fp, "post_play", lambda c, day: pytest.fail("must not post"))
    monkeypatch.setattr(fp, "load_candidates", lambda day, now: [cand("a", 90, key="a")])
    fp.run(NOW, dry_run=True)
    assert "[dry-run]" in capsys.readouterr().out and not store


def test_embed_labels_and_routes_to_free_plays_only(monkeypatch):
    e = fp.embed_for(cand("Judge HR", 88), DAY)
    assert e["title"] == "FREE PLAY | MLB | Beezy 88 STRONG"
    assert "paper-tracked" in e["footer"]["text"]
    monkeypatch.delenv("DISCORD_WEBHOOK_FREE_PLAYS", raising=False)
    monkeypatch.delenv("BEEZY_BOT_URL", raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hook.test/paid-or-legacy")
    assert fp.post_play(cand("Judge HR", 88), DAY) is False       # never falls back to another channel's webhook
