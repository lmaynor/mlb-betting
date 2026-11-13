# Weather classifier fixture (Build #4)

Offline evaluation fixture for `ev/decisions_weather.py` (Build #4 of
Argon's Nov 11, 2026 Decisions-API rulings; item 4, Kalshi Chicago
high-temperature contracts).

## cycles.jsonl (122)

Real archived NWS Chicago (LOT) products from the IEM AFOS archive
(mesonet.agron.iastate.edu retrieve service, pil AFDLOT / ZFPLOT),
2026-09-15 to 2026-11-13. Each item is one forecast-cycle pair for the
Chicago (Central Cook, ILZ104) zone:

- `forecast_high_prev` / `forecast_high_new`: the ZFP forecast high for
  `target_date` (tomorrow at the new cycle) at consecutive ZFP issuances.
  ZFP highs are verbal buckets ("upper 40s"); labels use bucket midpoints
  (lower = +2, mid = +5, upper = +8), so a one-bucket step is 3 degrees.
- `label`: `warmer` when the revision is >= +3, `cooler` when <= -3, else
  `steady`. All 22 shift pairs in the window are included, plus 100 sampled
  steady pairs (seed 20261113): 100 steady / 12 warmer / 10 cooler.
- `afd_short_term`: the SHORT TERM / DISCUSSION excerpt (fallback: KEY
  MESSAGES onward) of the AFD issued in the revision window, unedited
  apart from whitespace collapsing and a 2,200-character cut.

This is a modest real-data sample, NOT the ruling's 90-day backtest: the
90-cycle backtest harness ships in `ev/decisions_weather.py`
(`run_backtest`) and is the instrument for that judgment once a longer
archive pull is run. Nothing in this build enables live quoting; the gate
output is alert/telemetry only.

Evaluation: `python -m ev.decisions_evals.eval_weather` (live Decisions
calls, answers cached in `answers.jsonl` here; results in
`eval_results.json`). Evaluation code never synthesizes answers.
