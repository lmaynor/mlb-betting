# Handoff -- 2026-09-02 -- mlb-tweet-recap: 4 stacked bugs, all fixed and verified live

Picked up from a spawned follow-up task (`task_f6f0284c`, from the 2026-09-01
GCP cost review session) flagging that `mlb-tweet-recap` had no `TWEET_MODE`
env var and had likely never posted a real recap tweet. The task's own
suggested fix was "set the env var, verify with a manual trigger, check for
another silent skip" -- that verification surfaced three MORE real, fully
independent bugs. **All four are now fixed, deployed, and confirmed live**:
a real manual trigger generated 3 real tweet drafts from real settled-bet
data and all 3 landed in Typefully with real URLs.

## TL;DR

Four independent, stacked bugs, each of which alone would have kept
`mlb-tweet-recap` from ever posting a real recap tweet:

1. **`TWEET_MODE` env var missing on the Job** -- defaulted to `"picks"`,
   so the recap job ran the picks code path (harmlessly no-op'ing at 5am ET
   since no picks exist yet) every day, looking like a healthy success.
2. **`build_recap_prompt()`'s date filter could never match** -- compared
   `game_date` to `date.today()`, but `/settle` always settles *yesterday's*
   slate and this job runs an hour later, after `date.today()` has already
   rolled forward. Fixing bug 1 alone would still have produced "No settled
   bets today -- skipping" forever.
3. **`generationConfig.maxOutputTokens=512` too tight for the recap prompt**
   -- discovered live, mid-verification, immediately after fixing 1+2: two
   real manual triggers in a row crashed (`json.loads("")` on attempt 1,
   `json.loads(<truncated>)` on the auto-retry) because Gemini's variable
   internal "thinking" tokens ate the whole budget before any/all visible
   text. This is the exact risk yesterday's Gemini model migration
   (`0eee983`) flagged as observed-but-assumed-harmless -- it wasn't, for
   this specific (longer) prompt shape.
4. **Typefully disabled v1 API-key access entirely** -- found immediately
   after fixing 1-3, once the job finally had real tweets to push: every
   Typefully draft push 403'd ("API v1 access via API keys is disabled").
   Affects `mlb-tweet-picks` too (shared code, shared key), independent of
   and in addition to bugs 1-3. Migrated to v2; two more real surprises
   found only by actually calling the live API (see below).

All four fixed, deployed, and confirmed via a final real manual trigger:
3 real tweet drafts generated from real settled-bet data, all 3 pushed to
Typefully successfully (`https://typefully.com/?d=10589568&a=308369`,
`...d=10589569...`, `...d=10589570...`).

## Root causes, in the order they were found

### 1. TWEET_MODE env var wipe

`RUNBOOKS.md`'s own "one-shot Cloud Shell update after domain or tweet job
code changes" snippet used `--set-env-vars` (full replace of the entire env
list) for what was meant to be a 2-var partial patch
(`BEEZY_API_URL`/`BEEZY_SITE_URL`). Any time that snippet ran, it silently
dropped whatever `TWEET_MODE` value had been set on `mlb-tweet-recap` by
hand -- same "replace vs. merge" flag trap as `--set-cloudsql-instances`/
`--add-cloudsql-instances` elsewhere in this repo. There was also no
checked-in provisioning script for either tweet job at all (both were
hand-created originally) -- so there was no source of truth to catch the
drift.

Confirmed live: `gcloud run jobs describe mlb-tweet-recap` showed an env
list byte-identical to `mlb-tweet-picks` (no `TWEET_MODE` on either), and
`gcloud scheduler jobs describe mlb-tweet-recap-schedule` showed **no HTTP
body at all** -- the "TWEET_MODE=recap" table entries in CONTEXT.md/
RUNBOOKS.md documented *intent*, never an actual wiring. A real log line
nailed the symptom: `2026-09-01T10:01:31Z [tweet_drafter] mode=picks` on
what was supposed to be a recap run.

Full writeup: `docs/solutions/runtime-errors/cloud-run-job-set-env-vars-wipes-existing.md`.

### 2. Dead date filter in build_recap_prompt()

Confirmed live via yesterday's real `/settle` log:
`settle: starting for settle_date=2026-08-31` when run on 2026-09-01 --
`/settle` (09:00 UTC) always settles the PREVIOUS calendar day.
`mlb-tweet-recap` runs an hour later (10:00 UTC); by then `date.today()`
inside the container has already rolled to the next day relative to every
bet `/settle` just processed. The old filter (`game_date ==
str(date.today())`) was therefore false by construction, every single day,
independent of bug 1.

