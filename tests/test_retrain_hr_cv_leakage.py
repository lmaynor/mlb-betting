"""
Regression test for the 2026-08-16 audit's HR walk-forward CV-leakage fix
(finding C3.2): retrain_hr_v6.py's own inline `_walk_forward_cv` used to put
the fold's test set directly into the early-stopping watchlist
(`evals=[(dtest, "test")]`), leaking it into training the same way the
shared XGBModel.train() helper did (finding C3.1, see
test_xgbmodel_cv_leakage.py). This is a *separate* code path -- HR has its
own inline CV loop, distinct from the shared class -- and the file's own
`_oos_eval` already got this right (a real 70/10/20 split with a `val` set
for early stopping), so `_walk_forward_cv` was the odd one out.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import numpy as np
import pandas as pd
import xgboost as xgb

from mlb.training.retrain_hr_v6 import _walk_forward_cv


def _synthetic_hr_df(n_per_year: dict[int, int], seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frames = []
    for year, n in n_per_year.items():
        dates = pd.date_range(f"{year}-04-01", periods=n, freq="h")
        f1 = rng.normal(size=n)
        f2 = rng.normal(size=n)
        logit = 0.5 * f1 - 0.3 * f2 - 2.5  # ~7% HR base rate, matches docstring
        p = 1 / (1 + np.exp(-logit))
        hr = (rng.uniform(size=n) < p).astype(int)
        frames.append(pd.DataFrame({"game_date": dates, "f1": f1, "f2": f2, "hr": hr}))
    return pd.concat(frames, ignore_index=True)


def test_walk_forward_cv_watchlist_never_contains_test_tag(monkeypatch):
    """The literal tag "test" (paired with a fold's held-out dtest) must
    never appear in any evals= watchlist passed to xgb.train from inside
    _walk_forward_cv -- that was the leak."""
    monkeypatch.delenv("HR_SKIP_CV", raising=False)
    df = _synthetic_hr_df({2023: 600, 2024: 600, 2025: 250}, seed=1)

    captured_tags = []
    real_train = xgb.train

    def _spy_train(params, dtrain, **kwargs):
        evals = kwargs.get("evals") or []
        captured_tags.append([t for _, t in evals])
        return real_train(params, dtrain, **kwargs)

    monkeypatch.setattr(xgb, "train", _spy_train)

    results, summary = _walk_forward_cv(df, features=["f1", "f2"], scale_pos_weight=1.0)

    assert captured_tags, "expected at least one fold to run (none were skipped)"
    for tags in captured_tags:
        assert "test" not in tags, f"a fold's dtest leaked into its own early-stopping watchlist: tags={tags}"
        assert tags == ["train", "val"], f"expected exactly [train, val], got {tags}"


def test_walk_forward_cv_still_reports_per_fold_metrics(monkeypatch):
    """Sanity check the fix didn't break the diagnostic output shape."""
    monkeypatch.delenv("HR_SKIP_CV", raising=False)
    df = _synthetic_hr_df({2023: 600, 2024: 600, 2025: 250}, seed=2)

    results, summary = _walk_forward_cv(df, features=["f1", "f2"], scale_pos_weight=1.0)

    assert len(results) >= 1
    for fold in results:
        assert "auc" in fold and 0.0 <= fold["auc"] <= 1.0
        assert "brier" in fold
    assert "cv_mean_auc" in summary
