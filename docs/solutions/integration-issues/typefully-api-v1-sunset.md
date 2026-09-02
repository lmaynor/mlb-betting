---
title: Typefully disabled v1 API-key access -- migrated to v2 (existing key works, payload needs platforms.x.enabled)
module: tweet_drafter.py
tags: [typefully, api-migration, tweet-drafter]
problem_type: integration_error
category: integration-issues
date: 2026-09-02
---

## Problem

`push_to_typefully()` in `tweet_drafter.py` used the v1 endpoint
(`POST /v1/drafts/` with `X-API-KEY` header). Typefully has disabled v1
API-key access entirely -- every push returned 403. Confirmed live via a
real manual trigger of `mlb-tweet-recap` on 2026-09-02, after the
`TWEET_MODE`/date-filter/token-budget bugs were already fixed and the job
had real tweet drafts ready to push:

```
Draft 1 FAILED (403): {"detail":"API v1 access via API keys is disabled. Please update your integration to API v2: https:/...
```

Each push failure is caught per-item and only logged, never raised -- so
the job still exits 0. This affects `mlb-tweet-picks` identically (same
shared function, same account-level Typefully restriction), even though it
was not independently observed failing live this session (its one real
manual trigger hit the legitimate "No picks today" early return before
reaching this code path). Per Typefully's own migration guide, v1
API-key access was scheduled to stop working entirely by 15 June 2026 --
meaning this was likely silently broken for both jobs for most of the past
three months, independent of and in addition to the `TWEET_MODE`/date-
filter/Gemini-token-budget bugs documented alongside this one.

## Root Cause

Typefully rebuilt their API from the ground up (v2): different base path
(`/v2/social-sets/{social_set_id}/drafts` instead of `/v1/drafts/`),
different auth header (`Authorization: Bearer` instead of `X-API-KEY`),
different payload shape (a `platforms.x.posts[]` structure instead of a
flat `content` field), and a new concept -- a "social set" (the connected
platform account a draft posts under) looked up via `GET /v2/social-sets`
and referenced by numeric id in the URL.

## Fix

Migrated `push_to_typefully()` + added `_get_social_set_id()` to call the
v2 endpoints per Typefully's official docs
(https://typefully.com/docs/api,
https://support.typefully.com/en/articles/13133296-typefully-api-v1-v2-migration-guide).

Two real, live surprises the docs didn't fully cover, found only by
actually running it against the production account:

1. **The existing v1-era API key works fine against v2.** Typefully's own
   migration guide states "API v1 keys cannot be used with API v2" --
   in practice, the SAME `typefully-api-key` secret value that 403'd
   against the v1 endpoint authenticated successfully against both
   `GET /v2/social-sets` and `POST /v2/social-sets/{id}/drafts` (a real
   `Authorization: Bearer` request, not a guess). No key rotation was
   needed. (It's possible the docs mean something narrower -- e.g. keys
   created through a since-removed UI flow -- but for this account, the
   existing key just works.)
2. **`platforms.x.enabled: true` is a required field the docs' own
   "minimal request body" example omits.** The first real POST returned a
   clear `422`:
   ```json
   {"error": {"code": "VALIDATION_ERROR", "message": "Some fields are invalid.",
     "details": [{"message": "Field required", "field": "platforms.x.enabled", "type": "missing"}]}}
   ```
   Added `"enabled": True` alongside `"posts"` in the `platforms.x` object.

Both surprises were only findable by making real calls against the real
account and reading the real error bodies -- this is why
`generate_tweets()`/`push_to_typefully()` were hardened (in the companion
commit) to surface `resp.text`/structured error details on failure instead
of swallowing them into a generic exception. Confirmed end to end after
the `enabled` fix: real drafts land in Typefully (`Draft N pushed
(https://typefully.com/?d=...)`).

## Prevention

Don't trust a vendor's own migration-guide example payload as complete --
verify against a real account with a real call and read the actual error
body before assuming a documented "minimal" request is truly minimal.
Third-party vendors also sometimes state auth constraints ("v1 keys can't
be used with v2") that don't hold for every account/key -- when a
migration is required, try the existing credential against the new API
first before assuming a rotation is necessary; it's a strictly cheaper
first step and was correct here.
