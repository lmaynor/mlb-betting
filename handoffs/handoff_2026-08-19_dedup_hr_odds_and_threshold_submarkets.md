# Handoff -- 2026-08-19 -- dedup key fix, HR odds bug, 2+/3+ threshold sub-markets

Picked up from `handoffs/handoff_2026-08-17_audit_remediation_complete.md`. That
handoff's own open items are now resolved (see below); this session found and fixed
two more real production incidents, then shipped a full new feature. **Everything in
this handoff is deployed (rev `mlb-betting-00287-8pt`) and verified against real
runtime behavior, not just revision existence** -- see "the verification discipline"
note near the end, it matters.

## TL;DR

- **E9/B2.4 closed.** The two "missing" job-provisioning findings were real jobs,
  just orphaned (hand-created, last run 2026-06-24/05-25, superseded by
  `mlb-build-all-features`). CONTEXT.md's inventory corrected.
- **Bet dedup key was missing `player`.** Finding B3.3's new unique index crashed
  every runner in production the very first time it ran (pre-existing duplicate data
  blocked `CREATE UNIQUE INDEX`), and -- worse -- would have silently dropped every
  2nd-player bet per game forever for HR/K/OUTS/BATTER_HITS/BATTER_TB/PITCHER_ER if
  left as shipped. Fixed (`idx_bets_dedup_v3` adds `player`), cleaned, deployed,
  verified.
- **HR odds were coming through ~10x too long** (Acuna at +4300 instead of +400).
  DraftKings' own "2+ HR" alt-line was clobbering the real "1+ HR" price in the
  ParlayAPI adapter. Fixed, then found the identical bug also affects the O/U markets
  (hits/TB/strikeouts/outs/earned runs) and fixed that too. Both corrupted days'
  (08-18, 08-19) HR data were cleaned for 08-19; 08-18's is left uncorrected (see
  Loose Threads).
- **Shipped 2+/3+ threshold sub-markets** for K/OUTS/BATTER_TB/BATTER_HITS -- a full
  vertical (odds extraction -> scoring -> settlement -> Discord -> frontend), not
  just the model math. HR's 2+/3+ is deliberately deferred (needs a real count model,
  not just plumbing).
- All of the above is merged to `main`, pushed, and **live on revision
  `mlb-betting-00287-8pt`**, verified via real triggered runs (not just checking the
  revision exists -- see below).

## What's deployed and how it was verified

Revision `mlb-betting-00287-8pt`, deployed from a git worktree at commit `d0fa8df`
(see "A close call" below for why a worktree, not the normal working directory).

**Verification, in order, each one real, not assumed:**
1. `gcloud run revisions describe mlb-betting-00287-8pt ... creationTimestamp` --
   confirmed AFTER the build finished, not just that a revision object exists.
