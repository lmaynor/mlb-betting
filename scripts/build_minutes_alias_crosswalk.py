#!/usr/bin/env python3
"""scripts/build_minutes_alias_crosswalk.py -- build the MIN-06 alias crosswalk + audit from the
program's real name sources, offline.

WHAT IT DOES: loads the canonical roster (hoopR box-score mirror: ESPN athlete ids, display
names, team history), then resolves every distinct player name the minutes program actually
carries -- the NBC and RotoWire player-map names, the official injury reports' player column,
and the banked prose corpora's page/subject names -- through
nba.minutes_restriction.aliases.AliasResolver, and writes:

  crosswalk.csv   one row per (source, source_id, source_name): status, method, player_id,
                  canonical display name, candidate ids when ambiguous. Source ids come from
                  the player maps where the source has them (nbc, rotowire); official/prose
                  rows carry an empty source_id.
  alias_audit.json  per-source counts by status/method + the full ambiguous and unknown lists
                  (the human work queue: ambiguous = pick by hand with the candidates shown;
                  unknown = listed by a source but no roster identity exists -- never invent).

FAIL CLOSED is the point: unresolved names are REPORTED here, never guess-merged into the
crosswalk. The crosswalk is an artifact for review and downstream joins; this script writes
only to --out (a local directory). It makes no network calls and no storage writes.

Defaults point at the program's local mirrors as of 2026-11-13; every input is overridable.

Usage:
    python3 scripts/build_minutes_alias_crosswalk.py --out /tmp/alias-crosswalk
    python3 scripts/build_minutes_alias_crosswalk.py --box-dir ~/workspace/minutes-run-data/hoopr \
        --reports-dir ~/workspace/minutes-run-data/reports --out ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # runnable as `python scripts/...`

import pandas as pd

from nba.minutes_restriction.aliases import AliasResolver, RosterIndex

HOME = Path.home() / "workspace"


def _load_box(box_dir: str) -> pd.DataFrame:
    files = sorted(Path(box_dir).glob("player_boxscores/*.parquet"))
    if not files:
        raise ValueError(f"no player_boxscores parquet under {box_dir}")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def _load_map(path: str) -> dict:
    df = pd.read_csv(path, dtype=str)
    id_col = next(c for c in df.columns if c.endswith("_id"))
    return {str(i): str(p) for p, i in zip(df["player"], df[id_col])}


def _report_names(reports_dir: str) -> list:
    names = set()
    for f in Path(reports_dir).glob("*/report_*.csv"):
        names |= set(pd.read_csv(f, usecols=["player"])["player"].dropna().astype(str))
    return sorted(names)


def _corpus_names(parsed_dir: str) -> list:
    names = set()
    for f in Path(parsed_dir).glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            for k in ("player", "subject_player"):
                if rec.get(k):
                    names.add(str(rec[k]))
    return sorted(names)


def build(box_dir: str, nbc_map: str, rotowire_map: str, reports_dir: str,
          nbc_parsed: str, rotowire_parsed: str) -> tuple:
    """-> (crosswalk DataFrame, audit dict). Pure file reading + resolution; no writes."""
    roster = RosterIndex.from_box(_load_box(box_dir))
    maps = {"nbc": _load_map(nbc_map), "rotowire": _load_map(rotowire_map)}
    resolver = AliasResolver(roster, source_maps=maps)

    rows = []
    names_by_source: dict = {}
    for source, m in maps.items():
        names_by_source[source] = sorted(set(m.values()))
        for sid, name in sorted(m.items()):
            r = resolver.resolve_source_id(source, sid)
            rows.append({"source": source, "source_id": sid, "source_name": name,
                         "status": r.status, "method": r.method, "player_id": r.player_id,
                         "canonical_name": r.display_name,
                         "candidates": ";".join(str(c.player_id) for c in r.candidates)})
    names_by_source["official"] = _report_names(reports_dir)
    names_by_source["nbc_prose"] = _corpus_names(nbc_parsed)
    names_by_source["rotowire_prose"] = _corpus_names(rotowire_parsed)
    for source in ("official", "nbc_prose", "rotowire_prose"):
        for name in names_by_source[source]:
            r = resolver.resolve(name)
            rows.append({"source": source, "source_id": "", "source_name": name,
                         "status": r.status, "method": r.method, "player_id": r.player_id,
                         "canonical_name": r.display_name,
                         "candidates": ";".join(str(c.player_id) for c in r.candidates)})
    audit = resolver.audit({k: v for k, v in names_by_source.items() if k in
                            ("official", "nbc_prose", "rotowire_prose")})
    for source, m in maps.items():
        sub = [r for r in rows if r["source"] == source]
        audit["sources"][source] = {
            "n_names": len(sub),
            "resolved": sum(1 for r in sub if r["status"] == "resolved"),
            "ambiguous": sum(1 for r in sub if r["status"] == "ambiguous"),
            "unknown": sum(1 for r in sub if r["status"] == "unknown")}
    audit["roster_identities"] = len(roster.identities)
    return pd.DataFrame(rows), audit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--box-dir", default=str(HOME / "minutes-run-data/hoopr"))
    ap.add_argument("--nbc-map", default=str(HOME / "beezy/nbcsports-evidence/nbc_player_map.csv"))
    ap.add_argument("--rotowire-map", default=str(HOME / "beezy/rotowire-fix-evidence/player_map.csv"))
    ap.add_argument("--reports-dir", default=str(HOME / "minutes-run-data/reports"))
    ap.add_argument("--nbc-parsed", default=str(HOME / "arm-t-smoke-data/nbc/parsed"))
    ap.add_argument("--rotowire-parsed", default=str(HOME / "arm-t-smoke-data/rotowire/parsed"))
    ap.add_argument("--out", required=True, help="output directory (created)")
    a = ap.parse_args(argv)
    crosswalk, audit = build(a.box_dir, a.nbc_map, a.rotowire_map, a.reports_dir,
                             a.nbc_parsed, a.rotowire_parsed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    crosswalk.to_csv(out / "crosswalk.csv", index=False)
    (out / "alias_audit.json").write_text(json.dumps(audit, indent=1, sort_keys=True))
    for source, counts in sorted(audit["sources"].items()):
        print(source, counts)
    print(f"roster identities: {audit['roster_identities']}; "
          f"ambiguous: {len(audit['ambiguous'])}; unknown: {len(audit['unknown'])}")
    print(f"wrote {out / 'crosswalk.csv'} ({len(crosswalk)} rows) and {out / 'alias_audit.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
