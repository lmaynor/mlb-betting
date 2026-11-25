"""MOD-04e E3: live shadow wiring (predictions-file pattern U6).

Scores a pregame slate using fitted MOD-04e models, logs dual probabilities
(``p_model`` from the discrete PMF at the board line, ``p_market``, and
``p_served`` computed via ``ev.daily_projections.blend_p_over`` with the
resolved NFL ``c = 0.00``), stamps truthful engine label
``mod04_nfl_gamescript_shadow_v1``, and writes JSON under
``NFL/mod04e/shadow/<date>/<run_kind>.json``.

Only ``run_kind == "sun_90m_rescore"`` consumes banked game-day inactives;
inactive players are flagged ``inactive_scratch=True``, excluded from the
grading population (``gradeable=False``), and still logged with ``p_model``
mass moved to scratch/zero handling.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from ev import daily_projections as dp
from mlb_core import storage
from nfl.data import outcome_matching as om
from nfl.mod04e.features import assert_feature_schema
from nfl.mod04e.inactives import InactiveRecord, rescore_ready
from nfl.mod04e.model import ENGINE_ID, Model, pmf_p_over

RUN_KINDS = ("thu", "sun_am", "sun_90m_rescore")


def _lookup_line(
    lines_by_player: Mapping[Any, Any],
    player_id: str,
    player_name: str,
    family: str,
) -> Optional[Mapping[str, Any]]:
    # 1. Direct tuple key (player_id, family) or (player_name, family)
    for k in ((player_id, family), (player_name, family)):
        if k in lines_by_player:
            val = lines_by_player[k]
            if isinstance(val, Mapping):
                return val

    # 2. Nested dict {player_id: {family: {...}}} or {player_name: {family: {...}}}
    for pk in (player_id, player_name, om.normalize_name(player_name)):
        if pk and pk in lines_by_player:
            sub = lines_by_player[pk]
            if isinstance(sub, Mapping) and family in sub and isinstance(sub[family], Mapping):
                return sub[family]
    return None


def _build_inactive_lookup(
    inactives: Optional[Iterable[InactiveRecord | Mapping[str, Any]]],
    kickoff_utc_by_game: Optional[Mapping[str, Any]],
) -> tuple[set[str], set[tuple[str, str]]]:
    if not inactives:
        return set(), set()
    recs = list(inactives)
    if kickoff_utc_by_game:
        filtered: list[InactiveRecord] = []
        for rec in recs:
            gid = rec.game if isinstance(rec, InactiveRecord) else str(rec.get("game") or "")
            ko = kickoff_utc_by_game.get(gid)
            if ko is not None:
                filtered.extend(rescore_ready([rec], ko))
            else:
                if isinstance(rec, InactiveRecord):
                    filtered.append(rec)
        recs = filtered

    by_id: set[str] = set()
    by_name_team: set[tuple[str, str]] = set()
    for rec in recs:
        if isinstance(rec, InactiveRecord):
            pid, pname, tm = rec.player_id, rec.player, rec.team
        else:
            pid = rec.get("player_id")
            pname = rec.get("player")
            tm = rec.get("team")
        if pid:
            by_id.add(str(pid))
        nn = om.normalize_name(pname)
        tm_s = str(tm or "").strip().upper()
        if nn:
            by_name_team.add((nn, tm_s))
    return by_id, by_name_team


def score_slate(
    features: pd.DataFrame,
    models: Mapping[str, Model],
    lines_by_player: Mapping[Any, Any],
    *,
    run_kind: str = "sun_am",
    inactives: Optional[Iterable[InactiveRecord | Mapping[str, Any]]] = None,
    kickoff_utc_by_game: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Score a pregame slate and return dual-logged shadow rows."""
    if run_kind not in RUN_KINDS:
        raise ValueError(f"Unknown run_kind {run_kind!r}; expected one of {RUN_KINDS}")
    assert_feature_schema(features)
    if features.empty or not models:
        return []

    inact_ids: set[str] = set()
    inact_name_team: set[tuple[str, str]] = set()
    if run_kind == "sun_90m_rescore":
        inact_ids, inact_name_team = _build_inactive_lookup(inactives, kickoff_utc_by_game)

    rows: list[dict[str, Any]] = []
    for row in features.to_dict("records"):
        pid = str(row.get("player_id") or "")
        pname = str(row.get("player_name") or pid)
        tm = str(row.get("team") or "").strip().upper()
        gid = str(row.get("game_id") or "")
        gdate = str(row.get("game_date") or "")[:10]
        pnorm = om.normalize_name(pname)

        is_inactive = (pid in inact_ids) or ((pnorm, tm) in inact_name_team)

        for fam, mdl in models.items():
            quote = _lookup_line(lines_by_player, pid, pname, fam)
            if quote is None:
                continue
            line_raw = quote.get("line")
            p_mkt_raw = quote.get("p_market") if quote.get("p_market") is not None else quote.get("p_over_market")
            if line_raw is None or p_mkt_raw is None:
                continue
            line = float(line_raw)
            p_market = float(p_mkt_raw)

            if is_inactive:
                # Inactive player: p_model mass moved to inactive handling (0.0 probability of over)
                p_model = 0.0
            else:
                pmf = mdl.pmf(row)
                p_model = float(pmf_p_over(pmf, line))

            c_val = float(dp.blend_c("nfl", fam))
            p_served = float(dp.blend_p_over(p_model, p_market, sport="nfl", stat=fam))

            rows.append({
                "sport": "nfl",
                "player_id": pid,
                "player": pname,
                "team": tm,
                "opponent": str(row.get("opponent") or ""),
                "game_id": gid,
                "game_date": gdate,
                "family": fam,
                "stat": fam,
                "line": line,
                "p_model": p_model,
                "p_market": p_market,
                "p_served": p_served,
                "blend_c": c_val,
                "engine": ENGINE_ID,
                "run_kind": run_kind,
                "inactive_scratch": bool(is_inactive),
                "gradeable": not bool(is_inactive),
            })

    return rows


def shadow_key(slate_date: str, run_kind: str) -> str:
    if run_kind not in RUN_KINDS:
        raise ValueError(f"Unknown run_kind {run_kind!r}; expected one of {RUN_KINDS}")
    return f"NFL/mod04e/shadow/{str(slate_date)[:10]}/{run_kind}.json"


def write_shadow(
    rows: list[dict[str, Any]],
    root: Optional[str | Path],
    slate_date: str,
    run_kind: str,
) -> str:
    """Write shadow predictions JSON to NFL/mod04e/shadow/<date>/<run_kind>.json."""
    rel = shadow_key(slate_date, run_kind)
    payload = {
        "engine": ENGINE_ID,
        "slate_date": str(slate_date)[:10],
        "run_kind": run_kind,
        "count": len(rows),
        "rows": rows,
    }
    raw = json.dumps(payload, indent=2).encode("utf-8")

    if root is None or str(root).startswith("gs://"):
        storage.write_bytes(raw, rel)
        return rel

    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return str(path)
