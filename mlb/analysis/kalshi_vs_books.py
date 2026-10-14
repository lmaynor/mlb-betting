"""
mlb.analysis.kalshi_vs_books -- soft-book +EV vs the Kalshi sharp reference.

The strategy (2026-07 profit review): there is NO capturable model-vs-line edge;
the edge is intraday SOFT-LINE +EV measured against a sharp reference. Kalshi is
a no-vig exchange, so its (normalized) mid is that reference. This scans
odds_history and, for every book quote that shares a
(market, game_pk, player_id, selection, line) with a Kalshi mid, computes:

    p_true  = Kalshi mid, normalized so the two sides of the market sum to 1
              (removes the half-spread double-count)
    ev_pct  = p_true * book_decimal - 1        # EV of betting THIS side at the book
    edge    = p_true - book_implied_prob       # how much too-generous the book is

Positive ev_pct = the book is offering better than fair per the sharp exchange
= a soft-line +EV bet. We bet the BOOK (which has size); Kalshi is only the
truth estimate, so Kalshi's taker fee does NOT apply (it would only matter if we
also traded the Kalshi side to arb -- reported as kalshi_fee for reference).

Books are flagged soft/sharp via mlb.analysis.book_vig.get_vig (>=8% hold=soft).
Trust the signal most on the liquid Kalshi markets (nrfi_ou/game_ml/total_ou/
runline); prop mids (hr_yn/k_ou/btb_ou/...) are thin -> treat as soft evidence.

THE MIRROR (2026-09-18, --side kalshi): everywhere else in this repo Kalshi is
excluded as a bettable book (backtest_market.OFFSHORE) because it's a sharp
reference, not a sportsbook we can place at. But it IS an exchange with a
public order-placement API -- something no sportsbook here offers -- so the
reverse question is worth asking: is KALSHI's own contract mispriced vs the
real books, i.e. is buying Kalshi's ask +EV against the devigged book-pack
consensus? scan_kalshi_side()/_kalshi_ev() compute exactly that (same shape as
scan() above, roles swapped); validate_kalshi_side() settles flagged historical
quotes vs real outcomes for the markets with a known ground-truth source
(currently game_ml, nrfi_ou -- see _REALIZED_SOURCE). This is a market-
structure question, not a trading action: it does not place any order.

CORRECTION (2026-10-03, game_rl join): Kalshi's run-line contract is "TEAM wins by MORE THAN N"
(selection=TEAM, line=+N) while a sportsbook's AWAY +1.5 is the COMPLEMENT of "HOME wins by more
than 1.5". The raw (selection, line) join matched unlike bets (mirror median EV +112% on real
data). Fixed via _add_join_keys/_JK/_match_books -- see docs/solutions/integration-issues/
kalshi-run-line-semantics-mismatch.md. Historical-window output is still not time-aligned (below).

CORRECTION (2026-10-03, snapshot alignment): scan()/scan_kalshi_side() paired each source's
LATEST snapshot independently, so an in-play Kalshi price could meet a pre-game book price, and the
synthetic 'consensus' row counted as a real book. Now: _drop_inplay (pre-game snapshots only),
_gap_filter (--max-gap-min, default 60) and _real_books. Extreme (>30% EV) rows fell 26.8% -> 0.3%
(game_ml). ALL earlier Kalshi-vs-books figures below are contaminated; the aligned re-run
(--since 2026-08-10, game_ml+nrfi_ou, n=73 flagged, gross +3.5% t=0.26, NET of fee +1.5% t=0.12)
is in docs/solutions/logic-errors/kalshi-scanner-latest-snapshot-misalignment.md.
validate_kalshi_side now also reports ROI NET of Kalshi's taker fee and for the executor's gate.

CORRECTION (2026-10-02 audit): the nrfi_ou figures below used a wrong ground-
truth label (_realized_outcomes took one STARTER's half-inning `yrfi` as the
game-level result; ~18% of flagged nrfi_ou quotes were mislabeled, always in
the NRFI direction). Re-run on the larger 2026-10-02 window, n=646: shipped
label ROI -3.7% (nrfi_ou -7.7%) -> corrected ROI -0.1% (nrfi_ou +16.7%,
t=1.43, n=96; game_ml -3.0%, t=-0.26, n=550). Still NOT significant either
way; the conclusion "leans negative" no longer holds. Fixed in
_realized_outcomes (groupby game_pk max).

First-pass backtest (2026-09-19, game_ml+nrfi_ou, clean window --since
2026-08-10): **ROI -2.6%, t-stat -0.237, n=518 (game_ml -2.4%/-0.187/n=434;
nrfi_ou -3.6%/-0.299/n=84) -- NOT statistically significant, i.e. no proven
edge either way yet.** Two real bugs found+fixed en route to that number,
both worth knowing before trusting any Kalshi-vs-books number in this repo
(either direction):
  1. Kalshi's own implied_prob can be a stale/thin historical print (e.g.
     game_ml HOME=0.02 for a side real books had as a 55-64% favorite;
     nrfi_ou NRFI=0.01) with no liquidity signal available to catch it --
     see docs/solutions/integration-issues/
     kalshi-historical-backfill-stale-closing-candles.md. Now guarded by
     _KALSHI_SANE_RANGE (rejects Kalshi prices outside [0.03, 0.97] on
     LIQUID markets before they ever reach an EV computation) -- unfiltered,
     this manufactured a fake -32.8% ROI on the full history and a fake
     +879% ROI on nrfi_ou alone once bug #2 below was fixed.
  2. nrfi_ou's real-book rows are labeled YES/NO (a plain proposition) while
     Kalshi's are NRFI/YRFI -- these never matched on `selection` at all, so
     nrfi_ou silently produced ZERO joined rows in this scanner (either
     direction) until _NRFI_SELECTION_MAP normalized the book side. See
     docs/solutions/integration-issues/kalshi-nrfi-selection-label-mismatch.md.
Always bound --since to 2026-08-10+ for this market until more live history
accumulates; the pre-08-10 window still manufactures a much-worse-looking
fake "edge" via bug #1 even with the fix in place, since it's a data-quality
issue in that specific historical source, not something the sanity range can
fully launder.

Run (Cloud Shell; needs GCS):
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_vs_books --date 2026-07-23 --min-ev 0.03
  # closing-line divergence only:
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_vs_books --date 2026-07-23 --closing
  # the mirror direction, on the clean live-capture window, with a real-outcome go/no-go:
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_vs_books --side kalshi \
      --markets game_ml,nrfi_ou --since 2026-08-10 --min-ev 0.03 --validate
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from mlb.analysis import book_vig, odds_history as oh
from mlb_core.odds import kalshi


def _player_names(date: str) -> dict:
    """Best-effort {player_id: name} from the latest banked Kalshi raw snapshot
    (Odds/kalshi/raw/<date>/), so prop rows show the pitcher/batter, not an id."""
    from mlb_core import storage
    try:
        keys = sorted(storage.list_keys(f"Odds/kalshi/raw/{date}/"))
        raw = json.loads(storage.read_bytes(keys[-1])) if keys else []
        return {r["player_id"]: r["player"] for r in raw
                if r.get("player_id") is not None and r.get("player")}
    except Exception:  # noqa: BLE001 -- names are a nicety; fall back to ids
        return {}

DEFAULT_MARKETS = ["nrfi_ou", "game_ml", "game_total", "game_rl", "f5_ml",
                   "hr_yn", "k_ou", "outs_ou", "btb_ou", "bhits_ou"]
LIQUID = {"nrfi_ou", "game_ml", "game_total", "game_rl"}   # trustworthy Kalshi mids
# Markets whose two selections are NOT complementary (do not normalize to sum 1).
# Run line: "HOME by >N" and "AWAY by >N" are both false when margin < N+.5.
NO_NORM = {"game_rl"}
# Per-PLAYER prop markets (a row is one pitcher/batter, NOT a game-level line).
# The join keys on player_id; we also drop unresolved players (_pid == -1) here
# so the sentinel can never cross-match one player's Kalshi quote to another's.
PLAYER_MARKETS = {"hr_yn", "k_ou", "outs_ou", "btb_ou", "bhits_ou"}
_JOIN = ["market", "game_pk", "_pid", "_line", "selection"]
_PAIR = ["market", "game_pk", "_pid", "_line"]           # a two-sided quote
# Join identity actually used to match Kalshi to books. Equals _JOIN for every market except the
# run line, where a book's (selection, line) is NOT the proposition Kalshi quotes (see
# _add_join_keys). `_jsel`/`_jline` name the proposition priced; `_no` marks a book quote that is
# the COMPLEMENT of it. Display columns (`selection`, `line`) always stay the quote's own.
_JK = ["market", "game_pk", "_pid", "_jline", "_jsel"]
_OPP = {"HOME": "AWAY", "AWAY": "HOME"}

# nrfi_ou: real-book ingestion (BettingPros/ParlayAPI) frames this as a plain
# YES/NO proposition ("will a run score in the 1st"), no numeric line -- but
# Kalshi's own ingestion, and every other live bet/settlement in this repo
# (CONTEXT.md's bet_type table: "NRFI"/"YRFI"), use the named convention.
# Confirmed 2026-09-19: without this, `selection` never matches between the
# two sources for nrfi_ou, so it silently produced ZERO joined rows in BOTH
# scan() and scan_kalshi_side() the whole time -- not "an efficient market
# with no divergence," a real join failure hiding behind an empty result that
# looked plausible for a market this repo already documents as tight/liquid.
_NRFI_SELECTION_MAP = {"YES": "YRFI", "NO": "NRFI"}

# Kalshi's own implied_prob outside this band on a LIQUID (game-level) market is
# essentially never real -- confirmed 2026-09-19 via two independent examples
# (game_ml HOME=0.02 for a side real books had as a 55-64% favorite; nrfi_ou
# NRFI=0.01, i.e. "1% chance of a scoreless 1st"), both traced to stale/thin
# prints in the historical closing-candle backfill (no bid/ask SIZE data
# survives into odds_history to tell a real liquid print from an old resting
# order). Left unfiltered, these manufacture 25-900%+ fake EV. Same
# precautionary pattern as hr_softline.py's MAX_AMERICAN. Props are NOT
# filtered here -- already documented repo-wide as thin/soft evidence, where a
# genuinely extreme long-shot price is far more plausible than on a game line.
_KALSHI_SANE_RANGE = (0.03, 0.97)


def _ensure_jk(df: pd.DataFrame) -> pd.DataFrame:
    """Frames built by hand (tests, older callers) may lack the join-key columns: default them to
    the quote's own (selection, line) with no complement flag -- identical to the pre-fix join."""
    if len(df) == 0 and "_jsel" not in df.columns:
        return df.assign(_jsel=pd.Series(dtype="object"), _jline=pd.Series(dtype="float64"),
                         _no=pd.Series(dtype="bool"))
    out = df
    if "_jsel" not in out.columns:
        out = out.assign(_jsel=out["selection"])
    if "_jline" not in out.columns:
        out = out.assign(_jline=out["_line"])
    if "_no" not in out.columns:
        out = out.assign(_no=False)
    return out


