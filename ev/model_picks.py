"""
ev.model_picks -- track, announce and score the MODEL's own NFL / NBA / NHL prop picks (the projection board's confluence rows).

    python -m ev.model_picks --sport nba|nhl|nfl [--dry-run] [--no-post]     one pass: select -> log -> post -> closing lines
    python -m ev.model_picks --sport nba --closing-only                      CLV capture only
    python -m ev.model_picks --fix-tracked-stakes [--dry-run]                one-off: demote pre-split 'tracked' rows to stake 0

Until this module the engines' output was only DISPLAYED (the /picks boards, GET /api/public/projections). Nothing logged a
model pick as a bet, nothing graded it, and the only tracked winter-sport bets (system 'EV') are the BOOK-OUTLIER alerts of
ev.sweep_props_alerts, which do not use the model at all. This closes that gap with the same machinery as the MLB systems:

  * SELECTION (pre-registered, not tuned): a board row is a TRACKED pick when it has a real line and a real price at a non-offshore,
    non-DFS book for the side the model prefers, `confluence` is true (model and market agree on the side, both edges positive,
    not edge-capped, >= CONFLUENCE_MIN_BOOKS reference books) and the game is >= MIN_MINS_TO_START away. A pick that also passes
    the repo's own Official Card gate (ev.daily_card.is_official_card_play) AND whose market-anchored blended EV (`blend_ev`, the
    best quote priced at the blended posterior, c measured per stat) is >= OFFICIAL_MIN_BLEND_EV, on a stat with measured signal
    (PROPS_SIGNAL_ALLOWLIST) and with no sharp book pricing the side lower than the pack, is tier 'official'; every other
    confluence row is tier 'tracked'. Only official picks are posted and staked.
  * LEDGER: bets table, system='PROPS', sport=<sport>, paper. Official = flat 1 unit, kelly_triggered=True (the public record: /picks,
    /results, the #daily-recap headline all count only kelly_triggered, stake > 0 rows). Tracked = kelly_triggered=False, stake 0:
    monitoring rows, the same contract as every MLB system's unplaced predictions. They are still settled (on a 1-unit reference,
    settle_bets._settle_winter_bets) and still get closing lines, so the forward CLV / calibration study keeps every row without
    the unvetted ones reaching the public ledger. First qualifying sighting wins (never re-priced or re-logged), so the record
    cannot be cherry-picked after the fact. Settlement is the existing winter settler
    (settle_bets._settle_winter_bets: league player id, fail-closed, void only on a proven DNP) -- it keys on sport, not system.
  * CLV: closing_odds / closing_prob / clv_pct from the board archive's LAST pre-game state of the same prop (same line only).
  * DISCORD: official-tier picks only, through the sport's alerts channel, labelled paper / unvalidated. Never #ops-alerts, never
    a +EV channel. The recap embed renders PROPS separately from the 'EV' outlier alerts (settle_bets._winter_sport_stats).

Everything here is paper and UNVALIDATED: the repo's own studies (docs/analysis/*_vs_market_*.md) found no engine that beats the
market at executable prices. This module's job is to produce the forward, leak-free, price-and-result record that can settle that.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from ev import daily_card as DC
from ev import daily_projections as DP
from ev.props import DFS_BOOKS

log = logging.getLogger("ev.model_picks")

SYSTEM = "PROPS"
SPORTS = ("nfl", "nba", "nhl")
MIN_MINS_TO_START = 30.0
MAX_PAYLOAD_AGE_MIN = 45.0          # a board older than this is not live; log nothing rather than log a stale price
MAX_POSTS_PER_PASS = 5              # mirrors the Official Card's own cap
HEARTBEAT_KEY = "ModelPicks/last_run.json"          # {ts, sports: {sport: {status, selected, logged, ...}}}; monitor_ops reads it
STAKE = 1.0
# Official tier also needs the market-anchored blended EV to clear this. The engines' own standalone P(side) is noisier than the
# market (NBA log loss 0.7164 vs 0.6859), so a raw model-vs-market gap is mostly winner's curse; the blend shrinks it to what the
# stat's measured c says the engine adds (points / pra c = 0.02 add almost nothing, so they rarely clear this).
OFFICIAL_MIN_BLEND_EV = 0.025
# Official tier also needs the stat to carry MEASURED information beyond the market on the engine that prices it (blend c >= 0.08,
# t >= 2.5 in the repo's own real-line studies). A stat that fails stays on the board and in the tracked tier (stake 0, still settled
# and closed, so the forward record keeps accumulating) but is never staked or posted. Measured, per the study docs:
#   NBA threes +0.19 (t 8.4), rebounds +0.13 (t 9.4), assists +0.09 (t 5.7)      nba_engine_vs_market_2026-11-01.md, `ship_noabs`
#   NHL saves  +0.48 pooled on the board engine (-0.23 / +0.84 / +0.19 by season: regime-dependent)   nhl_engine_vs_market_2026-11-01.md
#   NFL rush_yds +0.25 (t 2.9) -- measured on the HIERARCHICAL engine only, so only rows that engine priced
# EXCLUDED on evidence: NBA points +0.02 / pra +0.02 (forward audit -9.5% / -26.5%), NHL goals +0.03 / assists +0.06 / points +0.03 on the
# board engine. NOT measured at all (no real lines to test them on): NBA pr / pa / ra / steals / blocks, NHL sog / blocked_shots on the
# board engine, every other NFL stat -- unmeasured is not allowed, whatever a prior says.
PROPS_SIGNAL_ALLOWLIST = {
    "nba": frozenset({"threes", "rebounds", "assists"}),
    "nhl": frozenset({"saves"}),
}
NFL_HIER_SIGNAL = frozenset({"rush_yds"})
# NHL rows served from the TRAINED models (NHL_TRAINED_PROPS on): measured c goals +0.37, assists +0.34, points +0.175, saves +0.75
# (docs/analysis/nhl_trained_serving_2026-11-01.md). Information, not money -- the blend_ev gate still has to clear.
NHL_TRAINED_SIGNAL = frozenset({"goals", "assists", "points", "saves"})


def has_measured_signal(r: dict) -> bool:
    """True when this row's (sport, stat, engine) has a measured market-blend c >= 0.08 (see PROPS_SIGNAL_ALLOWLIST)."""
    sport, stat = r.get("sport"), r.get("stat")
    if sport == "nhl" and r.get("nhl_model") == "trained_v1":
        return stat in NHL_TRAINED_SIGNAL
    if sport == "nfl":
        return stat in NFL_HIER_SIGNAL and "hierarchical" in str(r.get("engine") or "")
    return stat in PROPS_SIGNAL_ALLOWLIST.get(sport, ())


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _best(r: dict, side: str):
    """(book, american price) of the row's best quote on `side`, or None."""
    odds = r.get(f"best_{side}_odds")
    book = r.get(f"best_{side}_book")
    try:
        return (str(book), int(odds)) if book and odds is not None else None
    except (TypeError, ValueError):
        return None


