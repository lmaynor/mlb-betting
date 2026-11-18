---
title: Clerk updateUserMetadata deep-merges nested objects and only null deletes a key
module: beezy-vip.lib.contest.access
tags: [clerk, metadata, private-metadata, roles, contest]
problem_type: integration-issue
category: integration-issues
date: 2026-11-17
---

## Verified with the Clerk CLI on the DEVELOPMENT instance (throwaway user, deleted afterwards)

`PATCH /users/{id}/metadata` with `{"private_metadata": {"contest": {"roles": {...}}}}`:

- a nested write KEEPS keys it does not mention (writing only `march` kept `bowls`, and the Stripe `tier` was untouched);
- a key is removed only by writing `null` for it (`{"roles": {"bowls": null}}`);
- a write that merely leaves a key out does NOT delete it.

## The bug it exposed

`revokeContestRole` wrote the remaining roles without the slug, so revoking silently did nothing. It now writes `{contest: {roles: {<slug>: null}}}`.
`grantContestRole` writes only its own slug (race-safe), after a read to keep an admin from being downgraded by a repeat join.
The test fake in `tests/contest-access.test.ts` merges the way Clerk does (deep merge, null deletes) so it would have caught this.

## Also seen in the same session

A user created through the Backend API has `primary_email_address` verification `{status: "verified", strategy: "admin"}`, the field `contestUser()` reads.
Clerk's dev instance allows creating a throwaway user with `clerk api /users --instance dev --yes -d '{...}'`; production was never touched.
