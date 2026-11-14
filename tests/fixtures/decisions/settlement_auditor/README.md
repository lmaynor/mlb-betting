# Settlement auditor fixture (Build #1)

Offline evaluation fixture for `ev/settlement_auditor.py` (SettlementRulesAuditor,
Build #1 of Argon's Nov 11, 2026 Decisions-API rulings; merged items 13+14).

`entries.jsonl` — 25 venue/market rule entries. Each entry carries its own
`canonical_text` (beezy's consensus settlement definition for that market) and
the venue's `rule_text`, plus the label `divergent_dimensions` (a sublist of
`overtime_included`, `dnp_or_starter_rule`, `stat_source_mismatch`,
`postponement_cutoff_mismatch`, `tie_push_grading`; empty = the venue text
settles identically to canonical).

Counts: 15 entries with exactly one planted divergence each (15 planted
dimensions), 10 clean entries (several are deliberate paraphrases, so the
auditor cannot pass by text matching).

Provenance, honestly: three entries are DOCUMENTED from the repo's own
CONTEXT.md section 10 (DraftKings MLB batter/pitcher rules, and the FanDuel
batter-prop divergence: FanDuel requires a plate appearance only, no start
requirement, where DraftKings requires both). Every other entry is SEEDED:
rule texts composed for this fixture to state a known settlement position
(the position each label asserts), not retrieved house-rule documents.
`source_note` on each entry says which. The fixture grades whether the
auditor + the ruling's 0.20 divergence threshold recover the planted
dimensions and leave clean entries alone; it does not grade the real-world
accuracy of any venue's actual house rules.

Evaluation: `python -m ev.decisions_evals.eval_settlement` (live Decisions
calls, answers cached in `answers.jsonl` beside this README; re-scoring is
offline). Results land in `eval_results.json`: per-dimension recall,
entry-level quarantine precision/recall, and the clean-entry false-flag
rate. The ruling sets no numeric kill bar for Build #1; the operative gate
is the 0.20 quarantine threshold from the Nov 11 item-14 verdict.