def qualifies(r: dict, now: datetime | None = None) -> str | None:
    """None when the board row is not a tracked pick, else its tier: 'official' | 'tracked'."""
    if not r.get("has_line") or r.get("line") is None:
        return None
    side = str(r.get("side") or "").lower()
    if side not in ("over", "under") or not r.get("confluence") or r.get("edge_capped"):
        return None
    best = _best(r, side)
    if best is None:
        return None
    book = best[0].lower()
    if DC.is_offshore_book(book) or book in DFS_BOOKS:
        return None
    mins = _num(r.get("mins_to_start"))
    if mins is None or mins < MIN_MINS_TO_START:
        return None
    if not DC.is_official_card_play(r, allow_dislocation=False):
        return "tracked"
    # no measured information on this stat, or a sharp reference book (Novig / ProphetX / Pinnacle, when one quotes) prices our side
    # lower than the pack: monitored, never staked. A board with no sharp fields (older schema) passes the sharp test (fail-open).
    if not has_measured_signal(r) or r.get("sharp_agrees") is False:
        return "tracked"
    # a board written before `blend_ev` existed (or a row with no blend) cannot show the engine clears the bar: not official
    blend_ev = _num(r.get("blend_ev"))
    return "official" if blend_ev is not None and blend_ev >= OFFICIAL_MIN_BLEND_EV else "tracked"


