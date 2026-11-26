"""scripts/kalshi_rfq_fill_test.py -- MAK-02 $21 RFQ fill-test runner (dry-run replay + live).

`dry-run` replays banked RFQ tape chunks offline against a saved ESPN scoreboard JSON
with worst-case exposure accounting (every QUOTED decision commits its collateral for the
remainder of the replay).

`live` requires BOTH KALSHI_RFQ_FILL_TEST_ENABLE=1 AND KALSHI_RFQ_LIVE=1 AND the GCS
ENABLE flag (with STOP absent and flags readable), plus KALSHI_PROD_QUOTES_ARMED=1 in
KalshiClient, before touching any network client.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import signal
import sys
import time
from dataclasses import replace
from typing import Mapping

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev.kalshi_rfq_pricer import InMemoryFairBook, KalshiRFQPricer, PricerConfig  # noqa: E402
from mlb.execution.kalshi.client import PROD_QUOTES_ENV, load_private_key  # noqa: E402
from mlb.execution.kalshi.espn_tipoff import (  # noqa: E402
    EspnGameStatePoller,
    GameStateSnapshot,
    TipoffGate,
    parse_scoreboard,
)
from mlb.execution.kalshi.rfq_client import KalshiRFQStream  # noqa: E402
from mlb.execution.kalshi.rfq_fill_test import (  # noqa: E402
    ENABLE_ENV,
    DecisionLog,
    FillTestConfig,
    FillTestQuoter,
    FireView,
    GcsFlagSource,
    build_prod_client,
    evaluate_fire,
)
from mlb.execution.kalshi.rfq_maker import (  # noqa: E402
    LIVE_ENV_FLAG,
    RFQConfig,
    RFQLedger,
    RFQMaker,
    RFQRiskGate,
)
from scripts.kalshi_rfq_daemon import FeedState, banked_sports_for, make_game_client, split_sports  # noqa: E402

log = logging.getLogger("kalshi_rfq_fill_test")

# Replay assumption (named): a game counts as IN PLAY from its ESPN start time for
# LIVE_WINDOW_SEC (4h, generous for an NFL game incl. overtime/delays); after that it
# counts as final ("post"). Used only to derive per-record gate state in dry-run.
LIVE_WINDOW_SEC = 4 * 3600.0


def _infer_sport(payload: dict, default: str = "nfl") -> str:
    leagues = payload.get("leagues") or []
    if leagues and isinstance(leagues[0], dict):
        slug = leagues[0].get("slug") or leagues[0].get("abbreviation")
        if slug:
            return str(slug).lower()
    return default


def _view_from_tape_record(rec: dict) -> FireView:
    no_bid = rec.get("no_bid")
    no_bid_f = float(no_bid) if no_bid is not None else None
    contracts = rec.get("contracts_fp_num")
    if contracts is not None:
        contracts_f: float | None = float(contracts)
    else:
        tcost = rec.get("target_cost_dollars_num")
        if tcost is not None and no_bid_f is not None and (1.0 - no_bid_f) > 0:
            contracts_f = float(tcost) / (1.0 - no_bid_f)
        else:
            contracts_f = None
    collateral = (contracts_f * no_bid_f) if (contracts_f is not None and no_bid_f is not None) else None
    game_keys = tuple(
        sorted({str(leg["game_key"]) for leg in (rec.get("legs") or ()) if isinstance(leg, dict) and leg.get("game_key")})
    )
    p_yes_true = rec.get("p_yes_true")
    taper = rec.get("taper_weight")
    return FireView(
        rfq_id=str(rec.get("rfq_id") or ""),
        primary_class=str(rec.get("primary_class") or ""),
        disposition=str(rec.get("disposition") or ""),
        fire_tier=rec.get("fire_tier"),
        taper_weight=float(taper) if taper is not None else None,
        flag_names=tuple(rec.get("flag_names") or ()),
        collateral=collateral,
        contracts=contracts_f,
        no_bid=no_bid_f,
        p_yes_true=float(p_yes_true) if p_yes_true is not None else None,
        game_keys=game_keys,
        ts=float(rec.get("ts") or 0.0),
    )


def run_dry_run(
    *,
    tape_dir: str,
    from_ts: float,
    balance: float,
    espn_json_path: str,
    out_path: str | None = None,
    sport: str | None = None,
    cfg: FillTestConfig = FillTestConfig(),
) -> dict:
    with open(espn_json_path, "r", encoding="utf-8") as fh:
        espn_payload = json.load(fh)
    resolved_sport = (sport or _infer_sport(espn_payload)).lower()
    base_games = parse_scoreboard(espn_payload, resolved_sport)
    gate = TipoffGate(buffer_sec=cfg.tipoff_buffer_sec, max_snapshot_age_sec=cfg.snapshot_max_age_sec)

    cap_limit = min(float(balance), cfg.aggregate_cap_abs)
    committed = 0.0
    records_seen = 0
    admitted_fires = 0
    quoted_count = 0
    total_collateral_quoted = 0.0
    per_quote_collateral: list[float] = []
    cap_reached_at: dict | None = None
    first_aggregate_cap_skip_at: dict | None = None
    skips_by_reason: dict[str, int] = {}

    non_denom_quoted = 0
    bounded_over_cap_quoted = 0
    stale_zero_size_quoted = 0
    preview_quoted = 0
    tipoff_gated_admitted_fires = 0
    tipoff_gated_quoted = 0
    blocked_live_game_admitted_fires = 0
    blocked_live_game_quoted = 0

    files = sorted(glob.glob(os.path.join(tape_dir, "*.jsonl")))
    for path in files:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("kind") != "rfq":
                    continue
                rec_ts = float(rec.get("ts") or 0.0)
                if rec_ts < from_ts:
                    continue

                records_seen += 1
                view = _view_from_tape_record(rec)
                is_admitted = view.primary_class == "DENOMINATOR" and view.disposition == "ADMITTED_FIRE"
                if is_admitted:
                    admitted_fires += 1

                def _state_at(gs, _ts=rec_ts):
                    if _ts < gs.start_ts:
                        return "pre"
                    if _ts < gs.start_ts + LIVE_WINDOW_SEC:
                        return "in"
                    return "post"

                games_at_ts = {
                    k: replace(gs, state=_state_at(gs))
                    for k, gs in base_games.items()
                }
                snapshot = GameStateSnapshot(games=games_at_ts, fetched_at=rec_ts, source="espn_replay")
                gate_ok, gate_reason = gate.check(view.game_keys, rec_ts, snapshot)

                ev = evaluate_fire(
                    view,
                    cfg=cfg,
                    balance=balance,
                    committed=committed,
                    gate_ok=gate_ok,
                    gate_reason=gate_reason,
                )
                is_preview = bool(rec.get("preview_10")) or ("PREVIEW_10" in view.flag_names)
                is_tipoff_skip = ev.outcome == "SKIPPED" and ev.reason in (
                    "GATE_SNAPSHOT_STALE",
                    "GATE_UNKNOWN_GAME",
                    "GATE_STARTED",
                    "GATE_INSIDE_BUFFER",
                    "BLOCKED_LIVE_GAME",
                )
                if is_admitted and not gate_ok:
                    tipoff_gated_admitted_fires += 1
                # Live-blocker count is computed directly from game state at fire time
                # (independent of the cap-first evaluation order): an otherwise-admitted
                # fire whose legs' games had already started counts here.
                if is_admitted and any(
                    games_at_ts.get(k) is not None and games_at_ts[k].state == "in"
                    for k in view.game_keys
                ):
                    blocked_live_game_admitted_fires += 1

                if ev.outcome == "QUOTED":
                    col = float(ev.collateral or 0.0)
                    quoted_count += 1
                    total_collateral_quoted += col
                    committed = ev.committed_after
                    if len(per_quote_collateral) < 20:
                        per_quote_collateral.append(round(col, 6))
                    if cap_reached_at is None and committed >= cap_limit - 1e-9:
                        cap_reached_at = {
                            "ts": rec_ts,
                            "rfq_id": view.rfq_id,
                            "committed": round(committed, 6),
                        }
                    if view.primary_class != "DENOMINATOR":
                        non_denom_quoted += 1
                    if view.disposition == "FIRE_BOUNDED_OVER_CAP":
                        bounded_over_cap_quoted += 1
                    if view.disposition == "FIRE_STALE_ZERO_SIZE":
                        stale_zero_size_quoted += 1
                    if is_preview:
                        preview_quoted += 1
                    if not gate_ok:
                        tipoff_gated_quoted += 1
                    if any(
                        games_at_ts.get(k) is not None and games_at_ts[k].state == "in"
                        for k in view.game_keys
                    ):
                        blocked_live_game_quoted += 1
                else:
                    skips_by_reason[ev.reason] = skips_by_reason.get(ev.reason, 0) + 1
                    if ev.reason == "AGGREGATE_CAP" and first_aggregate_cap_skip_at is None:
                        first_aggregate_cap_skip_at = {
                            "ts": rec_ts,
                            "rfq_id": view.rfq_id,
                            "committed": round(committed, 6),
                        }
                    if is_tipoff_skip and ev.outcome == "QUOTED":
                        tipoff_gated_quoted += 1

    summary = {
        "exposure_model": "worst_case_committed_to_settlement (every QUOTED commits collateral for remainder of replay; balance fixed at --balance)",
        "balance": float(balance),
        "from_ts": float(from_ts),
        "sport": resolved_sport,
        "records_seen": records_seen,
        "admitted_fires": admitted_fires,
        "quoted_count": quoted_count,
        "total_collateral_quoted": round(total_collateral_quoted, 6),
        "committed_final": round(committed, 6),
        "per_quote_collateral_first_20": per_quote_collateral,
        "cap_reached_at": cap_reached_at,
        "first_aggregate_cap_skip_at": first_aggregate_cap_skip_at,
        "skips_by_reason": dict(sorted(skips_by_reason.items())),
        "zero_quote_proofs": {
            "non_denominator_quoted": non_denom_quoted,
            "bounded_over_cap_quoted": bounded_over_cap_quoted,
            "stale_zero_size_quoted": stale_zero_size_quoted,
            "preview_quoted": preview_quoted,
            "tipoff_gated_admitted_fires": tipoff_gated_admitted_fires,
            "tipoff_gated_quoted": tipoff_gated_quoted,
            "blocked_live_game_admitted_fires": blocked_live_game_admitted_fires,
            "blocked_live_game_quoted": blocked_live_game_quoted,
            "live_window_sec_assumption": LIVE_WINDOW_SEC,
        },
    }
    if out_path:
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return summary


def check_live_preconditions(
    *,
    env: Mapping[str, str] | None = None,
    flag_source: GcsFlagSource | None = None,
) -> str | None:
    """Return None if all live preconditions hold, else an exact human-readable missing precondition."""
    environ = os.environ if env is None else env
    if environ.get(ENABLE_ENV) != "1":
        return f"missing precondition: {ENABLE_ENV}=1"
    if environ.get(LIVE_ENV_FLAG) != "1":
        return f"missing precondition: {LIVE_ENV_FLAG}=1"
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
    key_id = environ.get("KALSHI_PROD_KEY_ID") or ""
    key_path = environ.get("KALSHI_PROD_PRIVATE_KEY_PATH") or ""
    if not key_id or not key_path:
        raise RuntimeError("missing credentials: KALSHI_PROD_KEY_ID and KALSHI_PROD_PRIVATE_KEY_PATH are required")
    with open(os.path.expanduser(key_path), "rb") as fh:
        return key_id, fh.read()


async def _run_live_async(args) -> int:
    cfg = FillTestConfig()
    key_id, pem = _read_prod_credentials()
    client = build_prod_client(key_id, pem)
    flag_source = GcsFlagSource()

    leagues = split_sports(args.espn_leagues) or ("nfl",)
    poller = EspnGameStatePoller(leagues=leagues, poll_interval_sec=cfg.poll_interval_sec)
    poller.poll_once()
    poller.start()

    book = InMemoryFairBook()
    game_spec = args.game_sports
    game_client, game_sports = make_game_client(game_spec)
    banked_sports = banked_sports_for("auto", split_sports(game_spec))
    prop_sports = split_sports(args.prop_sports)
    feed = FeedState(
        book,
        args.feed,
        args.hot_games_file,
        game_client,
        game_sports,
        prop_sports,
        ("h2h", "spreads", "totals"),
        banked_sports=banked_sports,
    )
    for fn in (feed.refresh_props, feed.refresh_banked, feed.refresh_games):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("initial feed refresh failed: %r", exc)

    pricer = KalshiRFQPricer(book, PricerConfig())
    ledger = RFQLedger(args.db)
    risk_gate = RFQRiskGate(RFQConfig(), ledger, "live")
    maker = RFQMaker(pricer, risk_gate, ledger, client, mode="live")
    pricer.inventory_fn = maker.inventory

    def _read_balance() -> float:
        data = client.get_balance()
        return float(data.get("balance_dollars") or 0.0)

    decision_log = DecisionLog(path=args.decision_log, storage_prefix=args.decision_log_prefix)
    tipoff_gate = TipoffGate(buffer_sec=cfg.tipoff_buffer_sec, max_snapshot_age_sec=cfg.snapshot_max_age_sec)
    quoter = FillTestQuoter(
        pricer=pricer,
        client=client,
        ledger=ledger,
        maker=maker,
        gate=tipoff_gate,
        snapshot_provider=poller.snapshot,
        flag_source=flag_source,
        balance_provider=_read_balance,
        decision_log=decision_log,
        cfg=cfg,
    )
    quoter.start()

    async def on_event(typ, msg, ts):
        await asyncio.to_thread(quoter.on_event, typ, msg, ts)

    stream = KalshiRFQStream(PROD_QUOTES_ENV, key_id, load_private_key(pem), on_event)

    async def poll():
        while True:
            try:
                for rfq in await asyncio.to_thread(client.get_rfqs, "open"):
                    await on_event("rfq_created", rfq, time.time())
            except Exception as exc:  # noqa: BLE001
                log.warning("rfq poll failed: %r", exc)
            await asyncio.sleep(args.poll_sec)

    async def sweep():
        # Rollover: release settled fills and refresh the balance so the same
        # capital can work the next game's pregame window.
        while True:
            await asyncio.sleep(60.0)
            try:
                rows = await asyncio.to_thread(client.get_settlements)
                done = await asyncio.to_thread(quoter.sweep_settlements, rows)
                if done:
                    log.info("settlement sweep released %d quote(s): %s", len(done), done)
            except Exception as exc:  # noqa: BLE001
                log.warning("settlement sweep failed: %r", exc)

    async def reload_props():
        while True:
            await asyncio.sleep(args.reload_sec)
            try:
                await asyncio.to_thread(feed.refresh_props)
            except Exception as exc:  # noqa: BLE001
                log.warning("props reload failed: %r", exc)

    async def reload_banked():
        while banked_sports:
            await asyncio.sleep(args.banked_reload_sec)
            try:
                await asyncio.to_thread(feed.refresh_banked)
            except Exception as exc:  # noqa: BLE001
                log.warning("banked reload failed: %r", exc)
        await asyncio.Event().wait()

    async def deadline():
        if args.max_runtime_sec:
            await asyncio.sleep(args.max_runtime_sec)
        else:
            await asyncio.Event().wait()

    loop = asyncio.get_running_loop()
    main_task = asyncio.ensure_future(asyncio.gather(stream.run(), poll(), reload_props(), reload_banked(), sweep()))
    stop_task = asyncio.ensure_future(deadline())
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_task.cancel)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        done, _ = await asyncio.wait({main_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if main_task in done:
            main_task.result()
    finally:
        stream.stop()
        poller.stop()
        for t in (main_task, stop_task):
            t.cancel()
        await asyncio.gather(main_task, stop_task, return_exceptions=True)
        decision_log.flush()
        maker.cancel_all_open_quotes()
    return 0


def run_live(args) -> int:
    missing = check_live_preconditions()
    if missing is not None:
        print(missing, file=sys.stderr)
        return 2
    return asyncio.run(_run_live_async(args))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="MAK-02 $21 RFQ fill-test runner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    dry = sub.add_parser("dry-run", help="Offline tape replay against a saved ESPN scoreboard JSON")
    dry.add_argument("--tape-dir", required=True)
    dry.add_argument("--from-ts", type=float, default=0.0)
    dry.add_argument("--balance", type=float, default=21.00)
    dry.add_argument("--espn-json", required=True)
    dry.add_argument("--sport", default=None)
    dry.add_argument("--out", default=None)

    live = sub.add_parser("live", help="Live production quoting (refuses unless all arm/enable preconditions hold)")
    live.add_argument("--feed", default="EVSweep/nfl_props/latest.json")
    live.add_argument("--hot-games-file", default=None)
    live.add_argument("--db", default=None)
    live.add_argument("--decision-log", default="data/kalshi_rfq/fill_test_decisions.jsonl")
    live.add_argument("--decision-log-prefix", default=None)
    live.add_argument("--espn-leagues", default="nfl,nba,nhl")
    live.add_argument("--game-sports", default="nfl,nba,nhl")
    live.add_argument("--prop-sports", default="nfl,nba,nhl")
    live.add_argument("--poll-sec", type=float, default=5.0)
    live.add_argument("--reload-sec", type=float, default=60.0)
    live.add_argument("--banked-reload-sec", type=float, default=300.0)
    live.add_argument("--max-runtime-sec", type=float, default=0.0)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.cmd == "dry-run":
        summary = run_dry_run(
            tape_dir=args.tape_dir,
            from_ts=args.from_ts,
            balance=args.balance,
            espn_json_path=args.espn_json,
            out_path=args.out,
            sport=args.sport,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if args.cmd == "live":
        return run_live(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
