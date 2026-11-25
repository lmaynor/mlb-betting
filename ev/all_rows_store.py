"""All-rows probability banking store (MOD-03 b-ii).

Banks EVERY priced board row (not just selected picks) in the OVER perspective,
with grading and market fields precomputed at ingestion and partitioned on disk
or in GCS/storage by ``sport={sport}/day={day}/family={family}/data.parquet``.
"""
from __future__ import annotations

import io
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from mlb_core import storage
from mlb_core.odds.utils import american_to_implied_prob, devig_two_way

_CT = ZoneInfo("America/Chicago")

COLUMNS: tuple[str, ...] = (
    "sport",
    "day",
    "game_key",
    "commence",
    "player",
    "player_norm",
    "team",
    "opp",
    "stat",
    "family",
    "line",
    "consensus_line",
    "is_alt",
    "proj_mean",
    "p_model",
    "p_served",
    "p_market",
    "best_over_odds",
    "best_over_book",
    "best_under_odds",
    "best_under_book",
    "n_books",
    "engine",
    "source",
    "banked_at",
)


def derive_mlb_family(bet_type: str) -> str:
    """Derive MLB family name from a bet_type string."""
    famname = re.sub(r"_(over|under)_[\d.]+.*$", "", bet_type, flags=re.I)
    famname = re.sub(r"_[a-z]+$", "", famname) if famname.endswith("_betmgm") else famname
    return famname


def ct_day(iso) -> str | None:
    """ISO timestamp -> America/Chicago calendar date 'YYYY-MM-DD', or None when unparseable."""
    if iso is None or (isinstance(iso, str) and not iso.strip()):
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(_CT).date().isoformat()


def _f(x) -> float | None:
    """Safe float conversion; returns None for None, bool, NaN, inf, or unparseable values."""
    if x is None or isinstance(x, bool):
        return None
    try:
        val = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(val) or math.isinf(val):
        return None
    return val


def _i(x) -> int | None:
    """Safe int conversion via _f."""
    v = _f(x)
    return int(v) if v is not None else None


def devig_over(best_over_odds, best_under_odds) -> float | None:
    """Compute fair P(over) from American over/under odds, or None if either side is missing."""
    over_odds = _f(best_over_odds)
    under_odds = _f(best_under_odds)
    if over_odds is None or under_odds is None:
        return None
    p_over_imp = american_to_implied_prob(over_odds)
    p_under_imp = american_to_implied_prob(under_odds)
    if p_over_imp is None or p_under_imp is None:
        return None
    if math.isnan(p_over_imp) or math.isnan(p_under_imp):
        return None
    fair_over, _ = devig_two_way(p_over_imp, p_under_imp)
    return _f(fair_over)


def _resolve_stat_family(row: dict) -> tuple[str | None, str | None]:
    raw_stat = row.get("stat")
    bet_type = row.get("bet_type")
    if bet_type and not raw_stat:
        family = derive_mlb_family(str(bet_type))
        return family, family
    return raw_stat, raw_stat


def _convert_mlb_board_row(
    row: dict,
    sport: str,
    day: str,
    line: float,
    stat: str | None,
    family: str | None,
    payload_engine: str | None,
    banked_at_str: str,
) -> dict:
    books = row.get("books") if isinstance(row.get("books"), list) else None
    p_cons = _f(row.get("p_cons"))
    best_over = row.get("best_over") if isinstance(row.get("best_over"), dict) else None
    best_under = row.get("best_under") if isinstance(row.get("best_under"), dict) else None

    best_over_odds = _f(best_over.get("american")) if best_over else None
    best_over_book = best_over.get("book") if best_over else None
    best_under_odds = _f(best_under.get("american")) if best_under else None
    best_under_book = best_under.get("book") if best_under else None

    p_market = p_cons if p_cons is not None else devig_over(best_over_odds, best_under_odds)
    consensus_line = _f(row.get("modal_point"))
    if "is_alt" in row and row.get("is_alt") is not None:
        is_alt = bool(row.get("is_alt"))
    else:
        is_alt = bool(consensus_line is not None and line != consensus_line)

    n_books = len(books) if books else _i(row.get("n_ref"))
    team = row.get("team")
    away = row.get("away")
    home = row.get("home")
    if team and home and team == home:
        opp = away
    elif team and away and team == away:
        opp = home
    else:
        opp = row.get("opp")

    return {
        "sport": sport,
        "day": day,
        "game_key": row.get("game_key"),
        "commence": row.get("commence"),
        "player": row.get("player"),
        "player_norm": row.get("player_norm"),
        "team": team,
        "opp": opp,
        "stat": stat,
        "family": family,
        "line": float(line),
        "consensus_line": consensus_line,
        "is_alt": is_alt,
        "proj_mean": None,
        "p_model": None,
        "p_served": None,
        "p_market": p_market,
        "best_over_odds": best_over_odds,
        "best_over_book": best_over_book,
        "best_under_odds": best_under_odds,
        "best_under_book": best_under_book,
        "n_books": n_books,
        "engine": row.get("engine") or row.get("model") or payload_engine,
        "source": "mlb_odds_board",
        "banked_at": banked_at_str,
    }


