"""Walk-forward score of the NBA game engine (nba.game: Kalman ratings + Four Factors + distributional margin / total)
against BettingPros CLOSING quotes (Odds/bettingpros_nba_games/{moneyline,spread,total}/{day}.parquet).

COVERAGE LIMIT, measured 2026-11-04: the engine's history loader (nba.game.features.build_team_game_stats_from_raw) keeps
only the games whose team box has rebounds and turnovers, which is 2025-26 onward (2,858 team-games of 18,586; 14,844 dropped
as `nan_stats`). So this replays the engine AS DEPLOYED on the seasons it can see, not 2021-2025. The earlier research models in
nba/games (branch feat/nba-game-models) covered 2021-2025 with a different feature set.
UPDATE (Argon fix, #183): the loader now backfills the 2019-24 NaN rebounds/turnovers from the player box master
(load_player_boxscores, wired into replay() below), so the full history is reachable; a full re-run on the
backfilled history is still pending, so the 2026-11-04 coverage numbers above stand until it lands.

Market per game = books whose quote was updated within 4 h before / 15 min after the scheduled tip (BettingPros keeps a last quote
per book, some days old); >= 3 books quoting the SAME main line, both sides; the Consensus pseudo-book is excluded. p_mkt = the
median across those books of each book's proportionally de-vigged probability; median / best price = across the same books.

    GCS_BUCKET=<bucket> python -m nba.game.market_eval --out docs/analysis/nba_game_market_eval.md
UNVALIDATED research tool.
"""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from mlb_core import storage
from mlb_core.eval import market_scoring as ms
from nba.game.evaluate import evaluate_walk_forward
from nba.game.features import build_team_game_stats_from_raw, sb_team_code
from nba.game.run_daily import load_historical_boxscores, load_player_boxscores
from nba.teams import team_code

log = logging.getLogger("nba.game.market_eval")
PREFIX = "Odds/bettingpros_nba_games"
BP_TO_TRICODE = {"PHO": "PHX", "NOR": "NOP", "UTH": "UTA"}      # BettingPros spellings
MIN_BOOKS = 3
FRESH_BEFORE_H, FRESH_AFTER_MIN = 4.0, 15.0


def _tri(x: str) -> Optional[str]:
    return BP_TO_TRICODE.get(x) or team_code(x) or None


def load_quotes(min_date: str, max_date: str, markets=("moneyline", "spread", "total"),
                local_dir: Optional[str] = None) -> pd.DataFrame:
    """Quote partitions for the day range. local_dir = a copy of the prefix made with `gcloud storage cp -r` (each
    storage.read_parquet call measured ~10 s from a laptop, so the bucket path is for small ranges only)."""
    if local_dir:
        import glob
        import os
        files = [f for m in markets for f in glob.glob(os.path.join(local_dir, m, "*.parquet"))
                 if min_date <= os.path.basename(f).split(".")[0] <= max_date]
        q = pd.concat([pd.read_parquet(f) for f in sorted(files)], ignore_index=True) if files else pd.DataFrame()
        q = q[q.columns[q.notna().any()]] if len(q) else q
        log.info("loaded %d local quote files, %d rows", len(files), len(q))
        return q
    keys = []
    for m in markets:
        for k in storage.list_keys(f"{PREFIX}/{m}/"):
            day = k.rsplit("/", 1)[-1].split(".")[0]
            if min_date <= day <= max_date:
                keys.append(k)
    with ThreadPoolExecutor(8) as ex:
        frames = list(ex.map(storage.read_parquet, keys))
    q = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    log.info("loaded %d quote files, %d rows", len(keys), len(q))
    return q


def _fresh_close(q: pd.DataFrame) -> pd.DataFrame:
    q = q[(q["is_open"] == False) & (q["book_kind"] == "book")].copy()    # noqa: E712
    tip = pd.to_datetime(q["scheduled"])
    upd = pd.to_datetime(q["updated"])
    ok = (upd >= tip - pd.Timedelta(hours=FRESH_BEFORE_H)) & (upd <= tip + pd.Timedelta(minutes=FRESH_AFTER_MIN))
    return q[ok]


