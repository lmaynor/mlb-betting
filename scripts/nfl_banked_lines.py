"""nfl_banked_lines.py -- build the NFL prop MARKET-LINES table from the shadow sweep's banked ParlayAPI payloads.

Reads `EVSweep/nfl_props/raw/**` (READ ONLY; never writes to GCS), parses with `ev.props`, keeps PRE-KICK snapshots
only, and writes LOCAL files + prints a coverage summary. See nfl/props/lines.py for the table definitions.

  GCS_BUCKET=<bucket> PYTHONPATH=. python scripts/nfl_banked_lines.py [--start 2026-10-04] [--end 2026-10-12] \\
        [--out .nfl_lines_out] [--cache-dir .nfl_lines_out/raw_cache] [--raw-dir <local mirror of .../raw>]

Outputs (in --out; do NOT commit this directory):
  lines_series.parquet      one row per (snapshot, game, player, stat, point): consensus + dispersion + per-book fair JSON
  lines_books.parquet       one row per (snapshot, quote): each book's over/under price, hold, de-vigged P(over)
  props_open_close.parquet  per prop: OPEN (first snapshot) / CLOSE proxy (last pre-kick snapshot) / move   (+ .csv)
  props_open_close_ge3.parquet  same, but open/close restricted to snapshots with >= 3 two-sided books
  player_stat_moves.parquet main-line open->close per (game, player, stat)
  snapshot_quality.parquet  per snapshot: props priced, real books, completeness (payloads can be partial views)
  lines_asof.parquet / props_open_close_asof.parquet   carry-forward consensus (each book's last quote <= N min old)
  coverage_summary.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from nfl.props import lines as L


# ----------------------------------------------------------------------------------------------- sources
def dir_source(root: str):
    """(lister, reader) over a local mirror laid out as <root>/raw/<ET day>/<event>_<HHMMSS>.json."""
    base = Path(root)

    def lister(prefix: str) -> list:
        return sorted(str(p.relative_to(base)) for p in base.rglob("*.json"))

    def reader(key: str) -> bytes:
        return (base / key).read_bytes()

    return lister, reader


def cached(reader, cache_dir: str):
    """Wrap a reader with an on-disk cache (key -> file) so re-runs do not re-download."""
    root = Path(cache_dir)

    def read(key: str) -> bytes:
        p = root / key
        if p.exists():
            return p.read_bytes()
        data = reader(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return data

    return read


# ----------------------------------------------------------------------------------------------- schedule join
def attach_schedule(df: pd.DataFrame, loader=None) -> pd.DataFrame:
    """Add season / week / game_id (nflverse schedules, joined on game_key) to any frame with `game_key`.
    Best effort: when schedules are not readable the columns are NaN and the summary falls back to ET dates."""
    out = df.copy()
    out["season"], out["week"], out["game_id"] = np.nan, np.nan, None
    if out.empty:
        return out
    try:
        if loader is None:
            from nfl.data.read import load
            from nfl.data.keys import add_game_keys
            seasons = sorted({int(k.split(":")[1][:4]) - (1 if int(k.split(":")[1][5:7]) < 3 else 0)
                              for k in out["game_key"].dropna().unique()})
            sched = add_game_keys(load("schedules", seasons=seasons))
        else:
            sched = loader()
    except Exception as e:                                                                    # noqa: BLE001
        print(f"[warn] schedules unavailable ({e!r}); week column left empty", file=sys.stderr)
        return out
    m = sched.dropna(subset=["game_key"]).drop_duplicates("game_key").set_index("game_key")
    for col in ("season", "week", "game_id"):
        out[col] = out["game_key"].map(m[col])
    return out


# ----------------------------------------------------------------------------------------------- summary
def _q(x, qs=(0.05, 0.25, 0.5, 0.75, 0.95)) -> str:
    x = pd.Series(x).dropna()
    return "n=0" if x.empty else "  ".join(f"p{int(q * 100)}={x.quantile(q):+.4f}" for q in qs)


def _move_block(title: str, d: pd.DataFrame, col: str = "move") -> list:
    x = d[col].dropna()
    if x.empty:
        return [f"{title}: n=0"]
    ax = x.abs()
    return [f"{title}: n={len(x)}  mean={x.mean():+.4f}  sd={x.std():.4f}  mean|move|={ax.mean():.4f}  "
            f"median|move|={ax.median():.4f}",
            f"    quantiles {_q(x)}",
            f"    share |move| >= 1pp {np.mean(ax >= .01):.1%}  >= 2pp {np.mean(ax >= .02):.1%}  "
            f">= 5pp {np.mean(ax >= .05):.1%}   t(mean!=0)={(x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 1 and x.std() > 1e-9 else float('nan')):+.2f}"]


def _oc_block(title: str, ser: pd.DataFrame, oc: pd.DataFrame, oc3: pd.DataFrame) -> list:
    """Books-per-prop histograms and the open->close move distribution for one flavour of series."""
    o = [f"--- {title} ---", f"props: {len(oc):,}"]
    add = o.append
    mx = ser.groupby(L.PROP_KEY)["n_books"].max()
    add("Books per prop (two-sided real books) at CLOSE proxy / at OPEN / max over time:")
    for tag, col in (("close", oc["close_n_books"]), ("open ", oc["open_n_books"]), ("max  ", mx)):
        h = col.clip(upper=8).value_counts().sort_index()
        add(f"  {tag}: " + "  ".join(f"{int(k)}{'+' if k >= 8 else ''}:{v}" for k, v in h.items())
            + f"   share>=3: {(col >= 3).mean():.1%}")
    both3 = oc[(oc["open_n_books"] >= 3) & (oc["close_n_books"] >= 3)]
    usable3 = both3[both3["close_fresh"] & (both3["n_snaps"] >= 2)]
    add(f">=3 books at BOTH open and close: {len(both3):,} props ({len(both3) / max(len(oc), 1):.1%}); "
        f"of which close within {int(L.CLOSE_FRESH_MIN)} min of kick AND >= 2 snapshots (CLV-usable): {len(usable3):,}")
    add(f"props still quoted at the game's final pre-kick snapshot: {oc['alive_at_close'].mean():.1%}   "
        f"last observation within {int(L.CLOSE_FRESH_MIN)} min of kick: {oc['close_fresh'].mean():.1%}")
    add("Snapshots per prop: " + _q(oc["n_snaps"], (0.25, 0.5, 0.75, 0.95)) + f"   single-snapshot props: {(oc['n_snaps'] == 1).mean():.1%}")
    add(f"OPEN -> CLOSE consensus move in P(over) (close - open); 'usable' = >= 2 snapshots and last obs within {int(L.CLOSE_FRESH_MIN)} min of kick:")
    multi = oc[(oc["n_snaps"] >= 2)]
    use = multi[multi["close_fresh"]]
    o.extend(_move_block("  all props with >= 2 snapshots", multi))
    o.extend(_move_block("  usable close", use))
    o.extend(_move_block("  usable close AND >= 3 books at both ends", usable3))
    o.extend(_move_block("  same, like-for-like books only (move_common)", usable3, "move_common"))
    if not usable3.empty:
        add(f"    mean open lead {usable3['open_lead_min'].mean():.0f} min -> close lead {usable3['close_lead_min'].mean():.0f} min; "
            "by stat mean|move|: " + "  ".join(f"{k}={v:.4f}(n={n})" for (k, v), n in zip(
                usable3.groupby('stat')['move'].agg(lambda x: x.abs().mean()).items(),
                usable3.groupby('stat').size())))
    if len(oc3):
        m3 = oc3[(oc3["n_snaps"] >= 2) & oc3["close_fresh"]]
        o.extend(_move_block("  open/close taken only at snapshots with >= 3 books (first/last such)", m3))
    jit = ser.sort_values("snap_ts").groupby(L.PROP_KEY)["p_over"].diff().abs().dropna()
    add(f"  consecutive-snapshot jitter of the consensus: median |dP|={jit.median():.4f}  p90={jit.quantile(.9):.4f}")
    return o


def coverage_summary(series, books, oc, oc3, moves, report, asof=None, oc_asof=None, oc3_asof=None, quality=None,
                     asof_stale=60.0) -> str:
    o = []
    add = o.append
    add("NFL BANKED PROP LINES -- coverage summary (PRE-KICK snapshots only)")
    add("=" * 78)
    add(f"raw payload refs listed: {report.get('raw_refs')}   parsed: {report.get('snapshots_parsed')}   "
        f"kept pre-kick: {report.get('snapshots_kept')}")
    for k in ("unreadable", "bad_payload", "no_commence", "unknown_teams", "dropped_not_pre_kick",
              "dropped_commence_unreported", "dropped_no_game_key", "dropped_duplicate_snapshot", "dup_quotes"):
        if report.get(k):
            add(f"  {k}: {report[k]}")
    if report.get("unmapped_keys"):
        add(f"  unmapped player_* market keys (quotes DROPPED by ev.props): {report['unmapped_keys']}")
    if report.get("events_with_changed_game_key"):
        add(f"  events whose ET game date changed across snapshots: {report['events_with_changed_game_key']}")
    if series.empty:
        add("NO ROWS")
        return "\n".join(o)
    add(f"series rows: {len(series):,}   book-quote rows: {len(books):,}   props (game,player,stat,point): {len(oc):,}")
    add(f"games: {series['game_key'].nunique()}   ParlayAPI events: {series['event_id'].nunique()}   "
        f"ET days (snapshot): {sorted(series['snap_ts'].dt.tz_convert(L.ET).dt.date.astype(str).unique())}")
    add(f"snapshot span (UTC): {series['snap_ts'].min():%Y-%m-%d %H:%M} -> {series['snap_ts'].max():%Y-%m-%d %H:%M}")
    add("")
    snaps = series.groupby(["event_id", "game_key"])["snap_ts"].agg(["nunique", "min", "max"]).reset_index()
    last_lead = series[series["is_game_last"]].groupby("event_id")["mins_to_kick"].first()
    add("Per game: pre-kick snapshots, and lead time (min before kick) of the LAST snapshot (= closing proxy):")
    for _, r in snaps.sort_values("min").iterrows():
        add(f"  {r['game_key']:<24} snaps={r['nunique']:>3}  last_snapshot_lead={last_lead.get(r['event_id'], float('nan')):>7.1f} min"
            f"{'' if last_lead.get(r['event_id'], 1e9) <= L.CLOSE_FRESH_MIN else '   <-- NOT a usable close (> ' + str(int(L.CLOSE_FRESH_MIN)) + ' min)'}")
    add("")
    oc = oc.copy()
    oc["wk"] = oc["week"].map(lambda w: f"wk{int(w)}" if pd.notna(w) else "wk?") if "week" in oc else "wk?"
    add("Props per week (RAW exact-point props):")
    for wk, g in oc.groupby("wk"):
        add(f"  {wk}: props={len(g):,}  games={g['game_key'].nunique()}  "
            f"props>=3books@close={int((g['close_n_books'] >= 3).sum()):,}")
    add("Props per stat:  " + "  ".join(f"{k}={v}" for k, v in oc["stat"].value_counts().items()))
    add("")
    o.extend(_oc_block("RAW (books present in each snapshot)", series, oc, oc3))
    add("")
    if asof is not None and len(asof):
        o.extend(_oc_block(f"AS-OF carry-forward (each book's last quote <= {int(asof_stale)} min old)", asof, oc_asof, oc3_asof))
        add("")
    if not moves.empty:
        pm = moves[moves["n_snaps"] >= 2]
        add(f"MAIN-LINE movement per (game, player, stat), >= 2 snapshots: n={len(pm)}  "
            f"main point changed: {pm['line_changed'].mean():.1%}  "
            f"mean |point move|={pm['point_move'].abs().mean():.2f}  (by stat: "
            + "  ".join(f"{k}={v:.2f}" for k, v in pm.groupby('stat')['point_move'].agg(lambda x: x.abs().mean()).items()) + ")")
    if quality is not None and len(quality):
        quality = quality.assign(day=quality["snap_ts"].dt.tz_convert(L.ET).dt.date.astype(str))
        add("Snapshot completeness (props priced / real books per payload) by ET day -- payloads can be PARTIAL VIEWS:")
        for day, g in quality.groupby("day"):
            add(f"  {day}: snapshots={len(g):>3}  props/snapshot median={g['n_props'].median():.0f} p10={g['n_props'].quantile(.1):.0f} "
                f"p90={g['n_props'].quantile(.9):.0f}  real books/snapshot median={g['n_real_books'].median():.0f}  "
                f"snapshots with completeness<0.5: {(g['completeness'] < 0.5).mean():.0%}")
    add("Consensus staleness (age of the contributing books' quotes at snapshot, min): "
        f"median-of-median={series['age_med_min'].median():.1f}  p95 of max-age={series['age_max_min'].quantile(.95):.1f}")
    add("")
    add("Books quoting (distinct quote rows): " + "  ".join(f"{k}={v}" for k, v in books["book"].value_counts().items()))
    add("Roles: " + "  ".join(f"{k}={v}" for k, v in books["role"].value_counts().items()))
    return "\n".join(o)


# ----------------------------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=None, help="first ET day (YYYY-MM-DD), inclusive")
    ap.add_argument("--end", default=None, help="last ET day, inclusive")
    ap.add_argument("--out", default=".nfl_lines_out", help="LOCAL output dir (do not commit)")
    ap.add_argument("--cache-dir", default=None, help="cache downloaded raw payloads here (default <out>/raw_cache)")
    ap.add_argument("--raw-dir", default=None, help="read a local mirror (<dir>/raw/<day>/*.json) instead of storage")
    ap.add_argument("--min-lead-min", type=float, default=0.0,
                    help="also drop snapshots closer to kick than this (default 0 = only drop at/after kick)")
    ap.add_argument("--asof-stale-min", type=float, default=60.0,
                    help="as-of carry-forward: max age of a book's last quote to still count (default 60)")
    ap.add_argument("--no-pooled", action="store_true", help="skip the evidence-only pooled columns")
    ap.add_argument("--no-schedule", action="store_true", help="do not join nflverse schedules (week/season)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.raw_dir:
        lister, reader = dir_source(a.raw_dir)
    else:
        lister, reader = L.storage_source()
        reader = cached(reader, a.cache_dir or str(out / "raw_cache"))
    series, books, report = L.build_from_source(lister, reader, a.start, a.end, a.min_lead_min, not a.no_pooled)
    if series.empty:
        print("no pre-kick rows built; report:", report)
        return 1

    oc, oc3 = L.collapse_props(series, 1), L.collapse_props(series, 3)
    moves = L.player_stat_moves(series, 1)
    quality = L.snapshot_quality(series, books)
    asof = L.asof_series(series, books, a.asof_stale_min)
    oc_asof, oc3_asof = L.collapse_props(asof, 1), L.collapse_props(asof, 3)
    if not a.no_schedule:
        series, oc, oc3, moves, oc_asof = (attach_schedule(x) for x in (series, oc, oc3, moves, oc_asof))
    series.to_parquet(out / "lines_series.parquet", index=False)
    books.to_parquet(out / "lines_books.parquet", index=False)
    oc.to_parquet(out / "props_open_close.parquet", index=False)
    oc.drop(columns=[c for c in oc.columns if c.endswith("_book_fair")]).to_csv(out / "props_open_close.csv", index=False)
    oc3.to_parquet(out / "props_open_close_ge3.parquet", index=False)
    moves.to_parquet(out / "player_stat_moves.parquet", index=False)
    quality.to_parquet(out / "snapshot_quality.parquet", index=False)
    asof.to_parquet(out / "lines_asof.parquet", index=False)
    oc_asof.to_parquet(out / "props_open_close_asof.parquet", index=False)
    text = coverage_summary(series, books, oc, oc3, moves, report, asof, oc_asof, oc3_asof, quality, a.asof_stale_min)
    (out / "coverage_summary.txt").write_text(text + "\n", encoding="ascii", errors="replace")
    print(text)
    print(f"\nwrote {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
