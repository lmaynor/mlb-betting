"""scripts/novig_live_maker.py -- MAK-03 Novig live maker runner (dry-run replay + live).

Safety invariants:
- `dry-run` is 100% offline: it constructs NO `NovigClient` and reads NO credentials.
- `live` refuses with exit code 2 unless ALL preconditions hold in order:
  1. `NOVIG_LIVE_MAKER_ENABLE=1`
  2. `NOVIG_LIVE_MAKER_LIVE=1`
  3. `NOVIG_PROD_ARMED=1`
  4. `NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD` set and parses to a float > 0
  5. Flag store readable (`GcsFlagSource` or injected flag source)
  6. `STOP` flag absent
  7. `ENABLE` flag present
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Mapping

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev.novig import exec as nx  # noqa: E402
from ev.novig import maker, maker_bank, maker_runner, nba_snapshot, nhl_snapshot, shadow_job  # noqa: E402
from ev.novig.client import PROD_ARM_ENV, PUBLIC_ENV, NovigClient  # noqa: E402
from ev.novig.live_maker import (  # noqa: E402
    ENABLE_ENV,
    LIVE_ENV,
    CandidateView,
    DecisionLog,
    GcsFlagSource,
    LiveMakerConfig,
    LiveMakerVenue,
    NovigLiveMaker,
    assign_rung,
    build_prod_client,
    build_readonly_client,
    evaluate_quote,
    load_live_maker_config,
)
from ev.novig.paper_orders import leg_key  # noqa: E402
from ev.novig.shadow_bank import DirStore  # noqa: E402
from mlb_core import storage  # noqa: E402

log = logging.getLogger("novig_live_maker")


def check_live_preconditions(
    *,
    env: Mapping[str, str] | None = None,
    flag_source: Any | None = None,
) -> str | None:
    """Return None if all live preconditions hold, else an exact missing precondition."""
    environ = os.environ if env is None else env
    if environ.get(ENABLE_ENV) != "1":
        return f"missing precondition: {ENABLE_ENV}=1"
    if environ.get(LIVE_ENV) != "1":
        return f"missing precondition: {LIVE_ENV}=1"
    if environ.get(PROD_ARM_ENV) != "1":
        return f"missing precondition: {PROD_ARM_ENV}=1"

    raw_cap = environ.get("NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD")
    if raw_cap in (None, ""):
        return "missing precondition: NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD"
    try:
        cap_val = float(raw_cap)
    except (TypeError, ValueError):
        return "missing precondition: NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD must be a float > 0"
    if cap_val <= 0.0:
        return "missing precondition: NOVIG_LIVE_MAKER_AGGREGATE_CAP_USD must be > 0"

    fs = flag_source or GcsFlagSource()
    enabled, stopped, readable = fs.flags()
    if not readable:
        return "missing precondition: flag store unreadable"
    if stopped:
        return "missing precondition: STOP flag is set"
    if not enabled:
        return "missing precondition: ENABLE flag is not set"
    return None


def _read_prod_credentials(env: Mapping[str, str] | None = None) -> tuple[str, bytes]:
    environ = os.environ if env is None else env
    key_id = environ.get("NOVIG_LIVE_KEY_ID") or ""
    key_path = environ.get("NOVIG_LIVE_KEY_PATH") or ""
    if not key_id or not key_path:
        raise RuntimeError("missing credentials: NOVIG_LIVE_KEY_ID and NOVIG_LIVE_KEY_PATH are required")
    with open(os.path.expanduser(key_path), "rb") as fh:
        return key_id, fh.read()


def _parse_balance_response(data: Mapping[str, Any]) -> float:
    if not isinstance(data, Mapping):
        raise RuntimeError(f"unparseable balance response: expected dict, got {type(data).__name__}")
    for key in ("balance", "available", "cash"):
        val = data.get(key)
        if val is not None and val != "":
            try:
                return float(val)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"unparseable balance field {key}={val!r}") from exc
    cents = data.get("balanceCents")
    if cents is not None and cents != "":
        try:
            return float(cents) / 100.0
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"unparseable balanceCents={cents!r}") from exc
    raise RuntimeError(f"unparseable balance response (missing balance/available/cash/balanceCents): {dict(data)!r}")


def run_dry_run(
    *,
    bank_dir: str,
    balance: float = 250.00,
    cfg: LiveMakerConfig | None = None,
    out_path: str | None = None,
) -> dict:
    """Offline replay of banked NOV-03 quote states through the live-maker gate pipeline.

    Zero network calls, zero NovigClient construction, zero credential reads.
    """
    if cfg is None:
        cfg = LiveMakerConfig(aggregate_cap_usd=float(balance))
    elif cfg.aggregate_cap_usd is None:
        cfg = LiveMakerConfig(
            per_quote_cap_usd=cfg.per_quote_cap_usd,
            quote_liability_usd=cfg.quote_liability_usd,
            aggregate_cap_usd=float(balance),
            rung_offsets_c=cfg.rung_offsets_c,
            rung_allocations=cfg.rung_allocations,
            clean_arm_disp_max=cfg.clean_arm_disp_max,
            poll_interval_sec=cfg.poll_interval_sec,
            settlement_sweep_sec=cfg.settlement_sweep_sec,
            boxscores_csv=cfg.boxscores_csv,
        )

    store = DirStore(bank_dir)
    states = maker_bank.load_quote_states(store)
    ordered = sorted(states.values(), key=lambda s: str(s.get("t_post") or ""))

    committed = 0.0
    states_seen = 0
    would_quote_count = 0
    would_quote_by_rung: dict[str, int] = {}
    total_cost_would_commit = 0.0
    per_quote_cost_first_20: list[float] = []
    skips_by_reason: dict[str, int] = {}

    for st in ordered:
        states_seen += 1
        game_key = str(st.get("game_key") or "")
        sport = str(st.get("sport") or "").upper()
        stat = str(st.get("stat") or "")
        side = str(st.get("side") or "yes")
        p_cons_side = float(st["p_cons_at_post"])
        bid_side = float(st["novig_bid_at_post"]) if st.get("novig_bid_at_post") is not None else None
        ask_side = float(st["novig_ask_at_post"]) if st.get("novig_ask_at_post") is not None else None
        ff = float(st["fair_floor"]) if st.get("fair_floor") is not None else None
        dc = float(st["delta"]) if st.get("delta") is not None else None

        rung = assign_rung(game_key, sport, stat, cfg)
        view = CandidateView(
            game_key=game_key,
            league=sport,
            stat=stat,
            side=side,
            mode="MAKER_PAPER",
            p_cons_side=p_cons_side,
            fair_floor=ff,
            delta_control=dc,
            disp=0.0,  # dispersion is not banked in Novig/maker_study; clean arms cannot pause in replay
            novig_bid_side=bid_side,
            novig_ask_side=ask_side,
            rung=rung,
        )
        ev = evaluate_quote(view, cfg=cfg, balance=float(balance), committed=committed)
        if ev.outcome == "QUOTED":
            would_quote_count += 1
            would_quote_by_rung[ev.rung] = would_quote_by_rung.get(ev.rung, 0) + 1
            total_cost_would_commit += ev.cost
            committed = ev.committed_after
            if len(per_quote_cost_first_20) < 20:
                per_quote_cost_first_20.append(round(ev.cost, 6))
        else:
            skips_by_reason[ev.reason] = skips_by_reason.get(ev.reason, 0) + 1

    summary = {
        "exposure_model": (
            "worst_case_committed_to_settlement "
            "(every WOULD_QUOTE commits cost for remainder of replay; balance fixed)"
        ),
        "bank_layout": f"{bank_dir}/{maker_bank.PREFIX}/{{YYYY-MM-DD}}/*.jsonl.gz",
        "balance": float(balance),
        "aggregate_cap": cfg.aggregate_cap_usd,
        "states_seen": states_seen,
        "would_quote_count": would_quote_count,
        "would_quote_by_rung": dict(sorted(would_quote_by_rung.items())),
        "total_cost_would_commit": round(total_cost_would_commit, 6),
        "committed_final": round(committed, 6),
        "per_quote_cost_first_20": per_quote_cost_first_20,
        "skips_by_reason": dict(sorted(skips_by_reason.items())),
        "zero_network": True,
        "assumptions": [
            (
                "disp=0.0 for all banked states because cross-book dispersion is not banked in "
                "Novig/maker_study; clean arms therefore cannot trigger DISPERSION_PAUSE in replay "
                "(upper-bound bias on would_quote_count)."
            ),
            "All banked quote states are evaluated with mode='MAKER_PAPER' because NOV-03 only banked posted quotes.",
            "Worst-case exposure accounting: committed capital is not released on simulated fills or settlements.",
        ],
    }

    if out_path:
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return summary


def _build_states_for_open_orders(
    quoter: NovigLiveMaker,
    assembled: list[maker_runner.AssembledCandidate],
) -> dict[str, dict[str, Any]]:
    cands_by_leg: dict[tuple, maker_runner.AssembledCandidate] = {
        leg_key(ac.leg): ac for ac in assembled
    }
    states: dict[str, dict[str, Any]] = {}
    for order_id, meta in list(quoter._open_meta.items()):
        if meta.get("terminal"):
            continue
        ac = cands_by_leg.get(meta.get("leg_key"))
        if ac is None:
            continue
        side = meta["side"]
        p_cons_side_now = ac.candidate.p_cons if side == "yes" else (1.0 - ac.candidate.p_cons)
        sharp_now: dict[str, float] = {}
        for b in maker.SHARP_BOOKS:
            if b in ac.book_fair:
                f_over = float(ac.book_fair[b])
                sharp_now[b] = f_over if side == "yes" else (1.0 - f_over)
        states[order_id] = {
            "p_cons_side_now": p_cons_side_now,
            "sharp_now": sharp_now,
            "n_books_now": ac.candidate.n_books,
            "snapshot_age_sec_now": ac.snapshot_age_sec,
        }
    return states


def run_live(
    args,
    *,
    env: Mapping[str, str] | None = None,
    flag_source: Any | None = None,
) -> int:
    missing = check_live_preconditions(env=env, flag_source=flag_source)
    if missing is not None:
        print(missing, file=sys.stderr)
        return 2

    key_id, pem = _read_prod_credentials(env=env)
    ro_client = build_readonly_client(key_id, pem)
    prod_client = build_prod_client(key_id, pem, env=env)
    venue = LiveMakerVenue(prod_client)
    public_nc = NovigClient(PUBLIC_ENV)

    def _balance_provider() -> float:
        return _parse_balance_response(ro_client.balance())

    base_cfg = load_live_maker_config(env=env)
    cfg = LiveMakerConfig(
        per_quote_cap_usd=base_cfg.per_quote_cap_usd,
        quote_liability_usd=base_cfg.quote_liability_usd,
        aggregate_cap_usd=base_cfg.aggregate_cap_usd,
        rung_offsets_c=base_cfg.rung_offsets_c,
        rung_allocations=base_cfg.rung_allocations,
        clean_arm_disp_max=base_cfg.clean_arm_disp_max,
        poll_interval_sec=float(getattr(args, "poll_sec", base_cfg.poll_interval_sec)),
        settlement_sweep_sec=base_cfg.settlement_sweep_sec,
        boxscores_csv=getattr(args, "boxscores_csv", None) or base_cfg.boxscores_csv,
    )

    fs = flag_source or GcsFlagSource()
    decision_log = DecisionLog(path=args.decision_log, storage_prefix=args.decision_log_prefix)
    quoter = NovigLiveMaker(
        venue=venue,
        flag_source=fs,
        balance_provider=_balance_provider,
        decision_log=decision_log,
        cfg=cfg,
        env=env,
    )
    quoter.start()

    stop_requested = False

    def _handle_sig(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_sig)
        except (ValueError, OSError):
            pass

    def _read_goalies():
        key = maker_runner.GOALIE_CACHE_KEY
        return storage.read_bytes(key) if storage.exists(key) else None

    def _read_nba_injuries():
        ptr_key = maker_runner.NBA_INJURY_LATEST
        ptr_b = storage.read_bytes(ptr_key) if storage.exists(ptr_key) else None
        csv_fn = lambda k: storage.read_bytes(k) if k and storage.exists(k) else None
        return ptr_b, csv_fn

    enabled_leagues = [x.strip().upper() for x in args.leagues.split(",") if x.strip()]
    last_legs_refresh: dict[str, float] = {}
    prod_legs: dict[str, dict] = {}
    start_ts = time.time()
    last_sweep_ts = start_ts

    try:
        while not stop_requested:
            now_ts = time.time()
            max_runtime = float(getattr(args, "max_runtime_sec", 0.0) or 0.0)
            if max_runtime > 0.0 and (now_ts - start_ts) >= max_runtime:
                break
            now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)

            for lg in enabled_leagues:
                prev_t = last_legs_refresh.get(lg)
                if prev_t is None or (now_ts - prev_t) > 600.0:
                    try:
                        prod_legs[lg] = nx.load_novig_legs(public_nc, now_dt, lg)
                        last_legs_refresh[lg] = now_ts
                    except Exception as exc:  # noqa: BLE001
                        log.warning("leg loading failed for %s: %r", lg, exc)

            assembled: list[maker_runner.AssembledCandidate] = []
            for lg in enabled_leagues:
                legs = prod_legs.get(lg, {})
                try:
                    if lg == "NFL":
                        cands, _ = maker_runner.assemble_nfl(
                            shadow_job.load_snapshot, legs, now_dt, args.horizon_hours, public_nc, args.pace_s, time.sleep
                        )
                        assembled.extend(cands)
                    elif lg == "NHL":
                        cands, _ = maker_runner.assemble_nhl(
                            nhl_snapshot.read_latest, _read_goalies, legs, now_dt, args.horizon_hours, public_nc, args.pace_s, time.sleep
                        )
                        assembled.extend(cands)
                    elif lg == "NBA":
                        cands, _ = maker_runner.assemble_nba(
                            nba_snapshot.read_latest, _read_nba_injuries, legs, now_dt, args.horizon_hours, public_nc, args.pace_s, time.sleep
                        )
                        assembled.extend(cands)
                except Exception as exc:  # noqa: BLE001
                    log.warning("candidate assembly failed for %s: %r", lg, exc)

            for ac in assembled:
                quoter.on_candidate(ac.candidate, ac.leg, now=now_ts, book_fair=ac.book_fair)

            states = _build_states_for_open_orders(quoter, assembled)
            quoter.poll_open_orders(states, now=now_ts)
            quoter.sync_fills()

            if (now_ts - last_sweep_ts) >= cfg.settlement_sweep_sec:
                quoter.sweep_settlements()
                last_sweep_ts = now_ts

            decision_log.flush()
            if stop_requested:
                break
            time.sleep(cfg.poll_interval_sec)
    finally:
        quoter.cancel_all_open("SHUTDOWN")
        decision_log.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="MAK-03 Novig live maker runner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    dry = sub.add_parser("dry-run", help="Offline replay of banked NOV-03 quote states")
    dry.add_argument("--bank-dir", required=True, help="Local mirror of the Novig/maker_study GCS prefix")
    dry.add_argument("--balance", type=float, default=250.00)
    dry.add_argument("--aggregate-cap", type=float, default=None)
    dry.add_argument("--per-quote-cap", type=float, default=5.00)
    dry.add_argument("--quote-liability", type=float, default=5.00)
    dry.add_argument("--out", default=None)

    live = sub.add_parser("live", help="Live production quoting (refuses unless all arm/enable preconditions hold)")
    live.add_argument("--leagues", default="NFL,NHL,NBA")
    live.add_argument("--horizon-hours", type=float, default=30.0)
    live.add_argument("--poll-sec", type=float, default=30.0)
    live.add_argument("--pace-s", type=float, default=0.2)
    live.add_argument("--max-runtime-sec", type=float, default=0.0)
    live.add_argument("--decision-log", default="data/novig_live_maker/decisions.jsonl")
    live.add_argument("--decision-log-prefix", default="Novig/live_maker/decisions")
    live.add_argument("--boxscores-csv", default=None)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.cmd == "dry-run":
        agg = float(args.balance) if args.aggregate_cap is None else float(args.aggregate_cap)
        cfg = LiveMakerConfig(
            per_quote_cap_usd=float(args.per_quote_cap),
            quote_liability_usd=float(args.quote_liability),
            aggregate_cap_usd=agg,
        )
        summary = run_dry_run(
            bank_dir=args.bank_dir,
            balance=float(args.balance),
            cfg=cfg,
            out_path=args.out,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if args.cmd == "live":
        return run_live(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
