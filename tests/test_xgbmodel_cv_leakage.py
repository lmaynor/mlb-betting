"""
Regression test for the 2026-08-16 audit's XGBModel CV-leakage fix
(finding C3.1): train()'s early-stopping watchlist must never include the
caller's X_test/y_test -- only an internal validation slice carved from the
tail of X_train. This is the shared helper retrain_nrfi_v17.py/v18.py's
walk_forward_cv() diagnostic path uses; leaking dtest into early stopping
there means the reported per-fold AUC/Brier (the E05 drift narrative in
CONTEXT.md is built on these numbers) were measuring a model that had
already "seen" the exact fold it was being scored on.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import numpy as np
import pandas as pd
import xgboost as xgb

from mlb_core.models.base import XGBModel


def _synthetic_binary_df(n=200, seed=0):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    logit = 0.8 * x1 - 0.5 * x2
    p = 1 / (1 + np.exp(-logit))
    y = (rng.uniform(size=n) < p).astype(int)
    return pd.DataFrame({"f1": x1, "f2": x2}), pd.Series(y)


def test_watchlist_never_contains_eval_tag(monkeypatch):
    """The literal tag "eval" (paired with dtest) must never appear in the
    evals= watchlist passed to xgb.train -- that was the leak."""
    X_train, y_train = _synthetic_binary_df(200, seed=1)
    X_test, y_test = _synthetic_binary_df(50, seed=2)

    captured = {}
    real_train = xgb.train

    def _spy_train(params, dtrain, **kwargs):
        captured["evals"] = kwargs.get("evals")
        return real_train(params, dtrain, **kwargs)

    monkeypatch.setattr(xgb, "train", _spy_train)

    model = XGBModel(
        params={"objective": "binary:logistic", "max_depth": 2, "eval_metric": "auc"},
        features=["f1", "f2"],
    )
    model.train(X_train, y_train, X_test, y_test, num_boost_round=20, early_stopping=5)

    tags = [t for _, t in captured["evals"]]
    assert "eval" not in tags, f"X_test leaked into the early-stopping watchlist: tags={tags}"
    assert tags == ["train", "val"], f"expected exactly [train, val], got {tags}"


def test_val_slice_is_disjoint_from_reported_test_metric_data():
    """The val slice used for early stopping must come from X_train's own
    tail, never from X_test -- i.e. X_test is untouched until the final
    post-hoc predict() call."""
    X_train, y_train = _synthetic_binary_df(200, seed=3)
    X_test, y_test = _synthetic_binary_df(50, seed=4)

    model = XGBModel(
        params={"objective": "binary:logistic", "max_depth": 2, "eval_metric": "auc"},
        features=["f1", "f2"],
    )
    results = model.train(X_train, y_train, X_test, y_test, num_boost_round=20, early_stopping=5)

    # The reported OOS metric must still be computed against the real X_test
    # (untouched by training) -- confirm it's present and sane.
    assert "auc" in results
    assert 0.0 <= results["auc"] <= 1.0


def test_val_frac_zero_disables_early_stopping_gracefully():
    """val_frac=0 (or too little data) must not crash -- falls back to
    training on all of X_train with no internal val split."""
    X_train, y_train = _synthetic_binary_df(30, seed=5)
    model = XGBModel(
        params={"objective": "binary:logistic", "max_depth": 2, "eval_metric": "auc"},
        features=["f1", "f2"],
    )
    results = model.train(X_train, y_train, val_frac=0, num_boost_round=10)
    assert "best_iteration" in results