def _add_join_keys(df: pd.DataFrame) -> pd.DataFrame:
    """Attach (_jsel, _jline, _no): which proposition each quote prices.

    RUN LINE (game_rl) -- the two sources use different conventions, found 2026-10-03:
      * Kalshi (KXMLBSPREAD) quotes "TEAM wins by MORE THAN N": selection=TEAM, line=+N (N=1.5,
        2.5, ...). That is exactly a sportsbook's "TEAM -N" cover.
      * A sportsbook quotes the standard line per side: HOME -1.5 and AWAY +1.5 (and alts).
        "AWAY +1.5" is NOT Kalshi's "AWAY wins by more than 1.5" -- it is the COMPLEMENT of
        "HOME wins by more than 1.5".
    Joining on raw (selection, line) matched Kalshi's AWAY/+1.5 (~0.24) to the book's AWAY/+1.5
    (~0.53) -- different bets -- while Kalshi's HOME/+1.5 never met the book's equivalent HOME/-1.5.
    Canonical proposition = "T wins by more than N":
        book line < 0  (S lays N)   -> (S, N), the same bet
        book line > 0  (S gets N)   -> (opp(S), N), as the COMPLEMENT (_no=True)
    Whole-number lines (push possible) are a different proposition entirely and cannot be matched,
    so those book rows are dropped. Every other market maps to itself.
    """
    df = df.reset_index(drop=True)
    df["_jsel"] = df["selection"]
    df["_jline"] = df["_line"]
    df["_no"] = False
    rl_book = (df["market"] == "game_rl") & (df["book"] != "kalshi")
    if rl_book.any():
        line = pd.to_numeric(df["line"], errors="coerce")
        sel = df["selection"].astype(str).str.upper()
        usable = rl_book & line.notna() & ((line.abs() % 1.0).round(6) == 0.5) & sel.isin(list(_OPP))
        neg, pos = usable & (line < 0), usable & (line > 0)
        df.loc[neg, "_jline"] = line[neg].abs()
        df.loc[neg, "_jsel"] = sel[neg]
        df.loc[pos, "_jline"] = line[pos]
        df.loc[pos, "_jsel"] = sel[pos].map(_OPP)
        df.loc[pos, "_no"] = True
        df = df[~rl_book | usable].reset_index(drop=True)
    return df


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    """Latest snapshot per (quote, book); NaN-safe join keys."""
    df = df[df["implied_prob"].notna()].copy()
    df["_pid"] = df["player_id"].fillna(-1)
    df["_line"] = df["line"].fillna(-99.0)
    is_book_nrfi = (df["market"] == "nrfi_ou") & (df["book"] != "kalshi")
    orig = df.loc[is_book_nrfi, "selection"]
    df.loc[is_book_nrfi, "selection"] = (
        orig.str.upper().map(_NRFI_SELECTION_MAP).fillna(orig))
    lo, hi = _KALSHI_SANE_RANGE
    is_kalshi_liquid = (df["book"] == "kalshi") & (df["market"].isin(LIQUID))
    df = df[~is_kalshi_liquid | df["implied_prob"].between(lo, hi)]
    df = df.sort_values("snapshot_ts")
    keys = ["market", "game_pk", "_pid", "_line", "selection", "book"]
    return _add_join_keys(df.drop_duplicates(subset=keys, keep="last"))