Fix: derive the recap date from `max(game_date)` in the settled batch
instead of `date.today()` -- `get_recent_settled()` already orders
`game_date DESC`, so this is exactly "the most recently completed, fully
settled slate," which is what a 5am-ET recap actually means by "today's
results."

Full writeup: `docs/solutions/logic-errors/tweet-recap-date-filter-never-matches.md`.

### 3. maxOutputTokens too tight for the recap prompt

Found by actually running the fixed job for real (not just reasoning about
it) -- two consecutive live manual triggers both crashed inside
`generate_tweets()`:
- Attempt 1: `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`
  -- Gemini's visible text was empty.
- Attempt 2 (Cloud Run's own auto-retry): `JSONDecodeError: Unterminated
  string starting at: line 2 column 3 (char 4)` -- visible text started
  but got cut off mid-array.

Both are the 512-token ceiling being exhausted by Gemini's internal
"thinking" tokens before (or partway through) producing visible output --
the exact caveat left in yesterday's `gemini-flash-latest` migration
commit (`0eee983`: "~360-420 of the 512 budget spent on thinking...
harmless at this budget... worth knowing if maxOutputTokens is ever
lowered"). It turned out not to be harmless for the longer recap prompt
(season stats + 3 line items) specifically.

Fix: bumped to 1536, and `generate_tweets()` no longer assumes
`parts[0]["text"]` exists/is non-empty -- an empty/missing result now
raises a clear `RuntimeError` with the real `finishReason` +
`usageMetadata`, instead of a bare `JSONDecodeError` three frames removed
from the actual cause.

### 4. Typefully v1 API disabled by the vendor

Once bugs 1-3 were fixed, a real trigger finally generated 3 real tweet
drafts from real data and every Typefully push 403'd: "API v1 access via
API keys is disabled. Please update your integration to API v2." Per
Typefully's own migration guide, v1 API-key access was scheduled to stop
entirely by 15 June 2026 -- this had likely been silently broken for BOTH
tweet jobs for ~3 months, independent of bugs 1-3.

Migrated `push_to_typefully()` + added `_get_social_set_id()` to Typefully's
v2 API (new base path, `Authorization: Bearer` header, `platforms.x.posts[]`
payload). Two real surprises the vendor's own docs didn't fully cover,
found only by actually calling the live API with the real production
account:
- **The existing v1-era key authenticates fine against v2** -- despite
  Typefully's docs stating "v1 keys cannot be used with v2." No key
  rotation was needed in practice.
- **`platforms.x.enabled: true` is required** -- the vendor's own "minimal
  request body" example omits it. The real API returned a clean 422
  (`"Field required", field "platforms.x.enabled"`) that pinpointed the fix
  immediately -- this is exactly why `push_to_typefully()`'s error handling
  was hardened to surface the real response body instead of swallowing it.

Full writeup: `docs/solutions/integration-issues/typefully-api-v1-sunset.md`.

## What was done (all live/deployed)

4 commits, 4 branches, each merged to `main` and pushed:
- `fix/tweet-recap-mode-and-date-filter-2026-09-02` (bugs 1+2): date-filter
  fix, RUNBOOKS.md `--set-env-vars` -> `--update-env-vars` + clarified
  TWEET_MODE ownership, CONTEXT.md s9 correction + new s15.9 gotcha,
  `deploy/setup_tweet_jobs.sh` (new -- first-ever provisioning script for
  these 2 jobs), 2 new `docs/solutions/` writeups, `tests/test_tweet_drafter.py`
  (new, 7 tests).
- `fix/tweet-drafter-gemini-max-tokens-2026-09-02` (bug 3): maxOutputTokens
  bump + `generate_tweets()` hardening, 4 more tests.
- `fix/typefully-api-v2-migration-2026-09-02` (bug 4, first pass): v1->v2
  migration, 1 doc writeup, 6 more tests.
- `fix/typefully-enabled-field-2026-09-02` (bug 4, second pass): the
  `platforms.x.enabled` fix found via the real 422, doc corrections.

**Live infra**, applied via `deploy/setup_tweet_jobs.sh` (idempotent, safe
to re-run) after each of 4 build+deploy cycles:
- `mlb-tweet-recap` now has `TWEET_MODE=recap` baked into the Job.
- `mlb-tweet-picks` explicitly has `TWEET_MODE=picks` too (previously
  relying on the code default -- exactly how this broke).
- `scheduler-invoker` re-confirmed with `run.invoker` on both jobs.
- Both scheduler jobs re-upserted with an explicit `--attempt-deadline=320s`.

4 full build+deploy cycles (`gcloud builds submit --config=cloudbuild.yaml`,
~1 min each thanks to the 2026-09-01 layer-caching fix, then
`gcloud run services update`/`update-traffic`, then `setup_tweet_jobs.sh`):
revisions `mlb-betting-00296-k78` -> `...00297-z7m` -> `...00298-kjh` ->
`...00299-q5z` (current).

**654 tests passing** (was 637 at session start; +17 new in
`tests/test_tweet_drafter.py`, the first coverage this file has ever had).
The 3 date-filter tests were verified via `git stash` to actually fail
against the pre-fix code (not tautological); the Gemini/Typefully tests
reproduce the exact live failure shapes seen during manual verification.

## Verification

Bootstrapped per CLAUDE.md (read CONTEXT.md in full, checked the latest
`handoffs/` file) before touching anything, then verified every claim
against real production state rather than trusting the spawned task's
"presumably" language -- and kept verifying after each fix instead of
declaring victory early, which is exactly how bugs 3 and 4 were found.

- `gcloud run jobs describe mlb-tweet-recap` (before fix): env list
  identical to `mlb-tweet-picks`, no `TWEET_MODE` on either.
- `gcloud scheduler jobs describe mlb-tweet-recap-schedule`: confirmed no
  HTTP body at all on either tweet scheduler job.
- `gcloud logging read` on 2026-09-01's actual recap execution: confirmed
  `mode=picks` in production, matching the task's own claim.
- `gcloud logging read` on 2026-09-01's actual `/settle` run: confirmed
  `settle_date=2026-08-31` when run on 2026-09-01, proving the date-filter
  bug independently of any code reading.
- Real manual trigger after fixing bugs 1+2: logged `mode=recap
  date=2026-09-02`, did not skip, reached "Generating tweets via Gemini..."
  -- then crashed on bug 3.
- Two real manual triggers hit bug 3's two failure shapes exactly as
  diagnosed (empty text, then truncated text on the auto-retry).
- Real manual trigger after fixing bug 3: 3 real tweet drafts generated
  correctly labeled "Aug 31" (today's `/settle` hadn't run yet at trigger
  time, so Aug 31 genuinely was the most recent settled slate) with a real
  record (6-4, +8.93u) and real season stats (18,715 bets, -0.92% ROI) --
  then every Typefully push 403'd, surfacing bug 4.
- Real manual trigger after the v1->v2 migration: auth succeeded (no
  401/403), but every draft POST returned a real 422 pinpointing the
  missing `enabled` field.
- **Final real manual trigger, after all four fixes**: 3 real drafts
  generated, all 3 pushed successfully with real Typefully draft URLs.
  Clean exit 0.
- 654/654 local tests pass (`.venv_audit`, the same ad hoc Python 3.14 venv
  used by prior sessions since this laptop's system Python can't build the
  production `pyarrow==17.0.0` pin).

## Open items for a future session

- `mlb-tweet-picks` itself was not independently re-verified pushing a real
  Typefully draft this session (its one manual trigger hit the legitimate
  "No picks today" early return before reaching that code path) -- it
  shares the exact same `push_to_typefully()` code and Typefully account,
  so there's no reason to expect it to behave differently, but worth a
  glance at Cloud Logging or the Typefully dashboard after its next real
  scheduled run (17:00 UTC) to confirm.
- No dedicated Discord/ops alert distinguishes "tweet job ran but Gemini/
  Typefully failed" from a genuine clean skip -- both currently just show
  as a Cloud Run Job failure or a quiet exit 0 (a real "no settled bets"
  day, which IS legitimate on off-days). Flagged as a prevention idea in
  `docs/solutions/integration-issues/typefully-api-v1-sunset.md`, not
  implemented this session.
- Typefully's free tier is 15 scheduled tweets/month, reset 2026-09-01;
  this session's verification used 3 of them (the one round of 3 that
  actually succeeded) on real test drafts (real settled-bet data, but
  drafts, not published tweets) -- worth deleting those 3 from the
  Typefully dashboard after reviewing them so they don't eat into real
  picks/recap quota later this month.
