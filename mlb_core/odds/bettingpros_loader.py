"""
mlb_core.odds.bettingpros_loader -- historical BettingPros prop lines -> ONE de-vigged market-consensus row per
(game, player, stat), ready to join to a model's prediction frame.

Input is the long table written by mlb_core.odds.bettingpros_props (read_props): one row per
(event, player, market, line, side, book). Rules (CONTEXT s15.11 / s21):

  * Reference books only. Dropped: book_kind in ('dfs', 'consensus', 'open', 'sharp') and any `is_open` row, and ANY
    Kalshi quote (book_id 68 / name contains 'kalshi') -- Kalshi is the venue the +EV sweep trades against, never the
    reference. Real sportsbooks AND exchanges (Novig, ProphetX) are kept.
  * Two-sided only. A book contributes at a (event, player, stat, line) only when BOTH its over and its under exist at
    that same line. Each book is de-vigged with mlb_core.odds.utils.devig_two_way (proportional); the consensus is the
    MEDIAN of the books' fair P(over); `disp` is their MAD.
  * One line per (game, player, stat): the `modal_line` = the line quoted two-sided by the MOST books; ties -> the line
    whose median fair P(over) is closest to 0.5, then the LOWER line. `min_books` gates on that line's book count.

SELECTION SAFETY. The history is ONE snapshot per (market, day): BettingPros keeps the last quoted state of a settled
day and the crawler writes only days strictly before today (bettingpros_props.is_closed_day), so each row is the LAST
quote available for that (book, line) -- close-ish, not a time series and not an opening line. `updated` is the quote's
last-change stamp. The output carries `updated_max` (latest `updated` among the quotes behind the row) and `pregame`
(updated_max <= scheduled, <NA> when either stamp is unparseable). `pregame_only=True` additionally drops post-start
quotes BEFORE pairing (assumes `updated` and `scheduled` are both UTC -- unverified on real data, hence default off).
Evaluators drop rows with pregame == False and count them.

ASCII only. Storage goes through mlb_core.storage (via bettingpros_props.read_props) -- never the GCS client.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ev.props import norm_name
from mlb_core.odds import bettingpros_props as bpp
from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_two_way

EXCLUDED_KINDS = ("dfs", "consensus", "open", "sharp")
KALSHI_BOOK_ID = 68
NOVIG_BOOK_ID = 60
OVER_SIDES = {"over", "yes"}
UNDER_SIDES = {"under", "no"}

OUTPUT_COLUMNS = [
    "date", "game_date", "sport", "event_id", "player_id", "player", "player_norm", "team", "away", "home", "scheduled",
    "stat", "line", "p_over_cons", "n_books", "disp", "best_over_odds", "best_under_odds", "novig_over_odds",
    "novig_under_odds", "updated_max", "pregame",
]
REQUIRED_COLUMNS = ["date", "event_id", "player_id", "player", "line", "side", "book_id", "book", "odds"]


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=object) for c in OUTPUT_COLUMNS})


def slug_to_stat(sport: str) -> dict:
    """{market slug: canonical stat label} for the sport (labels are the bettingpros_props.SPORTS keys)."""
    return {slug: stat for stat, slug in bpp.SPORTS[sport.upper()].items()}


def _is_kalshi(df: pd.DataFrame) -> pd.Series:
    bid = pd.to_numeric(df["book_id"], errors="coerce")
    name = df["book"].astype(str).str.lower()
    return (bid == KALSHI_BOOK_ID) | name.str.contains("kalshi", regex=False)


def _is_novig(df: pd.DataFrame) -> pd.Series:
    bid = pd.to_numeric(df["book_id"], errors="coerce")
    return (bid == NOVIG_BOOK_ID) | (df["book"].astype(str).str.strip().str.lower() == "novig")


def _fair_over(over: pd.Series, under: pd.Series) -> np.ndarray:
    """Proportional de-vig via devig_two_way, evaluated once per distinct (over, under) price pair."""
    pairs = pd.DataFrame({"o": over.to_numpy(dtype=float), "u": under.to_numpy(dtype=float)})
    uniq = pairs.drop_duplicates()
    fair = [devig_two_way(american_to_implied_prob(o), american_to_implied_prob(u), "proportional")[0]
            for o, u in zip(uniq["o"], uniq["u"])]
    uniq = uniq.assign(f=np.asarray(fair, dtype=float))
    return pairs.merge(uniq, on=["o", "u"], how="left")["f"].to_numpy()


def _mad(x: pd.Series) -> float:
    a = x.to_numpy(dtype=float)
    return float(np.median(np.abs(a - np.median(a)))) if len(a) else float("nan")


def _utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce", format="mixed")


def filter_reference_quotes(df: pd.DataFrame, pregame_only: bool = False) -> pd.DataFrame:
    """The kept quote rows: real books + exchanges, never Kalshi/DFS/consensus/open/sharp; valid over/under American
    prices at a numeric line. Adds `side_n` ('over'/'under') and `ts_updated` (UTC timestamp)."""
    miss = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if miss:
        raise ValueError(f"long table is missing columns {miss}")
    d = df
    if "book_kind" in d.columns:
        d = d[~d["book_kind"].isin(EXCLUDED_KINDS)]
    if "is_open" in d.columns:
        d = d[~d["is_open"].fillna(False).astype(bool)]
    d = d[~_is_kalshi(d)]
    side = d["side"].astype(str).str.strip().str.lower()
    d = d.assign(side_n=np.where(side.isin(OVER_SIDES), "over", np.where(side.isin(UNDER_SIDES), "under", "")))
    d = d[d["side_n"] != ""]
    odds = pd.to_numeric(d["odds"], errors="coerce")
    line = pd.to_numeric(d["line"], errors="coerce")
    ok = odds.notna() & line.notna() & (odds.abs() >= 100)
    d = d[ok].assign(odds=odds[ok].astype(float), line=line[ok].astype(float))
    d = d.assign(ts_updated=_utc(d["updated"]) if "updated" in d.columns else pd.NaT)
    if pregame_only and "scheduled" in d.columns and len(d):
        start = _utc(d["scheduled"])
        late = d["ts_updated"].notna() & start.notna() & (d["ts_updated"] > start)
        d = d[~late]
    return d


def consensus_from_long(df: pd.DataFrame, min_books: int = 3, stat_map: dict | None = None,
                        sport: str | None = None, pregame_only: bool = False) -> pd.DataFrame:
    """Pure: long BettingPros table -> one consensus row per (date, event, player, stat) at the modal line.

    `stat_map` ({market slug: stat label}) relabels the `market` column; when None the `market` value is used as the stat
    (or, with `sport`, mapped through slug_to_stat). See the module docstring for the rules. An empty / all-filtered
    input returns an empty frame with OUTPUT_COLUMNS."""
    if df is None or len(df) == 0:
        return _empty()
    d = filter_reference_quotes(df, pregame_only=pregame_only)
    if len(d) == 0:
        return _empty()
    if stat_map is None and sport:
        stat_map = slug_to_stat(sport)
    mk = d["market"].astype(str) if "market" in d.columns else pd.Series("", index=d.index)
    d = d.assign(stat=mk.map(stat_map).fillna(mk) if stat_map else mk)
    d = d.assign(player_norm=d["player"].astype(str).map(norm_name))
    d = d[d["player_norm"] != ""]
    pid = pd.to_numeric(d["player_id"], errors="coerce")
    d = d.assign(pkey=np.where(pid.notna(), pid.astype("Int64").astype(str), "n:" + d["player_norm"]))
    d = d.assign(event_key=d["event_id"].astype(str))
    # one quote per (event, player, stat, line, book, side): the latest `updated` wins
    gkey = ["event_key", "pkey", "stat"]
    qkey = gkey + ["line", "book_id", "side_n"]
    d = d.sort_values("ts_updated", kind="mergesort", na_position="first").drop_duplicates(qkey, keep="last")

    # --- novig prices at every line (independent of two-sidedness)
    nov = d[_is_novig(d)]
    nov_w = nov.pivot_table(index=gkey + ["line"], columns="side_n", values="odds", aggfunc="last") if len(nov) else None

    # --- two-sided books
    wide = d.pivot_table(index=gkey + ["line", "book_id"], columns="side_n", values="odds", aggfunc="last")
    for s in ("over", "under"):
        if s not in wide.columns:
            wide[s] = np.nan
    wide = wide.dropna(subset=["over", "under"]).reset_index()
    if len(wide) == 0:
        return _empty()
    wide["fair_over"] = _fair_over(wide["over"], wide["under"])
    wide["dec_over"] = wide["over"].map(american_to_decimal)
    wide["dec_under"] = wide["under"].map(american_to_decimal)

    # --- modal line per (event, player, stat)
    per_line = wide.groupby(gkey + ["line"], sort=False).agg(n_books=("book_id", "nunique"),
                                                              med_p=("fair_over", "median")).reset_index()
    per_line["dist_half"] = (per_line["med_p"] - 0.5).abs()
    per_line = per_line.sort_values(gkey + ["n_books", "dist_half", "line"], ascending=[True] * 3 + [False, True, True],
                                    kind="mergesort")
    modal = per_line.drop_duplicates(gkey, keep="first")
    modal = modal[modal["n_books"] >= int(min_books)]
    if len(modal) == 0:
        return _empty()
    w = wide.merge(modal[gkey + ["line"]], on=gkey + ["line"], how="inner")

    grp = w.groupby(gkey, sort=False)
    out = grp.agg(line=("line", "first"), p_over_cons=("fair_over", "median"), n_books=("book_id", "nunique"),
                  disp=("fair_over", _mad)).reset_index()
    bo = w.loc[grp["dec_over"].idxmax(), gkey + ["over"]].rename(columns={"over": "best_over_odds"})
    bu = w.loc[grp["dec_under"].idxmax(), gkey + ["under"]].rename(columns={"under": "best_under_odds"})
    out = out.merge(bo, on=gkey, how="left").merge(bu, on=gkey, how="left")

    if nov_w is not None and len(nov_w):
        nv = nov_w.reset_index()
        for s in ("over", "under"):
            if s not in nv.columns:
                nv[s] = np.nan
        nv = nv.rename(columns={"over": "novig_over_odds", "under": "novig_under_odds"})
        out = out.merge(nv[gkey + ["line", "novig_over_odds", "novig_under_odds"]], on=gkey + ["line"], how="left")
    else:
        out["novig_over_odds"] = np.nan
        out["novig_under_odds"] = np.nan

    # --- descriptors and provenance (taken from the quotes behind the chosen line)
    at_line = d.merge(out[gkey + ["line"]], on=gkey + ["line"], how="inner")
    desc_cols = [c for c in ("date", "sport", "player_id", "player", "player_norm", "team", "away", "home", "scheduled")
                 if c in at_line.columns]
    desc = at_line.sort_values("ts_updated", kind="mergesort").drop_duplicates(gkey, keep="last")[gkey + desc_cols]
    upd = at_line.groupby(gkey, sort=False)["ts_updated"].max().rename("updated_max").reset_index()
    out = out.merge(desc, on=gkey, how="left").merge(upd, on=gkey, how="left")
    out["event_id"] = pd.to_numeric(out["event_key"], errors="coerce").astype("Int64")
    if "sport" not in out.columns:
        out["sport"] = (sport or "").upper()
    for c in ("team", "away", "home", "scheduled", "player", "date"):
        if c not in out.columns:
            out[c] = ""
    sched = _utc(out["scheduled"])
    et = sched.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    out["game_date"] = et.where(sched.notna(), out["date"].astype(str).str[:10])
    pre = (out["updated_max"] <= sched).astype("boolean")
    out["pregame"] = pre.where(out["updated_max"].notna() & sched.notna(), pd.NA)
    out["updated_max"] = out["updated_max"].dt.strftime("%Y-%m-%d %H:%M:%S")
    for c in ("novig_over_odds", "novig_under_odds", "best_over_odds", "best_under_odds"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.sort_values(["date", "event_key", "player_norm", "stat"], kind="mergesort").reset_index(drop=True)
    return out[OUTPUT_COLUMNS]


def build_market_consensus(sport: str, start: str | None = None, end: str | None = None, markets: list | None = None,
                           prefix: str | None = None, min_books: int = 3, pregame_only: bool = False) -> pd.DataFrame:
    """Read the sport's backfilled partitions (Odds/bettingpros_{sport}/{market}/{date}.parquet) market by market and
    return the concatenated consensus table. `markets` = stat labels (SPORTS keys) and/or slugs; None = every market the
    sport defines. Markets with no partitions in range are skipped silently (an empty frame comes back if none have
    data)."""
    sp = sport.upper()
    if sp not in bpp.SPORTS:
        raise ValueError(f"unknown sport {sport!r}; known: {sorted(bpp.SPORTS)}")
    labels = bpp.SPORTS[sp]
    wanted = None if markets is None else set(markets)
    frames = []
    for stat, slug in labels.items():
        if wanted is not None and stat not in wanted and slug not in wanted:
            continue
        long_df = bpp.read_props(sp, slug, start, end, prefix)
        if len(long_df) == 0:
            continue
        c = consensus_from_long(long_df, min_books=min_books, stat_map={slug: stat}, sport=sp,
                                pregame_only=pregame_only)
        if len(c):
            frames.append(c)
    if not frames:
        return _empty()
    return pd.concat(frames, ignore_index=True).sort_values(["date", "event_id", "player_norm", "stat"],
                                                            kind="mergesort").reset_index(drop=True)


def coverage(cons: pd.DataFrame) -> dict:
    """Small coverage summary: rows, days, books-per-row histogram, rows per stat."""
    if cons is None or len(cons) == 0:
        return {"rows": 0, "days": 0, "books_hist": {}, "by_stat": {}}
    return {"rows": int(len(cons)), "days": int(cons["game_date"].nunique()),
            "books_hist": {int(k): int(v) for k, v in cons["n_books"].value_counts().sort_index().items()},
            "by_stat": {str(k): int(v) for k, v in cons["stat"].value_counts().items()}}