def _kalshi_truth(k: pd.DataFrame, normalize: bool = True) -> pd.DataFrame:
    """p_true from Kalshi mids. For complementary 2-way markets, normalize each
    quote's sides to sum to 1 (removes the half-spread double-count). For non-
    complementary markets (normalize=False, e.g. run line) the raw mid already
    IS the probability of that side, so use it directly."""
    k = _ensure_jk(k).copy()
    if normalize:
        pair_sum = k.groupby(_PAIR)["fair_prob"].transform("sum")
        n_sides = k.groupby(_PAIR)["selection"].transform("size")
        k["p_true"] = k["fair_prob"].where(n_sides < 2, k["fair_prob"] / pair_sum)
    else:
        k["p_true"] = k["fair_prob"]
    return k[_JK + ["p_true", "fair_prob", "snapshot_ts"]].rename(
        columns={"fair_prob": "k_mid", "snapshot_ts": "k_ts"})


def _match_books(k: pd.DataFrame, books: pd.DataFrame, mkt: str) -> pd.DataFrame:
    """Pure core of scan(): join each book quote to the Kalshi quote for the SAME proposition and
    compute the EV of betting that book quote against Kalshi's no-vig mid.

    A book quote flagged `_no` (run line: the complement of Kalshi's proposition) is a bet on
    NOT-X, so its true probability is 1 - Kalshi's mid, not the mid itself.
    """
    k, books = _ensure_jk(k), _ensure_jk(books)
    truth = _kalshi_truth(k, normalize=(mkt not in NO_NORM))
    # per-selection book consensus: how many books, and their median implied.
    # Lets us tell "one book is off (real edge)" from "Kalshi is the lone
    # outlier vs the whole book pack (suspect mid)". Grouped by _no so a quote and its
    # complement never share a median.
    cons = (books.groupby(_JK + ["_no"])["implied_prob"]
            .agg(cons_impl="median", n_books="size").reset_index())
    m = (books.merge(truth, on=_JK, how="inner", suffixes=("", "_k"))
              .merge(cons, on=_JK + ["_no"], how="left"))
    if not len(m):
        return m
    m = m.copy()
    flip = m["_no"].astype(bool)
    m.loc[flip, "p_true"] = 1.0 - m.loc[flip, "p_true"]
    m.loc[flip, "k_mid"] = 1.0 - m.loc[flip, "k_mid"]
    m["ev_pct"] = (m["p_true"] * m["decimal"] - 1.0).round(4)
    m["edge"] = (m["p_true"] - m["implied_prob"]).round(4)
    m["cons_impl"] = m["cons_impl"].round(4)
    m["k_dev"] = (m["p_true"] - m["cons_impl"]).round(4)   # +/- = Kalshi vs book pack
    m["bk_dev"] = (m["implied_prob"] - m["cons_impl"]).round(4)  # book vs pack (neg=cheap)
    m["kalshi_fee"] = (0.07 * m["p_true"] * (1 - m["p_true"])).round(4)
    return m


