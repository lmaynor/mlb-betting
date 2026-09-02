---
title: Typefully disabled v1 API-key access -- both tweet jobs' draft push always 403'd
module: tweet_drafter.py
tags: [typefully, api-migration, tweet-drafter]
problem_type: integration_error
category: integration-issues
date: 2026-09-02
---

## Problem

`push_to_typefully()` in `tweet_drafter.py` used the v1 endpoint
(`POST /v1/drafts/` with `X-API-KEY` header). Typefully has disabled v1
API-key access entirely -- every push now returns 403. Confirmed live via a
real manual trigger of `mlb-tweet-recap` on 2026-09-02, after the
`TWEET_MODE`/date-filter/token-budget bugs were already fixed and the job
had real tweet drafts ready to push:

```
Draft 1 FAILED (403): {"detail":"API v1 access via API keys is disabled. Please update your integration to API v2: https:/...
Draft 2 FAILED (403): ...
Draft 3 FAILED (403): ...
Done.
Container called exit(0).
```

Each push failure is caught per-item and only logged, never raised -- so
the job still exits 0. This affects `mlb-tweet-picks` identically (same
shared function, same account-level Typefully restriction) even though it
was not independently observed failing live this session -- the one real
manual trigger of `mlb-tweet-picks` on 2026-09-02 hit the (legitimate)
"No picks today" early return before ever reaching `push_to_typefully()`,
so this specific 403 has only been directly observed on the recap job. Both
jobs share one `typefully-api-key` secret and one code path, so there is no
reason to expect picks to behave differently the next time it actually has
picks to push (its next real scheduled run, 17:00 UTC).

Per Typefully's own migration guide, v1 API-key access was scheduled to
stop working entirely by "15th June 2026" -- meaning this has likely been
silently broken since then, for BOTH jobs, for the better part of three
months, independent of and in addition to the `TWEET_MODE`/date-filter/
Gemini-token-budget bugs documented alongside this one.

## Root Cause

Typefully rebuilt their API from the ground up (v2): different base path
(`/v2/social-sets/{social_set_id}/drafts` instead of `/v1/drafts/`),
different auth header (`Authorization: Bearer` instead of `X-API-KEY`),
different payload shape (a `platforms.x.posts[]` structure instead of a
flat `content` field), and a new concept -- a "social set" (the connected
platform account a draft posts under) that must be looked up via
`GET /v2/social-sets` and referenced by numeric id in the URL. v1 access
via API keys was deprecated on a fixed timeline that has since passed.

## Fix

Migrated `push_to_typefully()` and added `_get_social_set_id()` to call the
v2 endpoints per Typefully's official docs
(https://typefully.com/docs/api,
https://support.typefully.com/en/articles/13133296-typefully-api-v1-v2-migration-guide).

**This migration is UNVERIFIED against the real API.** Typefully's own
docs state "API v1 keys cannot be used with API v2" -- the only
`typefully-api-key` secret value available at the time of this fix is a v1
key, so no live call against the v2 endpoints could be made or confirmed.
The code is a faithful implementation of the documented v2 contract
(request/response shapes quoted directly from Typefully's docs) with
defensive, per-item error handling identical in spirit to the v1 version
(one item's failure doesn't block the others, and never crashes the job),
but it has not been exercised against a real, live v2-capable key.

**Action needed (cannot be completed by an agent):** log into the
Typefully dashboard, generate a new v2 API key (Settings -> API), and
rotate the `typefully-api-key` secret:
```bash
echo -n "NEW_V2_KEY" | gcloud secrets versions add typefully-api-key --data-file=-
```
Then re-run either tweet job manually and confirm a `Draft N pushed
(https://typefully.com/?d=...)` line appears in the logs instead of a
`FAILED` line, per RUNBOOKS.md s4's verification steps.

## Prevention

Third-party API vendors sometimes hard-cut older auth/API versions on a
fixed calendar date with no code-level warning beforehand -- there's no
generic prevention for the sunset itself, but the failure being *silent*
(caught, logged, job still exits 0) is avoidable. Consider: if
`push_to_typefully()`'s per-item failure rate is 100% for a run, that's
meaningfully different from "some transient failures" and arguably should
make the job exit non-zero (or post a one-time Discord ops alert) rather
than a routine exit 0 -- not changed in this pass, since the whole point of
per-item soft-fail was "one bad draft shouldn't sink the other two," which
is still the right call for a partial failure. A "100% of N failed" special
case could thread that needle without reintroducing the "success on
partial content" problem. Not implemented here -- flagging for a future
pass if this class of external-API sunset recurs.
