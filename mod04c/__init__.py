"""MOD-04 Cluster (c) Playmaking shadow models and data contracts.

Modules:
  - feature_store   : U1 leak-proof pre-game feature assembly and LabelStore
  - dist_negbin     : U2/U4 discrete NegBin PMF, convolution, logit blend, rung shift
  - prd04_guard     : PRD-04 hard injury/freshness guardrail for NBA assists
  - nhl_assist_split: C1 primary/secondary assist split from NHL shots/PBP
  - nba_assists     : C2 NBA assists creation x conversion head
  - nhl_assists     : C3 NHL primary/secondary/unified assists heads
  - passing         : C4 NBA.com Stats Passing API banker and skip-condition gate
  - shadow          : U3/U6 shadow predictions serialization

Note: On merge into main, feature assembly rebinds to Cluster (a)'s shared
backbone module. Nothing in this package alters serving weights or production boards.
"""
