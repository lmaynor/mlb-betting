"""Drive-level Markov-chain Monte Carlo NFL game simulator (research tooling, NOT wired into serving).

Modules:
  pbp_prep      nflverse play-by-play -> clean scrimmage-play / drive tables with the state buckets the engine uses
  script_state  MEASURES how pass rate responds to score differential and time remaining (no assumed coefficient)
  fit           walk-forward parameter estimation from pbp of seasons < the evaluated season (MLE / empirical)
  ratings       as-of team rating deltas (pass, rush, sack, pace ...) and as-of player usage shares
  engine        the vectorised simulator: one coherent joint game per draw
  evaluate      walk-forward replay vs the serving EWM engine and the hierarchical engine (log score / MAE), the
                correlation and scoreboard diagnostics, and the leakage test

Every output is UNVALIDATED research output; see docs/analysis/nfl_drive_simulator_*.md for what the evidence supports.
"""
