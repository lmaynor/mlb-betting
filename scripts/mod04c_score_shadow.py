"""Shadow scoring runner for MOD-04 Cluster (c) NBA & NHL assists."""
from __future__ import annotations

import argparse
import math
import sys
from typing import Optional

import numpy as np
import pandas as pd

from mlb_core import storage
from mod04c import nba_assists, nhl_assists, prd04_guard, shadow
from mod04c.dist_negbin import convolve_pmf, nb_pmf, pmf_p_over
from mod04c.feature_store import filter_history
from nba.config import GAMES_MASTER as NBA_GAMES_MASTER, PLAYER_BOX_MASTER as NBA_PLAYER_BOX_MASTER
from nhl.config import GAMES_MASTER as NHL_GAMES_MASTER, GOALIE_BOX_MASTER as NHL_GOALIE_MASTER, SKATER_BOX_MASTER as NHL_SKATER_MASTER


def _trailing_median_line_proxy(prior_history: pd.DataFrame, player_ids: pd.Series, stat_col: str = "assists") -> np.ndarray:
    """Compute half-point shadow measurement line proxy from strictly-prior player medians."""
    if prior_history.empty or stat_col not in prior_history.columns:
        return np.full(len(player_ids), 0.5, dtype=float)
    p = prior_history.sort_values(["player_id", "date"], kind="mergesort")
    meds = p.groupby("player_id", sort=False).tail(15).groupby("player_id")[stat_col].median()
    vals = player_ids.map(meds).fillna(0.0).to_numpy(float)
    return np.maximum(0.5, np.floor(vals) + 0.5)


