# Session bootstrap

Repo: https://github.com/lmaynor/mlb-betting

**Read `CONTEXT.md` in full before touching any code.**
Then check the latest file in `handoffs/` for current status.
Do not browse the repo on GitHub, explore the file tree, or grep until both are done.

## Knowledge store

`docs/solutions/` — documented bugs, gotchas, and conventions, organized by category with YAML frontmatter (`module`, `tags`, `problem_type`). Relevant when implementing features, debugging issues, or working in a documented area.

Categories:
- `runtime-errors/` — Cloud Run, pg8000, gcloud deploy failures
- `integration-issues/` — SGO API quirks, Savant leaderboard gotchas, MLB Stats API edge cases
- `logic-errors/` — feature build bugs, model artifact pitfalls, data pipeline issues
- `conventions/` — how we do things in this repo (retrain sequence, config imports, etc.)

`CONCEPTS.md` — shared domain vocabulary (SGO, NRFI, Kelly, NegBin, etc.). Relevant when orienting to the codebase or discussing domain terms.

## Model stack

Who does what. Pick the cheapest model that can do the step without quality loss.

| Layer | Model | Use for | Do not use for |
|---|---|---|---|
| Orchestration and judgment | Claude Opus 5.5 or Sonnet 5.5 (this session) | Planning, design, code review, deploy decisions, every user-facing answer | Bulk file reading, log trawling |
| Bounded lookups | Claude Haiku 5.5 (Agent tool, `model: "haiku"`) | Finding files, grep, summarizing logs or diffs, reading CI output, checking whether something exists | Design calls, debugging root causes, anything with a judgment call |
| Mechanical execution | agy, Gemini 3.8 Flash low (`agy-exec` skill) | Running a command list Claude has already written: git reads, branch/commit/push, gcloud reads | Choosing commands, editing code, interpreting results |

Rules:
- Escalate: if a Haiku result is ambiguous or conflicts with the code, re-check it at the orchestration level. Do not act on it.
- Verify agy output: Claude reads back state (`git log -1 --stat`, `gcloud run services describe`) before reporting success.
- agy write and deploy commands run only after the user approves the exact command list in chat.
- The Gemini CLI (`gemini`) does not work on this account (IneligibleTierError). Use `agy` only.

## Git and PR process

**Binning work into PRs.** Split before committing, not after.
- One PR = one concern. Concerns are: one sport's modeling work (MLB, NFL, NBA, NHL, NCAAB, soccer), shared UI, shared infra, and one type (feature, fix, refactor, docs, research).
- Modeling work stays separate per sport. Never mix two sports' models, engines, features, or data pipelines in one PR, even when the change looks similar. Do not touch another sport's files unless the PR is for that sport.
- UI and infra PRs may span all sports at once.
- Schema or contract changes (anything in CONTEXT.md section 5) get their own PR so they can be reviewed alone.
- Tests and docs ride with the change they cover. A separate docs-only PR is fine when nothing else changed.
- Run `git diff --stat main...HEAD` before opening a PR. If the file list spans two concerns, split it.
- Research and analysis (`docs/analysis/`, `scripts/`, offline tooling) goes in its own PR, separate from serving or deploy changes.

**Branches and commits.**
- Branch from an up-to-date `main`. Name it `feat/`, `fix/`, `chore/`, `docs/`, or `research/` plus a short slug.
- One logical change per commit. Use conventional prefixes (`feat:`, `fix:`, `docs:`, `chore:`) and say why in the body.
- Keep the attribution trailer from the session's system instructions on commits and PR bodies.
- Never commit directly to `main`. Never force-push. Never use `git stash` without a unique tag (other sessions share the stack).

**Before a PR.**
- Run tests. For Cloud Run changes, the deploy script runs them: `./deploy/deploy_service.sh`.
- PR body: what changed, what is deliberately NOT in it, deploy impact (undeployed, shadow-only, or OFF by default), and the test evidence.

**What merging does.**
- Pushing to `main` auto-deploys `beezy-vip` to Vercel. Treat a merge to `main` as a frontend deploy.
- Vercel comments on every push to `main`. That is expected noise. Do not investigate those comments as failures unless the Vercel deployment status is an error.
- Cloud Run is deployed only through `deploy/deploy_service.sh`, never `gcloud builds submit` directly, and only after the user approves.
- Do not enable auto-merge unless asked.

**Session wrap-up sequence.** Run these in order. Do not skip ahead.
1. Tests pass locally for every touched area.
2. Commit each concern as its own commit. Update `CONTEXT.md` and commit it last (`docs: update CONTEXT.md`).
3. Open the PRs. Merge only after the user approves.
4. Deploy Cloud Run through `deploy/deploy_service.sh`, only after the user approves the deploy.
5. Trigger the Cloud Run jobs the change needs, after the deploy finishes. See CONTEXT.md section 9 for the scheduler reference. Never trigger jobs before the deploy they depend on.
6. Verify: read back the revision with `gcloud run services describe`, and check the job's execution status.
7. Clean up merged branches and worktrees.

**Cleanup.** Delete merged branches and their worktrees. Use the `commit-commands:clean_gone` skill for branches already marked gone.
