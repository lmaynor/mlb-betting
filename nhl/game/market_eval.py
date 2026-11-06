"""Walk-forward score of the NHL game engine (nhl.game: Kalman ratings + goalie GSAx + team profiles -> Dixon-Coles / ENG score
matrix) against BettingPros CLOSING quotes (Odds/bettingpros_nhl_games/{moneyline,spread,total}/{day}.parquet, banked by
mlb_core.odds.bettingpros_games). `spread` is the puck line.

Per game the market is: real-sportsbook quotes (book_kind == "book": no Consensus pseudo-book, DFS, exchanges or opening
rows), main lines only, last-change stamp NOT AFTER 10 minutes past the scheduled puck drop (an in-play re-post is dropped,
never used as the close), >= 3 books quoting the SAME line with both sides; p_mkt = the median across those books of each
book's proportionally de-vigged probability; median / best price = across the same books.

The replay predicts a whole game day BEFORE any of that day's results are fed to the ratings (so a late game's result can never
inform an early game's price; `tests/test_nhl_market_eval.py` pins that by scrambling results on / after day t and
checking day t's predictions do not move). The engine is replayed as the daily runner builds it: every game in the team box
master (preseason included), goalie GSAx from the goalie master filtered to dates strictly before the game, unconfirmed
starters. `ARI` is not in `nhl.config.TEAMS`, so the deployed engine raises on Coyotes games and skips them in the Kalman
update; the default replay reproduces that (`--ari-as-uta` replays them as UTA instead, a sensitivity run).

    GCS_BUCKET=<bucket> python -m nhl.game.market_eval --out docs/analysis/nhl_game_market_eval.md
    python -m nhl.game.market_eval --local-dir <copy of Odds/bettingpros_nhl_games> --nhl-dir <copy of NHL masters>
    python -m nhl.game.market_eval --events events.csv --out report.md          # any prebuilt events table

UNVALIDATED research tool; nothing here is an adoption decision.
"""
from __future__ import annotations

import argparse
import logging
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from mlb_core.eval import market_scoring as ms
from mlb_core.odds import bettingpros_games as bg
from nhl.config import GAMES_MASTER, GOALIE_BOX_MASTER, TEAM_BOX_MASTER
from nhl.props.history import canon_team

log = logging.getLogger("nhl.game.market_eval")

PREFIX = bg.default_prefix("NHL")
MIN_BOOKS = 3
MIN_EVENTS = 200
MATCH_WINDOW_H = 6.0              # BettingPros `scheduled` vs the schedule's start_utc
SCORED_SEASON_TYPES = ("REG", "PST", "POST")     # BettingPros labels the playoffs PST (seen 2026-06); preseason is never scored
MARKET_LABELS = {"moneyline": "NHL moneyline (home wins)", "spread": "NHL puck line (home covers)",
                 "total": "NHL total (over, shootout excluded)"}


# ----------------------------------------------------------------------------------------------------- team codes / events
def to_et_date(scheduled) -> pd.Series:
    return pd.to_datetime(scheduled, utc=True, errors="coerce").dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")


def event_table(q: pd.DataFrame) -> pd.DataFrame:
    """One row per BettingPros event with the games-master tricodes (`home`, `away`). A code that survives canonicalisation
    unchanged and is not a 3-letter master code stays as is; the matcher counts it as `bad_code`."""
    ev = (q.sort_values("date").groupby("event_id")[["scheduled", "season_type", "event_status", "home", "away"]].last()
          .reset_index())
    ev["date_et"] = to_et_date(ev["scheduled"])
    ev["home_bp"], ev["away_bp"] = ev["home"], ev["away"]
    ev["home"] = [canon_team(c, d) for c, d in zip(ev["home_bp"], ev["date_et"])]
    ev["away"] = [canon_team(c, d) for c, d in zip(ev["away_bp"], ev["date_et"])]
    return ev


