"""nba.props.history_join -- score the NBA prop projections against HISTORICAL BettingPros per-book prop lines.

    ev = evaluate.evaluate_predictions(X, meta, preds, seasons, tables)
    rep = market_report(ev, X, meta, consensus, tables=tables, min_books=3)
    rep["by_stat"]["points"]["coverage"], rep["by_stat"]["points"]["scores"]      # see below

The model rows are the walk-forward test rows already in `ev` (season T scored by a model trained on seasons <= T-1, its
spread/PIT recalibration from T-1 only; features are strictly pre-tip by nba/props/features.py). For each row the model's
P(over) is recomputed AT THE MARKET'S OWN LINE with the same recalibrated distribution (dist.p_over_cal).

IDENTITY IS FAIL-CLOSED. The consensus names (BettingPros) are resolved to SportsBlaze player ids by
mlb_core.data.player_names.PlayerResolver over the players who PLAYED in the SAME ET game date (so a retired namesake can
never capture a quote), exact normalised name first, then the resolver's compatible-first-name rule; ambiguous -> dropped,
unknown -> dropped, two consensus rows resolving to the same (player, date) -> both dropped. All counted.

SELECTION SAFETY. The consensus is the LAST quote BettingPros held for the day (close-ish, one snapshot per day; see
mlb_core/odds/bettingpros_loader.py); rows whose quotes were updated after the scheduled tip are dropped
(`postgame_quote`). Only props whose player has a scored model row count: the evaluation population is conditional on the
player PLAYING (a DNP prop is void at the book, a missing row here -> `no_model_row`, never imputed). Games whose tip time
is unknown carry the UTC master date in `meta` rather than the ET day, so a late tip can fail the date join and land in
`no_model_row` -- it never mis-joins.

Whole-number lines can push: pushes are removed from both sides (model P(over) renormalised to over/(over+under)) and
counted under `push`. Brier / log-loss / blend c come from mlb_core.odds.model_vs_market (nhl.props.market math, reused).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from mlb_core.data.player_names import PlayerResolver
from mlb_core.odds import model_vs_market as MVM
from nba.props import config as C
from nba.props import dist as Dd

# bettingpros_props.SPORTS['NBA'] stat label -> nba.props.config.STATS name
STAT_MAP = {"pts": "points", "reb": "rebounds", "ast": "assists", "threes": "threes", "pra": "pra"}

JOINED_COLUMNS = ["player_id", "game_id", "game_date", "season", "line", "y", "over", "mu", "p_model", "p_market",
                  "n_books"]


def _date_str(s) -> pd.Series:
    return pd.to_datetime(s).dt.strftime("%Y-%m-%d")


def _resolve_ids(cons: pd.DataFrame, by_date: dict):
    """-> (SportsBlaze ids aligned to cons (None = lost), reason per row in {None,'no_model_row','unresolved','ambiguous'})."""
    cache: dict = {}
    ids, why = [], []
    for name, gd in zip(cons["player"], cons["game_date"]):
        rows = by_date.get(gd)
        if rows is None:
            ids.append(None)
            why.append("no_model_row")
            continue
        if gd not in cache:
            cache[gd] = PlayerResolver(rows)
        pid, how = cache[gd].resolve(str(name))
        ids.append(pid)
        why.append(None if pid is not None else ("ambiguous" if how == "ambiguous" else "unresolved"))
    return ids, why


def _p_model_at_line(m: pd.DataFrame, tables: dict, stat: str) -> np.ndarray:
    """Model P(over) at each row's market line, push-renormalised; recalibrated per season like evaluate_predictions."""
    out = np.full(len(m), np.nan)
    for season, g in m.groupby("season"):
        tab = tables.get((stat, int(season)))
        sel = m.index.get_indexer(g.index)
        line, mu, vg = g["line"].to_numpy(float), g["mu"].to_numpy(float), g["var_g"].to_numpy(float)
        fam = g["fam"].iloc[0]
        over = Dd.p_over_cal(line, mu, vg, fam, tab)
        push = Dd.p_push_cal(line, mu, vg, fam, tab)
        out[sel] = over / np.clip(1.0 - push, 1e-9, None)
    return out


