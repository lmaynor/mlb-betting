"""nba.props.official_availability -- pre-game availability from the OFFICIAL NBA injury report, per game, with the same
information the live scorer will have.

Every availability feature so far (nba/props/features.py "history" modes) is trained on WHO ACTUALLY DID NOT DRESS and served from
a live list nobody ever compared to it (an upper bound). The official report is published several times a day and the files are
retained (nba/data/nba_injury_report.py), so a historical, status-level, PRE-GAME list exists. This module turns banked reports
into (a) a per-game listing -- the newest report published at least LEAD_MIN before the tip that covers the game, (b) feature
columns built through the SAME FeatureContext code path the live scorer uses (per-game lists), and (c) an empirical
P(active | status) table, which turns a list into an expected-missing-minutes feature.

COVERAGE RULES (fail closed -- unknown is NaN, never "healthy"):
  * a game is covered by a report only when its matchup (AAA@BBB) appears in that report; a team listed in NO row of a covered
    game is healthy (the league lists only players), BUT a team carrying a "NOT YET SUBMITTED" row is unknown;
  * the report must have been stamped (title time) <= tip - LEAD_MIN (default 30 min, the inactive-list deadline);
  * unresolved names (no ESPN id in history) are dropped from the sets and counted, never guessed.
Selection bias note: the table / features describe players the league LISTS; unlisted players are treated as available.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from nba.data import nba_injury_report as R
from nba.props import config as C
from nba.teams import team_code

log = logging.getLogger(__name__)

LEAD_MIN = 30.0
STATUS_CODE = {"Available": 1, "Probable": 2, "Questionable": 3, "Doubtful": 4, "Out": 5}
LISTED_STATUSES = tuple(STATUS_CODE)


def load_reports(store_dir: str) -> pd.DataFrame:
    """All banked report CSVs under <store_dir>/NBA/injuries/official/ -> one frame with report_id and matchup teams."""
    files = sorted(Path(store_dir).glob("NBA/injuries/official/*/report_*.csv"))
    frames = []
    for f in files:
        d = pd.read_csv(f)
        d["report_id"] = f"{f.parent.name}/{f.stem}"
        frames.append(d)
    if not frames:
        return pd.DataFrame(columns=R.COLUMNS + ["report_id", "ts_utc", "m_a", "m_b"])
    df = pd.concat(frames, ignore_index=True)
    df["ts_utc"] = pd.to_datetime(df["report_ts_et"], utc=True)
    mt = df["matchup"].astype(str).str.split("@", n=1, expand=True)
    df["m_a"] = mt[0].map(team_code)
    df["m_b"] = mt[1].map(team_code) if mt.shape[1] > 1 else None
    df["game_date"] = pd.to_datetime(df["game_date"])
    return df


def resolve(reports: pd.DataFrame, rows: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Add espn_id via nba_injury_report.resolve_ids on UNIQUE (player, team) pairs (fast), -> (frame, stats)."""
    if reports.empty:
        return reports.assign(espn_id=pd.array([], dtype="Int64")), {"resolved": 0, "listed": 0}
    uniq = reports[["player", "team_code"]].drop_duplicates()
    res = R.resolve_ids(uniq, rows)[["player", "team_code", "espn_id", "how"]]
    out = reports.merge(res, on=["player", "team_code"], how="left")
    listed = out["status"].isin(LISTED_STATUSES)
    st = {"listed": int(listed.sum()), "resolved": int((listed & out["espn_id"].notna()).sum())}
    st["resolve_rate"] = st["resolved"] / max(st["listed"], 1)
    return out, st