# Synthetic rows that are NOT real, bettable books: 'consensus' is an aggregate the ingestion adds,
# 'open' is the opening-line tag. Counting them as books double-counts the pack in n_books/median
# and, in scan(), offered a synthetic aggregate as a bettable quote.
_NON_BOOKS = {"consensus", "open"}
DEFAULT_MAX_GAP_MIN = 60.0


def _real_books(d: pd.DataFrame) -> pd.DataFrame:
    return d[(d["book"] != "kalshi") & ~d["book"].str.lower().isin(_NON_BOOKS)]


def _drop_inplay(odds: pd.DataFrame) -> pd.DataFrame:
    """Drop every snapshot taken at/after first pitch BEFORE 'latest per quote' is chosen.

    Both sources keep capturing after the game starts. Taking each source's *latest* snapshot
    independently could pair a Kalshi IN-PLAY price (e.g. 0.96 once a team leads big) with the
    books' last PRE-GAME price -- manufacturing huge fake edges in every market. First pitch comes
    from the Kalshi event_id (event ticker, Eastern HHMM); games with no Kalshi row have nothing to
    join anyway and are left alone.
    """
    if "event_id" not in odds.columns or not len(odds):
        return odds
    k = odds[(odds["book"] == "kalshi") & odds["event_id"].notna() & odds["game_pk"].notna()]
    if not len(k):
        return odds
    starts = {}
    for gpk, ev in k.groupby("game_pk")["event_id"].first().items():
        st = kalshi.event_start_utc(str(ev))
        if st is not None:
            starts[int(gpk)] = pd.Timestamp(st).tz_convert("UTC").tz_localize(None)
    if not starts:
        return odds
    start = odds["game_pk"].map(lambda g: starts.get(int(g)) if pd.notna(g) else None)
    ts = pd.to_datetime(odds["snapshot_ts"], errors="coerce")
    keep = start.isna() | (ts < pd.to_datetime(start))
    return odds[keep]


def _gap_filter(m: pd.DataFrame, a_col: str, b_col: str, max_gap_min) -> pd.DataFrame:
    """Keep matched rows whose two source snapshots are within `max_gap_min` of each other
    (None disables). A match across hours compares prices from different market states."""
    if max_gap_min is None or not len(m) or a_col not in m.columns or b_col not in m.columns:
        return m
    gap = (pd.to_datetime(m[a_col], errors="coerce") - pd.to_datetime(m[b_col], errors="coerce")
           ).abs().dt.total_seconds() / 60.0
    return m[gap <= max_gap_min]


def scan(markets, date=None, since=None, until=None, closing=False,
         min_ev=0.0, max_gap_min=DEFAULT_MAX_GAP_MIN) -> pd.DataFrame:
    frames, cov = [], []
    for mkt in markets:
        odds = oh.read_history(mkt, since=since or date, until=until or date)
        if odds is None or not len(odds):
            continue
        odds = oh.dedupe_by_source(odds)
        if closing and "is_closing" in odds.columns:
            odds = odds[odds["is_closing"] == True]  # noqa: E712
        elif not closing:
            odds = _drop_inplay(odds)
        if not len(odds):
            continue
        d = _prep(odds)
        if mkt in PLAYER_MARKETS:            # keep only resolved players
            d = d[d["_pid"] != -1]
        k = d[d["book"] == "kalshi"]
        books = _real_books(d)
        cov.append({"market": mkt, "kalshi_sel": len(k), "book_rows": len(books),
                    "books": ",".join(sorted(books["book"].unique()))})
        if not len(k) or not len(books):
            continue
        m = _match_books(k, books, mkt)
        m = _gap_filter(m, "k_ts", "snapshot_ts", None if closing else max_gap_min)
        if not len(m):
            continue
        m["vig"] = [book_vig.get_vig(mkt, b) for b in m["book"]]
        m["soft"] = m["vig"] >= 0.08
        m["game"] = m["away_team"].astype(str) + "@" + m["home_team"].astype(str)
        frames.append(m)

    print("coverage (rows in odds_history for this window):")
    for c in cov:
        print(f"  {c['market']:10} kalshi_sel={c['kalshi_sel']:4} "
              f"book_rows={c['book_rows']:4}  books=[{c['books']}]")
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out[out["ev_pct"] >= min_ev]
    return out.sort_values("ev_pct", ascending=False).reset_index(drop=True)