def to_pick(r: dict) -> dict:
    """The board row as a pick: the fields the ledger, the Discord embed and the closing-line matcher need."""
    side = str(r["side"]).lower()
    book, price = _best(r, side)
    p_model = _num(r.get("p_model_side"))
    p_mkt = _num(r.get("p_mkt_side"))
    return {
        "sport": r["sport"], "player": r["player"], "player_norm": r.get("player_norm"), "stat": r["stat"], "side": side,
        "point": float(r["line"]), "book": book, "american": price, "odds": price,
        "ev": _num(r.get("market_ev")), "p_fair": p_mkt, "p_model": p_model, "model_edge": _num(r.get("model_edge")),
        "p_blend": _num(r.get("p_blend_side")), "blend_ev": _num(r.get("blend_ev")),
        "n_books": r.get("n_books"), "score": r.get("beezy_score"), "game_key": r.get("game_key"),
        "commence": r.get("commence"), "mins_to_start": r.get("mins_to_start"), "proj_mean": _num(r.get("proj_mean")),
        "engine": r.get("engine"), "team": r.get("team"), "opp": r.get("opp"), "row_key": DP.row_key(r),
        "player_id": r.get("player_id"),
    }


def select(payload: dict, now: datetime | None = None) -> list[dict]:
    """All picks on a board payload, best Beezy Score first. One pick per (game, player, stat)."""
    picks, seen = [], set()
    for r in payload.get("rows") or []:
        tier = qualifies(r, now)
        if tier is None:
            continue
        p = {**to_pick(r), "tier": tier}
        if p["row_key"] in seen:
            continue
        seen.add(p["row_key"])
        picks.append(p)
    picks.sort(key=lambda p: (p["tier"] != "official", -(p.get("score") or 0), -(p.get("ev") or 0.0)))
    return picks


def payload_fresh(payload: dict | None, now: datetime) -> bool:
    age = DP.payload_age_min(payload, now)
    return payload is not None and age is not None and age <= MAX_PAYLOAD_AGE_MIN


# -- ledger ------------------------------------------------------------------------------------------------------------

def _db_path() -> str | None:
    p = os.environ.get("MLB_DB_URL") or os.environ.get("DB_URL")
    if p:
        return p
    if os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"):
        log.error("model_picks: MLB_DB_URL is not set in a Cloud Run job; refusing to write to an ephemeral sqlite file")
        return None
    return "EV_Alerts/data/ev_bets.db"      # local development only


def bet_type_of(p: dict) -> str:
    return f"{p['stat']}_{p['side']}_{p['point']}"


def _already_logged(tracker, sport: str, game_day: str, p: dict) -> bool:
    """True when a PROPS bet already exists for this (game day, player, stat): first sighting wins, whatever the line is now."""
    from sqlalchemy import text
    with tracker.engine.connect() as conn:
        types = conn.execute(
            text("SELECT bet_type FROM bets WHERE system = :sys AND sport = :sp AND game_date = :d AND player = :pl"),
            {"sys": SYSTEM, "sp": sport, "d": game_day, "pl": p["player"]},
        ).fetchall()
    # bet_type = '<stat>_<side>_<point>'; compare the stat exactly ('pr' must not match 'pra')
    return any(str(t[0]).rsplit("_", 2)[0] == p["stat"] for t in types)


def ledger_stake(p: dict) -> tuple:
    """(stake, kelly_triggered) of a pick: official = a flat staked 1u bet on the public record, anything else = a zero-stake
    monitoring row (kelly_triggered=False) that is settled and closing-line-captured but never counted in the public record."""
    return (STAKE, True) if p.get("tier") == "official" else (0.0, False)