def select_listing(reports: pd.DataFrame, tg: pd.DataFrame, lead_min: float = LEAD_MIN) -> tuple[pd.DataFrame, pd.DataFrame]:
    """For each team-game choose the newest covering report stamped <= tip - lead_min.

    -> (listing, coverage). listing: one row per listed (resolved) player of that team-game:
         game_id, team, qd (ET day), espn_id, status, reason_cat, report_id.
       coverage: one row per team-game: game_id, team, qd, report_id (None = uncovered), known (False when the team has a
         NOT YET SUBMITTED row), n_listed."""
    if reports.empty:
        return (pd.DataFrame(columns=["game_id", "team", "qd", "espn_id", "status", "reason_cat", "report_id"]),
                pd.DataFrame(columns=["game_id", "team", "qd", "report_id", "known", "n_listed"]))
    by_day = {d: g for d, g in reports.groupby("game_date")}
    lst, cov = [], []
    for r in tg.itertuples(index=False):
        day = pd.Timestamp(r.date).normalize()
        qd = int(day.to_datetime64().astype("datetime64[D]").astype("int64"))
        cand = by_day.get(day)
        rid, known, rows_ = None, True, None
        if cand is not None:
            pair = {r.team, r.opp}
            cov_rows = cand[(cand["m_a"].isin(pair)) & (cand["m_b"].isin(pair))]
            if len(cov_rows):
                tip = pd.Timestamp(r.tip_sort, tz="UTC") - pd.Timedelta(minutes=lead_min)
                ok = cov_rows[cov_rows["ts_utc"] <= tip]
                if len(ok):
                    newest = ok["ts_utc"].max()
                    rid = ok.loc[ok["ts_utc"] == newest, "report_id"].iloc[0]
                    sel = cand[(cand["report_id"] == rid) & (cand["m_a"].isin(pair)) & (cand["m_b"].isin(pair))]
                    mine = sel[sel["team_code"] == r.team]
                    known = not (mine["status"] == R.NOT_SUBMITTED).any()
                    rows_ = mine[mine["status"].isin(LISTED_STATUSES) & mine["espn_id"].notna()]
        n = 0 if rows_ is None else len(rows_)
        cov.append((r.game_id, r.team, qd, rid, known, n))
        if rows_ is not None and known:
            for e, st, rc in zip(rows_["espn_id"], rows_["status"], rows_["reason_cat"]):
                lst.append((r.game_id, r.team, qd, int(e), st, rc, rid))
    listing = pd.DataFrame(lst, columns=["game_id", "team", "qd", "espn_id", "status", "reason_cat", "report_id"])
    coverage = pd.DataFrame(cov, columns=["game_id", "team", "qd", "report_id", "known", "n_listed"])
    return listing, coverage


def lists_by_game(listing: pd.DataFrame, coverage: pd.DataFrame, statuses) -> dict:
    """{(team, qd): set(espn ids)} for covered+known team-games (an empty set = known healthy); uncovered/unknown games are
    ABSENT from the mapping, which FeatureContext turns into NaN. Feed to ctx.features(availability=...)."""
    ok = coverage[coverage["report_id"].notna() & coverage["known"]]
    out = {(t, int(q)): set() for t, q in zip(ok["team"], ok["qd"])}
    sel = listing[listing["status"].isin(set(statuses))]
    for t, q, e in zip(sel["team"], sel["qd"], sel["espn_id"]):
        if (t, int(q)) in out:
            out[(t, int(q))].add(int(e))
    return out


