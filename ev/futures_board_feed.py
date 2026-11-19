"""ev/futures_board_feed.py -- Feed adapter and shadow logger for RES-12 futures sleeve.

Wires banked futures partitions (Kalshi and BettingPros) into the RES-12 futures sleeve
(ev.futures_sleeve.bank_futures_board), and produces shadow entry-signal logs.

THE q CONVENTION:
q is a fair-estimate input to the sleeve's n-way Power devig solver, NEVER the raw ask.
For Kalshi:
    1. Two-sided market: q = mid (yes_bid + yes_ask) / 2.0 when yes_bid > 0 and yes_ask > yes_bid.
    2. Else: q = last_price when last_price is present and > 0.
    3. Else: q = yes_ask when yes_ask is present and > 0 (supports ask-only tail markets).
    4. Rows with no usable price (all three missing/zero or non-positive) are skipped and counted
       in the returned stats dict.

For BettingPros (Outrights Only):
    Outright futures only: rows where line is null/NaN, side is not in ("over", "under"),
    and is_open is falsy. Season O/U futures (with non-null line or side in over/under) are
    two-sided lines, not n-way outrights, and are excluded and counted in stats. Opening quotes
    (is_open truthy) are also excluded and counted in stats.
    q is computed from American odds:
        odds > 0: 100.0 / (odds + 100.0)
        odds < 0: |odds| / (|odds| + 100.0)
    venue = book name lowercased with spaces removed (e.g. "DraftKings" -> "draftkings").
    market = f"{sport}:{slug}".
    outcome = player.

SHADOW SIGNAL LOGGING:
Emits daily board snapshots with Power-devigged fair probabilities. P_sim has NO source
anywhere in the stack and must stay explicitly null (None) -- never substitute p_fair_power
or any other number for p_sim, and never call evaluate_futures_entry with a fabricated p_sim.
The log banks daily boards and devigged fairs so dislocation distributions can be measured
the day a P_sim model lands.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Sequence

import pandas as pd

from ev.futures_sleeve import FuturesBoardRow, bank_futures_board, devig_n_way_power


class FeedSnapshots(NamedTuple):
    """Container for parsed snapshot dictionaries and extraction statistics."""
    snapshots: list[dict[str, Any]]
    stats: dict[str, int]


def _to_float(val: Any) -> float | None:
    """Safely convert value to finite float or return None."""
    if val is None or pd.isna(val):
        return None
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return None


def kalshi_rows_to_snapshots(df: pd.DataFrame | None) -> FeedSnapshots:
    """Convert Kalshi futures partition rows into snapshot dicts for bank_futures_board.

    THE q CONVENTION (prominently enforced):
    q is a fair-estimate input to the sleeve's n-way Power devig, NEVER the raw ask.
    - Two-sided: q = (yes_bid + yes_ask) / 2.0 when yes_bid > 0 and yes_ask > yes_bid.
    - Else: q = last_price when present and > 0.
    - Else: q = yes_ask when present and > 0 (supports ask-only tail markets where bid=0.0).
    - Rows with no usable price (all three missing/zero or non-positive) are skipped and counted
      in the returned stats dict.

    Returns:
        FeedSnapshots(snapshots: list[dict], stats: dict[str, int])
    """
    stats = {
        "total_rows": 0,
        "valid_snapshots": 0,
        "skipped_no_price": 0,
    }
    snapshots: list[dict[str, Any]] = []

    if df is None or df.empty:
        return FeedSnapshots(snapshots=snapshots, stats=stats)

    for _, row in df.iterrows():
        stats["total_rows"] += 1

        bid = _to_float(row.get("yes_bid"))
        ask = _to_float(row.get("yes_ask"))
        last = _to_float(row.get("last_price"))

        # Follow the q convention strictly
        q: float | None = None
        if bid is not None and ask is not None and bid > 0 and ask > bid:
            q = (bid + ask) / 2.0
        elif last is not None and last > 0:
            q = last
        elif ask is not None and ask > 0:
            q = ask

        if q is None or q <= 0:
            stats["skipped_no_price"] += 1
            continue

        venue = str(row.get("venue") or "kalshi").lower().strip()
        market = str(row.get("market") or row.get("series_ticker") or "").strip()
        outcome = str(row.get("outcome") or "").strip()
        ts = row.get("captured_at")

        extra_fields = {
            "bid": bid,
            "ask": ask,
            "last": last,
            "series_ticker": row.get("series_ticker"),
            "market_ticker": row.get("market_ticker"),
            "event_ticker": row.get("event_ticker"),
            "yes_sub_title": row.get("yes_sub_title"),
            "league": row.get("league"),
            "family": row.get("family"),
            "strike_type": row.get("strike_type"),
            "floor_strike": row.get("floor_strike"),
            "cap_strike": row.get("cap_strike"),
            "volume_fp": row.get("volume_fp"),
            "open_interest_fp": row.get("open_interest_fp"),
            "status": row.get("status"),
            "fee_type": row.get("fee_type"),
            "fee_multiplier": row.get("fee_multiplier"),
            "dt": row.get("dt"),
        }

        snap = {
            "venue": venue,
            "market": market,
            "outcome": outcome,
            "q": q,
            "ts": ts,
            **extra_fields,
        }
        snapshots.append(snap)
        stats["valid_snapshots"] += 1

    return FeedSnapshots(snapshots=snapshots, stats=stats)


def bp_rows_to_snapshots(
    df: pd.DataFrame | None,
    sport: str,
    slug: str | None = None,
) -> FeedSnapshots:
    """Convert BettingPros futures partition rows into snapshot dicts for bank_futures_board.

    OUTRIGHTS ONLY:
    - line must be null/NaN.
    - side must not be 'over' or 'under'.
    - is_open must be falsy (opening quote rows excluded).
    Season O/U futures (rows with line populated or side in over/under) are two-sided lines,
    not n-way outrights: excluded and counted in stats. Opening quotes are also excluded.

    q is computed from American odds:
        odds > 0: 100.0 / (odds + 100.0)
        odds < 0: abs(odds) / (abs(odds) + 100.0)

    Returns:
        FeedSnapshots(snapshots: list[dict], stats: dict[str, int])
    """
    stats = {
        "total_rows": 0,
        "valid_snapshots": 0,
        "excluded_season_ou": 0,
        "excluded_is_open": 0,
        "skipped_invalid_odds": 0,
    }
    snapshots: list[dict[str, Any]] = []

    if df is None or df.empty:
        return FeedSnapshots(snapshots=snapshots, stats=stats)

    for _, row in df.iterrows():
        stats["total_rows"] += 1

        # Check season O/U futures
        line = row.get("line")
        side = str(row.get("side") or "").strip().lower()
        if (line is not None and not pd.isna(line) and line != "") or side in ("over", "under"):
            stats["excluded_season_ou"] += 1
            continue

        # Check opening quotes (is_open)
        is_open = row.get("is_open")
        if is_open is not None and not pd.isna(is_open) and bool(is_open):
            stats["excluded_is_open"] += 1
            continue

        # Check American odds
        raw_odds = row.get("odds")
        odds_val: int | None = None
        if raw_odds is not None and not pd.isna(raw_odds):
            try:
                odds_val = int(raw_odds)
            except (ValueError, TypeError):
                try:
                    odds_val = int(float(raw_odds))
                except (ValueError, TypeError):
                    odds_val = None

        if odds_val is None or odds_val == 0:
            stats["skipped_invalid_odds"] += 1
            continue

        if odds_val > 0:
            q = 100.0 / (odds_val + 100.0)
        else:
            q = abs(odds_val) / (abs(odds_val) + 100.0)

        # venue = row's book name lowercased with spaces removed
        book_raw = str(row.get("book") or "").strip()
        venue = book_raw.lower().replace(" ", "")

        # market = f"{sport}:{slug}"
        row_market = str(row.get("market") or "").strip()
        m_slug = row_market or slug or "unknown"
        if m_slug.startswith(f"{sport}:"):
            market = m_slug
        else:
            market = f"{sport}:{m_slug}"

        # outcome = player column
        outcome = str(row.get("player") or "").strip()

        # ts = updated
        ts = row.get("updated")

        extra_fields = {
            "book": book_raw,
            "book_kind": row.get("book_kind"),
            "odds": odds_val,
            "market_id": row.get("market_id"),
            "team": row.get("team"),
            "date": row.get("date"),
            "sport": sport,
            "side": row.get("side"),
        }

        snap = {
            "venue": venue,
            "market": market,
            "outcome": outcome,
            "q": q,
            "ts": ts,
            **extra_fields,
        }
        snapshots.append(snap)
        stats["valid_snapshots"] += 1

    return FeedSnapshots(snapshots=snapshots, stats=stats)


def build_board(
    kalshi_df: pd.DataFrame | None = None,
    bp_frames: Sequence[tuple[Any, ...]] | None = None,
) -> list[FuturesBoardRow]:
    """Concatenate snapshots from Kalshi and BettingPros and call bank_futures_board.

    Args:
        kalshi_df: DataFrame of Kalshi futures partition rows.
        bp_frames: List of (df, sport) or (df, sport, slug) tuples.

    Returns:
        List of FuturesBoardRow with Power devig applied per (venue, market).
    """
    all_snapshots: list[dict[str, Any]] = []

    if kalshi_df is not None and not kalshi_df.empty:
        k_snaps, _ = kalshi_rows_to_snapshots(kalshi_df)
        all_snapshots.extend(k_snaps)

    if bp_frames:
        for item in bp_frames:
            if not item:
                continue
            df = item[0]
            sport = str(item[1]) if len(item) > 1 else "unknown"
            slug = str(item[2]) if len(item) > 2 else None
            if df is not None and not df.empty:
                b_snaps, _ = bp_rows_to_snapshots(df, sport, slug=slug)
                all_snapshots.extend(b_snaps)

    if not all_snapshots:
        return []

    return bank_futures_board(all_snapshots)


NO_BET_NO_PSIM = "NO_BET_NO_PSIM"


class FuturesFairProvider:
    """Interface for a model fair-probability source (RUL-20261119-closeout-plan).

    A provider answers p_sim for one (venue, market, outcome) as of a date, or
    None when it has no fair for that outcome. The first real provider is the
    NFL remaining-season Monte Carlo (named next build, NOT built tonight).
    Until one exists, NullFuturesFairProvider is the only implementation and
    every signal is NO_BET_NO_PSIM at $0.
    """

    def p_sim(self, venue: str, market: str, outcome: str, as_of: str) -> float | None:
        raise NotImplementedError


class NullFuturesFairProvider(FuturesFairProvider):
    """The honest default: no P_sim source exists, so no fair, ever."""

    def p_sim(self, venue: str, market: str, outcome: str, as_of: str) -> float | None:
        return None


def kalshi_fee(price: float) -> float:
    """Kalshi's per-contract fee form: 0.07 * p * (1 - p), in probability units."""
    return 0.07 * price * (1.0 - price)