def _kalshi_book_fair(books: pd.DataFrame, min_books: int = 4) -> pd.DataFrame:
    """Devigged real-book consensus per (market, game, player, line, selection):
    median of each book's own already-2-way-devigged fair_prob -- the SAME truth
    estimate outlier_scan.py's consensus_fair uses. NOT scan()'s cons_impl (a raw
    vig-loaded median, fine as a rough corroboration check but not a real
    fair-value anchor -- using it as truth would make every book's own vig look
    like free Kalshi edge). Drops groups under min_books (thin/untrustworthy)."""
    # n_books must count books that actually contributed a devigged fair_prob
    # ("count" skips NaN; "size" counted one-sided/un-devigged rows too, so a
    # 1-real + 3-NaN group passed a min_books=4 gate).
    books = _ensure_jk(books)
    # A complement quote (run line AWAY +1.5) devigs to P(AWAY covers +1.5) = 1 - P(HOME wins by
    # >1.5); flip it so every book votes on the SAME proposition Kalshi's contract settles on.
    books = books.assign(_fair=books["fair_prob"].where(~books["_no"].astype(bool), 1.0 - books["fair_prob"]))
    cons = (books.groupby(_JK)
            .agg(book_fair=("_fair", "median"), n_books=("_fair", "count"),
                 cons_ts=("snapshot_ts", "max")).reset_index())
    return cons[cons["n_books"] >= min_books]


def _kalshi_ev(k: pd.DataFrame, cons: pd.DataFrame, fee_mult: float = 1.0) -> pd.DataFrame:
    """Mirror of scan()'s EV math, opposite direction: is buying KALSHI's own
    contract (at its own ask -- executable via its order book/API, something no
    sportsbook offers us) +EV vs the real book pack's devigged consensus (`cons`,
    from _kalshi_book_fair)? `k` = Kalshi's own quote rows (implied_prob=its ask,
    decimal=1/ask, both set at ingestion).

        ev_pct = book_fair * kalshi_decimal - 1     # EV of buying Kalshi's own ask
        edge   = book_fair - kalshi_implied_prob    # +ve = Kalshi looks cheap

    `ev_pct` / `edge` are GROSS of Kalshi's taker fee (same convention as scan()'s
    kalshi_fee column) -- unchanged, so existing thresholds and backtests keep
    their meaning. Two ADDITIVE columns carry the fee-aware view:

        kalshi_fee = fee_mult * 0.07 * ask * (1 - ask)      # $ per contract
        net_ev_pct = book_fair / (ask + kalshi_fee) - 1     # EV per $ actually paid

    `fee_mult` is the series' taker multiplier (per SERIES, not 1.0: KXMLBGAME is
    0.5 -- see _series_fee_multipliers). The 1.0 default is the conservative
    general schedule; pass the real one before sizing anything real."""
    m = _ensure_jk(k).merge(_ensure_jk(cons), on=_JK, how="inner")
    if not len(m):
        return m
    m = m.copy()
    m["ev_pct"] = (m["book_fair"] * m["decimal"] - 1.0).round(4)
    m["edge"] = (m["book_fair"] - m["implied_prob"]).round(4)
    m["kalshi_fee"] = kalshi_taker_fee_per_contract(m["implied_prob"], fee_mult).round(4)
    m["net_ev_pct"] = (m["book_fair"] / (m["implied_prob"] + m["kalshi_fee"]) - 1.0).round(4)
    m["book_fair"] = m["book_fair"].round(4)
    return m


def scan_kalshi_side(markets, date=None, since=None, until=None, closing=False,
                      min_ev=0.0, min_books=4, max_gap_min=DEFAULT_MAX_GAP_MIN,
                      fee_mult: dict | None = None, min_net_ev: float | None = None) -> pd.DataFrame:
    """Mirror of scan(): scan() treats a sportsbook as bettable and Kalshi's mid
    as the truth anchor; this treats KALSHI as bettable and the real books'
    devigged consensus as the truth anchor -- see _kalshi_book_fair/_kalshi_ev
    for the actual math. Trust the liquid markets (LIQUID) most; prop mids are thin.

    `fee_mult` ({market: taker multiplier}, e.g. from _series_fee_multipliers) feeds the
    additive kalshi_fee / net_ev_pct columns (1.0 for a market not in it); `min_ev` still
    filters the GROSS ev_pct, `min_net_ev` (optional) additionally filters net_ev_pct.
    """
    frames, cov = [], []
    for mkt in markets:
        odds = oh.read_history(mkt, since=since or date, until=until or date)
        if odds is None or not len(odds):
            continue
        odds = oh.dedupe_by_source(odds)
        if closing and "is_closing" in odds.columns:
            odds = odds[odds["is_closing"] == True]  # noqa: E712
        elif not closing:
            odds = _drop_inplay(odds)
        if not len(odds):
            continue
        d = _prep(odds)
        if mkt in PLAYER_MARKETS:
            d = d[d["_pid"] != -1]
        k = d[d["book"] == "kalshi"]
        books = _real_books(d)
        cov.append({"market": mkt, "kalshi_sel": len(k), "book_rows": len(books)})
        if not len(k) or not len(books):
            continue
        m = _kalshi_ev(k, _kalshi_book_fair(books, min_books), (fee_mult or {}).get(mkt, 1.0))
        m = _gap_filter(m, "snapshot_ts", "cons_ts", None if closing else max_gap_min)
        if not len(m):
            continue
        m["game"] = m["away_team"].astype(str) + "@" + m["home_team"].astype(str)
        frames.append(m)

    print("coverage (rows in odds_history for this window):")
    for c in cov:
        print(f"  {c['market']:10} kalshi_sel={c['kalshi_sel']:4} book_rows={c['book_rows']:4}")
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out[out["ev_pct"] >= min_ev]
    if min_net_ev is not None:
        out = out[out["net_ev_pct"] >= min_net_ev]
    return out.sort_values("ev_pct", ascending=False).reset_index(drop=True)


