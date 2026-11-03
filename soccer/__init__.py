"""soccer -- the Beezy soccer vertical (Phase 1: EPL + UCL match markets).

Phase 1 is match markets only (1X2, totals, BTTS, double chance) priced from ONE Dixon-Coles scoreline matrix
(soccer.game), at $0 data spend: Understat match xG is the model input (metric-card variant "understat_shot_v1",
see soccer.config), football-data.co.uk supplies results + Pinnacle closing lines for the walk-forward backtest,
ClubElo is the anchor prior / sanity guard, and the Reep Register (soccer.data.reep_register) is the cross-provider
identity bridge. Everything this package emits is UNVALIDATED (validated: False) until the Pinnacle-close backtest
and live forward CLV clear the promotion bar.

Phase 2 (player props) is pre-wired in soccer.props and gated OFF.
"""
