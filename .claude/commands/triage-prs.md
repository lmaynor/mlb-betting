---
name: triage-prs
description: Triage all open PRs -- summarize, label, and walk through merge/close decisions one-by-one
argument-hint: "[optional: repo URL or owner/name]"
allowed-tools: Bash(gh *), Bash(git log *), Bash(git branch *)
---

# Triage Open Pull Requests

Review and act on all open PRs for the mlb-betting repo.

## Step 0: Detect Repo

Current repo: !`gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo "lmaynor/mlb-betting"`
Current branch: !`git branch --show-current 2>/dev/null`

If `$ARGUMENTS` specifies a different repo, use that instead.

## Step 1: Gather Context (Parallel)

Run these in parallel:

1. **List all open PRs:**
   ```bash
   gh pr list --state open --limit 50 --json number,title,author,createdAt,labels,headRefName
   ```

2. **Recent main commits** (to detect superseded PRs):
   ```bash
   git log --oneline -20 main
   ```

3. **List existing labels:**
   ```bash
   gh label list --limit 50
   ```

## Step 2: Classify PRs

Group by type:
- **Feature** — new system, new market, new runner, new endpoint
- **Bug fix** — settlement bug, scoring bug, feature build fix
- **Model** — retrain, calibration, feature engineering changes
- **Frontend** — beezy-vip changes
- **Infra/ops** — deploy scripts, Cloud Run jobs, scheduler, monitoring
- **Docs** — CONTEXT.md, RUNBOOKS.md, handoffs
- **Stale** — PRs older than 14 days

## Step 3: Review Each PR

For each PR:
```bash
gh pr view <number> --json title,body,files,additions,deletions,author,createdAt
gh pr diff <number> | head -300
```

Determine:
- **Summary**: 1-2 sentence description of what changed
- **Risk**: does it touch model artifacts, the DB schema, or live runners?
- **Deploy impact**: does it need `./deploy/deploy_service.sh` or a Cloud Run Job update?
- **Tests**: does `pytest tests/` cover this? Does it need a handoff note?
- **Action**: merge / request changes / close / skip

Flag any PR that:
- Touches `mlb_core/odds/sgo.py` without updating extractors in the related runner
- Changes `bet_tracker.py` schema without adding a migration in `_MIGRATE_*_SQL`
- Adds a new runner without a `if __name__ == "__main__":` block
- Changes `CONTEXT.md` section numbering (breaks cross-references)
- Removes `--add-cloudsql-instances` from the deploy script

## Step 4: Show Triage Report

Present a table:

```
| PR | Title | Type | Risk | Action | Notes |
|----|-------|------|------|--------|-------|
| #N | ...   | Bug  | Med  | Merge  | Needs deploy |
```

## Step 5: Walk Through One-by-One

For each PR, show:
```
### PR #<number> - <title>
Author: <author> | +<additions>/-<deletions> | <age>
Type: <type> | Risk: <risk>

<summary>

Deploy needed: <yes/no -- deploy_service.sh / Cloud Run Job update>
Tests: <passing / needs test / not testable>
```

Ask for decision:
- **Merge** — merge this PR
- **Comment & hold** — leave feedback, keep open
- **Close** — close with reason
- **Skip** — move on

Execute:
- **Merge**: `gh pr merge <number> --squash --delete-branch`
  - If it's a runner/feature change: remind to run `./deploy/deploy_service.sh`
  - If it's a model change: remind to run the appropriate retrain + calibrate job sequence
- **Comment**: `gh pr comment <number> --body "<message>"`
- **Close**: `gh pr close <number> --comment "<reason>"`

## Step 6: Summary

```
Triage complete

Merged:    X
Commented: Y
Closed:    Z
Skipped:   W

Deploy needed: <list of merged PRs that require a deploy>
```

## Notes

- Never force push or reset main
- Merged runner/model changes always need a deploy -- the running Cloud Run service uses a pinned image digest
- After merging CONTEXT.md changes: remind to run `git add CONTEXT.md && git commit` if the doc was updated in-session
