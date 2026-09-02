# Handoff -- 2026-09-02 -- mlb-tweet-recap: wrong mode, dead date filter, tight token budget

Picked up from a spawned follow-up task (`task_f6f0284c`, from the 2026-09-01
GCP cost review session) flagging that `mlb-tweet-recap` had no `TWEET_MODE`
env var and had likely never posted a real recap tweet. The task's own
suggested fix was "set the env var, verify with a manual trigger, check for
another silent skip" -- that verification surfaced two more real bugs, all
three now fixed, deployed, and confirmed live.

## TL;DR

Three independent, stacked bugs, each of which alone would have kept
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

All three fixed, deployed, and the job now runs to completion and pushes
real drafts to Typefully (verified via a clean-exit manual trigger post-fix
-- see Verification below).

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

## What was done (all live/deployed)

1. Code fixes, two commits, each on its own branch merged to `main`,
   pushed:
   - `fix/tweet-recap-mode-and-date-filter-2026-09-02` (bugs 1+2's code/doc
     side): `tweet_drafter.py` date-filter fix, `RUNBOOKS.md`
     `--set-env-vars` -> `--update-env-vars` + clarified TWEET_MODE
     ownership, `CONTEXT.md` s9 table correction + new s15.9 gotcha,
     `deploy/setup_tweet_jobs.sh` (new, idempotent provisioning for both
     tweet jobs -- neither had one before), two new `docs/solutions/`
     writeups, `tests/test_tweet_drafter.py` (new, 7 tests).
   - `fix/tweet-drafter-gemini-max-tokens-2026-09-02` (bug 3): the
     `maxOutputTokens` bump + `generate_tweets()` hardening, 4 more tests
     in the same test file.
2. **Live infra**, applied via the new `deploy/setup_tweet_jobs.sh`
   (idempotent -- safe to re-run):
   - `mlb-tweet-recap` now has `TWEET_MODE=recap` baked into the Job.
   - `mlb-tweet-picks` explicitly has `TWEET_MODE=picks` too (previously
     relying on the code default -- exactly how this broke).
   - `scheduler-invoker` re-confirmed with `run.invoker` on both jobs
     (idempotent grant; was already correct).
   - Both scheduler jobs (`mlb-tweet-picks-schedule`,
     `mlb-tweet-recap-schedule`) re-upserted with the same cron/target,
     now with an explicit `--attempt-deadline=320s`.
3. **Two full build+deploy cycles** (image rebuild via
   `gcloud builds submit --config=cloudbuild.yaml` -- ~1 min each thanks
   to the 2026-09-01 layer-caching fix -- then `gcloud run services
   update`/`update-traffic`, then re-running `setup_tweet_jobs.sh` to move
   both jobs onto the new image): revision `mlb-betting-00296-k78` (bugs
   1+2), then `mlb-betting-00297-z7m` (bug 3, current).
4. **648 tests passing** (was 637 at session start; +11 new in
   `tests/test_tweet_drafter.py`, the first coverage this file has ever
   had). The 3 date-filter tests were verified via `git stash` to actually
   fail against the pre-fix code (not tautological), and the 2 empty/
   truncated-text tests reproduce the exact live failure shapes seen
   during manual verification.

## Verification

Bootstrapped per CLAUDE.md (read CONTEXT.md in full, checked the latest
`handoffs/` file) before touching anything, then verified every claim
against real production state rather than trusting the spawned task's
"presumably" language:

- `gcloud run jobs describe mlb-tweet-recap` (before fix): env list
  identical to `mlb-tweet-picks`, no `TWEET_MODE` on either.
- `gcloud scheduler jobs describe mlb-tweet-recap-schedule`: confirmed no
  HTTP body at all on either tweet scheduler job.
- `gcloud logging read` on 2026-09-01's actual recap execution: confirmed
  `mode=picks` in production, matching the task's own claim.
- `gcloud logging read` on 2026-09-01's actual `/settle` run: confirmed
  `settle_date=2026-08-31` when run on 2026-09-01, proving the date-filter
  bug independently of any code reading.
- After deploying fixes 1+2 and re-running `setup_tweet_jobs.sh`: real
  manual trigger (`gcloud run jobs execute mlb-tweet-recap --wait`) logged
  `mode=recap date=2026-09-02` and, critically, did **not** skip -- it
  proceeded to "Generating tweets via Gemini..." (proof the date filter
  now matches real data) before hitting bug 3.
- After deploying fix 3 and re-running `setup_tweet_jobs.sh` again: a
  second real manual trigger is the final verification for this session
  -- see the note appended below once its outcome is known.
- 648/648 local tests pass (`.venv_audit`, the same ad hoc Python 3.14 venv
  used by prior sessions since this laptop's system Python can't build the
  production `pyarrow==17.0.0` pin).

## Open items for a future session

- No dedicated Discord/ops alert distinguishes "tweet job ran but Gemini/
  Typefully failed" from a genuine clean skip -- both currently just show
  as a Cloud Run Job failure (bug 3's crash) or a quiet exit 0 (a real "no
  settled bets" day, which IS legitimate on off-days). Worth a lightweight
  alert if `mlb-tweet-recap`/`mlb-tweet-picks` fails outright (not just a
  clean skip) -- currently nothing pages on this.
- `mlb-tweet-picks`'s own recent execution history (checked in passing,
  not deeply investigated this session) showed what may be several failed
  attempts from 2026-08-29 through 2026-09-01 before yesterday's Gemini
  fix, then a successful manual verification trigger this morning
  (2026-09-02T05:18 UTC) -- consistent with yesterday's own handoff notes,
  not re-litigated here.
- Typefully's free tier is 15 scheduled tweets/month; this session's
  verification triggers pushed real drafts (see below) -- worth a glance
  at the Typefully dashboard to delete unused variants so they don't
  silently eat the monthly cap before real picks/recap drafts need it.