def _convert_props_row(
    row: dict,
    sport: str,
    day: str,
    line: float,
    stat: str | None,
    family: str | None,
    source: str,
    payload_engine: str | None,
    banked_at_str: str,
) -> dict:
    consensus_line = _f(row.get("consensus_line"))
    is_alt = bool(consensus_line is not None and line is not None and float(line) != float(consensus_line))
    best_over_odds = _f(row.get("best_over_odds"))
    best_under_odds = _f(row.get("best_under_odds"))
    p_market = _f(row.get("p_over_market"))
    if p_market is None:
        p_market = devig_over(best_over_odds, best_under_odds)

    return {
        "sport": sport,
        "day": day,
        "game_key": row.get("game_key"),
        "commence": row.get("commence"),
        "player": row.get("player"),
        "player_norm": row.get("player_norm"),
        "team": row.get("team"),
        "opp": row.get("opp"),
        "stat": stat,
        "family": family,
        "line": float(line),
        "consensus_line": consensus_line,
        "is_alt": is_alt,
        "proj_mean": _f(row.get("proj_mean")),
        "p_model": _f(row.get("p_over_model")),
        "p_served": _f(row.get("p_over_blend")),
        "p_market": p_market,
        "best_over_odds": best_over_odds,
        "best_over_book": row.get("best_over_book"),
        "best_under_odds": best_under_odds,
        "best_under_book": row.get("best_under_book"),
        "n_books": _i(row.get("n_books")),
        "engine": row.get("engine") or row.get("model") or payload_engine,
        "source": source,
        "banked_at": banked_at_str,
    }


def convert_with_summary(
    payload: dict,
    source: str = "daily_projections",
    banked_at: str | None = None,
) -> tuple[list[dict], dict]:
    """Convert a board or archive payload into canonical OVER-perspective records and counts."""
    banked_at_str = banked_at or datetime.now(timezone.utc).isoformat()
    raw_rows = payload.get("rows")
    archive_day = payload.get("day") if isinstance(raw_rows, dict) else None
    if isinstance(raw_rows, dict):
        rows_iter = list(raw_rows.values())
    elif isinstance(raw_rows, list):
        rows_iter = raw_rows
    else:
        rows_iter = []

    payload_sport = payload.get("sport")
    payload_ts = payload.get("ts") or payload.get("updated_at")
    payload_engine = payload.get("engine")

    records: list[dict] = []
    seen: set[tuple] = set()
    rows_unpriced = 0
    rows_duplicate = 0

    for row in rows_iter:
        if not isinstance(row, dict):
            rows_unpriced += 1
            continue

        sport = str(payload_sport or row.get("sport") or "").lower()
        is_mlb_board = (source == "mlb_odds_board") or (
            sport == "mlb" and ("p_cons" in row or "books" in row)
        )
        day = archive_day or ct_day(row.get("commence")) or ct_day(payload_ts)
        line = _f(row.get("line"))

        if is_mlb_board:
            books = row.get("books") if isinstance(row.get("books"), list) else None
            p_cons = _f(row.get("p_cons"))
            best_over = row.get("best_over") if isinstance(row.get("best_over"), dict) else None
            best_under = row.get("best_under") if isinstance(row.get("best_under"), dict) else None
            has_priced = line is not None and (
                bool(books) or p_cons is not None or best_over is not None or best_under is not None
            )
            if not has_priced or not day:
                rows_unpriced += 1
                continue
        else:
            if not row.get("has_line") or line is None or not day:
                rows_unpriced += 1
                continue

        stat, family = _resolve_stat_family(row)
        dedupe_key = (sport, day, row.get("player_norm"), stat, line)
        if dedupe_key in seen:
            rows_duplicate += 1
            continue
        seen.add(dedupe_key)

        if is_mlb_board:
            rec = _convert_mlb_board_row(
                row=row,
                sport=sport,
                day=day,
                line=line,
                stat=stat,
                family=family,
                payload_engine=payload_engine,
                banked_at_str=banked_at_str,
            )
        else:
            rec = _convert_props_row(
                row=row,
                sport=sport,
                day=day,
                line=line,
                stat=stat,
                family=family,
                source=source,
                payload_engine=payload_engine,
                banked_at_str=banked_at_str,
            )
        records.append(rec)

    summary = {
        "rows_in": len(rows_iter),
        "rows_banked": len(records),
        "rows_unpriced": rows_unpriced,
        "rows_duplicate": rows_duplicate,
    }
    return records, summary


