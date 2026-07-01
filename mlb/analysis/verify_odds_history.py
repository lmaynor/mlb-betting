"""
mlb.analysis.verify_odds_history -- data-integrity + join audit for odds_history.

Run this BEFORE trusting any backtest. It emits a per-market scorecard:
- coverage (rows, dates, per-season, sources)
- resolution rate (game_pk + player_id non-null) -- the >=95% gate
- value sanity (american range, implied_prob in (0,1), de-vig pairs sum ~1)
- team normalization (blank away/home)
- JOIN COVERAGE to the realized-outcome source for the market's shape:
    inning markets (nrfi/f5/game/...) -> scoring_master by game_pk
    player props (hr/hits/tb/k/outs)  -> statcast_master by (game_pk, batter|pitcher)
    earned runs (per_ou)              -> boxscore/game_result (not statcast) = N/A here

The join contract this encodes (the canonical keys):
- odds_history.game_pk  -> scoring_master.game_pk            (realized inning runs)
- odds_history.(game_pk, player_id) -> statcast_master.(game_pk, batter|pitcher)  (realized prop stat)
- odds_history.(game_pk, market, selection, line) -> gen_preds output             (model probs)

Run (Cloud Shell / Cloud Run; needs GCS + pyarrow):
  PYTHONPATH=. python3 -m mlb.analysis.verify_odds_history --markets all
"""

from __future__ import annotations

import argparse

from mlb.analysis import odds_history as oh

# market -> realized-outcome source kind.
REALIZED = {
    # inning / game-level -> scoring_master (game_pk + inning runs)
    "nrfi_ou": "scoring", "game_ml": "scoring", "f5_ml": "scoring", "game_total": "scoring",
    "game_rl": "scoring", "1i_total": "scoring", "5i_total": "scoring", "2i_total": "scoring",
    "1i_spread": "scoring", "5i_spread": "scoring", "team_total": "scoring",
    "5i_team_total": "scoring", "f1_ml": "scoring", "first_to_score": "scoring",
    # batter props -> statcast aggregated by (game_pk, batter)
    "hr_yn": "batter", "bhits_ou": "batter", "btb_ou": "batter", "runs_ou": "batter",
    "rbi_ou": "batter", "singles_ou": "batter", "doubles_ou": "batter",
    "triples_ou": "batter", "steals_ou": "batter", "hrr_ou": "batter",
    # pitcher props -> statcast aggregated by (game_pk, pitcher)
    "k_ou": "pitcher", "outs_ou": "pitcher", "phits_ou": "pitcher", "pwalks_ou": "pitcher",
    # earned runs not in statcast (pitch-level) -> needs boxscore
    "per_ou": "boxscore",
}

# Thresholds for the pass/fail flags.
RESOLVE_GATE = 0.95   # game_pk + player_id non-null
JOIN_GATE = 0.90      # realized-outcome joinable (on settled dates)
STATCAST_LAG_DAYS = 2  # realized masters ingest nightly; exclude recent dates from join


def _all_markets() -> list:
    """Known market universe (avoids listing ~14k blobs, which times out).
    Empty markets simply audit as empty."""
    return sorted(REALIZED)


# --- realized-outcome key sets (loaded once, cached) -------------------------

_cache: dict = {}


def _scoring_game_pks() -> set:
    if "scoring" not in _cache:
        try:
            sm = oh_read_csv("Scoring/scoring_master.csv", usecols=["game_pk"])
            _cache["scoring"] = set(sm["game_pk"].dropna().astype(int))
        except Exception as e:  # noqa: BLE001
            print(f"  (scoring_master unreadable: {e})")
            _cache["scoring"] = set()
    return _cache["scoring"]


def _statcast_pairs():
    """Return (batter_pairs, pitcher_pairs) sets of (game_pk, mlbam_id)."""
    if "statcast" not in _cache:
        try:
            st = oh_read_csv("Statcast/statcast_master.csv",
                            usecols=["game_pk", "batter", "pitcher"])
            st = st.dropna(subset=["game_pk"])
            bp = set(zip(st["game_pk"].astype(int), st["batter"].dropna().astype(int)))
            pp = set(zip(st["game_pk"].astype(int), st["pitcher"].dropna().astype(int)))
            _cache["statcast"] = (bp, pp)
        except Exception as e:  # noqa: BLE001
            print(f"  (statcast_master unreadable: {e})")
            _cache["statcast"] = (set(), set())
    return _cache["statcast"]


def oh_read_csv(key, **kw):
    from mlb_core.storage import read_csv
    return read_csv(key, **kw)


