---
title: BestBook live feed is ON (owner decision 2026-11-01) with no ToS / licensing review; kill switches documented
module: mlb_core/odds/bestbook.py
tags: [bestbook, tos, licensing, compliance, sse, deploy]
problem_type: convention
category: conventions
date: 2026-11-01
---

## Decision (recorded)

- 2026-11-01, the owner (lmaynor): BestBook is enabled and is an ADDITIONAL line source, not a gated experiment. The BESTBOOK_ENABLED opt-in
  gate is removed from `mlb/runners/bestbook_live_props.py` and `deploy/setup_bestbook_live.sh`; the `bb-live-*` schedulers are created enabled.
- What was NOT done: any ToS / licensing review. `mlb_core/odds/bestbook.py` reads a competitor's private, cookie-authenticated SSE endpoint
  (bestbook.app/events); it is not a published API. This repo ruled out 4codds on ToS grounds, so the same exposure applies here and it is the
  owner's accepted risk, not a cleared one. Revisit if BestBook objects, blocks, or the product becomes commercial at scale.
- Kill switches: `BESTBOOK_DISABLED=1` (also true/yes/on) on a job makes the runner exit 2 without touching the endpoint;
  `PAUSE=1 PROJECT_ID=... bash deploy/setup_bestbook_live.sh` pauses every `bb-live-*` scheduler, `RESUME=1` re-enables them.
- Downstream: `ev.daily_projections.load_events` reads `Odds/bestbook_live/{sport}/latest.json` as the third feed beside ParlayAPI and BettingPros
  (fresher feed wins a (book, stat); a book is counted once per stat). With no crawl it simply sees no BestBook file.
- History: the previous version of this note said the schedulers were "born PAUSED". The script never implemented that (a scheduler created by
  `gcloud scheduler jobs create http` is enabled); only the refusal flag was real. The test that claimed to pin it only grepped for strings.

## Rules that stay regardless

- Request pacing is described as what it is: startup jitter and randomized pauses to spread load. It is not, and must not be described as, a way
  to evade detection. Do not reintroduce "anti-detection" / "anti-sniffing" wording or behavior (`tests/test_bestbook_events.py` enforces it).

## Client behavior (so the retry loop cannot hammer the endpoint)

`_sse_messages` gives up (raises `BestBookError`) after 6 consecutive failed attempts or a 180 s overall deadline for the one-shot
calls, backs off exponentially with jitter, honours `Retry-After`, and re-bootstraps the `bb` cookie on 403/429. `crawl_sport`
fetches one game snapshot at a time and stops after 3 consecutive snapshot failures.