def _norm_name(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def basis_monitor(
    kalshi_snapshots: Sequence[dict[str, Any]],
    bp_snapshots: Sequence[dict[str, Any]],
    ts: str | datetime | None = None,
) -> list[dict[str, Any]]:
    """Shadow cross-venue basis monitor (RUL-20261119-closeout-plan).

    For outcomes quoted on BOTH venues: the BettingPros multi-book futures
    consensus (each book's board Power-devigged -- the stack's method="log"
    power devig -- then averaged across books) versus the Kalshi EXECUTABLE
    ask, fee-adjusted (ask + 0.07*p*(1-p)), on both the YES and NO sides.
    Records are measurement only: kind="basis", status="SHADOW_BASIS_ONLY".
    Outcomes are matched across venues by normalized name within a league;
    unmatched outcomes are counted honestly in the summary record, never
    force-matched.
    """
    if ts is None:
        log_ts = datetime.now(timezone.utc).isoformat()
    elif isinstance(ts, datetime):
        log_ts = (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).isoformat()
    else:
        log_ts = str(ts)

    # BP consensus: per (market, book) Power devig, then mean across books.
    bp_by_market_book: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for s in bp_snapshots:
        bp_by_market_book.setdefault((s["market"], s["venue"]), []).append(s)
    consensus: dict[tuple[str, str], dict[str, Any]] = {}  # (sport, norm outcome) -> info
    for (market, _book), rows in bp_by_market_book.items():
        sport = market.split(":", 1)[0]
        qs = [r["q"] for r in rows]
        try:
            fairs = devig_n_way_power(qs)
        except ValueError:
            continue
        for r, fair in zip(rows, fairs):
            key = (sport, _norm_name(r["outcome"]))
            entry = consensus.setdefault(key, {"fairs": [], "books": set(), "market": market, "outcome": r["outcome"]})
            entry["fairs"].append(fair)
            entry["books"].add(r["venue"])

    records: list[dict[str, Any]] = []
    matched_kalshi: set[tuple[str, str]] = set()
    for s in kalshi_snapshots:
        league = str(s.get("league") or "").lower()
        key = (league, _norm_name(s.get("outcome")))
        c = consensus.get(key)
        if c is None:
            continue
        matched_kalshi.add(key)
        bid, ask = s.get("bid"), s.get("ask")
        if ask is None or ask <= 0:
            continue
        fair = sum(c["fairs"]) / len(c["fairs"])
        exec_yes = ask + kalshi_fee(ask)
        rec: dict[str, Any] = {
            "kind": "basis", "status": "SHADOW_BASIS_ONLY", "ts": log_ts,
            "league": league, "outcome": s.get("outcome"),
            "kalshi_market": s.get("market"), "bp_market": c["market"],
            "bp_consensus_fair": round(fair, 6), "bp_n_books": len(c["books"]),
            "devig": "power(method=log)",
            "kalshi_ask_yes": ask, "kalshi_exec_yes_fee_adj": round(exec_yes, 6),
            "basis_yes": round(fair - exec_yes, 6),
        }
        if bid is not None and bid > 0:
            no_ask = 1.0 - bid
            exec_no = no_ask + kalshi_fee(no_ask)
            rec.update({
                "kalshi_ask_no": round(no_ask, 6),
                "kalshi_exec_no_fee_adj": round(exec_no, 6),
                "basis_no": round((1.0 - fair) - exec_no, 6),
            })
        records.append(rec)

    matched_bp = {k for k in consensus if k in matched_kalshi}
    records.append({
        "kind": "basis_summary", "status": "SHADOW_BASIS_ONLY", "ts": log_ts,
        "matched_outcomes": len(matched_bp),
        "bp_outcomes_total": len(consensus),
        "bp_outcomes_unmatched": len(consensus) - len(matched_bp),
        "kalshi_snapshots_total": len(kalshi_snapshots),
        "kalshi_snapshots_matched": sum(1 for s in kalshi_snapshots
                                        if (str(s.get("league") or "").lower(), _norm_name(s.get("outcome"))) in matched_bp),
    })
    return records


def canonical_futures_snapshots(date: str, root: str = "") -> list[dict[str, Any]] | None:
    """PR-C consumes PR-B's canonical futures interface when it exists
    (RUL-20261119: PR-B merges before PR-C). Guarded import: on a tree without
    the canonical layer this returns None and callers fall back to the direct
    bank adapters (load_kalshi_partition / load_bp_futures). Canonical Kalshi
    rows convert with the same q convention: mid when two-sided, else ask."""
    try:
        from mlb_core.odds.canonical.read import canonical_futures
    except Exception:
        return None
    try:
        df = canonical_futures(date, root=root)
    except Exception:
        return None
    if df is None or not len(df):
        return None
    snaps: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        if rec.get("book_id") != "kalshi":
            continue
        extras = {}
        raw_extras = rec.get("source_extras")
        if isinstance(raw_extras, str) and raw_extras:
            try:
                extras = json.loads(raw_extras)
            except Exception:
                extras = {}
        ask = extras.get("yes_ask") or rec.get("implied_raw")
        bid = extras.get("yes_bid")
        if bid is None and rec.get("mid_prob") is not None and ask:
            bid = 2.0 * float(rec["mid_prob"]) - float(ask)
        q = None
        if bid and ask and float(bid) > 0 and float(ask) > float(bid):
            q = (float(bid) + float(ask)) / 2.0
        elif rec.get("mid_prob"):
            q = float(rec["mid_prob"])
        elif ask:
            q = float(ask)
        if not q or q <= 0:
            continue
        snaps.append({
            "venue": "kalshi", "market": rec.get("market"),
            "outcome": extras.get("outcome"), "q": q, "ts": rec.get("observed_at"),
            "bid": float(bid) if bid else None, "ask": float(ask) if ask else None,
            "league": rec.get("league"), "via": "canonical_futures",
        })
    return snaps


def shadow_signal_log(
    board_rows: Sequence[FuturesBoardRow],
    ts: str | datetime | None = None,
    fair_provider: FuturesFairProvider | None = None,
) -> list[dict[str, Any]]:
    """Produce shadow entry-signal log records from devigged board rows.

    P_sim comes ONLY from the injected FuturesFairProvider. The default (and,
    tonight, only) provider is NullFuturesFairProvider: p_sim stays explicitly
    null, entry_fired stays False, and every signal's reason is the ruling's
    token NO_BET_NO_PSIM ($0). Never substitute p_fair_power for p_sim, and
    never call evaluate_futures_entry from the shadow path.

    Emits:
        - "board_summary" records per (venue, market) with n_outcomes and sum_q_market.
        - "signal" records per board row with q_market, p_fair_power, p_sim,
          entry_fired=False, reason.
    """
    provider = fair_provider if fair_provider is not None else NullFuturesFairProvider()
    if ts is None:
        log_ts = datetime.now(timezone.utc).isoformat()
    elif isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        log_ts = ts.isoformat()
    else:
        log_ts = str(ts)

    # Group by (venue, market) preserving order
    grouped: dict[tuple[str, str], list[FuturesBoardRow]] = {}
    for row in board_rows:
        key = (row.venue, row.market)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(row)

    records: list[dict[str, Any]] = []
    for (venue, market), rows in grouped.items():
        sum_q = sum(r.q_market for r in rows)
        records.append({
            "kind": "board_summary",
            "ts": log_ts,
            "venue": venue,
            "market": market,
            "n_outcomes": len(rows),
            "sum_q_market": round(sum_q, 6),
            "q_market_sum": round(sum_q, 6),
        })

        for r in rows:
            p_sim = provider.p_sim(r.venue, r.market, r.outcome, log_ts[:10])
            records.append({
                "kind": "signal",
                "ts": log_ts,
                "venue": r.venue,
                "market": r.market,
                "outcome": r.outcome,
                "q_market": r.q_market,
                "p_fair_power": r.p_fair_power,
                "p_sim": p_sim,
                "entry_fired": False,
                "reason": NO_BET_NO_PSIM if p_sim is None else "P_SIM_PRESENT_SHADOW_ONLY",
            })

    return records


# ---------------------------------------------------------------------------
# Storage & IO Helpers (Thin)
# ---------------------------------------------------------------------------

def _parse_root(root: str | Path | None) -> tuple[str, str, str | Path]:
    """Parse root into (mode, gcs_bucket_or_empty, prefix_or_local_path)."""
    if root is None or (isinstance(root, str) and not root.strip()):
        from mlb_core.storage import _get_bucket, _get_base_data
        b = _get_bucket()
        if b:
            return "gcs", b, ""
        return "local", "", _get_base_data()

    root_str = str(root).strip()
    if root_str.startswith("gs://"):
        without_scheme = root_str[5:]
        parts = without_scheme.split("/", 1)
        bucket = parts[0]
        prefix = parts[1].strip("/") if len(parts) > 1 else ""
        return "gcs", bucket, prefix
    return "local", "", Path(root_str)


def _read_parquet_key(root: str | Path | None, rel_key: str) -> pd.DataFrame | None:
    """Read a parquet file from either GCS or local filesystem."""
    mode, bucket_name, prefix_or_path = _parse_root(root)
    if mode == "gcs":
        try:
            from google.cloud import storage as gcs
            client = gcs.Client()
            bucket = client.bucket(bucket_name)
            pfx = str(prefix_or_path).strip("/") if prefix_or_path else ""
            blob_key = f"{pfx}/{rel_key.lstrip('/')}".strip("/") if pfx else rel_key.lstrip("/")
            blob = bucket.blob(blob_key)
            if not blob.exists():
                return None
            data = blob.download_as_bytes(timeout=600)
            return pd.read_parquet(io.BytesIO(data))
        except Exception:
            return None
    else:
        assert isinstance(prefix_or_path, Path)
        p = prefix_or_path / rel_key.lstrip("/")
        if not p.is_file():
            return None
        try:
            return pd.read_parquet(p)
        except Exception:
            return None


def _list_keys_under(root: str | Path | None, prefix: str) -> list[str]:
    """List relative keys matching prefix under root."""
    mode, bucket_name, prefix_or_path = _parse_root(root)
    if mode == "gcs":
        try:
            from google.cloud import storage as gcs
            client = gcs.Client()
            bucket = client.bucket(bucket_name)
            base_pfx = str(prefix_or_path).strip("/") if prefix_or_path else ""
            clean_prefix = prefix.strip("/")
            full_pfx = f"{base_pfx}/{clean_prefix}".strip("/") if base_pfx else clean_prefix
            blobs = client.list_blobs(bucket, prefix=full_pfx)
            keys: list[str] = []
            for b in blobs:
                name = b.name
                if base_pfx and name.startswith(base_pfx + "/"):
                    name = name[len(base_pfx) + 1:]
                keys.append(name)
            return keys
        except Exception:
            return []
    else:
        assert isinstance(prefix_or_path, Path)
        dir_p = prefix_or_path / prefix.strip("/")
        if not dir_p.exists():
            return []
        keys = []
        for p in dir_p.rglob("*.parquet"):
            if p.is_file():
                rel = p.relative_to(prefix_or_path)
                keys.append(str(rel))
        return sorted(keys)


def load_kalshi_partition(root: str | Path | None, dt: str) -> pd.DataFrame | None:
    """Load Kalshi futures partition for a given date.

    Key: Futures/kalshi/dt={dt}/snapshot.parquet
    """
    rel_key = f"Futures/kalshi/dt={dt}/snapshot.parquet"
    return _read_parquet_key(root, rel_key)


def load_bp_futures(
    root: str | Path | None,
    sport: str,
    dt: str,
    slug: str | None = None,
) -> pd.DataFrame | None:
    """Load BettingPros futures partition(s) for a sport and date.

    If slug is specified, reads Odds/bettingpros_{sport}_futures/{slug}/{dt}.parquet.
    If slug is None, enumerates all slugs for that sport/date, assigns the market column
    if absent, and concatenates them into a single DataFrame.
    """
    if slug is not None:
        rel_key = f"Odds/bettingpros_{sport}_futures/{slug}/{dt}.parquet"
        return _read_parquet_key(root, rel_key)

    prefix = f"Odds/bettingpros_{sport}_futures/"
    keys = _list_keys_under(root, prefix)
    pattern = re.compile(
        rf"^Odds/bettingpros_{re.escape(sport)}_futures/([^/]+)/{re.escape(dt)}\.parquet$"
    )

    frames: list[pd.DataFrame] = []
    for k in keys:
        m = pattern.match(k)
        if m:
            s_slug = m.group(1)
            df = _read_parquet_key(root, k)
            if df is not None and not df.empty:
                if "market" not in df.columns or df["market"].isna().all():
                    df = df.copy()
                    df["market"] = s_slug
                frames.append(df)

    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def discover_bp_futures_partitions(
    root: str | Path | None,
    dt: str,
) -> list[tuple[str, str, str]]:
    """Discover all BettingPros futures partitions for a given date.

    Returns:
        List of (sport, slug, relative_key) tuples.
    """
    keys = _list_keys_under(root, "Odds/")
    pattern = re.compile(
        rf"^Odds/bettingpros_([a-zA-Z0-9_]+)_futures/([^/]+)/{re.escape(dt)}\.parquet$"
    )
    results: list[tuple[str, str, str]] = []
    for k in keys:
        m = pattern.match(k)
        if m:
            sport = m.group(1)
            slug = m.group(2)
            results.append((sport, slug, k))
    return results


def write_shadow_log(
    records: Sequence[dict[str, Any]],
    out_path: str,
    root: str | Path | None = None,
) -> str:
    """Write shadow signal log records as JSONL to stdout, GCS, or local file.

    Returns:
        Destination path string where output was written.
    """
    lines = [json.dumps(r, default=str) for r in records]
    content = "\n".join(lines) + ("\n" if lines else "")

    if out_path == "-":
        import sys
        sys.stdout.write(content)
        sys.stdout.flush()
        return "stdout"

    # Check if out_path itself is a GCS URI
    if out_path.startswith("gs://"):
        without_scheme = out_path[5:]
        bucket_name, blob_key = without_scheme.split("/", 1)
        from google.cloud import storage as gcs
        client = gcs.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_key)
        blob.upload_from_string(content.encode("utf-8"), content_type="application/x-ndjson")
        return out_path

    # Check if root is GCS
    mode, bucket_name, prefix_or_path = _parse_root(root)
    if mode == "gcs":
        pfx = str(prefix_or_path).strip("/") if prefix_or_path else ""
        blob_key = f"{pfx}/{out_path.lstrip('/')}".strip("/") if pfx else out_path.lstrip("/")
        from google.cloud import storage as gcs
        client = gcs.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_key)
        blob.upload_from_string(content.encode("utf-8"), content_type="application/x-ndjson")
        return f"gs://{bucket_name}/{blob_key}"
    else:
        assert isinstance(prefix_or_path, Path)
        dest = (prefix_or_path / out_path.lstrip("/")) if not Path(out_path).is_absolute() else Path(out_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return str(dest)