def _build_slate_from_games(games_df: pd.DataFrame, prior_box: pd.DataFrame, target_date: str) -> pd.DataFrame:
    g = games_df.copy()
    g["date"] = pd.to_datetime(g["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    day_games = g[g["date"] == target_date]
    if day_games.empty or prior_box.empty:
        return pd.DataFrame()

    tcol = "team_abbr" if "team_abbr" in prior_box.columns else ("team" if "team" in prior_box.columns else None)
    if tcol is None:
        return pd.DataFrame()

    team_match = {}
    for row in day_games.itertuples(index=False):
        gid = getattr(row, "game_id", None)
        lock = getattr(row, "start_utc", None) or getattr(row, "tip_utc", None)
        home = getattr(row, "home_abbr", None) or getattr(row, "home_team", None)
        away = getattr(row, "away_abbr", None) or getattr(row, "away_team", None)
        if home and away:
            team_match[home] = {"game_id": gid, "team": home, "opponent": away, "lock_utc": lock}
            team_match[away] = {"game_id": gid, "team": away, "opponent": home, "lock_utc": lock}

    p_sorted = prior_box.sort_values(["player_id", "date"], kind="mergesort")
    latest_players = p_sorted.drop_duplicates("player_id", keep="last")
    latest_players = latest_players[latest_players[tcol].isin(team_match)].copy()
    if latest_players.empty:
        return pd.DataFrame()

    rows = []
    for rec in latest_players.to_dict("records"):
        tm = rec[tcol]
        m = team_match[tm]
        rows.append({
            "date": target_date,
            "game_id": m["game_id"],
            "player_id": rec["player_id"],
            "player_name": rec.get("name") or rec.get("player_name"),
            "team": tm,
            "opponent": m["opponent"],
            "position": rec.get("position"),
            "lock_utc": m["lock_utc"],
        })
    return pd.DataFrame(rows)


def _load_freshest_nba_injuries() -> tuple[Optional[pd.DataFrame], Optional[str]]:
    keys = [k for k in storage.list_keys("NBA/injuries/") if k.endswith(".csv")]
    if not keys:
        return None, None
    keys.sort()
    freshest_key = keys[-1]
    try:
        df = storage.read_csv(freshest_key)
    except Exception:
        return None, None
    ts = None
    for col in ("fetched_at_utc", "snapshot_ts_utc", "banked_at_utc", "updated"):
        if col in df.columns and df[col].notna().any():
            ts = str(df[col].dropna().iloc[-1])
            break
    return df, ts


def _score_nba(target_date: str, c_serving: float, version: str) -> dict:
    if not storage.exists(NBA_GAMES_MASTER) or not storage.exists(NBA_PLAYER_BOX_MASTER):
        print(f"nba_assists: masters missing for {target_date}; skipping")
        return {"scored": 0, "excluded": 0, "total": 0}

    games = storage.read_csv(NBA_GAMES_MASTER)
    box = storage.read_csv(NBA_PLAYER_BOX_MASTER)
    if "season_type" in box.columns:
        box = box[box["season_type"] != "Preseason"].copy()

    prior = filter_history(box, target_date)
    slate = _build_slate_from_games(games, prior, target_date)
    if slate.empty:
        print(f"nba_assists: no games on {target_date}; exiting with 0 rows")
        return {"scored": 0, "excluded": 0, "total": 0}

    inj_df, snap_ts = _load_freshest_nba_injuries()
    if inj_df is not None and not inj_df.empty:
        inj_tcol = "team_abbrev" if "team_abbrev" in inj_df.columns else ("team_code" if "team_code" in inj_df.columns else "team")
        unres_by_team = {}
        if inj_tcol in inj_df.columns and "status" in inj_df.columns:
            for tm, grp in inj_df.groupby(inj_tcol):
                unres_by_team[tm] = [s for s in grp["status"].tolist() if prd04_guard.is_unresolved_status(s)]
        slate["snapshot_ts_utc"] = snap_ts
        slate["unresolved_statuses"] = slate["team"].map(lambda t: unres_by_team.get(t, []))
    else:
        slate["snapshot_ts_utc"] = None
        slate["unresolved_statuses"] = [[] for _ in range(len(slate))]

    panel = nba_assists._prior_history_panel(prior, shots=None, include_game_env=True)
    tr = panel[(panel["games_played"] >= 1) & panel["assists"].notna()]
    if len(tr) < 20:
        print(f"nba_assists: insufficient prior training rows ({len(tr)}) before {target_date}")
        return {"scored": 0, "excluded": 0, "total": 0}

    model = nba_assists.CreationConversionAssistsModel(include_game_env=True).fit(tr, y_col="assists")
    feat = nba_assists.build_nba_assist_features(
        target_date,
        f"{target_date}T23:59:59Z",
        slate=slate,
        history=box,
        injuries=inj_df,
    )
    pmf, _, _ = model.predict_pmf(feat, kmax=40)
    lines = _trailing_median_line_proxy(prior, feat["player_id"], stat_col="assists")
    p_model = pmf_p_over(pmf, lines)

    feat["line"] = lines
    feat["line_source"] = "trailing_median_proxy"
    feat["p_model"] = p_model
    guarded = prd04_guard.apply_guard(feat, scrub_probs=True)

    shadow_rows = shadow.make_shadow_rows(
        "nba_assists",
        nba_assists.ENGINE_LABEL,
        guarded,
        guarded["p_model"].tolist(),
        lines=lines,
        c_serving=c_serving,
    )
    key = f"EVSweep/mod04c/shadow/nba_assists/{target_date}.json"
    shadow.write_predictions_json(shadow_rows, "nba_assists", version, key)

    scored = sum(1 for r in shadow_rows if r.get("guard_status") == "scored")
    excluded = sum(1 for r in shadow_rows if r.get("guard_status") == "excluded")
    print(f"nba_assists: scored={scored} excluded={excluded} total={len(shadow_rows)} -> {key}")
    return {"scored": scored, "excluded": excluded, "total": len(shadow_rows), "key": key}


def _score_nhl(target_date: str, c_serving: float, version: str) -> dict:
    if not storage.exists(NHL_GAMES_MASTER) or not storage.exists(NHL_SKATER_MASTER):
        print(f"nhl_assists: masters missing for {target_date}; skipping")
        return {"scored": 0, "excluded": 0, "total": 0}

    games = storage.read_csv(NHL_GAMES_MASTER)
    sk = storage.read_csv(NHL_SKATER_MASTER)
    split_key = "NHL/assist_split/assist_split.csv"
    if storage.exists(split_key):
        sp = storage.read_csv(split_key)
        sk = sk.merge(
            sp[["game_id", "player_id", "primary_assists", "secondary_assists"]],
            on=["game_id", "player_id"],
            how="left",
        )
        sk["primary_assists"] = sk["primary_assists"].fillna(0)
        sk["secondary_assists"] = sk["secondary_assists"].fillna(0)
    else:
        print(f"nhl_assists: {split_key} missing; using unified assists only")
        sk["primary_assists"] = sk["assists"]
        sk["secondary_assists"] = 0

    goalies = storage.read_csv(NHL_GOALIE_MASTER) if storage.exists(NHL_GOALIE_MASTER) else None
    prior = filter_history(sk, target_date)
    slate = _build_slate_from_games(games, prior, target_date)
    if slate.empty:
        print(f"nhl_assists: no games on {target_date}; exiting with 0 rows")
        return {"scored": 0, "excluded": 0, "total": 0}

    panel = nhl_assists._prior_nhl_panel(prior, shots=None, goalies=goalies)
    tr = panel[(panel["games_played"] >= 1) & panel["assists"].notna()]
    if len(tr) < 20:
        print(f"nhl_assists: insufficient prior training rows ({len(tr)}) before {target_date}")
        return {"scored": 0, "excluded": 0, "total": 0}

    prim_m = nhl_assists.PrimaryAssistsModel().fit(tr)
    sec_m = nhl_assists.SecondaryAssistsModel().fit(tr)
    feat = nhl_assists.build_nhl_assist_features(
        target_date,
        f"{target_date}T23:59:59Z",
        slate=slate,
        history_with_split=sk,
        shots=None,
        lineups=None,
        goalies=goalies,
    )
    pmf_p, _, _ = prim_m.predict_pmf(feat, kmax=15)
    pmf_s, _, _ = sec_m.predict_pmf(feat, kmax=15)
    pmf_conv = convolve_pmf(pmf_p, pmf_s)

    lines = _trailing_median_line_proxy(prior, feat["player_id"], stat_col="assists")
    p_model = pmf_p_over(pmf_conv, lines)
    feat["line_source"] = "trailing_median_proxy"

    shadow_rows = shadow.make_shadow_rows(
        "nhl_assists",
        nhl_assists.SPLIT_ENGINE_LABEL,
        feat,
        p_model,
        lines=lines,
        c_serving=c_serving,
    )
    key = f"EVSweep/mod04c/shadow/nhl_assists/{target_date}.json"
    shadow.write_predictions_json(shadow_rows, "nhl_assists", version, key)
    print(f"nhl_assists: scored={len(shadow_rows)} excluded=0 total={len(shadow_rows)} -> {key}")
    return {"scored": len(shadow_rows), "excluded": 0, "total": len(shadow_rows), "key": key}


# ---------------------------------------------------------------------------
# Board-slate mode (coordinator addition, Nov 25, 2026): the played-games
# masters lag the current slate (they carry only completed games), so the
# live shadow population is the daily board itself (EVSweep projections_latest
# assists rows: real lines, market probs, commence = lock). Features still
# come only from strictly-prior master history (U1); identity is resolved by
# normalized name against the masters (suffix-stripped, RES-17 style).
# ---------------------------------------------------------------------------
import json as _json
import re as _re

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def _norm_name(s) -> str:
    import unicodedata as _ud
    s = _ud.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    s = s.lower().replace(".", " ").replace("'", "")
    parts = [p for p in _re.split(r"\s+", s.strip()) if p]
    while parts and parts[-1] in _NAME_SUFFIXES:
        parts.pop()
    return " ".join(parts)


def _name_id_map(master: pd.DataFrame) -> dict:
    m = master.dropna(subset=["name"]).copy()
    m["_n"] = m["name"].map(_norm_name)
    m = m.sort_values("date") if "date" in m.columns else m
    out = {}
    for n, pid in zip(m["_n"], m["player_id"]):
        out[n] = pid  # latest occurrence wins (frame sorted by date)
    return out


def _board_assists_rows(sport: str, target_date: str) -> tuple[pd.DataFrame, str]:
    key = f"EVSweep/{sport}_props/projections_latest.json"
    payload = _json.loads(storage.read_bytes(key))
    rows = [r for r in payload.get("rows", []) if r.get("stat") == "assists"]
    # The board file also carries future slates; a date's shadow bank holds only
    # that date's games: commence in [target_date 00:00Z, target_date+1 12:00Z)
    # (US evening games cross midnight UTC).
    lo = f"{target_date}T00:00:00"
    hi_date = (pd.Timestamp(target_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    hi = f"{hi_date}T12:00:00"
    rows = [r for r in rows if lo <= str(r.get("commence"))[:19] < hi]
    df = pd.DataFrame(rows)
    return df, str(payload.get("ts"))


def _board_slate(sport: str, master: pd.DataFrame, target_date: str) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Returns (matched_slate, unmatched_board_rows, board_ts)."""
    board, board_ts = _board_assists_rows(sport, target_date)
    if board.empty:
        return pd.DataFrame(), board, board_ts
    idmap = _name_id_map(master)
    latest = master.sort_values("date").drop_duplicates("player_id", keep="last") if "date" in master.columns else master.drop_duplicates("player_id", keep="last")
    pos_map = dict(zip(latest["player_id"], latest.get("position", pd.Series([None] * len(latest), index=latest.index))))
    recs, unmatched = [], []
    for r in board.to_dict("records"):
        pid = idmap.get(_norm_name(r.get("player")))
        base = {
            "player_name": r.get("player"),
            "team": r.get("team"),
            "opponent": r.get("opp"),
            "line": r.get("line"),
            "p_over_market": r.get("p_over_market"),
            "lock_utc": r.get("commence"),
            "game_key": r.get("game_key"),
            "board_served_p_model": r.get("p_over_model"),
            "board_served_p_blend": r.get("p_over_blend"),
        }
        if pid is None:
            unmatched.append(base)
        else:
            base["player_id"] = pid
            base["position"] = pos_map.get(pid)
            base["date"] = str(r.get("commence"))[:10]
            recs.append(base)
    return pd.DataFrame(recs), pd.DataFrame(unmatched), board_ts


def _score_nba_board(target_date: str, c_serving: float, version: str) -> dict:
    box = storage.read_csv(NBA_PLAYER_BOX_MASTER)
    if "season_type" in box.columns:
        box = box[box["season_type"] != "Preseason"].copy()
    slate, unmatched, board_ts = _board_slate("nba", box, target_date)
    if slate.empty:
        print(f"nba_assists(board): no matched board rows; unmatched={len(unmatched)}")
        return {"scored": 0, "excluded": 0, "total": 0}
    prior = filter_history(box, target_date)

    inj_df, snap_ts = _load_freshest_nba_injuries()
    unres_by_team = {}
    if inj_df is not None and not inj_df.empty and "team_abbrev" in inj_df.columns and "status" in inj_df.columns:
        for tm, grp in inj_df.groupby("team_abbrev"):
            unres_by_team[tm] = [s for s in grp["status"].tolist() if prd04_guard.is_unresolved_status(s)]
    slate["snapshot_ts_utc"] = snap_ts
    slate["unresolved_statuses"] = slate["team"].map(lambda t: unres_by_team.get(t, []))

    panel = nba_assists._prior_history_panel(prior, shots=None, include_game_env=True)
    tr = panel[(panel["games_played"] >= 1) & panel["assists"].notna()]
    model = nba_assists.CreationConversionAssistsModel(include_game_env=True).fit(tr, y_col="assists")
    feat = nba_assists.build_nba_assist_features(target_date, f"{target_date}T23:59:59Z", slate=slate, history=box, injuries=inj_df)
    if "player_id" in feat.columns and len(feat) == len(slate):
        feat = feat.set_index("player_id").loc[slate["player_id"]].reset_index()
    feat["lock_utc"] = slate["lock_utc"].values
    feat["snapshot_ts_utc"] = snap_ts
    feat["unresolved_statuses"] = slate["unresolved_statuses"].values
    pmf, _, _ = model.predict_pmf(feat, kmax=40)
    lines = slate["line"].astype(float).values
    feat["line"] = lines
    feat["line_source"] = "board"
    feat["p_model"] = pmf_p_over(pmf, lines)
    guarded = prd04_guard.apply_guard(feat, scrub_probs=True)

    shadow_rows = shadow.make_shadow_rows(
        "nba_assists", nba_assists.ENGINE_LABEL, guarded, guarded["p_model"].tolist(),
        market_p=slate["p_over_market"].tolist(), lines=lines, c_serving=c_serving,
    )
    for row, lk in zip(shadow_rows, slate["lock_utc"].tolist()):
        row["lock_utc"] = lk
        row["board_ts"] = board_ts
    for r in unmatched.to_dict("records"):
        shadow_rows.append({
            "family": "nba_assists", "engine": nba_assists.ENGINE_LABEL, "date": target_date,
            "player_name": r["player_name"], "team": r["team"], "opponent": r["opponent"],
            "line": r["line"], "line_source": "board", "p_model": None, "p_served": None,
            "guard_status": "excluded", "guard_reason": "identity_unmatched_to_master",
            "lock_utc": r["lock_utc"], "board_ts": board_ts,
        })
    key = f"EVSweep/mod04c/shadow/nba_assists/{target_date}.json"
    shadow.write_predictions_json(shadow_rows, "nba_assists", version, key)
    scored = sum(1 for r in shadow_rows if r.get("guard_status") == "scored")
    excluded = sum(1 for r in shadow_rows if r.get("guard_status") == "excluded")
    print(f"nba_assists(board): scored={scored} excluded={excluded} total={len(shadow_rows)} snapshot={snap_ts} -> {key}")
    return {"scored": scored, "excluded": excluded, "total": len(shadow_rows), "key": key}


def _score_nhl_board(target_date: str, c_serving: float, version: str) -> dict:
    sk = storage.read_csv(NHL_SKATER_MASTER)
    split_key = "NHL/assist_split/assist_split.csv"
    if storage.exists(split_key):
        sp = storage.read_csv(split_key)
        sk = sk.merge(sp[["game_id", "player_id", "primary_assists", "secondary_assists"]], on=["game_id", "player_id"], how="left")
        sk["primary_assists"] = sk["primary_assists"].fillna(0)
        sk["secondary_assists"] = sk["secondary_assists"].fillna(0)
    else:
        sk["primary_assists"] = sk["assists"]
        sk["secondary_assists"] = 0
    goalies = storage.read_csv(NHL_GOALIE_MASTER) if storage.exists(NHL_GOALIE_MASTER) else None
    slate, unmatched, board_ts = _board_slate("nhl", sk, target_date)
    if slate.empty:
        print(f"nhl_assists(board): no matched board rows; unmatched={len(unmatched)}")
        return {"scored": 0, "excluded": 0, "total": 0}
    prior = filter_history(sk, target_date)
    panel = nhl_assists._prior_nhl_panel(prior, shots=None, goalies=goalies)
    tr = panel[(panel["games_played"] >= 1) & panel["assists"].notna()]
    prim_m = nhl_assists.PrimaryAssistsModel().fit(tr)
    sec_m = nhl_assists.SecondaryAssistsModel().fit(tr)
    feat = nhl_assists.build_nhl_assist_features(target_date, f"{target_date}T23:59:59Z", slate=slate, history_with_split=sk, shots=None, lineups=None, goalies=goalies)
    if "player_id" in feat.columns and len(feat) == len(slate):
        feat = feat.set_index("player_id").loc[slate["player_id"]].reset_index()
    pmf_p, _, _ = prim_m.predict_pmf(feat, kmax=15)
    pmf_s, _, _ = sec_m.predict_pmf(feat, kmax=15)
    pmf_conv = convolve_pmf(pmf_p, pmf_s)
    lines = slate["line"].astype(float).values
    p_model = pmf_p_over(pmf_conv, lines)
    feat["line_source"] = "board"
    shadow_rows = shadow.make_shadow_rows(
        "nhl_assists", nhl_assists.SPLIT_ENGINE_LABEL, feat, p_model,
        market_p=slate["p_over_market"].tolist(), lines=lines, c_serving=c_serving,
    )
    for row, lk in zip(shadow_rows, slate["lock_utc"].tolist()):
        row["lock_utc"] = lk
        row["board_ts"] = board_ts
    for r in unmatched.to_dict("records"):
        shadow_rows.append({
            "family": "nhl_assists", "engine": nhl_assists.SPLIT_ENGINE_LABEL, "date": target_date,
            "player_name": r["player_name"], "team": r["team"], "opponent": r["opponent"],
            "line": r["line"], "line_source": "board", "p_model": None, "p_served": None,
            "guard_status": "excluded", "guard_reason": "identity_unmatched_to_master",
            "lock_utc": r["lock_utc"], "board_ts": board_ts,
        })
    key = f"EVSweep/mod04c/shadow/nhl_assists/{target_date}.json"
    shadow.write_predictions_json(shadow_rows, "nhl_assists", version, key)
    print(f"nhl_assists(board): scored={len(shadow_rows) - len(unmatched)} unmatched={len(unmatched)} total={len(shadow_rows)} -> {key}")
    return {"scored": len(shadow_rows) - len(unmatched), "excluded": len(unmatched), "total": len(shadow_rows), "key": key}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score MOD-04c shadow assists predictions.")
    ap.add_argument("--date", required=True, help="Target game date YYYY-MM-DD")
    ap.add_argument("--family", choices=("nba_assists", "nhl_assists", "both"), default="both")
    ap.add_argument("--c-serving", type=float, default=None,
                    help="Blend weight for p_served; default per family in board mode (NBA 0.00, NHL 0.34), 0.0 in games mode")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--slate-source", choices=("games", "board"), default="games")
    args = ap.parse_args(argv)

    if args.slate_source == "board":
        if args.family in ("nba_assists", "both"):
            _score_nba_board(args.date, 0.00 if args.c_serving is None else args.c_serving, args.version)
        if args.family in ("nhl_assists", "both"):
            _score_nhl_board(args.date, 0.34 if args.c_serving is None else args.c_serving, args.version)
        return 0

    c = 0.0 if args.c_serving is None else args.c_serving
    if args.family in ("nba_assists", "both"):
        _score_nba(args.date, c, args.version)
    if args.family in ("nhl_assists", "both"):
        _score_nhl(args.date, c, args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
