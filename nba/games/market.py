"""
nba.games.market -- per-game consensus (moneyline / spread / total) from the BettingPros long-format game-line history.

Input: `nba.games.bp_games.read_games()` rows. Only real two-sided sportsbook quotes are used (`book_kind == "book"`:
no BettingPros "Consensus", no opening-line rows, no DFS, no exchanges). The partition holds each book's LAST quote of the
day, i.e. a closing-line PROXY (there is no timestamped series, so no true CLV from this source).

Per game:
  ml_p_home            median over books of the de-vigged (proportional) home win probability
  sp_line / tot_line   the MODAL quoted line across books (ties -> the one nearest the median); `sp_line` is the HOME handicap
                       (home -2.5 -> -2.5: home covers when margin + sp_line > 0)
  sp_p_home / tot_p_over   median de-vigged probability among the books quoting exactly that line
  *_dec_*              decimal price per side: `_med` median across those books, `_best` the best (line-shopping UPPER bound)
  n_books_*            how many books went into each
Join key to the game table: (ET date of `scheduled`, home tricode, away tricode).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_two_way
from nba.teams import team_code


# BettingPros tricodes that nba.teams.team_code does not know (verified over the whole 2021-26 crawl: only these three).
# Missing them silently dropped every Suns / Pelicans / Jazz game (19% of the slate) from the market join.
BP_CODES = {"PHO": "PHX", "NOR": "NOP", "UTH": "UTA"}


def _code(x):
    return team_code(BP_CODES.get(str(x).upper(), x))


def _devig(oh, oa) -> float:
    ph, pa = american_to_implied_prob(oh), american_to_implied_prob(oa)
    return float(devig_two_way(ph, pa)[0])


def _mode_line(lines: pd.Series) -> float:
    vc = lines.value_counts()
    top = vc[vc == vc.max()].index.values
    return float(top[np.argmin(np.abs(top - lines.median()))])


def _pairs(d: pd.DataFrame, a: str, b: str) -> pd.DataFrame:
    """One row per (book, line) with the two sides' odds side by side."""
    pa = d[d["side"] == a].drop_duplicates(["book_id", "line"], keep="last").set_index(["book_id", "line"])["odds"]
    pb = d[d["side"] == b].drop_duplicates(["book_id", "line"], keep="last").set_index(["book_id", "line"])["odds"]
    j = pd.concat([pa.rename("oa"), pb.rename("ob")], axis=1).dropna().reset_index()
    return j


def _summ(j: pd.DataFrame) -> dict:
    p = [_devig(x, y) for x, y in zip(j["oa"], j["ob"])]
    da = [american_to_decimal(x) for x in j["oa"]]
    db = [american_to_decimal(x) for x in j["ob"]]
    return {"p": float(np.median(p)), "dec_a_med": float(np.median(da)), "dec_b_med": float(np.median(db)),
            "dec_a_best": float(max(da)), "dec_b_best": float(max(db)), "n": len(j)}


PREGAME_GRACE_MIN = 10


def consensus(bp: pd.DataFrame, grace_min: float = PREGAME_GRACE_MIN) -> pd.DataFrame:
    """Per-game consensus. A quote whose last-change stamp (`updated`, UTC) is more than `grace_min` minutes AFTER the
    scheduled start is an in-play line and is dropped; 0-5 minutes after is normal (books re-post at the tip) and kept --
    on the NBA crawl the median stamp is +1 min, the 99th percentile +3.4 min, the max +15 (see the NHL note in
    docs/solutions/integration-issues/bettingpros-nhl-pregame-flag-and-team-codes.md)."""
    b = bp[(bp["book_kind"] == "book") & (~bp["is_open"].astype(bool))].copy()
    if "updated" in b.columns:
        gap = (pd.to_datetime(b["updated"], utc=True, errors="coerce")
               - pd.to_datetime(b["scheduled"], utc=True, errors="coerce")).dt.total_seconds() / 60.0
        b = b[~(gap > grace_min)]
    b["odds"] = b["odds"].astype(float)
    ev = (b.sort_values("date").groupby("event_id")[["scheduled", "away", "home"]].last())
    rows = []
    for eid, d in b.groupby("event_id"):
        out = {"event_id": eid}
        m = d[d["market"] == "moneyline"]
        j = _pairs(m, "home", "away")
        j = j[(j["oa"].abs() >= 100) & (j["ob"].abs() >= 100)]
        if len(j):
            s = _summ(j)
            out.update(ml_p_home=s["p"], ml_dec_home_med=s["dec_a_med"], ml_dec_away_med=s["dec_b_med"],
                       ml_dec_home_best=s["dec_a_best"], ml_dec_away_best=s["dec_b_best"], n_books_ml=s["n"])
        for mk, (sa, sb), pre in (("spread", ("home", "away"), "sp"), ("total", ("over", "under"), "tot")):
            x = d[d["market"] == mk]
            if mk != "spread":
                j = _pairs(x, sa, sb)
            else:  # the away side is quoted at -home_line: re-key it onto the HOME line before pairing
                pa_ = x[x["side"] == "home"].drop_duplicates(["book_id", "line"], keep="last")
                pb_ = x[x["side"] == "away"].copy()
                pb_["line"] = -pb_["line"]
                pb_ = pb_.drop_duplicates(["book_id", "line"], keep="last")
                j = (pa_.set_index(["book_id", "line"])["odds"].rename("oa").to_frame()
                     .join(pb_.set_index(["book_id", "line"])["odds"].rename("ob"), how="inner").reset_index())
            j = j[(j["oa"].abs() >= 100) & (j["ob"].abs() >= 100)]
            if not len(j):
                continue
            line = _mode_line(j["line"])
            s = _summ(j[j["line"] == line])
            out.update({f"{pre}_line": line, f"{pre}_p_{'home' if mk == 'spread' else 'over'}": s["p"],
                        f"{pre}_dec_{'home' if mk == 'spread' else 'over'}_med": s["dec_a_med"],
                        f"{pre}_dec_{'away' if mk == 'spread' else 'under'}_med": s["dec_b_med"],
                        f"{pre}_dec_{'home' if mk == 'spread' else 'over'}_best": s["dec_a_best"],
                        f"{pre}_dec_{'away' if mk == 'spread' else 'under'}_best": s["dec_b_best"],
                        f"n_books_{pre}": s["n"], f"{pre}_line_med_all": float(j["line"].median())})
        rows.append(out)
    m = pd.DataFrame(rows).merge(ev.reset_index(), on="event_id")
    sched = pd.to_datetime(m["scheduled"], utc=True)
    m["date"] = sched.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    m["home"] = m["home"].map(_code)
    m["away"] = m["away"].map(_code)
    return m.dropna(subset=["home", "away"]).drop_duplicates(["date", "home", "away"], keep="last")