def log_picks(sport: str, picks: list[dict], tracker=None) -> list[dict]:
    """Log picks not seen before. -> the picks that were NEWLY logged (each gets `bet_id`). Never raises per pick."""
    from mlb_core.alerts.sport_alerts import alert_game_day
    from mlb_core.odds.utils import american_to_implied_prob
    if not picks:
        return []
    if tracker is None:
        path = _db_path()
        if path is None:
            return []
        from mlb_core.tracking import BetTracker
        tracker = BetTracker(path, system=SYSTEM, sport=sport)
    new = []
    for p in picks:
        try:
            day = alert_game_day(p)
            if _already_logged(tracker, sport, day, p):
                continue
            a, h = _teams(p)
            mkt = american_to_implied_prob(p["american"])
            model_p = p["p_model"] if p["p_model"] is not None else (p["p_fair"] or mkt)
            stake, triggered = ledger_stake(p)
            bid = tracker.log_bet(
                game_date=day, player=p["player"], away_team=a, home_team=h, bet_type=bet_type_of(p),
                model_prob=float(model_p), market_prob=float(mkt),
                edge=float(p["model_edge"]) if p["model_edge"] is not None else float(model_p) - float(mkt),
                odds=int(p["american"]), stake=stake, kelly_triggered=triggered, paper=True, book=p["book"],
                proj_mean=p["proj_mean"],
                notes=json.dumps({
                    "stat": p["stat"], "point": p["point"], "side": p["side"], "n_books": p["n_books"], "tier": p["tier"],
                    "player_id": p.get("player_id") or _pid_from_norm(p.get("player_norm")), "game_key": p["game_key"],
                    "row_key": p["row_key"], "engine": p["engine"], "commence": p["commence"],
                    "p_fair": p["p_fair"], "ev": p["ev"], "score": p["score"],
                    "p_blend": p.get("p_blend"), "blend_ev": p.get("blend_ev"),
                }, default=str),
            )
            if bid is not None and bid > 0:
                new.append({**p, "bet_id": bid})
        except Exception:                                                                    # noqa: BLE001
            log.exception("model_picks: failed to log %s pick %s", sport, p.get("player"))
    return new


def _pid_from_norm(pn) -> str | None:
    pn = str(pn or "")
    return pn[2:] if pn.startswith("id") and len(pn) > 2 else None


def _teams(p: dict) -> tuple:
    away = home = None
    gk = str(p.get("game_key") or "")
    if "@" in gk:
        away, home = gk.rsplit(":", 1)[-1].split("@", 1)
    return away, home


# -- Discord -----------------------------------------------------------------------------------------------------------

def post_official(sport: str, picks: list[dict]) -> bool:
    """Post the newly logged OFFICIAL picks to the sport's alerts channel (paper / unvalidated). True if delivered."""
    from mlb_core.alerts.sport_alerts import post_sport_alerts
    rows = [p for p in picks if p.get("tier") == "official" and p.get("american") is not None][:MAX_POSTS_PER_PASS]
    if not rows:
        return False
    return post_sport_alerts(sport, rows, log_bets=False)


# -- one-off ledger repair ---------------------------------------------------------------------------------------------