# Realized-outcome ground truth, reused VERBATIM from each system's own training
# table -- never re-derive a label this repo already computes correctly elsewhere.
# Only markets with an existing feature table are covered; game_total/game_rl/
# f5_ml/props have no registered system behind them yet (no ground-truth source),
# so validate_kalshi_side() reports them as unmatched rather than guessing.
_REALIZED_SOURCE = {
    "game_ml": ("GAME_Pro_System/data/model_features.csv", "home_win"),
    "nrfi_ou": ("NRFI_Pro_System/data/model_features.csv", "yrfi"),
}


def _realized_outcomes(market: str) -> dict:
    """{game_pk: realized_label} from the market's own feature table, or {} if
    this market has no known ground-truth source yet."""
    src = _REALIZED_SOURCE.get(market)
    if not src:
        return {}
    import io
    from mlb_core import storage
    feature_csv, label_col = src
    df = pd.read_csv(io.BytesIO(storage.read_bytes(feature_csv)),
                     usecols=["game_pk", label_col]).dropna()
    # NRFI's model_features.csv is one row PER STARTER (two per game) and its
    # `yrfi` is that pitcher's own 1st-inning runs allowed, not the game-level
    # outcome. A plain dict(zip(...)) keeps whichever row is last -> ~20% of
    # games mislabeled NRFI when only the other half scored. Game-level YRFI =
    # either half scored = max. (Harmless for game_ml: home_win is constant
    # within a game.)
    df = df.groupby("game_pk", as_index=False)[label_col].max()
    return {int(g): float(v) for g, v in zip(df["game_pk"], df[label_col])}


def _kalshi_won(market: str, selection: str, realized: float) -> float | None:
    """1.0 win / 0.0 loss for a Kalshi-side bet. Mirrors backtest_market._won's
    binary-market logic for the two markets with a real ground-truth source."""
    if realized is None or pd.isna(realized):
        return None
    sel = (selection or "").upper()
    if market == "game_ml":
        return 1.0 if (sel == "HOME") == (realized >= 1) else 0.0
    if market == "nrfi_ou":
        return 1.0 if (sel == "YRFI") == (realized >= 1) else 0.0
    return None


def _tstat(vals) -> float | None:
    """t-stat of the mean. Guards against float64 rounding noise on
    near-identical values (e.g. three 0.1s) producing a near-zero-but-not-exactly-
    zero sem, which would otherwise blow up into a meaningless ~1e16 t-stat
    instead of the "no real variance, no signal" None a truly flat sample should
    report -- caught via test_none_when_zero_variance, not a hypothetical."""
    from scipy import stats as scipy_stats
    vals = vals.dropna()
    if len(vals) < 2:
        return None
    sem = float(scipy_stats.sem(vals))
    return round(float(vals.mean()) / sem, 3) if sem > 1e-9 else None


# Kalshi series per canonical market, for the fee multiplier (read live: it is per-SERIES and not 1.0 --
# KXMLBGAME is 0.5; see docs/designs/kalshi_auto_execution.md).
_FEE_SERIES = {"game_ml": "KXMLBGAME", "nrfi_ou": "KXMLBRFI", "game_total": "KXMLBTOTAL",
               "game_rl": "KXMLBSPREAD"}


def _series_fee_multipliers(markets) -> dict:
    """{market: taker fee multiplier} from the live series objects; 1.0 (conservative) if unavailable."""
    out = {}
    for m in markets:
        mult = 1.0
        series = _FEE_SERIES.get(m)
        data = kalshi.get(f"/series/{series}") if series else None
        try:
            v = float(((data or {}).get("series") or {}).get("fee_multiplier"))
            if v > 0:
                mult = v
        except (TypeError, ValueError):
            pass
        out[m] = mult
    return out


def kalshi_taker_fee_per_contract(ask, mult: float = 1.0):
    """Kalshi taker fee per contract: M * 0.07 * P * (1-P) (centicent rounding ignored at the
    per-contract level; fine for an ROI estimate)."""
    return mult * 0.07 * ask * (1.0 - ask)