def played_outcomes(listing: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """listing + `played` (did the listed player log minutes in that game): rows = prepared history (played rows only)."""
    played = set(zip(rows["game_id"].astype("int64"), rows["player_id"].astype("int64")))
    out = listing.copy()
    out["played"] = [(int(g), int(e)) in played for g, e in zip(out["game_id"], out["espn_id"])]
    return out


def p_active_table(outcomes: pd.DataFrame, max_season: int | None = None, season_of: dict | None = None,
                   prior_n: float = 20.0) -> dict:
    """Empirical P(played | status), shrunk toward the pooled rate with `prior_n` pseudo-games, from listing rows whose game
    season_year <= max_season (walk-forward safe: pass T-1). `season_of` maps game_id -> season_year.
    -> {status: p}; every status in LISTED_STATUSES is present (pooled rate when unseen)."""
    o = outcomes
    if max_season is not None and season_of is not None:
        o = o[o["game_id"].map(season_of) <= max_season]
    pooled = float(o["played"].mean()) if len(o) else 0.5
    tab = {}
    for s in LISTED_STATUSES:
        g = o[o["status"] == s]
        tab[s] = float((g["played"].sum() + prior_n * pooled) / (len(g) + prior_n)) if len(g) else pooled
    return tab


def status_table(outcomes: pd.DataFrame) -> pd.DataFrame:
    """Reporting table: n, P(played), by status and by status x reason_cat."""
    a = outcomes.groupby("status")["played"].agg(["size", "mean"]).rename(columns={"size": "n", "mean": "p_played"})
    b = outcomes.groupby(["status", "reason_cat"])["played"].agg(["size", "mean"]).rename(
        columns={"size": "n", "mean": "p_played"})
    return a.reindex(list(LISTED_STATUSES)), b


# ----------------------------------------------------------------------------------------------- features
AV = list(C.AVAILABILITY_FEATURES)
VARIANTS = ("v1", "history", "list", "list_full", "list_pactive")
STUDY_STATS = ("points", "rebounds", "assists", "pra", "min")
FIRST_SEASON_YEAR = 2021          # first season with banked official reports (start year)


def build(espn_box: pd.DataFrame, store_dir: str, lead_min: float = LEAD_MIN):
    """-> (parts, meta, info). parts: X_base (v1 columns), X_history (v1 + actual-absence AV), per-status AV frames, own
    status, outcomes/season_of for the P(active) tables. All aligned to meta.index. Rows restricted to seasons >=
    FIRST_SEASON_YEAR so every variant sees the same rows."""
    from nba.props import espn_data as ED
    from nba.props.features import FeatureContext, ID_COLS, build_training_frame
    rows, tg = ED.prepare_espn(espn_box)
    ctx = FeatureContext(rows, tg)
    Xh, meta = build_training_frame(rows, tg, ctx, availability="history")
    keep = (meta["season_year"] >= FIRST_SEASON_YEAR).to_numpy()
    Xh, meta = Xh[keep], meta[keep]
    q = rows.loc[Xh.index, ID_COLS]

    reports, rstats = resolve(load_reports(store_dir), rows)
    listing, coverage = select_listing(reports, tg, lead_min)
    per_status = {}
    for s in LISTED_STATUSES:
        per_status[s] = ctx.features(q, availability=lists_by_game(listing, coverage, [s]))[AV]
    # own status: code of the row's own player on the selected list (0 = not listed), NaN when the game is not covered
    code = {(int(g), int(e)): STATUS_CODE[s] for g, e, s in zip(listing["game_id"], listing["espn_id"], listing["status"])}
    cov_ok = coverage[coverage["report_id"].notna() & coverage["known"]]
    covered = set(zip(cov_ok["game_id"].astype("int64"), cov_ok["team"]))
    gid = rows.loc[Xh.index, "game_id"].astype("int64").to_numpy()
    pid = rows.loc[Xh.index, "player_id"].astype("int64").to_numpy()
    team = rows.loc[Xh.index, "team"].to_numpy()
    own = np.array([code.get((g, p), 0.0) if (g, t) in covered else np.nan for g, p, t in zip(gid, pid, team)], float)
    outcomes = played_outcomes(listing, rows)
    season_of = dict(zip(tg["game_id"], tg["season_year"]))
    info = {"n_rows": int(len(Xh)), "reports": int(reports["report_id"].nunique()) if len(reports) else 0, **rstats,
            "team_games": int(len(coverage)), "covered": int(len(cov_ok)),
            "covered_share": float(len(cov_ok) / max(len(coverage), 1)),
            "not_submitted_games": int((~coverage["known"]).sum()), "listing_rows": int(len(listing)),
            "row_coverage": float(np.isfinite(own).mean())}
    parts = {"base": Xh.drop(columns=AV), "history": Xh, "per_status": per_status, "own": pd.Series(own, index=Xh.index),
             "outcomes": outcomes, "season_of": season_of}
    return parts, meta, info


def variant_frames(parts: dict, T: int) -> dict:
    """The five feature sets for test season T (the P(active) table uses ONLY seasons <= T-1)."""
    base = parts["base"]
    ps = parts["per_status"]
    tab = p_active_table(parts["outcomes"], T - 1, parts["season_of"])
    out_cols = ps["Out"]
    exp_min = sum((1.0 - tab[s]) * ps[s]["team_missing_min_l10"] for s in LISTED_STATUSES)
    exp_n = sum((1.0 - tab[s]) * ps[s]["team_missing_n_rotation"] for s in LISTED_STATUSES)
    full = pd.concat([base, out_cols,
                      (ps["Doubtful"]["team_missing_min_l10"] + ps["Questionable"]["team_missing_min_l10"]).rename(
                          "lst_doubtful_questionable_min_l10"),
                      parts["own"].rename("lst_own_status")], axis=1)
    return {"v1": base, "history": parts["history"], "list": pd.concat([base, out_cols], axis=1), "list_full": full,
            "list_pactive": pd.concat([base, exp_min.rename("lst_exp_missing_min_l10"),
                                       exp_n.rename("lst_exp_missing_n")], axis=1)}, tab


def run(parts: dict, meta: pd.DataFrame, stats, test_seasons, progress=print) -> pd.DataFrame:
    import xgboost as xgb
    from nba.props.availability_study import fit_mean
    from nba.props.train import target_of
    seasons = meta["season_year"].to_numpy()
    reg = (meta["season_type"] == "Regular Season").to_numpy()
    out = []
    for T in test_seasons:
        tr, va, te = seasons <= T - 2, seasons == T - 1, (seasons == T) & reg
        if not (tr.any() and va.any() and te.any()):
            progress(f"skip T={T}: empty split")
            continue
        X, tab = variant_frames(parts, T)
        progress(f"T={T} P(active|status) from seasons <= {T - 1}: " + ", ".join(f"{k}={v:.3f}" for k, v in tab.items()))
        for stat in stats:
            y = target_of(meta, stat)
            for v in VARIANTS:
                m, best = fit_mean(X[v][tr], y[tr], X[v][va], y[va])
                mu = m.predict(xgb.DMatrix(X[v][te].astype("float32"), feature_names=list(X[v].columns)))
                out.append(pd.DataFrame({"stat": stat, "season": T, "variant": v, "row": np.where(te)[0], "mu": mu}))
                progress(f"T={T} {stat:<8} {v:<13} rounds={best}")
    cols = ["stat", "season", "variant", "row", "mu"]
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=cols)