def market_by_game(q: pd.DataFrame) -> Dict[Tuple[str, str, str], dict]:
    """{(home, away, ET day): {'ml': {...}, 'spread': {...}, 'total': {...}}} from fresh closing quotes."""
    out: Dict[Tuple[str, str, str], dict] = {}
    fq = _fresh_close(q)
    for (mk, ev), g in fq.groupby(["market", "event_id"]):
        home, away = _tri(g["home"].iloc[0]), _tri(g["away"].iloc[0])
        if not home or not away:
            continue
        a_side, b_side = ("home", "away") if mk != "total" else ("over", "under")
        books = []
        for _, bg in g.groupby("book"):
            a = bg[bg["side"] == a_side]
            b = bg[bg["side"] == b_side]
            if len(a) and len(b):
                a, b = a.iloc[-1], b.iloc[-1]
                books.append({"line": float(a["line"]) if mk != "moneyline" else 0.0, "a": a["odds"], "b": b["odds"]})
        if not books:
            continue
        if mk != "moneyline":                       # keep the books on the modal line (ties: smallest |line|)
            lines = pd.Series([b["line"] for b in books])
            top = lines.value_counts()
            line = min(top[top == top.max()].index, key=abs)
            books = [b for b in books if b["line"] == line]
        else:
            line = None
        if len(books) < MIN_BOOKS:
            continue
        pa = [ms.devig_pair(b["a"], b["b"])[0] for b in books]
        da = [ms.american_to_decimal_safe(b["a"]) for b in books]
        db = [ms.american_to_decimal_safe(b["b"]) for b in books]
        day = str(g["date"].iloc[0])[:10]
        out.setdefault((home, away, day), {})[{"moneyline": "ml"}.get(mk, mk)] = {
            "line": line, "p": float(np.median(pa)), "dec_a_med": float(np.median(da)), "dec_b_med": float(np.median(db)),
            "dec_a_best": float(max(da)), "dec_b_best": float(max(db)), "n": len(books)}
    return out


def _find(mkt: Dict[Tuple[str, str, str], dict], home: str, away: str, day: str) -> Optional[dict]:
    d0 = pd.Timestamp(day)
    for off in (0, 1, -1):                               # the master's date may be the UTC day
        v = mkt.get((home, away, (d0 + pd.Timedelta(days=off)).date().isoformat()))
        if v:
            return v
    return None


def dropped_history_flags(raw: pd.DataFrame, kept_game_ids, window: int = 10) -> Dict[Tuple[str, str], bool]:
    """Data-quality flag per (team, ET day): True when any of the team's previous `window` raw (non-preseason) box games was
    DROPPED by build_team_game_stats_from_raw (nan_stats / not-two-rows / unknown team), i.e. the engine's rolling state for
    that team has a hole. Uses only games strictly before the day, so it is pre-game information."""
    if raw is None or raw.empty:
        return {}
    df = raw
    if "season_type" in df.columns:
        df = df[~df["season_type"].astype(str).str.lower().eq("preseason")]
    kept = set(map(str, kept_game_ids))
    out: Dict[Tuple[str, str], bool] = {}
    per_team: Dict[str, List[Tuple[str, bool]]] = {}
    for r in df[["game_id", "team_abbr", "date"]].itertuples(index=False):
        t = sb_team_code(r.team_abbr)
        if t:
            per_team.setdefault(t, []).append((str(r.date)[:10], str(r.game_id) in kept))
    import bisect
    for t, lst in per_team.items():
        lst.sort()
        days = [d for d, _ in lst]
        bad = np.cumsum([0 if k else 1 for _, k in lst])
        for d in sorted(set(days)):
            i = bisect.bisect_left(days, d)               # games strictly before day d
            lo = max(0, i - window)
            out[(t, d)] = bool(i > lo and (bad[i - 1] - (bad[lo - 1] if lo > 0 else 0)) > 0)
    return out