def validate_kalshi_side(df: pd.DataFrame, fee_mult: dict | None = None,
                         ev_gate: tuple = (0.03, 0.10), haircut: float = 0.005) -> None:
    """Settle flagged Kalshi-side +EV quotes (from scan_kalshi_side) vs REAL
    outcomes -- the go/no-go. A Kalshi-side edge is only real if buying these
    flagged contracts actually profits, same bar hr_softline.validate() uses
    (ROI>0 AND t-stat significant). Only settles markets with a known
    ground-truth source (_REALIZED_SOURCE); others are reported unmatched
    rather than guessed at."""
    if df is None or not len(df):
        print("\nvalidate_kalshi_side: no flagged quotes to settle.")
        return
    q = df.copy()
    outcomes = {m: _realized_outcomes(m) for m in q["market"].unique()}
    q["realized"] = [outcomes.get(m, {}).get(int(g)) if pd.notna(g) else None
                     for m, g in zip(q["market"], q["game_pk"])]
    unmatched_markets = sorted(set(q.loc[q["realized"].isna(), "market"]) - set(_REALIZED_SOURCE))
    matched = q[q["realized"].notna()].copy()
    if not len(matched):
        print(f"\nvalidate_kalshi_side: 0/{len(q)} flagged quotes matched a ground-truth "
              f"outcome (no source yet for: {unmatched_markets or 'n/a'}).")
        return
    matched["won"] = [_kalshi_won(m, s, r) for m, s, r in
                      zip(matched["market"], matched["selection"], matched["realized"])]
    matched = matched[matched["won"].notna()]
    matched["roi"] = matched["won"].mul(matched["decimal"] - 1.0).where(matched["won"] == 1, -1.0)

    # NET of Kalshi's taker fee: cost per contract c = ask + fee; a win pays $1.
    fee_mult = fee_mult if fee_mult is not None else _series_fee_multipliers(matched["market"].unique())
    ask = matched["implied_prob"].astype(float)
    mult = matched["market"].map(lambda m: fee_mult.get(m, 1.0))
    cost = ask + kalshi_taker_fee_per_contract(ask, mult)
    matched["cost"] = cost
    matched["roi_net"] = (1.0 - cost) / cost
    matched.loc[matched["won"] != 1, "roi_net"] = -1.0
    # the executor's own gate: shrunk fair prob (book_fair - haircut) vs all-in cost, EV in [lo, hi]
    matched["ev_net"] = (matched["book_fair"] - haircut) / cost - 1.0
    gated = matched[(matched["ev_net"] >= ev_gate[0]) & (matched["ev_net"] <= ev_gate[1])]

    t = _tstat(matched["roi"])
    print("\n=== REALIZED validation: flagged Kalshi-side +EV quotes settled vs actual outcomes ===")
    print(f"  n={len(matched)} (skipped {len(q) - len(matched)}: no ground truth yet for "
          f"{unmatched_markets or 'none'})")
    print(f"  hit%={matched['won'].mean()*100:.1f}  ROI={matched['roi'].mean()*100:+.1f}%  "
          f"units={matched['roi'].sum():+.1f}  t-stat={t}"
          + ("  (|t|<2 -> NOT yet significant, small-n variance)"
             if t is not None and abs(t) < 2 else ""))
    by = matched.groupby("market").agg(n=("won", "size"), hit=("won", "mean"),
                                       roi=("roi", "mean"), tstat=("roi", _tstat))
    by["hit"] = (by["hit"] * 100).round(1)
    by["roi"] = (by["roi"] * 100).round(1)
    print(by.sort_values("roi", ascending=False).to_string())
    def _line(tag, d, col):
        if not len(d):
            print(f"  {tag}: n=0"); return
        tt = _tstat(d[col])
        print(f"  {tag}: n={len(d)} hit%={d['won'].mean()*100:.1f} ROI={d[col].mean()*100:+.1f}% "
              f"t-stat={tt}")
    print(f"\n  --- NET of Kalshi taker fee (multipliers {fee_mult}) ---")
    _line("all flagged, net ", matched, "roi_net")
    _line(f"executor gate (net EV {ev_gate[0]:.0%}-{ev_gate[1]:.0%} after {haircut:.1%} haircut), net", gated, "roi_net")
    if len(gated):
        byg = gated.groupby("market").agg(n=("won", "size"), hit=("won", "mean"), roi_net=("roi_net", "mean"),
                                          tstat=("roi_net", _tstat))
        byg["hit"] = (byg["hit"] * 100).round(1)
        byg["roi_net"] = (byg["roi_net"] * 100).round(1)
        print(byg.to_string())
    print("  REAL edge => ROI > 0 AND t-stat significant (|t|>=2) on decent n. ROI<=0 or\n"
          "  |t|<2 => not yet distinguishable from noise. This assumes execution at the\n"
          "  historical ask with no slippage/size limit -- Kalshi's own book depth isn't\n"
          "  in odds_history, so treat this as an optimistic upper bound on the real edge.")


def classify(df, kdev_max=0.04, stale_gap=0.15, min_books=4):
    """Add a `verdict` column separating credible edges from artifacts:
    thin_pack (too few books), kalshi_off (|k_dev|>kdev_max => Kalshi disagrees
    with the whole pack), stale? (book >stale_gap below consensus => stale/
    placeholder quote), check (survives all = modest, corroborated edge)."""
    if not len(df):
        return df
    df = df.copy()
    df["verdict"] = np.select(
        [df["n_books"] < min_books,
         df["k_dev"].abs() > kdev_max,
         df["bk_dev"] < -stale_gap],
        ["thin_pack", "kalshi_off", "stale?"],
        default="check")
    return df


