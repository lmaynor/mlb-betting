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
