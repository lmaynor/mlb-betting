---
name: agy-exec
description: Hand mechanical shell steps (git reads, branch/commit/push, gcloud builds and deploys) to the agy CLI on Gemini Flash to save Claude tokens. Claude keeps the decisions, the approval gate, and verification of every result. Use for routine git and GCP command sequences, not for code design or debugging.
---

# agy-exec: delegate mechanical shell steps to agy

## Split of work

- **Claude** decides what to do, writes the exact command list, judges the output, and owns any user-facing reply.
- **agy** (Gemini 3.8 Flash, low effort) runs the listed commands and returns raw output. It does not choose commands, edit code, or interpret results.
- **The user** approves every write or deploy before agy runs it.

Use this only for steps where the commands are already known. If the task needs judgment about code, use Claude directly.

## Tiers

| Tier | Examples | Gate |
|---|---|---|
| Read | `git status`, `git log`, `git diff --stat`, `gcloud run services describe`, `gcloud builds list` | None. Run with `--mode plan`. |
| Local write | `git add`, `git commit`, `git checkout -b`, `git push` to a feature branch | User approves the exact command list in chat. |
| Deploy | `gcloud builds submit`, `gcloud run deploy`, `gcloud run jobs update`, anything touching prod | User approves the exact command list in chat, every time. Never reuse a prior approval. |

Never push to `main`, force-push, delete branches, or run destructive `gcloud` commands through agy. Those stay with Claude and need explicit user approval in chat.

## How to call

Read tier:

```bash
agy -p "<PROMPT>" --mode plan --model gemini-3.8-flash-low --print-timeout 120s
```

Write or deploy tier, after the user approves the exact list:

```bash
agy -p "<PROMPT>" --mode accept-edits --model gemini-3.8-flash-low --print-timeout 600s
```

Do not use `--dangerously-skip-permissions`.

## Prompt template

Keep the prompt narrow and mechanical:

```
Run these commands in order from the repo root. Stop at the first non-zero exit.
Print each command, then its full output. Do not run any other command. Do not edit files.
1. <command>
2. <command>
```

## Verification (Claude does this, not agy)

1. Check the exit status reported for each step. If a step failed or was skipped, say so and stop.
2. For deploys, read back the state with a read-tier command (for example `gcloud run services describe`) and confirm the new revision and image. Do not trust agy's summary alone.
3. For commits, run `git log -1 --stat` yourself before reporting success.
4. If output is ambiguous or truncated, re-run the read in Claude instead of asking agy to interpret it.

## Gotchas

- Gemini CLI (`gemini`) is no longer usable on this account (IneligibleTierError). Use `agy` only.
- `agy --mode plan` was observed running read-only git commands. Its write blocking is not verified, so keep write-tier commands out of plan mode.
- `--print-timeout 0` waits indefinitely. Always set a timeout.