def replay(local_dir: Optional[str] = None) -> pd.DataFrame:
    raw = load_historical_boxscores()
    games = build_team_game_stats_from_raw(raw, df_player_boxscores=load_player_boxscores())
    dq = dropped_history_flags(raw, {g.game_id for g in games})
    days = sorted({g.game_date[:10] for g in games})
    mkt = market_by_game(load_quotes(days[0], (pd.Timestamp(days[-1]) + pd.Timedelta(days=1)).date().isoformat(),
                                     local_dir=local_dir))
    closing: Dict[str, Dict[str, float]] = {}
    ids = {}
    away_of = {}
    for g in games:
        if g.is_home:
            ids[g.game_id] = g
        else:
            away_of[g.game_id] = g
    for gid, h in ids.items():
        a = away_of.get(gid)
        m = _find(mkt, h.team, a.team, h.game_date[:10]) if a else None
        if m:
            c = {}
            if "spread" in m:
                c["spread"] = m["spread"]["line"]
            if "total" in m:
                c["total"] = m["total"]["line"]
            closing[gid] = c
    rep = evaluate_walk_forward(games, closing_odds=closing)
    rows = []
    for r in rep.records:
        m = _find(mkt, r.home_team, r.away_team, r.game_date[:10]) or {}
        rows.append({"game_id": r.game_id, "date": r.game_date[:10], "home": r.home_team, "away": r.away_team,
                     "p_home_ml": r.pred_p_home_ml, "p_home_cover": r.pred_home_cover, "p_over": r.pred_over,
                     "mu_margin": r.pred_margin, "mu_total": r.pred_total, "margin": r.actual_margin,
                     "total": r.actual_total, "ml": m.get("ml"), "spread": m.get("spread"), "total_m": m.get("total"),
                     "kal_margin": r.kal_margin, "kal_total": r.kal_total, "feat_margin": r.feat_margin,
                     "feat_total": r.feat_total, "kal_std_margin": r.kal_std_margin, "kal_std_total": r.kal_std_total,
                     "sd_margin": r.sd_margin, "sd_total": r.sd_total, "n_prior_min": r.n_prior_min,
                     "dq_bad": bool(dq.get((r.home_team, r.game_date[:10]), False)
                                    or dq.get((r.away_team, r.game_date[:10]), False))})
    return pd.DataFrame(rows)


def to_events(rec: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}

    def build(name, mk_col, p_col, y_fn, push_fn, kind):
        rows = []
        for r in rec.itertuples(index=False):
            m = getattr(r, mk_col)
            p = getattr(r, p_col)
            if not isinstance(m, dict) or p is None or pd.isna(p) or push_fn(r, m):
                continue
            sp = r.spread if isinstance(r.spread, dict) else None
            if kind == "margin":
                conf = {"c_kal": r.kal_margin, "c_feat": r.feat_margin, "c_mu": r.mu_margin,
                        "c_line": (-sp["line"]) if sp else np.nan, "c_sd": r.kal_std_margin}
            else:
                conf = {"c_kal": r.kal_total, "c_feat": r.feat_total, "c_mu": r.mu_total, "c_line": m["line"],
                        "c_sd": r.kal_std_total}
            d0 = str(r.date)
            rows.append({"date": r.date, "p_model": float(p), "p_mkt": m["p"], "y": int(y_fn(r, m)),
                         "game_id": r.game_id, "season": int(d0[:4]) if d0[5:7] >= "08" else int(d0[:4]) - 1,
                         **conf, "c_phase": r.n_prior_min, "c_dq": float(bool(r.dq_bad)),
                         "dec_a_med": m["dec_a_med"], "dec_b_med": m["dec_b_med"], "dec_a_best": m["dec_a_best"],
                         "dec_b_best": m["dec_b_best"]})
        out[name] = pd.DataFrame(rows)

    build("moneyline (home wins)", "ml", "p_home_ml", lambda r, m: r.margin > 0, lambda r, m: r.margin == 0, "margin")
    build("spread (home covers)", "spread", "p_home_cover", lambda r, m: r.margin + m["line"] > 0,
          lambda r, m: r.margin + m["line"] == 0, "margin")
    build("total (over)", "total_m", "p_over", lambda r, m: r.total > m["line"], lambda r, m: r.total == m["line"], "total")
    return out


def report(rec: pd.DataFrame) -> str:
    lines = ["## NBA game engine vs BettingPros closing quotes", "",
             f"- games scored: {len(rec)} ({rec['date'].min()} .. {rec['date'].max()}); with a market: ml "
             f"{int(rec['ml'].notna().sum())}, spread {int(rec['spread'].notna().sum())}, total "
             f"{int(rec['total_m'].notna().sum())}", ""]
    for name, ev in to_events(rec).items():
        lines += [ms.render_markdown(ms.score_market(ev, name)) if len(ev) > 30 else f"### {name}: n={len(ev)} (too few)", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None)
    ap.add_argument("--local-dir", default=None, help="local copy of Odds/bettingpros_nba_games (moneyline/ spread/ total/)")
    ap.add_argument("--records-pkl", default=None, help="write the per-game replay records (pickle: has dict columns)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rec = replay(args.local_dir)
    if args.records_pkl:
        rec.to_pickle(args.records_pkl)
    text = report(rec)
    print(text)
    if args.out:
        open(args.out, "w").write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