def main(argv=None) -> int:
    from datetime import datetime, timezone
    p = argparse.ArgumentParser(description="Soft-book +EV vs Kalshi sharp reference")
    p.add_argument("--side", choices=["books", "kalshi"], default="books",
                   help="'books' (default): is a sportsbook +EV vs Kalshi's mid. "
                        "'kalshi': the mirror -- is KALSHI's own contract +EV vs "
                        "the real book pack (the side we could execute via its API).")
    p.add_argument("--markets", default=",".join(DEFAULT_MARKETS))
    p.add_argument("--date", default=None, help="single game_date YYYY-MM-DD (default: today UTC)")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--closing", action="store_true", help="compare closing snapshots only")
    p.add_argument("--min-ev", type=float, default=0.03, help="min ev_pct to show (default 0.03)")
    p.add_argument("--soft-only", action="store_true", help="only soft books (vig>=8%%)")
    p.add_argument("--liquid-only", action="store_true",
                   help="only markets with trustworthy Kalshi mids (nrfi/game/total/runline)")
    p.add_argument("--kdev-max", type=float, default=0.04,
                   help="max |p_true - book consensus| for a trusted row (default 0.04)")
    p.add_argument("--stale-gap", type=float, default=0.15,
                   help="flag a book quote this far below consensus as stale (default 0.15)")
    p.add_argument("--min-books", type=int, default=4,
                   help="min books for a trustworthy consensus (default 4)")
    p.add_argument("--all", action="store_true",
                   help="show every verdict (default: only credible 'check' rows)")
    p.add_argument("--validate", action="store_true",
                   help="--side kalshi only: settle flagged quotes vs real outcomes (go/no-go)")
    p.add_argument("--max-gap-min", type=float, default=DEFAULT_MAX_GAP_MIN,
                   help="drop matches whose Kalshi and book snapshots are further apart than this "
                        "(minutes; default 60). Ignored with --closing.")
    p.add_argument("--top", type=int, default=40)
    args = p.parse_args(argv)

    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    if args.liquid_only:
        markets = [m for m in markets if m in LIQUID]
    date = args.date or (None if (args.since or args.until)
                         else datetime.now(timezone.utc).date().isoformat())

    if args.side == "kalshi":
        df = scan_kalshi_side(markets, date=date, since=args.since, until=args.until,
                              closing=args.closing, min_ev=args.min_ev,
                              min_books=args.min_books, max_gap_min=args.max_gap_min)
        if not len(df):
            print("\nno +EV Kalshi-side divergences (or no overlapping kalshi+book quotes in window).")
            return 0
        names = _player_names(date) if date else {}
        df["player"] = df["player_id"].map(names).fillna(
            df["player_id"].map(lambda p: "" if pd.isna(p) else str(int(p))))
        cols = ["market", "game", "player", "selection", "line", "american",
                "implied_prob", "book_fair", "n_books", "edge", "ev_pct", "snapshot_ts"]
        cols = [c for c in cols if c in df.columns]
        print(f"\n{len(df)} +EV Kalshi-side quotes (buying Kalshi's own ask vs the "
              f"devigged book-pack consensus, min_ev={args.min_ev}, min_books={args.min_books}):")
        print("  ev_pct/edge are gross of Kalshi's own taker fee -- back it out before sizing.\n")
        with pd.option_context("display.max_rows", args.top, "display.width", 220):
            print(df[cols].head(args.top).to_string(index=False))
        if args.validate:
            validate_kalshi_side(df)
        return 0

    df = scan(markets, date=date, since=args.since, until=args.until,
              closing=args.closing, min_ev=args.min_ev, max_gap_min=args.max_gap_min)
    if not len(df):
        print("\nno +EV divergences (or no overlapping kalshi+book quotes in window).")
        return 0
    if args.soft_only:
        df = df[df["soft"]]
    df = classify(df, kdev_max=args.kdev_max, stale_gap=args.stale_gap,
                  min_books=args.min_books)
    names = _player_names(date) if date else {}
    df["player"] = df["player_id"].map(names).fillna(
        df["player_id"].map(lambda p: "" if pd.isna(p) else str(int(p))))
    shown = df if args.all else df[df["verdict"] == "check"]
    cols = ["market", "game", "player", "selection", "line", "book", "american",
            "implied_prob", "cons_impl", "n_books", "p_true", "k_dev", "bk_dev",
            "ev_pct", "verdict", "soft"]
    cols = [c for c in cols if c in shown.columns]
    counts = df["verdict"].value_counts().to_dict()
    print(f"\nverdict mix (min_ev={args.min_ev}): {counts}")
    print(f"showing {len(shown)} {'ALL rows' if args.all else 'credible check rows'} "
          f"({'CLOSING' if args.closing else 'latest'} snapshots):")
    print("  check = |k_dev|<=kdev_max (Kalshi corroborates the pack) AND book not")
    print("  stale; kalshi_off = Kalshi is the lone outlier; stale? = junk book quote.\n")
    with pd.option_context("display.max_rows", args.top, "display.width", 220):
        print(shown[cols].head(args.top).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
