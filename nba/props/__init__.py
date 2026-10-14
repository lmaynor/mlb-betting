"""NBA player-prop MODEL pillar (points, rebounds, assists, threes, PRA) -- paper / LOG-ONLY.

  data.py      SportsBlaze masters -> played-game rows + team games (ET game days from the schedule dumps)
  features.py  leakage-safe per-player-game features (strictly-earlier-day as-of joins; same code for train and live)
  dist.py      NegBin / discretised-Gamma predictive distributions, CRPS, P(over)
  train.py     per-stat XGBoost mean + heteroskedastic spread, walk-forward by season, artifact writer (local by default)
  evaluate.py  OFFLINE evaluation (accuracy vs naive baselines, calibration at PROXY lines) -> markdown
  identity.py  SportsBlaze UUID <-> ESPN id bridge (ambiguity dropped)
  score.py     LOG-ONLY scorer of banked props snapshots -> evidence rows (never Discord, never the bets table)
  forward.py   the pre-registered forward evaluation: grade + CLV vs the closing banked snapshot -> verdict

No NBA prop odds history exists, so no edge is claimed anywhere in this package. See
handoffs/handoff_2026-10-14_nba_props_model.md and docs/analysis/nba_props_evaluation_2026-10-14.md.
"""