def match_to_games(ev: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Attach `game_id`, `season_year`, `date` (the schedule's local day), the result columns and `dt_min` (BettingPros
    scheduled minus the schedule's start, minutes). Pairs on (home, away) tricodes and takes the NEAREST start within
    MATCH_WINDOW_H, so two meetings of the same pair inside two days cannot be crossed. Adds `unmatched` = '' or a reason."""
    g = games[games["game_type"].isin([2, 3])][["game_id", "season_year", "game_type", "date", "start_utc", "home_abbr",
                                                "away_abbr", "home_score", "away_score", "home_goals_ex_so",
                                                "away_goals_ex_so", "last_period_type"]].copy()
    g["start_ts"] = pd.to_datetime(g["start_utc"], utc=True, errors="coerce")
    e = ev.copy()
    e["sched_ts"] = pd.to_datetime(e["scheduled"], utc=True, errors="coerce")
    m = e.merge(g, left_on=["home", "away"], right_on=["home_abbr", "away_abbr"], how="left", suffixes=("", "_g"))
    m["dt_min"] = (m["sched_ts"] - m["start_ts"]).dt.total_seconds() / 60.0
    m["abs_dt"] = m["dt_min"].abs()
    m = m.sort_values(["event_id", "abs_dt"], na_position="last").drop_duplicates("event_id", keep="first")
    ok = m["game_id"].notna() & (m["abs_dt"] <= MATCH_WINDOW_H * 60.0)
    known = set(g["home_abbr"]) | set(g["away_abbr"])
    m["unmatched"] = np.where(ok, "", np.where(~m["home"].isin(known) | ~m["away"].isin(known), "bad_code", "no_game"))
    m.loc[~ok, ["game_id", "season_year", "date"]] = np.nan
    return m.drop(columns=["start_ts", "sched_ts", "home_abbr", "away_abbr"]).reset_index(drop=True)


# ----------------------------------------------------------------------------------------------------- consensus
def _pairs(c: pd.DataFrame, market: str) -> pd.DataFrame:
    """One row per (event, book, line) with side-a / side-b American odds; the away side of a puck line is re-keyed onto the
    HOME handicap (away +1.5 == home -1.5) so the two sides pair. a = home (or over), b = away (or under)."""
    d = c[c["market"] == market].copy()
    if d.empty:
        return pd.DataFrame(columns=["event_id", "book_id", "line", "a", "b"])
    if market == "spread":
        d["lk"] = np.where(d["side"] == "home", d["line"], -d["line"])
    elif market == "total":
        d["lk"] = d["line"]
    else:
        d["lk"] = 0.0
    a_side, b_side = ("over", "under") if market == "total" else ("home", "away")
    d = d[d["side"].isin([a_side, b_side])]
    d["odds"] = d["odds"].astype(float)
    piv = d.pivot_table(index=["event_id", "book_id", "lk"], columns="side", values="odds", aggfunc="last")
    if a_side not in piv or b_side not in piv:
        return pd.DataFrame(columns=["event_id", "book_id", "line", "a", "b"])
    piv = piv[[a_side, b_side]].dropna().reset_index()
    piv.columns = ["event_id", "book_id", "line", "a", "b"]
    return piv


def _modal_line(lines: pd.Series, market: str) -> float:
    vc = lines.value_counts()
    top = vc[vc == vc.max()].index.values.astype(float)
    if market == "spread":
        return float(top[np.argmin(np.abs(top))])
    return float(top[np.argmin(np.abs(top - lines.median()))])


def consensus(c: pd.DataFrame, min_books: int = MIN_BOOKS) -> pd.DataFrame:
    """Per (event, market): modal line, median de-vigged P(a), median / best decimal prices per side, n books. `c` = the output
    of bettingpros_games.close_quotes. Wide rows, one per (event_id, market)."""
    rows = []
    for market in bg.MARKETS:
        pr = _pairs(c, market)
        for eid, g in pr.groupby("event_id"):
            line = _modal_line(g["line"], market) if market != "moneyline" else 0.0
            g = g[g["line"] == line] if market != "moneyline" else g
            if len(g) < min_books:
                continue
            pa = [ms.devig_pair(a, b)[0] for a, b in zip(g["a"], g["b"])]
            da = [ms.american_to_decimal_safe(x) for x in g["a"]]
            db = [ms.american_to_decimal_safe(x) for x in g["b"]]
            rows.append({"event_id": eid, "market": market, "line": line if market != "moneyline" else np.nan,
                         "p_mkt": float(np.median(pa)), "dec_a_med": float(np.median(da)), "dec_b_med": float(np.median(db)),
                         "dec_a_best": float(max(da)), "dec_b_best": float(max(db)), "n_books": int(len(g)),
                         "book_ids": ",".join(str(int(b)) for b in sorted(g["book_id"]))})
    return pd.DataFrame(rows)


def opening_pairs(q: pd.DataFrame, max_gap_s: float = 120.0) -> pd.DataFrame:
    """The opening quote as a PAIR: BettingPros stores one opening line per side, and only the rows where both sides name the
    same book and were created within `max_gap_s` seconds are an actual two-sided open. Spread: home line L, away -L must agree."""
    o = q[q["is_open"].astype(bool)].copy()
    if o.empty:
        return pd.DataFrame(columns=["event_id", "market", "open_book_id", "line_open", "dec_a_open", "dec_b_open"])
    o["ts"] = pd.to_datetime(o["updated"], errors="coerce")
    o = o[o["ts"] <= pd.to_datetime(o["scheduled"], errors="coerce")]      # an "opening" quote stamped after the drop is not an open
    out = []
    for market in bg.MARKETS:
        d = o[o["market"] == market]
        a_side, b_side = ("over", "under") if market == "total" else ("home", "away")
        a = d[d["side"] == a_side].drop_duplicates(["event_id"], keep="last")
        b = d[d["side"] == b_side].drop_duplicates(["event_id"], keep="last")
        j = a.merge(b, on="event_id", suffixes=("_a", "_b"))
        j = j[(j["book_id_a"] == j["book_id_b"]) & ((j["ts_a"] - j["ts_b"]).abs().dt.total_seconds() <= max_gap_s)]
        if market == "spread":
            j = j[(j["line_a"] + j["line_b"]).abs() < 1e-9]
        elif market == "total":
            j = j[(j["line_a"] - j["line_b"]).abs() < 1e-9]
        for r in j.itertuples(index=False):
            out.append({"event_id": r.event_id, "market": market, "open_book_id": int(r.book_id_a),
                        "line_open": float(r.line_a) if market != "moneyline" else np.nan,
                        "dec_a_open": ms.american_to_decimal_safe(r.odds_a), "dec_b_open": ms.american_to_decimal_safe(r.odds_b),
                        "open_ts": r.ts_a})
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------------------------------- masters / replay
def load_masters(nhl_dir: Optional[str] = None) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(games_master, team_boxscores_master, goalie_boxscores_master). nhl_dir = a local copy of the NHL/ prefix."""
    if nhl_dir:
        rd = lambda k: pd.read_csv(os.path.join(nhl_dir, os.path.basename(k)))   # noqa: E731
    else:
        from mlb_core import storage
        rd = storage.read_csv
    return rd(GAMES_MASTER), rd(TEAM_BOX_MASTER), rd(GOALIE_BOX_MASTER)


def _remap_ari(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        if c in df:
            df[c] = df[c].replace({"ARI": "UTA"})
    return df


def replay(team_box: pd.DataFrame, goalies: pd.DataFrame, lines: Dict[str, dict], ari_as_uta: bool = False,
           min_date: Optional[str] = None) -> pd.DataFrame:
    """Day-batched walk-forward replay. `lines` = {game_id: {"spread": home handicap or None, "total": line or None}}: a game is
    priced only if it is in `lines` (every game still updates the ratings). Returns one row per priced game:
    game_id, date, home, away, p_home_ml, p_home_cover (at lines[...]["spread"]), p_over (at ...["total"]), lam_h, lam_a.
    The prediction for day t is computed BEFORE any game of day t is fed to Kalman / the feature store."""
    from nhl.game.features import FeatureStore, build_team_game_stats_from_raw
    from nhl.game.goalie_model import build_goalie_tracker_from_df
    from nhl.game.kalman_ratings import KalmanTeamTracker
    from nhl.game.matrix_pricer import DistributionalNHLMatrixEngine

    if ari_as_uta:
        team_box = _remap_ari(team_box, ["team_abbr", "opp_abbr"])
        goalies = _remap_ari(goalies, ["team_abbr"])
    stats = build_team_game_stats_from_raw(team_box)
    by_game: Dict[str, Dict[str, object]] = {}
    for s in stats:
        by_game.setdefault(s.game_id, {})["home" if s.is_home else "away"] = s
    by_date: Dict[str, List[str]] = {}
    for gid, sides in by_game.items():
        if "home" in sides and "away" in sides:
            by_date.setdefault(sides["home"].game_date, []).append(gid)

    kalman, store, engine = KalmanTeamTracker(), FeatureStore(), DistributionalNHLMatrixEngine()
    tracker = build_goalie_tracker_from_df(goalies)
    out: List[dict] = []
    skipped = {"unknown_team": 0}
    for d in sorted(by_date):
        gids = sorted(by_date[d])
        kalman.roll_season(d)
        for gid in gids:                                            # 1. every prediction of the day, results not yet seen
            if gid not in lines or (min_date and d < min_date):
                continue
            h, a = by_game[gid]["home"], by_game[gid]["away"]
            try:
                kp = kalman.predict_game(h.team, a.team, d)
            except ValueError:                                      # `ARI` as deployed
                skipped["unknown_team"] += 1
                continue
            hp, ap = tracker.project_team_goalie(h.team, d), tracker.project_team_goalie(a.team, d)
            mu = store.build_matchup_features(h.team, a.team, d, home_goalie_gsax_60=hp.projected_gsax_60,
                                              away_goalie_gsax_60=ap.projected_gsax_60)
            lh, la = engine.compute_scoring_rates(kp, mu)
            m = engine.compute_score_matrix(lh, la)
            ln = lines[gid]
            sp, tot = ln.get("spread"), ln.get("total")
            out.append({"game_id": gid, "date": d, "home": h.team, "away": a.team, "lam_h": lh, "lam_a": la,
                        "p_home_ml": float(m.p_home_ml),
                        "p_home_cover": float(m.puck_line_two_way(float(sp))[0]) if sp is not None and not pd.isna(sp) else np.nan,
                        "p_over": float(m.total(float(tot))[3]) if tot is not None and not pd.isna(tot) else np.nan})
        for gid in gids:                                            # 2. only now feed the day's results
            h, a = by_game[gid]["home"], by_game[gid]["away"]
            kalman.update_game(home=h.team, away=h.opp, home_goals=h.goals, away_goals=h.goals_against, home_sog=h.sog,
                               away_sog=h.shots_against, minutes=h.minutes, game_date=d)
            store.add_game_stat(h)
            store.add_game_stat(a)
    res = pd.DataFrame(out)
    res.attrs["skipped"] = skipped
    return res


# ----------------------------------------------------------------------------------------------------- events + report
def build_events(pred: pd.DataFrame, games: pd.DataFrame, cons: pd.DataFrame, evm: pd.DataFrame,
                 opens: Optional[pd.DataFrame] = None) -> Dict[str, pd.DataFrame]:
    """Per market, the table market_scoring wants: date, p_model, p_mkt, y, prices, plus season / n_books / line columns.
    Outcomes: moneyline = final score incl. OT / shootout; puck line = final-score margin (a shootout win is a 1-goal margin);
    total = goals EXCLUDING the shootout. Pushes (total on an integer line) are dropped."""
    res = games.set_index("game_id")
    link = evm[evm["unmatched"] == ""][["event_id", "game_id", "season_year"]]
    link = link.assign(game_id=link["game_id"].astype(np.int64).astype(str))
    pred = pred.assign(game_id=pred["game_id"].astype(str))
    res.index = res.index.astype(str)
    out: Dict[str, pd.DataFrame] = {}
    for market in bg.MARKETS:
        c = cons[cons["market"] == market].merge(link, on="event_id").merge(pred, on="game_id")
        if opens is not None and len(opens):
            c = c.merge(opens[opens["market"] == market].drop(columns=["market"]), on="event_id", how="left")
        rows = []
        for r in c.itertuples(index=False):
            g = res.loc[r.game_id]
            hs, as_ = float(g["home_score"]), float(g["away_score"])
            hx, ax = float(g["home_goals_ex_so"]), float(g["away_goals_ex_so"])
            if market == "moneyline":
                p, y = r.p_home_ml, int(hs > as_)
            elif market == "spread":
                p = r.p_home_cover
                m = (hs - as_) + r.line
                y = None if abs(m) < 1e-9 else int(m > 0)
            else:
                p = r.p_over
                m = (hx + ax) - r.line
                y = None if abs(m) < 1e-9 else int(m > 0)
            if y is None or p is None or pd.isna(p):
                continue
            row = {"date": r.date, "season": int(r.season_year), "game_id": r.game_id, "event_id": r.event_id, "p_model": float(p),
                   "p_mkt": r.p_mkt, "y": y, "line": r.line, "n_books": r.n_books, "dec_a_med": r.dec_a_med,
                   "dec_b_med": r.dec_b_med, "dec_a_best": r.dec_a_best, "dec_b_best": r.dec_b_best}
            if hasattr(r, "dec_a_open") and not pd.isna(getattr(r, "dec_a_open")):
                row.update({"dec_a_open": r.dec_a_open, "dec_b_open": r.dec_b_open,
                            "line_open": r.line_open if market != "moneyline" else np.nan,
                            "line_close": r.line if market != "moneyline" else np.nan, "open_book_id": r.open_book_id})
            rows.append(row)
        out[market] = pd.DataFrame(rows)
    return out


def per_season(ev: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for s, g in ev.groupby("season"):
        ll = ms.paired_logloss(g)
        bl = ms.blend_weight(g) if len(g) >= 60 else {"c_model": float("nan"), "z_c": float("nan")}
        rows.append({"season": int(s), "n": int(len(g)), "ll_gain": ll["mean"], "z": ll["z"], "c": bl["c_model"], "z_c": bl["z_c"]})
    return pd.DataFrame(rows)


def report_markdown(events: Dict[str, pd.DataFrame], extra: str = "", close: Optional[pd.DataFrame] = None) -> str:
    lines = ["## NHL game engine vs BettingPros closing quotes", ""]
    if extra:
        lines += [extra, ""]
    for market, ev in events.items():
        name = MARKET_LABELS[market]
        if len(ev) < 30:
            lines += [f"### {name}: n={len(ev)} (too few)", ""]
            continue
        lines += [ms.render_markdown(ms.score_market(ev, name))]
        if close is not None:
            sb = same_book_clv(ev, close, market)
            if sb.get("n"):
                m, c, d = sb["model"], sb["control"], sb["diff"]
                lines.append(f"- SAME-BOOK open-to-close price CLV (opening book vs its own close, identical line, n={sb['n']}): "
                             f"model side {m['mean']:+.3%} (z {m['z']:+.2f}), no-model control (both sides) {c['mean']:+.3%} "
                             f"(z {c['z']:+.2f}), model minus control {d['mean']:+.3%} (z {d['z']:+.2f})")
                ro = sb["roi_open"]
                big = (f"; model-minus-control on the {ro['n_big']} games with a >= 4% model-market gap "
                       f"{ro['diff_big']['mean']:+.2%} (z {ro['diff_big']['z']:+.2f})") if ro["diff_big"].get("mean") is not None else ""
                lines.append(f"  - REALISED ROI at the opening price (same book, same line, flat 1u): model side {ro['model']['mean']:+.2%} "
                             f"(se {ro['model']['se']:.2%}), both sides blind {ro['control']['mean']:+.2%}, model minus control "
                             f"{ro['diff']['mean']:+.2%} (z {ro['diff']['z']:+.2f}){big}")
                side_a = "over" if market == "total" else "home"
                side_b = "under" if market == "total" else "away"
                lines.append(f"  - by side: the model leans {side_a} in {sb['share_on_a']:.0%} of games; average open-to-close drift of the "
                             f"{side_a} price {sb['drift_a']:+.3%}, of the {side_b} price {sb['drift_b']:+.3%}; model CLV when it leans "
                             f"{side_a} {sb['model_when_a']:+.3%}, when it leans {side_b} {sb['model_when_b']:+.3%}")
        vo = vs_open(ev, market)
        if vo.get("open"):
            lines.append(f"- model vs the OPENING line on the same {vo['n']} events: paired gain vs open {vo['open']['mean']:+.4f} "
                         f"(z {vo['open']['z']:+.2f}), vs close {vo['close']['mean']:+.4f} (z {vo['close']['z']:+.2f})  [positive = model better]")
        lines.append("")
        ps = per_season(ev)
        lines += ["per season (paired log-loss gain, positive = model better; c = blend weight on model - market):", "",
                  "| season | n | gain | z | c | z_c |", "|---|---|---|---|---|---|"]
        lines += [f"| {r.season}-{str(r.season + 1)[2:]} | {r.n} | {r.ll_gain:+.4f} | {r.z:+.2f} | {r.c:+.3f} | {r.z_c:+.2f} |"
                  for r in ps.itertuples()]
        lines.append("")
    return "\n".join(lines)


def same_book_clv(ev: pd.DataFrame, close: pd.DataFrame, market: str) -> Dict[str, dict]:
    """Open-to-close price CLV against the SAME book's close on the IDENTICAL line. The harness's `clv_price` compares one
    book's opening price with the cross-book MEDIAN close, which mixes book-to-book price differences into the number, so this
    pairs the opening book with its own last pre-drop quote. Returns clustered stats for the model's side, for the no-model
    control (mean of both sides: what the open-to-close drift is worth with no view at all) and for their paired difference.
    The opening quote is one book's first post, an entry that is only hypothetical: it is not a price anyone could have taken
    at a time the model had run."""
    if "open_book_id" not in ev.columns or "event_id" not in ev.columns:
        return {"n": 0}
    e = ev.dropna(subset=["dec_a_open", "dec_b_open"]).copy()
    if market != "moneyline":
        e = e[e["line_open"] == e["line_close"]]
    if e.empty:
        return {"n": 0}
    e["lk"] = 0.0 if market == "moneyline" else e["line_open"].astype(float)
    pr = _pairs(close, market).rename(columns={"book_id": "open_book_id", "line": "lk", "a": "a_close", "b": "b_close"})
    pr["event_id"] = pr["event_id"].astype("int64")
    pr["open_book_id"] = pr["open_book_id"].astype("int64")
    e["event_id"] = e["event_id"].astype("int64")
    e["open_book_id"] = e["open_book_id"].astype("int64")
    j = e.merge(pr, on=["event_id", "open_book_id", "lk"])
    if j.empty:
        return {"n": 0}
    ca = j["a_close"].map(ms.american_to_decimal_safe)
    cb = j["b_close"].map(ms.american_to_decimal_safe)
    clv_a, clv_b = j["dec_a_open"] / ca - 1.0, j["dec_b_open"] / cb - 1.0
    model = np.where(j["p_model"] > j["p_mkt"], clv_a, clv_b)
    control = (clv_a + clv_b) / 2.0
    on_a = (j["p_model"] > j["p_mkt"]).to_numpy()
    # Realised flat-stake ROI of taking the opening price on the model's side vs both sides blind (same book, same line)
    y = j["y"].to_numpy(dtype=float)
    roi_a, roi_b = y * j["dec_a_open"].to_numpy(dtype=float) - 1.0, (1.0 - y) * j["dec_b_open"].to_numpy(dtype=float) - 1.0
    roi_model = np.where(on_a, roi_a, roi_b)
    roi_ctrl = (roi_a + roi_b) / 2.0
    edge = (j["p_model"] - j["p_mkt"]).abs().to_numpy()
    big = edge >= 0.04
    roi = {"n_big": int(big.sum()),
           "model": ms.clustered_mean(roi_model, j["date"]), "control": ms.clustered_mean(roi_ctrl, j["date"]),
           "diff": ms.clustered_mean(roi_model - roi_ctrl, j["date"]),
           "model_big": ms.clustered_mean(roi_model[big], j["date"].to_numpy()[big]) if big.sum() > 30 else {"n": int(big.sum())},
           "diff_big": ms.clustered_mean((roi_model - roi_ctrl)[big], j["date"].to_numpy()[big]) if big.sum() > 30 else {"n": int(big.sum())}}
    # A model that simply leans the side the market drifts toward (e.g. overs) earns "CLV" with no skill: show the drift by side
    return {"n": int(len(j)), "model": ms.clustered_mean(model, j["date"]), "control": ms.clustered_mean(control, j["date"]),
            "diff": ms.clustered_mean(model - control, j["date"]), "roi_open": roi, "share_on_a": float(on_a.mean()),
            "drift_a": float(clv_a.mean()), "drift_b": float(clv_b.mean()),
            "model_when_a": float(clv_a[on_a].mean()) if on_a.any() else float("nan"),
            "model_when_b": float(clv_b[~on_a].mean()) if (~on_a).any() else float("nan")}


def vs_open(ev: pd.DataFrame, market: str) -> Dict[str, object]:
    """Model vs the OPENING line on the events with a two-sided open at the same line as the close: does the model beat the
    first post even though it loses to the close? p_open = the opening book's proportionally de-vigged probability (one book,
    so noisier than the cross-book close; the close is scored on the same events for a like-for-like gap)."""
    if "dec_a_open" not in ev.columns:
        return {"n": 0}
    e = ev.dropna(subset=["dec_a_open", "dec_b_open"]).copy()
    if market != "moneyline":
        e = e[e["line_open"] == e["line_close"]]
    if len(e) < 30:
        return {"n": int(len(e))}
    ia, ib = 1.0 / e["dec_a_open"], 1.0 / e["dec_b_open"]
    e["p_open"] = ia / (ia + ib)
    out = {"n": int(len(e))}
    for name, other in (("open", "p_open"), ("close", "p_mkt")):
        d = e.assign(p_mkt=e[other])
        out[name] = ms.paired_logloss(d)
    return out


def coverage_lines(ctx: dict) -> List[str]:
    """What a reader needs to judge the closes: how stale the last-change stamps are, how many books, how much was dropped."""
    c = ctx["close"]
    if c.empty:
        return []
    g = -c["gap_min"]
    out = [f"- close quotes used: {len(c)} real-book rows; last change vs puck drop: median {g.median():.0f} min before, "
           f"{(g <= 60).mean():.0%} within 1 h, {(g <= 360).mean():.0%} within 6 h, {(g > 1440).mean():.1%} more than a day stale"]
    cons = ctx["cons"]
    if len(cons):
        nb = cons.groupby("market")["n_books"].agg(["median", "min", "max"])
        out.append("- books on the modal line per game (median / min / max): " + "; ".join(
            f"{m} {int(r['median'])}/{int(r['min'])}/{int(r['max'])}" for m, r in nb.iterrows()))
    return out



def run(local_dir: Optional[str] = None, nhl_dir: Optional[str] = None, ari_as_uta: bool = False, fresh_hours: Optional[float] = None,
        min_books: int = MIN_BOOKS, since: Optional[str] = None,
        exclude_book_ids: Optional[List[int]] = None) -> Tuple[Dict[str, pd.DataFrame], dict]:
    """Everything end to end. Returns ({market: events table}, context for the report)."""
    raw = bg.read_raw("NHL", local_dir=local_dir, start=since)
    q_all = bg.dedupe(raw)
    q = q_all[q_all["season_type"].isin(SCORED_SEASON_TYPES)]
    c = bg.close_quotes(q)
    c_all = c
    if fresh_hours is not None:
        c = c[c["gap_min"] >= -60.0 * fresh_hours]
    if exclude_book_ids:
        c = c[~c["book_id"].isin(list(exclude_book_ids))]
    cons = consensus(c, min_books)
    opens = opening_pairs(q)
    ev = event_table(q)
    games, team_box, goalies = load_masters(nhl_dir)
    evm = match_to_games(ev, games)
    ok = evm[evm["unmatched"] == ""]
    cons_ok = cons[cons["event_id"].isin(ok["event_id"])]
    gid_of = dict(zip(ok["event_id"], ok["game_id"].astype(np.int64).astype(str)))
    lines: Dict[str, dict] = {}
    for r in cons_ok.itertuples(index=False):
        lines.setdefault(gid_of[r.event_id], {})
        if r.market == "spread":
            lines[gid_of[r.event_id]]["spread"] = r.line
        elif r.market == "total":
            lines[gid_of[r.event_id]]["total"] = r.line
        else:
            lines[gid_of[r.event_id]]["ml"] = True
    team_box = team_box.assign(game_id=team_box["game_id"].astype(str))
    pred = replay(team_box, goalies, lines, ari_as_uta=ari_as_uta, min_date=since)
    games = games.assign(game_id=games["game_id"].astype(str))
    events = build_events(pred, games, cons_ok, evm, opens)
    ctx = {"n_bp_events": int(len(ev)), "unmatched": evm["unmatched"].value_counts().to_dict(), "skipped": pred.attrs.get("skipped", {}),
           "close": c, "cons": cons_ok, "evm": evm, "pred": pred, "raw_rows": int(len(raw)), "q_all": q_all, "q": q,
           "games": games, "close_unfiltered": c_all}
    return events, ctx


def validation_lines(ctx: dict) -> List[str]:
    """Data-quality checks on the banked quotes and the join; every count here is a fact about the data, not a model result."""
    q_all, q, evm, games, c = ctx["q_all"], ctx["q"], ctx["evm"], ctx["games"], ctx["close_unfiltered"]
    out = ["## Validation", "", f"- rows read {ctx['raw_rows']}, after de-duplication {len(q_all)} "
           f"({ctx['raw_rows'] - len(q_all)} duplicate quote rows removed: one event sits under two adjacent date partitions)"]
    st = q_all.drop_duplicates("event_id")
    out.append(f"- events by season_type: {st['season_type'].value_counts().to_dict()}; by status: {st['event_status'].value_counts().to_dict()}")
    key = ["market", "event_id", "side", "book_id", "line", "is_open"]
    out.append(f"- duplicate quote keys after de-duplication: {int(q_all.duplicated(key).sum())}; (event, market, side, book) with more than "
               f"one quoted line: {int((q_all[~q_all['is_open'].astype(bool)].groupby(['market', 'event_id', 'side', 'book_id']).size() > 1).sum())}")
    # tricodes
    codes = sorted(set(evm["home_bp"]) | set(evm["away_bp"]))
    mapped = {k: v for k, v in zip(pd.concat([evm["home_bp"], evm["away_bp"]]), pd.concat([evm["home"], evm["away"]])) if k != v}
    known = set(games["home_abbr"]) | set(games["away_abbr"])
    bad = sorted({v for v in set(evm["home"]) | set(evm["away"]) if v not in known})
    out.append(f"- BettingPros tricodes seen: {len(codes)}; renamed to the schedule's: {dict(sorted(mapped.items()))}; "
               f"codes not in the schedule after mapping: {bad}")
    um = evm["unmatched"].value_counts().to_dict()
    ok = evm[evm["unmatched"] == ""]
    dt = ok["dt_min"]
    out.append(f"- events matched to a schedule game: {len(ok)} of {len(evm)} (unmatched: { {k: v for k, v in um.items() if k} }); "
               f"BettingPros scheduled minus schedule start: median {dt.median():+.0f} min, p1 {dt.quantile(.01):+.0f}, p99 {dt.quantile(.99):+.0f}, "
               f"max |{dt.abs().max():.0f}| min")
    dup_games = ok[ok.duplicated("game_id", keep=False)]
    out.append(f"- schedule games matched by more than one BettingPros event: {dup_games['game_id'].nunique()}")
    # coverage per season
    g = games[games["game_type"].isin([2, 3])]
    g = g[g["date"] >= (q_all["scheduled"].min()[:10] if len(q_all) else "9999")]
    ms_ = ctx["cons"].merge(ok[["event_id", "season_year"]], on="event_id")
    rows = []
    for s, gg in g.groupby("season_year"):
        e = ok[ok["season_year"] == s]
        r = {"season": f"{int(s)}-{str(int(s) + 1)[2:]}", "schedule_games": len(gg), "bp_events_matched": len(e),
             "coverage": f"{len(e) / len(gg):.1%}"}
        for m in bg.MARKETS:
            r[f"{m}_3books"] = int((ms_[(ms_["season_year"] == s) & (ms_["market"] == m)]["event_id"].nunique()))
        rows.append(r)
    if rows:
        cols = list(rows[0])
        out += ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        out += ["| " + " | ".join(str(r[c_]) for c_ in cols) + " |" for r in rows]
    # per-book coverage: share of matched events with a close quote from the book, by market
    if len(c):
        cc = c[c["event_id"].isin(ok["event_id"])].merge(ok[["event_id", "season_year"]], on="event_id")
        n_ev = ok.groupby("season_year")["event_id"].nunique()
        tab = (cc.drop_duplicates(["market", "event_id", "book"]).groupby(["season_year", "market", "book"])["event_id"].nunique()
               .reset_index())
        tab["share"] = tab.apply(lambda r: r["event_id"] / n_ev[r["season_year"]], axis=1)
        pv = tab.pivot_table(index="book", columns=["market", "season_year"], values="share")
        out += ["", "per-book share of matched events with a pre-drop close quote (moneyline / puck line / total, by season start year):", ""]
        cols = list(pv.columns)
        out += ["| book | " + " | ".join(f"{m[:2]} {int(s)}" for m, s in cols) + " |", "|---|" + "---|" * len(cols)]
        out += [f"| {b} | " + " | ".join("-" if pd.isna(v) else f"{v:.0%}" for v in row) + " |" for b, row in pv.iterrows()]
    # leakage invariants
    real = q[(q["book_kind"] == "book") & (~q["is_open"].astype(bool))]
    gap = (pd.to_datetime(real["updated"], errors="coerce") - pd.to_datetime(real["scheduled"], errors="coerce")).dt.total_seconds() / 60.0
    op = q[q["is_open"].astype(bool)]
    ogap = (pd.to_datetime(op["updated"], errors="coerce") - pd.to_datetime(op["scheduled"], errors="coerce")).dt.total_seconds() / 60.0
    out += ["", f"- leakage invariants: max (last-change stamp minus scheduled start) among close quotes = {c['gap_min'].max():+.1f} min "
            f"(grace {bg.GRACE_MIN:.0f}); real-book quotes dropped as in-play re-posts = {int((gap > bg.GRACE_MIN).sum())} of {len(real)}, "
            f"unparseable stamps dropped = {int(gap.isna().sum())}; opening quotes created after the drop = {int((ogap > 0).sum())} of {len(op)}"]
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--events", default=None, help="CSV with date,p_model,p_mkt,y[,dec_* prices] (score a prebuilt table)")
    ap.add_argument("--market", default="nhl game line")
    ap.add_argument("--local-dir", default=None, help="local copy of Odds/bettingpros_nhl_games (moneyline/ spread/ total/)")
    ap.add_argument("--nhl-dir", default=None, help="local copy of the NHL/ masters (games, team box, goalie box CSVs)")
    ap.add_argument("--ari-as-uta", action="store_true")
    ap.add_argument("--fresh-hours", type=float, default=None, help="only quotes last changed within this many hours of puck drop")
    ap.add_argument("--min-books", type=int, default=MIN_BOOKS)
    ap.add_argument("--exclude-book-ids", default="", help="comma list of BettingPros book ids to drop from the close (39 = Fliff)")
    ap.add_argument("--since", default=None, help="first game day to price (history before it still trains the ratings)")
    ap.add_argument("--validate", action="store_true", help="append the data-quality / coverage / join validation section")
    ap.add_argument("--save-events", default=None, help="directory to write the per-market events tables (CSV)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.events:
        ev = pd.read_csv(args.events)
        if len(ev) < MIN_EVENTS:
            print(f"Only {len(ev)} events; need >= {MIN_EVENTS} before any verdict means anything.")
            return 2
        text = ms.render_markdown(ms.score_market(ev, args.market))
    else:
        events, ctx = run(args.local_dir, args.nhl_dir, args.ari_as_uta, args.fresh_hours, args.min_books, args.since,
                           [int(x) for x in args.exclude_book_ids.split(",") if x])
        info = (f"BettingPros events {ctx['n_bp_events']}; unmatched to the schedule: {ctx['unmatched']}; skipped by the engine: "
                f"{ctx['skipped']}; ari_as_uta={args.ari_as_uta}; fresh_hours={args.fresh_hours}; min_books={args.min_books}")
        info = "\n".join([info] + coverage_lines(ctx))
        text = report_markdown(events, info, close=ctx["close"])
        if args.validate:
            text += "\n\n" + "\n".join(validation_lines(ctx))
        if args.save_events:
            os.makedirs(args.save_events, exist_ok=True)
            for m, e in events.items():
                e.to_csv(os.path.join(args.save_events, f"nhl_events_{m}.csv"), index=False)
    print(text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
