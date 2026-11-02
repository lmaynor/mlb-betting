"""scripts/nba_engine_vs_market.py: the as-of cut, the identity join and the walk-forward machinery, on SYNTHETIC masters.

The important one is `test_scrambling_games_on_or_after_the_target_does_not_change_the_price`: price a player-game, rewrite every
box score dated on/after that game with garbage, and the engine's mu and P(over) (all six variants, all five stats) must not move.
A positive control (garbling the player's own PRIOR games) must move it, so the test cannot pass vacuously.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ev import daily_projections as DP
from nhl.props import market as M
from scripts import nba_engine_vs_market as N
from tests.nba_props_helpers import make_masters

STATS5 = ("points", "rebounds", "assists", "threes", "pra")
NUM_COLS = ("points", "rebounds", "assists", "three_points_made", "three_points_attempted", "field_goals_made",
            "field_goals_attempted", "free_throws_made", "free_throws_attempted", "steals", "blocks", "turnovers")


def _setup(pb):
    index, dnp = N.build_index(pb)
    return N.Replayer(index, dnp), index


def _garble(pb: pd.DataFrame, mask, seed: int = 9, flip_played: bool = False) -> pd.DataFrame:
    """Copy of the master with every numeric box column of the masked PLAYED rows replaced by garbage."""
    out = pb.copy()
    rng = np.random.default_rng(seed)
    hit = mask & out["played"].astype(bool)
    n = int(hit.sum())
    for c in NUM_COLS:
        out.loc[hit, c] = rng.integers(0, 70, n)
    out.loc[hit, "minutes"] = [f"{m}:00" for m in rng.integers(30, 47, n)]
    if flip_played:
        out.loc[mask, "played"] = ~out.loc[mask, "played"].astype(bool)
    return out


def _price(R, index, name: str, j: int) -> dict:
    d = index[name][j]["_date"]
    props = [(i, s, 10.5 if s != "threes" else 1.5) for i, s in enumerate(STATS5)]
    out = R.price_block(d[:7], [(name, j, props)])
    return {(i, v): (mu, p) for i, v, mu, p in out}


@pytest.fixture(scope="module")
def masters():
    return make_masters(n_days=45, start="2025-11-01", seed=3)


def _target(index, name="jayson tatum"):
    rows = index[name]
    j = next(k for k in range(len(rows) - 1, -1, -1) if rows[k]["_date"] >= "2025-12-05")   # a December game: the month block is November
    j = max(j - 3, 0)
    assert rows[j]["_date"] >= "2025-12-01"
    return name, j, rows[j]["_date"]


def test_scrambling_games_on_or_after_the_target_does_not_change_the_price(masters):
    pb = masters[0]
    R, index = _setup(pb)
    name, j, d = _target(index)
    base = _price(R, index, name, j)
    assert len(base) == len(STATS5) * len(N.VARIANTS), "every stat x variant must be priced"
    late = pb["date"].astype(str) >= d
    assert late.any() and (~late).any()
    R2, index2 = _setup(_garble(pb, late))
    j2 = next(k for k, r in enumerate(index2[name]) if r["_date"] == d)
    assert j2 == j                                                   # same prior history, same position
    again = _price(R2, index2, name, j2)
    assert base.keys() == again.keys()
    for k in base:
        assert base[k] == pytest.approx(again[k], rel=0, abs=1e-12), k
    # strictly-later games: even their `played` flags (the oracle's input) must not matter
    later = pb["date"].astype(str) > d
    R3, index3 = _setup(_garble(pb, later, flip_played=True))
    j3 = next(k for k, r in enumerate(index3[name]) if r["_date"] == d)
    for k, v in _price(R3, index3, name, j3).items():
        assert v == pytest.approx(base[k], rel=0, abs=1e-12), k


def test_noabs_variants_ignore_the_box_score_of_the_game_day(masters):
    """Oracle variants read who sat out THAT game (by design); the no-absence variants must not."""
    pb = masters[0]
    R, index = _setup(pb)
    name, j, d = _target(index)
    base = _price(R, index, name, j)
    day = pb["date"].astype(str) == d
    pb2 = _garble(pb, day, flip_played=True)
    pb2.loc[(pb2["name"] == "Jayson Tatum") & day, "played"] = True      # keep the target himself in the sample
    R2, index2 = _setup(pb2)
    j2 = next(k for k, r in enumerate(index2[name]) if r["_date"] == d)
    again = _price(R2, index2, name, j2)
    for (i, v), val in base.items():
        if v.endswith("_noabs"):
            assert again[(i, v)] == pytest.approx(val, rel=0, abs=1e-12), (i, v)


def test_garbling_the_players_prior_games_moves_the_price(masters):
    pb = masters[0]
    R, index = _setup(pb)
    name, j, d = _target(index)
    base = _price(R, index, name, j)
    mask = (pb["name"] == "Jayson Tatum") & (pb["date"].astype(str) < d)
    R2, index2 = _setup(_garble(pb, mask))
    j2 = next(k for k, r in enumerate(index2[name]) if r["_date"] == d)
    again = _price(R2, index2, name, j2)
    moved = sum(abs(again[k][0] - base[k][0]) > 1e-6 for k in base)
    assert moved >= len(base) // 2


def test_shipped_switches_are_read_from_the_engine(monkeypatch, masters):
    R, _ = _setup(masters[0])
    assert R.switches == dict(DP.NBA_FEATURES)
    assert R.switches["eta_scale"] == 0.4 and R.switches["shot_gamma"] == 0.5      # the shipped defaults this report scores


def test_live_variant_has_no_opponent_information(masters):
    """ship_live must differ from ship (opponent + team-makes factors) while old / noabs structure is untouched."""
    R, index = _setup(masters[0])
    name, j, d = _target(index)
    got = _price(R, index, name, j)
    diff = sum(abs(got[(i, "ship")][0] - got[(i, "ship_live")][0]) > 1e-9 for i in range(len(STATS5)))
    assert diff >= 1


# ----------------------------------------------------------------------------------------------------------------- join
def _cons_row(player, stat, scheduled, away, home, **kw):
    r = {"date": scheduled[:10], "game_date": scheduled[:10], "sport": "NBA", "event_id": 1, "player_id": 7, "player": player,
         "player_norm": player.lower(), "team": "FA", "away": away, "home": home, "scheduled": scheduled, "stat": stat,
         "line": 10.5, "p_over_cons": 0.5, "n_books": 5, "disp": 0.01, "best_over_odds": -105.0, "best_under_odds": -105.0,
         "novig_over_odds": np.nan, "novig_under_odds": np.nan, "updated_max": scheduled, "pregame": True}
    r.update(kw)
    return r


def test_join_is_fail_closed_and_balances():
    pb, gm, tips = make_masters(n_days=12, start="2025-11-01", seed=5, skip={"BOS-3": set(range(0, 100))})
    pb = pb.copy()
    pb.loc[pb["player_id"] == "MIA-4", "name"] = pb.loc[pb["player_id"] == "BOS-4", "name"].iloc[0]       # a namesake pair
    index, dnp = N.build_index(pb)
    late = gm[gm["away_abbr"] == "NY"].iloc[0]                                  # a 22:30 ET tip: its UTC date is the NEXT day
    sched = pd.Timestamp(tips[late["game_id"]]).strftime("%Y-%m-%d %H:%M:%S")
    away, home = N.canon(late["away_abbr"]), N.canon(late["home_abbr"])
    row = pb[(pb["game_id"] == late["game_id"]) & (pb["player_id"] == "NY-0") & pb["played"]]
    assert len(row) == 1
    name, pts = row["name"].iloc[0], float(row["points"].iloc[0])
    et_day = (pd.Timestamp(sched, tz="UTC").tz_convert("America/New_York")).strftime("%Y-%m-%d")
    assert et_day != sched[:10], "fixture must exercise the UTC-vs-ET date trap"
    dnp_name = pb[pb["player_id"] == "BOS-3"]["name"].iloc[0]
    rows = [
        _cons_row(name, "pts", sched, away, home, game_date=et_day, event_id=11),                    # good (wrong-ET-day tolerant: joins on `scheduled`)
        _cons_row(name, "reb", sched, away, home, event_id=11, line=3.5),                              # good, other stat
        _cons_row(name, "ast", sched, "MIA", "LAL", event_id=11),                                      # team mismatch
        _cons_row("Zzz Nobody", "pts", sched, away, home, event_id=11),                                # unknown player
        _cons_row(name, "threes", sched, away, home, event_id=11, n_books=2),                          # low books
        _cons_row(name, "pra", sched, away, home, event_id=11, pregame=False),                         # postgame quote
        _cons_row(name, "pts", sched, away, home, event_id=12),                                        # duplicate of the first (same player/date/stat)
    ]
    # a player who did not play the day (void prop) and the namesake pair
    bos = gm[(gm["home_abbr"] == "LAL") | (gm["away_abbr"] == "BOS")].iloc[0]
    sb = pd.Timestamp(tips[bos["game_id"]]).strftime("%Y-%m-%d %H:%M:%S")
    rows.append(_cons_row(dnp_name, "pts", sb, N.canon(bos["away_abbr"]), N.canon(bos["home_abbr"]), event_id=21))
    rows.append(_cons_row(pb[pb["player_id"] == "BOS-4"]["name"].iloc[0], "pts", sb, N.canon(bos["away_abbr"]), N.canon(bos["home_abbr"]), event_id=22))
    cons = pd.DataFrame(rows)
    props, cov = N.join_props(cons, index, dnp)
    assert cov["consensus_rows"] == len(rows)
    assert cov["matched"] + sum(cov["dropped"].values()) == len(rows)
    d = cov["dropped"]
    assert d["team_mismatch"] == 1 and d["unresolved"] == 1 and d["low_books"] == 1 and d["postgame_quote"] == 1
    assert d["duplicate"] == 2
    assert d["void_dnp"] == 1
    assert d["ambiguous"] + d["namesake_history"] == 1
    assert sorted(props["stat"]) == ["rebounds"]                       # the duplicated pts rows are both gone; only reb survives
    assert props["date"].iloc[0] == sched[:10] and props["date"].iloc[0] != et_day
    assert props["y"].iloc[0] == float(row["rebounds"].iloc[0])
    assert props["season"].iloc[0] == 2025


def test_join_uses_the_scheduled_utc_date_and_scores_the_right_row():
    pb, gm, tips = make_masters(n_days=12, start="2025-11-01", seed=5)
    index, dnp = N.build_index(pb)
    late = gm[gm["away_abbr"] == "NY"].iloc[0]
    sched = pd.Timestamp(tips[late["game_id"]]).strftime("%Y-%m-%d %H:%M:%S")
    row = pb[(pb["game_id"] == late["game_id"]) & (pb["player_id"] == "NY-0") & pb["played"]].iloc[0]
    cons = pd.DataFrame([_cons_row(row["name"], "pts", sched, N.canon(late["away_abbr"]), N.canon(late["home_abbr"]), line=20.5)])
    props, cov = N.join_props(cons, index, dnp)
    assert cov["matched"] == 1
    p = props.iloc[0]
    assert p["y"] == float(row["points"]) and p["over"] == float(float(row["points"]) > 20.5)
    r = index[p["name_key"]][int(p["j"])]
    assert r["game_id"] == late["game_id"]                             # the master row is the SAME game, not another day's


# --------------------------------------------------------------------------------------------- walk-forward machinery
def _frame(n=1200, seed=1, seasons=(2022, 2023, 2024)):
    rng = np.random.default_rng(seed)
    k = len(seasons)
    s = np.repeat(seasons, n // k)
    pm = np.clip(rng.normal(0.5, 0.08, len(s)), 0.2, 0.8)
    y = (rng.random(len(s)) < pm).astype(float)
    pmod = np.clip(pm + rng.normal(0, 0.05, len(s)), 0.05, 0.95)
    return pd.DataFrame({"stat": "points", "season": s, "over": y, "p_market": pm, "p_eng": pmod,
                         "game_date": [f"{int(x)}-{(i % 28) + 1:02d}" for i, x in enumerate(s)],
                         "game_id": np.arange(len(s)) // 3, "n_books": 5,
                         "best_over_odds": -105.0, "best_under_odds": -105.0, "med_o_am": -110.0, "med_u_am": -110.0})


def test_wf_blend_fits_on_earlier_seasons_only():
    f = _frame()
    t = N.wf_blend(f, "p_eng", min_train=300)
    assert sorted(t["season"].unique()) == [2023, 2024]                # the first season is never scored
    tr = f[f["season"] < 2024]
    fit = M.blend_regression(tr["over"], tr["p_market"], tr["p_eng"], tr["game_date"])
    assert t[t["season"] == 2024]["fit_c"].iloc[0] == pytest.approx(float(fit["c"]))
    f2 = f.copy()
    f2.loc[f2["season"] == 2024, "over"] = 1.0 - f2.loc[f2["season"] == 2024, "over"]      # changing the scored season's outcomes ...
    t2 = N.wf_blend(f2, "p_eng", min_train=300)
    assert t2[t2["season"] == 2024]["fit_c"].iloc[0] == pytest.approx(t[t["season"] == 2024]["fit_c"].iloc[0])   # ... cannot move its fit


def test_bets_pick_the_edge_side_and_pay_at_the_stated_price():
    t = pd.DataFrame({"over": [1.0, 0.0, 1.0], "p_market": [0.60, 0.60, 0.50], "game_date": ["a", "b", "c"], "season": 2023,
                      "stat": "points", "n_books": 5, "best_over_odds": [100.0, 100.0, 100.0], "best_under_odds": [-120.0, -120.0, -120.0]})
    b = N.bets(t, "p_market", 0.05)
    assert len(b) == 2 and set(b["side"]) == {"over"}                 # 0.60 vs 0.50 implied: +10 pts; the 0.50 prop has no edge
    assert b["profit"].tolist() == [1.0, -1.0]                         # +100 wins 1u, loses 1u
    u = N.bets(t.assign(p_market=0.30), "p_market", 0.05)              # under: 0.70 vs 0.5455 implied
    assert set(u["side"]) == {"under"} and len(u) == 3


def test_wf_threshold_chooses_on_earlier_seasons_only():
    f = _frame(n=3000, seed=2)
    px = N.PRICES["median"]
    oos, chosen = N.wf_threshold(f, "p_market", px, [2023, 2024], min_bets=50)
    assert set(chosen) <= {2023, 2024}
    assert oos is None or set(oos["season"]) <= {2023, 2024}
    f2 = f.copy()
    f2.loc[f2["season"] == 2024, "over"] = 1.0 - f2.loc[f2["season"] == 2024, "over"]
    _, chosen2 = N.wf_threshold(f2, "p_market", px, [2023, 2024], min_bets=50)
    assert chosen2.get(2024) == chosen.get(2024)                       # the scored season's outcomes cannot pick its own threshold


# ------------------------------------------------------------------------- live loader bug found by this study (2026-11-02)
def test_live_loader_derives_opponent_and_folds_team_codes(monkeypatch):
    """The SportsBlaze player master has no opp_abbr and spells GSW / NOP / NYK / SAS / PHX as GS / NO / NY / SA / PHO. The loader
    left `_opp` empty (so the opponent factor and the team-makes assist factor were inert live) and the team-game table was keyed
    by codes the engine never asks for."""
    from mlb_core.data import cockpit_form as cf
    pb, gm, tips = make_masters(n_days=14, start="2025-11-01", seed=3)
    monkeypatch.setattr(cf, "_read", lambda keys, label, cols=None: pb.copy())
    monkeypatch.setattr(cf, "_hoopr_nba_rows", lambda after, first: pd.DataFrame())
    idx = cf._load_nba()["all"]
    rows = [r for rs in idx.values() for r in rs]
    assert rows and all(r["_opp"] for r in rows)
    ny = [r for r in rows if r["team_abbr"] == "NYK"]
    assert ny and not any(r["team_abbr"] == "NY" for r in rows)                # folded to the tricode
    by_game: dict = {}
    for r in rows:
        by_game.setdefault(r["game_id"], {})[r["team_abbr"]] = r["_opp"]
    for g in by_game.values():
        (a, ao), (b, bo) = g.items()
        assert ao == b and bo == a                                              # each side's opponent is the other
    from nba.props import team_context as TC
    tg = TC.TeamGames(idx)
    day = max(r["_date"] for r in rows)
    assert tg.expected_makes("NYK", "MIA", day) is not None                     # was None: no opponent codes, raw 'NY' key
