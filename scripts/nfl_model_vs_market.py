"""Model vs market on the BANKED NFL prop lines (shadow evaluation; no betting, no GCS writes).

Compares the NFL prop models' P(over) with the de-vigged book consensus at the closing proxy, both scored
against the real outcome (graded off nflverse player_stats by nfl.props.grade). The question it answers is
"is the model any better than the market at predicting the same props?" -- the only honest edge test the
repo can run, since no historical NFL prop lines exist.

Inputs
  --lines   props_open_close.parquet from scripts/nfl_banked_lines.py
  --features-cache  parquet of nfl.features.props_features.build_player_week_features (built if absent; ~3.5 min)

Method
  * models are trained ONLY on seasons < the lines' season (no look-ahead);
  * the model is scored at the market's own point for each prop (p_over from nfl.models.props.p_over);
  * only decided props count (win/loss; push/void/ungradable/not_yet_graded dropped);
  * the market side is the consensus P(over) at close (close_p_over), restricted to props whose close is fresh
    and still on the board, and reported by number of books behind it;
  * a paired difference in Brier/log-loss with its standard error is the headline; small n means "no verdict".

CLV is this repo's go/no-go and needs open->close moves on a much larger bank than exists today; this script
does NOT establish edge.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nfl.features.props_features import build_player_week_features  # noqa: E402
from nfl.models import props as P  # noqa: E402
from nfl.props.grade import Actuals, grade_frame  # noqa: E402
from nfl.props.resolve import PlayerResolver  # noqa: E402

EPS = 1e-4


def _brier(p, y):
    return (p - y) ** 2


def _logloss(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def _paired(a, b):
    d = np.asarray(a) - np.asarray(b)
    n = len(d)
    se = d.std(ddof=1) / np.sqrt(n) if n > 1 else float("nan")
    return d.mean(), se


def build_table(lines_path, features, seasons_lines):
    lines = pd.read_parquet(lines_path)
    lines = lines[lines["season"].isin(seasons_lines)].copy()
    lines = lines[lines["close_p_over"].notna() & lines["close_fresh"].fillna(False) & lines["alive_at_close"].fillna(False)]
    seasons_all = sorted(set(features["season"]))
    resolver = PlayerResolver.from_mirror(sorted(set(lines["season"])))
    actuals = Actuals.from_mirror(sorted(set(lines["season"])))
    q = lines.rename(columns={"player": "player_display"})
    q = q.assign(player=q["player_display"], team=q["team_hint"], side="over")
    graded = grade_frame(q[["player", "team", "stat", "point", "side", "game_id", "season", "week", "close_p_over",
                            "close_n_books", "open_p_over", "open_n_books"]], resolver=resolver, actuals=actuals)
    graded["y"] = graded["result"].map({"win": 1.0, "loss": 0.0})
    graded = graded[graded["y"].notna() & graded["gsis_id"].notna()].copy()

    train_cut = min(seasons_lines)
    train = features[features["season"] < train_cut]
    rows = []
    for stat in sorted(set(graded["stat"])):
        if stat not in P.SPECS:
            continue
        model = P.train_stat(train, stat)
        g = graded[graded["stat"] == stat]
        test = features[(features["season"].isin(seasons_lines)) & (features[P.SPECS[stat].elig] == 1)]
        test = test[["player_id", "game_id"] + [c for c in model.features if c in test.columns]]
        m = g.merge(test, left_on=["gsis_id", "game_id"], right_on=["player_id", "game_id"], how="inner")
        if m.empty:
            continue
        pr = P.predict_prob(model, m, m["point"].to_numpy())
        m = m.assign(mu=pr["mu"].to_numpy(), p_model=pr["p_over"].to_numpy(), p_push=pr["p_push"].to_numpy())
        rows.append(m[["stat", "season", "week", "game_id", "gsis_id", "point", "y", "close_p_over", "close_n_books",
                       "p_model", "mu", "actual"]])
    if not rows:
        raise SystemExit("no props could be joined to eligible model rows")
    return pd.concat(rows, ignore_index=True)


def summarize(t, label):
    n = len(t)
    if n < 2:
        return f"| {label} | {n} | -- | -- | -- | -- | -- |"
    bm, bk = _brier(t["p_model"], t["y"]), _brier(t["close_p_over"], t["y"])
    lm, lk = _logloss(t["p_model"], t["y"]), _logloss(t["close_p_over"], t["y"])
    db, dbse = _paired(bm, bk)
    dl, dlse = _paired(lm, lk)
    return (f"| {label} | {n} | {bm.mean():.4f} | {bk.mean():.4f} | {db:+.4f} +/- {dbse:.4f} | "
            f"{dl:+.4f} +/- {dlse:.4f} | {t['y'].mean():.3f} |")


def main_line_only(t):
    """One prop per (game, player, stat): the point with the most books at close, ties -> closest to 50/50.
    Alt-line ladders are strongly correlated copies of one outcome; counting them as independent inflates n."""
    t = t.assign(_dev=(t["close_p_over"] - 0.5).abs())
    t = t.sort_values(["close_n_books", "_dev"], ascending=[False, True])
    return t.drop_duplicates(["game_id", "gsis_id", "stat"], keep="first").drop(columns="_dev")


def render(t):
    m = main_line_only(t)
    hdr = ("| subset | n | Brier model | Brier market | model-market Brier (+/- SE) | model-market logloss (+/- SE) "
           "| over rate |\n|---|---|---|---|---|---|---|")
    out = ["# NFL model vs market (banked lines, closing proxy)", "",
           "Negative differences mean the MODEL beat the market. Paired over the same props. Small n => no verdict.", "",
           "## Pooled", hdr, summarize(t, "all decided props"),
           summarize(t[t["close_n_books"] >= 2], "market has >=2 books"),
           summarize(t[t["close_n_books"] >= 3], "market has >=3 books"), "",
           "## One prop per player-game-stat (main line; alt ladders removed)", hdr,
           summarize(m, "main line, any books"),
           summarize(m[m["close_n_books"] >= 2], "main line, >=2 books"),
           summarize(m[m["close_n_books"] >= 3], "main line, >=3 books"), "",
           "## By stat (main line only)", hdr]
    for s, g in m.groupby("stat"):
        out.append(summarize(g, s))
    out += ["", "## By week", hdr]
    for (se, wk), g in t.groupby(["season", "week"]):
        out.append(summarize(g, f"{se} wk{wk}"))
    d = (t["p_model"] - t["close_p_over"])
    out += ["", "## Does disagreement predict the outcome? (model - market P(over))",
            "| gap bin | n | mean gap | realized over rate | mean market P | mean model P |", "|---|---|---|---|---|---|"]
    bins = pd.cut(d, [-1, -0.10, -0.03, 0.03, 0.10, 1])
    for b, g in t.groupby(bins, observed=True):
        out.append(f"| {b} | {len(g)} | {d[g.index].mean():+.3f} | {g['y'].mean():.3f} | "
                   f"{g['close_p_over'].mean():.3f} | {g['p_model'].mean():.3f} |")
    out += ["", "Caveats: the market 'close' is the last snapshot 20-35 min pre-kick (not the true close); most props are",
            "priced by a single book (a noisy reference); the model uses nflverse closing spread/total as inputs;",
            "props the model deems ineligible are excluded; one season, few weeks. This is plumbing validation,",
            "not evidence of edge."]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lines", required=True)
    ap.add_argument("--features-cache", default=None)
    ap.add_argument("--seasons", default="2026", help="comma list of seasons present in --lines")
    ap.add_argument("--out", default=None, help="write the markdown report here")
    a = ap.parse_args(argv)
    seasons = [int(x) for x in a.seasons.split(",")]
    if a.features_cache and os.path.exists(a.features_cache):
        feats = pd.read_parquet(a.features_cache)
    else:
        feats = build_player_week_features(range(2018, max(seasons) + 1))
        if a.features_cache:
            feats.to_parquet(a.features_cache)
    t = build_table(a.lines, feats, seasons)
    rep = render(t)
    print(rep)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(rep + "\n")
        t.to_parquet(os.path.splitext(a.out)[0] + "_table.parquet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