2. `POST /snapshot-odds` to force a fresh odds pull on the new code (the previous
   session's HR-odds investigation found the hard way that `/run` only ever reads
   whatever's already in `Odds/sgo/latest.json` -- it never rebuilds it itself).
3. `POST /run` for K/BATTER_TB/BATTER_HITS against that fresh snapshot.
4. Queried the live `bets` table directly for `bet_type LIKE '%PLUS%'` rows from
   today -- confirmed real `BATTER_HITS_2PLUS_2.0` (23 rows) and
   `BATTER_TB_2PLUS_2.0`/`_3PLUS_3.0` (56+5 rows, 15 triggered) rows with sane,
   distinct odds per player (105-426 range). K/OUTS's ladder-style alt lines simply
   weren't quoted for today's specific pitchers (confirmed via log search, zero "N+
   Ks" lines) -- not a bug, just today's market data; the extraction logic itself
   was already verified against real captured Chris Bassitt ladder data in
   `tests/test_parlay_adapter.py`, and the shared scoring path
   (`mlb_core/risk/threshold_bets.py`) is the exact same code proven live via
   BATTER_TB/BATTER_HITS.

**Don't trust a Cloud Run revision timestamp or "build SUCCESS" alone** -- this
session hit that exact mistake twice (see the bet-dedup and HR-odds incidents in
memory) before landing on "trigger a real run, check real log/DB output" as the only
reliable check.

## A close call: a second, concurrent session shares this working directory

Partway through wrapping up, `git status` showed uncommitted changes to
`mlb/runners/build_game_features.py`, `mlb_core/data/lineups.py`, and
`mlb_core/data/weather.py` that this session never touched. Investigation (git
reflog) confirmed a **second, concurrent Claude Code session is working in this
same `/Users/lmaynor/mlb-betting` directory right now**, on a branch called
`fix/feature-data-pipeline-2026-08-19` -- almost certainly acting on
`docs/audits/2026-08-19_feature_data_pipeline_review.md` (an unrelated, real finding
that surfaced mid-session; see Loose Threads).

**What happened:** a routine `git commit` from this session landed on THEIR branch
(shared working directory = shared HEAD; their `checkout` moved it out from under
this session between an earlier check and a later one). Caught it before pushing
anything wrong, confirmed via `git diff main <their-branch> -- <3 files>` that the
two branches were commit-identical for those files (the differences were purely
their uncommitted work, safe to disentangle), cherry-picked the stray commit onto
`main`, reset their branch pointer back to its original commit, and switched back --
**their uncommitted working-directory changes were never touched or at risk** at any
point once this was noticed. The actual deploy build was then done from an isolated
`git worktree` at the exact commit to be deployed, specifically so their in-progress,
uncommitted edits could never get swept into the Cloud Build upload (which tars up
the current directory by default).

**Takeaway for next session: check `git branch --show-current` and `git status`
before assuming this repo's working directory is exclusively yours.** If another
session's branch/uncommitted changes are present, either coordinate, or build/deploy
from a `git worktree` at the specific commit you intend to ship, never from the
shared directory directly.

## The three fixes, briefly (full detail in memory)

**Bet dedup (`mlb_core/tracking/bet_tracker.py`, deployed `00285`):** added `player`
to `is_duplicate()`, `log_bet()`'s `ON CONFLICT`, and the unique index itself
(`idx_bets_dedup_v3`). 16 genuine duplicate rows cleaned from prod (F5/NRFI/OUTS);
24 HR rows that LOOKED like duplicates under the old key were confirmed to be
distinct players and left untouched. See memory
`project_ops_incident_2026-08-18_bet_dedup.md`.

**HR + O/U alt-line odds (`mlb_core/odds/parlay_adapter.py`, deployed `00286`,
`845d83e`):** a book's own "2+ HR" (or "2+ Ks" ladder, or a second two-sided O/U
line) was clobbering the real main-line price via last-write-wins on JSON key order.
Fixed for HR first (keep lowest point, unambiguous), then found and fixed the
identical bug for the other 5 markets (prefer whichever point has both `over` AND
`under` quoted over any one-sided alt-line rung). See memory
`project_ops_incident_2026-08-19_hr_odds.md`.

**2+/3+ threshold sub-markets (`0a07a61`):** new bet_type convention
`"{SYSTEM}_{N}PLUS_{N}.0"`, full stack (odds extraction preserves every quoted
point via a new `alt_lines` field, not just each book's canonical line; 4 new
`sgo.py` extractors; shared one-sided scoring math in new module
`mlb_core/risk/threshold_bets.py`; settlement grades one-sided, HR-style; Discord
and the `beezy-vip` frontend BOTH had a real pre-existing bug here that had to be
fixed, not just extended -- see memory `project_threshold_submarkets_2026-08-19.md`
for the full writeup, and CONTEXT.md's new "Adding a 2+/3+ threshold sub-market"
subsection (s6) for the template if this gets extended to PITCHER_ER or a
real-count-model HR later.

## Loose threads

- **Yesterday's (2026-08-18) already-corrupted HR bets were never cleaned up** --
  those games are over, no live market to re-fetch, so delete+rerun isn't meaningful
  there. Open decision: void those specific rows, or leave as known-flawed history.
  Not urgent, just not forgotten.
- **HR 2+/3+ is deferred**, needs an actual new count regression (Poisson/NegBin on
  raw HR count), not a re-evaluation of the existing binary classifier. The bakeoff's
  `xhr_poisson` candidate is NOT a shortcut -- confirmed it still only outputs
  P(HR>=1), needs a feature absent from `main`, and has never successfully scored in
  any bakeoff run.
- **`docs/audits/2026-08-19_feature_data_pipeline_review.md`** -- written by the
  concurrent session mentioned above, not this one. Real, well-substantiated finding:
  GAME's home/away starter-attribution fix (finding A4, previously believed complete)
  never reached 89.6% of training data, because `build_starter_features()` is an
  incremental builder that only ever rewrites the trailing ~90 days and carries older
  rows forward byte-for-byte unchanged. User explicitly said they'll handle this in
  a separate session -- left completely untouched here, including the file itself
  (still untracked as of this handoff).
- **Local machine now has full gcloud + cloud-sql-proxy + psql access**, set up
  across the last two sessions (`brew install --cask google-cloud-sdk`,
  `gcloud auth login`, `gcloud components install cloud-sql-proxy`,
  `brew install libpq && brew link --force libpq`). No longer purely a
  Cloud-Shell-relay workflow -- but see "a close call" above for the one real hazard
  this unlocks (shared working directory with other concurrent sessions).

## Where things stand

`docs/audits/2026-08-16_fix_checklist.md`: 51/51 (E9/B2.4 closed this session).
`main` is clean, in sync with `origin/main`, at `d0fa8df`. 560 Python tests + 33
frontend jest tests passing. Service live on `mlb-betting-00287-8pt`.