def fix_tracked_stakes(tracker=None, dry_run: bool = False) -> dict:
    """Demote PROPS rows logged as staked bets before the tier split (tier 'tracked' in notes, kelly_triggered true / stake > 0) to
    monitoring rows: kelly_triggered = false, stake = 0. Idempotent. Settled rows keep their result and profit (the 1u reference
    profit), so the shadow record survives; they simply stop counting in the public record (/picks, /results, the recap headline).
    Rows with unparseable notes are left alone. Run it where the DB is reachable (the Cloud Run job):
        gcloud run jobs execute props-model-picks --region us-central1 --args="-m,ev.model_picks,--fix-tracked-stakes" --wait
    -> {"candidates", "updated", "unparsed"}"""
    from sqlalchemy import text
    if tracker is None:
        path = _db_path()
        if path is None:
            return {"candidates": 0, "updated": 0, "unparsed": 0, "error": "no db"}
        from mlb_core.tracking import BetTracker
        tracker = BetTracker(path, system=SYSTEM, sport="nba")        # the sport only scopes the tracker's own reads; the SQL below is explicit
    with tracker.engine.connect() as conn:
        rows = conn.execute(text("SELECT id, notes FROM bets WHERE system = :sys AND kelly_triggered = :t"),
                            {"sys": SYSTEM, "t": True}).fetchall()
    ids, unparsed = [], 0
    for bid, notes_s in rows:
        try:
            tier = json.loads(notes_s or "{}").get("tier")
        except (TypeError, ValueError):
            unparsed += 1
            continue
        if tier == "tracked":
            ids.append(int(bid))
    if ids and not dry_run:
        with tracker.engine.begin() as conn:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                keys = {f"i{n}": v for n, v in enumerate(chunk)}
                conn.execute(text("UPDATE bets SET kelly_triggered = :f, stake = 0 WHERE id IN (%s)" % ", ".join(":" + k for k in keys)),
                             {"f": False, **keys})
    return {"candidates": len(ids), "updated": 0 if dry_run else len(ids), "unparsed": unparsed}


# -- closing lines -----------------------------------------------------------------------------------------------------

def capture_closing(sport: str, now: datetime | None = None, lookback_days: int = 3, get_archive=None, tracker=None) -> dict:
    """Fill closing_odds / closing_prob / clv_pct on started PROPS bets from the board archive's last pre-game state.

    Compared only when the archive row is the SAME line (a moved line is not a comparable close). closing_odds = the best price
    on the bet's side in that last state; closing_prob = the de-vigged consensus probability of the side."""
    from sqlalchemy import text
    from mlb_core.odds.utils import clv_pct_from_prices
    now = now or datetime.now(timezone.utc)
    get_archive = get_archive or DP.get_archive
    if tracker is None:
        path = _db_path()
        if path is None:
            return {"updated": 0, "skipped": 0, "error": "no db"}
        from mlb_core.tracking import BetTracker
        tracker = BetTracker(path, system=SYSTEM, sport=sport)
    since = (now - timedelta(days=lookback_days)).date().isoformat()
    with tracker.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, notes, odds FROM bets WHERE system = :sys AND sport = :sp AND closing_odds IS NULL "
                 "AND game_date >= :s"), {"sys": SYSTEM, "sp": sport, "s": since}).fetchall()
    updated = skipped = 0
    arch_cache: dict = {}
    for bid, notes_s, entry in rows:
        try:
            notes = json.loads(notes_s or "{}")
            commence = datetime.fromisoformat(str(notes["commence"]).replace("Z", "+00:00"))
            if commence.tzinfo is None:
                commence = commence.replace(tzinfo=timezone.utc)
            if commence > now:
                skipped += 1                                  # not started: the archive row is not a closing state yet
                continue
            day = DP.ct_day(notes["commence"])
            if day not in arch_cache:
                arch_cache[day] = get_archive(sport, day)
            arch = arch_cache[day]
            if not arch:
                skipped += 1
                continue
            row = next((r for r in arch.get("rows") or [] if DP.row_key(r) == notes.get("row_key")), None)
            side = str(notes["side"])
            best = _best(row, side) if row else None
            if (row is None or best is None or not row.get("has_line") or row.get("line") is None
                    or abs(float(row["line"]) - float(notes["point"])) > 1e-9):
                skipped += 1
                continue
            p_close = _num(row.get("p_mkt_side"))
            clv = clv_pct_from_prices(int(entry), best[1])
            with tracker.engine.begin() as conn:
                conn.execute(text("UPDATE bets SET closing_odds = :c, closing_prob = :p, clv_pct = :v WHERE id = :id"),
                             {"c": best[1], "p": p_close, "v": None if clv != clv else float(clv), "id": int(bid)})
            updated += 1
        except Exception:                                                                    # noqa: BLE001
            log.exception("model_picks: closing capture failed for bet %s", bid)
            skipped += 1
    return {"updated": updated, "skipped": skipped, "candidates": len(rows)}


