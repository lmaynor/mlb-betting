"""xG-aware saves / skater features (nhl.props.xgfeat) and the BettingPros evaluator (nhl.props.bp_eval): leakage safety,
starter handling, ablation lists, coverage bookkeeping, team-code mapping."""
import numpy as np
import pandas as pd
import pytest

from nhl.props import bp_eval as BE
from nhl.props import history
from nhl.props import xgfeat as X

PAIRS = [((1, 2), (3, 4)), ((1, 3), (2, 4)), ((1, 4), (2, 3))]
ABBR = {1: "AAA", 2: "BBB", 3: "CCC", 4: "DDD"}


def synth(n_days=60, seed=0):
    """4 teams; per team one goalie (id t*10+1) and 3 skaters (t*100+k); deterministic-ish outcomes + matching xG tables."""
    rng = np.random.default_rng(seed)
    games, gbox, xgl, xt, sbox, xsk = [], [], [], [], [], []
    base = pd.Timestamp("2025-10-10")
    gid = 2025020000
    for d in range(0, n_days, 2):
        date = (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        for home, away in PAIRS[(d // 2) % 3]:
            gid += 1
            games.append({"game_id": gid, "season_year": 2025, "game_type": 2, "date": date, "home_id": home, "away_id": away,
                          "home_abbr": ABBR[home], "away_abbr": ABBR[away]})
            stats = {}
            for team, opp, is_home in ((home, away, 1), (away, home, 0)):
                sa = int(rng.integers(20, 38))
                ga = int(rng.integers(0, 5))
                stats[team] = {"sa": sa, "ga": ga, "xga": float(sa * 0.095), "att_ag": sa + int(rng.integers(8, 18))}
            for team, opp, is_home in ((home, away, 1), (away, home, 0)):
                s = stats[team]
                gpid = team * 10 + 1
                gbox.append({"game_id": gid, "season_year": 2025, "game_type": 2, "date": date, "team_id": team, "team_abbr": ABBR[team],
                             "is_home": bool(is_home), "player_id": gpid, "name": f"G{team}", "toi_sec": 3600, "starter": True,
                             "shots_against": s["sa"], "saves": s["sa"] - s["ga"], "goals_against": s["ga"]})
                xgl.append({"game_id": gid, "player_id": gpid, "shots_faced": s["sa"], "xga": s["xga"], "ga_pop": s["ga"], "xga_pk": 0.2 * s["xga"],
                            "sa_pk": 4, "hd_faced": s["sa"] // 4, "gsax": s["xga"] - s["ga"]})
                o = stats[opp]
                xt.append({"game_id": gid, "team_id": team, "att_for": o["att_ag"], "xgf": o["xga"], "sog_for": o["sa"], "xgf_sog": o["xga"],
                           "goals_for_pop": o["ga"], "hd_for": o["sa"] // 4, "att_pp_for": 5, "att_against": s["att_ag"], "xga": s["xga"],
                           "sog_against": s["sa"], "xga_sog": s["xga"], "goals_against_pop": s["ga"], "hd_against": s["sa"] // 4, "att_pp_against": 5})
                for k in range(3):
                    pid = team * 100 + k
                    g, a = int(rng.poisson(0.3)), int(rng.poisson(0.4))
                    sbox.append({"game_id": gid, "season_year": 2025, "game_type": 2, "date": date, "team_id": team, "team_abbr": ABBR[team],
                                 "is_home": bool(is_home), "player_id": pid, "name": f"S{pid}", "position": "D" if k == 0 else "C",
                                 "toi_sec": float(rng.integers(900, 1300)), "goals": g, "assists": a, "points": g + a, "sog": int(rng.poisson(2.0))})
                    xsk.append({"game_id": gid, "player_id": pid, "attempts": 3, "ixg": 0.25, "ixg_ev": 0.2, "ixg_pp": 0.05, "att_ev": 2,
                                "att_pp": 1, "hd_att": 1, "sog_pop": 2, "ixg_sog": 0.3, "goals_pop": g})
    return {k: pd.DataFrame(v) for k, v in dict(games=games, gbox=gbox, xgl=xgl, xt=xt, sbox=sbox, xsk=xsk).items()}


def build(d):
    tt = X.team_table(d["games"], d["xt"])
    gt = X.goalie_table(d["games"], d["gbox"], d["xgl"])
    st = X.skater_table(d["games"], d["sbox"], d["xsk"])
    return tt, gt, X.saves_features(gt, tt), X.skater_features(st, tt, gt)


def _corrupt_from(d, cutoff):
    """Wreck every OUTCOME dated on/after `cutoff` in every input table."""
    m = {k: v.copy() for k, v in d.items()}
    ids = set(m["games"].loc[m["games"]["date"] >= cutoff, "game_id"])
    for key, cols in (("gbox", ["shots_against", "saves", "goals_against"]), ("sbox", ["goals", "assists", "points", "sog"]),
                      ("xgl", ["shots_faced", "xga", "ga_pop", "gsax", "hd_faced"]),
                      ("xt", ["att_for", "xgf", "sog_for", "goals_for_pop", "att_against", "xga", "sog_against", "goals_against_pop", "hd_for", "hd_against"]),
                      ("xsk", ["attempts", "ixg", "ixg_ev", "ixg_pp", "hd_att", "goals_pop"])):
        sel = m[key]["game_id"].isin(ids)
        m[key].loc[sel, cols] = 999.0
    return m


def test_no_market_feature_and_ablation_lists_are_strict_subsets():
    banned = ("line", "price", "odds", "implied", "consensus", "prob", "fair", "market")
    for lst in (X.SAVES_FEATURES, X.SKATER_FEATURES):
        assert not [f for f in lst if any(b in f for b in banned)]
    assert set(X.SAVES_FEATURES_NOXG) < set(X.SAVES_FEATURES) and set(X.SKATER_FEATURES_NOXG) < set(X.SKATER_FEATURES)
    assert not [f for f in X.SAVES_FEATURES_NOXG if any(t in f for t in ("gsax", "xga", "xgf", "hd_"))]
    assert not [f for f in X.SKATER_FEATURES_NOXG if any(t in f for t in ("ixg", "xgf", "xga", "gsax", "hd_", "fin_"))]


def test_features_never_see_their_own_game_or_later_ones():
    d = synth()
    _, _, sf0, kf0 = build(d)
    cut = sorted(d["games"]["date"].unique())[12]
    _, _, sf1, kf1 = build(_corrupt_from(d, cut))
    for f0, f1, feats in ((sf0, sf1, X.SAVES_FEATURES), (kf0, kf1, X.SKATER_FEATURES)):
        early = f0["date"] <= cut                                        # the game's OWN row (date == cut) included
        a = f0.loc[early, feats].reset_index(drop=True).astype(float)
        b = f1.set_index(["game_id", "player_id"]).loc[list(zip(f0.loc[early, "game_id"], f0.loc[early, "player_id"])), feats].reset_index(drop=True).astype(float)
        pd.testing.assert_frame_equal(a, b)
        late = f0["date"] > cut
        bb = f1.set_index(["game_id", "player_id"]).loc[list(zip(f0.loc[late, "game_id"], f0.loc[late, "player_id"])), feats].reset_index(drop=True).astype(float)
        assert not np.allclose(f0.loc[late, feats].reset_index(drop=True).fillna(-1).to_numpy(), bb.fillna(-1).to_numpy())   # not vacuous


def test_rolling_values_are_exactly_the_prior_games():
    d = synth()
    _, gt, sf, _ = build(d)
    g = gt[gt["player_id"] == 11].sort_values(["date", "game_id"]).reset_index(drop=True)
    k = 14
    prior = g.iloc[:k]
    row = g.iloc[k]
    assert np.isclose(row["sa_l10"], prior["shots_against"].tail(10).mean())
    assert np.isclose(row["sv_l60"], (prior["saves"].sum() + X.LG_SV * X.SV_PRIOR_SHOTS) / (prior["shots_against"].sum() + X.SV_PRIOR_SHOTS))
    assert np.isclose(row["gsax100_l20"], 100.0 * prior["gsax"].tail(20).sum() / (prior["shots_faced"].tail(20).sum() + X.SV_PRIOR_SHOTS))
    assert row["n_prior"] == k
    # first appearance has no history -> NaN, not zero
    assert np.isnan(g.iloc[0]["sa_l10"]) and g.iloc[0]["n_prior"] == 0


def test_saves_features_are_starters_only_and_analytic_baseline_is_sane():
    d = synth()
    d["gbox"] = pd.concat([d["gbox"], d["gbox"].iloc[:3].assign(player_id=999, starter=False, toi_sec=300)], ignore_index=True)
    d["xgl"] = pd.concat([d["xgl"], d["xgl"].iloc[:3].assign(player_id=999)], ignore_index=True)
    gt = X.goalie_table(d["games"], d["gbox"], d["xgl"])
    tt = X.team_table(d["games"], d["xt"])
    assert (gt["player_id"] == 999).any()
    sf = X.saves_features(gt, tt)
    assert 999 not in set(sf["player_id"]) and len(X.saves_features(gt, tt, starters_only=False)) == len(gt)
    mu = X.analytic_saves_mu(sf).dropna()
    assert len(mu) > 20 and (mu > 5).all() and (mu < 60).all()


def test_opposing_starter_is_attached_to_skaters():
    d = synth()
    _, gt, _, kf = build(d)
    r = kf.dropna(subset=["opp_g_sv_l60"]).iloc[0]
    opp_goalie = gt[(gt["game_id"] == r["game_id"]) & (gt["team_id"] == r["opp_id"]) & (gt["is_start"] == 1)].iloc[0]
    assert np.isclose(r["opp_g_sv_l60"], opp_goalie["sv_l60"])


# ---- bp_eval ------------------------------------------------------------------------------------------------------
def test_canon_maps_bettingpros_codes_and_utah_and_history_fix_matches():
    assert [BE.canon(c) for c in ("LA", "NJ", "SJ", "TB", "ARI", "NYR")] == ["LAK", "NJD", "SJS", "TBL", "UTA", "NYR"]
    for k in ("LA", "NJ", "SJ", "TB"):
        assert history.TEAM_FIX[k] == BE.BP_TO_NHL[k]


def test_apply_slack_keeps_last_lines_updated_just_after_the_start():
    cons = pd.DataFrame({"scheduled": ["2025-11-01 00:00:00"] * 4,
                         "updated_max": ["2025-10-31 21:00:00", "2025-11-01 00:00:05", "2025-11-01 00:05:00", "2025-11-01 02:30:00"]})
    out = BE.apply_slack(cons, 10.0)
    assert out["pregame"].tolist() == [True, True, True, False]


def _cons_row(event, player, line, p, team="AAA", home="AAA", away="BBB", stat="goals", n=4, date="2025-10-10"):
    return {"date": date, "game_date": date, "sport": "NHL", "event_id": event, "player_id": 1, "player": player,
            "player_norm": player.lower(), "team": team, "away": away, "home": home, "scheduled": f"{date} 23:00:00", "stat": stat,
            "line": line, "p_over_cons": p, "n_books": n, "disp": 0.01, "best_over_odds": 120.0, "best_under_odds": -110.0,
            "novig_over_odds": np.nan, "novig_under_odds": np.nan, "updated_max": f"{date} 22:59:00", "pregame": True}


def test_join_market_balances_the_books_and_drops_each_reason_once():
    names = pd.DataFrame({"player_id": [101, 102, 103], "full_name": ["Alice Skater", "Bob Winger", "Carl Centre"], "team": ["AAA", "AAA", "BBB"]})
    games = pd.DataFrame({"game_id": [7], "date": ["2025-10-10"], "home_abbr": ["AAA"], "away_abbr": ["BBB"]})
    outcome = pd.DataFrame({"game_id": [7, 7], "player_id": [101, 103], "actual": [1, 0]})
    cons = pd.DataFrame([
        _cons_row(1, "Alice Skater", 0.5, 0.45),                       # scored: scores 1 > 0.5 -> over wins
        _cons_row(1, "Carl Centre", 0.5, 0.55, team="BBB"),            # scored: 0 -> under wins
        _cons_row(1, "Bob Winger", 0.5, 0.5),                          # resolved but did not play -> void
        _cons_row(1, "Nobody Known", 0.5, 0.5),                        # unresolved
        _cons_row(1, "Alice Skater", 1.0, 0.3),                        # integer line -> int_line
        _cons_row(2, "Alice Skater", 0.5, 0.5, home="CCC", away="DDD"),  # no game
        _cons_row(1, "Dave Low", 0.5, 0.5, n=2),                       # low books
    ])
    props, cov = BE.join_market(cons, names, games, outcome, "actual", min_books=3)
    d = cov["dropped"]
    assert cov["matched"] + sum(d.values()) == cov["consensus_rows"] == 7
    assert (cov["matched"], d["void"], d["unresolved"], d["int_line"], d["no_game"], d["low_books"]) == (2, 1, 1, 1, 1, 1)
    assert sorted(props["won_over"]) == [0.0, 1.0]


def test_disagreement_roi_pays_the_right_side_and_reports_a_no_skill_reference():
    rng = np.random.default_rng(0)
    n = 400
    p_true = rng.uniform(0.3, 0.7, n)
    won = (rng.random(n) < p_true).astype(float)
    df = pd.DataFrame({"game_id": np.arange(n) // 2, "won_over": won, "cons_fair": 0.5, "p_model": p_true, "best_over": 100.0,
                       "best_under": 100.0, "novig_over": np.nan, "novig_under": np.nan})
    r = BE.disagreement_roi(df, 0.05, "best")
    assert r["n"] > 100 and r["roi"] > 0.1 and r["t"] > 2                  # a model that knows the truth makes money at even odds
    assert abs(r["no_skill_roi"]) < 0.15 and r["vs_no_skill"] > 0.1
    assert BE.disagreement_roi(df, 0.05, "novig")["n"] == 0                # novig prices absent -> no bets, not NaN bets
