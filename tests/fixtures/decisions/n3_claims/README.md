# N3 fixture: 100 research claims (seeded)

Seeded 2026-11-13 for the N3 research-claim evidence auditor (Argon ruling:
`~/workspace/beezy/profitability/argon-decisions-api-expansion-VERBATIM-2026-11-13.md`,
candidate N3, ADOPT standalone). Kill criterion this fixture grades: kill N3
if recall on contradicted claims is < 100%, overall planted-flaw recall is
< 90%, or the false-block rate on clean claims exceeds 5%.

File: `claims.jsonl`, one JSON object per line, exactly 100 claims.

## Seeding method

Every claim is drawn from real beezy material read during seeding — no
invented corpus. Source documents (all under `~/workspace/beezy/` unless
noted): `ladder-banking-2026-11-12.md`, `novig-prop-analysis-2026-11-12.md`,
`sgp-linemates-fix-2026-11-12.md`, `ui-sdk-fit-audit-2026-11-12.md`,
`odds-provider-scan.md`, `nbc-pull-relaunch-2026-11-12.md`,
`tools-tab-removal-2026-11-12.md`, `merge-all-2026-11-13.md`,
`merge-cleanup-2026-11-13.md`, `blocked-items-fix-2026-11-13.md`,
`entity-presence-audit.md`, `picks-surfaces-alignment-2026-11-12.md`, the N3
ruling itself, and one memory-note-derived incident recorded in
`~/AGENTS.md` (the Oct 18, 2026 ESPN wrong-week pull).

- **Clean claims** quote or closely paraphrase a statement from a source
  document, and each carries an `evidence` field naming what settles it:
  the banked artifact, GCS prefix, audit reference, or verification run the
  source itself used (e.g. sink read-backs, `firstpass_matched.csv`
  re-counts, PR read-backs, production text fetches).
- **Planted flaws** are derived by mutating a real claim. Each carries
  `seeded_from` (the real claim/document it came from) and `mutation`
  (exactly what changed). Stale claims are usually unmutated facts seeded
  after a real superseding event; the `evidence` field names that event.

## Per-class counts

| Label | Count | Target |
|---|---|---|
| clean | 50 | ~45-55 |
| contradicted | 22 | >= 20 (kill bar is 100% recall on this class) |
| unsupported | 10 | spread across classes |
| stale | 9 | spread across classes |
| wrong_scope | 9 | spread across classes |
| **Total** | **100** | **100** |

Planted flaws total 50; clean controls total 50.

## How clean claims were verified

A claim was only admitted as clean if the seeding pass read the figure in
the source document AND the source's own audit trail verified it against
banked or live data: ladder counts read back from the sink files
themselves; Novig coverage and SOG figures re-counted from the banked
BettingPros archives and `firstpass_matched.csv`; PR sizes and merge states
from connector read-backs in the merge ledger; the Tools 404 and final jest
counts from the cleanup run's own verification on final main `ada59aa8`;
the pyarrow root cause from the reproduction in `blocked-items-fix`
(shadowing the pin makes all 21 tests pass). Claims whose sources flagged
their own figures as unverified (e.g. the Hydrogen note's repository
figures) were NOT admitted as clean.

Real incidents used as seed material include: the Hydrogen-4/Opus model
mapping error (contradicted), the agy-coder default-model change
(stale), the SGP "LINEMATE PTS" pairs that were not line mates
(wrong_scope), the ESPN wrong-week lineup pull (stale), D12's deleted
Boost EV calculator consumer (stale), the ia.ts authorship misremembering
(unsupported), PR #243 merging into the wrong base (clean fact + its
supersession as stale), and the Novig contested-vs-all coverage split
(wrong_scope).

## Fields

Clean: `{claim_id, claim_text, source_doc, evidence, label: "clean"}`.
Planted: the same plus `{seeded_from, mutation}` and `label` one of
`contradicted | unsupported | stale | wrong_scope`. Claim IDs: `N3-C###`
clean, `N3-X###` contradicted, `N3-U###` unsupported, `N3-S###` stale,
`N3-W###` wrong_scope.

## Known limits

- Class balance is uneven by design priority: contradicted is deliberately
  oversized (22) because its kill bar is 100% recall; stale and
  wrong_scope sit at 9 each, so per-class recall estimates for them carry
  wide error bars at n=9.
- All sources are beezy's own Nov 2026 research corpus; claims from other
  periods or external research are unrepresented.
- Several stale claims turn on intra-day Nov 13, 2026 supersessions
  (merge-all morning run vs cleanup run). Their `evidence` fields name the
  superseding merge commit so an auditor can be given the post-cleanup
  state as evidence; with pre-cleanup evidence only, they would grade as
  supported, which is the correct behavior for a point-in-time auditor.
- Two clean claims rest on documents whose own verification was a text
  fetch rather than a rendered-page check (production /tools 404,
  /cheat-sheet title); the cleanup report states that limit itself.
- The fixture grades the gate; it does not grade the deterministic numeric
  checker, which has its own unit tests in `tests/test_decisions_prep_n3.py`.