def render(summary: pd.DataFrame, info: dict, seasons: list, status_tab: pd.DataFrame, reason_tab: pd.DataFrame) -> str:
    from nba.props.availability_study import _md_table
    L = ["# NBA availability from the official pre-game injury report (accuracy only)\n",
         "Mean model, rolling origin (train <= T-2, early-stop T-1, refit <= T-1), regular-season rows of the test season,",
         f"ALL variants on the same rows (season_year >= {FIRST_SEASON_YEAR}). ACCURACY, not edge: no NBA prop odds history.\n",
         f"Test seasons (start year): {seasons}. Rows: {info['n_rows']:,}. Reports banked: {info['reports']:,}. "
         f"Team-games covered by a report stamped >= {int(LEAD_MIN)} min pre-tip: {info['covered']:,} of {info['team_games']:,} "
         f"({100 * info['covered_share']:.1f}%); listed names resolved to ESPN ids: {100 * info.get('resolve_rate', 0):.1f}%.\n",
         "| variant | what it is |", "|---|---|",
         "| v1 | shipped default features |",
         "| history | + availability from who actually did not dress (the existing opt-in; an UPPER BOUND, not available live) |",
         "| list | + the same 3 availability columns computed from the pre-game official list's `Out` players (live parity) |",
         "| list_full | list + Doubtful/Questionable missing minutes + the player's own listed status |",
         "| list_pactive | v1 + expected missing minutes = sum over listed rotation players of (1 - P(active | status)) x minutes; "
         "P(active) fitted on prior seasons only |\n",
         "## MAE by stat (all test seasons, row-weighted); gain vs v1 with date-cluster 95% CI\n",
         "| stat | variant | MAE | gain vs v1 | 95% CI |", "|---|---|---|---|---|"]
    for stat, g in summary.groupby("stat", sort=False):
        w = g.groupby("variant").apply(lambda d: np.average(d["mae"], weights=d["n"]), include_groups=False)
        for v in VARIANTS:
            gv = g[g["variant"] == v]
            if v == "v1":
                L.append(f"| {stat} | v1 | {w[v]:.4f} | - | - |")
                continue
            n = gv["n"].to_numpy()
            gain, lo, hi = (np.average(gv[c], weights=n) for c in ("mae_gain_vs_v1", "ci_lo", "ci_hi"))
            L.append(f"| {stat} | {v} | {w[v]:.4f} | {100 * gain / w['v1']:+.2f}% | "
                     f"[{100 * lo / w['v1']:+.2f}%, {100 * hi / w['v1']:+.2f}%] |")
    L += ["\n## P(played | listed status), all banked seasons\n", _md_table(status_tab.reset_index().round(4)),
          "\n### by reason class\n", _md_table(reason_tab.reset_index().round(4)), "\n## Per season\n",
          _md_table(summary.round(4)), ""]
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    import json
    from nba.data import hoopr
    from nba.props import espn_data as ED
    from nba.props.availability_study import summarize
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hoopr-dir", required=True)
    ap.add_argument("--store-dir", required=True, help="dir holding NBA/injuries/official/ (the banked reports)")
    ap.add_argument("--first-season", type=int, default=2019)
    ap.add_argument("--seasons", nargs="*", type=int, default=[2024, 2025], help="test seasons (START year)")
    ap.add_argument("--stats", nargs="*", default=list(STUDY_STATS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--reuse-preds", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    box = ED.load_espn(range(a.first_season, hoopr.current_season() + 1), a.hoopr_dir)
    parts, meta, info = build(box, a.store_dir)
    log.info("built: %s", json.dumps(info))
    cache = Path(a.out).with_suffix(".preds.parquet")
    if a.reuse_preds and cache.exists():
        preds = pd.read_parquet(cache)
    else:
        preds = run(parts, meta, a.stats, a.seasons, progress=log.info)
        preds.to_parquet(cache)
    summary = summarize(preds, meta, VARIANTS)
    s_tab, r_tab = status_table(parts["outcomes"])
    md = render(summary, info, a.seasons, s_tab, r_tab)
    Path(a.out).write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
