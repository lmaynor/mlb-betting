"""
XGBModel base class shared by NRFI, HR, F5, and K Pro.
Each system instantiates this with its own params and feature list.
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

import xgboost as xgb
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss
from scipy.stats import chi2


class XGBModel:
    """
    Wraps XGBoost for binary classification (NRFI, HR, F5)
    and Poisson regression (K Pro).

    Usage:
        m = XGBModel(params=XGB_PARAMS, features=FEATURE_LIST, meta={"version": "v17"})
        results = m.train(X_train, y_train, X_test, y_test)
        m.save(model_path, meta_path)
        m = XGBModel.load(model_path, meta_path)
        preds = m.predict(X)
        cv = m.walk_forward_cv(df, folds=[2023, 2024, 2025])
    """

    def __init__(self, params: dict, features: list, meta: dict = None):
        self.params = params
        self.features = features
        self.meta = meta or {}
        self.booster = None

    def _to_dmatrix(self, X: pd.DataFrame) -> xgb.DMatrix:
        avail = [f for f in self.features if f in X.columns]
        return xgb.DMatrix(
            X[avail].apply(pd.to_numeric, errors="coerce").astype(float),
            feature_names=avail,
        )

    def train(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_test: pd.DataFrame = None,
        y_test: pd.Series = None,
        early_stopping: int = 50,
        num_boost_round: int = 2000,
        verbose_eval: int = 0,
        val_frac: float = 0.125,
    ) -> dict:
        """
        Train model. Returns dict with AUC, Brier, log_loss, best_iteration.
        If X_test/y_test provided, evaluates OOS metrics.

        Early stopping watches an internal validation slice carved from the
        TAIL of X_train (val_frac, default last 1/8 by date -- X_train is
        re-sorted here to guarantee that), NEVER X_test/y_test. Fixed
        2026-08-17 (finding C3.1): this used to put dtest directly into the
        early-stopping watchlist, so walk_forward_cv()'s reported per-fold
        AUC/Brier (retrain_nrfi_v17.py/v18.py's only callers of this class)
        measured a model whose stopping point had already "seen" the exact
        fold it was being scored on -- the textbook C03 leak this repo's
        other retrain scripts were specifically fixed to avoid, and which
        this class's own docstring claims ("shared by NRFI, HR, F5, and K
        Pro") to already be free of. In practice only NRFI's diagnostic
        walk_forward_cv path uses this class -- HR/F5/K each have their own,
        already-correct inline CV loops -- so this fix changes NRFI's
        reported cv_mean_auc/cv_summary numbers (used for the E05 drift
        narrative in CONTEXT.md) without touching any production booster.
        See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
        """
        # Caller (walk_forward_cv) is responsible for passing X_train already
        # sorted chronologically -- this just takes the tail as-given, the
        # same convention every other retrain script's own inline CV loop
        # already uses (e.g. retrain_f5_v5.py's _walk_forward_cv).
        n = len(X_train)
        n_val = max(1, int(n * val_frac)) if val_frac and n >= 20 else 0

        if n_val:
            X_tr_inner, y_tr_inner = X_train.iloc[:-n_val], y_train.iloc[:-n_val]
            X_val_inner, y_val_inner = X_train.iloc[-n_val:], y_train.iloc[-n_val:]
        else:
            X_tr_inner, y_tr_inner = X_train, y_train
            X_val_inner, y_val_inner = None, None

        dtrain = self._to_dmatrix(X_tr_inner)
        dtrain.set_label(y_tr_inner.values)

        watchlist = [(dtrain, "train")]
        evals_result = {}
        _early_stopping = None

        if X_val_inner is not None:
            dval = self._to_dmatrix(X_val_inner)
            dval.set_label(y_val_inner.values)
            watchlist.append((dval, "val"))
            _early_stopping = early_stopping

        self.booster = xgb.train(
            self.params,
            dtrain,
            num_boost_round=num_boost_round,
            evals=watchlist,
            early_stopping_rounds=_early_stopping,
            evals_result=evals_result,
            verbose_eval=verbose_eval,
        )

        results = {"best_iteration": getattr(self.booster, 'best_iteration', getattr(self.booster, 'best_ntree_limit', 0))}

        if X_test is not None and y_test is not None:
            preds = self.predict(X_test)
            objective = self.params.get("objective", "binary:logistic")
            if "binary" in objective:
                results["auc"]    = round(roc_auc_score(y_test, preds), 4)
                results["brier"]  = round(brier_score_loss(y_test, preds), 4)
                results["logloss"] = round(log_loss(y_test, preds), 4)
                results["hl_p"]   = self._hosmer_lemeshow(y_test.values, preds)
                results["cal_gap"] = round(abs(preds.mean() - y_test.mean()), 4)
                print(f"  OOS AUC: {results['auc']} | Brier: {results['brier']}"
                      f" | H-L p: {results['hl_p']:.3f} | cal gap: {results['cal_gap']}")
            else:
                # Poisson - use MAE as primary metric
                results["mae"] = round(float(np.mean(np.abs(preds - y_test.values))), 4)
                print(f"  OOS MAE: {results['mae']}")

        return results

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Predict probabilities (binary) or expected values (Poisson)."""
        if self.booster is None:
            raise RuntimeError("Model not trained. Call train() or load() first.")
        dm = self._to_dmatrix(X)
        return self.booster.predict(dm, iteration_range=(0, getattr(self.booster, 'best_iteration', getattr(self.booster, 'best_ntree_limit', 0))))

    def walk_forward_cv(
        self,
        df: pd.DataFrame,
        folds: list,
        date_col: str = "game_date",
        target_col: str = "target",
    ) -> dict:
        """
        Walk-forward cross-validation.

        Args:
            df:         Full feature DataFrame with date_col and target_col.
            folds:      List of test-year ints e.g. [2023, 2024, 2025].
                        Each fold trains on all prior years, tests on that year.
            date_col:   Date column name.
            target_col: Target column name.

        Returns:
            Dict with per-fold metrics and mean AUC / Brier.
        """
        df = df.copy()
        df[date_col] = pd.to_datetime(df[date_col])
        df["_year"] = df[date_col].dt.year

        fold_results = []
        print(f"Walk-forward CV: {len(folds)} folds")

        for test_year in folds:
            # Sort chronologically so train()'s tail-slice validation carve
            # (see its val_frac docstring, finding C3.1) is genuinely the
            # most-recent-before-cutoff rows, not an arbitrary tail of
            # whatever order df happened to arrive in.
            train_df = df[df["_year"] < test_year].sort_values(date_col)
            test_df  = df[df["_year"] == test_year]

            if train_df.empty or test_df.empty:
                print(f"  {test_year}: insufficient data, skipping")
                continue

            X_train = train_df
            y_train = train_df[target_col]
            X_test  = test_df
            y_test  = test_df[target_col]

            fold_model = XGBModel(self.params.copy(), self.features, self.meta.copy())
            fold_results_dict = fold_model.train(
                X_train, y_train, X_test, y_test,
                verbose_eval=0,
            )
            fold_results_dict["year"] = test_year
            fold_results_dict["n_train"] = len(train_df)
            fold_results_dict["n_test"] = len(test_df)
            fold_results.append(fold_results_dict)
            print(f"  {test_year}: AUC {fold_results_dict.get('auc','N/A')}"
                  f" | n_train={len(train_df):,} | n_test={len(test_df):,}")

        summary = {"folds": fold_results}
        if fold_results and "auc" in fold_results[0]:
            aucs   = [f["auc"] for f in fold_results if "auc" in f]
            briers = [f["brier"] for f in fold_results if "brier" in f]
            summary["mean_auc"]   = round(float(np.mean(aucs)), 4)
            summary["std_auc"]    = round(float(np.std(aucs)), 4)
            summary["mean_brier"] = round(float(np.mean(briers)), 4)
            print(f"  Walk-forward mean AUC: {summary['mean_auc']} (+/- {summary['std_auc']})")

        return summary

    def save(self, model_path: Path, meta_path: Path) -> None:
        """Save booster and metadata to disk."""
        if self.booster is None:
            raise RuntimeError("No trained model to save.")
        model_path = Path(model_path)
        meta_path  = Path(meta_path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(model_path))
        meta_out = {
            **self.meta,
            "features": self.features,
            "params":   self.params,
            "saved_at": datetime.now().isoformat(),
            "best_iteration": getattr(self.booster, 'best_iteration', getattr(self.booster, 'best_ntree_limit', 0)),
        }
        meta_path.write_text(json.dumps(meta_out, indent=2))
        print(f"  Model saved: {model_path.name}")
        print(f"  Meta  saved: {meta_path.name}")

    @classmethod
    def load(cls, model_path: Path, meta_path: Path) -> "XGBModel":
        """Load a saved model and metadata."""
        model_path = Path(model_path)
        meta_path  = Path(meta_path)
        meta = json.loads(meta_path.read_text())
        features = meta.get("features", [])
        params   = meta.get("params", {})
        instance = cls(params=params, features=features, meta=meta)
        instance.booster = xgb.Booster()
        instance.booster.load_model(str(model_path))
        instance.booster.best_ntree_limit = meta.get("best_iteration", 0)
        print(f"  Model loaded: {model_path.name} | {len(features)} features"
              f" | best_iter={instance.booster.best_ntree_limit}")
        return instance

    @staticmethod
    def _hosmer_lemeshow(y_true: np.ndarray, y_pred: np.ndarray, n_bins: int = 10) -> float:
        """Hosmer-Lemeshow goodness-of-fit p-value. Higher = better calibrated."""
        try:
            df = pd.DataFrame({"y": y_true, "p": y_pred})
            df["bin"] = pd.qcut(df["p"], q=n_bins, duplicates="drop")
            grouped = df.groupby("bin").agg(
                obs=("y", "sum"), exp=("p", "sum"), n=("y", "count")
            )
            hl = ((grouped["obs"] - grouped["exp"]) ** 2 /
                  (grouped["exp"] * (1 - grouped["exp"] / grouped["n"]))).sum()
            return float(chi2.sf(hl, df=n_bins - 2))
        except Exception:
            return float("nan")