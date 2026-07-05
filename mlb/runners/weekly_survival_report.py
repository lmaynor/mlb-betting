"""
mlb.runners.weekly_survival_report -- Monday soft-line intelligence report.

The weekly ritual, automated: refit the empirical per-(market,book) vig
lookup, run the stale-quote survival analysis over the trailing 14 days of
intraday snapshots, and post both to Discord (#performance) so nobody has to
remember to run it.

Scheduled DAILY-safe: exits immediately unless today is Monday (UTC), so it
can hang off any daily scheduler slot. Override with SURVIVAL_FORCE=1 for an
ad-hoc run. Provisioned by deploy/setup_weekly_survival.sh (Cloud Run Job +
Monday 13:00 UTC scheduler).
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

MARKETS_VIG = "hr_yn,k_ou,outs_ou,btb_ou,bhits_ou,nrfi_ou"
MARKETS_SURV = "hr_yn,k_ou,outs_ou,btb_ou,bhits_ou"
LOOKBACK_DAYS = 14
MIN_EV = float(os.environ.get("SURVIVAL_MIN_EV", "0.02"))


def _perf_webhook() -> str | None:
    return (os.getenv("DISCORD_WEBHOOK_PERFORMANCE")
            or os.getenv("DISCORD_WEBHOOK_OPS")
            or os.getenv("DISCORD_WEBHOOK_URL"))


def _post_report(title: str, body: str) -> None:
    from mlb_core.notify.discord import _post
    url = _perf_webhook()
    if not url:
        logger.warning("no Discord webhook configured -- printing report only")
        print(body)
        return
    # Discord embed description cap is 4096; keep margin.
    _post(url, {"embeds": [{"title": title, "description": body[:3900],
                            "color": 0x71D083}]})


def _vig_section() -> str:
    from mlb.analysis import book_vig as bv
    markets = [m.strip() for m in MARKETS_VIG.split(",")]
    since = (date.today() - timedelta(days=45)).isoformat()
    stats = bv.fit_markets(markets, since=since)
    if not len(stats):
        return "vig refit: no two-sided pairs found\n"
    bv.save_book_vig(stats)
    lines = ["**Vig refit (45d, saved to lookup)**"]
    for market, grp in stats.groupby("market"):
        soft = grp[grp["vig_median"] >= 0.08]
        sharp = grp[grp["vig_median"] <= 0.035]
        lines.append(
            f"`{market}` mkt {grp['vig_median'].median()*100:.1f}%"
            + (f" | soft: {', '.join(soft['book'])}" if len(soft) else "")
            + (f" | sharp: {', '.join(sharp['book'])}" if len(sharp) else ""))
    return "\n".join(lines) + "\n"


def _survival_section() -> str:
    from mlb.analysis import quote_survival as qs
    markets = [m.strip() for m in MARKETS_SURV.split(",")]
    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    frames = []
    for m in markets:
        ev = qs.events_for_market(m, since=since, min_ev=MIN_EV)
        if len(ev):
            frames.append(ev)
    if not frames:
        return (f"**Survival ({LOOKBACK_DAYS}d, min_ev {MIN_EV:.0%})**\n"
                "no outlier events yet -- intraday snapshot density still building")
    events = pd.concat(frames, ignore_index=True)
    stats = qs.summarize(events)
    lines = [f"**Stale-quote survival ({LOOKBACK_DAYS}d, min_ev {MIN_EV:.0%}, "
             f"{len(events)} events)**",
             "_strike: high survival + high book-corrected; avoid fading leaders_"]
    for market, grp in stats.groupby("market"):
        lines.append(f"`{market}`")
        for _, r in grp.head(8).iterrows():
            bm = r["pct_book_moved"]
            bm_s = f"{bm*100:.0f}%" if pd.notna(bm) else "n/a"
            lines.append(
                f"  {r['book']}: n={int(r['n_events'])} "
                f"med {r['median_surv_min']:.0f}m, >=30m {r['pct_surv_30m']*100:.0f}%, "
                f"held {r['pct_censored']*100:.0f}%, book-corrected {bm_s}")
    return "\n".join(lines)


def run() -> dict:
    if date.today().weekday() != 0 and os.environ.get("SURVIVAL_FORCE") != "1":
        logger.info("weekly_survival_report: not Monday -- skipping "
                    "(SURVIVAL_FORCE=1 to override)")
        return {"status": "skipped", "reason": "not monday"}
    logger.info("weekly_survival_report: running (vig refit + survival)")
    sections = []
    for name, fn in (("vig", _vig_section), ("survival", _survival_section)):
        try:
            sections.append(fn())
        except Exception as e:  # noqa: BLE001 -- one section failing should not kill the report
            logger.exception(f"weekly_survival_report: {name} section failed")
            sections.append(f"{name} section FAILED: {e}")
    body = "\n".join(sections)
    _post_report(f"Soft-line weekly report -- {date.today().isoformat()}", body)
    return {"status": "ok"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = run()
    raise SystemExit(0 if out.get("status") in ("ok", "skipped") else 1)