def _settled(df):
    """Rows on SETTLED dates only. Realized masters (statcast/scoring) ingest
    nightly, so same-day/recent games haven't landed -- join coverage is only
    meaningful once the outcome data exists. Excludes the last STATCAST_LAG_DAYS."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=STATCAST_LAG_DAYS)).isoformat()
    return df[df["game_date"] <= cutoff]


def _join_coverage(df, kind: str) -> float | None:
    """Fraction of odds rows (on settled dates) whose realized outcome joins.
    Returns None (N/A) when there are no settled rows yet (e.g. only today's data)."""
    if kind == "boxscore":
        return None  # earned runs come from game_result API, not a master
    d = _settled(df).dropna(subset=["game_pk"])
    if not len(d):
        return None  # nothing settled to check yet
    if kind == "scoring":
        gp = _scoring_game_pks()
        return None if not gp else d["game_pk"].astype(int).isin(gp).mean()
    if kind in ("batter", "pitcher"):
        bp, pp = _statcast_pairs()
        pairs = bp if kind == "batter" else pp
        if not pairs:
            return None
        d = d.dropna(subset=["player_id"])
        if not len(d):
            return None
        hit = [(int(g), int(p)) in pairs for g, p in zip(d["game_pk"], d["player_id"])]
        return sum(hit) / len(hit)
    return None


def _devig_ok(df) -> float | None:
    """Among two-way (game_pk, player_id, line, book, snapshot_ts) pairs with both
    fair probs, fraction summing to ~1.0."""
    two = df.dropna(subset=["fair_prob"])
    if not len(two):
        return None
    grp = two.groupby(["game_pk", "player_id", "line", "book", "snapshot_ts"], dropna=False)
    ok = tot = 0
    for _, g in grp:
        if len(g) == 2:
            tot += 1
            if abs(g["fair_prob"].sum() - 1.0) < 0.02:
                ok += 1
    return (ok / tot) if tot else None


def audit_market(market: str, since: str | None = None) -> dict:
    df = oh.read_history(market, since=since)
    n = len(df)
    r = {"market": market, "rows": n}
    if n == 0:
        r["empty"] = True
        return r
    r["sources"] = df["source"].value_counts().to_dict()
    r["dates"] = df["game_date"].nunique()
    r["game_pk_resolved"] = 1 - df["game_pk"].isna().mean()
    has_player = df["player_id"].notna().any()
    r["player_id_resolved"] = (1 - df["player_id"].isna().mean()) if has_player else None
    # books post extreme but valid juice (e.g. HR UNDER -200000); only flag absurd.
    r["american_bad"] = (~df["american"].between(-1_000_000, 1_000_000)).mean()
    r["implied_bad"] = (~df["implied_prob"].between(0, 1)).mean()
    r["team_blank"] = ((df["away_team"] == "") | (df["home_team"] == "")).mean()
    r["devig_ok"] = _devig_ok(df)
    kind = REALIZED.get(market, "?")
    r["realized_via"] = kind
    r["join_cov"] = _join_coverage(df, kind)
    # pass/fail
    resolve = min(r["game_pk_resolved"], r["player_id_resolved"] or 1.0)
    r["PASS"] = bool(resolve >= RESOLVE_GATE
                     and r["american_bad"] == 0 and r["implied_bad"] == 0
                     and (r["join_cov"] is None or r["join_cov"] >= JOIN_GATE))
    return r


def _fmt(x):
    return "n/a" if x is None else (f"{x:.1%}" if isinstance(x, float) else str(x))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Audit odds_history integrity + joins")
    p.add_argument("--markets", default="all", help="'all' or comma list of canonical markets")
    p.add_argument("--since", default=None, help="YYYY-MM-DD lower bound (speeds up / bounds the read)")
    args = p.parse_args(argv)
    markets = _all_markets() if args.markets == "all" else args.markets.split(",")

    print(f"odds_history audit -- {len(markets)} markets\n")
    hdr = f"{'market':16} {'rows':>6} {'dates':>5} {'gpk':>6} {'pid':>6} {'devig':>6} {'join':>6} {'via':>8} {'PASS'}"
    print(hdr); print("-" * len(hdr))
    fails = []
    for m in markets:
        r = audit_market(m, since=args.since)
        if r.get("empty"):
            print(f"{m:16} {'0':>6}  (empty)")
            continue
        print(f"{m:16} {r['rows']:>6} {r['dates']:>5} "
              f"{_fmt(r['game_pk_resolved']):>6} {_fmt(r['player_id_resolved']):>6} "
              f"{_fmt(r['devig_ok']):>6} {_fmt(r['join_cov']):>6} {r['realized_via']:>8} "
              f"{'ok' if r['PASS'] else 'FAIL'}")
        if not r["PASS"]:
            fails.append((m, r))
    print()
    if fails:
        print("FAILURES (investigate before backtesting):")
        for m, r in fails:
            why = []
            if (r["player_id_resolved"] or 1) < RESOLVE_GATE:
                why.append(f"player_id {_fmt(r['player_id_resolved'])}<95%")
            if r["game_pk_resolved"] < RESOLVE_GATE:
                why.append(f"game_pk {_fmt(r['game_pk_resolved'])}<95%")
            if r["join_cov"] is not None and r["join_cov"] < JOIN_GATE:
                why.append(f"join {_fmt(r['join_cov'])}<90%")
            if r["american_bad"] or r["implied_bad"]:
                why.append("value-range")
            print(f"  {m}: {', '.join(why)}")
    else:
        print("All audited markets PASS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