def join_stat(ev: dict, meta: pd.DataFrame, consensus: pd.DataFrame, stat: str, tables: dict | None = None,
              min_books: int = 3, _by_date: dict | None = None):
    """One stat's join. Returns (joined frame, coverage). Coverage reasons: low_books, postgame_quote, unresolved,
    ambiguous, duplicate, no_model_row, push."""
    tables = tables or {}
    labels = {k: v for k, v in STAT_MAP.items() if v == stat}
    cons, n_in, dropped = MVM.screen_consensus(consensus, labels, min_books)
    dropped = dict(dropped, unresolved=0, ambiguous=0, duplicate=0, no_model_row=0, push=0)
    pooled = ev.get("pooled", {}).get(stat)
    if len(cons) == 0 or pooled is None:
        dropped["no_model_row"] += int(len(cons))
        return pd.DataFrame(columns=JOINED_COLUMNS), MVM.coverage(n_in, 0, dropped, stat=stat)
    if _by_date is None:
        _by_date = _players_by_date(meta)
    ids, why = _resolve_ids(cons, _by_date)
    cons = cons.assign(sb_id=ids, why=why)
    for k in ("no_model_row", "unresolved", "ambiguous"):
        dropped[k] += int((cons["why"] == k).sum())
    cons = cons[cons["sb_id"].notna()]
    dup = cons.duplicated(["sb_id", "game_date"], keep=False)
    dropped["duplicate"] = int(dup.sum())
    cons = cons[~dup]

    idx = np.asarray(pooled["idx"])
    mdl = pd.DataFrame({"sb_id": meta["player_id"].iloc[idx].to_numpy(),
                        "game_date": _date_str(meta["date"].iloc[idx]).to_numpy(),
                        "game_id": meta["game_id"].iloc[idx].to_numpy(), "season": np.asarray(pooled["season"]),
                        "y": np.asarray(pooled["y"], float), "mu": np.asarray(pooled["mu"], float),
                        "var_g": np.asarray(pooled["var_g"], float), "fam": np.asarray(pooled["fam"], object)})
    m = cons.merge(mdl, on=["sb_id", "game_date"], how="left")
    miss = m["mu"].isna()
    dropped["no_model_row"] += int(miss.sum())
    m = m[~miss].reset_index(drop=True)
    if len(m):
        is_push = np.isclose(m["y"].to_numpy(float), m["line"].to_numpy(float))
        dropped["push"] = int(is_push.sum())
        m = m[~is_push].reset_index(drop=True)
    if len(m):
        m["p_model"] = _p_model_at_line(m, tables, stat)
        m["over"] = (m["y"].to_numpy(float) > m["line"].to_numpy(float)).astype(float)
    else:
        m["p_model"], m["over"] = np.nan, np.nan
    m = m.drop(columns=["player_id"]).rename(columns={"sb_id": "player_id", "p_over_cons": "p_market"})   # BP id out, SB id in
    joined = m[JOINED_COLUMNS].reset_index(drop=True)
    return joined, MVM.coverage(n_in, len(joined), dropped, stat=stat)


def _players_by_date(meta: pd.DataFrame) -> dict:
    """{ET date 'YYYY-MM-DD': [{player_id, full_name, team}]} over EVERY played row in meta (not only scored rows, so an
    unknown name is distinguishable from an unscored player)."""
    d = pd.DataFrame({"date": _date_str(meta["date"]), "player_id": meta["player_id"], "full_name": meta["name"],
                      "team": meta["team"]}).drop_duplicates(["date", "player_id"])
    out: dict = {}
    for k, g in d.groupby("date"):
        out[k] = g[["player_id", "full_name", "team"]].to_dict("records")
    return out


def market_report(ev: dict, X: pd.DataFrame, meta: pd.DataFrame, consensus: pd.DataFrame, tables: dict | None = None,
                  min_books: int = 3) -> dict:
    """OPTIONAL model-vs-real-market hook over every stat in `ev["pooled"]` the consensus covers.

    ev         evaluate.evaluate_predictions output;  X unused (kept for call symmetry with the other evaluate helpers)
    meta       the frame evaluate_predictions was given (needs player_id, name, team, date, game_id)
    tables     {(stat, season): pit_table} as passed to evaluate_predictions (None -> raw, un-recalibrated distribution)
    returns    {"by_stat": {stat: {"coverage", "scores", "frame"}}, "all": {"coverage", "scores"}}; "all" pools the stats
               (counts summed, scored on the concatenated rows)."""
    by_date = _players_by_date(meta)
    out = {"by_stat": {}}
    frames, tot_in, tot_matched, tot_dropped = [], 0, 0, {}
    for stat in C.STATS:
        if stat not in ev.get("pooled", {}):
            continue
        joined, cov = join_stat(ev, meta, consensus, stat, tables, min_books, _by_date=by_date)
        scores = MVM.score_model_vs_market(joined["over"], joined["p_model"], joined["p_market"], joined["game_id"]) \
            if len(joined) else MVM.score_model_vs_market([], [], [], [])
        out["by_stat"][stat] = {"coverage": cov, "scores": scores, "frame": joined}
        frames.append(joined)
        tot_in += cov["consensus_rows"]
        tot_matched += cov["matched"]
        for k, v in cov["dropped"].items():
            tot_dropped[k] = tot_dropped.get(k, 0) + v
    allj = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=JOINED_COLUMNS)
    out["all"] = {"coverage": MVM.coverage(tot_in, tot_matched, tot_dropped),
                  "scores": MVM.score_model_vs_market(allj["over"], allj["p_model"], allj["p_market"], allj["game_id"])
                  if len(allj) else MVM.score_model_vs_market([], [], [], [])}
    return out


def render_market_section(rep: dict) -> str:
    L = ["\n## Model vs historical BettingPros market (OPTIONAL; real per-book lines)\n",
         "Consensus = median of de-vigged two-sided books (no Kalshi / DFS / BettingPros-consensus rows) at the modal "
         "line, the LAST quote BettingPros held for the day. Model P(over) recalibrated and evaluated at that line; "
         "pushes removed; conditional on the player playing. Brier / log-loss lower = better; blend `c` > 0 "
         "(significant) = the model adds information beyond the market. Calibration vs a market, NOT CLV, NOT an edge "
         "claim.\n"]
    for stat, r in rep["by_stat"].items():
        L.append(f"### {stat}")
        L.append(MVM.render_coverage(stat, r["coverage"]))
        L.append(MVM.render_scores(stat, r["scores"]))
    L.append("### all stats")
    L.append(MVM.render_coverage("all", rep["all"]["coverage"]))
    L.append(MVM.render_scores("all", rep["all"]["scores"]))
    return "\n".join(L) + "\n"
