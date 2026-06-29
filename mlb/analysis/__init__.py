"""MLB analysis tooling (offline / research).

Modules here are not part of the daily Cloud Run loops. They are review-ready
diagnostics and backtests that read GCS/local masters and model artifacts to
answer model-improvement questions, plus the odds_history store + loaders that
back the cross-system historical-odds + ROI program
(see handoffs/roadmap_2026-06-29_cross_system_odds_and_roi.md and
handoffs/roadmap_2026-06-28_model_improvement.md).
"""