def convert_payload(
    payload: dict,
    source: str = "daily_projections",
    banked_at: str | None = None,
) -> list[dict]:
    """Convert a board or archive payload into canonical OVER-perspective records."""
    records, _ = convert_with_summary(payload, source=source, banked_at=banked_at)
    return records


def _sanitize_family(family: str | None) -> str:
    raw = str(family or "unknown")
    return raw.replace("/", "_").replace(os.sep, "_")


def _parse_storage_prefix(root: str | Path) -> str | None:
    """Return storage key prefix for GCS/EVSweep roots, or None for local filesystem paths."""
    root_str = str(root)
    if root_str.startswith("gs://"):
        rest = root_str[5:]
        parts = rest.split("/", 1)
        return parts[1].rstrip("/") if len(parts) > 1 else ""
    if root_str.startswith("EVSweep/") or root_str == "EVSweep":
        return root_str.rstrip("/")
    return None


def write_records(records: list[dict], root: str | Path) -> dict:
    """Write canonical records as partitioned parquet files (idempotent per partition)."""
    if not records:
        return {"partitions": 0, "rows": 0, "paths": []}

    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for rec in records:
        key = (
            str(rec.get("sport") or ""),
            str(rec.get("day") or ""),
            _sanitize_family(rec.get("family")),
        )
        grouped[key].append(rec)

    key_prefix = _parse_storage_prefix(root)
    paths: list[str] = []

    for (sport, day, safe_family), grp in grouped.items():
        partition = f"sport={sport}/day={day}/family={safe_family}"
        df = pd.DataFrame(grp)
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = None
        df = df[list(COLUMNS)]

        if key_prefix is not None:
            buf = io.BytesIO()
            df.to_parquet(buf, index=False, engine="pyarrow")
            obj_key = f"{key_prefix}/{partition}/data.parquet" if key_prefix else f"{partition}/data.parquet"
            storage.write_bytes(buf.getvalue(), obj_key)
            paths.append(obj_key)
        else:
            part_dir = Path(str(root)).expanduser() / partition
            part_dir.mkdir(parents=True, exist_ok=True)
            for existing in part_dir.glob("*.parquet"):
                existing.unlink()
            out_path = part_dir / "data.parquet"
            df.to_parquet(out_path, index=False, engine="pyarrow")
            paths.append(str(out_path))

    return {"partitions": len(paths), "rows": len(records), "paths": paths}


def read_records(root: str | Path, sport: str | None = None, day: str | None = None) -> pd.DataFrame:
    """Read partitioned parquet files from a local root directory into a DataFrame."""
    base = Path(str(root)).expanduser()
    if not base.exists():
        return pd.DataFrame(columns=list(COLUMNS))

    sport_pat = f"sport={sport.lower()}" if sport else "sport=*"
    day_pat = f"day={day}" if day else "day=*"
    files = sorted(base.glob(f"{sport_pat}/{day_pat}/family=*/data.parquet"))
    if not files:
        return pd.DataFrame(columns=list(COLUMNS))

    dfs = [pd.read_parquet(f) for f in files]
    out = pd.concat(dfs, ignore_index=True)
    for col in COLUMNS:
        if col not in out.columns:
            out[col] = None
    return out[list(COLUMNS)]


def bank_payload(
    payload: dict,
    source: str = "daily_projections",
    root: str | Path = "EVSweep/all_rows",
    banked_at: str | None = None,
) -> dict:
    """Convert a payload and write its banked parquet partitions, returning a merged summary."""
    records, conv_summary = convert_with_summary(payload, source=source, banked_at=banked_at)
    write_summary = write_records(records, root)
    return {**conv_summary, **write_summary}
