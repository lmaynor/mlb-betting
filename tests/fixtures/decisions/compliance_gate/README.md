# Publication compliance gate fixture (Build #3)

Offline evaluation fixture for `mlb_core/alerts/compliance_gate.py`
(PublicationComplianceGate, Build #3 of Argon's Nov 11, 2026 Decisions-API
rulings; merged items 8+9).

## explanations.jsonl (100)

Each item carries a model `row` (daily-card play shape: player, bet_type,
book, odds, line, model_prob, edge, projection, and the `factors` list the
engine actually used) and a one-paragraph `explanation` of the play.

- `clean` (50): the explanation cites only factors from the row and numbers
  from the row.
- `hallucinated` (50): identical construction plus exactly ONE injected
  unsupported factor (`injected_factor` in weather / injury / trend / pace),
  appended as a final clause. The injected factor appears nowhere in the
  row's factors.

Rows are composed from 12 representative model rows across MLB/NBA/NHL/NFL
(the row shapes mirror ev/daily_card.py plays); explanations are composed
text, seeded RNG 20261113.

Bar (Nov 13 ruling): 100% rejection of the injected hallucinations by the
gate (deterministic number verification + the Decisions entailment gate at
P(unsupported_claim) > 0.15). The eval also reports the entailment-only
rejection rate and the clean-item false-rejection rate for transparency.

## social_drafts.jsonl (40)

Social draft texts paired with their model row: 20 `clean` (plain card
language) and 20 `tout` (lock/guarantee/streak/hype framing: "lock of the
day", "free money", "on fire", "cannot miss", "hammer it"). The compliance
choice question must classify the tout set; beezy copy carries no
trend/streak/hype framing.

Evaluation: `python -m ev.decisions_evals.eval_compliance` (live Decisions
calls, answers cached in this directory; results in `eval_results.json`).
Evaluation code never synthesizes answers: without an API key and a warm
cache it raises.
