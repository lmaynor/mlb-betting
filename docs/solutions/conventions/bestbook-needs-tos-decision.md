---
title: The BestBook live feed is gated behind BESTBOOK_ENABLED until a ToS / licensing decision is recorded
module: mlb_core/odds/bestbook.py
tags: [bestbook, tos, licensing, compliance, sse, deploy]
problem_type: convention
category: conventions
date: 2026-11-01
---

## Convention

- `mlb_core/odds/bestbook.py` reads a competitor's private, cookie-authenticated SSE endpoint (bestbook.app/events). It is not a
  published API and no ToS / licensing review is on record. This repo has already ruled out another provider on ToS grounds
  (4codds, see the memory index), so the same bar applies here: **a ToS / licensing decision is required before this is enabled.**
- Until that decision is written down (here, with the date and who made it), the feed is OFF:
  - `mlb/runners/bestbook_live_props.py` refuses to run (exit 2, `BestBookDisabled`) unless `BESTBOOK_ENABLED=1`.
  - `deploy/setup_bestbook_live.sh` refuses to provision unless `BESTBOOK_ENABLED=1`, and every scheduler it creates is born
    PAUSED (`BESTBOOK_RESUME=1` un-pauses; a deliberate second step). It passes `BESTBOOK_ENABLED=1` to the jobs it creates.
- The code is kept (not deleted) so the decision can go either way. Downstream readers (`ev.daily_projections.load_events`) only
  read `Odds/bestbook_live/{sport}/latest.json`; with no crawl they simply see no BestBook file.
- Request pacing is described as what it is: startup jitter and randomized pauses to spread load. It is not, and must not be
  described as, a way to evade detection. Do not reintroduce "anti-detection" / "anti-sniffing" wording or behavior.

## Client behavior (so the retry loop cannot hammer the endpoint)

`_sse_messages` gives up (raises `BestBookError`) after 6 consecutive failed attempts or a 180 s overall deadline for the one-shot
calls, backs off exponentially with jitter, honours `Retry-After`, and re-bootstraps the `bb` cookie on 403/429. `crawl_sport`
fetches one game snapshot at a time and stops after 3 consecutive snapshot failures.