# -- CLI ---------------------------------------------------------------------------------------------------------------

def run(sport: str, dry_run: bool = False, post: bool = True, closing_only: bool = False, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out: dict = {"sport": sport, "dry_run": dry_run}
    if not closing_only:
        payload = DP.read_precomputed(sport)
        if not payload_fresh(payload, now):
            out.update(status="stale_or_missing_board", selected=0, logged=0)
        else:
            picks = select(payload, now)
            out.update(status="ok", selected=len(picks), official=sum(p["tier"] == "official" for p in picks))
            if dry_run:
                out["picks"] = picks
            else:
                new = log_picks(sport, picks)
                out["logged"] = len(new)
                out["logged_official"] = sum(p["tier"] == "official" for p in new)
                if post and new:
                    out["posted"] = post_official(sport, new)
    if not dry_run:
        out["closing"] = capture_closing(sport, now)
        _heartbeat(sport, out, now)
    return out


def _heartbeat(sport: str, out: dict, now: datetime) -> None:
    """Record this pass for monitor_ops (read-modify-write; a failure here never fails the pass)."""
    try:
        from mlb_core import storage
        try:
            hb = json.loads(storage.read_bytes(HEARTBEAT_KEY)) if storage.exists(HEARTBEAT_KEY) else {}
        except Exception:                                                                    # noqa: BLE001
            hb = {}
        keep = {k: out.get(k) for k in ("status", "selected", "official", "logged", "logged_official", "posted", "closing")}
        hb = {"ts": now.isoformat(), "sports": {**(hb.get("sports") or {}), sport: {**keep, "ts": now.isoformat()}}}
        storage.write_bytes(json.dumps(hb, default=str).encode(), HEARTBEAT_KEY)
    except Exception:                                                                        # noqa: BLE001
        log.warning("model_picks: heartbeat write failed", exc_info=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport", choices=SPORTS + ("all",), help="required unless --fix-tracked-stakes")
    ap.add_argument("--dry-run", action="store_true", help="select and print only; no ledger, Discord or closing writes")
    ap.add_argument("--no-post", action="store_true")
    ap.add_argument("--closing-only", action="store_true")
    ap.add_argument("--fix-tracked-stakes", action="store_true",
                    help="one-off: demote PROPS rows logged as staked bets with tier 'tracked' to kelly_triggered=false, stake=0 "
                         "(honours --dry-run); does nothing else")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if a.fix_tracked_stakes:
        print(json.dumps(fix_tracked_stakes(dry_run=a.dry_run)))
        return 0
    if a.sport is None:
        ap.error("--sport is required")
    for sport in (SPORTS if a.sport == "all" else (a.sport,)):
        try:
            res = run(sport, dry_run=a.dry_run, post=not a.no_post, closing_only=a.closing_only)
        except Exception:                                                                    # noqa: BLE001
            log.exception("model_picks: %s pass failed", sport)           # one sport failing must not stop the others
            continue
        _print(res)
    return 0


def _print(res: dict) -> None:
    picks = res.pop("picks", None)
    print(json.dumps(res, default=str))
    for p in picks or []:
        print("  %-8s %-24s %-14s %-5s %6g @ %-12s %+5d  ev %+.1f%%  model %.1f%%  mkt %.1f%%  books %s  score %s" % (
            p["tier"], p["player"][:24], p["stat"], p["side"], p["point"], p["book"], p["american"],
            100 * (p["ev"] or 0), 100 * (p["p_model"] or 0), 100 * (p["p_fair"] or 0), p["n_books"], p["score"]))


if __name__ == "__main__":
    sys.exit(main())
