"""NHL expected goals (xG) built ONLY from our own NHL-API shots master (nhl/data/flatten.py): no MoneyPuck, Natural Stat
Trick or Evolving Hockey data is read or needed here (their terms bar commercial use / automated access; see
docs/analysis/nhl_xg_saves_goals_validation_2026-10-16.md s0).

    features.py   shot rows -> leakage-free model columns (geometry, shot type, strength, previous-attempt context)
    model.py      XGBoost classifiers (all unblocked attempts, and on-target only), time-ordered fitting + walk-forward OOF
    aggregate.py  shot-level xG -> per skater-game / goalie-game / team-game tables
    data.py       shots + masters loaders (local directory or mlb_core.storage)
    evaluate.py   `python -m nhl.xg.evaluate` temporal holdout report
"""