def attach(games: pd.DataFrame, cons: pd.DataFrame) -> pd.DataFrame:
    """Left-join the consensus onto the game/feature table (exact date; a +-1 day fallback for UTC/ET window skew)."""
    key = ["date", "home", "away"]
    out = games.merge(cons, on=key, how="left")
    miss = out["event_id"].isna()
    if miss.any():
        shifted = cons.copy()
        for delta in (-1, 1):
            shifted["date"] = (pd.to_datetime(cons["date"]) + pd.Timedelta(days=delta)).dt.strftime("%Y-%m-%d")
            fill = out.loc[miss, key].merge(shifted, on=key, how="left")
            fill.index = out.index[miss]
            ok = fill["event_id"].notna()
            cols = [c for c in cons.columns if c not in key]
            out.loc[fill.index[ok], cols] = fill.loc[ok, cols]
            miss = out["event_id"].isna()
    return out


# ---- ParlayAPI (live / forward-banked) adapter ------------------------------------------------------------------------

PARLAY_EXCHANGES = frozenset({"novig", "prophetx", "kalshi", "polymarket"})
PARLAY_SHARP = frozenset({"pinnacle"})


def from_parlay(df: pd.DataFrame) -> pd.DataFrame:
    """ParlayAPI banked game-line rows (OddsAccum/basketball_nba/{date}/game_lines_{HHMM}.csv: event_id, commence_time,
    home_team, away_team, book, market h2h|spreads|totals, outcome, price, point) -> the long frame `consensus` takes.

    DFS / social (nba.config.DFS_BOOKS, incl. fliff) and exchanges (novig, prophetx: no-vig-ish prices, not two-sided
    sportsbook quotes) are classed away from "book" exactly as the BettingPros side does; Pinnacle is kept as `sharp` (NOT
    in the consensus -- read it separately as a reference)."""
    from nba.config import DFS_BOOKS
    d = df.copy()
    d["market"] = d["market"].map({"h2h": "moneyline", "spreads": "spread", "totals": "total"})
    d = d[d["market"].notna()]
    hn = d["home_team"].map(team_code)
    on = d["outcome"].astype(str)
    team_side = np.where(d["outcome"].map(team_code) == hn, "home", "away")
    d["side"] = np.where(d["market"] == "total", on.str.lower().str[:1].map({"o": "over", "u": "under"}), team_side)
    d["line"] = pd.to_numeric(d["point"], errors="coerce")
    d["odds"] = pd.to_numeric(d["price"], errors="coerce")
    b = d["book"].str.lower()
    d["book_kind"] = np.where(b.isin(DFS_BOOKS), "dfs", np.where(b.isin(PARLAY_EXCHANGES), "exchange",
                                                                np.where(b.isin(PARLAY_SHARP), "sharp", "book")))
    d["book_id"] = b
    d["is_open"] = False
    d["scheduled"] = d["commence_time"]
    d["home"], d["away"] = d["home_team"], d["away_team"]
    d["date"] = d["event_date"]
    d = d.dropna(subset=["side", "odds"])
    return d[["market", "event_id", "book_id", "side", "line", "odds", "book_kind", "is_open", "scheduled", "away",
              "home", "date"]]


def sharp_reference(parlay_long: pd.DataFrame) -> pd.DataFrame:
    """Pinnacle's de-vigged home win prob / spread / total per event (when it quotes), for a side-by-side reference."""
    p = parlay_long[parlay_long["book_kind"] == "sharp"].copy()
    if p.empty:
        return pd.DataFrame(columns=["date", "home", "away", "pin_p_home", "pin_sp_line", "pin_tot_line"])
    p["book_kind"] = "book"
    c = consensus(p)
    return c.rename(columns={"ml_p_home": "pin_p_home", "sp_line": "pin_sp_line", "tot_line": "pin_tot_line"}).reindex(
        columns=["date", "home", "away", "pin_p_home", "pin_sp_line", "pin_tot_line"])
