#!/usr/bin/env python3
"""Guarded CLI for the NBC Sports NBA historic prose puller (nba/data/nbcsports_archive.py).

DISABLED BY DEFAULT. Without BOTH gates open this script makes ZERO HTTP requests and
ZERO storage writes:
  * no --execute flag            -> prints the dry-run plan and exits 0
  * --execute but the env flag   -> prints a refusal and exits 2
    NBCSPORTS_PULL_ENABLED is not "1"

The exact trigger command for a run (only on Luc's authorization):

    NBCSPORTS_PULL_ENABLED=1 python3 scripts/pull_nbcsports_prose.py --execute \
        --players-csv <player map CSV/JSON: player, nbc_id, slug> \
        --cases <cases JSON: the validated case population> \
        [--delay 2.5] [--jitter 0.5]

The case population is the validated team-calendar set (1,318 cases / 417 players,
source-independent); cases are loaded from JSON rather than rebuilt here so the pull
consumes exactly the validated population.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nba.data import nbcsports_archive as nb  # noqa: E402


def _load_players(source) -> list:
    if hasattr(source, "read"):
        text = source.read()
    else:
        text = Path(source).read_text(encoding="utf-8")
    rows = json.loads(text) if text.lstrip().startswith("[") else list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        try:
            nid = str(r.get("nbc_id")).strip()  # numeric string or Brightspot UUID
        except (TypeError, ValueError):
            logging.warning("player map: skipping %r (no nbc_id)", r.get("player"))
            continue
        name = str(r.get("player") or r.get("name") or "").strip()
        out.append(nb.PlayerRef(player=name, nbc_id=nid,
                                slug=str(r.get("slug") or nb.slugify(name))))
    return out


def _load_cases(source) -> list:
    text = source.read() if hasattr(source, "read") else Path(source).read_text(encoding="utf-8")
    return json.loads(text)


def main(argv=None, session=None, env=None, store=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--execute", action="store_true",
                    help="actually run (ALSO requires env NBCSPORTS_PULL_ENABLED=1; without it, refused)")
    ap.add_argument("--players-csv", help="player map CSV/JSON: player, nbc_id, slug")
    ap.add_argument("--cases", help="cases JSON (validated population: player, return_date, window_start, ...)")
    ap.add_argument("--delay", type=float, default=nb.DEFAULT_DELAY_S,
                    help="seconds between requests (>= 2.5 polite default)")
    ap.add_argument("--jitter", type=float, default=0.0,
                    help="extra uniform jitter seconds on top of --delay")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    store = nb.storage if store is None else store
    effective_env = dict(os.environ) if env is None else env
    if args.execute and str(effective_env.get(nb.ENABLE_ENV, "")).strip() != "1":
        print(json.dumps({"status": "refused",
                          "note": f"--execute given but {nb.ENABLE_ENV} is not '1'; zero requests made"}))
        return 2

    players = _load_players(args.players_csv) if args.players_csv else []
    cases = _load_cases(args.cases) if args.cases else []
    res = nb.run_historic_pull(players, cases, session=session, execute=args.execute,
                               env=effective_env, delay=args.delay, jitter=args.jitter,
                               store=store)
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
